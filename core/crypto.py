"""Argon2id key derivation for the encrypted vault.

The master password never touches disk. It is run through Argon2id — a
memory-hard KDF — to derive a 256-bit key that is later handed to SQLCipher as a
raw hex key (``PRAGMA key = "x'<64 hex>'"``), so SQLCipher performs no key
derivation of its own (see ``LESSONS.md`` / spike #11). This module owns the KDF.

Pure Python, no Qt: ``core/`` is the unit-testable layer (CLAUDE.md). Policy such
as requiring a non-empty master password belongs to the caller (the unlock
dialog), not here — :func:`derive_key` is a total function over its byte inputs.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from argon2.low_level import Type, hash_secret_raw

# A derived vault key is 256 bits; SQLCipher consumes it as 64 hex chars.
KEY_BYTES = 32
# Salt length generated for new vaults (not secret, but must be stable per vault).
SALT_BYTES = 16
# Argon2's own lower bound on salt length.
MIN_SALT_BYTES = 8


@dataclass(frozen=True)
class KdfParams:
    """Tunable Argon2id cost parameters.

    Defaults target an interactive desktop unlock (run once per vault open) and
    sit at or above the OWASP Argon2id floor. ``memory_cost`` is in KiB, matching
    argon2's low-level API. These are stored alongside a vault so the same params
    are reused on every unlock; bump them deliberately, never below the floor.
    """

    time_cost: int = 3  # iterations
    memory_cost: int = 64 * 1024  # 64 MiB, expressed in KiB
    parallelism: int = 4  # lanes


DEFAULT_PARAMS = KdfParams()


def generate_salt() -> bytes:
    """Return a fresh cryptographically random salt (:data:`SALT_BYTES` bytes).

    Store this with the vault. It is not secret, but it must stay stable for a
    given vault so the same password re-derives the same key.
    """
    return secrets.token_bytes(SALT_BYTES)


def derive_key(password: str, salt: bytes, params: KdfParams = DEFAULT_PARAMS) -> bytes:
    """Derive a 256-bit vault key from ``password`` and ``salt`` via Argon2id.

    Deterministic: identical ``password``, ``salt``, and ``params`` always yield
    the same :data:`KEY_BYTES`-byte key. A different password or salt yields a
    different key. The password is encoded as UTF-8.

    Raises ``ValueError`` if ``salt`` is shorter than :data:`MIN_SALT_BYTES`.
    """
    if len(salt) < MIN_SALT_BYTES:
        raise ValueError(f"salt must be at least {MIN_SALT_BYTES} bytes, got {len(salt)}")
    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=params.time_cost,
        memory_cost=params.memory_cost,
        parallelism=params.parallelism,
        hash_len=KEY_BYTES,
        type=Type.ID,
    )
