"""Integration tests for debounced auto-save in the Qt layer.

Drives the real path the running app uses — a :class:`ui.editor.MarkdownEditor`
wired by :class:`ui.autosave.AutoSaveController` to a
:class:`core.repository.Repository` over a real (in-memory) SQLCipher connection —
and proves a typed edit lands in the database after a debounced flush, with no
real-time waiting. The debounce window is driven by an injected :class:`FakeClock`,
and a tick is simulated by calling ``saver.flush_if_due()`` (exactly what the
controller's ``QTimer`` calls each tick).

Guarded by ``importorskip`` and run headless via the ``offscreen`` Qt platform,
matching the other Qt tests so the merge gate stays green wherever Qt is present.
"""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.repository import Repository

# Select the headless platform before any Qt import instantiates a plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.autosave import AutoSaveController  # noqa: E402
from ui.editor import MarkdownEditor  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

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
    """A process-wide QApplication (singleton) for widget construction."""
    yield QApplication.instance() or QApplication([])


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


def test_typed_edit_persists_after_debounced_flush(qapp, repo):
    note = repo.create_note(title="Untitled", body="")
    editor = MarkdownEditor()
    clock = FakeClock()
    controller = AutoSaveController(editor, repo, debounce=DEBOUNCE, clock=clock)
    controller.load_note(note)

    # The user types into the source pane (emits textChanged → saver.edit).
    editor.source.setPlainText("# Shopping\n\nmilk and eggs")

    # Before the debounce elapses, nothing is written.
    clock.advance(DEBOUNCE - 0.01)
    assert controller.saver.flush_if_due() is False
    assert repo.get_note(note.id).body == ""

    # Once typing settles for a full debounce window, the edit lands in the DB.
    clock.advance(0.01)
    assert controller.saver.flush_if_due() is True
    saved = repo.get_note(note.id)
    assert saved.body == "# Shopping\n\nmilk and eggs"
    assert saved.title == "Shopping"  # re-derived from the body


def test_loading_a_note_does_not_trigger_a_spurious_save(qapp, repo):
    note = repo.create_note(title="Greeting", body="hello")
    editor = MarkdownEditor()
    clock = FakeClock()
    controller = AutoSaveController(editor, repo, debounce=DEBOUNCE, clock=clock)

    controller.load_note(note)
    # set_markdown emitted textChanged with the loaded body, but it equals the
    # baseline, so the note is not dirty and no write is scheduled.
    assert controller.saver.is_dirty is False
    assert editor.markdown() == "hello"
    clock.advance(DEBOUNCE * 2)
    assert controller.saver.flush_if_due() is False


def test_switching_notes_flushes_the_previous_one(qapp, repo):
    first = repo.create_note(title="First", body="first")
    second = repo.create_note(title="Second", body="second")
    editor = MarkdownEditor()
    controller = AutoSaveController(editor, repo, debounce=DEBOUNCE, clock=FakeClock())

    controller.load_note(first)
    editor.source.setPlainText("first edited")  # pending, debounce not elapsed

    # Switching notes flushes the outgoing note immediately, so its edit isn't lost.
    controller.load_note(second)
    assert repo.get_note(first.id).body == "first edited"
    assert editor.markdown() == "second"


def test_flush_persists_pending_edit_immediately(qapp, repo):
    note = repo.create_note(title="Note", body="")
    editor = MarkdownEditor()
    controller = AutoSaveController(editor, repo, debounce=DEBOUNCE, clock=FakeClock())
    controller.load_note(note)

    editor.source.setPlainText("written without waiting")
    assert controller.flush() is True
    assert repo.get_note(note.id).body == "written without waiting"


def test_typing_into_an_unbound_editor_emits_orphan_edit(qapp, repo):
    editor = MarkdownEditor()
    controller = AutoSaveController(editor, repo, debounce=DEBOUNCE, clock=FakeClock())
    seen: list[str] = []
    controller.orphan_edit_detected.connect(seen.append)

    # No note is loaded, so the first keystroke is an "orphan" edit that belongs
    # to no note yet — the controller signals it so the UI can create one.
    editor.source.setPlainText("orphan thoughts")

    assert seen == ["orphan thoughts"]


def test_no_orphan_signal_once_a_note_is_bound(qapp, repo):
    note = repo.create_note(title="Bound", body="")
    editor = MarkdownEditor()
    controller = AutoSaveController(editor, repo, debounce=DEBOUNCE, clock=FakeClock())
    controller.load_note(note)
    seen: list[str] = []
    controller.orphan_edit_detected.connect(seen.append)

    # A note is bound, so edits are tracked normally and no orphan is signalled.
    editor.source.setPlainText("typed into a real note")

    assert seen == []


def test_clearing_an_unbound_editor_emits_no_orphan_edit(qapp, repo):
    editor = MarkdownEditor()
    controller = AutoSaveController(editor, repo, debounce=DEBOUNCE, clock=FakeClock())
    seen: list[str] = []
    controller.orphan_edit_detected.connect(seen.append)

    # Empty text in an unbound editor is not worth a note — no signal.
    editor.source.setPlainText("")

    assert seen == []


# -- MainWindow seam --------------------------------------------------------


def test_main_window_autosave_unbound_by_default(qapp):
    window = MainWindow()
    assert window.autosave is None


def test_main_window_bind_autosave_then_load_and_persist(qapp, repo):
    note = repo.create_note(title="Untitled", body="")
    window = MainWindow()
    controller = window.bind_autosave(repo, debounce=DEBOUNCE)

    assert window.autosave is controller
    window.load_note(note)
    window.editor.source.setPlainText("typed in the main window")
    assert controller.flush() is True
    assert repo.get_note(note.id).body == "typed in the main window"


def test_main_window_load_note_without_autosave_still_shows_body(qapp, repo):
    note = repo.create_note(title="Read only", body="just showing this")
    window = MainWindow()  # no bind_autosave: editor edits text with nowhere to persist
    window.load_note(note)
    assert window.editor.markdown() == "just showing this"


def test_typing_with_no_note_loaded_creates_and_binds_a_note(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()
    # Fresh launch: nothing is loaded, so the editor is unbound.
    assert window.autosave.saver.note_id is None

    window.editor.source.setPlainText("a thought I started writing")

    # The first keystroke created a real note and bound it to the editor.
    assert window.autosave.saver.note_id is not None
    assert len(repo.list_notes()) == 1
    # The new note is the selected row in the list.
    from PySide6.QtCore import Qt

    current = window.note_list.currentItem()
    assert current is not None
    assert current.data(Qt.ItemDataRole.UserRole).id == window.autosave.saver.note_id


def test_orphan_text_is_saved_when_navigating_to_another_note(qapp, repo):
    existing = repo.create_note(title="Old", body="old body")
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    # Type with nothing loaded, then click an existing note in the navigator.
    window.editor.source.setPlainText("brand new idea")
    window._select_note(existing.id)

    # The in-progress text was persisted as its own note, not lost...
    bodies = {n.body for n in repo.list_notes()}
    assert bodies == {"old body", "brand new idea"}
    # ...and the editor now shows the note that was clicked.
    assert window.editor.markdown() == "old body"


def test_typing_in_new_note_does_not_create_a_second_note(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    # New Note already binds a note; typing into it must not spawn another.
    window.new_note()
    window.editor.source.setPlainText("groceries")
    window.editor.source.setPlainText("groceries and milk")

    assert len(repo.list_notes()) == 1
