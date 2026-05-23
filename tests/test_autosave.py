"""Unit tests for ``core.autosave`` — the debounced auto-save policy.

Pure Python, no Qt and no sleeping: a :class:`FakeClock` drives the debounce
window and a :class:`RecordingRepository` spy captures the writes the saver would
make. (The real editor → saver → encrypted-DB path is proven end-to-end in
``tests/test_autosave_controller.py``.)
"""

import pytest

from core.autosave import DEFAULT_DEBOUNCE_SECONDS, AutoSaver
from core.text import derive_title

DEBOUNCE = 1.0


class FakeClock:
    """A controllable monotonic clock: call it to read, ``advance`` to move it."""

    def __init__(self, start: float = 1000.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


class RecordingRepository:
    """Captures ``update_note`` calls — the only repository method AutoSaver uses."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, str, str]] = []

    def update_note(self, note_id, *, title, body):
        self.calls.append((note_id, title, body))


def make_saver(*, debounce: float = DEBOUNCE):
    repo = RecordingRepository()
    clock = FakeClock()
    saver = AutoSaver(repo, debounce=debounce, clock=clock)
    return saver, repo, clock


# -- construction -----------------------------------------------------------


def test_negative_debounce_rejected():
    with pytest.raises(ValueError):
        AutoSaver(RecordingRepository(), debounce=-1)


def test_default_debounce_is_positive():
    assert DEFAULT_DEBOUNCE_SECONDS > 0


def test_starts_clean_and_detached():
    saver, _repo, _clock = make_saver()
    assert saver.note_id is None
    assert saver.is_dirty is False


# -- debounce timing --------------------------------------------------------


def test_no_save_before_debounce_elapses():
    saver, repo, clock = make_saver()
    saver.load(1, "")
    saver.edit("new text")
    assert saver.is_dirty is True

    clock.advance(DEBOUNCE - 0.01)
    assert saver.is_due() is False
    assert saver.flush_if_due() is False
    assert repo.calls == []


def test_saves_after_debounce_elapses():
    saver, repo, clock = make_saver()
    saver.load(1, "")
    saver.edit("new text")

    clock.advance(DEBOUNCE)
    assert saver.is_due() is True
    assert saver.flush_if_due() is True
    assert repo.calls == [(1, derive_title("new text"), "new text")]
    # The save cleared the dirty flag — a second tick does not write again.
    assert saver.is_dirty is False
    assert saver.flush_if_due() is False
    assert len(repo.calls) == 1


def test_rapid_edits_debounce_to_a_single_write():
    saver, repo, clock = make_saver()
    saver.load(1, "")

    # Three edits, each within the debounce window, keep deferring the save.
    for i, text in enumerate(("a", "ab", "abc")):
        saver.edit(text)
        clock.advance(DEBOUNCE - 0.1)
        assert saver.flush_if_due() is False, f"should not save mid-typing (edit {i})"
    assert repo.calls == []

    # Once typing settles for a full debounce window, exactly one write lands,
    # carrying only the final text — not one write per keystroke.
    clock.advance(DEBOUNCE)
    assert saver.flush_if_due() is True
    assert repo.calls == [(1, derive_title("abc"), "abc")]


# -- dirty tracking ---------------------------------------------------------


def test_reloading_same_text_does_not_dirty():
    saver, repo, clock = make_saver()
    saver.load(1, "hello")
    # Re-feeding the loaded text (as a programmatic set_markdown would) is a no-op.
    saver.edit("hello")
    assert saver.is_dirty is False
    clock.advance(DEBOUNCE * 2)
    assert saver.flush_if_due() is False
    assert repo.calls == []


def test_reverting_to_saved_text_clears_dirty():
    saver, repo, clock = make_saver()
    saver.load(1, "hello")
    saver.edit("hello world")
    assert saver.is_dirty is True
    saver.edit("hello")  # typed, then deleted back to the original
    assert saver.is_dirty is False
    clock.advance(DEBOUNCE * 2)
    assert saver.flush_if_due() is False
    assert repo.calls == []


def test_edit_ignored_when_no_note_loaded():
    saver, repo, clock = make_saver()
    saver.edit("orphan text")
    assert saver.is_dirty is False
    clock.advance(DEBOUNCE * 2)
    assert saver.is_due() is False
    assert saver.flush_if_due() is False
    assert saver.flush() is False
    assert repo.calls == []


# -- flush ------------------------------------------------------------------


def test_flush_persists_immediately_ignoring_debounce():
    saver, repo, clock = make_saver()
    saver.load(1, "")
    saver.edit("urgent")
    # No time advanced — flush() writes anyway (used on note switch / close).
    assert saver.flush() is True
    assert repo.calls == [(1, derive_title("urgent"), "urgent")]
    assert saver.is_dirty is False


def test_flush_is_noop_when_not_dirty():
    saver, repo, _clock = make_saver()
    saver.load(1, "unchanged")
    assert saver.flush() is False
    assert repo.calls == []


def test_persists_body_verbatim_with_derived_title():
    saver, repo, _clock = make_saver()
    saver.load(1, "")
    body = "# My Heading\n\nSome **bold** body text."
    saver.edit(body)
    assert saver.flush() is True
    note_id, title, saved_body = repo.calls[-1]
    assert note_id == 1
    assert saved_body == body  # verbatim, markers and all
    assert title == "My Heading" == derive_title(body)


# -- note switching ---------------------------------------------------------


def test_load_clears_pending_edit_of_previous_note():
    saver, repo, clock = make_saver()
    saver.load(1, "first")
    saver.edit("first edited")  # pending, not yet flushed
    assert saver.is_dirty is True

    # Loading a new note discards the previous note's pending edit (the caller
    # is expected to flush() first if it wants to keep it).
    saver.load(2, "second")
    assert saver.note_id == 2
    assert saver.is_dirty is False
    clock.advance(DEBOUNCE * 2)
    assert saver.flush_if_due() is False
    assert repo.calls == []


def test_load_none_detaches():
    saver, repo, clock = make_saver()
    saver.load(1, "x")
    saver.load(None)
    assert saver.note_id is None
    saver.edit("ignored")
    assert saver.is_dirty is False
    assert saver.flush() is False
    assert repo.calls == []


def test_second_note_saves_independently_after_switch():
    saver, repo, clock = make_saver()
    saver.load(1, "one")
    saver.edit("one v2")
    assert saver.flush() is True  # save note 1 before switching

    saver.load(2, "two")
    saver.edit("two v2")
    clock.advance(DEBOUNCE)
    assert saver.flush_if_due() is True
    assert repo.calls == [
        (1, derive_title("one v2"), "one v2"),
        (2, derive_title("two v2"), "two v2"),
    ]
