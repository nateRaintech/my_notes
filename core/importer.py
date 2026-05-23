"""Import a legacy ``notes.db`` into the encrypted vault.

The old tkinter prototype stored notes in a **plain, unencrypted SQLite** file
(CLAUDE.md: columns ``content``, ``format``, ``category``, and a timestamp). This
module is the Qt-free *engine* that reads that file and writes its notes into the
current encrypted vault through :class:`~core.repository.Repository`. The wizard
UI that drives it (file picker, preview, progress) is a separate ``ui/`` slice;
this layer is pure logic so it unit-tests without Qt or a real vault.

We do not have the prototype's exact DDL, so the reader is **defensive and
auto-detecting** rather than hard-coded to one schema:

* It finds the notes table by name (``notes`` by default, else the lone user
  table) instead of assuming one.
* It matches columns **case-insensitively** and treats everything but ``content``
  as optional, auto-detecting the timestamp column from a list of common names.
* A missing ``content`` column (the one thing an import can't do without) raises
  :class:`LegacyDatabaseError` with a clear message rather than failing obscurely.

Mapping (``read_legacy_notes`` parses; ``import_legacy_notes`` writes):

* ``content`` -> note **body** (verbatim); the **title** is derived from it via
  :func:`core.text.derive_title`, exactly as the rest of the app labels notes.
* ``category`` -> a **notebook**: one notebook per distinct non-empty category,
  get-or-created and reused (no duplicates); blank/absent category -> the root.
* the timestamp -> the note's ``created_at`` / ``updated_at``, normalized to the
  vault's ``YYYY-MM-DD HH:MM:SS`` form (numeric Unix epochs are converted; values
  that already look like datetime strings pass through; blank/absent -> the DB
  default ``datetime('now')``).
* ``format`` is captured on :class:`LegacyNote` (so the wizard can surface it) but
  not acted on here — the body is stored verbatim; format conversion is out of
  scope for the engine.

Imported notes become searchable automatically: the schema's ``notes_ai`` trigger
fires on every ``notes`` insert and keeps the FTS index in sync.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.text import derive_title

if TYPE_CHECKING:
    from os import PathLike

    from core.repository import Repository


class LegacyDatabaseError(Exception):
    """The legacy database can't be read or lacks the data an import needs.

    Raised for a missing file, a file that isn't a usable SQLite database, an
    ambiguous/absent notes table, or a notes table with no ``content`` column.
    Named to avoid shadowing the built-in :class:`ImportError`.
    """


# The default notes table name, and the optional columns we auto-detect. The
# timestamp tuple is searched in order; the first present column wins.
_DEFAULT_TABLE = "notes"
_TIMESTAMP_COLUMNS = (
    "updated_at",
    "modified",
    "modified_at",
    "timestamp",
    "created_at",
    "created",
    "date",
)
_VAULT_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class LegacyNote:
    """A normalized row parsed from a legacy ``notes.db``.

    ``content`` is the raw note text; the others are ``None`` when the source
    column is absent or NULL. ``timestamp`` is the raw legacy value coerced to a
    string (e.g. ``"2020-01-01 09:30:00"`` or an epoch like ``"1577871000"``) —
    :func:`import_legacy_notes` normalizes it to the vault's datetime form when
    writing, so a hand-built :class:`LegacyNote` can carry either.
    """

    content: str
    category: str | None = None
    format: str | None = None
    timestamp: str | None = None


@dataclass(frozen=True)
class ImportResult:
    """A summary of one import run, for the wizard to report to the user."""

    notes_imported: int
    notebooks_created: int
    rows_skipped: int


def _quote_ident(name: str) -> str:
    """Quote a SQLite identifier (table/column) by doubling embedded quotes.

    Used for names that come from the database's own catalog (validated against
    the live table list), not from user query input — but quoting keeps names with
    spaces or reserved words valid and is defensive regardless.
    """
    return '"' + name.replace('"', '""') + '"'


def _coerce_text(value: Any) -> str | None:
    """Coerce a raw SQLite cell to text, or ``None`` if the cell was NULL.

    ``bytes`` (a BLOB column) is decoded as UTF-8 with replacement so a stray
    byte never aborts an import; other non-text values are stringified.
    """
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _normalize_timestamp(value: str | None) -> str | None:
    """Normalize a legacy timestamp to the vault's ``YYYY-MM-DD HH:MM:SS`` form.

    ``None`` / blank -> ``None`` (the note then takes the DB default). An all-digit
    value is treated as a Unix epoch in **seconds** and converted to a UTC datetime
    string. Anything else is assumed to already be a datetime string and passed
    through trimmed — so ISO/SQLite timestamps survive unchanged.
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.isdigit():
        try:
            dt = datetime.fromtimestamp(int(text), tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return text
        return dt.strftime(_VAULT_TIMESTAMP_FORMAT)
    return text


def _resolve_table(conn: sqlite3.Connection, table: str | None) -> str:
    """Find the table to import from, or raise :class:`LegacyDatabaseError`."""
    names = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
    ]
    if table is not None:
        if table not in names:
            raise LegacyDatabaseError(
                f"table {table!r} not found in legacy database; tables present: {names}"
            )
        return table
    if _DEFAULT_TABLE in names:
        return _DEFAULT_TABLE
    if len(names) == 1:
        return names[0]
    raise LegacyDatabaseError(
        "could not determine which table holds the notes "
        f"(no {_DEFAULT_TABLE!r} table and {len(names)} candidates: {names}); "
        "specify one with table=..."
    )


def read_legacy_notes(
    path: str | PathLike[str], *, table: str | None = None
) -> list[LegacyNote]:
    """Read a legacy ``notes.db`` and return its rows as :class:`LegacyNote`s.

    Opens ``path`` **read-only** (it's a plain, unencrypted SQLite file — stdlib
    :mod:`sqlite3`, not ``sqlcipher3``) and never writes to it. ``table`` overrides
    the auto-detected notes table.

    Raises :class:`LegacyDatabaseError` if the file is missing, isn't a usable
    SQLite database, has no resolvable notes table, or that table has no
    ``content`` column. Optional columns (``category``, ``format``, a timestamp)
    are matched case-insensitively and default to ``None`` when absent.
    """
    p = Path(path)
    if not p.is_file():
        raise LegacyDatabaseError(f"legacy database not found: {p}")

    # file: URI with mode=ro opens the existing file read-only and refuses to
    # create one; as_uri() handles absolute Windows paths and percent-encoding.
    uri = f"{p.resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError as exc:  # pragma: no cover - rare open failure
        raise LegacyDatabaseError(f"could not open legacy database {p}: {exc}") from exc

    try:
        try:
            tbl = _resolve_table(conn, table)
            columns = [
                row[1]
                for row in conn.execute(
                    f"PRAGMA table_info({_quote_ident(tbl)})"
                ).fetchall()
            ]
        except sqlite3.DatabaseError as exc:
            raise LegacyDatabaseError(
                f"{p} is not a readable SQLite database: {exc}"
            ) from exc

        by_lower = {col.lower(): col for col in columns}
        if "content" not in by_lower:
            raise LegacyDatabaseError(
                f"table {tbl!r} has no 'content' column (columns: {columns}); "
                "cannot import note bodies"
            )

        content_col = by_lower["content"]
        category_col = by_lower.get("category")
        format_col = by_lower.get("format")
        ts_col = next(
            (by_lower[name] for name in _TIMESTAMP_COLUMNS if name in by_lower), None
        )

        selected = [content_col]
        positions: dict[str, int] = {"content": 0}
        for field, col in (
            ("category", category_col),
            ("format", format_col),
            ("timestamp", ts_col),
        ):
            if col is not None:
                positions[field] = len(selected)
                selected.append(col)

        query = (
            f"SELECT {', '.join(_quote_ident(c) for c in selected)} "
            f"FROM {_quote_ident(tbl)}"
        )
        rows = conn.execute(query).fetchall()
    finally:
        conn.close()

    def cell(row: tuple[Any, ...], field: str) -> str | None:
        pos = positions.get(field)
        return _coerce_text(row[pos]) if pos is not None else None

    return [
        LegacyNote(
            content=cell(row, "content") or "",
            category=cell(row, "category"),
            format=cell(row, "format"),
            timestamp=cell(row, "timestamp"),
        )
        for row in rows
    ]


def import_legacy_notes(
    repository: Repository, notes: Iterable[LegacyNote]
) -> ImportResult:
    """Write parsed :class:`LegacyNote`s into the vault via ``repository``.

    For each note: a blank/whitespace-only body is skipped (and counted); a
    non-empty ``category`` maps to a notebook (get-or-created and reused, so the
    same category never makes two notebooks) while a blank/absent category lands at
    the root; the body is stored verbatim with a title derived from it; the
    timestamp is preserved (normalized) on both ``created_at`` and ``updated_at``.

    Returns an :class:`ImportResult` with the counts. Existing notebooks are reused
    by exact name, so importing into a non-empty vault won't duplicate them.
    """
    notebooks_by_name = {nb.name: nb.id for nb in repository.list_notebooks()}
    notes_imported = 0
    notebooks_created = 0
    rows_skipped = 0

    for note in notes:
        body = note.content or ""
        if not body.strip():
            rows_skipped += 1
            continue

        notebook_id: int | None = None
        category = (note.category or "").strip()
        if category:
            existing = notebooks_by_name.get(category)
            if existing is None:
                created = repository.create_notebook(category)
                notebooks_by_name[category] = created.id
                notebooks_created += 1
                notebook_id = created.id
            else:
                notebook_id = existing

        timestamp = _normalize_timestamp(note.timestamp)
        repository.create_note(
            notebook_id=notebook_id,
            title=derive_title(body),
            body=body,
            created_at=timestamp,
            updated_at=timestamp,
        )
        notes_imported += 1

    return ImportResult(
        notes_imported=notes_imported,
        notebooks_created=notebooks_created,
        rows_skipped=rows_skipped,
    )


def import_legacy_db(
    repository: Repository, path: str | PathLike[str], *, table: str | None = None
) -> ImportResult:
    """Read a legacy ``notes.db`` at ``path`` and import it into the vault.

    Convenience wrapper over :func:`read_legacy_notes` + :func:`import_legacy_notes`
    — the single call the wizard UI makes once the user picks a file.
    """
    return import_legacy_notes(repository, read_legacy_notes(path, table=table))
