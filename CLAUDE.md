# my_notes — Project Instructions

## What this is

A local-first, **encrypted** Markdown notes desktop app. KeePass-style: the user sets a
**master password**, and notes live in a single encrypted vault file that is only ever
decrypted in memory.

This is a **clean-room rebuild**. An earlier tkinter prototype (in a separate conda env)
informs the design only — **no code is carried over**. **No AI features** in this version;
the goal is to make core note-taking excellent.

### Product decisions (locked)

- **Encryption at rest:** whole-database encryption via **SQLCipher** (`sqlcipher3-binary`).
  The master password is run through **Argon2id** to derive a 256-bit key handed to SQLCipher.
- **No password recovery — by design.** Forgetting the master password means the notes are
  unrecoverable. This is the KeePass guarantee, not a gap.
- **Editor:** Markdown source with **live preview** (Qt-native `QTextDocument.setMarkdown()`,
  no heavy WebEngine).
- **Organization:** **notebooks/folders** for primary structure + **tags** for cross-cutting labels.
- **Import:** a wizard that reads a legacy `notes.db` (`content`, `format`, `category`,
  timestamp) into the encrypted vault.
- **Ship target:** runs locally, packaged to a Windows `.exe` with **PyInstaller**.

## Architecture — strict logic/UI separation

The hard rule (learned from the prototype's 5,800-line god-class): **the core never imports Qt.**

```
my_notes/
├── app.py                  # entry point
├── core/                   # pure Python — zero Qt, unit-testable
│   ├── crypto.py           # Argon2id KDF, key lifecycle (derive, hold, wipe)
│   ├── vault.py            # open/create/unlock SQLCipher DB, lock & zero key
│   ├── repository.py       # CRUD: notes, notebooks, tags + FTS5 search
│   ├── importer.py         # legacy notes.db -> encrypted vault
│   └── schema.py           # table DDL + migrations
├── ui/                     # PySide6 only
│   ├── unlock_dialog.py    # create-vault / unlock prompt
│   ├── main_window.py      # 3-pane shell (notebooks/tags | note list | editor)
│   ├── editor.py           # Markdown editor + live preview
│   └── import_wizard.py
└── resources/              # QSS theme, icons
```

Data model (created on first vault open): `notebooks`, `notes`, `tags`, `note_tags`,
and a `notes_fts` FTS5 table for full-text search.

## Stack & Commands

- **GUI:** PySide6 (Qt 6, LGPL) · **Storage:** `sqlcipher3-binary` · **KDF:** `argon2-cffi`
- **Dev:** pytest · ruff · PyInstaller

```powershell
pip install -r requirements.txt   # install deps into the project's Python env
python app.py                     # run the app (entry point lands in M1)
pytest                            # run tests  (CI gate)
ruff check .                      # lint       (CI gate)
```

- **Test command (for CI and reviews):** `pytest`
- **Lint command:** `ruff check .`
- Lint/test config is in `pyproject.toml`.

> **Biggest technical risk (validate first):** SQLCipher on Windows + PyInstaller bundling.
> The first M2 capability is a spike to confirm it. If it proves painful, the agreed fallback
> is field-level AES-GCM encryption (no native dependency). See `LESSONS.md`.

---

## GitHub Workflow — MANDATORY FOR ALL WORK

**CRITICAL: The workflow in `github.md` starts BEFORE you write any code.** When asked to
build, fix, or change anything, your FIRST action — before reading source files, before
planning, before writing a single line — is to follow the task-start checklist in `github.md`
("Board Sync" section):

1. `gh issue list` / `gh pr list` to check current state
2. Create or find a GitHub Issue for the work
3. Add it to the project board → move to **In Progress**
4. Create a branch from latest `main` referencing the issue number

Only THEN do you start coding. The board must reflect reality at all times.

All git and GitHub interactions must follow the standards in `github.md`. Read and obey
`github.md` before any git operation — commits, branching, PRs, issue management — every
time, even for shorthand instructions like "commit this" or "push it up."

For one-time setup commands (CLI install, project creation, GraphQL reference), see `github_setup.md`.

## Autodevelopment Framework

This project runs an autonomous development loop (`autodev.py` + `autodev.md`). These rules
apply to **all** development, not just unattended runs:

- **`ROADMAP.md`** — the source of direction. Milestones (mirrored as GitHub Milestones) are
  decomposed into issues when the board is dry. To steer long-horizon work, edit this file.
  Keep capability checkboxes current as issues merge.
- **`LESSONS.md`** — committed, cross-session memory. Read it before starting work; append a
  line when you learn a reusable convention, gotcha, or anti-pattern.
- **CI is the merge gate.** Never merge a PR unless CI is green. A red default branch is the
  top priority to fix (Priority 0). CI lives in `.github/workflows/`.
- **Runner modes:** `python autodev.py --always` runs forever (polls hourly when idle);
  `--workers N` runs N parallel build sessions in git worktrees plus a serial integration/merge
  session. `board_snapshot.md` (git-ignored) is written each session for a cheaper cold start.

See `autodev.md` for the full priority loop and execution modes.

## GitHub Project Board IDs

These IDs let Claude Code sync the board during work sessions without re-querying. See
`github.md` → "Board Sync" for the workflow. **Note: this board is user-owned, so GraphQL
queries use `user(login: ...)`, not `organization(...)`.**

```
OWNER:             nateRaintech
OWNER_TYPE:        User
REPO:              my_notes
PROJECT_NUMBER:    6
PROJECT_URL:       https://github.com/users/nateRaintech/projects/6
PROJECT_ID:        PVT_kwHODNNZlM4BYaSW
STATUS_FIELD_ID:   PVTSSF_lAHODNNZlM4BYaSWzhTgWZw

# Column option IDs for gh project item-edit --single-select-option-id
BACKLOG:           83a06085
TODO:              8b155366
IN_PROGRESS:       873b5322
IN_REVIEW:         41e378cb
BLOCKED:           13320c83
DONE:              359ab011
```

## Environment paths (this machine)

```
gh CLI:   C:\Users\Nate\bin\gh.exe        (not on PowerShell PATH; on PATH in git-bash)
Python:   C:\Users\Nate\anaconda3\envs\playground\python.exe
Project:  C:\Users\Nate\anaconda3\envs\playground\my_notes
```
