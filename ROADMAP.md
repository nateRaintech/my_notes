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

### M4: Search, Organization & Import — `status: planned`

Usable at scale, and able to bring existing notes in.

- [x] Full-text search across all notes via FTS5, with a results list _(done: `ui/main_window.py` middle pane is now a composite `note_pane` — `QLineEdit` `search_input` above the `note_list` — fed from the keyed `Repository`. `bind_autosave` stores `self.repository`; `refresh_notes()` populates the list (empty box → `list_notes()` newest-first, non-blank → `search_notes(query)`); rows label by title→`derive_title(body)` fallback and carry the `Note` in `UserRole`; `search_input.textChanged`→live refilter, `note_list.currentItemChanged`→`load_note`. `_populate_note_list` blocks signals during rebuild so refiltering never spuriously loads a note (loads only on explicit click). `app.py` calls `refresh_notes()` on launch. Builds on the `search_notes` engine (#31/#32) + keyed `Repository` from the unlock flow (#33/#34). 10 new behavioral tests against a real Repository; `core/` stays Qt-free. No "New note" authoring UI yet — fresh vault lists nothing until notes exist. — #35 / PR #36)_
- [ ] Quick-switcher (Ctrl+P): jump to any note by fuzzy title match
- [ ] Notebook management: create / rename / delete / nest notebooks and move notes between them
- [ ] Import wizard: read a legacy `notes.db`, map `content`→body / `category`→notebook / timestamps, and write into the encrypted vault
- [x] Startup flow: create-vault and unlock-vault dialogs wired into app launch _(done: `ui/unlock_dialog.py` `UnlockDialog(QDialog)` — filesystem mode auto-detection (create iff neither the vault file nor its `.meta` sidecar exists, mirroring `Vault.create`'s no-clobber guard; stray `.meta` → unlock), create-mode password+confirm validation, unlock-mode `InvalidPassword`→inline error with retry (dialog stays open, no exception escapes); OK routes through a public `attempt() -> bool` not `accept()` so failures stay open and headless tests drive it without the modal loop. `app.py` shows the dialog at launch, builds `Repository(vault.connection)` → `MainWindow.bind_autosave` → `show()` on accept, exits rc 0 on cancel, and flushes auto-save + locks the vault on `aboutToQuit`; vault path `~/.my_notes/notes.vault`, overridable via `MY_NOTES_VAULT`. 10 headless Qt tests against a real Vault in `tmp_path`; `core/` stays Qt-free — #33 / PR #34)_

### M5: Polish, Packaging & Docs — `status: planned`

- [ ] Dark theme via QSS, keyboard-first navigation, and a word count
- [ ] PyInstaller spec that builds a working `my_notes.exe`, with the build documented
- [ ] Settings: configurable idle-lock timeout, vault file location, and optional lock-on-minimize
- [ ] User-facing documentation is complete and accurate (README + usage notes)

---

## Completed milestones

### M3: Notes CRUD + Markdown Editor — `status: done` (completed 2026-05-22)

The core note-taking experience, end to end for the happy path.

- [x] `core/repository.py`: create / read / update / delete notes and notebooks through the encrypted vault, with tests _(done: typed CRUD layer over a DB-API connection — frozen `Note`/`Notebook` value objects, `_UNSET` partial-update sentinel, FK cascade/SET NULL, writes-to-notes/notebooks-only with FTS kept in sync by schema triggers, 24 tests incl. a vault round-trip — #21 / PR #22)_
- [x] Main window: resizable 3-pane layout (notebooks/tags tree | note list | editor) via `QSplitter` _(done: horizontal `QSplitter` (`self.splitter`) holding three typed, non-collapsible panes — `notebook_tree`/`note_list`/`editor` — that later M3/M4 capabilities populate rather than rebuild; default sizes (220,300,480) favor the editor, only the editor stretches on resize; shell-only, no data binding yet — #23 / PR #24)_
- [x] Markdown editor with live preview (editable source + `QTextDocument.setMarkdown()` preview pane) _(done: `ui/editor.py` `MarkdownEditor(QWidget)` — editable `QPlainTextEdit` source beside a read-only `QTextEdit` preview in a non-collapsible `QSplitter`; live `textChanged`→`setMarkdown()` re-render (no Save button); `set_markdown`/`markdown()` seams; replaced the placeholder `editor` in `MainWindow`; 9 headless Qt tests; `core/` stays Qt-free — #25 / PR #26)_
- [x] Auto-save: edits persist (debounced) without an explicit Save button _(done: split across two modules mirroring the vault auto-lock pattern — pure-Python `core/autosave.py` `AutoSaver` debounce/persist policy (injectable clock, dirty-only-on-change, body verbatim + title re-derived via `core.text.derive_title`) driven by `ui/autosave.py` `AutoSaveController` (`source.textChanged`→`edit`, 200ms `QTimer`→`flush_if_due`; binds saver before `set_markdown` so load doesn't spuriously save); `MainWindow.bind_autosave`/`load_note` seams inert until the M4 keyed repository; 22 tests; `core/` stays Qt-free — #27 / PR #28)_
- [x] Tags: assign/remove tags on a note and filter the note list by tag _(done: tag data-access layer in `core/repository.py` over the schema's existing `tags`/`note_tags` tables — frozen `Tag(id, name)` value object (no timestamps); tag CRUD mirroring notebooks (`create_tag`→`IntegrityError` on duplicate name, `get_tag`/`get_tag_by_name`/`list_tags` name COLLATE NOCASE/id, `delete_tag -> bool` with note_tags cascade); note↔tag association (`add_tag_to_note` idempotent via `INSERT OR IGNORE`, `remove_tag_from_note -> bool`, `tags_for_note` ordered by name); `list_notes` refactored into a dynamic WHERE builder so `notebook_id` + a new `tag_id` filter AND together (JOIN `note_tags`, columns table-qualified); 17 new behavioral tests; tag UI deferred to M4; `core/` stays Qt-free — #29 / PR #30)_

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
