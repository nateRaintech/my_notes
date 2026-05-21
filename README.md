# my_notes

A local-first, **encrypted** Markdown notes app for the desktop. KeePass-style: you
set a master password, and your notes live in a single encrypted vault file that is
only ever decrypted in memory.

This is a clean-room rebuild — a previous tkinter prototype informs the design only;
no code is carried over.

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

## Development

```powershell
# From the project root, in the project's Python environment:
pip install -r requirements.txt

# Lint + test
ruff check .
pytest
```

> Detailed run/build instructions land as the app takes shape (ROADMAP M1).

## How this project is run

This repo is driven by an autonomous development loop. See:

- **`ROADMAP.md`** — the north star; milestones decomposed into issues.
- **`autodev.md`** / **`autodev.py`** — the autonomous build loop.
- **`github.md`** — the mandatory git/GitHub workflow (board-driven).
- **`LESSONS.md`** — accumulated cross-session knowledge.
- **`CLAUDE.md`** — project instructions for the coding assistant.
