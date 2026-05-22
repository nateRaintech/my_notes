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

Auto-lock
---------
The derived key is held in a mutable ``bytearray`` and zeroed in place when the
vault locks (best-effort — see :meth:`Vault.lock`). Locking can be triggered on
demand, or after a configurable idle timeout: :attr:`Vault.idle_timeout` plus an
injectable monotonic ``clock`` let the UI drive auto-lock from a ``QTimer`` tick
(:meth:`Vault.lock_if_idle`) while this layer stays Qt-free and testable.

Scope: this module owns create / unlock / lock and the idle auto-lock policy. The
database **schema** (notebooks, notes, tags, FTS5) lives in :mod:`core.schema`;
:meth:`Vault.create` initialises it on a fresh vault and :meth:`Vault.unlock`
migrates an existing one forward, so the schema is always present once open.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from pathlib import Path

from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
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


def _wipe(buf: bytearray) -> None:
    """Overwrite a mutable byte buffer with zeros in place.

    Operates on the *same* object (no reallocation), so any reference still
    pointing at the buffer sees the zeroed bytes — that is what makes this a
    wipe rather than a discard.
    """
    buf[:] = b"\x00" * len(buf)


class Vault:
    """A SQLCipher-encrypted vault file and its plaintext metadata sidecar.

    Construct with a path, then either :meth:`create` a new vault or
    :meth:`unlock` an existing one. While unlocked, :attr:`connection` exposes
    the live SQLCipher connection for higher layers (e.g. ``core.repository``).
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        idle_timeout: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = Path(path)
        self.meta_path = Path(str(self.path) + META_SUFFIX)
        self._conn: sqlcipher.Connection | None = None
        # The key lives in a mutable buffer so it can be zeroed in place on lock.
        self._key: bytearray | None = None
        # Auto-lock policy. ``idle_timeout`` is seconds of inactivity before the
        # vault should auto-lock; ``None`` disables it. ``clock`` is injectable so
        # tests drive time without sleeping; production uses a monotonic clock
        # (immune to wall-clock adjustments). ``idle_timeout`` is public so a
        # future settings screen can retune it on a live vault.
        self.idle_timeout = idle_timeout
        self._clock = clock
        self._last_activity: float | None = None

    # -- state ---------------------------------------------------------------

    @property
    def is_locked(self) -> bool:
        """True when no key is held and no connection is open."""
        return self._conn is None

    @property
    def connection(self) -> sqlcipher.Connection:
        """The live SQLCipher connection. Raises :class:`VaultLocked` if locked.

        Accessing the connection counts as activity and defers the idle
        auto-lock (see :meth:`touch`) — using the database keeps the vault open.
        """
        if self._conn is None:
            raise VaultLocked("vault is locked")
        self.touch()
        return self._conn

    # -- lifecycle -----------------------------------------------------------

    @classmethod
    def create(
        cls,
        path: str | os.PathLike[str],
        password: str,
        params: KdfParams = DEFAULT_PARAMS,
        *,
        idle_timeout: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> "Vault":
        """Create a new encrypted vault and return it **unlocked**.

        Generates a fresh salt, derives the key, materialises an encrypted
        (empty) database, and writes the sidecar metadata. Refuses to clobber an
        existing vault or metadata file. ``idle_timeout``/``clock`` configure the
        auto-lock policy (see :meth:`__init__`).
        """
        vault = cls(path, idle_timeout=idle_timeout, clock=clock)
        if vault.path.exists() or vault.meta_path.exists():
            raise VaultError(f"vault already exists at {vault.path}")

        salt = generate_salt()
        key = derive_key(password, salt, params)

        conn = sqlcipher.connect(str(vault.path))
        try:
            conn.execute(_key_pragma(key))
            # Foreign keys are off per-connection by default in SQLite; turn them
            # on so the schema's ON DELETE CASCADE / SET NULL relationships hold.
            conn.execute("PRAGMA foreign_keys = ON")
            # Create the schema. This initialises the tables and, because it
            # writes pages, forces SQLCipher to encrypt and flush page 1 — so even
            # a brand-new vault is a real encrypted DB on disk, and a wrong key on
            # a later open fails on the very first read. (migrate() commits.)
            schema.migrate(conn)
        except Exception:
            conn.close()
            # Don't leave a half-written file behind on failure.
            vault.path.unlink(missing_ok=True)
            raise

        vault._write_meta(salt, params)
        vault._conn = conn
        vault._key = bytearray(key)
        vault.touch()
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

        # The key is valid (page 1 decrypted). Enforce FK relationships and bring
        # the schema forward — idempotent, a no-op when already at SCHEMA_VERSION.
        # Kept out of the block above so a real migration error is never
        # misreported as a wrong password.
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            schema.migrate(conn)
        except Exception:
            conn.close()
            raise

        self._conn = conn
        self._key = bytearray(key)
        self.touch()

    def lock(self) -> None:
        """Close the connection, wipe the key from memory, and stop the idle timer.

        Idempotent. The key is held in a ``bytearray`` and zeroed in place here
        (see :func:`_wipe`) before the reference is dropped. Mind the limits of
        secure wiping in CPython: copies outside our control — the immutable
        ``bytes`` returned by the KDF, the hex string handed to ``PRAGMA key``,
        and SQLCipher's own in-memory key — cannot be zeroed and may linger until
        garbage-collected. This zeroes the one buffer we own, as best-effort
        defence-in-depth.
        """
        if self._conn is not None:
            self._conn.close()
        if self._key is not None:
            _wipe(self._key)
        self._conn = None
        self._key = None
        self._last_activity = None

    # -- auto-lock -----------------------------------------------------------

    def touch(self) -> None:
        """Record activity now, deferring any idle auto-lock.

        Accessing :attr:`connection` calls this automatically, so any database
        use counts as activity; higher layers can also call it directly for
        non-database activity (e.g. editor keystrokes between debounced saves).
        """
        self._last_activity = self._clock()

    def is_idle_expired(self) -> bool:
        """True when an unlocked vault has been idle past :attr:`idle_timeout`.

        Always False when locked, when ``idle_timeout`` is ``None`` (auto-lock
        disabled), or before any activity has been recorded.
        """
        if self.is_locked or self.idle_timeout is None or self._last_activity is None:
            return False
        return self._clock() - self._last_activity >= self.idle_timeout

    def lock_if_idle(self) -> bool:
        """Lock the vault iff it has been idle past :attr:`idle_timeout`.

        Returns True if it locked (and wiped the key), False otherwise. Intended
        to be called periodically by the UI (e.g. a ``QTimer`` tick); the core
        stays Qt-free and merely exposes the policy.
        """
        if self.is_idle_expired():
            self.lock()
            return True
        return False

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
