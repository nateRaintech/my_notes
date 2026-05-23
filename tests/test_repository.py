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
from core.repository import Note, NotFoundError, Notebook, Repository, Tag
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


def _note_tag_count(connection, note_id):
    return connection.execute(
        "SELECT count(*) FROM note_tags WHERE note_id = ?", (note_id,)
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


# -- tags: create / read / delete --------------------------------------------


def test_create_tag_returns_populated_object(repo):
    tag = repo.create_tag("work")
    assert isinstance(tag, Tag)
    assert tag.id > 0
    assert tag.name == "work"


def test_create_tag_duplicate_name_raises(repo):
    repo.create_tag("dup")
    # tags.name has a UNIQUE constraint — a second insert of the same name fails.
    with pytest.raises(sqlcipher.IntegrityError):
        repo.create_tag("dup")


def test_get_tag_roundtrip_and_missing(repo):
    tag = repo.create_tag("idea")
    assert repo.get_tag(tag.id) == tag
    assert repo.get_tag(9999) is None


def test_get_tag_by_name(repo):
    tag = repo.create_tag("urgent")
    assert repo.get_tag_by_name("urgent") == tag
    assert repo.get_tag_by_name("nope") is None


def test_list_tags_orders_by_name_case_insensitive(repo):
    repo.create_tag("zebra")
    repo.create_tag("Apple")
    repo.create_tag("mango")
    assert [t.name for t in repo.list_tags()] == ["Apple", "mango", "zebra"]


def test_delete_tag_returns_bool(repo):
    tag = repo.create_tag("temp")
    assert repo.delete_tag(tag.id) is True
    assert repo.get_tag(tag.id) is None
    assert repo.delete_tag(tag.id) is False  # already gone


def test_delete_tag_detaches_from_notes_but_keeps_them(repo):
    note = repo.create_note(body="kept")
    tag = repo.create_tag("ephemeral")
    repo.add_tag_to_note(note.id, tag.id)
    repo.delete_tag(tag.id)
    # The join row cascades away; the note itself is untouched.
    assert repo.get_note(note.id) is not None
    assert repo.tags_for_note(note.id) == []
    assert _note_tag_count(repo._conn, note.id) == 0


# -- note <-> tag association -------------------------------------------------


def test_add_tag_to_note_and_tags_for_note(repo):
    note = repo.create_note(body="tagged")
    a = repo.create_tag("alpha")
    b = repo.create_tag("beta")
    repo.add_tag_to_note(note.id, b.id)
    repo.add_tag_to_note(note.id, a.id)
    # tags_for_note is ordered by name, regardless of attach order.
    assert repo.tags_for_note(note.id) == [a, b]


def test_add_tag_to_note_is_idempotent(repo):
    note = repo.create_note(body="x")
    tag = repo.create_tag("once")
    repo.add_tag_to_note(note.id, tag.id)
    repo.add_tag_to_note(note.id, tag.id)  # no error, no duplicate
    assert _note_tag_count(repo._conn, note.id) == 1
    assert repo.tags_for_note(note.id) == [tag]


def test_add_tag_to_note_unknown_note_or_tag_raises(repo):
    note = repo.create_note(body="real")
    tag = repo.create_tag("real")
    with pytest.raises(sqlcipher.IntegrityError):
        repo.add_tag_to_note(9999, tag.id)  # no such note
    with pytest.raises(sqlcipher.IntegrityError):
        repo.add_tag_to_note(note.id, 9999)  # no such tag


def test_remove_tag_from_note_returns_bool(repo):
    note = repo.create_note(body="y")
    tag = repo.create_tag("removable")
    repo.add_tag_to_note(note.id, tag.id)
    assert repo.remove_tag_from_note(note.id, tag.id) is True
    assert repo.tags_for_note(note.id) == []
    assert repo.remove_tag_from_note(note.id, tag.id) is False  # already detached


def test_tags_for_note_empty_when_untagged_or_missing(repo):
    note = repo.create_note(body="bare")
    assert repo.tags_for_note(note.id) == []
    assert repo.tags_for_note(9999) == []  # missing note: just no join rows


def test_delete_note_cascades_note_tags(repo):
    note = repo.create_note(body="doomed")
    tag = repo.create_tag("survivor")
    repo.add_tag_to_note(note.id, tag.id)
    repo.delete_note(note.id)
    # The join row cascades with the note; the tag itself survives.
    assert _note_tag_count(repo._conn, note.id) == 0
    assert repo.get_tag(tag.id) == tag


# -- notes: filter by tag -----------------------------------------------------


def test_list_notes_filters_by_tag(repo):
    tag = repo.create_tag("topic")
    tagged = repo.create_note(body="in")
    repo.create_note(body="out")  # untagged, must be excluded
    repo.add_tag_to_note(tagged.id, tag.id)
    assert [n.id for n in repo.list_notes(tag_id=tag.id)] == [tagged.id]


def test_list_notes_by_tag_newest_first(repo):
    tag = repo.create_tag("multi")
    first = repo.create_note(body="first")
    second = repo.create_note(body="second")
    repo.add_tag_to_note(first.id, tag.id)
    repo.add_tag_to_note(second.id, tag.id)
    # Same ordering contract as the unfiltered list: newest updated, id DESC tie.
    assert [n.id for n in repo.list_notes(tag_id=tag.id)] == [second.id, first.id]


def test_list_notes_tag_and_notebook_filters_combine(repo):
    nb = repo.create_notebook("Project")
    tag = repo.create_tag("flag")
    in_nb = repo.create_note(notebook_id=nb.id, body="in notebook")
    at_root = repo.create_note(body="at root")
    repo.add_tag_to_note(in_nb.id, tag.id)
    repo.add_tag_to_note(at_root.id, tag.id)
    # Both carry the tag, but only one is in the notebook — the filters AND.
    assert [n.id for n in repo.list_notes(notebook_id=nb.id, tag_id=tag.id)] == [
        in_nb.id
    ]


def test_list_notes_unfiltered_unaffected_by_tags(repo):
    tag = repo.create_tag("noise")
    a = repo.create_note(body="a")
    b = repo.create_note(body="b")
    repo.add_tag_to_note(a.id, tag.id)
    # A bare list_notes() still returns every note once, regardless of tags.
    assert [n.id for n in repo.list_notes()] == [b.id, a.id]


# -- full-text search -------------------------------------------------------


def test_search_notes_matches_body_and_returns_note_objects(repo):
    target = repo.create_note(title="Recipe", body="how to bake sourdough bread")
    repo.create_note(title="Other", body="completely unrelated text")
    results = repo.search_notes("sourdough")
    assert [n.id for n in results] == [target.id]
    assert isinstance(results[0], Note)
    assert results[0].title == "Recipe"
    assert results[0].body == "how to bake sourdough bread"


def test_search_notes_matches_title(repo):
    target = repo.create_note(title="Quarterly Budget", body="numbers here")
    repo.create_note(title="Vacation", body="beach plans")
    assert [n.id for n in repo.search_notes("budget")] == [target.id]


def test_search_notes_spans_all_notebooks_and_root(repo):
    work = repo.create_notebook("Work")
    home = repo.create_notebook("Home")
    a = repo.create_note(notebook_id=work.id, title="A", body="shared keyword alpha")
    b = repo.create_note(notebook_id=home.id, title="B", body="shared keyword beta")
    c = repo.create_note(title="C", body="shared keyword gamma")  # root note
    found = {n.id for n in repo.search_notes("shared")}
    assert found == {a.id, b.id, c.id}


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
def test_search_notes_empty_query_returns_empty(repo, query):
    repo.create_note(title="Note", body="some content")
    assert repo.search_notes(query) == []


def test_search_notes_no_match_returns_empty(repo):
    repo.create_note(title="Note", body="apples and oranges")
    assert repo.search_notes("bananas") == []


def test_search_notes_multi_word_requires_all_terms(repo):
    both = repo.create_note(title="Both", body="alpha and beta together")
    repo.create_note(title="One", body="only alpha here")
    # Implicit AND: only the note containing every word matches.
    assert [n.id for n in repo.search_notes("alpha beta")] == [both.id]


def test_search_notes_ranks_more_relevant_first(repo):
    low = repo.create_note(
        title="Misc",
        body="a long note that happens to mention python once amid many words",
    )
    high = repo.create_note(title="Python", body="python python python tutorial")
    # The note with the term in its title and repeated in a short body is the
    # stronger bm25 match, so it sorts ahead of the incidental mention.
    assert [n.id for n in repo.search_notes("python")] == [high.id, low.id]


@pytest.mark.parametrize(
    "query",
    ['"', "*", "AND", "OR", "NOT", "-foo", "foo:", "(unbalanced", "NEAR(a b)", '""'],
)
def test_search_notes_metacharacters_do_not_raise(repo, query):
    repo.create_note(title="Note", body="ordinary content")
    # Arbitrary search-box input must be treated as literal terms, never as FTS5
    # query syntax — so none of these raise sqlcipher3.OperationalError.
    assert isinstance(repo.search_notes(query), list)


def test_search_notes_treats_operator_keyword_as_literal(repo):
    # Unquoted, MATCH 'OR' is a syntax error; quoted, it matches the literal
    # token "or" (tokenization is case-insensitive).
    target = repo.create_note(title="Mix", body="salt or pepper")
    repo.create_note(title="Plain", body="just salt")
    assert [n.id for n in repo.search_notes("OR")] == [target.id]


def test_search_notes_limit_caps_results(repo):
    for i in range(3):
        repo.create_note(title=f"N{i}", body="recurring keyword")
    assert len(repo.search_notes("recurring")) == 3
    assert len(repo.search_notes("recurring", limit=2)) == 2


def test_search_notes_reflects_edits_via_triggers(repo):
    note = repo.create_note(title="Draft", body="this is a draft")
    assert [n.id for n in repo.search_notes("draft")] == [note.id]

    repo.update_note(note.id, title="Final", body="this is the final version")
    # The FTS triggers re-index on update, so search sees the change immediately.
    assert repo.search_notes("draft") == []
    assert [n.id for n in repo.search_notes("final")] == [note.id]


def test_search_notes_reflects_deletes_via_triggers(repo):
    note = repo.create_note(title="Temp", body="ephemeral entry")
    assert [n.id for n in repo.search_notes("ephemeral")] == [note.id]
    repo.delete_note(note.id)
    assert repo.search_notes("ephemeral") == []


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
