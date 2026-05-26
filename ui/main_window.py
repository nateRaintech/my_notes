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

from PySide6.QtCore import QByteArray, QEvent, QPoint, Qt, QTimer, Signal
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

        # No repository until a vault is opened; auto-save is bound later.
        self.autosave: AutoSaveController | None = None
        self.repository: Repository | None = None
        self.current_notebook_id: int | None = None
        self.current_tag_id: int | None = None
        self._minimize_locked = False

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

        self.editor = MarkdownEditor()

        # --- Central widget: editor source -----------------------------------

        self.editor.source.setMinimumWidth(_EDITOR_MIN_WIDTH)
        self.setCentralWidget(self.editor.source)

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
        self.dock_preview.setWidget(self.editor.preview)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_preview)

        # --- Signals ---------------------------------------------------------

        self.search_input.textChanged.connect(self._on_search_changed)
        self.note_list.currentItemChanged.connect(self._on_note_selected)
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

        view_menu.addSeparator()
        self.focus_mode_action = view_menu.addAction("Focus mode")
        self.focus_mode_action.setCheckable(True)
        self.focus_mode_action.setShortcut(QKeySequence("Ctrl+Shift+F"))
        self.focus_mode_action.triggered.connect(
            lambda checked: self.set_focus_mode(checked)
        )

        # --- Status bar ------------------------------------------------------

        self.word_count_label = QLabel()
        self.statusBar().addPermanentWidget(self.word_count_label)
        self.editor.source.textChanged.connect(self._update_word_count)
        self._update_word_count()

        self.statusBar().showMessage("Ready")

        self.apply_theme(DEFAULT_THEME)

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
    ) -> AutoSaveController:
        """Attach debounced auto-save to the editor, backed by ``repository``."""
        self.repository = repository
        self.autosave = AutoSaveController(
            self.editor, repository, debounce=debounce, parent=self
        )
        self._populate_notebook_tree()
        return self.autosave

    def load_note(self, note: Note) -> None:
        """Load ``note`` into the editor for editing (and debounced auto-saving)."""
        if self.autosave is None:
            self.editor.set_markdown(note.body)
            return
        self.autosave.load_note(note)

    def _update_word_count(self) -> None:
        count = count_words(self.editor.markdown())
        unit = "word" if count == 1 else "words"
        self.word_count_label.setText(f"{count} {unit}")

    # -------------------------------------------------------------------------
    # Session lock
    # -------------------------------------------------------------------------

    def flush_pending(self) -> None:
        """Persist any pending auto-save edit immediately."""
        if self.autosave is not None:
            self.autosave.flush()

    def lock_session(self) -> None:
        """Clear all decrypted content and detach the data layer after an auto-lock."""
        if self.autosave is not None:
            self.autosave.stop()
        self.autosave = None
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

        self.editor.set_markdown("")
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
        """Move keyboard focus to the editor's editable source pane (Ctrl+3)."""
        self.editor.source.setFocus()

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
        if note is not None:
            self.load_note(note)

    def new_note(self) -> Note | None:
        """Create a new, empty note in the current view and open it for editing."""
        if self.repository is None:
            return None
        note = self.repository.create_note(notebook_id=self.current_notebook_id)
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
        """Delete a note from the vault and refresh the list."""
        if self.repository is None:
            return False
        editing_deleted = (
            self.autosave is not None and self.autosave.saver.note_id == note_id
        )
        deleted = self.repository.delete_note(note_id)
        if deleted and editing_deleted:
            self.autosave.saver.load(None)
            self.editor.set_markdown("")
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
