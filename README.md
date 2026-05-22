# my_notes

A local-first, **encrypted** Markdown notes app for the desktop. KeePass-style: you
set a master password, and your notes live in a single encrypted vault file that is
only ever decrypted in memory.

This is a clean-room rebuild — a previous tkinter prototype informs the design only;
no code is carried over.

> **Project status: early foundation.** Today `python app.py` opens a minimal window
> with a placeholder body. The encrypted vault, Markdown editor, search, and import
> are planned but **not built yet** — see [`ROADMAP.md`](ROADMAP.md) for what lands when.

## Goals

- **Encrypted at rest** — whole-database encryption via SQLCipher; master password
  derived to a key with Argon2id. No password recovery, by design.
- **Great Markdown editing** — write in Markdown with live preview.
- **Organized** — notebooks/folders for structure, tags for cross-cutting labels.
- **Importable** — bring notes in from the old `notes.db`.
- **Shippable** — runs locally, packaged to a Windows `.exe` with PyInstaller.

AI features are intentionally out of scope for the first version.

## Stack

- [PySide6](https://doc.qt.io/qtforpython/) — Qt 6 GUI (LGPL)
- [`sqlcipher3-binary`](https://pypi.org/project/sqlcipher3-binary/) — encrypted SQLite
- [`argon2-cffi`](https://pypi.org/project/argon2-cffi/) — master-password KDF
- pytest · ruff · PyInstaller

## Prerequisites

- **Python 3.11+** (the project targets `>=3.11`).
- Developed on **Windows**, but setup/test work cross-platform for development.

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

> **Note on the encryption deps.** `requirements.txt` lists `sqlcipher3-binary` and
> `argon2-cffi`, but they aren't exercised yet — the SQLCipher-on-Windows + PyInstaller
> spike (ROADMAP M2) hasn't run, and CI intentionally skips installing them until it
> passes. If a wheel for either is unavailable on your platform, you can still run the
> app and the test suite with just `PySide6` installed.

## Run

```powershell
python app.py
```

This opens a 1000×700 window titled **my_notes** with a centered placeholder and a
status bar. That's the whole app at this stage — the 3-pane notebooks/notes/editor
layout and the unlock flow arrive in later milestones (ROADMAP M3–M4).

## Test & lint

```powershell
pytest          # run the test suite
ruff check .    # lint
```

Both are the CI merge gate (see [`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

- The pure-Python `core/` tests run anywhere — they don't need Qt.
- The Qt smoke tests (`tests/test_app_launch.py`) **skip** if PySide6 isn't installed
  locally. In CI they run headless via `QT_QPA_PLATFORM=offscreen` (the workflow installs
  PySide6 plus the required X/EGL system libs), so the launch path is exercised on every PR.

## Project layout

Strict logic/UI separation (the hard rule from `CLAUDE.md`): **`core/` never imports Qt.**

```
my_notes/
├── app.py        # entry point — builds the QApplication, shows MainWindow
├── core/         # pure Python, zero Qt, unit-testable (e.g. core/text.py)
├── ui/           # PySide6 only (ui/main_window.py is the app shell)
└── tests/        # pytest suite (core/ unit tests + Qt launch smoke test)
```

See [`CLAUDE.md`](CLAUDE.md) for the full architecture and [`ROADMAP.md`](ROADMAP.md)
for the planned `core/` (crypto, vault, repository, importer) and `ui/` modules.

## How this project is run

This repo is driven by an autonomous development loop. See:

- **`ROADMAP.md`** — the north star; milestones decomposed into issues.
- **`autodev.md`** / **`autodev.py`** — the autonomous build loop.
- **`github.md`** — the mandatory git/GitHub workflow (board-driven).
- **`LESSONS.md`** — accumulated cross-session knowledge.
- **`CLAUDE.md`** — project instructions for the coding assistant.
