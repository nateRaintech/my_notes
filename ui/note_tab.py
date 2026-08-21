"""A single tabbed editing surface: one note, its own debounced auto-save.

Each open note in the tabbed editor is one ``NoteTab``. It owns an editable
Markdown source pane and an :class:`ui.autosave.AutoSaveController` bound to the
note it is editing, so every guarantee the single-editor app had — debounced
auto-save, save-on-switch (here: save-on-tab-change), create-on-type (#90),
fetch-fresh-on-open (#92) — applies per tab. The shared preview lives in
``MainWindow``; a tab only owns its source.

Per CLAUDE.md's strict layering, the UI layer may import Qt freely; ``core/``
must never import this module.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

from core.autosave import DEFAULT_DEBOUNCE_SECONDS
from ui.autosave import AutoSaveController

if TYPE_CHECKING:
    from core.repository import Note, Repository

_SOURCE_MIN_WIDTH = 240


class NoteTab(QWidget):
    """One editable note with its own auto-saver.

    Exposes the same ``source`` / ``markdown()`` / ``set_markdown()`` seam the
    old ``MarkdownEditor`` did, so :class:`AutoSaveController` drives it directly.
    Re-emits its controller's ``orphan_edit_detected`` and the source's text
    changes so the owner (``TabbedEditor`` / ``MainWindow``) can react.
    """

    #: Re-emitted from the controller: the first keystroke into an unbound tab.
    orphan_edit_detected = Signal(str)
    #: Re-emitted from the source pane on every edit (for preview / word count).
    text_changed = Signal()
    #: The source pane was right-clicked, at the given widget-relative position.
    #: Re-emitted so the window can append its Tools submenu to Qt's standard
    #: editor context menu (#99) without the tab knowing what a tool is.
    context_menu_requested = Signal(QPoint)

    def __init__(
        self,
        repository: Repository,
        *,
        debounce: float = DEFAULT_DEBOUNCE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.source = QPlainTextEdit()
        self.source.setPlaceholderText("Write Markdown here…")
        self.source.setMinimumWidth(_SOURCE_MIN_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.source)

        self._controller = AutoSaveController(
            self, repository, debounce=debounce, clock=clock, parent=self
        )
        self._controller.orphan_edit_detected.connect(self.orphan_edit_detected)
        self.source.textChanged.connect(self.text_changed)

        self.source.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.source.customContextMenuRequested.connect(self.context_menu_requested)

    # -- editor seam used by AutoSaveController -----------------------------
    def markdown(self) -> str:
        return self.source.toPlainText()

    def set_markdown(self, text: str) -> None:
        self.source.setPlainText(text)

    # -- public API ---------------------------------------------------------
    def load(self, note: Note) -> None:
        """Load ``note`` into this tab (flush any prior note first)."""
        self._controller.load_note(note)

    def bind_new_note(self, note: Note) -> None:
        """Bind a freshly created note with a blank baseline (create-on-type)."""
        self._controller.saver.load(note.id, "")

    def flush(self) -> bool:
        return self._controller.flush()

    def stop(self) -> None:
        self._controller.stop()

    @property
    def note_id(self) -> int | None:
        return self._controller.saver.note_id
