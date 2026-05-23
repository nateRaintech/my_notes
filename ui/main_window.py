"""The application's main window.

The app shell: a horizontal three-pane layout — notebooks/tags tree, note list,
and editor — held in a resizable :class:`QSplitter`. This is the frame every
other M3/M4 piece plugs into (ROADMAP.md): the Markdown editor replaces the
editor pane, note-list population and auto-save fill the list, tags extend the
tree, and the M4 unlock flow feeds them from ``core.repository.Repository`` over
the keyed vault connection.

This module builds the *shell* — the named, typed panes those capabilities
populate. Data binding stays out until a vault is opened: the editor edits text
with nowhere to persist until :meth:`MainWindow.bind_autosave` is called with a
keyed repository (the M4 unlock flow does this), at which point :meth:`load_note`
opens a note for editing and debounced auto-save persists it.

Per CLAUDE.md's strict layering, the UI layer may import Qt freely; ``core/``
must never import this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QSplitter,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from core.autosave import DEFAULT_DEBOUNCE_SECONDS
from core.text import derive_title
from ui.autosave import AutoSaveController
from ui.editor import MarkdownEditor
from ui.quick_switcher import QuickSwitcher

if TYPE_CHECKING:
    from core.repository import Note, Repository

WINDOW_TITLE = "my_notes"
DEFAULT_SIZE = (1000, 700)

# Initial pane widths (sidebar, note list, editor), summing to the default
# window width. The editor pane is widest; the stretch factors below make it
# absorb window resizing.
PANE_DEFAULT_SIZES = (220, 300, 480)

# Minimum width each pane keeps so a drag can't squeeze one to nothing (paired
# with setChildrenCollapsible(False) to preserve the 3-pane invariant).
_SIDEBAR_MIN_WIDTH = 140
_NOTE_LIST_MIN_WIDTH = 180
_EDITOR_MIN_WIDTH = 240


class MainWindow(QMainWindow):
    """Top-level application window: the resizable 3-pane shell.

    The three panes are exposed as attributes so later capabilities and tests can
    reach them:

    * :attr:`notebook_tree` — the notebooks/tags tree (left).
    * :attr:`note_list` — the note list / search results (middle), sitting below
      :attr:`search_input` inside the composite :attr:`note_pane`.
    * :attr:`editor` — the Markdown editor pane (right): editable source beside a
      live-rendered preview (see :class:`ui.editor.MarkdownEditor`).

    :attr:`search_input` filters :attr:`note_list` live (full-text search via
    :meth:`core.repository.Repository.search_notes`); selecting a row loads that note
    into the editor. **Ctrl+P** opens the quick-switcher
    (:class:`ui.quick_switcher.QuickSwitcher`) to jump to any note by fuzzy title
    match. :attr:`splitter` is the central :class:`QSplitter` holding the three
    (logical) panes. :attr:`autosave` is the debounced auto-save controller and
    :attr:`repository` the keyed data layer, both ``None`` until :meth:`bind_autosave`
    is called by the M4 unlock flow.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(*DEFAULT_SIZE)

        # No repository until a vault is opened; auto-save is bound later.
        self.autosave: AutoSaveController | None = None
        self.repository: Repository | None = None

        self.notebook_tree = QTreeWidget()
        self.notebook_tree.setHeaderLabel("Notebooks")
        self.notebook_tree.setMinimumWidth(_SIDEBAR_MIN_WIDTH)

        # Middle pane: a search box above the note list, wrapped in a composite
        # widget so the splitter still holds three (logical) panes. Typing filters
        # the list (full-text search); selecting a row loads that note.
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search notes…")
        self.search_input.setClearButtonEnabled(True)

        self.note_list = QListWidget()

        self.note_pane = QWidget()
        self.note_pane.setMinimumWidth(_NOTE_LIST_MIN_WIDTH)
        note_pane_layout = QVBoxLayout(self.note_pane)
        note_pane_layout.setContentsMargins(0, 0, 0, 0)
        note_pane_layout.addWidget(self.search_input)
        note_pane_layout.addWidget(self.note_list)

        self.editor = MarkdownEditor()
        self.editor.setMinimumWidth(_EDITOR_MIN_WIDTH)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.notebook_tree)
        self.splitter.addWidget(self.note_pane)
        self.splitter.addWidget(self.editor)

        # Keep all three panes visible: a drag can resize but not collapse one.
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setSizes(list(PANE_DEFAULT_SIZES))
        # Only the editor pane (index 2) grows when the window widens.
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setStretchFactor(2, 1)

        self.setCentralWidget(self.splitter)

        # Live filtering and click-to-load. Selection only loads on an explicit
        # user action — list rebuilds block signals (see _populate_note_list).
        self.search_input.textChanged.connect(self._on_search_changed)
        self.note_list.currentItemChanged.connect(self._on_note_selected)

        # Ctrl+P opens the quick-switcher to jump to a note by fuzzy title match.
        self.quick_switch_shortcut = QShortcut(QKeySequence("Ctrl+P"), self)
        self.quick_switch_shortcut.activated.connect(self.open_quick_switcher)

        self.statusBar().showMessage("Ready")

    def bind_autosave(
        self,
        repository: Repository,
        *,
        debounce: float = DEFAULT_DEBOUNCE_SECONDS,
    ) -> AutoSaveController:
        """Attach debounced auto-save to the editor, backed by ``repository``.

        Constructs an :class:`~ui.autosave.AutoSaveController` over :attr:`editor`,
        stores it as :attr:`autosave`, and keeps ``repository`` as :attr:`repository`
        so the note list can be populated and searched. The M4 unlock flow calls this
        once it has a keyed :class:`~core.repository.Repository`; until then the editor
        edits text with nowhere to persist and the note list stays empty.
        """
        self.repository = repository
        self.autosave = AutoSaveController(
            self.editor, repository, debounce=debounce, parent=self
        )
        return self.autosave

    def load_note(self, note: Note) -> None:
        """Load ``note`` into the editor for editing (and debounced auto-saving).

        If auto-save is bound, this also flushes the previously-open note and binds
        the new one; otherwise it just shows the note's body.
        """
        if self.autosave is None:
            self.editor.set_markdown(note.body)
            return
        self.autosave.load_note(note)

    def refresh_notes(self) -> None:
        """Repopulate the note list from the repository for the current search.

        With an empty search box, lists every note (most-recently-updated first);
        otherwise shows the full-text search matches. A no-op until a repository is
        bound — the M4 unlock flow calls :meth:`bind_autosave`, then ``app.main``
        calls this once to fill the list on launch.
        """
        if self.repository is None:
            return
        query = self.search_input.text().strip()
        notes = (
            self.repository.search_notes(query)
            if query
            else self.repository.list_notes()
        )
        self._populate_note_list(notes)

    def open_quick_switcher(self) -> None:
        """Open the Ctrl+P quick-switcher and load the chosen note into the editor.

        Builds a :class:`~ui.quick_switcher.QuickSwitcher` over the vault's notes;
        if the user picks one, it is loaded into the editor (the same seam a list
        click uses). A no-op until a repository is bound (no vault open yet).
        """
        dialog = self._make_quick_switcher()
        if dialog is None:
            return
        if (
            dialog.exec() == QDialog.DialogCode.Accepted
            and dialog.selected_note is not None
        ):
            self.load_note(dialog.selected_note)

    def _make_quick_switcher(self) -> QuickSwitcher | None:
        """Construct a quick-switcher over the current notes, or ``None`` if no
        repository is bound.

        Separated from :meth:`open_quick_switcher` so tests can drive the dialog
        directly without the modal event loop (mirroring how the unlock flow is
        tested via :meth:`ui.unlock_dialog.UnlockDialog.attempt`).
        """
        if self.repository is None:
            return None
        return QuickSwitcher(self.repository.list_notes(), parent=self)

    def _populate_note_list(self, notes: list[Note]) -> None:
        """Replace the list rows with ``notes`` (title, falling back to body)."""
        # Rebuild without firing currentItemChanged for the implicit selection
        # change: a note loads only on an explicit user click, not on every
        # keystroke that refilters the list.
        self.note_list.blockSignals(True)
        self.note_list.clear()
        for note in notes:
            label = note.title.strip() or derive_title(note.body)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, note)
            self.note_list.addItem(item)
        self.note_list.blockSignals(False)

    def _on_search_changed(self, _text: str) -> None:
        self.refresh_notes()

    def _on_note_selected(
        self,
        current: QListWidgetItem | None,
        previous: QListWidgetItem | None,
    ) -> None:
        if current is None:
            return
        note = current.data(Qt.ItemDataRole.UserRole)
        if note is not None:
            self.load_note(note)
