# Tabbed Editor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single central editor with a tabbed editor so opening another note never overwrites the note being edited.

**Architecture:** A new `NoteTab` widget (one source pane + its own `AutoSaveController`, reusing all the tested autosave/create-on-type machinery) and a `TabbedEditor` wrapper around `QTabWidget` become the window's central widget. `MainWindow` opens/focuses tabs instead of replacing one editor; a single shared preview dock and the word-count/AI/focus features follow the active tab. `core/` is untouched.

**Tech Stack:** PySide6 (Qt 6), pytest (offscreen Qt), existing `core.autosave.AutoSaver` / `ui.autosave.AutoSaveController`.

---

## File structure

- **Create** `ui/note_tab.py` — `NoteTab`: one editable source + its own `AutoSaveController`; re-emits `orphan_edit_detected` and `text_changed`.
- **Create** `ui/tabbed_editor.py` — `TabbedEditor`: `QTabWidget` wrapper; `open(note)` focus-or-create, `new_blank_tab`, `close`, `flush_all`, `clear_all`, `active_tab`, placeholder when empty.
- **Create** `tests/test_note_tab.py`, `tests/test_tabbed_editor.py`.
- **Modify** `ui/main_window.py` — central widget becomes `TabbedEditor`; preview/word-count/AI/focus follow the active tab; `_on_note_selected` opens a tab; lock clears tabs; compatibility properties `editor`/`autosave` resolve to the active tab.
- **Modify** existing `tests/` that reach `window.editor` / `window.autosave` (covered in Task 8).

Reference signatures (do not change `core/`):
- `ui.autosave.AutoSaveController(editor, repository, *, debounce=DEFAULT_DEBOUNCE_SECONDS, clock=time.monotonic, parent=None)` — has `.saver`, `.load_note(note)`, `.flush()`, `.stop()`, signal `orphan_edit_detected = Signal(str)`. It only uses `editor.source` (a `QPlainTextEdit`), `editor.markdown()`, `editor.set_markdown(text)`.
- `core.autosave.DEFAULT_DEBOUNCE_SECONDS` (= 0.8); `core.text.derive_title(body)`.
- `Repository.get_note(id)`, `Repository.create_note(*, notebook_id=None, title="", body="")`.

---

## Task 1: `NoteTab` — editing surface bound to one note

**Files:**
- Create: `ui/note_tab.py`
- Test: `tests/test_note_tab.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_note_tab.py
import os
import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.repository import Repository

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtWidgets import QPlainTextEdit  # noqa: E402

from ui.note_tab import NoteTab  # noqa: E402

DEBOUNCE = 1.0


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def repo():
    conn = sqlcipher.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    schema.migrate(conn)
    try:
        yield Repository(conn)
    finally:
        conn.close()


def test_note_tab_loads_a_note_and_autosaves_edits(qapp, repo):
    note = repo.create_note(title="N", body="hello")
    tab = NoteTab(repo, debounce=DEBOUNCE, clock=FakeClock())

    tab.load(note)
    assert isinstance(tab.source, QPlainTextEdit)
    assert tab.markdown() == "hello"
    assert tab.note_id == note.id

    tab.source.setPlainText("hello world")
    assert tab.flush() is True
    assert repo.get_note(note.id).body == "hello world"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_note_tab.py::test_note_tab_loads_a_note_and_autosaves_edits -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ui.note_tab'`.

- [ ] **Step 3: Write minimal implementation**

```python
# ui/note_tab.py
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

from PySide6.QtCore import Signal
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
```

Note: `AutoSaveController` currently has no `clock` parameter pass-through — verify. If `AutoSaveController.__init__` does not accept `clock`, add it: it already builds `AutoSaver(repository, debounce=debounce, clock=clock)` — thread a `clock: Callable[[], float] = time.monotonic` kwarg through `AutoSaveController.__init__` to the `AutoSaver`. (Check `ui/autosave.py` first; add the kwarg only if missing.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_note_tab.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/note_tab.py tests/test_note_tab.py ui/autosave.py
git commit -m "feat(#94): add NoteTab — a per-note editing surface with its own autosave"
```

---

## Task 2: `NoteTab` — create-on-type re-emits and binds

**Files:**
- Modify: `ui/note_tab.py` (already re-emits; this task locks behavior with tests)
- Test: `tests/test_note_tab.py`

- [ ] **Step 1: Write the failing test**

```python
def test_typing_into_an_unbound_tab_signals_orphan_then_binds(qapp, repo):
    tab = NoteTab(repo, debounce=DEBOUNCE, clock=FakeClock())
    seen: list[str] = []

    def handle(text: str) -> None:
        seen.append(text)
        note = repo.create_note(notebook_id=None)
        tab.bind_new_note(note)

    tab.orphan_edit_detected.connect(handle)
    assert tab.note_id is None

    tab.source.setPlainText("fresh thought")

    assert seen == ["fresh thought"]
    assert tab.note_id is not None
    assert tab.flush() is True
    assert repo.get_note(tab.note_id).body == "fresh thought"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_note_tab.py::test_typing_into_an_unbound_tab_signals_orphan_then_binds -q`
Expected: This should already PASS if Task 1's implementation is correct (the handler binds synchronously during the signal, so the controller's subsequent `saver.edit(text)` marks it dirty). If it FAILS, the bug is ordering — ensure `orphan_edit_detected` is connected before `text_changed` so the controller emits the orphan signal before its own `saver.edit`. (This is a verification test; if it passes immediately, note that and continue.)

- [ ] **Step 3: (only if failing) Fix ordering** — no code change expected; the `AutoSaveController._on_text_changed` already emits the orphan signal before calling `saver.edit`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_note_tab.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_note_tab.py
git commit -m "test(#94): lock NoteTab create-on-type behavior"
```

---

## Task 3: `TabbedEditor` — open focus-or-create, active tab, close

**Files:**
- Create: `ui/tabbed_editor.py`
- Test: `tests/test_tabbed_editor.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tabbed_editor.py
import os
import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.repository import Repository

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.note_tab import NoteTab  # noqa: E402
from ui.tabbed_editor import TabbedEditor  # noqa: E402

DEBOUNCE = 1.0


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def repo():
    conn = sqlcipher.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    schema.migrate(conn)
    try:
        yield Repository(conn)
    finally:
        conn.close()


def test_open_creates_a_tab_and_focuses_it(qapp, repo):
    a = repo.create_note(title="A", body="a body")
    te = TabbedEditor(repo, debounce=DEBOUNCE)

    tab = te.open(a)

    assert isinstance(tab, NoteTab)
    assert te.count() == 1
    assert te.active_tab is tab
    assert te.active_tab.markdown() == "a body"


def test_open_same_note_focuses_existing_tab_no_duplicate(qapp, repo):
    a = repo.create_note(title="A", body="a body")
    b = repo.create_note(title="B", body="b body")
    te = TabbedEditor(repo, debounce=DEBOUNCE)

    tab_a = te.open(a)
    te.open(b)
    again = te.open(a)

    assert again is tab_a          # same tab reused
    assert te.count() == 2         # not three
    assert te.active_tab is tab_a  # focused


def test_close_removes_the_tab(qapp, repo):
    a = repo.create_note(title="A", body="a body")
    te = TabbedEditor(repo, debounce=DEBOUNCE)
    te.open(a)

    te.close_tab(te.active_tab)

    assert te.count() == 0
    assert te.active_tab is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tabbed_editor.py -q`
Expected: FAIL — `No module named 'ui.tabbed_editor'`.

- [ ] **Step 3: Write minimal implementation**

```python
# ui/tabbed_editor.py
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

        self._tabs = QTabWidget()
        self._tabs.setTabsClosable(True)
        self._tabs.setMovable(False)
        self._tabs.tabCloseRequested.connect(self._on_close_requested)
        self._tabs.currentChanged.connect(lambda _i: self.active_tab_changed.emit())

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

    def _on_tab_text_changed(self) -> None:
        if self.sender() is self.active_tab:
            self.tab_text_changed.emit()

    def _on_close_requested(self, index: int) -> None:
        tab = self._tabs.widget(index)
        if isinstance(tab, NoteTab):
            self.close_tab(tab)

    def _update_stack(self) -> None:
        self._stack.setCurrentIndex(1 if self._tabs.count() else 0)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tabbed_editor.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/tabbed_editor.py tests/test_tabbed_editor.py
git commit -m "feat(#94): add TabbedEditor — open/focus/close note tabs with empty-state placeholder"
```

---

## Task 4: `TabbedEditor` — flush_all, clear_all, active-tab signals

**Files:**
- Modify: `ui/tabbed_editor.py` (already implemented above; this task tests it)
- Test: `tests/test_tabbed_editor.py`

- [ ] **Step 1: Write the failing test**

```python
def test_flush_all_persists_every_open_tab(qapp, repo):
    a = repo.create_note(title="A", body="a")
    b = repo.create_note(title="B", body="b")
    te = TabbedEditor(repo, debounce=DEBOUNCE)
    ta = te.open(a)
    tb = te.open(b)
    ta.source.setPlainText("a edited")
    tb.source.setPlainText("b edited")

    te.flush_all()

    assert repo.get_note(a.id).body == "a edited"
    assert repo.get_note(b.id).body == "b edited"


def test_clear_all_flushes_then_removes_every_tab(qapp, repo):
    a = repo.create_note(title="A", body="a")
    te = TabbedEditor(repo, debounce=DEBOUNCE)
    ta = te.open(a)
    ta.source.setPlainText("a edited")

    te.clear_all()

    assert te.count() == 0
    assert te.active_tab is None
    assert repo.get_note(a.id).body == "a edited"  # flushed before wipe


def test_active_tab_changed_fires_on_open_and_close(qapp, repo):
    a = repo.create_note(title="A", body="a")
    te = TabbedEditor(repo, debounce=DEBOUNCE)
    fired = []
    te.active_tab_changed.connect(lambda: fired.append(True))

    te.open(a)
    te.close_tab(te.active_tab)

    assert len(fired) >= 2
```

- [ ] **Step 2: Run test to verify it fails / passes**

Run: `python -m pytest tests/test_tabbed_editor.py -q`
Expected: PASS (implemented in Task 3). If any fail, fix in `ui/tabbed_editor.py` to match these contracts.

- [ ] **Step 3: (only if a test failed) Fix the implementation** to satisfy the contract above.

- [ ] **Step 4: Run all TabbedEditor tests**

Run: `python -m pytest tests/test_tabbed_editor.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_tabbed_editor.py ui/tabbed_editor.py
git commit -m "test(#94): cover TabbedEditor flush_all/clear_all/active-tab signals"
```

---

## Task 5: Verify full suite still green before integration

**Files:** none (gate)

- [ ] **Step 1:** Run `python -m pytest -q` — Expected: PASS (new files only added; nothing wired into `MainWindow` yet).
- [ ] **Step 2:** Run `python -m ruff check .` — Expected: `All checks passed!`.
- [ ] **Step 3:** Commit nothing (gate only). If red, fix before continuing.

---

## Task 6: `MainWindow` — central widget becomes `TabbedEditor`; preview + word count follow active tab

**Files:**
- Modify: `ui/main_window.py`
- Test: `tests/test_autosave_controller.py`, `tests/test_word_count_status.py`

This is the integration core. Make these edits together, then fix the tests they break.

- [ ] **Step 1: Edit `MainWindow.__init__`** — replace the `MarkdownEditor` central widget with `TabbedEditor` and a standalone preview.

Replace:
```python
        self.editor = MarkdownEditor()
        # --- Central widget: editor source -----------------------------------
        self.editor.source.setMinimumWidth(_EDITOR_MIN_WIDTH)
        self.setCentralWidget(self.editor.source)
```
with:
```python
        # Tabbed editor is the central widget. The repository is wired in later
        # by bind_autosave; until then no tabs can be opened (empty placeholder).
        self.tabbed_editor = TabbedEditor()
        self.tabbed_editor.setMinimumWidth(_EDITOR_MIN_WIDTH)
        self.setCentralWidget(self.tabbed_editor)

        # Shared Markdown preview (dock); always renders the active tab.
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
```

Update the preview dock to use `self.preview`:
```python
        self.dock_preview.setWidget(self.preview)
```
(replacing `self.dock_preview.setWidget(self.editor.preview)`).

Add imports at top of `ui/main_window.py`: `from PySide6.QtWidgets import QTextEdit` (extend the existing import list) and `from ui.tabbed_editor import TabbedEditor`. Remove the now-unused `from ui.editor import MarkdownEditor` import only after Task 8 (other methods still reference it until then — keep it until cleanup).

- [ ] **Step 2: Wire preview + word count to the active tab.** Replace the old `self.editor.source.textChanged.connect(self._update_word_count)` wiring and add preview rendering:

```python
        self.tabbed_editor.active_tab_changed.connect(self._on_active_tab_changed)
        self.tabbed_editor.tab_text_changed.connect(self._on_active_text_changed)
```

Add methods:
```python
    def _on_active_tab_changed(self) -> None:
        self._render_preview()
        self._update_word_count()

    def _on_active_text_changed(self) -> None:
        self._render_preview()
        self._update_word_count()

    def _render_preview(self) -> None:
        tab = self.tabbed_editor.active_tab
        self.preview.setMarkdown(tab.markdown() if tab is not None else "")

    def _active_markdown(self) -> str:
        tab = self.tabbed_editor.active_tab
        return tab.markdown() if tab is not None else ""
```

Change `_update_word_count` to read the active tab:
```python
    def _update_word_count(self) -> None:
        count = count_words(self._active_markdown())
        unit = "word" if count == 1 else "words"
        self.word_count_label.setText(f"{count} {unit}")
```

- [ ] **Step 3: Add compatibility properties** so existing call sites and tests that use `window.editor` / `window.autosave` resolve to the active tab. Add to `MainWindow`:

```python
    @property
    def editor(self) -> NoteTab | None:
        """The active tab's editing surface, or None when no note is open.

        Compatibility shim: the app used to have a single ``editor``. Most
        callers want the active tab's ``source`` / ``markdown()``.
        """
        return self.tabbed_editor.active_tab

    @property
    def autosave(self):
        """The active tab's auto-save controller, or None when no tab is open."""
        tab = self.tabbed_editor.active_tab
        return tab._controller if tab is not None else None
```

Add `from ui.note_tab import NoteTab` import (TYPE_CHECKING is fine for the annotation; import normally is also acceptable).

- [ ] **Step 4: Rewrite `bind_autosave`** to wire the repository into the tabbed editor instead of creating one controller:

```python
    def bind_autosave(self, repository, *, debounce=DEFAULT_DEBOUNCE_SECONDS):
        """Attach the repository so each opened tab auto-saves over it."""
        self.repository = repository
        self.tabbed_editor.set_repository(repository)
        self.tabbed_editor._debounce = debounce
        self.tabbed_editor.tab_orphan_edit.connect(self._on_tab_orphan_edit)
        self._populate_notebook_tree()
        return self.tabbed_editor
```

(Note: tests that did `controller = window.bind_autosave(repo)` and then drove `controller` directly are updated in Task 8; `bind_autosave` now returns the `TabbedEditor`.)

- [ ] **Step 5: Rewrite `load_note` and `_on_note_selected`** to open tabs:

```python
    def load_note(self, note) -> None:
        """Open ``note`` in a tab (focus its tab if already open)."""
        if self.repository is None:
            return
        self.tabbed_editor.open(note)
```

```python
    def _on_note_selected(self, current, previous) -> None:
        if current is None:
            return
        note = current.data(Qt.ItemDataRole.UserRole)
        if note is None:
            return
        if self.repository is not None:
            fresh = self.repository.get_note(note.id)
            if fresh is not None:
                note = fresh
        self.tabbed_editor.open(note)
        self._refresh_list_row(previous)
```

- [ ] **Step 6: Update the failing tests.** Run `python -m pytest tests/test_word_count_status.py -q` and `tests/test_autosave_controller.py -q`. For word-count tests that do `window.editor.set_markdown("hello world")` with no note open, change them to open a note first, e.g.:

```python
def test_word_count_updates_on_edit(qapp, repo):
    note = repo.create_note(title="N", body="")
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()
    window.load_note(note)              # opens a tab
    window.editor.source.setPlainText("hello world")
    assert window.word_count_label.text() == "2 words"
```

For `tests/test_autosave_controller.py`, replace direct controller usage:
- `controller = window.bind_autosave(repo, debounce=DEBOUNCE)` then `window.load_note(note)` then `controller.flush()` →
  `window.bind_autosave(repo, debounce=DEBOUNCE); window.load_note(note); window.editor.source.setPlainText(...); window.autosave.flush()`.
- Assertions on `window.autosave.saver.note_id` still work (active tab's saver).

- [ ] **Step 7: Run the suite, iterate to green**

Run: `python -m pytest tests/test_word_count_status.py tests/test_autosave_controller.py -q`
Expected: PASS after edits.

- [ ] **Step 8: Commit**

```bash
git add ui/main_window.py tests/test_word_count_status.py tests/test_autosave_controller.py
git commit -m "feat(#94): make TabbedEditor the central widget; preview + word count follow the active tab"
```

---

## Task 7: `MainWindow` — New Note opens a tab; create-on-type targets the active tab

**Files:**
- Modify: `ui/main_window.py`
- Test: `tests/test_new_note.py`, `tests/test_autosave_controller.py`

- [ ] **Step 1: Rewrite `new_note`** to open a fresh tab and bind it on first edit via the existing flow:

```python
    def new_note(self):
        """Create a new note, open it in a fresh tab, and focus the editor."""
        if self.repository is None:
            return None
        note = self.repository.create_note(notebook_id=self.current_notebook_id)
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        if self.current_tag_id is not None:
            self._select_all_notes()
        self.refresh_notes()
        self._select_note(note.id)        # selection opens the tab via _on_note_selected
        self.tabbed_editor.active_tab.source.setFocus()
        return note
```

- [ ] **Step 2: Rewrite `_on_orphan_edit` as `_on_tab_orphan_edit(tab, text)`** — bind the *tab* that fired, then update the list and the tab title:

```python
    def _on_tab_orphan_edit(self, tab, _text: str) -> None:
        """Back an unbound tab's first keystroke with a real note (issue #90)."""
        if self.repository is None:
            return
        note = self.repository.create_note(notebook_id=self.current_notebook_id)
        tab.bind_new_note(note)
        if self.current_tag_id is not None:
            self._select_all_notes()
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        self.refresh_notes()
        self.note_list.blockSignals(True)
        self._select_note(note.id)
        self.note_list.blockSignals(False)
        self.tabbed_editor.set_tab_title(tab, _title_for_note(note))
```

Add a module helper near the top of `ui/main_window.py` (or reuse `derive_title` inline):
```python
def _title_for_note(note) -> str:
    return note.title.strip() or derive_title(note.body)
```
Add `from core.text import count_words, derive_title` (extend the existing `core.text` import).

Remove the old `self.autosave.orphan_edit_detected.connect(...)` line (the connection now lives in `bind_autosave` via `tab_orphan_edit`).

- [ ] **Step 3: Update tests.** In `tests/test_new_note.py`, `test_new_note_is_selected_loaded_and_focused` should now assert the focused widget is `window.tabbed_editor.active_tab.source`:

```python
    assert window.focusWidget() is window.tabbed_editor.active_tab.source
```
The `test_typing_in_new_note_does_not_create_a_second_note` and create-on-type tests in `tests/test_autosave_controller.py` should still pass (the active tab is bound by `new_note`'s open, so typing does not orphan). Update any `window.editor.source` references to `window.tabbed_editor.active_tab.source` if a test had no active tab before typing.

- [ ] **Step 4: Run tests, iterate to green**

Run: `python -m pytest tests/test_new_note.py tests/test_autosave_controller.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/main_window.py tests/test_new_note.py tests/test_autosave_controller.py
git commit -m "feat(#94): New Note opens a tab; create-on-type binds the active tab and titles it"
```

---

## Task 8: `MainWindow` — focus mode, AI actions, flush/lock, delete-closes-tab; migrate remaining tests

**Files:**
- Modify: `ui/main_window.py`
- Test: `tests/test_*` (any still referencing `window.editor` / `window.autosave` / single-editor behavior)

- [ ] **Step 1: Point active-tab features at the active tab.**

`focus_editor`:
```python
    def focus_editor(self) -> None:
        tab = self.tabbed_editor.active_tab
        if tab is not None:
            tab.source.setFocus()
```

`analyze_selection` reads the active tab's source:
```python
        tab = self.tabbed_editor.active_tab
        if tab is None:
            self.statusBar().showMessage("No note open — open a note first.")
            return
        selected = tab.source.textCursor().selectedText()
```
(keep the U+2029/U+2028 → newline conversion and the rest).

For `analyze_text_action` enable/disable: remove the old `self.editor.source.copyAvailable.connect(...)` line; instead leave the action enabled and rely on the existing "No text selected" guard. Set `self.analyze_text_action.setEnabled(True)` at creation.

- [ ] **Step 2: flush + lock.**

```python
    def flush_pending(self) -> None:
        self.tabbed_editor.flush_all()
```

In `lock_session`, replace the `self.autosave.stop()` / `self.editor.set_markdown("")` block with:
```python
        self.tabbed_editor.flush_all()
        self.tabbed_editor.clear_all()
        self.repository = None
        self.current_notebook_id = None
        self.current_tag_id = None
```
(keep the note_list / notebook_tree / search clearing). Remove references to `self.autosave` (now a property).

- [ ] **Step 3: delete-closes-tab.** In `delete_note`, after deleting, close any tab open on it:
```python
        tab = self.tabbed_editor.tab_for_note(note_id)
        if tab is not None:
            self.tabbed_editor.close_tab(tab)
        self.refresh_notes()
```
(replace the old `self.autosave.saver.load(None)` / `self.editor.set_markdown("")` handling).

- [ ] **Step 4: Ctrl+W closes the active tab.** In `__init__` add:
```python
        self.close_tab_shortcut = QShortcut(QKeySequence("Ctrl+W"), self)
        self.close_tab_shortcut.activated.connect(
            lambda: self.tabbed_editor.close_tab(self.tabbed_editor.active_tab)
        )
```

- [ ] **Step 5: Update `app.py` shutdown** (`_shutdown`): replace `if window.autosave is not None: window.autosave.stop()` with `window.flush_pending()` (flush all tabs). Verify `app.py` `_bind_vault` still calls `window.bind_autosave(repository)` then `window.refresh_notes()` — unchanged.

- [ ] **Step 6: Migrate remaining tests.** Run the full suite and fix references file-by-file:

Run: `python -m pytest -q 2>&1 | tail -40`

Common migrations:
- `window.editor.source.setPlainText(x)` → ensure a note/tab is open first (call `window.load_note(note)` or `window.new_note()`), then `window.editor.source.setPlainText(x)` (the `editor` property returns the active tab).
- `window.editor.markdown()` → unchanged (property), but requires an active tab.
- `window.editor.set_markdown(x)` on a window with no tab → open a note first.
- `window.autosave...` → unchanged (property) once a tab is active.
- `tests/test_idle_lock.py`, `tests/test_lock_on_minimize.py`: after lock, assert `window.tabbed_editor.count() == 0` instead of `window.editor.markdown() == ""`.
- `tests/test_delete_note.py`: assert the tab closed (`window.tabbed_editor.tab_for_note(id) is None`) where it asserted the editor cleared.

Work one failing file at a time; re-run that file until green, then move on.

- [ ] **Step 7: Remove dead code.** Once green, delete the now-unused `from ui.editor import MarkdownEditor` import from `ui/main_window.py` (the `MarkdownEditor` class stays for its own unit tests). Remove `_EDITOR_MIN_WIDTH` only if unused.

- [ ] **Step 8: Full green + lint**

Run: `python -m pytest -q` — Expected: all pass.
Run: `python -m ruff check .` — Expected: `All checks passed!`.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat(#94): route focus/AI/lock/delete through the active tab; migrate tests to tabbed editor"
```

---

## Task 9: Manual verification + packaging

**Files:** none

- [ ] **Step 1:** Build the exe: `python -m PyInstaller my_notes.spec --noconfirm --clean` (stop any running `my_notes` first).
- [ ] **Step 2:** Launch `dist/my_notes.exe`, unlock, and verify by hand:
  - Type a new note from cold start → a tab appears titled from the first line.
  - Click an existing note → opens in its own tab; the first tab is untouched.
  - Click the same note again → focuses the existing tab (no duplicate).
  - Ctrl+W closes a tab; closing all shows the placeholder.
  - Preview and word count track the active tab.
  - Lock/minimise (if configured) → tabs clear; re-unlock starts empty with no data loss.
- [ ] **Step 3:** Open the PR to `main` with `Closes #94`, move the board card to In Review.

---

## Self-review notes

- **Spec coverage:** behavior (Tasks 6–8), `NoteTab`/`TabbedEditor` architecture (Tasks 1–4), save model incl. flush-on-switch via per-tab controllers and flush_all/clear_all on lock (Tasks 4, 8), preview-follows-active-tab (Task 6), create-on-type & fetch-fresh carry-over (Tasks 2, 6–7), edge cases delete/close/empty (Tasks 3, 8), non-goals respected (no reorder/split/detach/restore). Optional "•" dirty marker intentionally omitted (YAGNI) — add later if wanted.
- **Type consistency:** `NoteTab.note_id`, `.load`, `.bind_new_note`, `.flush`, `.stop`, `.source`, `.markdown`/`.set_markdown`; `TabbedEditor.open`, `.new_blank_tab`, `.active_tab`, `.tab_for_note`, `.close_tab`, `.flush_all`, `.clear_all`, `.set_tab_title`, `.count`, signals `active_tab_changed`/`tab_text_changed`/`tab_orphan_edit` — used consistently across tasks.
- **Known churn:** Task 8 migrates existing tests; the `editor`/`autosave` compatibility properties keep most call sites working once a tab is active.
