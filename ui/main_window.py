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
from PySide6.QtWidgets import (
    QListWidget,
    QMainWindow,
    QSplitter,
    QTreeWidget,
)

from core.autosave import DEFAULT_DEBOUNCE_SECONDS
from ui.autosave import AutoSaveController
from ui.editor import MarkdownEditor

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
    * :attr:`note_list` — the note list for the selected scope (middle).
    * :attr:`editor` — the Markdown editor pane (right): editable source beside a
      live-rendered preview (see :class:`ui.editor.MarkdownEditor`).

    :attr:`splitter` is the central :class:`QSplitter` holding them. :attr:`autosave`
    is the debounced auto-save controller once :meth:`bind_autosave` is called, and
    ``None`` until then.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(*DEFAULT_SIZE)

        # No repository until a vault is opened; auto-save is bound later.
        self.autosave: AutoSaveController | None = None

        self.notebook_tree = QTreeWidget()
        self.notebook_tree.setHeaderLabel("Notebooks")
        self.notebook_tree.setMinimumWidth(_SIDEBAR_MIN_WIDTH)

        self.note_list = QListWidget()
        self.note_list.setMinimumWidth(_NOTE_LIST_MIN_WIDTH)

        self.editor = MarkdownEditor()
        self.editor.setMinimumWidth(_EDITOR_MIN_WIDTH)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.notebook_tree)
        self.splitter.addWidget(self.note_list)
        self.splitter.addWidget(self.editor)

        # Keep all three panes visible: a drag can resize but not collapse one.
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setSizes(list(PANE_DEFAULT_SIZES))
        # Only the editor pane (index 2) grows when the window widens.
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setStretchFactor(2, 1)

        self.setCentralWidget(self.splitter)

        self.statusBar().showMessage("Ready")

    def bind_autosave(
        self,
        repository: Repository,
        *,
        debounce: float = DEFAULT_DEBOUNCE_SECONDS,
    ) -> AutoSaveController:
        """Attach debounced auto-save to the editor, backed by ``repository``.

        Constructs an :class:`~ui.autosave.AutoSaveController` over :attr:`editor`,
        stores it as :attr:`autosave`, and returns it. The M4 unlock flow calls this
        once it has a keyed :class:`~core.repository.Repository`; until then the
        editor edits text with nowhere to persist.
        """
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
