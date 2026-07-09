import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.repository import Repository

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtWidgets import QPlainTextEdit  # noqa: E402

from ui.note_tab import NoteTab  # noqa: E402

DEBOUNCE = 1.0


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


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


def test_note_tab_loads_a_note_and_autosaves_edits(qapp, repo):
    note = repo.create_note(title="N", body="hello")
    tab = NoteTab(repo, debounce=DEBOUNCE, clock=FakeClock())

    tab.load(note)
    assert isinstance(tab.source, QPlainTextEdit)
    assert tab.markdown() == "hello"
    assert tab.note_id == note.id

    tab.source.setPlainText("hello world")
    assert tab.flush() is True
    assert repo.get_note(note.id).body == "hello world"


def test_typing_into_an_unbound_tab_signals_orphan_then_binds(qapp, repo):
    tab = NoteTab(repo, debounce=DEBOUNCE, clock=FakeClock())
    seen: list[str] = []

    def handle(text: str) -> None:
        seen.append(text)
        note = repo.create_note(notebook_id=None)
        tab.bind_new_note(note)

    tab.orphan_edit_detected.connect(handle)
    assert tab.note_id is None

    tab.source.setPlainText("fresh thought")

    assert seen == ["fresh thought"]
    assert tab.note_id is not None
    assert tab.flush() is True
    assert repo.get_note(tab.note_id).body == "fresh thought"
