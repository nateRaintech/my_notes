"""The tabbed editor: the window's central widget, one tab per open note.

Owns a stack of two pages — a placeholder shown when nothing is open, and a
``QTabWidget`` whose pages are :class:`ui.note_tab.NoteTab` instances. Opening a
note focuses its existing tab or creates a new one; the note in every other tab
is left untouched. The shared Markdown preview and the word-count/AI features
(in ``MainWindow``) follow the active tab via :attr:`active_tab_changed`.

Per CLAUDE.md's strict layering, the UI layer may import Qt freely; ``core/``
must never import this module.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QLabel,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.autosave import DEFAULT_DEBOUNCE_SECONDS
from core.text import derive_title
from ui.note_tab import NoteTab

if TYPE_CHECKING:
    from core.repository import Note, Repository

_PLACEHOLDER_TEXT = "No note open — pick one in the list or press Ctrl+N"


def _title_for(note: Note) -> str:
    return note.title.strip() or derive_title(note.body)


class TabbedEditor(QWidget):
    """A ``QTabWidget`` of :class:`NoteTab`s with an empty-state placeholder."""

    #: The active tab changed (selection, open, or close).
    active_tab_changed = Signal()
    #: The active tab's text changed (drives preview + word count).
    tab_text_changed = Signal()
    #: An unbound tab got its first keystroke: (NoteTab, text).
    tab_orphan_edit = Signal(object, str)

    def __init__(
        self,
        repository: Repository | None = None,
        *,
        debounce: float = DEFAULT_DEBOUNCE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._debounce = debounce
        self._clock = clock

        # The tab we were last on, so we can flush it when the user switches away.
        self._active_tab: NoteTab | None = None

        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(False)
        self._tabs.tabCloseRequested.connect(self._on_close_requested)
        self._tabs.currentChanged.connect(self._on_current_changed)

        self._placeholder = QLabel(_PLACEHOLDER_TEXT)
        self._placeholder.setEnabled(False)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._placeholder)  # index 0
        self._stack.addWidget(self._tabs)          # index 1

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)
        self._update_stack()

    def set_repository(self, repository: Repository) -> None:
        self._repository = repository

    # -- queries ------------------------------------------------------------
    def count(self) -> int:
        return self._tabs.count()

    @property
    def active_tab(self) -> NoteTab | None:
        widget = self._tabs.currentWidget()
        return widget if isinstance(widget, NoteTab) else None

    def tab_for_note(self, note_id: int) -> NoteTab | None:
        for i in range(self._tabs.count()):
            tab = self._tabs.widget(i)
            if isinstance(tab, NoteTab) and tab.note_id == note_id:
                return tab
        return None

    # -- open / create ------------------------------------------------------
    def open(self, note: Note) -> NoteTab:
        """Focus the tab editing ``note``, or create one and focus it."""
        existing = self.tab_for_note(note.id)
        if existing is not None:
            self._tabs.setCurrentWidget(existing)
            return existing
        tab = self._make_tab()
        tab.load(note)
        self._tabs.addTab(tab, _title_for(note))
        self._tabs.setCurrentWidget(tab)
        self._update_stack()
        return tab

    def new_blank_tab(self) -> NoteTab:
        """Open an empty, unbound tab (for New Note)."""
        tab = self._make_tab()
        self._tabs.addTab(tab, "Untitled")
        self._tabs.setCurrentWidget(tab)
        self._update_stack()
        return tab

    def set_tab_title(self, tab: NoteTab, title: str) -> None:
        index = self._tabs.indexOf(tab)
        if index != -1:
            self._tabs.setTabText(index, title or "Untitled")

    # -- close / flush ------------------------------------------------------
    def close_tab(self, tab: NoteTab | None) -> None:
        if tab is None:
            return
        index = self._tabs.indexOf(tab)
        if index == -1:
            return
        tab.flush()
        self._tabs.removeTab(index)
        tab.stop()
        tab.deleteLater()
        self._update_stack()
        self.active_tab_changed.emit()

    def flush_all(self) -> None:
        for i in range(self._tabs.count()):
            tab = self._tabs.widget(i)
            if isinstance(tab, NoteTab):
                tab.flush()

    def clear_all(self) -> None:
        """Flush then remove every tab and wipe content (on lock)."""
        while self._tabs.count():
            tab = self._tabs.widget(0)
            if isinstance(tab, NoteTab):
                tab.flush()
                tab.stop()
            self._tabs.removeTab(0)
            tab.deleteLater()
        self._update_stack()
        self.active_tab_changed.emit()

    # -- internals ----------------------------------------------------------
    def _make_tab(self) -> NoteTab:
        assert self._repository is not None, "set_repository before opening tabs"
        tab = NoteTab(
            self._repository, debounce=self._debounce, clock=self._clock
        )
        tab.text_changed.connect(self._on_tab_text_changed)
        tab.orphan_edit_detected.connect(
            lambda text, t=tab: self.tab_orphan_edit.emit(t, text)
        )
        return tab

    def _on_current_changed(self, _index: int) -> None:
        """Flush the tab being left behind, then announce the new active tab.

        Switching away from a tab persists its pending edit immediately — the
        same save-on-switch guarantee the single editor gave on note change. The
        per-tab debounce timer would eventually save it anyway, but flushing here
        keeps the note list's titles current the moment the user navigates.
        """
        new = self.active_tab
        if self._active_tab is not None and self._active_tab is not new:
            self._active_tab.flush()
        self._active_tab = new
        self.active_tab_changed.emit()

    def _on_tab_text_changed(self) -> None:
        if self.sender() is self.active_tab:
            self.tab_text_changed.emit()

    def _on_close_requested(self, index: int) -> None:
        tab = self._tabs.widget(index)
        if isinstance(tab, NoteTab):
            self.close_tab(tab)

    def _update_stack(self) -> None:
        self._stack.setCurrentIndex(1 if self._tabs.count() else 0)
