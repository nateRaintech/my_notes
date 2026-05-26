"""Database schema and forward-only migrations for the encrypted vault.

The vault stores everything in five tables — ``notebooks``, ``notes``, ``tags``,
``note_tags`` and an ``notes_fts`` full-text index — created automatically the
first time a vault is opened. The logical schema version is tracked in SQLite's
``PRAGMA user_version``; :func:`migrate` brings a connection up to
:data:`SCHEMA_VERSION` by applying each pending migration in order. It is
**forward-only** and **idempotent**: a database already at the latest version is
left untouched, so it is safe to call on every open.

:meth:`Vault.create <core.vault.Vault.create>` calls it on a fresh database and
:meth:`Vault.unlock <core.vault.Vault.unlock>` calls it when reopening an existing
one, so the schema is guaranteed present on first open and upgraded forward on
later opens.

Pure Python, no Qt. It takes any DB-API connection (it does not import the vault
layer), so it can be unit-tested against an in-memory connection.

Full-text search
----------------
``notes_fts`` is an **external-content** FTS5 table over ``notes(title, body)``
(``content='notes'``): it indexes the note text without storing a second copy.
Three triggers (``notes_ai`` / ``notes_ad`` / ``notes_au``) mirror every
insert/update/delete on ``notes`` into the index, so callers write to ``notes``
only and search stays correct — the canonical SQLite FTS5 pattern.

Referential integrity (the ``ON DELETE CASCADE`` / ``SET NULL`` clauses below) is
only enforced when the connection has ``PRAGMA foreign_keys = ON``; the vault
sets that on every connection it opens.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # The real connection is a keyed SQLCipher one; the type matches a plain
    # in-memory connection too, which is what the unit tests pass.
    from sqlcipher3.dbapi2 import Connection

# Bump this when appending a migration below. Always equals the highest
# migration version, i.e. the schema a freshly created vault ends up at.
SCHEMA_VERSION = 2

# Migration 1 — the initial schema.
#   notebooks  nest via a self-referential parent_id (deleting a notebook removes
#              its descendants); notes in a deleted notebook are orphaned to the
#              root (notebook_id -> NULL) rather than destroyed.
#   tags       have unique names; note_tags is the many-to-many join and cascades
#              away with either endpoint.
#   notes_fts  is an external-content FTS5 index kept in sync by the triggers.
_MIGRATION_1 = """
CREATE TABLE notebooks (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    parent_id  INTEGER REFERENCES notebooks(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE notes (
    id          INTEGER PRIMARY KEY,
    notebook_id INTEGER REFERENCES notebooks(id) ON DELETE SET NULL,
    title       TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_notes_notebook ON notes(notebook_id);

CREATE TABLE tags (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE note_tags (
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id)  ON DELETE CASCADE,
    PRIMARY KEY (note_id, tag_id)
);

CREATE VIRTUAL TABLE notes_fts USING fts5(
    title,
    body,
    content='notes',
    content_rowid='id'
);

CREATE TRIGGER notes_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, title, body)
        VALUES (new.id, new.title, new.body);
END;

CREATE TRIGGER notes_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, body)
        VALUES ('delete', old.id, old.title, old.body);
END;

CREATE TRIGGER notes_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, body)
        VALUES ('delete', old.id, old.title, old.body);
    INSERT INTO notes_fts(rowid, title, body)
        VALUES (new.id, new.title, new.body);
END;
"""

# Migration 2 — encrypted key-value secret store.
#   app_secrets  holds small named secrets (e.g. the AI API key). A single row
#                per name; INSERT OR REPLACE is the upsert idiom. Protected at
#                rest because the whole SQLCipher database is encrypted; the
#                value is stored verbatim and never exposed to the UI.
#                CREATE TABLE IF NOT EXISTS makes this idempotent and safe for
#                existing vaults that are opened against this schema version.
_MIGRATION_2 = """
CREATE TABLE IF NOT EXISTS app_secrets (
    name   TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

# Forward-only, ordered (version, DDL) migrations. Append new ones and bump
# SCHEMA_VERSION; never edit or reorder a shipped migration — vaults in the field
# have already applied it, and migrations only ever run *forward* from the
# version a database is currently at.
_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (1, _MIGRATION_1),
    (2, _MIGRATION_2),
)


def migrate(conn: Connection) -> int:
    """Bring ``conn`` up to :data:`SCHEMA_VERSION`; return the resulting version.

    Reads ``PRAGMA user_version`` and applies every migration newer than it, in
    order, bumping ``user_version`` after each. Forward-only and idempotent: a
    connection already at the latest version applies nothing, so this is safe to
    call on every vault open.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, script in _MIGRATIONS:
        if version > current:
            conn.executescript(script)
            # PRAGMA values can't be bound parameters; `version` is an int
            # constant we control, so the f-string is not an injection vector.
            conn.execute(f"PRAGMA user_version = {version}")
            current = version
    conn.commit()
    return current
