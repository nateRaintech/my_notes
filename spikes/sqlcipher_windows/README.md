# Spike: SQLCipher on Windows + PyInstaller (issue #11, milestone M2)

**Status: PASS — SQLCipher is viable on the Windows ship target. The AES-GCM
fallback is NOT needed.**

This validates the project's biggest technical risk before any vault/crypto code
is built: that whole-database encryption via SQLCipher (1) opens an encrypted DB
on Windows and (2) survives PyInstaller bundling into a standalone `.exe`.

## Headline finding — use `sqlcipher3`, NOT `sqlcipher3-binary`

`CLAUDE.md`/`requirements.txt` originally specified **`sqlcipher3-binary`**. That
package ships **Linux-only `manylinux` wheels** (verified through 0.6.0) — there is
no Windows or macOS wheel, so `pip install sqlcipher3-binary` fails on Windows with
`Could not find a version that satisfies the requirement ... (from versions: none)`.

The sibling package **`sqlcipher3`** (>= **0.6.2**) now publishes prebuilt binary
wheels for **Windows** (`cp312-cp312-win_amd64`, plus win32/arm64), macOS, manylinux,
and musllinux. It used to be sdist-only (needed a C compiler + the SQLCipher
amalgamation), which is why `-binary` was originally chosen — that has since flipped.
**The fix is a one-line dependency swap: `sqlcipher3-binary` → `sqlcipher3>=0.6.2`.**

Confirmed on this machine: Python 3.12 / Windows, `sqlcipher3` 0.6.2 wheel installs
in seconds and links **SQLCipher 4.12.0 community** (`PRAGMA cipher_version`).

## What the spike proves

`encrypt_repro.py` (run directly with Python):
- Creates an encrypted DB and reads it back with the correct key, using **both** a
  passphrase key and a **raw 256-bit hex key** (`PRAGMA key = "x'<64 hex>'"`) — the
  raw form is how the real app will hand SQLCipher the Argon2id-derived key.
- Verifies the on-disk file is genuinely encrypted (header is random, not the
  plaintext `SQLite format 3\0` magic).
- Verifies a **wrong key** and an **unkeyed open** both fail cleanly with
  `DatabaseError: file is not a database` — no partial reads, no plaintext leak.
  (SQLCipher logs `hmac check failed for pgno=1` to stderr on those rejections;
  that's the page-level HMAC integrity check doing its job, not an error in the test.)

`pyinstaller_probe.py` (built into a one-file exe):
- A `--onefile` PyInstaller build runs as a standalone 10 MB `.exe`, reports
  `frozen=True`, and opens/reads an encrypted SQLCipher DB. **No `--hidden-import`
  or `--collect-binaries` flags were needed** — PyInstaller's bundled hooks pick up
  the `sqlcipher3` C extension and its DLL automatically.

## Reproduce

From the repo root, with the project env:

```powershell
python -m pip install "sqlcipher3>=0.6.2" pyinstaller

# 1. Encryption behaviour (exit 0 = all checks pass)
python spikes/sqlcipher_windows/encrypt_repro.py

# 2. PyInstaller bundling (exit 0 = frozen exe opened an encrypted DB)
python -m PyInstaller --onefile --noconfirm `
  --distpath spikes/sqlcipher_windows/dist `
  --workpath spikes/sqlcipher_windows/build `
  --specpath spikes/sqlcipher_windows `
  spikes/sqlcipher_windows/pyinstaller_probe.py
spikes/sqlcipher_windows/dist/pyinstaller_probe.exe
```

`build/` and `dist/` are git-ignored; delete them (and the generated `.spec`) when
done — this is throwaway repro code, not part of the app.

## Implications for M2

- Storage can be built on SQLCipher as designed. **`core/vault.py`** opens the vault
  with `from sqlcipher3 import dbapi2 as sqlite` and keys it via `PRAGMA key` before
  the first query (the first DB access is what triggers a wrong-key failure).
- Hand SQLCipher the Argon2id key as a **raw hex key** (`x'...'`) so it skips its own
  KDF — `core/crypto.py` owns the KDF.
- Bring `sqlcipher3` (and `argon2-cffi`) into CI only when a real test imports them
  (i.e. with the `core/crypto.py` / `core/vault.py` capabilities); on `ubuntu-latest`,
  `sqlcipher3>=0.6.2` resolves to a manylinux wheel.
