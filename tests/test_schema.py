"""Unit tests for ``core.schema`` — the vault's DDL + forward-only migrations.

Pure Python, no Qt. Most tests run ``migrate`` against an in-memory
``sqlcipher3`` connection (no key needed — the schema is independent of
encryption) so they exercise the exact SQLite/FTS5 build the real vault uses. A
couple of vault-level round-trips prove the schema is created on
:meth:`Vault.create` and survives a lock/unlock cycle.
"""

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.crypto import KdfParams
from core.vault import Vault

# Minimal valid Argon2 params keep the vault-level tests' key derivation cheap.
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
PASSWORD = "correct horse battery staple"

EXPECTED_TABLES = {"notebooks", "notes", "tags", "note_tags", "notes_fts"}


@pytest.fixture
def conn():
    """An in-memory connection with FK enforcement on, like the vault opens."""
    c = sqlcipher.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
    finally:
        c.close()


def _table_names(connection):
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row[0] for row in rows}


def _fts_matches(connection, term):
    return connection.execute(
        "SELECT count(*) FROM notes_fts WHERE notes_fts MATCH ?", (term,)
    ).fetchone()[0]


# -- migration runner -------------------------------------------------------


def test_migrate_creates_all_tables(conn):
    schema.migrate(conn)
    assert EXPECTED_TABLES <= _table_names(conn)


def test_migrate_sets_user_version_to_schema_version(conn):
    schema.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == schema.SCHEMA_VERSION


def test_migrate_returns_resulting_version(conn):
    assert schema.migrate(conn) == schema.SCHEMA_VERSION


def test_migrate_is_idempotent(conn):
    assert schema.migrate(conn) == schema.SCHEMA_VERSION
    tables_after_first = _table_names(conn)
    # A second run must neither error nor re-create anything.
    assert schema.migrate(conn) == schema.SCHEMA_VERSION
    assert _table_names(conn) == tables_after_first


# -- full-text index stays in sync via triggers -----------------------------


def test_fts_finds_inserted_note(conn):
    schema.migrate(conn)
    conn.execute(
        "INSERT INTO notes (title, body) VALUES (?, ?)",
        ("Shopping", "buy milk and eggs"),
    )
    assert _fts_matches(conn, "milk") == 1
    assert _fts_matches(conn, "Shopping") == 1


def test_fts_reflects_update(conn):
    schema.migrate(conn)
    cur = conn.execute("INSERT INTO notes (title, body) VALUES ('t', 'apples')")
    conn.execute("UPDATE notes SET body = 'oranges' WHERE id = ?", (cur.lastrowid,))
    assert _fts_matches(conn, "apples") == 0  # old text no longer indexed
    assert _fts_matches(conn, "oranges") == 1


def test_fts_reflects_delete(conn):
    schema.migrate(conn)
    cur = conn.execute("INSERT INTO notes (title, body) VALUES ('t', 'ephemeral')")
    conn.execute("DELETE FROM notes WHERE id = ?", (cur.lastrowid,))
    assert _fts_matches(conn, "ephemeral") == 0


# -- referential integrity --------------------------------------------------


def test_note_tags_cascade_on_note_delete(conn):
    schema.migrate(conn)
    conn.execute("INSERT INTO notes (id, title, body) VALUES (1, 't', 'b')")
    conn.execute("INSERT INTO tags (id, name) VALUES (1, 'work')")
    conn.execute("INSERT INTO note_tags (note_id, tag_id) VALUES (1, 1)")
    conn.execute("DELETE FROM notes WHERE id = 1")
    # The join row cascades away with the note; the tag itself survives.
    assert conn.execute("SELECT count(*) FROM note_tags").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM tags").fetchone()[0] == 1


def test_tag_names_are_unique(conn):
    schema.migrate(conn)
    conn.execute("INSERT INTO tags (name) VALUES ('dup')")
    with pytest.raises(sqlcipher.IntegrityError):
        conn.execute("INSERT INTO tags (name) VALUES ('dup')")


# -- vault integration: schema is present on create and after unlock ---------


def test_vault_create_initialises_schema(tmp_path):
    vault = Vault.create(tmp_path / "notes.vault", PASSWORD, FAST)
    try:
        assert EXPECTED_TABLES <= _table_names(vault.connection)
        version = vault.connection.execute("PRAGMA user_version").fetchone()[0]
        assert version == schema.SCHEMA_VERSION
    finally:
        vault.lock()


def test_vault_schema_survives_unlock(tmp_path):
    path = tmp_path / "notes.vault"
    Vault.create(path, PASSWORD, FAST).lock()
    vault = Vault(path)
    vault.unlock(PASSWORD)
    try:
        assert EXPECTED_TABLES <= _table_names(vault.connection)
        # A note written then read back across the unlock proves the FTS triggers
        # work on the real keyed connection too.
        vault.connection.execute("INSERT INTO notes (title, body) VALUES ('t', 'hello')")
        assert _fts_matches(vault.connection, "hello") == 1
    finally:
        vault.lock()
