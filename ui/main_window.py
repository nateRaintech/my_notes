"""The application's main window.

The app shell: a dock-based layout — notebooks/tags tree, note list, and editor
— built on Qt's native docking system. Every panel is a QDockWidget: movable,
re-dockable at any edge, tabbable, floatable as a standalone window, and
hideable. The entire layout (dock positions, sizes, floating state) is saved and
restored across launches via QMainWindow.saveState() / restoreState().

This module builds the *shell* — the named, typed panels those capabilities
populate. Data binding stays out until a vault is opened: the editor edits text
with nowhere to persist until :meth:`MainWindow.bind_autosave` is called with a
keyed repository (the M4 unlock flow does this), at which point :meth:`load_note`
opens a note for editing and debounced auto-save persists it.

Per CLAUDE.md's strict layering, the UI layer may import Qt freely; ``core/``
must never import this module.
"""

from __future__ import annotations

import base64
from dataclasses import replace
from typing import TYPE_CHECKING

from PySide6.QtCore import QByteArray, QEvent, QPoint, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QDockWidget,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QTextEdit,
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
from ui.ai_chat import AiChatPanel
from ui.ai_worker import AiWorker
from ui.api_key_dialog import APIKeyDialog
from ui.import_wizard import ImportWizard
from ui.quick_switcher import QuickSwitcher
from ui.settings_dialog import SettingsDialog
from ui.tabbed_editor import TabbedEditor
from ui.tag_editor import TagEditorDialog

if TYPE_CHECKING:
    import os

    from core.repository import Note, Notebook, Repository
    from core.settings import Settings

WINDOW_TITLE = "my_notes"
DEFAULT_SIZE = (1000, 700)

# Label for the "no notebook / top level" option in the move pickers.
_ROOT_CHOICE = "(Root)"

# Custom item-data role + kind markers for the left "notebooks/tags" tree.
_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_KIND_NOTEBOOK = "notebook"
_KIND_TAG = "tag"
_KIND_TAGS_HEADER = "tags-header"

# Label for the "Tags" grouping header in the tree.
_TAGS_HEADER_LABEL = "Tags"

# Minimum width for the notebooks tree dock.
_SIDEBAR_MIN_WIDTH = 140
# Minimum width for the note list dock.
_NOTE_LIST_MIN_WIDTH = 180
# Minimum width for the editor source (central widget).
_EDITOR_MIN_WIDTH = 240


def _title_for_note(note) -> str:
    """The list/tab display title for ``note``: its title, else derived from body."""
    return note.title.strip() or derive_title(note.body)


class MainWindow(QMainWindow):
    """Top-level application window: a dock-based 3-panel shell.

    The editor source (:attr:`editor.source`) is the central widget. Three
    :class:`QDockWidget`s surround it:

    * ``dock_notebooks`` — the notebooks/tags tree (left edge by default).
    * ``dock_notelist`` — search box + note list (left edge, below/beside notebooks).
    * ``dock_preview`` — the Markdown preview (right edge by default).

    All docks are movable, re-dockable, tabbable, floatable, and closable; the
    layout is saved on close and restored on launch via
    :meth:`QMainWindow.saveState` / :meth:`QMainWindow.restoreState`.

    Key public attributes (for callers and tests):
    * :attr:`notebook_tree` — the notebooks/tags QTreeWidget.
    * :attr:`note_list` — the note list QListWidget.
    * :attr:`search_input` — the search QLineEdit.
    * :attr:`editor` — the :class:`~ui.editor.MarkdownEditor` (owns source + preview).
    * :attr:`editor.source` — the editable QPlainTextEdit (central widget).
    * :attr:`editor.preview` — the rendered QTextEdit (inside dock_preview).
    * :attr:`dock_notebooks`, :attr:`dock_notelist`, :attr:`dock_preview` — the docks.

    **View menu shortcuts:**
    * ``Ctrl+Shift+1`` — toggle Notebooks dock.
    * ``Ctrl+Shift+2`` — toggle Note list dock.
    * ``Ctrl+Shift+3`` — *dropped* (editor source is the central widget, always
      present; no dock to toggle). A comment below marks the gap.
    * ``Ctrl+Shift+4`` — toggle Preview dock.
    * ``Ctrl+Shift+F`` — Focus mode (hides all docks, leaving only the editor).

    **Focus mode** snapshots the current dock layout via saveState(), hides all
    docks, and restores the snapshot via restoreState() when toggled off.

    **Persistence** (after :meth:`configure_settings` binds a settings file):
    :meth:`closeEvent` writes ``QMainWindow.saveState()`` and
    ``saveGeometry()`` as base64 strings to settings. :meth:`configure_settings`
    restores them via ``restoreState()`` / ``restoreGeometry()``.
    """

    #: Emitted when the window is minimised and lock-on-minimise should engage.
    lock_on_minimize_requested = Signal()
    #: Emitted when the window is restored after a lock-on-minimise.
    restore_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(WINDOW_TITLE)
        self.resize(*DEFAULT_SIZE)

        # No repository until a vault is opened; tabs are bound later.
        self.repository: Repository | None = None
        self.current_notebook_id: int | None = None
        self.current_tag_id: int | None = None
        self._minimize_locked = False
        # Strong references to in-flight AI (worker, thread) pairs. A worker moved
        # to a QThread has no parent (you can't parent across threads), so without
        # holding it here it is garbage-collected the instant the launching method
        # returns — before `thread.started -> worker.run` ever fires (issue #85).
        # Pairs are discarded on thread.finished (see _make_ai_worker).
        self._ai_jobs: set = set()

        # Persisted settings. Until configure_settings() binds a real settings
        # location the window runs on defaults and nothing is written to disk.
        self.settings: Settings = DEFAULT_SETTINGS
        self.settings_path: str | os.PathLike[str] | None = None
        self._persist_settings = False

        # Focus-mode state: snapshot of QMainWindow state taken before hiding docks.
        self._focus_mode = False
        self._pre_focus_state: QByteArray | None = None

        # --- Build widgets ---------------------------------------------------

        self.notebook_tree = QTreeWidget()
        self.notebook_tree.setHeaderLabel("Notebooks")
        self.notebook_tree.setMinimumWidth(_SIDEBAR_MIN_WIDTH)
        self.notebook_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search notes…")
        self.search_input.setClearButtonEnabled(True)

        self.note_list = QListWidget()
        self.note_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        # Note list pane: search box above the list.
        _notelist_container = QWidget()
        _notelist_container.setMinimumWidth(_NOTE_LIST_MIN_WIDTH)
        _notelist_layout = QVBoxLayout(_notelist_container)
        _notelist_layout.setContentsMargins(0, 0, 0, 0)
        _notelist_layout.setSpacing(0)
        _notelist_layout.addWidget(self.search_input)
        _notelist_layout.addWidget(self.note_list)

        # --- Central widget: tabbed editor -----------------------------------
        # One tab per open note; the repository is wired in later by
        # bind_autosave, so until then no tabs can open (empty placeholder shows).
        self.tabbed_editor = TabbedEditor()
        self.tabbed_editor.setMinimumWidth(_EDITOR_MIN_WIDTH)
        self.setCentralWidget(self.tabbed_editor)

        # Shared Markdown preview (in a dock); always renders the active tab.
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumWidth(_EDITOR_MIN_WIDTH)

        # --- Dock widgets ----------------------------------------------------

        self.setDockNestingEnabled(True)

        _dock_features = (
            QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )

        # Notebooks dock (left).
        self.dock_notebooks = QDockWidget("Notebooks", self)
        self.dock_notebooks.setObjectName("dock_notebooks")
        self.dock_notebooks.setFeatures(_dock_features)
        self.dock_notebooks.setWidget(self.notebook_tree)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_notebooks)

        # Note list dock (left, split below/beside notebooks).
        self.dock_notelist = QDockWidget("Note list", self)
        self.dock_notelist.setObjectName("dock_notelist")
        self.dock_notelist.setFeatures(_dock_features)
        self.dock_notelist.setWidget(_notelist_container)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_notelist)

        # Preview dock (right).
        self.dock_preview = QDockWidget("Preview", self)
        self.dock_preview.setObjectName("dock_preview")
        self.dock_preview.setFeatures(_dock_features)
        self.dock_preview.setWidget(self.preview)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_preview)

        # AI Chat dock (right, hidden by default).
        self.ai_chat_panel = AiChatPanel()
        self.dock_ai_chat = QDockWidget("AI Chat", self)
        self.dock_ai_chat.setObjectName("dock_ai_chat")
        self.dock_ai_chat.setFeatures(_dock_features)
        self.dock_ai_chat.setWidget(self.ai_chat_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_ai_chat)
        self.dock_ai_chat.hide()

        # --- Signals ---------------------------------------------------------

        self.search_input.textChanged.connect(self._on_search_changed)
        self.note_list.currentItemChanged.connect(self._on_note_selected)
        # currentItemChanged doesn't fire when the already-selected row is clicked,
        # so clicking a note whose tab was closed (Ctrl+W) wouldn't reopen it.
        # itemClicked fires on every click and open() is focus-or-create, so this
        # reopens a closed tab without duplicating one on ordinary navigation.
        self.note_list.itemClicked.connect(self._on_note_clicked)
        self.note_list.customContextMenuRequested.connect(self._show_note_menu)

        self.notebook_tree.currentItemChanged.connect(self._on_notebook_selected)
        self.notebook_tree.customContextMenuRequested.connect(self._show_notebook_menu)

        # --- Keyboard shortcuts ----------------------------------------------

        self.quick_switch_shortcut = QShortcut(QKeySequence("Ctrl+P"), self)
        self.quick_switch_shortcut.activated.connect(self.open_quick_switcher)

        self.focus_tree_shortcut = QShortcut(QKeySequence("Ctrl+1"), self)
        self.focus_tree_shortcut.activated.connect(self.focus_notebook_tree)
        self.focus_list_shortcut = QShortcut(QKeySequence("Ctrl+2"), self)
        self.focus_list_shortcut.activated.connect(self.focus_note_list)
        self.focus_editor_shortcut = QShortcut(QKeySequence("Ctrl+3"), self)
        self.focus_editor_shortcut.activated.connect(self.focus_editor)
        self.focus_search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.focus_search_shortcut.activated.connect(self.focus_search)

        self.close_tab_shortcut = QShortcut(QKeySequence("Ctrl+W"), self)
        self.close_tab_shortcut.activated.connect(
            lambda: self.tabbed_editor.close_tab(self.tabbed_editor.active_tab)
        )

        # --- Menus -----------------------------------------------------------

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

        view_menu = self.menuBar().addMenu("&View")
        self.dark_theme_action = view_menu.addAction("&Dark Theme")
        self.dark_theme_action.setCheckable(True)
        self.dark_theme_action.triggered.connect(self._on_toggle_dark_theme)

        # Panel visibility via dock toggleViewAction() (Qt provides these for free:
        # checkable show/hide actions that stay in sync with the dock's visibility).
        view_menu.addSeparator()

        # Notebooks dock toggle — Ctrl+Shift+1.
        self.toggle_notebooks_action = self.dock_notebooks.toggleViewAction()
        self.toggle_notebooks_action.setShortcut(QKeySequence("Ctrl+Shift+1"))
        view_menu.addAction(self.toggle_notebooks_action)

        # Note list dock toggle — Ctrl+Shift+2.
        self.toggle_notelist_action = self.dock_notelist.toggleViewAction()
        self.toggle_notelist_action.setShortcut(QKeySequence("Ctrl+Shift+2"))
        view_menu.addAction(self.toggle_notelist_action)

        # Ctrl+Shift+3 is intentionally absent: the editor source is the central
        # widget (always present, not in a dock), so there is nothing to toggle.

        # Preview dock toggle — Ctrl+Shift+4.
        self.toggle_preview_action = self.dock_preview.toggleViewAction()
        self.toggle_preview_action.setShortcut(QKeySequence("Ctrl+Shift+4"))
        view_menu.addAction(self.toggle_preview_action)

        # AI Chat dock toggle — Ctrl+Shift+5.
        self.toggle_ai_chat_action = self.dock_ai_chat.toggleViewAction()
        self.toggle_ai_chat_action.setShortcut(QKeySequence("Ctrl+Shift+5"))
        view_menu.addAction(self.toggle_ai_chat_action)

        view_menu.addSeparator()
        self.focus_mode_action = view_menu.addAction("Focus mode")
        self.focus_mode_action.setCheckable(True)
        self.focus_mode_action.setShortcut(QKeySequence("Ctrl+Shift+F"))
        self.focus_mode_action.triggered.connect(
            lambda checked: self.set_focus_mode(checked)
        )

        # AI menu — items are disabled when the vault is locked (no repository).
        self._ai_menu = self.menuBar().addMenu("&AI")
        self.set_api_key_action = self._ai_menu.addAction("Set API key…")
        self.set_api_key_action.triggered.connect(self.open_api_key_dialog)
        self.test_connection_action = self._ai_menu.addAction("Test connection")
        self.test_connection_action.triggered.connect(self.open_test_connection)
        self.chat_action = self._ai_menu.addAction("Chat")
        self.chat_action.triggered.connect(self.open_ai_chat)
        self._ai_menu.addSeparator()
        self.analyze_text_action = self._ai_menu.addAction("Analyze text with AI")
        self.analyze_text_action.triggered.connect(self.analyze_selection)
        # Always enabled; analyze_selection guards on an open tab + a selection.
        self.analyze_text_action.setEnabled(True)
        self.analyze_note_action = self._ai_menu.addAction("Analyze note with AI")
        self.analyze_note_action.triggered.connect(self.analyze_note)

        # --- Status bar ------------------------------------------------------

        self.word_count_label = QLabel()
        self.statusBar().addPermanentWidget(self.word_count_label)
        self.tabbed_editor.active_tab_changed.connect(self._on_active_tab_changed)
        self.tabbed_editor.tab_text_changed.connect(self._on_active_text_changed)
        self._update_word_count()

        self.statusBar().showMessage("Ready")

        self.apply_theme(DEFAULT_THEME)

    # -------------------------------------------------------------------------
    # Active-tab compatibility shims + preview/word-count wiring
    # -------------------------------------------------------------------------

    @property
    def editor(self):
        """The active tab's editing surface, or ``None`` when no note is open.

        Compatibility shim: the app used to have a single ``editor``. Callers and
        tests that reach ``window.editor.source`` / ``.markdown()`` /
        ``.set_markdown()`` now get the active tab (which exposes the same seam).
        """
        return self.tabbed_editor.active_tab

    @property
    def autosave(self):
        """The active tab's auto-save controller, or ``None`` when no tab is open."""
        tab = self.tabbed_editor.active_tab
        return tab._controller if tab is not None else None

    def _on_active_tab_changed(self) -> None:
        self._render_preview()
        self._update_word_count()

    def _on_active_text_changed(self) -> None:
        self._render_preview()
        self._update_word_count()

    def _render_preview(self) -> None:
        """Render the active tab's Markdown into the shared preview (blank if none)."""
        self.preview.setMarkdown(self._active_markdown())

    def _active_markdown(self) -> str:
        tab = self.tabbed_editor.active_tab
        return tab.markdown() if tab is not None else ""

    # -------------------------------------------------------------------------
    # Focus mode
    # -------------------------------------------------------------------------

    def is_focus_mode(self) -> bool:
        """Return ``True`` if focus mode is active."""
        return self._focus_mode

    def set_focus_mode(self, on: bool) -> None:
        """Enable or disable focus mode.

        Enabling snapshots the current dock layout via ``saveState()``, then
        hides all three docks leaving only the editor source. Disabling restores
        the pre-focus snapshot via ``restoreState()``, so the docks return to
        exactly the arrangement the user had before focus mode was engaged.
        """
        if on == self._focus_mode:
            return
        self._focus_mode = on
        self.focus_mode_action.setChecked(on)
        if on:
            # Snapshot the dock layout before hiding.
            self._pre_focus_state = self.saveState()
            self.dock_notebooks.hide()
            self.dock_notelist.hide()
            self.dock_preview.hide()
        else:
            # Restore the pre-focus dock arrangement.
            if self._pre_focus_state is not None:
                self.restoreState(self._pre_focus_state)
                self._pre_focus_state = None

    # -------------------------------------------------------------------------
    # Persistence helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _encode_state(qba: QByteArray) -> str:
        """Encode a QByteArray as a base64 string for JSON storage."""
        return base64.b64encode(bytes(qba)).decode("ascii")

    @staticmethod
    def _decode_state(s: str) -> QByteArray:
        """Decode a base64 string back to a QByteArray."""
        return QByteArray(base64.b64decode(s))

    def _persist_layout(self) -> None:
        """Save the current window state and geometry to settings, if enabled."""
        if not self._persist_settings:
            return
        state_str = self._encode_state(self.saveState())
        geom_str = self._encode_state(self.saveGeometry())
        self.settings = replace(
            self.settings,
            window_state=state_str,
            window_geometry=geom_str,
        )
        save_settings(self.settings, self.settings_path)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Persist layout on close, then proceed normally."""
        self._persist_layout()
        super().closeEvent(event)

    # -------------------------------------------------------------------------
    # Settings
    # -------------------------------------------------------------------------

    def configure_settings(
        self,
        settings: Settings,
        *,
        settings_path: str | os.PathLike[str] | None = None,
    ) -> None:
        """Bind persisted settings to the window (called by ``app.main`` at launch).

        Applies the saved theme and restores the dock layout (state + geometry)
        if values are present in ``settings``. Enables persistence of subsequent
        changes. A bare ``MainWindow()`` in a unit test never calls this, so it
        never touches the real settings file.
        """
        self.settings = settings
        self.settings_path = settings_path
        self._persist_settings = True
        self.apply_theme(settings.theme)

        if settings.window_geometry:
            self.restoreGeometry(self._decode_state(settings.window_geometry))
        if settings.window_state:
            self.restoreState(self._decode_state(settings.window_state))

    def open_settings(self) -> None:
        """Open the Settings dialog and apply the result to the live window."""
        dialog = self._make_settings_dialog()
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._apply_settings_result(dialog.settings)

    def _make_settings_dialog(self) -> SettingsDialog:
        return SettingsDialog(
            self.settings, settings_path=self.settings_path, parent=self
        )

    def _apply_settings_result(self, settings: Settings) -> None:
        """Adopt ``settings`` (already persisted by the dialog) and apply its theme."""
        self.settings = settings
        self.apply_theme(settings.theme)

    # -------------------------------------------------------------------------
    # Theme
    # -------------------------------------------------------------------------

    def apply_theme(self, name: str) -> None:
        """Apply the theme ``name`` to the window."""
        self.setStyleSheet(load_stylesheet(name))
        self.current_theme = name
        self.dark_theme_action.setChecked(name == "dark")

    def _on_toggle_dark_theme(self, checked: bool) -> None:
        name = "dark" if checked else "light"
        self.apply_theme(name)
        self._persist_theme(name)

    def _persist_theme(self, name: str) -> None:
        if not self._persist_settings:
            return
        self.settings = replace(self.settings, theme=name)
        save_settings(self.settings, self.settings_path)

    # -------------------------------------------------------------------------
    # Auto-save / repository binding
    # -------------------------------------------------------------------------

    def bind_autosave(
        self,
        repository: Repository,
        *,
        debounce: float = DEFAULT_DEBOUNCE_SECONDS,
    ) -> TabbedEditor:
        """Wire ``repository`` into the tabbed editor so each opened tab auto-saves.

        Each :class:`~ui.note_tab.NoteTab` builds its own auto-saver over this
        repository, so debounced save, create-on-type (#90), and fetch-fresh-on-open
        (#92) all apply per tab. Returns the tabbed editor (was: a controller).
        """
        self.repository = repository
        self.tabbed_editor.set_repository(repository)
        self.tabbed_editor._debounce = debounce
        # Create-on-type: typing into a fresh, unbound tab creates a note to hold
        # the text so it can't be lost on the next navigation (issue #90).
        self.tabbed_editor.tab_orphan_edit.connect(self._on_tab_orphan_edit)
        self._populate_notebook_tree()
        return self.tabbed_editor

    def load_note(self, note: Note) -> None:
        """Open ``note`` in a tab (focusing its tab if it is already open)."""
        if self.repository is None:
            return
        self.tabbed_editor.open(note)

    def _update_word_count(self) -> None:
        count = count_words(self._active_markdown())
        unit = "word" if count == 1 else "words"
        self.word_count_label.setText(f"{count} {unit}")

    # -------------------------------------------------------------------------
    # Session lock
    # -------------------------------------------------------------------------

    def flush_pending(self) -> None:
        """Persist any pending auto-save edits across every open tab."""
        self.tabbed_editor.flush_all()

    def lock_session(self) -> None:
        """Clear all decrypted content and detach the data layer after an auto-lock."""
        # Flush every tab's pending edit, then wipe all tabs (encrypted-vault
        # requirement: no decrypted note text lingers after lock). clear_all
        # flushes each tab before removing it.
        self.tabbed_editor.clear_all()
        self.repository = None
        self.current_notebook_id = None
        self.current_tag_id = None

        self.note_list.blockSignals(True)
        self.note_list.clear()
        self.note_list.blockSignals(False)

        self.notebook_tree.blockSignals(True)
        self.notebook_tree.clear()
        self.notebook_tree.blockSignals(False)

        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)

        self.statusBar().showMessage("Vault locked")

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            QTimer.singleShot(
                0, lambda: self.handle_window_state_change(minimized=self.isMinimized())
            )
        super().changeEvent(event)

    def handle_window_state_change(self, *, minimized: bool) -> None:
        """React to a minimise/restore transition; emit the lock/restore signal."""
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

    # -------------------------------------------------------------------------
    # Keyboard-first navigation
    # -------------------------------------------------------------------------

    def focus_notebook_tree(self) -> None:
        """Move keyboard focus to the notebooks tree (Ctrl+1)."""
        self.notebook_tree.setFocus()

    def focus_note_list(self) -> None:
        """Move keyboard focus to the note list (Ctrl+2)."""
        self.note_list.setFocus()

    def focus_editor(self) -> None:
        """Move keyboard focus to the active tab's editable source pane (Ctrl+3)."""
        tab = self.tabbed_editor.active_tab
        if tab is not None:
            tab.source.setFocus()

    def focus_search(self) -> None:
        """Move keyboard focus to the search box and select its text (Ctrl+F)."""
        self.search_input.setFocus()
        self.search_input.selectAll()

    # -------------------------------------------------------------------------
    # Note list
    # -------------------------------------------------------------------------

    def refresh_notes(self) -> None:
        """Repopulate the note list from the repository for the current view."""
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
        """Open the Ctrl+P quick-switcher and load the chosen note into the editor."""
        dialog = self._make_quick_switcher()
        if dialog is None:
            return
        if (
            dialog.exec() == QDialog.DialogCode.Accepted
            and dialog.selected_note is not None
        ):
            self.load_note(dialog.selected_note)

    def _make_quick_switcher(self) -> QuickSwitcher | None:
        if self.repository is None:
            return None
        return QuickSwitcher(self.repository.list_notes(), parent=self)

    # -------------------------------------------------------------------------
    # Legacy import
    # -------------------------------------------------------------------------

    def open_import_wizard(self) -> None:
        """Open the legacy-``notes.db`` import wizard and refresh on success."""
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
        if self.repository is None:
            return None
        return ImportWizard(self.repository, parent=self)

    # -------------------------------------------------------------------------
    # Notebook tree
    # -------------------------------------------------------------------------

    def _populate_notebook_tree(self) -> None:
        """Rebuild the notebooks/tags tree from the repository."""
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

        if self.current_tag_id is not None and self.current_tag_id in tag_items:
            tree.setCurrentItem(tag_items[self.current_tag_id])
        else:
            self.current_tag_id = None
            if self.current_notebook_id not in notebook_items:
                self.current_notebook_id = None
            tree.setCurrentItem(notebook_items[self.current_notebook_id])

        tree.blockSignals(False)

    def select_notebook(self, notebook_id: int | None) -> None:
        """Filter the note list to ``notebook_id`` (``None`` = all notebooks)."""
        self.current_notebook_id = notebook_id
        self.current_tag_id = None
        self.refresh_notes()

    def select_tag(self, tag_id: int) -> None:
        """Filter the note list to notes carrying ``tag_id``."""
        self.current_tag_id = tag_id
        self.current_notebook_id = None
        self.refresh_notes()

    def add_notebook(
        self, name: str, *, parent_id: int | None = None
    ) -> Notebook | None:
        """Create a notebook and refresh the tree."""
        if self.repository is None:
            return None
        notebook = self.repository.create_notebook(name, parent_id=parent_id)
        self._populate_notebook_tree()
        return notebook

    def rename_notebook(self, notebook_id: int, new_name: str) -> Notebook | None:
        """Rename a notebook and refresh the tree."""
        if self.repository is None:
            return None
        notebook = self.repository.update_notebook(notebook_id, name=new_name)
        self._populate_notebook_tree()
        return notebook

    def remove_notebook(self, notebook_id: int) -> bool:
        """Delete a notebook and refresh."""
        if self.repository is None:
            return False
        deleted = self.repository.delete_notebook(notebook_id)
        self._populate_notebook_tree()
        self.refresh_notes()
        return deleted

    def move_notebook(
        self, notebook_id: int, new_parent_id: int | None
    ) -> Notebook | None:
        """Re-parent a notebook under ``new_parent_id``."""
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

    def _show_notebook_menu(self, pos: QPoint) -> None:
        """Right-click menu on the tree: create / rename / delete notebooks."""
        if self.repository is None:
            return
        item = self.notebook_tree.itemAt(pos)
        if item is not None and item.data(0, _KIND_ROLE) in (
            _KIND_TAG,
            _KIND_TAGS_HEADER,
        ):
            return
        notebook_id = (
            item.data(0, Qt.ItemDataRole.UserRole) if item is not None else None
        )

        menu = QMenu(self.notebook_tree)
        menu.addAction("New notebook…", lambda *_: self._prompt_new_notebook())
        if notebook_id is not None:
            menu.addAction(
                "New sub-notebook…",
                lambda *_: self._prompt_new_notebook(parent_id=notebook_id),
            )
            menu.addAction("Rename…", lambda *_: self._prompt_rename_notebook(notebook_id))
            menu.addAction("Move to…", lambda *_: self._prompt_move_notebook(notebook_id))
            menu.addAction("Delete", lambda *_: self._prompt_delete_notebook(notebook_id))
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
        """Ask for a new parent and re-parent the notebook."""
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

    def _populate_note_list(self, notes: list[Note]) -> None:
        """Replace the list rows with ``notes``."""
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
        if note is None:
            return
        # The row holds a Note snapshot frozen at populate time, but autosave may
        # have written a newer body to the vault since (e.g. a note that was empty
        # when listed now holds text). Re-read it from the repository so we open
        # what is actually saved, not a stale — possibly empty — cached body.
        if self.repository is not None:
            fresh = self.repository.get_note(note.id)
            if fresh is not None:
                note = fresh
        self.load_note(note)
        # load_note flushed the note we just left; only THAT row's title can have
        # changed (e.g. an auto-created note that listed as "Untitled" now has a
        # real title). Update it in place. Rebuilding the whole list here was an
        # O(all-notes) re-query on *every* click AND re-entrant — refresh_notes
        # reset the current row, re-firing this handler in a recursive spiral that
        # froze large vaults (issue #92). An in-place row update emits itemChanged,
        # not currentItemChanged, so it neither rebuilds nor re-enters.
        self._refresh_list_row(previous)

    def _on_note_clicked(self, item: QListWidgetItem | None) -> None:
        """Reopen a note when its (already-selected) row is clicked.

        Complements :meth:`_on_note_selected`, which only fires on a *change* of
        selection. Clicking the row of a note whose tab was closed (Ctrl+W) must
        reopen it even though the selection did not change. Re-reads fresh so a
        stale row snapshot never shows (#92); :meth:`TabbedEditor.open` is
        focus-or-create, so this never duplicates a tab.
        """
        if item is None or self.repository is None:
            return
        note = item.data(Qt.ItemDataRole.UserRole)
        if note is None:
            return
        fresh = self.repository.get_note(note.id)
        self.tabbed_editor.open(fresh if fresh is not None else note)

    def _refresh_list_row(self, item: QListWidgetItem | None) -> None:
        """Re-sync one note-list row's label and cached snapshot from the vault.

        The cheap O(1) counterpart to :meth:`refresh_notes`, for when only a
        single row can have gone stale — e.g. after navigating away from a note
        that auto-save just flushed. Updates the item's stored ``Note`` and its
        text in place; this emits ``itemChanged`` (not ``currentItemChanged``),
        so it does not re-enter :meth:`_on_note_selected`.
        """
        if item is None or self.repository is None:
            return
        snapshot = item.data(Qt.ItemDataRole.UserRole)
        if snapshot is None:
            return
        fresh = self.repository.get_note(snapshot.id)
        if fresh is None:
            return
        item.setData(Qt.ItemDataRole.UserRole, fresh)
        item.setText(fresh.title.strip() or derive_title(fresh.body))

    def _on_tab_orphan_edit(self, tab, _text: str) -> None:
        """Back an unbound tab's first keystroke with a real note (issue #90).

        Fired via :attr:`TabbedEditor.tab_orphan_edit` on the first keystroke into
        an unbound tab (a fresh blank tab). Creates a note in the current view's
        notebook and binds it to *that tab's* saver with a blank baseline — the
        saver records the just-typed text as a pending edit immediately after this
        returns, so the normal debounced-save / save-on-switch path then keeps it
        safe.

        The tab's text is deliberately left untouched: the note is selected in the
        list with signals blocked so the selection does not open/replace a tab and
        overwrite what the user is typing. The tab's title is synced to the note.
        """
        if self.repository is None:
            return
        note = self.repository.create_note(notebook_id=self.current_notebook_id)
        # Baseline blank; the edit that follows in the controller marks it dirty.
        tab.bind_new_note(note)
        # A tag filter would hide the (untagged) new note — drop to All Notes,
        # mirroring new_note(), so it stays visible.
        if self.current_tag_id is not None:
            self._select_all_notes()
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        self.refresh_notes()
        # Select the row without opening a duplicate tab / clobbering the text now
        # living in this tab and its saver.
        self.note_list.blockSignals(True)
        self._select_note(note.id)
        self.note_list.blockSignals(False)
        self.tabbed_editor.set_tab_title(tab, _title_for_note(note))

    def new_note(self) -> Note | None:
        """Create a new, empty note in the current view and open it in a tab."""
        if self.repository is None:
            return None
        note = self.repository.create_note(notebook_id=self.current_notebook_id)
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        if self.current_tag_id is not None:
            self._select_all_notes()
        self.refresh_notes()
        self._select_note(note.id)  # selection opens the tab via _on_note_selected
        self.focus_editor()
        return note

    def _select_all_notes(self) -> None:
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
        for i in range(self.note_list.count()):
            note = self.note_list.item(i).data(Qt.ItemDataRole.UserRole)
            if note is not None and note.id == note_id:
                self.note_list.setCurrentRow(i)
                return

    def delete_note(self, note_id: int) -> bool:
        """Delete a note from the vault, close any tab open on it, and refresh."""
        if self.repository is None:
            return False
        tab = self.tabbed_editor.tab_for_note(note_id)
        deleted = self.repository.delete_note(note_id)
        if deleted and tab is not None:
            # Detach the tab from the now-deleted note BEFORE closing it. Closing
            # flushes, and flushing a deleted row calls update_note on a missing
            # id, which raises NotFoundError. Detaching (load(None)) makes the
            # flush a no-op so the note stays gone.
            tab._controller.saver.load(None)
            self.tabbed_editor.close_tab(tab)
        self.refresh_notes()
        return deleted

    def move_note(self, note_id: int, notebook_id: int | None) -> Note | None:
        """Move a note into ``notebook_id`` and refresh."""
        if self.repository is None:
            return None
        note = self.repository.update_note(note_id, notebook_id=notebook_id)
        self.refresh_notes()
        return note

    def _show_note_menu(self, pos: QPoint) -> None:
        """Right-click menu on the note list: move, edit tags, or delete."""
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
        menu.addAction("Analyze note with AI", lambda *_: self.analyze_note(note))
        menu.addAction("Delete", lambda *_: self._prompt_delete_note(note))
        menu.exec(self.note_list.viewport().mapToGlobal(pos))

    def open_tag_editor(self, note: Note) -> None:
        """Open the tag editor for ``note``."""
        dialog = self._make_tag_editor(note)
        if dialog is None:
            return
        dialog.exec()
        self._populate_notebook_tree()
        self.refresh_notes()

    def _make_tag_editor(self, note: Note) -> TagEditorDialog | None:
        if self.repository is None:
            return None
        return TagEditorDialog(self.repository, note.id, parent=self)

    def _prompt_move_note(self, note: Note) -> None:
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

    # -------------------------------------------------------------------------
    # AI menu
    # -------------------------------------------------------------------------

    def open_api_key_dialog(self) -> None:
        """Open the API-key dialog; no-op when the vault is locked."""
        dialog = self._make_api_key_dialog()
        if dialog is None:
            self.statusBar().showMessage("Vault is locked — unlock first.")
            return
        dialog.exec()

    def _make_api_key_dialog(self) -> "APIKeyDialog | None":
        """Return a new APIKeyDialog, or None if the vault is locked.

        Test seam: call this instead of open_api_key_dialog to get the
        dialog object without running the modal event loop.
        """
        if self.repository is None:
            return None
        return APIKeyDialog(self.repository, parent=self)

    def open_test_connection(self) -> None:
        """Test the stored API key on a background thread; show result in a message box."""
        if self.repository is None:
            self.statusBar().showMessage("Vault is locked — unlock first.")
            return
        if not self.repository.has_api_key():
            QMessageBox.warning(self, "AI", "No API key stored. Set one via AI → Set API key…")
            return
        api_key = self.repository.get_api_key()
        assert api_key is not None  # guarded by has_api_key() above
        worker, thread = self._make_ai_worker(
            api_key, [{"role": "user", "content": "Reply with OK"}]
        )
        self.statusBar().showMessage("Testing connection…")
        self.test_connection_action.setEnabled(False)

        def on_reply(reply: str) -> None:
            self.statusBar().showMessage("Connection OK")
            self.test_connection_action.setEnabled(True)
            QMessageBox.information(self, "AI connection", f"Connection successful.\nReply: {reply}")

        def on_error(message: str) -> None:
            self.statusBar().showMessage("Connection failed")
            self.test_connection_action.setEnabled(True)
            QMessageBox.warning(self, "AI connection", f"Connection failed:\n{message}")

        # These callbacks call QMessageBox, so they MUST run on the GUI thread.
        # Stash them on the worker and connect its signals to the window's
        # bound-method slots, which Qt queues to the main thread (#87).
        worker._main_on_reply = on_reply
        worker._main_on_error = on_error
        worker.finished.connect(self._on_ai_finished)
        worker.error.connect(self._on_ai_error)
        thread.started.connect(worker.run)
        thread.start()

    def open_ai_chat(self) -> None:
        """Show and raise the AI Chat dock, then focus its input field.

        Wires the run-chat and save-note seams into the panel on first call
        (idempotent: the panel checks for None seams itself).
        """
        self._wire_ai_chat_seams()
        self.dock_ai_chat.show()
        self.dock_ai_chat.raise_()
        self.ai_chat_panel.input_edit.setFocus()

    def _wire_ai_chat_seams(self) -> None:
        """Inject run-chat and save-note callbacks into the panel.

        Called lazily from :meth:`open_ai_chat` so the panel is testable
        without a live repository.  Seams are replaced on every call; because
        they close over ``self``, they always see the latest repository state.
        """
        self.ai_chat_panel.run_chat_fn = self._send_chat
        self.ai_chat_panel.save_note_fn = self._save_chat_note

    def _send_chat(self, messages: list[dict]) -> None:
        """Run-chat seam: spin an AiWorker for the chat panel.

        Guards for a locked vault (``self.repository is None``) and a missing
        API key, showing appropriate status messages instead of crashing.
        """
        if self.repository is None:
            self.ai_chat_panel.status_label.setText(
                "Vault is locked — unlock the vault first."
            )
            self.ai_chat_panel._set_thinking(False)
            return
        if not self.repository.has_api_key():
            self.ai_chat_panel.status_label.setText(
                "No API key stored. Set one via AI → Set API key…"
            )
            self.ai_chat_panel._set_thinking(False)
            return

        api_key = self.repository.get_api_key()
        assert api_key is not None  # guarded by has_api_key() above

        worker, thread = self._make_ai_worker(api_key, messages)

        # The panel updates touch widgets, so they MUST run on the GUI thread.
        # Route results through the window's bound-method slots (queued to the
        # main thread); connecting closures would run on the worker thread (#87).
        worker._main_on_reply = self.ai_chat_panel._on_reply
        worker._main_on_error = self.ai_chat_panel._on_error
        worker.finished.connect(self._on_ai_finished)
        worker.error.connect(self._on_ai_error)
        thread.started.connect(worker.run)
        thread.start()

    def _save_chat_note(self, markdown: str) -> None:
        """Save-note seam: create a note from the conversation Markdown.

        Guards for a locked vault; shows a status-bar confirmation on success.
        """
        if self.repository is None:
            self.statusBar().showMessage("Vault is locked — unlock first.")
            return
        self.repository.create_note(
            notebook_id=self.current_notebook_id,
            title="AI Chat",
            body=markdown,
        )
        self.refresh_notes()
        self.statusBar().showMessage("Chat saved as note.")

    def analyze_selection(self) -> None:
        """Seed the AI chat with the currently selected editor text.

        Reads the selection from the editor source, converts Qt's paragraph
        separator (U+2029 and U+2028) back to newlines, asks for an optional
        prompt via :meth:`_ask_analysis_prompt`, then opens the AI chat and
        calls :meth:`~ui.ai_chat.AiChatPanel.start_with_context`.

        No-op (with a status message) if:
        * nothing is selected in the editor,
        * the vault is locked (``self.repository is None``),
        * the vault has no API key, or
        * the user cancels the prompt dialog.
        """
        tab = self.tabbed_editor.active_tab
        selected = tab.source.textCursor().selectedText() if tab is not None else ""
        # Qt uses U+2029 (PARAGRAPH SEPARATOR) between paragraphs and U+2028
        # (LINE SEPARATOR) for soft line-breaks — convert both to plain newlines.
        selected = selected.replace(" ", "\n").replace(" ", "\n")
        if not selected.strip():
            self.statusBar().showMessage("No text selected — select text first.")
            return
        if self.repository is None:
            self.statusBar().showMessage("Vault is locked — unlock the vault first.")
            return
        if not self.repository.has_api_key():
            self.statusBar().showMessage("No API key stored. Set one via AI → Set API key…")
            return
        prompt = self._ask_analysis_prompt()
        if prompt is None:
            return
        self.open_ai_chat()
        self.ai_chat_panel.start_with_context(selected, prompt)

    def analyze_note(self, note=None) -> None:
        """Seed the AI chat with the body of a note.

        ``note`` defaults to the currently selected item in the note list.

        No-op (with a status message) if:
        * no note is provided and none is selected,
        * the vault is locked,
        * the vault has no API key, or
        * the user cancels the prompt dialog.
        """
        if note is None:
            item = self.note_list.currentItem()
            if item is None:
                self.statusBar().showMessage("No note selected — select a note first.")
                return
            note = item.data(Qt.ItemDataRole.UserRole)
        if note is None:
            self.statusBar().showMessage("No note selected — select a note first.")
            return
        if self.repository is None:
            self.statusBar().showMessage("Vault is locked — unlock the vault first.")
            return
        if not self.repository.has_api_key():
            self.statusBar().showMessage("No API key stored. Set one via AI → Set API key…")
            return
        prompt = self._ask_analysis_prompt()
        if prompt is None:
            return
        self.open_ai_chat()
        self.ai_chat_panel.start_with_context(note.body, prompt)

    def _ask_analysis_prompt(self) -> str | None:
        """Ask the user for an optional analysis prompt via a dialog.

        Returns the entered text (possibly blank — blank means "summarize"),
        or ``None`` if the user clicked Cancel.

        This is the monkeypatchable seam for tests: replace it with a lambda
        that returns a fixed string (or ``None``) to bypass the modal dialog.
        """
        text, ok = QInputDialog.getMultiLineText(
            self,
            "Analyze with AI",
            "Optional prompt (leave blank to summarise):",
            "",
        )
        if not ok:
            return None
        return text

    def _make_ai_worker(
        self,
        api_key: str,
        messages: list[dict],
        *,
        timeout: float = 120.0,
    ) -> "tuple[AiWorker, QThread]":
        """Create and wire an AiWorker on a new thread.

        Test seam: call this to get the worker and thread objects without
        triggering a real network call; monkeypatch worker.run or call
        worker.run_with(mock_fn) directly.

        Returns (worker, thread) — the worker has been moved to the thread
        but the thread has NOT been started yet.
        """
        thread = QThread(self)
        worker = AiWorker(api_key, messages, timeout=timeout)
        worker.moveToThread(thread)
        worker._thread = thread  # the result slots (_on_ai_finished/_error) quit it
        # Keep a strong reference to the (worker, thread) pair so neither is
        # garbage-collected mid-flight. The thread is parented to the window, but
        # the worker has no parent (moveToThread forbids one), so without this it
        # would be collected the moment the caller returns and `worker.run` would
        # never fire (issue #85). Release the pair once the thread finishes.
        job = (worker, thread)
        self._ai_jobs.add(job)
        thread.finished.connect(lambda: self._ai_jobs.discard(job))
        thread.finished.connect(thread.deleteLater)
        return worker, thread

    def _on_ai_finished(self, reply: str) -> None:
        """Main-thread slot for ``AiWorker.finished``; dispatches the per-call callback.

        Connecting ``worker.finished`` to this **bound method of the (main-thread)
        window** makes Qt deliver it on the GUI thread via a queued connection — so
        the per-call ``_main_on_reply`` callback (which touches widgets / dialogs)
        runs on the GUI thread. Connecting closures directly delivered on the worker
        thread and crashed the app (#87).
        """
        worker = self.sender()
        callback = getattr(worker, "_main_on_reply", None)
        if callback is not None:
            callback(reply)
        thread = getattr(worker, "_thread", None)
        if thread is not None:
            thread.quit()

    def _on_ai_error(self, message: str) -> None:
        """Main-thread slot for ``AiWorker.error`` — see :meth:`_on_ai_finished`."""
        worker = self.sender()
        callback = getattr(worker, "_main_on_error", None)
        if callback is not None:
            callback(message)
        thread = getattr(worker, "_thread", None)
        if thread is not None:
            thread.quit()
