# Tabbed Editor — Design

**Issue:** #94
**Date:** 2026-06-18
**Status:** Approved (design)

## Problem

The app has a single central editor. Opening another note replaces whatever is in
the editor, so the note you're writing is destroyed on navigation. The autosave
and stale-list fixes (#90, #92) ensure the *data* is saved, but the editing
*context* is still wiped on every navigation — which reads as data loss to the
user.

## Goal

Keep multiple notes open at once as **tabs**. Clicking a note focuses its tab (or
opens a new one); the note currently being edited is never touched. The Markdown
preview follows the active tab.

## Non-goals (this version)

- Drag-to-reorder tabs.
- Split view / multiple visible editors.
- Detach a tab into its own OS window.
- Restoring open tabs across app restarts or re-unlocks (session-only for now).

## Behavior

- A tab bar sits above the editor. Each open note is one tab, labelled with the
  note's title (falling back to the derived title, then "Untitled").
- **Open from the note list:** if the note is already open, focus its tab;
  otherwise open a new tab and focus it. No duplicate tabs for one note.
- **New Note (Ctrl+N):** opens a fresh tab and focuses it.
- **Close a tab:** via its `×` button or **Ctrl+W**. Closing the last tab is
  allowed and shows a quiet placeholder: "No note open — pick one in the list or
  press Ctrl+N".
- **Preview:** remains a single dock; always mirrors the active tab and
  re-renders on active-tab change and on the active tab's text changes.
- Opening another note never modifies the note in any other tab.

## Architecture

Strict UI/`core` layering is preserved: `core/` never imports Qt; this is all in
`ui/`.

### `NoteTab` (new, `ui/note_tab.py`)

One self-contained editing surface:

- Owns an editable Markdown **source** pane (`QPlainTextEdit`).
- Owns an `AutoSaver` (from `core.autosave`) bound to the note it is editing.
- Tracks its bound note id and exposes `markdown()` / `set_markdown()` /
  `flush()`.
- Carries over the per-tab behaviors from earlier fixes:
  - **Create-on-type (#90):** typing into a tab whose editor is unbound creates a
    note to hold the text and binds it.
  - **Fetch-fresh-on-open (#92):** a note is read fresh from the repository when
    opened into a tab, never from a stale snapshot.

**What it does:** edits exactly one note and auto-saves it.
**How you use it:** `open(note)`, `markdown()`, `flush()`, `note_id`.
**Depends on:** a `Repository`, `core.autosave.AutoSaver`, `core.text`.

### `TabbedEditor` (new, `ui/tabbed_editor.py`)

Wraps a `QTabWidget` and manages the open `NoteTab`s; becomes the window's central
widget.

- `open(note)` — focus the existing tab for `note.id`, else create a new
  `NoteTab` and focus it.
- `new_blank_tab()` — open an empty unbound tab (for New Note).
- `close(index)` / current-tab accessors / `active_tab`.
- `flush_all()` — flush every open tab (close / lock / shutdown).
- `clear_all()` — close every tab and wipe content (lock).
- Signals: `active_tab_changed` (preview + word count follow it), and surfaces the
  active tab's `text_changed`.
- A single shared debounce `QTimer` ticks `flush_if_due()` on all open tabs (one
  timer, not one per tab).

**What it does:** owns the set of open note tabs and routes "open/close/flush".
**How you use it:** `open(note)`, `active_tab`, `flush_all()`, `clear_all()`.
**Depends on:** `NoteTab`, Qt.

### `MainWindow` changes (`ui/main_window.py`)

- Central widget becomes the `TabbedEditor` instead of a single `editor.source`.
- The preview dock holds one shared preview `QTextEdit`, re-rendered from the
  active tab on `active_tab_changed` and on the active tab's text changes.
- `_on_note_selected` calls `tabbed_editor.open(fresh_note)` instead of replacing
  one editor's text.
- `new_note()` opens a new tab.
- Word count, **Focus mode**, and the AI **Analyze selection / Analyze note**
  actions read the **active tab's** source.
- `flush_pending()` → `tabbed_editor.flush_all()`.
- `lock_session()` → `tabbed_editor.clear_all()` (wipe all decrypted content),
  then detach the repository as today.
- `bind_autosave(repository)` wires the repository into the `TabbedEditor` so each
  new tab gets a saver over it.

## Save model

- Each tab debounce-saves its own note independently.
- Switching away from a tab flushes that tab immediately (same guarantee as
  today's note-switch), so its latest edit is persisted before it loses focus.
- App close, idle auto-lock, and lock-on-minimise call `flush_all()` then
  `clear_all()`.
- **Lock wipes all tabs** — required by the encrypted-vault model: no decrypted
  note text may linger after a lock. Re-unlock starts with no tabs open.

## Edge cases

- **Deleting a note that is open in a tab:** close that tab (if it was the active
  one, fall back to the placeholder or an adjacent tab).
- **Closing a tab with a pending edit:** flush it first, then close.
- **Empty unbound tab left open (typed nothing):** behaves like today's empty
  "Untitled" note — no note is created until the first keystroke (#90 carry-over).
- **Lock while tabs are dirty:** `flush_all()` runs before `clear_all()`, so no
  edit is lost.

## Optional (low-priority polish)

- A small "•" marker on a tab while it has an unsaved (mid-debounce) edit, cleared
  on flush. Include only if cheap; not required for the feature.

## Testing strategy

- `core` is unchanged; existing `core/autosave` tests still cover the saver.
- New unit tests for `NoteTab` (bind/edit/flush, create-on-type, fetch-fresh) and
  `TabbedEditor` (open focuses existing vs creates new, close, flush_all,
  clear_all, active_tab_changed).
- `MainWindow` integration tests updated/added: clicking a note opens/focuses a
  tab without disturbing other tabs; New Note opens a tab; lock clears tabs;
  preview/word-count follow the active tab; the #90 and #92 guarantees still hold
  through the tabbed path.
- `pytest` and `ruff` green at every step.

## Rollout

Built in small, independently-tested steps (NoteTab → TabbedEditor → MainWindow
wiring → preview/word-count/AI/focus follow-through → lock/close integration),
keeping the suite green throughout. Detailed steps live in the implementation
plan.
