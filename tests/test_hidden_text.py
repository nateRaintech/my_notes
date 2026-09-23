"""Unit tests for hidden text: the vault store and the Markdown references (#113).

Pure Python, no Qt.
"""

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.hidden_text import HiddenTextStore, hidden_markdown, parse_url, referenced_ids
from core.repository import Repository


@pytest.fixture
def conn():
    c = sqlcipher.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    schema.migrate(c)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def store(conn):
    return HiddenTextStore(conn)


# -- migration ----------------------------------------------------------------


def test_migration_4_upgrades_a_v3_vault_and_keeps_its_notes():
    c = sqlcipher.connect(":memory:")
    for script in (schema._MIGRATION_1, schema._MIGRATION_2, schema._MIGRATION_3):
        c.executescript(script)
    c.execute("PRAGMA user_version = 3")
    c.execute("INSERT INTO notes (title, body) VALUES ('kept', 'body')")
    c.commit()

    assert schema.migrate(c) == 4
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "hidden_texts" in tables
    assert c.execute("SELECT title FROM notes").fetchall() == [("kept",)]
    assert schema.migrate(c) == 4  # idempotent


# -- store --------------------------------------------------------------------


def test_add_then_get_round_trips_the_value(store):
    hidden_id = store.add("hunter2")
    assert hidden_id > 0
    assert store.get(hidden_id) == "hunter2"


def test_values_keep_whitespace_and_newlines_verbatim(store):
    value = "  line one\nline two  "
    assert store.get(store.add(value)) == value


def test_identical_values_get_separate_rows(store):
    assert store.add("same") != store.add("same")


def test_add_refuses_a_blank_value(store):
    with pytest.raises(ValueError):
        store.add("   \n ")


def test_get_unknown_id_is_none(store):
    assert store.get(999) is None


def test_update_replaces_the_value(store):
    hidden_id = store.add("old")
    assert store.update(hidden_id, "new") is True
    assert store.get(hidden_id) == "new"


def test_update_unknown_id_is_false(store):
    assert store.update(999, "x") is False


def test_update_refuses_a_blank_value(store):
    hidden_id = store.add("old")
    with pytest.raises(ValueError):
        store.update(hidden_id, "")
    assert store.get(hidden_id) == "old"


def test_sweep_removes_only_values_no_note_mentions(conn, store):
    kept = store.add("kept")
    in_code = store.add("in code")
    orphan = store.add("orphan")
    repo = Repository(conn)
    repo.create_note(title="a", body=f"pw: {hidden_markdown(kept)}")
    repo.create_note(title="b", body=f"`{hidden_markdown(in_code)}`")

    assert store.sweep_orphans() == 1
    assert store.get(kept) == "kept"
    assert store.get(in_code) == "in code"  # liberal: a mention anywhere keeps it
    assert store.get(orphan) is None


def test_ids_are_never_reused_after_a_sweep(store):
    first = store.add("gone")
    store.sweep_orphans()
    assert store.add("next") > first


# -- references -----------------------------------------------------------------


def test_hidden_markdown_builds_an_image_reference():
    assert hidden_markdown(7) == "![hidden](mnsec:7)"
    assert hidden_markdown(7, "VPN [admin]") == "![VPN admin](mnsec:7)"


def test_parse_url():
    assert parse_url("mnsec:7") == 7
    assert parse_url("mnimg:7") is None
    assert parse_url("mnsec:abc") is None
    assert parse_url("mnsec:7?w=5") is None


def test_referenced_ids_counts_every_mention():
    assert referenced_ids("x mnsec:3 `![a](mnsec:4)` mnimg:5") == {3, 4}
