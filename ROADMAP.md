# ROADMAP.md — Project Direction

This is the **north star** for autonomous development. When the board runs dry, autodev decomposes the next unfinished milestone here into issues instead of inventing busywork (see `autodev.md` → Priority 5A). This is the single most powerful lever for steering long-horizon, hands-off work: **edit this file to change what the project builds next.**

## How it works

- Milestones are listed **in priority order**. The *current milestone* is the first one not marked `Done`.
- Each milestone has a **capability checklist**. Each unchecked item `[ ]` is a unit of work waiting to become a GitHub issue. Autodev creates one issue per capability, on demand, when there's no other work.
- **GitHub Milestones mirror this file.** Each milestone below has a matching GitHub Milestone whose **title is identical** (e.g. `M2: Encrypted Vault Core`). Issues are filed against it with `--milestone "M2: Encrypted Vault Core"`, so progress is queryable: `gh issue list --milestone "M2: Encrypted Vault Core" --state all`.
- Autodev checks a box `[x]` when that capability's issue is merged, and marks the milestone `Done` when all its boxes are checked. You can check/uncheck boxes too — adding an unchecked item is how you queue new work.
- When every milestone is `Done`, autodev falls back to speculative quality work and, after a few idle sessions, emails for direction. **The response is to add a milestone here.**

## Authoring guidance (for whoever owns direction — usually a human)

- Keep capabilities **small and testable** — roughly one PR each. "Add CSV export endpoint" not "build reporting."
- Write them as outcomes, not tasks. Autodev fills in the implementation issue (approach, acceptance criteria) when it picks one up.
- Order milestones by dependency — earlier ones unblock later ones.
- If a capability needs a genuine product/business decision, leave it unchecked and add a `> Open question:` note beneath it; autodev will leave it alone rather than guess.

---

## Project summary

**my_notes** is a local-first, encrypted Markdown notes desktop app (PySide6). KeePass-style:
the user sets a master password and notes live in a single SQLCipher-encrypted vault file,
decrypted only in memory. Notebooks/folders + tags for organization, Markdown editing with live
preview, full-text search, and an importer for the legacy `notes.db`. Packaged to a Windows `.exe`
with PyInstaller. **No AI features in this version** — the goal is to make core note-taking excellent.
See `CLAUDE.md` for the full design and architecture (strict `core/` logic vs `ui/` separation).

---

## Milestones

### M3: Notes CRUD + Markdown Editor — `status: planned`

The core note-taking experience, end to end for the happy path.

- [x] `core/repository.py`: create / read / update / delete notes and notebooks through the encrypted vault, with tests _(done: typed CRUD layer over a DB-API connection — frozen `Note`/`Notebook` value objects, `_UNSET` partial-update sentinel, FK cascade/SET NULL, writes-to-notes/notebooks-only with FTS kept in sync by schema triggers, 24 tests incl. a vault round-trip — #21 / PR #22)_
- [x] Main window: resizable 3-pane layout (notebooks/tags tree | note list | editor) via `QSplitter` _(done: horizontal `QSplitter` (`self.splitter`) holding three typed, non-collapsible panes — `notebook_tree`/`note_list`/`editor` — that later M3/M4 capabilities populate rather than rebuild; default sizes (220,300,480) favor the editor, only the editor stretches on resize; shell-only, no data binding yet — #23 / PR #24)_
- [x] Markdown editor with live preview (editable source + `QTextDocument.setMarkdown()` preview pane) _(done: `ui/editor.py` `MarkdownEditor(QWidget)` — editable `QPlainTextEdit` source beside a read-only `QTextEdit` preview in a non-collapsible `QSplitter`; live `textChanged`→`setMarkdown()` re-render (no Save button); `set_markdown`/`markdown()` seams; replaced the placeholder `editor` in `MainWindow`; 9 headless Qt tests; `core/` stays Qt-free — #25 / PR #26)_
- [ ] Auto-save: edits persist (debounced) without an explicit Save button
- [ ] Tags: assign/remove tags on a note and filter the note list by tag

### M4: Search, Organization & Import — `status: planned`

Usable at scale, and able to bring existing notes in.

- [ ] Full-text search across all notes via FTS5, with a results list
- [ ] Quick-switcher (Ctrl+P): jump to any note by fuzzy title match
- [ ] Notebook management: create / rename / delete / nest notebooks and move notes between them
- [ ] Import wizard: read a legacy `notes.db`, map `content`→body / `category`→notebook / timestamps, and write into the encrypted vault
- [ ] Startup flow: create-vault and unlock-vault dialogs wired into app launch

### M5: Polish, Packaging & Docs — `status: planned`

- [ ] Dark theme via QSS, keyboard-first navigation, and a word count
- [ ] PyInstaller spec that builds a working `my_notes.exe`, with the build documented
- [ ] Settings: configurable idle-lock timeout, vault file location, and optional lock-on-minimize
- [ ] User-facing documentation is complete and accurate (README + usage notes)

---

## Completed milestones

### M2: Encrypted Vault Core — `status: done` (completed 2026-05-22)

The security foundation. Everything persists through the encrypted vault; build and prove it before any UI stores data.

- [x] Spike: confirm `sqlcipher3-binary` opens an encrypted DB on Windows **and** bundles via PyInstaller; record findings + the fallback decision (field-level AES-GCM) in `LESSONS.md` _(done: use `sqlcipher3>=0.6.2`, not `-binary`; SQLCipher viable on Windows + PyInstaller, AES-GCM fallback not needed — #11)_
- [x] `core/crypto.py`: derive a 256-bit key from a master password via **Argon2id** (tunable params), with unit tests for determinism and wrong-password behavior _(done: low-level `hash_secret_raw(Type.ID)`, tunable `KdfParams`, 9 pure-Python tests — #13)_
- [x] `core/vault.py`: create / open / unlock a SQLCipher vault file with the derived key; a wrong password fails cleanly with no partial reads _(done: `Vault.create/unlock/lock`, salt in plaintext `<vault>.meta` sidecar, raw-hex key + page-1 read validation → `InvalidPassword`, no-clobber/no-silent-create guards, 12 tests — #15)_
- [x] Auto-lock: close the vault and wipe the key from memory on demand and after a configurable idle timeout _(done: in-place `bytearray` key wipe on `lock()` + injectable-clock idle policy (`idle_timeout`/`touch`/`is_idle_expired`/`lock_if_idle`), UI drives via QTimer, `core/` stays Qt-free, 10 tests — #17)_
- [x] Schema + migrations: `notebooks`, `notes`, `tags`, `note_tags`, and a `notes_fts` (FTS5) table are created on first open _(done: `core/schema.py` forward-only idempotent `migrate(conn)` keyed off `PRAGMA user_version` (SCHEMA_VERSION=1); external-content `notes_fts` FTS5 + ai/ad/au sync triggers; wired into `Vault.create`/`unlock` with `PRAGMA foreign_keys = ON`; 11 tests — #19)_

### M1: Project Foundation — `status: done` (completed 2026-05-22)

The project builds, runs, and has a green CI pipeline.

- [x] App launches: a minimal PySide6 main window opens from a fresh checkout (`python app.py`)
- [x] Test framework wired up with at least one real test that passes under `pytest`
- [x] CI runs the real test suite + `ruff` on every PR to `main` (expand the scaffold gate)
- [x] `README.md` documents how to set up, run, and test the app
