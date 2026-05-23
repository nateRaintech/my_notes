"""Unit tests for ``core.importer`` — importing a legacy notes.db into the vault.

Pure Python, no Qt. Each test builds a throwaway legacy ``notes.db`` with stdlib
:mod:`sqlite3` (the prototype's files are plain, unencrypted SQLite) and imports
it into an in-memory, migrated ``sqlcipher3`` :class:`~core.repository.Repository`
— the same fixture ``test_repository`` uses, so the real SQLite/FTS5 build is
exercised without the cost of key derivation. The assertions are behavioral
(rows, notebooks, timestamps, counts, error paths), never editorial pins.
"""

from __future__ import annotations

import sqlite3

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.importer import (
    ImportResult,
    LegacyDatabaseError,
    LegacyNote,
    _normalize_timestamp,
    import_legacy_db,
    import_legacy_notes,
    read_legacy_notes,
)
from core.repository import Repository


@pytest.fixture
def repo():
    """A repository over a migrated, FK-enforcing in-memory connection."""
    conn = sqlcipher.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    schema.migrate(conn)
    try:
        yield Repository(conn)
    finally:
        conn.close()


def _make_legacy_db(path, rows, *, table="notes", columns=("content", "category", "timestamp")):
    """Create a legacy SQLite db at ``path`` with ``rows`` (tuples per ``columns``)."""
    conn = sqlite3.connect(str(path))
    try:
        coldefs = ", ".join(f"{col}" for col in columns)
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, {coldefs})")
        placeholders = ", ".join("?" * len(columns))
        conn.executemany(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
    return path


# -- read_legacy_notes: parsing & schema detection --------------------------


def test_read_parses_rows_into_legacy_notes(tmp_path):
    db = _make_legacy_db(
        tmp_path / "notes.db",
        [("Body one", "Work", "2020-01-01 09:30:00")],
        columns=("content", "category", "timestamp"),
    )
    notes = read_legacy_notes(db)
    assert notes == [LegacyNote("Body one", "Work", None, "2020-01-01 09:30:00")]


def test_read_captures_format_column(tmp_path):
    db = _make_legacy_db(
        tmp_path / "notes.db",
        [("text", "markdown")],
        columns=("content", "format"),
    )
    (note,) = read_legacy_notes(db)
    assert note.format == "markdown"
    assert note.category is None and note.timestamp is None


def test_read_tolerates_missing_optional_columns(tmp_path):
    # Only the required 'content' column — no category/format/timestamp.
    db = _make_legacy_db(tmp_path / "notes.db", [("just a body",)], columns=("content",))
    (note,) = read_legacy_notes(db)
    assert note == LegacyNote("just a body", None, None, None)


def test_read_columns_are_case_insensitive(tmp_path):
    db = _make_legacy_db(
        tmp_path / "notes.db",
        [("hi", "Recipes", "2021-06-01 00:00:00")],
        columns=("Content", "Category", "Timestamp"),
    )
    (note,) = read_legacy_notes(db)
    assert note.content == "hi"
    assert note.category == "Recipes"
    assert note.timestamp == "2021-06-01 00:00:00"


def test_read_detects_alternate_timestamp_column(tmp_path):
    db = _make_legacy_db(
        tmp_path / "notes.db",
        [("hi", "1577871000")],
        columns=("content", "modified"),
    )
    (note,) = read_legacy_notes(db)
    assert note.timestamp == "1577871000"


def test_read_null_content_becomes_empty_string(tmp_path):
    db = _make_legacy_db(tmp_path / "notes.db", [(None,)], columns=("content",))
    (note,) = read_legacy_notes(db)
    assert note.content == ""


def test_read_missing_file_raises(tmp_path):
    with pytest.raises(LegacyDatabaseError, match="not found"):
        read_legacy_notes(tmp_path / "nope.db")


def test_read_missing_content_column_raises(tmp_path):
    db = _make_legacy_db(
        tmp_path / "notes.db",
        [("x", "y")],
        columns=("body", "category"),
    )
    with pytest.raises(LegacyDatabaseError, match="content"):
        read_legacy_notes(db)


def test_read_auto_detects_single_non_notes_table(tmp_path):
    db = _make_legacy_db(
        tmp_path / "legacy.db",
        [("from the entries table",)],
        table="entries",
        columns=("content",),
    )
    (note,) = read_legacy_notes(db)
    assert note.content == "from the entries table"


def test_read_ambiguous_tables_raises(tmp_path):
    path = tmp_path / "ambiguous.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE alpha (content TEXT)")
    conn.execute("CREATE TABLE beta (content TEXT)")
    conn.commit()
    conn.close()
    with pytest.raises(LegacyDatabaseError, match="which table"):
        read_legacy_notes(path)


def test_read_explicit_table_selects_and_validates(tmp_path):
    path = tmp_path / "two.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE notes (content TEXT)")
    conn.execute("CREATE TABLE archive (content TEXT)")
    conn.executemany("INSERT INTO archive (content) VALUES (?)", [("archived",)])
    conn.commit()
    conn.close()
    assert read_legacy_notes(path, table="archive")[0].content == "archived"
    with pytest.raises(LegacyDatabaseError, match="not found"):
        read_legacy_notes(path, table="missing")


def test_read_does_not_modify_the_legacy_file(tmp_path):
    db = _make_legacy_db(tmp_path / "notes.db", [("a",)], columns=("content",))
    before = db.read_bytes()
    read_legacy_notes(db)
    assert db.read_bytes() == before  # opened read-only


# -- _normalize_timestamp ---------------------------------------------------


def test_normalize_timestamp_passes_through_datetime_strings():
    assert _normalize_timestamp("2020-01-01 09:30:00") == "2020-01-01 09:30:00"
    assert _normalize_timestamp("2020-01-01T09:30:00") == "2020-01-01T09:30:00"


def test_normalize_timestamp_converts_unix_epoch():
    # 1577871000 == 2020-01-01 09:30:00 UTC.
    assert _normalize_timestamp("1577871000") == "2020-01-01 09:30:00"


def test_normalize_timestamp_blank_and_none_become_none():
    assert _normalize_timestamp(None) is None
    assert _normalize_timestamp("") is None
    assert _normalize_timestamp("   ") is None


# -- import_legacy_notes: mapping into the vault ----------------------------


def test_import_maps_content_category_and_title(repo):
    result = import_legacy_notes(
        repo,
        [LegacyNote("# Heading\n\nbody text", "Work", None, "2020-01-01 09:30:00")],
    )
    assert result == ImportResult(notes_imported=1, notebooks_created=1, rows_skipped=0)

    notes = repo.list_notes()
    assert len(notes) == 1
    note = notes[0]
    assert note.body == "# Heading\n\nbody text"  # stored verbatim
    assert note.title == "Heading"  # derived via core.text.derive_title
    assert note.created_at == "2020-01-01 09:30:00"
    assert note.updated_at == "2020-01-01 09:30:00"

    notebooks = repo.list_notebooks()
    assert [nb.name for nb in notebooks] == ["Work"]
    assert note.notebook_id == notebooks[0].id


def test_import_dedupes_notebooks_by_category(repo):
    result = import_legacy_notes(
        repo,
        [
            LegacyNote("one", "Work"),
            LegacyNote("two", "Work"),
            LegacyNote("three", "Personal"),
        ],
    )
    assert result.notes_imported == 3
    assert result.notebooks_created == 2
    assert sorted(nb.name for nb in repo.list_notebooks()) == ["Personal", "Work"]


def test_import_reuses_existing_notebook(repo):
    repo.create_notebook("Work")  # already present in the vault
    result = import_legacy_notes(repo, [LegacyNote("hello", "Work")])
    assert result.notebooks_created == 0  # reused, not duplicated
    assert [nb.name for nb in repo.list_notebooks()] == ["Work"]
    assert repo.list_notes()[0].notebook_id == repo.list_notebooks()[0].id


def test_import_blank_category_goes_to_root(repo):
    import_legacy_notes(
        repo,
        [LegacyNote("rooted", None), LegacyNote("also rooted", "   ")],
    )
    assert repo.list_notebooks() == []
    assert all(note.notebook_id is None for note in repo.list_notes())


def test_import_skips_blank_content(repo):
    result = import_legacy_notes(
        repo,
        [LegacyNote("real", "Work"), LegacyNote("", "Work"), LegacyNote("   ", "Work")],
    )
    assert result == ImportResult(notes_imported=1, notebooks_created=1, rows_skipped=2)
    assert len(repo.list_notes()) == 1


def test_import_blank_timestamp_uses_db_default(repo):
    import_legacy_notes(repo, [LegacyNote("body", None, None, None)])
    note = repo.list_notes()[0]
    # Falls back to the schema's datetime('now') default — a real, non-empty stamp.
    assert note.created_at
    assert note.updated_at


def test_import_normalizes_epoch_timestamp(repo):
    import_legacy_notes(repo, [LegacyNote("body", None, None, "1577871000")])
    note = repo.list_notes()[0]
    assert note.created_at == "2020-01-01 09:30:00"


def test_import_makes_notes_searchable(repo):
    import_legacy_notes(repo, [LegacyNote("the quick brown fox", "Animals")])
    found = repo.search_notes("brown")
    assert len(found) == 1
    assert found[0].body == "the quick brown fox"


def test_import_empty_iterable_is_a_noop(repo):
    assert import_legacy_notes(repo, []) == ImportResult(0, 0, 0)
    assert repo.list_notes() == []


# -- import_legacy_db: end-to-end read + write ------------------------------


def test_import_legacy_db_end_to_end(tmp_path, repo):
    db = _make_legacy_db(
        tmp_path / "notes.db",
        [
            ("First note", "Work", "2020-01-01 09:30:00"),
            ("Second note", "Work", "2020-02-01 12:00:00"),
            ("Loose note", None, "2020-03-01 08:00:00"),
            ("", "Work", "2020-04-01 08:00:00"),  # blank body -> skipped
        ],
        columns=("content", "category", "timestamp"),
    )
    result = import_legacy_db(repo, db)
    assert result == ImportResult(notes_imported=3, notebooks_created=1, rows_skipped=1)

    bodies = {note.body for note in repo.list_notes()}
    assert bodies == {"First note", "Second note", "Loose note"}
    assert [nb.name for nb in repo.list_notebooks()] == ["Work"]
