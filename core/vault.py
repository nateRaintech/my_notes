"""Encrypted vault lifecycle: create, unlock, and lock a SQLCipher database.

This is the security foundation every later persistence feature builds on. The
master password is run through Argon2id (:mod:`core.crypto`) to derive a 256-bit
key, which is handed to SQLCipher as a **raw hex key** (``PRAGMA key = "x'<64
hex>'"``) so SQLCipher performs no key derivation of its own (see ``LESSONS.md``
and spike #11). The whole database is encrypted at rest; it is only ever
decrypted in memory while unlocked.

Pure Python, no Qt: ``core/`` is the unit-testable layer (CLAUDE.md).

Where the salt lives
--------------------
Unlocking re-derives the key, which needs the Argon2 ``salt`` and ``KdfParams``.
But the salt is needed *before* the encrypted DB can be opened — it cannot live
inside the very file it is needed to decrypt. The salt is **not secret**
(``LESSONS.md``), so it is stored in a small plaintext sidecar next to the vault
(``<vault>.meta``, JSON). This mirrors KeePass's unencrypted KDBX header. The
read/write of that metadata is encapsulated here, so a future single-file format
(an embedded plaintext header) can replace it without changing callers.

Scope: this module owns only create / unlock / lock. The database **schema**
(notebooks, notes, tags, FTS5) and **idle auto-lock + secure key wiping** are
separate M2 capabilities (see ``ROADMAP.md``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from sqlcipher3 import dbapi2 as sqlcipher

from core.crypto import DEFAULT_PARAMS, KdfParams, derive_key, generate_salt

# Bumped when the on-disk metadata layout changes; lets future versions migrate.
META_FORMAT_VERSION = 1
# Suffix for the plaintext sidecar holding the (non-secret) salt + KDF params.
META_SUFFIX = ".meta"


class VaultError(Exception):
    """Base class for vault errors."""


class InvalidPassword(VaultError):
    """The master password did not decrypt the vault."""


class VaultLocked(VaultError):
    """An operation requiring an unlocked vault was attempted while locked."""


def _key_pragma(key: bytes) -> str:
    """Build the ``PRAGMA key`` statement for a raw 256-bit key.

    Uses the ``x'<hex>'`` raw-key form so SQLCipher uses the bytes directly and
    skips its own KDF (``core.crypto`` owns key derivation). ``key.hex()`` is
    pure hex, so there is nothing to escape.
    """
    return f"PRAGMA key = \"x'{key.hex()}'\""


class Vault:
    """A SQLCipher-encrypted vault file and its plaintext metadata sidecar.

    Construct with a path, then either :meth:`create` a new vault or
    :meth:`unlock` an existing one. While unlocked, :attr:`connection` exposes
    the live SQLCipher connection for higher layers (e.g. ``core.repository``).
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.meta_path = Path(str(self.path) + META_SUFFIX)
        self._conn: sqlcipher.Connection | None = None
        self._key: bytes | None = None

    # -- state ---------------------------------------------------------------

    @property
    def is_locked(self) -> bool:
        """True when no key is held and no connection is open."""
        return self._conn is None

    @property
    def connection(self) -> sqlcipher.Connection:
        """The live SQLCipher connection. Raises :class:`VaultLocked` if locked."""
        if self._conn is None:
            raise VaultLocked("vault is locked")
        return self._conn

    # -- lifecycle -----------------------------------------------------------

    @classmethod
    def create(
        cls,
        path: str | os.PathLike[str],
        password: str,
        params: KdfParams = DEFAULT_PARAMS,
    ) -> "Vault":
        """Create a new encrypted vault and return it **unlocked**.

        Generates a fresh salt, derives the key, materialises an encrypted
        (empty) database, and writes the sidecar metadata. Refuses to clobber an
        existing vault or metadata file.
        """
        vault = cls(path)
        if vault.path.exists() or vault.meta_path.exists():
            raise VaultError(f"vault already exists at {vault.path}")

        salt = generate_salt()
        key = derive_key(password, salt, params)

        conn = sqlcipher.connect(str(vault.path))
        try:
            conn.execute(_key_pragma(key))
            # Writing the header page forces SQLCipher to encrypt and flush page
            # 1, so even an otherwise-empty vault is a real encrypted DB on disk
            # (and a wrong key later fails on the very first read). Doubles as a
            # schema-version marker for the future migrations capability.
            conn.execute(f"PRAGMA user_version = {META_FORMAT_VERSION}")
            conn.commit()
        except Exception:
            conn.close()
            # Don't leave a half-written file behind on failure.
            vault.path.unlink(missing_ok=True)
            raise

        vault._write_meta(salt, params)
        vault._conn = conn
        vault._key = key
        return vault

    def unlock(self, password: str) -> None:
        """Open and decrypt the vault with ``password``.

        Re-derives the key from the stored salt/params, keys the connection, and
        forces a page-1 read to validate the key. A wrong password raises
        :class:`InvalidPassword` with no partial read; a missing vault or
        metadata file raises :class:`VaultError` (it never silently creates a new
        empty database). Idempotent re-locking on failure leaves the vault
        locked.
        """
        if not self.path.exists():
            raise VaultError(f"no vault file at {self.path}")
        if not self.meta_path.exists():
            raise VaultError(f"no vault metadata at {self.meta_path}")

        salt, params = self._read_meta()
        key = derive_key(password, salt, params)

        conn = sqlcipher.connect(str(self.path))
        try:
            conn.execute(_key_pragma(key))
            # The first statement that touches the database is what forces
            # SQLCipher to decrypt page 1 — a wrong/absent key raises here,
            # before any row is returned, so there is never a partial read.
            conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except sqlcipher.DatabaseError as exc:
            conn.close()
            raise InvalidPassword("incorrect master password") from exc
        except Exception:
            conn.close()
            raise

        self._conn = conn
        self._key = key

    def lock(self) -> None:
        """Close the connection and drop the key reference.

        Idempotent. Note: this drops the Python reference to the key but does not
        securely zero it — defence-in-depth key wiping and idle-timeout locking
        are the separate M2 "Auto-lock" capability.
        """
        if self._conn is not None:
            self._conn.close()
        self._conn = None
        self._key = None

    # Allow `with Vault.create(...) as v:` / `with vault: ...` usage.
    def __enter__(self) -> "Vault":
        return self

    def __exit__(self, *exc: object) -> None:
        self.lock()

    # -- metadata sidecar ----------------------------------------------------

    def _write_meta(self, salt: bytes, params: KdfParams) -> None:
        meta = {
            "format_version": META_FORMAT_VERSION,
            "kdf": "argon2id",
            "salt": salt.hex(),
            "time_cost": params.time_cost,
            "memory_cost": params.memory_cost,
            "parallelism": params.parallelism,
        }
        self.meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def _read_meta(self) -> tuple[bytes, KdfParams]:
        try:
            meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            salt = bytes.fromhex(meta["salt"])
            params = KdfParams(
                time_cost=meta["time_cost"],
                memory_cost=meta["memory_cost"],
                parallelism=meta["parallelism"],
            )
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            raise VaultError(f"corrupt vault metadata at {self.meta_path}") from exc
        return salt, params
