"""Tests for the API-key secret store — round-trip through a REAL temp vault.

Exercises :meth:`Repository.set_api_key`, :meth:`Repository.has_api_key`,
:meth:`Repository.get_api_key`, and :meth:`Repository.clear_api_key` against
an in-memory connection AND a real encrypted vault to confirm the schema
migration and FK enforcement work end-to-end.

Pure Python, no Qt.
"""

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.crypto import KdfParams
from core.repository import Repository
from core.vault import Vault

# Minimal Argon2 params — keeps key derivation cheap in vault-level tests.
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
PASSWORD = "correct horse battery staple"


@pytest.fixture
def repo():
    """Repository over a migrated in-memory connection (fast, no I/O)."""
    conn = sqlcipher.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    schema.migrate(conn)
    try:
        yield Repository(conn)
    finally:
        conn.close()


@pytest.fixture
def vault_path(tmp_path):
    return tmp_path / "notes.vault"


# ---------------------------------------------------------------------------
# In-memory round-trips
# ---------------------------------------------------------------------------


def test_has_api_key_false_when_none_stored(repo):
    assert repo.has_api_key() is False


def test_set_then_has_returns_true(repo):
    repo.set_api_key("sk-test-key-abc123")
    assert repo.has_api_key() is True


def test_get_api_key_returns_stored_value(repo):
    repo.set_api_key("sk-test-key-abc123")
    assert repo.get_api_key() == "sk-test-key-abc123"


def test_get_api_key_returns_none_when_absent(repo):
    assert repo.get_api_key() is None


def test_set_api_key_replaces_existing(repo):
    repo.set_api_key("first-key")
    repo.set_api_key("second-key")
    assert repo.get_api_key() == "second-key"
    # Still only one row after multiple sets (upsert).
    count = repo._conn.execute("SELECT count(*) FROM app_secrets").fetchone()[0]
    assert count == 1


def test_clear_api_key_removes_it(repo):
    repo.set_api_key("sk-test-key-abc123")
    repo.clear_api_key()
    assert repo.has_api_key() is False
    assert repo.get_api_key() is None


def test_clear_api_key_is_no_op_when_absent(repo):
    # Must not raise even when nothing is stored.
    repo.clear_api_key()
    assert repo.has_api_key() is False


def test_full_round_trip_set_has_get_clear_has(repo):
    """Canonical set → has → get → clear → has sequence."""
    assert not repo.has_api_key()
    repo.set_api_key("round-trip-key")
    assert repo.has_api_key()
    assert repo.get_api_key() == "round-trip-key"
    repo.clear_api_key()
    assert not repo.has_api_key()


# ---------------------------------------------------------------------------
# Real encrypted vault — confirms schema migration on create AND on unlock
# ---------------------------------------------------------------------------


def test_secret_survives_vault_lock_unlock_cycle(vault_path):
    """The app_secrets table persists through a lock/unlock cycle."""
    vault = Vault.create(vault_path, PASSWORD, FAST)
    try:
        repo = Repository(vault.connection)
        repo.set_api_key("persist-test-key")
    finally:
        vault.lock()

    # Re-open the encrypted file and confirm the row is still there.
    vault2 = Vault(vault_path)
    vault2.unlock(PASSWORD)
    try:
        repo2 = Repository(vault2.connection)
        assert repo2.has_api_key() is True
        assert repo2.get_api_key() == "persist-test-key"
    finally:
        vault2.lock()


def test_migration_creates_app_secrets_on_existing_vault(vault_path):
    """Unlocking a vault that predates the app_secrets migration adds the table.

    Simulates an existing vault at schema v1: we create a vault then manually
    roll its user_version back to 1 (re-lock first) and confirm that unlock
    (which runs migrate()) brings it forward and the table is usable.
    """
    # Step 1: create a vault and manually set its schema version back to 1.
    vault = Vault.create(vault_path, PASSWORD, FAST)
    vault.connection.execute("PRAGMA user_version = 1")
    vault.connection.commit()
    vault.lock()

    # Step 2: reopen — migrate() should add app_secrets (migration 2).
    vault2 = Vault(vault_path)
    vault2.unlock(PASSWORD)
    try:
        repo = Repository(vault2.connection)
        # The table must exist and work normally.
        repo.set_api_key("post-migration-key")
        assert repo.has_api_key() is True
        assert repo.get_api_key() == "post-migration-key"
    finally:
        vault2.lock()
