"""The Ctrl+P quick-switcher: jump to any note by fuzzy title match.

A command-palette-style popup (VS Code Ctrl+P, Sublime "Go to anything"): a
search box over a results list. As the user types, the notes are filtered and
ranked by fuzzy *title* match (:mod:`core.fuzzy`) — every typed character must
appear in the title, in order — and the best match is auto-selected. Enter or a
click loads the chosen note into the editor.

This is navigation, distinct from the middle pane's full-text search
(:mod:`ui.main_window` + :meth:`core.repository.Repository.search_notes`), which
is FTS5 *word* matching over title and body. The quick-switcher matches titles
only and never touches the DB: it ranks an in-memory list of notes handed to it
at construction.

Per CLAUDE.md's strict layering, the UI may import Qt freely; the fuzzy ranking
lives in the Qt-free :mod:`core.fuzzy` so it stays unit-testable. ``core/`` never
imports this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.fuzzy import fuzzy_filter
from core.text import derive_title

if TYPE_CHECKING:
    from PySide6.QtCore import QObject

    from core.repository import Note

WINDOW_TITLE = "Go to note"
_DEFAULT_SIZE = (520, 360)


def _display_title(note: Note) -> str:
    """The title shown (and fuzzily matched) for a note.

    The stored title, or one derived from the body when the title is blank —
    mirroring the note list (:meth:`ui.main_window.MainWindow._populate_note_list`)
    so the switcher and the list label notes identically.
    """
    return note.title.strip() or derive_title(note.body)


class QuickSwitcher(QDialog):
    """Fuzzy note-title switcher.

    Construct with the notes to choose among (the running app passes
    ``repository.list_notes()``); the dialog ranks them by fuzzy title match as
    the user types. On accept, :attr:`selected_note` holds the chosen
    :class:`~core.repository.Note` (``None`` while open or after a cancel).

    Test seams (mirroring :class:`ui.unlock_dialog.UnlockDialog`): set
    :attr:`search_input` text to refilter, read :attr:`results`, and call
    :meth:`accept_selection` / :meth:`select_next` / :meth:`select_previous`
    directly without running the modal event loop.
    """

    def __init__(self, notes: list[Note], *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._notes = list(notes)
        self.selected_note: Note | None = None

        self.setWindowTitle(WINDOW_TITLE)
        self.resize(*_DEFAULT_SIZE)

        layout = QVBoxLayout(self)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Go to note…")
        self.search_input.setClearButtonEnabled(True)
        layout.addWidget(self.search_input)

        self.results = QListWidget()
        layout.addWidget(self.results)

        # Type to refilter; Enter or double-click chooses the selection. Up/Down
        # navigate the results while focus stays in the input (event filter).
        self.search_input.textChanged.connect(self._refilter)
        self.search_input.returnPressed.connect(self.accept_selection)
        self.results.itemActivated.connect(lambda _item: self.accept_selection())
        self.search_input.installEventFilter(self)

        self._refilter()  # populate with all notes, best one selected

    def current_note(self) -> Note | None:
        """The :class:`~core.repository.Note` for the highlighted row, or ``None``."""
        item = self.results.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def accept_selection(self) -> bool:
        """Set :attr:`selected_note` from the current row and accept the dialog.

        Returns ``True`` when a note was chosen (and the dialog accepted), ``False``
        when there is no current selection (e.g. the query matched nothing) — in
        which case the dialog stays open. Safe to call from tests without the modal
        loop, mirroring :meth:`ui.unlock_dialog.UnlockDialog.attempt`.
        """
        note = self.current_note()
        if note is None:
            return False
        self.selected_note = note
        self.accept()
        return True

    def select_next(self) -> None:
        """Move the highlight down one row, wrapping to the top past the end."""
        self._move_selection(1)

    def select_previous(self) -> None:
        """Move the highlight up one row, wrapping to the bottom past the start."""
        self._move_selection(-1)

    def _move_selection(self, delta: int) -> None:
        count = self.results.count()
        if count == 0:
            return
        row = self.results.currentRow()
        if row < 0:
            row = 0
        self.results.setCurrentRow((row + delta) % count)

    def _refilter(self, _text: str | None = None) -> None:
        """Rebuild the results list, ranked by fuzzy title match for the query.

        Connected to ``search_input.textChanged`` (which passes the new text, here
        ignored in favour of reading the field) and called once at construction to
        show every note. The top (best) row is auto-selected so Enter works
        immediately.
        """
        query = self.search_input.text().strip()
        ranked = fuzzy_filter(query, self._notes, key=_display_title)
        self.results.clear()
        for note in ranked:
            item = QListWidgetItem(_display_title(note))
            item.setData(Qt.ItemDataRole.UserRole, note)
            self.results.addItem(item)
        if self.results.count() > 0:
            self.results.setCurrentRow(0)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Route Up/Down in the search box to results-list navigation."""
        if obj is self.search_input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self.select_next()
                return True
            if key == Qt.Key.Key_Up:
                self.select_previous()
                return True
        return super().eventFilter(obj, event)
