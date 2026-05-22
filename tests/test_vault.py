"""Unit tests for ``core.vault`` — the SQLCipher vault lifecycle, no Qt.

These exercise real SQLCipher encryption (create / lock / unlock round-trips and
wrong-password failure), so they require the ``sqlcipher3`` wheel — which CI
installs for this layer. A fast Argon2 cost keeps key derivation cheap; the KDF
itself is covered by ``test_crypto.py``.
"""

import pytest

from core.crypto import KdfParams
from core.vault import InvalidPassword, Vault, VaultError, VaultLocked

# Minimal valid Argon2 params (memory_cost floor is 8 * parallelism) — fast.
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
PASSWORD = "correct horse battery staple"
SQLITE_PLAINTEXT_MAGIC = b"SQLite format 3\x00"


@pytest.fixture
def vault_path(tmp_path):
    return tmp_path / "notes.vault"


def test_create_writes_an_encrypted_file_and_meta(vault_path):
    vault = Vault.create(vault_path, PASSWORD, FAST)
    try:
        assert vault.path.exists()
        assert vault.meta_path.exists()
        # On-disk header must NOT be the plaintext SQLite magic — proves the DB
        # is genuinely encrypted, not a vanilla SQLite file.
        assert vault_path.read_bytes()[:16] != SQLITE_PLAINTEXT_MAGIC
    finally:
        vault.lock()


def test_create_returns_unlocked_vault(vault_path):
    vault = Vault.create(vault_path, PASSWORD, FAST)
    try:
        assert vault.is_locked is False
        assert vault.connection is not None
    finally:
        vault.lock()


def test_round_trip_create_lock_unlock_reads_data_back(vault_path):
    # Create, write a row, lock (closes the file), then unlock with the correct
    # password and read it back — proves real encrypt/decrypt across a close.
    vault = Vault.create(vault_path, PASSWORD, FAST)
    vault.connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, body TEXT)")
    vault.connection.execute("INSERT INTO t (body) VALUES (?)", ("secret note",))
    vault.connection.commit()
    vault.lock()
    assert vault.is_locked

    vault.unlock(PASSWORD)
    try:
        rows = vault.connection.execute("SELECT body FROM t").fetchall()
        assert rows == [("secret note",)]
    finally:
        vault.lock()


def test_unlock_with_wrong_password_raises_and_stays_locked(vault_path):
    Vault.create(vault_path, PASSWORD, FAST).lock()

    reopened = Vault(vault_path)
    with pytest.raises(InvalidPassword):
        reopened.unlock("not the password")
    # No partial read, and the vault is still locked after the failed attempt.
    assert reopened.is_locked


def test_unlock_after_failed_attempt_still_works_with_right_password(vault_path):
    Vault.create(vault_path, PASSWORD, FAST).lock()

    reopened = Vault(vault_path)
    with pytest.raises(InvalidPassword):
        reopened.unlock("wrong")
    reopened.unlock(PASSWORD)  # the bad attempt must not corrupt anything
    try:
        assert reopened.is_locked is False
    finally:
        reopened.lock()


def test_create_refuses_to_clobber_existing_vault(vault_path):
    Vault.create(vault_path, PASSWORD, FAST).lock()
    with pytest.raises(VaultError):
        Vault.create(vault_path, PASSWORD, FAST)


def test_unlock_missing_vault_raises_not_creates(vault_path):
    vault = Vault(vault_path)
    with pytest.raises(VaultError):
        vault.unlock(PASSWORD)
    # Must NOT have silently created a new empty database.
    assert not vault_path.exists()


def test_unlock_with_missing_meta_raises(vault_path):
    Vault.create(vault_path, PASSWORD, FAST).lock()
    Vault(vault_path).meta_path.unlink()
    with pytest.raises(VaultError):
        Vault(vault_path).unlock(PASSWORD)


def test_corrupt_meta_raises_vault_error(vault_path):
    Vault.create(vault_path, PASSWORD, FAST).lock()
    meta = Vault(vault_path).meta_path
    meta.write_text("not json", encoding="utf-8")
    with pytest.raises(VaultError):
        Vault(vault_path).unlock(PASSWORD)


def test_connection_while_locked_raises(vault_path):
    vault = Vault(vault_path)
    with pytest.raises(VaultLocked):
        _ = vault.connection


def test_lock_is_idempotent(vault_path):
    vault = Vault.create(vault_path, PASSWORD, FAST)
    vault.lock()
    vault.lock()  # second lock must not raise
    assert vault.is_locked


def test_context_manager_locks_on_exit(vault_path):
    with Vault.create(vault_path, PASSWORD, FAST) as vault:
        assert vault.is_locked is False
    assert vault.is_locked
