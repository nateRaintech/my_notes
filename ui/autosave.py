"""Drives debounced auto-save for the Markdown editor (Qt layer).

Wires a :class:`ui.editor.MarkdownEditor` to a pure-Python
:class:`core.autosave.AutoSaver`: the editor's ``source.textChanged`` feeds the
current text into the saver, and a repeating ``QTimer`` asks the saver to flush
once its debounce window has elapsed. The debounce *decision* lives in ``core/``
(Qt-free, fake-clock-testable); this class only supplies the Qt heartbeat and the
signal wiring — mirroring how the vault's idle auto-lock policy is driven by a UI
``QTimer`` (LESSONS.md).

Per CLAUDE.md's strict layering, the UI layer may import Qt freely; ``core/``
must never import this module.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QObject, QTimer

from core.autosave import DEFAULT_DEBOUNCE_SECONDS, AutoSaver

if TYPE_CHECKING:
    from core.repository import Note, Repository

    from ui.editor import MarkdownEditor

# How often the timer asks the saver whether a debounced flush is due. A fraction
# of the debounce window so a save lands within roughly one tick of the user
# pausing, without polling needlessly often.
_TICK_MS = 200


class AutoSaveController(QObject):
    """Binds a :class:`MarkdownEditor` to an :class:`AutoSaver` on a timer.

    Construct it with the editor and a keyed :class:`~core.repository.Repository`;
    it connects the editor's edits to the saver and starts a debounce timer
    immediately. Load a note for editing with :meth:`load_note`. The pure-Python
    saver is exposed as :attr:`saver` for tests and for callers that want to drive
    flushes directly.
    """

    def __init__(
        self,
        editor: MarkdownEditor,
        repository: Repository,
        *,
        debounce: float = DEFAULT_DEBOUNCE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._editor = editor
        self.saver = AutoSaver(repository, debounce=debounce, clock=clock)

        editor.source.textChanged.connect(self._on_text_changed)

        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    def load_note(self, note: Note) -> None:
        """Flush any pending edit, then load ``note`` into the editor and saver.

        Binding the saver *before* setting the editor text matters: ``set_markdown``
        emits ``textChanged`` synchronously, which feeds the same text back through
        :meth:`AutoSaver.edit` — and because it equals the just-loaded baseline, the
        note is not marked dirty (no spurious save right after loading).
        """
        self.saver.flush()
        self.saver.load(note.id, note.body)
        self._editor.set_markdown(note.body)

    def _on_text_changed(self) -> None:
        self.saver.edit(self._editor.markdown())

    def _on_tick(self) -> None:
        self.saver.flush_if_due()

    def flush(self) -> bool:
        """Persist any pending edit immediately (e.g. on note switch or close)."""
        return self.saver.flush()

    def stop(self) -> None:
        """Stop the debounce timer after a final flush (e.g. on app shutdown)."""
        self.flush()
        self._timer.stop()
