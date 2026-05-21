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

- _(none recorded yet)_

## Gotchas

- 2026-05-21 — The GitHub Project board is **user-owned** (`nateRaintech`), not org-owned. `autodev.py`'s board query uses `user(login: ...)`; the stock template uses `organization(...)`. If board fetches start returning 0 items / "fetch failed", check this first. (Project #6, IDs in `CLAUDE.md`.)
- 2026-05-21 — `gh` is **not on PowerShell's PATH**; it lives at `C:\Users\Nate\bin\gh.exe`. The runner and docs call it by full path. From the Bash tool (git-bash) `gh` does resolve on PATH.
- 2026-05-21 — Unverified risk for M2: SQLCipher (`sqlcipher3-binary`) on Windows + PyInstaller bundling is the project's biggest technical unknown. The first M2 capability is a spike to confirm it; if it's painful, the agreed fallback is field-level AES-GCM (no native dep). Don't build storage on SQLCipher until the spike passes.

## Anti-patterns

- Seed lesson — **Do not write "editorial-pin" tests** that assert a doc/markdown list matches a code constant, AST-presence checks, or set-identity between prose and code. They never catch real bugs; they only fire on intentional human edits, creating a maintenance tax. (This is encoded as a hard rule in `autodev.md` → Priority 5B "Prohibited test patterns"; recorded here so the reasoning travels with the project. If cross-file drift ever causes a *real* bug, fix it with one CI script, not per-pair tests.)
