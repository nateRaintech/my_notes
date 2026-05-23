# my_notes

A local-first, **encrypted** Markdown notes app for the desktop. KeePass-style: you
set a master password, and your notes live in a single encrypted vault file that is
only ever decrypted in memory.

This is a clean-room rebuild — a previous tkinter prototype informs the design only;
no code is carried over.

## What it does

- **Encrypted at rest.** The whole database is encrypted with SQLCipher; your master
  password is stretched into the encryption key with Argon2id. **There is no password
  recovery — by design.** Forget the master password and the notes are unrecoverable
  (the KeePass guarantee, not a gap).
- **Create / unlock flow at launch.** First run creates a vault and sets the master
  password; later runs prompt to unlock it.
- **Three-pane window** — a notebooks tree, a searchable note list, and a Markdown
  editor with **live preview**. Edits **auto-save** (debounced); there is no Save button.
- **Full-text search** across all notes (SQLite FTS5), plus a **Ctrl+P quick-switcher**
  that jumps to any note by fuzzy title match.
- **Notebooks** for organization — create, rename, delete, nest, and move notes between
  them.
- **Import** your old notes from a legacy `notes.db` via a guided wizard.
- **Auto-lock** — the vault locks and re-prompts for the master password after a
  configurable idle timeout, and optionally when the window is minimized.
- **Polish** — an optional dark theme, keyboard-first navigation, a live word count in
  the status bar, and a Settings dialog.
- **Shippable** — packages to a single-file Windows `.exe` with PyInstaller.

AI features are intentionally out of scope for this version — the goal is to make core
note-taking excellent.

> **Current limitations (honest status).** There is **no "new note" command in the UI
> yet** — a freshly created vault is empty, and the way to populate it today is the
> legacy-`notes.db` **import wizard** (File → *Import legacy notes…*). The data model
> also supports **tags**, but they are not yet exposed in the UI. See
> [`ROADMAP.md`](ROADMAP.md) for what's planned next.

## Stack

- [PySide6](https://doc.qt.io/qtforpython/) — Qt 6 GUI (LGPL)
- [`sqlcipher3`](https://pypi.org/project/sqlcipher3/) (`>=0.6.2`) — SQLCipher-backed,
  whole-database encryption. **Not** `sqlcipher3-binary`, which ships Linux-only wheels
  and won't install on the Windows ship target (confirmed by the M2 spike, see
  [`spikes/sqlcipher_windows/`](spikes/sqlcipher_windows/)).
- [`argon2-cffi`](https://pypi.org/project/argon2-cffi/) — Argon2id key derivation for
  the master password
- pytest · ruff · PyInstaller

## Prerequisites

- **Python 3.11+** (the project targets `>=3.11`).
- Developed and shipped on **Windows**; setup, tests, and lint work cross-platform for
  development.

## Setup

From the project root, in a fresh virtual environment:

```powershell
# Windows (PowerShell)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

```bash
# macOS / Linux
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Prebuilt wheels for `sqlcipher3` (Windows/macOS/Linux) and `argon2-cffi` mean no
SQLCipher toolchain or C compiler is needed for a normal install.

## Run

```powershell
python app.py
```

On first launch you'll be asked to **create a vault** and choose a master password.
On later launches you'll be prompted to **unlock** the existing vault. After unlocking,
the main window opens with three panes: notebooks on the left, the note list (with a
search box) in the middle, and the Markdown editor with live preview on the right.

By default the vault lives at `~/.my_notes/notes.vault`; see
[Where your data lives](#where-your-data-lives) to change it.

## Using my_notes

**Create or unlock the vault.** The startup dialog detects whether a vault already
exists. If not, it asks you to set (and confirm) a master password and creates the
encrypted vault. If one exists, it asks for the password to unlock it; a wrong password
is reported inline and you can retry. There is **no password recovery** — keep the
master password safe.

**Write notes.** The right pane is a Markdown editor: type Markdown source on the left
half and see the rendered preview on the right. Changes are **auto-saved** a moment
after you stop typing — there's no Save button. The status bar shows a live word count.

> Because there is no "new note" UI yet, the way to get notes into a fresh vault today
> is the **import wizard** (below). Once notes exist, selecting one in the note list
> opens it in the editor.

**Organize with notebooks.** Right-click in the left pane to create, rename, delete, or
nest notebooks, and to *Move to…* a notebook under another. Right-click a note in the
list to *Move to notebook…*. Selecting a notebook filters the note list to that
notebook; the **All Notes** root shows everything.

**Find notes.** Type in the search box above the note list for full-text search across
note titles and bodies (FTS5). Press **Ctrl+P** for the quick-switcher and start typing
a title to fuzzy-jump to any note.

**Import legacy notes.** File → *Import legacy notes…* opens a wizard: choose a legacy
`notes.db` file, preview what will be imported, then run the import. Each row's
`content` becomes the note body (title derived from the first line), its `category`
becomes a notebook, and its timestamps are preserved. The source file is opened
read-only and never modified.

**Settings.** File → *Settings…* lets you change the **theme**, the **vault file
location**, the **idle-lock timeout**, and whether to **lock when the window is
minimized**. Settings persist to disk (see below) and apply on the next launch (theme
applies immediately).

**Dark theme.** View → *Dark Theme* toggles a dark Qt stylesheet. The choice is
remembered via Settings.

**Auto-lock.** If an idle-lock timeout is configured, the vault automatically locks
after that period of inactivity (key wiped from memory) and re-prompts for the master
password in place. With *lock on minimize* enabled, minimizing the window locks the
vault and you're re-prompted when you restore it.

### Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+P` | Quick-switcher — jump to a note by fuzzy title |
| `Ctrl+F` | Focus the search box |
| `Ctrl+1` | Focus the notebooks tree |
| `Ctrl+2` | Focus the note list |
| `Ctrl+3` | Focus the editor |

### Where your data lives

| What | Default location | Override |
|------|------------------|----------|
| Encrypted vault | `~/.my_notes/notes.vault` | `MY_NOTES_VAULT` env var, or the Settings dialog |
| App settings | `~/.my_notes/settings.json` | `MY_NOTES_SETTINGS` env var |

A small plaintext `notes.vault.meta` sidecar sits next to the vault. It holds the
**non-secret** Argon2id salt and KDF parameters needed to derive the key *before* the
encrypted database can be opened — it contains no note content and no key material.

## Build a standalone Windows executable

The app packages to a single-file Windows `.exe` with PyInstaller, driven by the
checked-in [`my_notes.spec`](my_notes.spec). From the project root, with the deps
installed:

```powershell
pyinstaller my_notes.spec
```

This produces **`dist/my_notes.exe`** — a one-file, windowed (no console) build of
`app.py` that bundles the dark-theme stylesheet (`resources/dark.qss`). The SQLCipher
driver and the Qt plugins are picked up by PyInstaller's bundled hooks automatically,
so no `--hidden-import`/`--collect-binaries` flags are needed (confirmed by the
SQLCipher-on-Windows spike in [`spikes/sqlcipher_windows/`](spikes/sqlcipher_windows/)).

`build/` and `dist/` are git-ignored — only the `.spec` is committed. Run the result by
double-clicking it or from a shell:

```powershell
.\dist\my_notes.exe
```

## Test & lint

```powershell
pytest          # run the test suite
ruff check .    # lint
```

Both are the CI merge gate (see [`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

- The pure-Python `core/` tests run anywhere — they don't need Qt.
- The Qt tests **skip** if PySide6 isn't installed locally. In CI they run headless via
  `QT_QPA_PLATFORM=offscreen` (the workflow installs PySide6 plus the required X/EGL
  system libs), so the UI paths are exercised on every PR.

## Project layout

Strict logic/UI separation (the hard rule from `CLAUDE.md`): **`core/` never imports Qt.**

```
my_notes/
├── app.py        # entry point — create/unlock flow, then shows MainWindow
├── core/         # pure Python, zero Qt, unit-testable
│   ├── crypto.py       # Argon2id KDF — derive the vault key from the master password
│   ├── vault.py        # open/create/unlock the SQLCipher vault; lock & wipe the key
│   ├── schema.py       # table DDL + migrations (notebooks/notes/tags + notes_fts FTS5)
│   ├── repository.py   # CRUD for notes/notebooks/tags + FTS5 search
│   ├── importer.py     # read a legacy notes.db into the encrypted vault
│   ├── notebooks.py    # notebook-tree builder + re-parent cycle guard
│   ├── fuzzy.py        # fuzzy title matching for the quick-switcher
│   ├── settings.py     # persistent app-settings model (load/save JSON)
│   ├── theme.py        # QSS theme loader (Qt-free)
│   ├── autosave.py     # debounce/persist policy for auto-save
│   └── text.py         # title derivation + word count
├── ui/           # PySide6 only
│   ├── main_window.py     # 3-pane shell (notebooks | note list + search | editor)
│   ├── unlock_dialog.py   # create-vault / unlock prompt
│   ├── editor.py          # Markdown editor + live preview
│   ├── quick_switcher.py  # Ctrl+P fuzzy note switcher
│   ├── import_wizard.py   # guided legacy-notes.db import
│   ├── settings_dialog.py # Settings dialog
│   ├── autosave.py        # QTimer-driven auto-save controller
│   └── idle_lock.py        # idle/activity-driven auto-lock controller
├── resources/    # QSS theme (dark.qss)
└── tests/        # pytest suite (core/ unit tests + headless Qt tests)
```

See [`CLAUDE.md`](CLAUDE.md) for the full architecture and design decisions.

## How this project is run

This repo is driven by an autonomous development loop. See:

- **`ROADMAP.md`** — the north star; milestones decomposed into issues.
- **`autodev.md`** / **`autodev.py`** — the autonomous build loop.
- **`github.md`** — the mandatory git/GitHub workflow (board-driven).
- **`LESSONS.md`** — accumulated cross-session knowledge.
- **`CLAUDE.md`** — project instructions for the coding assistant.
