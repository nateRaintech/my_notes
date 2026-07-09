import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.repository import Repository

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.note_tab import NoteTab  # noqa: E402
from ui.tabbed_editor import TabbedEditor  # noqa: E402

DEBOUNCE = 1.0


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def repo():
    conn = sqlcipher.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    schema.migrate(conn)
    try:
        yield Repository(conn)
    finally:
        conn.close()


def test_open_creates_a_tab_and_focuses_it(qapp, repo):
    a = repo.create_note(title="A", body="a body")
    te = TabbedEditor(repo, debounce=DEBOUNCE)

    tab = te.open(a)

    assert isinstance(tab, NoteTab)
    assert te.count() == 1
    assert te.active_tab is tab
    assert te.active_tab.markdown() == "a body"


def test_open_same_note_focuses_existing_tab_no_duplicate(qapp, repo):
    a = repo.create_note(title="A", body="a body")
    b = repo.create_note(title="B", body="b body")
    te = TabbedEditor(repo, debounce=DEBOUNCE)

    tab_a = te.open(a)
    te.open(b)
    again = te.open(a)

    assert again is tab_a          # same tab reused
    assert te.count() == 2         # not three
    assert te.active_tab is tab_a  # focused


def test_close_removes_the_tab(qapp, repo):
    a = repo.create_note(title="A", body="a body")
    te = TabbedEditor(repo, debounce=DEBOUNCE)
    te.open(a)

    te.close_tab(te.active_tab)

    assert te.count() == 0
    assert te.active_tab is None


def test_flush_all_persists_every_open_tab(qapp, repo):
    a = repo.create_note(title="A", body="a")
    b = repo.create_note(title="B", body="b")
    te = TabbedEditor(repo, debounce=DEBOUNCE)
    ta = te.open(a)
    tb = te.open(b)
    ta.source.setPlainText("a edited")
    tb.source.setPlainText("b edited")

    te.flush_all()

    assert repo.get_note(a.id).body == "a edited"
    assert repo.get_note(b.id).body == "b edited"


def test_clear_all_flushes_then_removes_every_tab(qapp, repo):
    a = repo.create_note(title="A", body="a")
    te = TabbedEditor(repo, debounce=DEBOUNCE)
    ta = te.open(a)
    ta.source.setPlainText("a edited")

    te.clear_all()

    assert te.count() == 0
    assert te.active_tab is None
    assert repo.get_note(a.id).body == "a edited"  # flushed before wipe


def test_active_tab_changed_fires_on_open_and_close(qapp, repo):
    a = repo.create_note(title="A", body="a")
    te = TabbedEditor(repo, debounce=DEBOUNCE)
    fired = []
    te.active_tab_changed.connect(lambda: fired.append(True))

    te.open(a)
    te.close_tab(te.active_tab)

    assert len(fired) >= 2
