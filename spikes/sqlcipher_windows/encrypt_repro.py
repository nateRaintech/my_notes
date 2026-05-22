"""Spike: prove SQLCipher whole-database encryption works on Windows (issue #11).

THROWAWAY repro — not wired into the app. Validates the M2 storage foundation:

  1. The pip-installed `sqlcipher3` wheel actually links a SQLCipher build
     (PRAGMA cipher_version is non-empty), not vanilla SQLite.
  2. An encrypted DB can be created and read back with the correct key, using
     both a passphrase key AND a raw 256-bit hex key (the latter is how the real
     app will hand SQLCipher the Argon2id-derived key).
  3. The on-disk file is genuinely encrypted (header is NOT "SQLite format 3\\0").
  4. A wrong key — and an unkeyed open — fail cleanly, with no partial reads.

Run: python spikes/sqlcipher_windows/encrypt_repro.py
Exit code 0 = all checks passed; non-zero = a check failed (prints which).

NOTE: install the cross-platform binary wheel as `sqlcipher3` (>=0.6.2), NOT
`sqlcipher3-binary` — the latter ships Linux-only wheels (see the spike README).
"""

from __future__ import annotations

import os
import secrets
import sys
import tempfile

from sqlcipher3 import dbapi2 as sqlcipher

PASSPHRASE = "correct horse battery staple"
WRONG_PASSPHRASE = "Tr0ub4dor&3"
SQLITE_PLAINTEXT_MAGIC = b"SQLite format 3\x00"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def key_pragma_value(key: str, *, raw_hex: bool) -> str:
    """SQLCipher key spec. Raw keys use the x'..' form so SQLCipher skips its
    own KDF and uses the bytes directly (what the app does with the Argon2 key).
    """
    return f"\"x'{key}'\"" if raw_hex else f"'{key}'"


def create_encrypted_db(path: str, key: str, *, raw_hex: bool) -> None:
    conn = sqlcipher.connect(path)
    try:
        conn.execute(f"PRAGMA key = {key_pragma_value(key, raw_hex=raw_hex)}")
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
        conn.execute("INSERT INTO notes (body) VALUES (?)", ("secret note",))
        conn.commit()
    finally:
        conn.close()


def read_with_key(path: str, key: str, *, raw_hex: bool) -> list[tuple]:
    """Open, key, and read. Raises if the key is wrong (the SELECT is what
    forces SQLCipher to decrypt page 1 — an unkeyed/badly-keyed handle only
    errors when it first touches the database, never returning partial rows).
    """
    conn = sqlcipher.connect(path)
    try:
        conn.execute(f"PRAGMA key = {key_pragma_value(key, raw_hex=raw_hex)}")
        return conn.execute("SELECT body FROM notes").fetchall()
    finally:
        conn.close()


def expect_open_failure(path: str, key: str | None, *, raw_hex: bool = False) -> bool:
    """Return True iff the open fails (no rows leaked). key=None => unkeyed open."""
    conn = sqlcipher.connect(path)
    try:
        if key is not None:
            conn.execute(f"PRAGMA key = {key_pragma_value(key, raw_hex=raw_hex)}")
        rows = conn.execute("SELECT body FROM notes").fetchall()
        # Should not get here — reaching a result set means the wrong key "worked".
        print(f"      (leaked {len(rows)} row(s) with bad/no key!)")
        return False
    except sqlcipher.DatabaseError as exc:
        print(f"      (rejected as expected: {exc})")
        return True
    finally:
        conn.close()


def scenario(label: str, key: str, *, raw_hex: bool) -> None:
    print(f"\n{label}")
    tmp = tempfile.mkdtemp(prefix="sqlcipher_spike_")
    path = os.path.join(tmp, "vault.db")

    create_encrypted_db(path, key, raw_hex=raw_hex)

    with open(path, "rb") as fh:
        header = fh.read(16)
    check(
        "on-disk file is encrypted (no plaintext SQLite header)",
        header != SQLITE_PLAINTEXT_MAGIC,
        f"first bytes: {header!r}",
    )

    rows = read_with_key(path, key, raw_hex=raw_hex)
    check("correct key reads the row back", rows == [("secret note",)], f"rows={rows}")

    wrong = WRONG_PASSPHRASE if not raw_hex else secrets.token_hex(32)
    check(
        "wrong key fails cleanly (no partial read)",
        expect_open_failure(path, wrong, raw_hex=raw_hex),
    )
    check(
        "unkeyed open fails cleanly",
        expect_open_failure(path, None),
    )


def main() -> int:
    print(f"sqlcipher3 version : {sqlcipher.version}")  # the python binding version
    probe = sqlcipher.connect(":memory:")
    cipher_version = probe.execute("PRAGMA cipher_version").fetchone()
    probe.close()
    print(f"PRAGMA cipher_version: {cipher_version}")
    check(
        "wheel links a real SQLCipher build (cipher_version present)",
        bool(cipher_version) and bool(cipher_version[0]),
        str(cipher_version),
    )

    scenario("Scenario A — passphrase key (SQLCipher runs its own KDF)",
             PASSPHRASE, raw_hex=False)
    scenario("Scenario B — raw 256-bit hex key (app hands SQLCipher the Argon2 key)",
             secrets.token_hex(32), raw_hex=True)

    print("\n" + "=" * 60)
    if failures:
        print(f"RESULT: FAIL ({len(failures)} check(s) failed): {failures}")
        return 1
    print("RESULT: PASS — SQLCipher encryption verified on Windows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
