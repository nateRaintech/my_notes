# LESSONS.md — Accumulated Project Memory

This is autodev's **compounding memory**. Every session is a cold start with no recollection of previous runs — this file is how hard-won knowledge survives across sessions so the framework gets *smarter* over time instead of relearning (or repeating) the same mistakes.

**Read this at session startup. Append to it at wrap-up when you learn something reusable.** Unlike `autodev_log.md` (local-only, per-session noise), `LESSONS.md` is committed and shared — keep it concise and high-signal.

## What belongs here

- **Conventions** — project-specific patterns not obvious from a quick read (naming, layering, how config/secrets are loaded, where to put X).
- **Gotchas** — non-obvious traps: flaky tests, slow steps to avoid, environment quirks, commands that need special flags.
- **Anti-patterns** — approaches that wasted a session or got bounced. Record what *not* to do and why, so no future session retries it.

## What does NOT belong here

- Per-session status (that's `autodev_log.md`).
- Things already documented in `CLAUDE.md` or `github.md` — link to those instead of duplicating.
- Speculation. Only record something once a session has actually confirmed it.

## Format

One dated bullet per lesson, newest at the top of its section. Keep each to a sentence or two.

```
- YYYY-MM-DD — <the lesson>. (why it matters / what triggered it)
```

---

## Conventions

- 2026-05-22 — **Argon2id KDF lives in `core/crypto.py`; derive raw key bytes with the *low-level* API.** Use `argon2.low_level.hash_secret_raw(secret=pw.encode("utf-8"), salt=..., hash_len=32, type=Type.ID)` — NOT the high-level `argon2.PasswordHasher`, which emits encoded *verification* hashes, not raw key material. `derive_key()` returns 32 raw bytes; `core/vault.py` will hand `key.hex()` to SQLCipher as the raw-hex key (ties into the SQLCipher bullet below). Cost is tunable via the frozen `KdfParams` dataclass (`memory_cost` is in **KiB**), defaults at/above the OWASP floor (64 MiB / t=3 / p=4) — store the params per-vault so unlock re-derives identically. `derive_key` is a **total function over its byte inputs**: password policy (non-empty, strength) is the unlock dialog's job, not the KDF's. argon2 rejects salts < 8 bytes (we generate 16). `argon2-cffi` is now installed in CI (a dep enters CI only once a real test imports it). (issue #13 / PR #14.)
- 2026-05-22 — **SQLCipher storage uses the `sqlcipher3` package (`>=0.6.2`), NOT `sqlcipher3-binary`.** The M2 spike (#11) found `sqlcipher3-binary` ships **Linux-only manylinux wheels** (through 0.6.0) — `pip install sqlcipher3-binary` fails on the Windows ship target with "Could not find a version that satisfies the requirement … (from versions: none)". The sibling package **`sqlcipher3` >= 0.6.2** publishes prebuilt **Windows** wheels (`cp312-cp312-win_amd64`) and links **SQLCipher 4.12.0 community**. Open with `from sqlcipher3 import dbapi2 as sqlite`, then `PRAGMA key` before the first query; hand it the Argon2id key as a **raw hex key** (`PRAGMA key = "x'<64 hex>'"`) so SQLCipher skips its own KDF (crypto.py owns the KDF). A wrong/absent key fails on first DB access with `DatabaseError: file is not a database` — clean, no partial read (SQLCipher logs `hmac check failed for pgno=1` to stderr on those, which is the page HMAC integrity check working, not a bug). A PyInstaller `--onefile` build opens an encrypted DB with **no `--hidden-import`/`--collect-binaries` flags** — the bundled hooks pick up the extension + DLL. `requirements.txt` + `CLAUDE.md` updated to match; repro in `spikes/sqlcipher_windows/`. **The biggest project risk is resolved — the AES-GCM fallback is NOT needed.** (issue #11.)
- 2026-05-22 — **CI runs the full suite headless, including Qt.** `.github/workflows/ci.yml` installs `PySide6` plus the apt libs `libegl1 libgl1 libxkbcommon0 libdbus-1-3` and sets `QT_QPA_PLATFORM=offscreen`, so `tests/test_app_launch.py` actually runs (not skips) on `ubuntu-latest` — confirmed green (PR #8, run in 26s). The Test step is now plain `pytest -q`; the old exit-5 "no tests collected" crutch is gone, which is safe because `tests/test_text.py` provides real collected tests. `sqlcipher3-binary`/`argon2-cffi` are still **not** installed in CI — keep them out until the M2 SQLCipher spike. (issue #7 / PR #8.)
- 2026-05-21 — **Real (CI-running) tests target the pure-Python `core/` layer with no `importorskip`** — they execute in CI even though PySide6 isn't installed, so `pytest` exits 0 on real assertions instead of relying on the exit-5 "no tests collected" crutch. Qt-dependent tests stay `importorskip`-guarded smoke tests. The first such test is `tests/test_text.py` against `core/text.derive_title`. (issue #3 / PR #4.)
- 2026-05-21 — Entry point is `app.py` (a `main()` that builds the `QApplication`, shows `MainWindow`, runs the event loop); Qt-facing code lives in `ui/` (`ui/main_window.py` holds `MainWindow`). `core/` must never import Qt. (Established with M1 "app launches", issue #1 / PR #2.)
- 2026-05-21 — Tests import `app`/`ui` via `pythonpath = ["."]` in `[tool.pytest.ini_options]` — `tests/` has no `__init__.py`, so without this `import ui` fails under pytest's prepend import mode.

## Gotchas

- 2026-05-21 (updated 2026-05-22) — Qt/PySide6 tests guard with `pytest.importorskip("PySide6")` and run headless via `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` set *before* any Qt import; the guarded imports need `# noqa: E402` (ruff `E4` selects E402). **`importorskip` only guards the *import*** — once PySide6 is actually installed (as it now is in CI, see Conventions), a *missing Qt system lib* makes `QApplication()` abort the process (uncatchable — not a Python exception, not a skip), so the apt libs are mandatory, not optional. The old exit-5 = pass crutch is retired now that a real collected test exists. PySide6 6.11.1 installs cleanly on Windows and on `ubuntu-latest` CI. (issue #1 / PR #2; CI wired in #7 / #8.)
- 2026-05-21 — The GitHub Project board is **user-owned** (`nateRaintech`), not org-owned. `autodev.py`'s board query uses `user(login: ...)`; the stock template uses `organization(...)`. If board fetches start returning 0 items / "fetch failed", check this first. (Project #6, IDs in `CLAUDE.md`.)
- 2026-05-21 — `gh` is **not on PowerShell's PATH**; it lives at `C:\Users\Nate\bin\gh.exe`. The runner and docs call it by full path. From the Bash tool (git-bash) `gh` does resolve on PATH.
- 2026-05-21 (RESOLVED 2026-05-22, spike #11) — SQLCipher on Windows + PyInstaller bundling *was* the project's biggest technical unknown. The M2 spike confirmed it works — but via the **`sqlcipher3>=0.6.2`** wheel, not the documented `sqlcipher3-binary` (which is Linux-only). See the Conventions bullet above for the full finding. Storage can now be built on SQLCipher; the AES-GCM fallback is not needed.

## Anti-patterns

- Seed lesson — **Do not write "editorial-pin" tests** that assert a doc/markdown list matches a code constant, AST-presence checks, or set-identity between prose and code. They never catch real bugs; they only fire on intentional human edits, creating a maintenance tax. (This is encoded as a hard rule in `autodev.md` → Priority 5B "Prohibited test patterns"; recorded here so the reasoning travels with the project. If cross-file drift ever causes a *real* bug, fix it with one CI script, not per-pair tests.)
