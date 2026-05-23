"""Debounced auto-save policy for note edits (pure Python, no Qt).

KeePass-style note-taking should never make the user think about saving, so a
loaded note's edits persist automatically once typing settles — there is no Save
button. This module owns the *policy*: which note is being edited, whether it has
unsaved changes, and whether the debounce window has elapsed. It never sleeps and
never imports Qt.

Mirrors the auto-lock idle policy in :mod:`core.vault`: a UI driver
(:class:`ui.autosave.AutoSaveController`) feeds edits in via :meth:`AutoSaver.edit`
and ticks :meth:`AutoSaver.flush_if_due` on a ``QTimer``; the debounce decision is
made here against an injectable monotonic ``clock``, so all timing logic stays
unit-testable with a fake clock and ``core/`` stays Qt-free (CLAUDE.md).

The body is persisted verbatim and the title is re-derived from it via
:func:`core.text.derive_title` (the repository stores both verbatim — deriving the
display title is this layer's call, not the repository's).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable

from core.text import derive_title

if TYPE_CHECKING:
    from core.repository import Repository

# Default quiet period (seconds) after the last edit before a save fires. Long
# enough that ordinary typing doesn't write on every keystroke, short enough that
# a save lands almost as soon as the user pauses.
DEFAULT_DEBOUNCE_SECONDS = 0.8


class AutoSaver:
    """Tracks the active note's pending edits and persists them on a debounce.

    Pure policy — no Qt, no sleeping. The lifecycle a UI driver follows is:

    * :meth:`load` to bind the note being edited (and its current text),
    * :meth:`edit` on each change to the editor's text,
    * :meth:`flush_if_due` on a timer tick to persist once typing has settled,
    * :meth:`flush` to persist immediately (on note switch or app close).

    "Dirty" means the pending text differs from what was last saved; a save only
    happens while dirty, so reverting an edit or reloading the same text writes
    nothing.
    """

    def __init__(
        self,
        repository: Repository,
        *,
        debounce: float = DEFAULT_DEBOUNCE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if debounce < 0:
            raise ValueError("debounce must be non-negative")
        self._repo = repository
        self._debounce = debounce
        self._clock = clock
        self._note_id: int | None = None
        self._saved_text = ""
        self._pending_text = ""
        self._dirty = False
        self._last_edit = 0.0

    @property
    def note_id(self) -> int | None:
        """The id of the note currently bound for editing, or ``None``."""
        return self._note_id

    @property
    def is_dirty(self) -> bool:
        """Whether there is a pending edit not yet persisted."""
        return self._dirty

    def load(self, note_id: int | None, text: str = "") -> None:
        """Bind the active note and its current text; clears any pending edit.

        Pass ``note_id=None`` to detach — with no note bound, edits are ignored
        and nothing is saved. Flush the previously-loaded note first if it may
        have unsaved edits (:class:`ui.autosave.AutoSaveController` does this on
        note switch); :meth:`load` itself does not save the outgoing note.
        """
        self._note_id = note_id
        self._saved_text = text
        self._pending_text = text
        self._dirty = False
        self._last_edit = self._clock()

    def edit(self, text: str) -> None:
        """Record the editor's current text as a pending edit.

        Marks the note dirty and stamps the edit time only when ``text`` differs
        from what was last saved, so re-rendering or programmatically reloading
        the same text schedules no redundant write. Reverting back to the saved
        text clears the dirty flag. A no-op when no note is bound.
        """
        if self._note_id is None:
            return
        self._pending_text = text
        if text != self._saved_text:
            self._dirty = True
            self._last_edit = self._clock()
        else:
            self._dirty = False

    def is_due(self) -> bool:
        """Whether a save is pending and its debounce quiet window has elapsed."""
        if not self._dirty or self._note_id is None:
            return False
        return self._clock() - self._last_edit >= self._debounce

    def flush_if_due(self) -> bool:
        """Persist the pending edit iff dirty and the debounce has elapsed.

        Returns ``True`` if a write happened. This is what the UI timer calls each
        tick.
        """
        if self.is_due():
            return self.flush()
        return False

    def flush(self) -> bool:
        """Persist the pending edit now, regardless of the debounce window.

        Used on note switch and app close so no edit is lost. Writes the body
        verbatim and re-derives the title via :func:`core.text.derive_title`.
        Returns ``True`` if a write happened (a note was bound with unsaved
        changes); ``False`` otherwise.
        """
        if not self._dirty or self._note_id is None:
            return False
        text = self._pending_text
        self._repo.update_note(self._note_id, title=derive_title(text), body=text)
        self._saved_text = text
        self._dirty = False
        return True
