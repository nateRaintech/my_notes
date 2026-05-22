"""Spike: prove a PyInstaller-frozen exe can still open a SQLCipher DB (issue #11).

THROWAWAY entry point used only to build a one-file .exe and confirm the native
SQLCipher extension (sqlcipher3 + its bundled DLL) survives freezing. The usual
PyInstaller failure mode for native deps is a missing extension module / DLL at
runtime, so this exercises the full create -> encrypt -> read-back path inside
the frozen binary.

Build & run (from the repo root):
    python -m PyInstaller --onefile --noconfirm \
        --distpath spikes/sqlcipher_windows/dist \
        --workpath spikes/sqlcipher_windows/build \
        --specpath spikes/sqlcipher_windows \
        spikes/sqlcipher_windows/pyinstaller_probe.py
    spikes/sqlcipher_windows/dist/pyinstaller_probe.exe

Exit code 0 = the frozen exe opened an encrypted DB; non-zero = it failed.
"""

from __future__ import annotations

import os
import sys
import tempfile

from sqlcipher3 import dbapi2 as sqlcipher

KEY = "frozen-binary-spike-key"
SQLITE_PLAINTEXT_MAGIC = b"SQLite format 3\x00"


def main() -> int:
    frozen = getattr(sys, "frozen", False)
    print(f"frozen={frozen}  sqlcipher3={sqlcipher.version}")
    cipher_version = sqlcipher.connect(":memory:").execute(
        "PRAGMA cipher_version"
    ).fetchone()
    print(f"PRAGMA cipher_version={cipher_version}")
    if not (cipher_version and cipher_version[0]):
        print("RESULT: FAIL — no SQLCipher build linked in the frozen exe")
        return 1

    tmp = tempfile.mkdtemp(prefix="pyi_sqlcipher_")
    path = os.path.join(tmp, "vault.db")

    conn = sqlcipher.connect(path)
    conn.execute(f"PRAGMA key = '{KEY}'")
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    conn.execute("INSERT INTO notes (body) VALUES (?)", ("frozen secret",))
    conn.commit()
    conn.close()

    with open(path, "rb") as fh:
        encrypted = fh.read(16) != SQLITE_PLAINTEXT_MAGIC

    conn = sqlcipher.connect(path)
    conn.execute(f"PRAGMA key = '{KEY}'")
    rows = conn.execute("SELECT body FROM notes").fetchall()
    conn.close()

    ok = encrypted and rows == [("frozen secret",)]
    print(f"encrypted_on_disk={encrypted}  readback={rows}")
    print("RESULT:", "PASS — frozen exe opened an encrypted SQLCipher DB" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
