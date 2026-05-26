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

from dataclasses import replace
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.autosave import DEFAULT_DEBOUNCE_SECONDS
from core.notebooks import build_notebook_tree, would_create_cycle
from core.settings import DEFAULT_SETTINGS, save_settings
from core.text import count_words, derive_title
from core.theme import DEFAULT_THEME, load_stylesheet
from ui.autosave import AutoSaveController
from ui.editor import MarkdownEditor
from ui.import_wizard import ImportWizard
from ui.quick_switcher import QuickSwitcher
from ui.settings_dialog import SettingsDialog
from ui.tag_editor import TagEditorDialog

if TYPE_CHECKING:
    import os

    from core.repository import Note, Notebook, Repository, Tag
    from core.settings import Settings

WINDOW_TITLE = "my_notes"
DEFAULT_SIZE = (1000, 700)

# Label for the "no notebook / top level" option in the move pickers.
_ROOT_CHOICE = "(Root)"

# Custom item-data role + kind markers for the left "notebooks/tags" tree. A
# notebook row and a tag row both store an int id in UserRole, so the selection
# handler and the context menu read this role to tell them apart. The "Tags"
# grouping header is a third, non-selectable kind.
_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_KIND_NOTEBOOK = "notebook"
_KIND_TAG = "tag"
_KIND_TAGS_HEADER = "tags-header"

# Label for the "Tags" grouping header in the tree.
_TAGS_HEADER_LABEL = "Tags"

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

    * :attr:`notebook_tree` — the notebooks/tags tree (left): an "All Notes" root
      and the vault's notebooks nested by ``parent_id``, then a "Tags" section
      listing every tag. Selecting a notebook filters the note list to it;
      selecting a tag filters to notes carrying that tag (across all notebooks).
      The notebook and tag filters are mutually exclusive — selecting one clears
      the other, and "All Notes" clears both. A right-click menu on a notebook
      creates / renames / moves (re-parents) / deletes notebooks. Populated from
      the repository by :meth:`_populate_notebook_tree`. Right-clicking a note in
      :attr:`note_list` moves it to another notebook (:meth:`move_note`), assigns /
      removes its tags (:meth:`open_tag_editor`), or deletes it (:meth:`delete_note`,
      after a confirmation prompt).
    * :attr:`note_list` — the note list / search results (middle), sitting below
      :attr:`search_input` inside the composite :attr:`note_pane`.
    * :attr:`editor` — the Markdown editor pane (right): editable source beside a
      live-rendered preview (see :class:`ui.editor.MarkdownEditor`).

    :attr:`search_input` filters :attr:`note_list` live (full-text search via
    :meth:`core.repository.Repository.search_notes`); selecting a row loads that note
    into the editor.

    **Keyboard-first navigation** (M5) lets the app be driven without the mouse:

    * **Ctrl+P** — open the quick-switcher (:class:`ui.quick_switcher.QuickSwitcher`)
      to jump to any note by fuzzy title match.
    * **Ctrl+1 / Ctrl+2 / Ctrl+3** — move focus to the notebook tree / note list /
      editor source (:meth:`focus_notebook_tree` / :meth:`focus_note_list` /
      :meth:`focus_editor`).
    * **Ctrl+F** — focus the search box and select its text (:meth:`focus_search`).

    :attr:`splitter` is the central :class:`QSplitter` holding the three
    (logical) panes. :attr:`autosave` is the debounced auto-save controller and
    :attr:`repository` the keyed data layer, both ``None`` until :meth:`bind_autosave`
    is called by the M4 unlock flow. :attr:`current_notebook_id` is the notebook the
    note list is filtered to (``None`` = "All Notes", no filter) and
    :attr:`current_tag_id` the tag it is filtered to (``None`` = no tag filter); the
    two are mutually exclusive. :attr:`word_count_label`
    is a status-bar widget showing the editor's live word count (M5). :meth:`apply_theme`
    styles the window via a Qt Style Sheet — the **View → Dark Theme** menu action
    (:attr:`dark_theme_action`) toggles between the dark theme and the native light
    look, with :attr:`current_theme` tracking the active one (M5).

    **Lock on minimise** (M5 Settings): when :attr:`settings`'
    ``lock_on_minimize`` is enabled, minimising the window emits
    :attr:`lock_on_minimize_requested` (``app`` flushes, locks the vault, and
    clears the session via :meth:`lock_session`) and restoring it emits
    :attr:`restore_requested` (``app`` re-prompts and rebinds). The window only
    detects the transitions and decides whether to signal — via the public
    :meth:`handle_window_state_change` seam, which :meth:`changeEvent` drives — so
    ``core``/``app`` own the actual lock/re-prompt and the behaviour is
    headless-testable.
    """

    #: Emitted when the window is minimised and lock-on-minimise should engage.
    lock_on_minimize_requested = Signal()
    #: Emitted when the window is restored after a lock-on-minimise.
    restore_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(*DEFAULT_SIZE)

        # No repository until a vault is opened; auto-save is bound later.
        self.autosave: AutoSaveController | None = None
        self.repository: Repository | None = None
        # The notebook the note list is filtered to; None = "All Notes".
        self.current_notebook_id: int | None = None
        # The tag the note list is filtered to; None = no tag filter. The tag
        # and notebook filters are mutually exclusive (one tree selection at a
        # time) — selecting one clears the other.
        self.current_tag_id: int | None = None
        # True while the session is locked because the window was minimised — so a
        # later restore re-prompts exactly that lock (and minimise fires only once).
        self._minimize_locked = False

        # Persisted settings. Until configure_settings() binds a real settings
        # location (the M4/M5 launch flow does), the window runs on defaults and
        # theme changes are NOT written to disk — so a bare MainWindow() in a
        # unit test never touches the real settings file.
        self.settings: Settings = DEFAULT_SETTINGS
        self.settings_path: str | os.PathLike[str] | None = None
        self._persist_settings = False

        self.notebook_tree = QTreeWidget()
        self.notebook_tree.setHeaderLabel("Notebooks")
        self.notebook_tree.setMinimumWidth(_SIDEBAR_MIN_WIDTH)
        self.notebook_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )

        # Middle pane: a search box above the note list, wrapped in a composite
        # widget so the splitter still holds three (logical) panes. Typing filters
        # the list (full-text search); selecting a row loads that note.
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search notes…")
        self.search_input.setClearButtonEnabled(True)

        self.note_list = QListWidget()
        self.note_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )

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
        # Right-click a note to move it to another notebook.
        self.note_list.customContextMenuRequested.connect(self._show_note_menu)

        # Selecting a notebook filters the note list to it; right-click manages
        # notebooks. Tree rebuilds block signals (see _populate_notebook_tree).
        self.notebook_tree.currentItemChanged.connect(self._on_notebook_selected)
        self.notebook_tree.customContextMenuRequested.connect(
            self._show_notebook_menu
        )

        # Ctrl+P opens the quick-switcher to jump to a note by fuzzy title match.
        self.quick_switch_shortcut = QShortcut(QKeySequence("Ctrl+P"), self)
        self.quick_switch_shortcut.activated.connect(self.open_quick_switcher)

        # Keyboard-first navigation: jump focus between the three panes and the
        # search box without the mouse. Each shortcut routes to a public seam
        # method (mirroring Ctrl+P) so the behaviour is headless-testable.
        self.focus_tree_shortcut = QShortcut(QKeySequence("Ctrl+1"), self)
        self.focus_tree_shortcut.activated.connect(self.focus_notebook_tree)
        self.focus_list_shortcut = QShortcut(QKeySequence("Ctrl+2"), self)
        self.focus_list_shortcut.activated.connect(self.focus_note_list)
        self.focus_editor_shortcut = QShortcut(QKeySequence("Ctrl+3"), self)
        self.focus_editor_shortcut.activated.connect(self.focus_editor)
        self.focus_search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.focus_search_shortcut.activated.connect(self.focus_search)

        # File menu: create a new note (Ctrl+N), import notes from a legacy
        # notes.db into the open vault, and open the Settings dialog.
        file_menu = self.menuBar().addMenu("&File")
        self.new_note_action = file_menu.addAction("&New Note")
        self.new_note_action.setShortcut(QKeySequence("Ctrl+N"))
        self.new_note_action.triggered.connect(self.new_note)
        file_menu.addSeparator()
        self.import_action = file_menu.addAction("Import legacy notes…")
        self.import_action.triggered.connect(self.open_import_wizard)
        file_menu.addSeparator()
        self.settings_action = file_menu.addAction("Settings…")
        self.settings_action.triggered.connect(self.open_settings)

        # View menu: a checkable toggle between the dark theme (QSS) and the
        # native light look. Connected to triggered (the user activating it), not
        # toggled, so apply_theme()'s setChecked() sync never re-enters here.
        view_menu = self.menuBar().addMenu("&View")
        self.dark_theme_action = view_menu.addAction("&Dark Theme")
        self.dark_theme_action.setCheckable(True)
        self.dark_theme_action.triggered.connect(self._on_toggle_dark_theme)

        # A live word count for the editor, pinned to the right of the status bar
        # (a permanent widget, so transient showMessage() text never overwrites
        # it). It updates on every edit and starts at the empty editor's count.
        self.word_count_label = QLabel()
        self.statusBar().addPermanentWidget(self.word_count_label)
        self.editor.source.textChanged.connect(self._update_word_count)
        self._update_word_count()

        self.statusBar().showMessage("Ready")

        # Style the window with the default theme so a freshly opened window is
        # themed (the user switches via the View menu). Persisting the choice
        # across launches is the M5 Settings capability; for now it resets to
        # the default each launch.
        self.apply_theme(DEFAULT_THEME)

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
        self._populate_notebook_tree()
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

    def _update_word_count(self) -> None:
        """Refresh the status-bar word count from the editor's current text.

        Connected to the editor source's ``textChanged`` signal, so the count
        tracks edits live. Counting is delegated to the Qt-free
        :func:`core.text.count_words`; the label reads ``"1 word"`` /
        ``"N words"`` with correct singular/plural.
        """
        count = count_words(self.editor.markdown())
        unit = "word" if count == 1 else "words"
        self.word_count_label.setText(f"{count} {unit}")

    def apply_theme(self, name: str) -> None:
        """Apply the theme ``name`` to the window (and its child dialogs).

        Sets the window's stylesheet from :func:`core.theme.load_stylesheet`,
        records it as :attr:`current_theme`, and keeps the View-menu "Dark Theme"
        action's checked state in sync. Child dialogs parented to the window —
        the quick-switcher and the import wizard — inherit the stylesheet. Raises
        :class:`ValueError` for an unknown theme name.
        """
        self.setStyleSheet(load_stylesheet(name))
        self.current_theme = name
        self.dark_theme_action.setChecked(name == "dark")

    def _on_toggle_dark_theme(self, checked: bool) -> None:
        """View-menu handler: switch to the dark theme, or back to light (native)."""
        name = "dark" if checked else "light"
        self.apply_theme(name)
        self._persist_theme(name)

    def _persist_theme(self, name: str) -> None:
        """Persist a theme chosen via the View menu, if persistence is enabled.

        A bare :class:`MainWindow` (e.g. in a unit test) has not been bound to a
        settings location via :meth:`configure_settings`, so a theme toggle is
        applied in-memory but never written to disk — keeping tests off the real
        settings file. The other settings fields are carried through unchanged.
        """
        if not self._persist_settings:
            return
        self.settings = replace(self.settings, theme=name)
        save_settings(self.settings, self.settings_path)

    # -- settings ------------------------------------------------------------

    def configure_settings(
        self,
        settings: Settings,
        *,
        settings_path: str | os.PathLike[str] | None = None,
    ) -> None:
        """Bind persisted settings to the window (called by ``app.main`` at launch).

        Records the current :attr:`settings` and where to persist changes
        (:attr:`settings_path`; ``None`` = the default location), enables
        persistence of subsequent theme changes, and applies the saved theme so a
        freshly launched window reflects the user's last choice.
        """
        self.settings = settings
        self.settings_path = settings_path
        self._persist_settings = True
        self.apply_theme(settings.theme)

    def open_settings(self) -> None:
        """Open the Settings dialog and apply the result to the live window.

        Runs the modal :class:`~ui.settings_dialog.SettingsDialog` (which persists
        on accept); if accepted, the chosen theme is applied immediately. The
        vault-location change takes effect on next launch (``app.main`` reads it).
        """
        dialog = self._make_settings_dialog()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_settings_result(dialog.settings)

    def _make_settings_dialog(self) -> SettingsDialog:
        """Construct a Settings dialog over the current settings + persistence path.

        Separated from :meth:`open_settings` so tests can drive the dialog directly
        without the modal event loop (mirroring :meth:`_make_quick_switcher` /
        :meth:`_make_import_wizard`).
        """
        return SettingsDialog(
            self.settings, settings_path=self.settings_path, parent=self
        )

    def _apply_settings_result(self, settings: Settings) -> None:
        """Adopt ``settings`` (already persisted by the dialog) and apply its theme."""
        self.settings = settings
        self.apply_theme(settings.theme)

    # -- session lock --------------------------------------------------------

    def flush_pending(self) -> None:
        """Persist any pending auto-save edit immediately.

        Called just before the vault auto-locks (while its connection is still
        open) so an in-flight edit is not lost when the key is wiped. A no-op when
        no auto-save is bound.
        """
        if self.autosave is not None:
            self.autosave.flush()

    def lock_session(self) -> None:
        """Clear all decrypted content and detach the data layer after an auto-lock.

        When the vault idle-locks, its key is wiped and its connection closed, so
        the repository is unusable and any note text still on screen is stale
        plaintext. This stops auto-save, drops the :attr:`repository` /
        :attr:`autosave` references, and clears the editor, note list, notebook
        tree, and search box so nothing decrypted lingers. The window stays open
        for the re-unlock prompt; ``app`` re-binds a fresh repository via
        :meth:`bind_autosave` once the user unlocks again.

        Safe to call after the vault has locked: auto-save was already flushed via
        :meth:`flush_pending` on ``about_to_lock``, so stopping it writes nothing
        to the closed connection.
        """
        if self.autosave is not None:
            self.autosave.stop()
        self.autosave = None
        self.repository = None
        self.current_notebook_id = None
        self.current_tag_id = None

        # Clear the panes with signals blocked so emptying them doesn't fire the
        # selection handlers (now no-ops without a repository, but kept tidy).
        self.note_list.blockSignals(True)
        self.note_list.clear()
        self.note_list.blockSignals(False)

        self.notebook_tree.blockSignals(True)
        self.notebook_tree.clear()
        self.notebook_tree.blockSignals(False)

        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)

        self.editor.set_markdown("")
        self.statusBar().showMessage("Vault locked")

    def changeEvent(self, event: QEvent) -> None:
        """Lock on minimise / re-prompt on restore when lock-on-minimise is enabled.

        Defers the decision to the next event-loop turn so a modal re-prompt that a
        restore may trigger never runs re-entrantly inside this window-state-change
        event. The decision itself lives in :meth:`handle_window_state_change` (the
        public seam tests drive directly, without real window events).
        """
        if event.type() == QEvent.Type.WindowStateChange:
            QTimer.singleShot(
                0, lambda: self.handle_window_state_change(minimized=self.isMinimized())
            )
        super().changeEvent(event)

    def handle_window_state_change(self, *, minimized: bool) -> None:
        """React to a minimise/restore transition; emit the lock/restore signal.

        On minimising with an active (unlocked) session and ``lock_on_minimize``
        enabled, mark the session minimise-locked and emit
        :attr:`lock_on_minimize_requested`. On restoring from such a lock, clear the
        flag and emit :attr:`restore_requested`. The flag pairs the two so minimise
        fires once and restore only re-prompts a minimise-initiated lock (an
        idle-lock, or no lock at all, restores silently).
        """
        if minimized:
            if (
                not self._minimize_locked
                and self.settings.lock_on_minimize
                and self.repository is not None
            ):
                self._minimize_locked = True
                self.lock_on_minimize_requested.emit()
        elif self._minimize_locked:
            self._minimize_locked = False
            self.restore_requested.emit()

    # -- keyboard-first navigation -------------------------------------------

    def focus_notebook_tree(self) -> None:
        """Move keyboard focus to the notebooks tree (Ctrl+1)."""
        self.notebook_tree.setFocus()

    def focus_note_list(self) -> None:
        """Move keyboard focus to the note list (Ctrl+2)."""
        self.note_list.setFocus()

    def focus_editor(self) -> None:
        """Move keyboard focus to the editor's editable source pane (Ctrl+3)."""
        self.editor.source.setFocus()

    def focus_search(self) -> None:
        """Move keyboard focus to the search box and select its text (Ctrl+F).

        Selecting the existing query lets the user immediately type a new search
        (the next keystroke replaces it) without first clearing the field.
        """
        self.search_input.setFocus()
        self.search_input.selectAll()

    def refresh_notes(self) -> None:
        """Repopulate the note list from the repository for the current view.

        With an empty search box, lists the notes for the current view: the
        selected tag (:attr:`current_tag_id`, across all notebooks), else the
        selected notebook (:attr:`current_notebook_id`, or every note when it is
        ``None`` = "All Notes"), most-recently-updated first. A non-empty search
        box shows the full-text matches across **all** notes — search is global,
        not scoped to the selected notebook or tag. A no-op until a repository is
        bound — the M4 unlock flow calls :meth:`bind_autosave`, then ``app.main``
        calls this once to fill the list on launch.
        """
        if self.repository is None:
            return
        query = self.search_input.text().strip()
        if query:
            notes = self.repository.search_notes(query)
        elif self.current_tag_id is not None:
            notes = self.repository.list_notes(tag_id=self.current_tag_id)
        elif self.current_notebook_id is None:
            notes = self.repository.list_notes()
        else:
            notes = self.repository.list_notes(notebook_id=self.current_notebook_id)
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

    # -- legacy import -------------------------------------------------------

    def open_import_wizard(self) -> None:
        """Open the legacy-``notes.db`` import wizard and refresh on success.

        Runs the modal :class:`~ui.import_wizard.ImportWizard`; if the user
        completes an import (the wizard's :attr:`result` is set), the notebook
        tree and note list are repopulated so the imported notebooks and notes
        appear immediately. A no-op until a repository is bound (no vault open).
        """
        wizard = self._make_import_wizard()
        if wizard is None:
            return
        if (
            wizard.exec() == QDialog.DialogCode.Accepted
            and wizard.result is not None
        ):
            self._populate_notebook_tree()
            self.refresh_notes()

    def _make_import_wizard(self) -> ImportWizard | None:
        """Construct an import wizard over the vault, or ``None`` if no repository.

        Separated from :meth:`open_import_wizard` so tests can drive the wizard
        directly without the modal event loop (mirroring :meth:`_make_quick_switcher`).
        """
        if self.repository is None:
            return None
        return ImportWizard(self.repository, parent=self)

    # -- notebook tree -------------------------------------------------------

    def _populate_notebook_tree(self) -> None:
        """Rebuild the notebooks/tags tree from the repository.

        Shows an "All Notes" root (selecting it clears all filters) above the
        vault's notebooks nested by ``parent_id`` (via
        :func:`core.notebooks.build_notebook_tree`), then — when the vault has any
        tags — a non-selectable "Tags" header with one row per tag (from
        :meth:`core.repository.Repository.list_tags`). Each row carries its id in
        ``UserRole`` (``None`` for "All Notes") and a kind marker in
        :data:`_KIND_ROLE` so selection and the context menu can tell a notebook
        row from a tag row. A no-op until a repository is bound.

        Signals are blocked during the rebuild so reselecting an item does not
        spuriously refresh the note list. The previously-active filter is
        re-selected: the tag if one is active and still exists, otherwise the
        notebook, otherwise "All Notes" (resetting the stale filter to ``None``).
        """
        if self.repository is None:
            return

        tree = self.notebook_tree
        notebook_items: dict[int | None, QTreeWidgetItem] = {}
        tag_items: dict[int, QTreeWidgetItem] = {}

        tree.blockSignals(True)
        tree.clear()

        all_item = QTreeWidgetItem(["All Notes"])
        all_item.setData(0, Qt.ItemDataRole.UserRole, None)
        all_item.setData(0, _KIND_ROLE, _KIND_NOTEBOOK)
        tree.addTopLevelItem(all_item)
        notebook_items[None] = all_item

        def make_item(node) -> QTreeWidgetItem:
            item = QTreeWidgetItem([node.notebook.name])
            item.setData(0, Qt.ItemDataRole.UserRole, node.notebook.id)
            item.setData(0, _KIND_ROLE, _KIND_NOTEBOOK)
            notebook_items[node.notebook.id] = item
            for child in node.children:
                item.addChild(make_item(child))
            return item

        for node in build_notebook_tree(self.repository.list_notebooks()):
            tree.addTopLevelItem(make_item(node))

        # A "Tags" section below the notebooks — only when tags exist, so a fresh
        # vault is not cluttered with an empty header. The header groups the tag
        # rows but is not itself a filter (made non-selectable).
        tags = self.repository.list_tags()
        if tags:
            header = QTreeWidgetItem([_TAGS_HEADER_LABEL])
            header.setData(0, _KIND_ROLE, _KIND_TAGS_HEADER)
            header.setFlags(header.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            tree.addTopLevelItem(header)
            for tag in tags:
                tag_item = QTreeWidgetItem([tag.name])
                tag_item.setData(0, Qt.ItemDataRole.UserRole, tag.id)
                tag_item.setData(0, _KIND_ROLE, _KIND_TAG)
                header.addChild(tag_item)
                tag_items[tag.id] = tag_item

        tree.expandAll()

        # Restore the active filter's selection. Prefer the tag (if it survives);
        # otherwise the notebook, falling back to All Notes for a stale filter.
        if self.current_tag_id is not None and self.current_tag_id in tag_items:
            tree.setCurrentItem(tag_items[self.current_tag_id])
        else:
            self.current_tag_id = None
            if self.current_notebook_id not in notebook_items:
                self.current_notebook_id = None
            tree.setCurrentItem(notebook_items[self.current_notebook_id])

        tree.blockSignals(False)

    def select_notebook(self, notebook_id: int | None) -> None:
        """Filter the note list to ``notebook_id`` (``None`` = all notebooks).

        Sets :attr:`current_notebook_id`, clears any tag filter (the two are
        mutually exclusive), and refreshes the note list. This is the seam the
        tree's selection signal drives and that tests call directly.
        """
        self.current_notebook_id = notebook_id
        self.current_tag_id = None
        self.refresh_notes()

    def select_tag(self, tag_id: int) -> None:
        """Filter the note list to notes carrying ``tag_id`` (across all notebooks).

        Sets :attr:`current_tag_id` and clears any notebook filter (the two are
        mutually exclusive — a tag view spans every notebook), then refreshes the
        note list. The seam the tree's tag rows drive and that tests call directly;
        a no-op against the list until a repository is bound.
        """
        self.current_tag_id = tag_id
        self.current_notebook_id = None
        self.refresh_notes()

    def add_notebook(
        self, name: str, *, parent_id: int | None = None
    ) -> Notebook | None:
        """Create a notebook (optionally nested under ``parent_id``) and refresh.

        Returns the created :class:`~core.repository.Notebook`, or ``None`` if no
        repository is bound. The tree is repopulated so the new notebook appears.
        Driven by the right-click "New notebook" / "New sub-notebook" actions and
        callable directly in tests.
        """
        if self.repository is None:
            return None
        notebook = self.repository.create_notebook(name, parent_id=parent_id)
        self._populate_notebook_tree()
        return notebook

    def rename_notebook(self, notebook_id: int, new_name: str) -> Notebook | None:
        """Rename a notebook and refresh the tree; ``None`` if no repository."""
        if self.repository is None:
            return None
        notebook = self.repository.update_notebook(notebook_id, name=new_name)
        self._populate_notebook_tree()
        return notebook

    def remove_notebook(self, notebook_id: int) -> bool:
        """Delete a notebook and refresh; return ``True`` if a row was removed.

        Descendant notebooks cascade away and their notes orphan to the root (the
        repository's FK behaviour). If the deleted notebook was the active filter,
        :meth:`_populate_notebook_tree` resets the selection to "All Notes"; the
        note list is then refreshed to reflect the change.
        """
        if self.repository is None:
            return False
        deleted = self.repository.delete_notebook(notebook_id)
        self._populate_notebook_tree()
        self.refresh_notes()
        return deleted

    def move_notebook(
        self, notebook_id: int, new_parent_id: int | None
    ) -> Notebook | None:
        """Re-parent a notebook under ``new_parent_id`` (``None`` = top level).

        Refuses a move that would create a cycle — a notebook under itself or one
        of its own descendants — returning ``None`` and leaving the repository
        unchanged (the data layer has no such guard, so this is the safety net,
        via :func:`core.notebooks.would_create_cycle`). Otherwise re-parents the
        notebook, repopulates the tree so it re-nests, and returns it. ``None``
        when no repository is bound. Driven by the right-click "Move to…" action
        and callable directly in tests.
        """
        if self.repository is None:
            return None
        if would_create_cycle(
            self.repository.list_notebooks(), notebook_id, new_parent_id
        ):
            return None
        notebook = self.repository.update_notebook(
            notebook_id, parent_id=new_parent_id
        )
        self._populate_notebook_tree()
        return notebook

    def rename_tag(self, tag_id: int, new_name: str) -> Tag | None:
        """Rename a tag across the whole vault and refresh the tree + note list.

        The new name takes effect on every note carrying the tag (the repository
        renames it in place). Returns the updated :class:`~core.repository.Tag`, or
        ``None`` (a no-op) when no repository is bound or when ``new_name`` already
        belongs to a *different* tag — tag names are unique, so renaming onto an
        existing name is refused here rather than surfacing the database's
        ``IntegrityError`` (merging two tags is out of scope). Renaming a tag to its
        own current name is allowed (a harmless no-op rename). After a rename the
        tree is repopulated so the row's label updates, and the note list refreshed
        so a tag-filtered view reflects the new name. Driven by the right-click
        "Rename…" action on a tag row and callable directly in tests.
        """
        if self.repository is None:
            return None
        existing = self.repository.get_tag_by_name(new_name)
        if existing is not None and existing.id != tag_id:
            return None
        tag = self.repository.rename_tag(tag_id, new_name)
        self._populate_notebook_tree()
        self.refresh_notes()
        return tag

    def _on_notebook_selected(
        self,
        current: QTreeWidgetItem | None,
        previous: QTreeWidgetItem | None,
    ) -> None:
        if current is None:
            return
        kind = current.data(0, _KIND_ROLE)
        if kind == _KIND_TAG:
            self.select_tag(current.data(0, Qt.ItemDataRole.UserRole))
        elif kind == _KIND_NOTEBOOK:
            self.select_notebook(current.data(0, Qt.ItemDataRole.UserRole))
        # The "Tags" header is non-selectable, so it never reaches here.

    def _show_notebook_menu(self, pos: QPoint) -> None:
        """Right-click menu on the tree, dispatched by the row kind.

        A notebook row (or empty space) gets the notebook menu: create / rename /
        move / delete. A tag row gets the tag menu (rename it across the vault — see
        :meth:`_show_tag_menu`). The non-selectable "Tags" grouping header has no
        menu.
        """
        if self.repository is None:
            return
        item = self.notebook_tree.itemAt(pos)
        kind = item.data(0, _KIND_ROLE) if item is not None else _KIND_NOTEBOOK
        if kind == _KIND_TAGS_HEADER:
            return
        if kind == _KIND_TAG:
            self._show_tag_menu(item.data(0, Qt.ItemDataRole.UserRole), pos)
            return

        notebook_id = (
            item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        )

        menu = QMenu(self.notebook_tree)
        menu.addAction("New notebook…", lambda *_: self._prompt_new_notebook())
        if notebook_id is not None:  # a real notebook, not the "All Notes" row
            menu.addAction(
                "New sub-notebook…",
                lambda *_: self._prompt_new_notebook(parent_id=notebook_id),
            )
            menu.addAction("Rename…", lambda *_: self._prompt_rename_notebook(notebook_id))
            menu.addAction("Move to…", lambda *_: self._prompt_move_notebook(notebook_id))
            menu.addAction("Delete", lambda *_: self._prompt_delete_notebook(notebook_id))
        menu.exec(self.notebook_tree.viewport().mapToGlobal(pos))

    def _show_tag_menu(self, tag_id: int, pos: QPoint) -> None:
        """Right-click menu on a tag row: rename the tag across the vault.

        Tag *assignment* stays per-note (the note-list "Tags…" editor); this menu
        manages the tag itself, vault-wide. Routes to :meth:`_prompt_rename_tag`.
        """
        menu = QMenu(self.notebook_tree)
        menu.addAction("Rename…", lambda *_: self._prompt_rename_tag(tag_id))
        menu.exec(self.notebook_tree.viewport().mapToGlobal(pos))

    def _prompt_new_notebook(self, *, parent_id: int | None = None) -> None:
        name, ok = QInputDialog.getText(self, "New notebook", "Notebook name:")
        if ok and name.strip():
            self.add_notebook(name.strip(), parent_id=parent_id)

    def _prompt_rename_notebook(self, notebook_id: int) -> None:
        if self.repository is None:
            return
        current = self.repository.get_notebook(notebook_id)
        if current is None:
            return
        name, ok = QInputDialog.getText(
            self, "Rename notebook", "New name:", text=current.name
        )
        if ok and name.strip():
            self.rename_notebook(notebook_id, name.strip())

    def _prompt_move_notebook(self, notebook_id: int) -> None:
        """Ask for a new parent and re-parent the notebook via :meth:`move_notebook`.

        Offers the root plus every notebook that isn't this one or one of its
        descendants (those would cycle, per
        :func:`core.notebooks.would_create_cycle`), so the picker only ever lists
        legal targets.
        """
        if self.repository is None:
            return
        notebooks = self.repository.list_notebooks()
        targets: list[tuple[str, int | None]] = [(_ROOT_CHOICE, None)]
        targets.extend(
            (nb.name, nb.id)
            for nb in notebooks
            if nb.id != notebook_id
            and not would_create_cycle(notebooks, notebook_id, nb.id)
        )
        labels = [label for label, _ in targets]
        choice, ok = QInputDialog.getItem(
            self, "Move notebook", "Move under:", labels, 0, False
        )
        if ok and choice:
            for label, target_id in targets:
                if label == choice:
                    self.move_notebook(notebook_id, target_id)
                    break

    def _prompt_delete_notebook(self, notebook_id: int) -> None:
        if self.repository is None:
            return
        notebook = self.repository.get_notebook(notebook_id)
        if notebook is None:
            return
        reply = QMessageBox.question(
            self,
            "Delete notebook",
            f"Delete “{notebook.name}” and its sub-notebooks?\n"
            "Notes inside are moved to the root, not deleted.",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.remove_notebook(notebook_id)

    def _prompt_rename_tag(self, tag_id: int) -> None:
        """Ask for a new name and rename the tag via :meth:`rename_tag`.

        Seeds the input with the tag's current name. A blank entry, or a name
        already used by another tag, is ignored (the rename no-ops). Mirrors
        :meth:`_prompt_rename_notebook`.
        """
        if self.repository is None:
            return
        current = self.repository.get_tag(tag_id)
        if current is None:
            return
        name, ok = QInputDialog.getText(
            self, "Rename tag", "New name:", text=current.name
        )
        if ok and name.strip():
            self.rename_tag(tag_id, name.strip())

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

    def new_note(self) -> Note | None:
        """Create a new, empty note in the current view and open it for editing.

        Creates the note in the notebook the list is currently filtered to
        (:attr:`current_notebook_id`; the root when "All Notes" or a tag is
        selected), clears any active search and tag filter so the new (empty,
        untagged) note is visible, refreshes the list, then selects the note —
        loading it into the editor via the usual selection seam — and moves focus
        to the editable source so the user can start typing immediately. Debounced
        auto-save persists the edits (there is no Save button). Returns the created
        note, or ``None`` if no repository is bound (no vault open yet).

        Driven by the File-menu "New Note" action / Ctrl+N, and callable directly
        in tests.
        """
        if self.repository is None:
            return None
        note = self.repository.create_note(notebook_id=self.current_notebook_id)
        # A new note has no title/body/tags, so it would not match an active
        # search query or tag filter; clear the search (without re-triggering it)
        # and, if a tag filter is active, reset the view to All Notes so the note
        # shows in the list. A notebook filter is fine — the note was created
        # into that notebook.
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        if self.current_tag_id is not None:
            self._select_all_notes()
        self.refresh_notes()
        self._select_note(note.id)
        self.focus_editor()
        return note

    def _select_all_notes(self) -> None:
        """Reset the view to "All Notes", clearing the notebook and tag filters.

        Clears both filters and highlights the "All Notes" tree row (with signals
        blocked — the caller refreshes the note list). Used when an action must
        show a note the active tag filter would otherwise hide (e.g. a new,
        untagged note created from :meth:`new_note`).
        """
        self.current_notebook_id = None
        self.current_tag_id = None
        tree = self.notebook_tree
        for i in range(tree.topLevelItemCount()):
            item = tree.topLevelItem(i)
            if (
                item.data(0, _KIND_ROLE) == _KIND_NOTEBOOK
                and item.data(0, Qt.ItemDataRole.UserRole) is None
            ):
                tree.blockSignals(True)
                tree.setCurrentItem(item)
                tree.blockSignals(False)
                return

    def _select_note(self, note_id: int) -> None:
        """Select the list row carrying ``note_id``, loading it into the editor.

        Setting the current row emits ``currentItemChanged`` → :meth:`load_note`,
        the same path an explicit user click uses. A no-op if no row matches.
        """
        for i in range(self.note_list.count()):
            note = self.note_list.item(i).data(Qt.ItemDataRole.UserRole)
            if note is not None and note.id == note_id:
                self.note_list.setCurrentRow(i)
                return

    def delete_note(self, note_id: int) -> bool:
        """Delete a note from the vault and refresh the list; ``True`` if removed.

        If the deleted note is the one currently open in the editor (bound to
        auto-save), it is first detached from auto-save and the editor cleared —
        otherwise a later flush would try to ``update_note`` a now-deleted row
        and raise :class:`~core.repository.NotFoundError`, and stale plaintext
        would linger on screen. Deleting any other note leaves the editor
        untouched. Returns ``False`` when no repository is bound (no vault open).
        Driven by the note-list right-click "Delete" action (which confirms first
        via :meth:`_prompt_delete_note`) and callable directly in tests.
        """
        if self.repository is None:
            return False
        editing_deleted = (
            self.autosave is not None and self.autosave.saver.note_id == note_id
        )
        deleted = self.repository.delete_note(note_id)
        if deleted and editing_deleted:
            # Detach the deleted note before clearing the editor: set_markdown("")
            # emits textChanged, but with no note bound the saver ignores the
            # edit, so nothing flushes to the deleted row.
            self.autosave.saver.load(None)
            self.editor.set_markdown("")
        self.refresh_notes()
        return deleted

    def move_note(self, note_id: int, notebook_id: int | None) -> Note | None:
        """Move a note into ``notebook_id`` (``None`` = the root) and refresh.

        Updates the note's notebook via the repository, then repopulates the note
        list so the current view reflects the move (a note moved out of the
        selected notebook drops from the list; one moved into it appears).
        Returns the updated note, or ``None`` if no repository is bound. Driven by
        the note-list right-click "Move to notebook…" action and callable directly
        in tests.
        """
        if self.repository is None:
            return None
        note = self.repository.update_note(note_id, notebook_id=notebook_id)
        self.refresh_notes()
        return note

    def _show_note_menu(self, pos: QPoint) -> None:
        """Right-click menu on the note list: move, edit tags, or delete the note."""
        if self.repository is None:
            return
        item = self.note_list.itemAt(pos)
        if item is None:
            return
        note = item.data(Qt.ItemDataRole.UserRole)
        if note is None:
            return
        menu = QMenu(self.note_list)
        menu.addAction(
            "Move to notebook…", lambda *_: self._prompt_move_note(note)
        )
        menu.addAction("Tags…", lambda *_: self.open_tag_editor(note))
        menu.addAction("Delete", lambda *_: self._prompt_delete_note(note))
        menu.exec(self.note_list.viewport().mapToGlobal(pos))

    def open_tag_editor(self, note: Note) -> None:
        """Open the tag editor for ``note`` to assign / remove its tags.

        Runs the modal :class:`~ui.tag_editor.TagEditorDialog`, which mutates the
        vault live (each add / remove commits immediately), so there is nothing to
        apply on close. After it closes, the tree is repopulated (a brand-new tag
        appears in the "Tags" section) and the note list refreshed (a tag-filtered
        view reflects the note's changed tags). A no-op until a repository is bound
        (no vault open). Driven by the note-list right-click "Tags…" action.
        """
        dialog = self._make_tag_editor(note)
        if dialog is None:
            return
        dialog.exec()
        self._populate_notebook_tree()
        self.refresh_notes()

    def _make_tag_editor(self, note: Note) -> TagEditorDialog | None:
        """Construct a tag editor for ``note``, or ``None`` if no repository.

        Separated from :meth:`open_tag_editor` so tests can drive the dialog
        directly without the modal event loop (mirroring :meth:`_make_quick_switcher`
        / :meth:`_make_import_wizard`).
        """
        if self.repository is None:
            return None
        return TagEditorDialog(self.repository, note.id, parent=self)

    def _prompt_move_note(self, note: Note) -> None:
        """Ask for a target notebook and move the note via :meth:`move_note`.

        Offers the root plus every notebook (a note can live in any one of them,
        unlike a notebook re-parent there is no cycle to avoid).
        """
        if self.repository is None:
            return
        targets: list[tuple[str, int | None]] = [(_ROOT_CHOICE, None)]
        targets.extend((nb.name, nb.id) for nb in self.repository.list_notebooks())
        labels = [label for label, _ in targets]
        choice, ok = QInputDialog.getItem(
            self, "Move note", "Move to notebook:", labels, 0, False
        )
        if ok and choice:
            for label, target_id in targets:
                if label == choice:
                    self.move_note(note.id, target_id)
                    break

    def _prompt_delete_note(self, note: Note) -> None:
        """Confirm, then delete ``note`` via :meth:`delete_note`.

        Shows a Yes/No confirmation before removing the note — deletion is
        irreversible (there is no trash). Driven by the note-list right-click
        "Delete" action.
        """
        if self.repository is None:
            return
        label = note.title.strip() or derive_title(note.body)
        reply = QMessageBox.question(
            self,
            "Delete note",
            f"Delete “{label}”?\nThis cannot be undone.",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.delete_note(note.id)
