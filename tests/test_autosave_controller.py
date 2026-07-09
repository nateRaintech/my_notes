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
    window.bind_autosave(repo, debounce=DEBOUNCE)

    window.load_note(note)  # opens a tab bound to the note
    window.editor.source.setPlainText("typed in the main window")
    assert window.autosave.flush() is True
    assert repo.get_note(note.id).body == "typed in the main window"


def test_main_window_load_note_opens_a_tab_showing_the_body(qapp, repo):
    note = repo.create_note(title="Read only", body="just showing this")
    window = MainWindow()
    window.bind_autosave(repo)
    window.load_note(note)  # opening a note requires a bound repository now
    assert window.editor.markdown() == "just showing this"


def test_new_note_creates_binds_and_selects_a_note(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    note = window.new_note()  # creates a note and opens it in a bound tab

    # The active tab is bound to the new note, and it is the selected list row.
    assert window.autosave.saver.note_id == note.id
    assert len(repo.list_notes()) == 1
    from PySide6.QtCore import Qt

    current = window.note_list.currentItem()
    assert current is not None
    assert current.data(Qt.ItemDataRole.UserRole).id == note.id


def test_navigating_between_notes_keeps_each_tab_intact(qapp, repo):
    a = repo.create_note(title="A", body="a body")
    b = repo.create_note(title="B", body="b body")
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    window._select_note(a.id)  # opens tab A
    window.editor.source.setPlainText("a body edited in place")
    window._select_note(b.id)  # opens tab B; tab A is untouched (and flushed)
    assert window.editor.markdown() == "b body"

    # Both notes persisted; A's in-progress edit was never overwritten.
    bodies = {n.body for n in repo.list_notes()}
    assert bodies == {"a body edited in place", "b body"}
    window._select_note(a.id)  # back to tab A
    assert window.editor.markdown() == "a body edited in place"


def test_typing_in_new_note_does_not_create_a_second_note(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    # New Note already binds a note; typing into it must not spawn another.
    window.new_note()
    window.editor.source.setPlainText("groceries")
    window.editor.source.setPlainText("groceries and milk")

    assert len(repo.list_notes()) == 1


def test_active_tab_title_follows_the_notes_first_line(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    window.new_note()  # opens a fresh tab, initially "Untitled"
    tabs = window.tabbed_editor._tabs
    assert tabs.tabText(tabs.currentIndex()) == "Untitled"

    window.editor.source.setPlainText("# Shopping\n\nmilk and eggs")

    assert tabs.tabText(tabs.currentIndex()) == "Shopping"


def test_clicking_a_note_whose_tab_was_closed_reopens_it(qapp, repo):
    """Closing a tab (Ctrl+W) leaves its row selected; clicking it must reopen it.

    currentItemChanged does not fire for an already-selected row, so reopening
    relies on the itemClicked seam.
    """
    note = repo.create_note(title="A", body="a body")
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()
    window._select_note(note.id)  # opens the tab

    window.tabbed_editor.close_tab(window.tabbed_editor.active_tab)  # Ctrl+W
    assert window.tabbed_editor.active_tab is None

    window._on_note_clicked(window.note_list.currentItem())  # click the same row

    assert window.tabbed_editor.active_tab is not None
    assert window.editor.markdown() == "a body"


def test_reopening_a_closed_note_shows_its_saved_text(qapp, repo):
    """#92: reopening a note from a stale list row must show its saved text.

    Type into a new note (autosave writes it to the vault), close its tab, then
    reopen from the list. The row still holds the empty snapshot captured at
    creation, so selection must read fresh from the vault, not the stale body.
    """
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    note = window.new_note()
    window.editor.source.setPlainText("brand new idea")
    window.flush_pending()               # persist to the vault
    window.tabbed_editor.clear_all()     # close the tab (drop the live copy)
    window.note_list.setCurrentRow(-1)   # deselect so re-selecting fires a change

    window._select_note(note.id)         # reopen from the stale list row
    assert window.editor.markdown() == "brand new idea"


def test_reselecting_an_edited_note_shows_current_body_not_stale_snapshot(qapp, repo):
    """General staleness: re-clicking any edited note must show the saved body.

    The note list caches a Note snapshot per row; an autosave updates the vault
    without refreshing that snapshot, so selection must read fresh from the repo.
    """
    a = repo.create_note(title="A", body="a body")
    b = repo.create_note(title="B", body="b body")
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    window._select_note(a.id)
    window.editor.source.setPlainText("a body edited")  # pending edit on A
    window._select_note(b.id)  # flushes A to the vault
    window._select_note(a.id)  # re-open A

    assert window.editor.markdown() == "a body edited"


def test_autocreated_note_shows_its_title_in_the_list_after_navigating(qapp, repo):
    """The auto-created note must list under its derived title, not "Untitled".

    It is created empty (listed as "Untitled"); once typing fills it and the user
    navigates away (flushing it), the list should reflect the saved title.
    """
    existing = repo.create_note(title="Old", body="old body")
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    window.new_note()
    window.editor.source.setPlainText("Buy milk\n\nand eggs")
    window._select_note(existing.id)  # switching flushes the new note + refreshes its row

    labels = [window.note_list.item(i).text() for i in range(window.note_list.count())]
    assert "Buy milk" in labels
    assert "Untitled" not in labels


def test_selecting_a_note_does_not_rebuild_the_whole_list(qapp, repo):
    """Selection must be cheap: no full list re-query/rebuild per click.

    Rebuilding every row on each selection froze the app on real-sized vaults.
    Navigating refreshes only the row we left (its title may have changed), never
    the entire list.
    """
    a = repo.create_note(title="A", body="a body")
    b = repo.create_note(title="B", body="b body")
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()

    rebuilds: list = []
    original = window._populate_note_list
    window._populate_note_list = lambda notes: (rebuilds.append(notes), original(notes))[1]

    window._select_note(a.id)
    window._select_note(b.id)
    window._select_note(a.id)

    assert rebuilds == []  # navigation never repopulates the full list
