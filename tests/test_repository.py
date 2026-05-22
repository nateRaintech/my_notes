"""Unit tests for ``core.repository`` — CRUD over notes and notebooks.

Pure Python, no Qt. Most tests run against an in-memory ``sqlcipher3``
connection (migrated, foreign keys on — exactly how the vault opens one), so they
exercise the real SQLite/FTS5 build without the cost of key derivation. A couple
of vault-level round-trips prove the repository works over a real encrypted
``Vault.connection`` and that rows survive a lock/unlock cycle.
"""

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.crypto import KdfParams
from core.repository import Note, NotFoundError, Notebook, Repository
from core.vault import Vault

# Minimal valid Argon2 params keep the vault-level tests' key derivation cheap.
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
PASSWORD = "correct horse battery staple"


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


def _fts_matches(connection, term):
    return connection.execute(
        "SELECT count(*) FROM notes_fts WHERE notes_fts MATCH ?", (term,)
    ).fetchone()[0]


# -- notebooks: create / read ----------------------------------------------


def test_create_notebook_returns_populated_object(repo):
    nb = repo.create_notebook("Work")
    assert isinstance(nb, Notebook)
    assert nb.id > 0
    assert nb.name == "Work"
    assert nb.parent_id is None
    assert nb.created_at and nb.updated_at  # timestamps filled by the DB defaults


def test_create_notebook_nests_under_parent(repo):
    parent = repo.create_notebook("Parent")
    child = repo.create_notebook("Child", parent_id=parent.id)
    assert child.parent_id == parent.id


def test_get_notebook_roundtrip_and_missing(repo):
    nb = repo.create_notebook("Recipes")
    assert repo.get_notebook(nb.id) == nb
    assert repo.get_notebook(9999) is None


def test_list_notebooks_orders_by_name_case_insensitive(repo):
    repo.create_notebook("zebra")
    repo.create_notebook("Apple")
    repo.create_notebook("mango")
    assert [nb.name for nb in repo.list_notebooks()] == ["Apple", "mango", "zebra"]


# -- notebooks: update / delete ---------------------------------------------


def test_update_notebook_renames(repo):
    nb = repo.create_notebook("Drat")
    updated = repo.update_notebook(nb.id, name="Draft")
    assert updated.name == "Draft"
    assert repo.get_notebook(nb.id).name == "Draft"


def test_update_notebook_reparents(repo):
    a = repo.create_notebook("A")
    b = repo.create_notebook("B")
    updated = repo.update_notebook(b.id, parent_id=a.id)
    assert updated.parent_id == a.id


def test_update_notebook_missing_raises(repo):
    with pytest.raises(NotFoundError):
        repo.update_notebook(9999, name="nope")


def test_delete_notebook_returns_bool(repo):
    nb = repo.create_notebook("Temp")
    assert repo.delete_notebook(nb.id) is True
    assert repo.get_notebook(nb.id) is None
    assert repo.delete_notebook(nb.id) is False  # already gone


def test_delete_notebook_cascades_to_children(repo):
    parent = repo.create_notebook("Parent")
    child = repo.create_notebook("Child", parent_id=parent.id)
    repo.delete_notebook(parent.id)
    # ON DELETE CASCADE removes the descendant notebook too.
    assert repo.get_notebook(child.id) is None


def test_delete_notebook_orphans_notes_to_root(repo):
    nb = repo.create_notebook("Box")
    note = repo.create_note(notebook_id=nb.id, body="kept")
    repo.delete_notebook(nb.id)
    # ON DELETE SET NULL keeps the note but moves it to the root.
    survivor = repo.get_note(note.id)
    assert survivor is not None
    assert survivor.notebook_id is None


# -- notes: create / read ----------------------------------------------------


def test_create_note_defaults(repo):
    note = repo.create_note()
    assert isinstance(note, Note)
    assert note.id > 0
    assert note.notebook_id is None
    assert note.title == ""
    assert note.body == ""
    assert note.created_at and note.updated_at


def test_create_note_with_fields(repo):
    nb = repo.create_notebook("Journal")
    note = repo.create_note(notebook_id=nb.id, title="Day 1", body="hello world")
    assert note.notebook_id == nb.id
    assert note.title == "Day 1"
    assert note.body == "hello world"


def test_get_note_roundtrip_and_missing(repo):
    note = repo.create_note(title="t", body="b")
    assert repo.get_note(note.id) == note
    assert repo.get_note(9999) is None


def test_create_note_with_unknown_notebook_raises(repo):
    # FK is enforced through the repository — a dangling notebook_id is rejected.
    with pytest.raises(sqlcipher.IntegrityError):
        repo.create_note(notebook_id=4242, body="orphan")


# -- notes: list / filter ----------------------------------------------------


def test_list_notes_returns_all_newest_first(repo):
    first = repo.create_note(body="first")
    second = repo.create_note(body="second")
    # Same-second timestamps tie on updated_at; the id DESC tiebreaker is
    # deterministic, so the newer note sorts first.
    assert [n.id for n in repo.list_notes()] == [second.id, first.id]


def test_list_notes_filters_by_notebook(repo):
    nb = repo.create_notebook("Filtered")
    inside = repo.create_note(notebook_id=nb.id, body="in")
    repo.create_note(body="out")  # root note, must be excluded
    assert [n.id for n in repo.list_notes(notebook_id=nb.id)] == [inside.id]


def test_list_notes_filter_none_returns_root_notes(repo):
    nb = repo.create_notebook("Filed")
    repo.create_note(notebook_id=nb.id, body="filed")
    root = repo.create_note(body="loose")
    assert [n.id for n in repo.list_notes(notebook_id=None)] == [root.id]


# -- notes: update / delete --------------------------------------------------


def test_update_note_changes_title_and_body(repo):
    note = repo.create_note(title="old", body="stale")
    updated = repo.update_note(note.id, title="new", body="fresh")
    assert (updated.title, updated.body) == ("new", "fresh")
    assert repo.get_note(note.id).body == "fresh"


def test_update_note_partial_leaves_other_fields(repo):
    note = repo.create_note(title="keep", body="change me")
    updated = repo.update_note(note.id, body="changed")
    assert updated.title == "keep"  # untouched
    assert updated.body == "changed"


def test_update_note_moves_between_notebooks(repo):
    a = repo.create_notebook("A")
    b = repo.create_notebook("B")
    note = repo.create_note(notebook_id=a.id, body="movable")
    moved = repo.update_note(note.id, notebook_id=b.id)
    assert moved.notebook_id == b.id
    # And back to the root.
    rooted = repo.update_note(note.id, notebook_id=None)
    assert rooted.notebook_id is None


def test_update_note_keeps_fts_index_in_sync(repo):
    note = repo.create_note(title="t", body="apples")
    assert _fts_matches(repo._conn, "apples") == 1
    repo.update_note(note.id, body="oranges")
    # The update trigger re-indexes: old term gone, new term present.
    assert _fts_matches(repo._conn, "apples") == 0
    assert _fts_matches(repo._conn, "oranges") == 1


def test_update_note_missing_raises(repo):
    with pytest.raises(NotFoundError):
        repo.update_note(9999, body="nope")


def test_delete_note_returns_bool(repo):
    note = repo.create_note(body="bye")
    assert repo.delete_note(note.id) is True
    assert repo.get_note(note.id) is None
    assert repo.delete_note(note.id) is False  # already gone


# -- vault integration: persists encrypted across a lock/unlock cycle --------


def test_repository_over_vault_persists_across_unlock(tmp_path):
    path = tmp_path / "notes.vault"
    vault = Vault.create(path, PASSWORD, FAST)
    try:
        nb = Repository(vault.connection).create_notebook("Personal")
        note = Repository(vault.connection).create_note(
            notebook_id=nb.id, title="Hello", body="encrypted body"
        )
    finally:
        vault.lock()

    reopened = Vault(path)
    reopened.unlock(PASSWORD)
    try:
        repo = Repository(reopened.connection)
        restored = repo.get_note(note.id)
        assert restored is not None
        assert restored.title == "Hello"
        assert restored.body == "encrypted body"
        assert restored.notebook_id == nb.id
        assert [n.name for n in repo.list_notebooks()] == ["Personal"]
    finally:
        reopened.lock()
