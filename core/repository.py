"""CRUD data-access layer for notes and notebooks in the encrypted vault.

This is the typed Python API the UI calls instead of writing SQL: the editor,
note list, and notebook tree all go through :class:`Repository`. It turns the raw
``notes`` / ``notebooks`` / ``tags`` tables created by :mod:`core.schema` into
immutable :class:`Note` / :class:`Notebook` / :class:`Tag` value objects.

Pure Python, no Qt: ``core/`` is the unit-testable layer (CLAUDE.md). Like
:func:`core.schema.migrate`, :class:`Repository` takes any DB-API connection
rather than the :class:`~core.vault.Vault` itself, so it exercises the exact
SQLite/FTS5 build the real vault uses while unit-testing against an in-memory
``sqlcipher3`` connection. In the running app, construct it with the keyed
``Vault.connection``.

What this layer relies on the connection providing
-------------------------------------------------
* The schema is already migrated (``Vault.create`` / ``Vault.unlock`` do this).
* ``PRAGMA foreign_keys = ON`` (the vault sets it on every connection it opens),
  so ``ON DELETE CASCADE`` removes a deleted notebook's descendants and join rows
  while ``ON DELETE SET NULL`` orphans its notes to the root rather than
  destroying them.

Writes touch ``notes`` / ``notebooks`` only — the schema's ``notes_ai`` /
``notes_ad`` / ``notes_au`` triggers keep the ``notes_fts`` full-text index in
sync automatically, so this layer never writes to it directly. Full-text search
*reads* that index through :meth:`Repository.search_notes`.

Scope: CRUD for notes, notebooks, and tags — including assigning/removing tags on
a note, filtering the note list by tag, and full-text search over notes
(:meth:`Repository.search_notes`). Markdown title derivation
(:func:`core.text.derive_title`) is deliberately elsewhere — the repository
stores whatever ``title`` / ``body`` it is given.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlcipher3.dbapi2 import Connection


class _Unset:
    """Sentinel type for "argument not provided" in partial updates.

    Distinct from ``None`` so ``update_note(id, notebook_id=None)`` (move the note
    to the root) is distinguishable from ``update_note(id)`` (leave it where it
    is). A private singleton instance, :data:`_UNSET`, is the only value used.
    """

    def __repr__(self) -> str:  # pragma: no cover - debug aid only
        return "<UNSET>"


_UNSET: Any = _Unset()


@dataclass(frozen=True)
class Notebook:
    """An immutable snapshot of a row in ``notebooks``.

    ``parent_id`` is ``None`` for a top-level notebook, otherwise the id of the
    notebook it nests under. Timestamps are SQLite ``datetime('now')`` strings
    (UTC, second resolution).
    """

    id: int
    name: str
    parent_id: int | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Note:
    """An immutable snapshot of a row in ``notes``.

    ``notebook_id`` is ``None`` for a note at the root (no notebook). ``title``
    and ``body`` are stored verbatim — deriving a display title from Markdown is
    :func:`core.text.derive_title`'s job, not this layer's.
    """

    id: int
    notebook_id: int | None
    title: str
    body: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Tag:
    """An immutable snapshot of a row in ``tags``.

    A tag is just a unique label; unlike notes and notebooks it carries no
    timestamps. Tags attach to notes through the ``note_tags`` join table.
    """

    id: int
    name: str


class RepositoryError(Exception):
    """Base class for repository errors."""


class NotFoundError(RepositoryError):
    """An update targeted a row id that does not exist."""


# Column lists are fixed constants we control (never user input), selected in the
# exact order of the dataclass fields so a row maps with ``Notebook(*row)``.
_NOTEBOOK_COLUMNS = "id, name, parent_id, created_at, updated_at"
_NOTE_COLUMNS = "id, notebook_id, title, body, created_at, updated_at"
_TAG_COLUMNS = "id, name"

# The same note columns, table-qualified — used by list_notes and search_notes,
# which JOIN another table (note_tags / notes_fts) and so must disambiguate
# (and order by) notes.* .
_NOTE_COLUMNS_QUALIFIED = ", ".join(
    f"notes.{col.strip()}" for col in _NOTE_COLUMNS.split(",")
)


def _fts_match_expr(query: str) -> str | None:
    """Turn free-text search input into a safe FTS5 ``MATCH`` expression.

    A search box hands us arbitrary text, but FTS5 ``MATCH`` parses its argument
    as a *query language* — bare metacharacters (``"`` ``*`` ``:`` ``^`` ``-``
    ``(`` ``)``) and the ``AND`` / ``OR`` / ``NOT`` keywords would either change
    the meaning or raise ``sqlcipher3.OperationalError`` (fts5: syntax error). So
    each whitespace-separated token is wrapped as a quoted FTS5 *string* term
    (any embedded ``"`` doubled, per FTS5's escaping rule) and the terms are
    joined by spaces. FTS5 implicitly ANDs space-separated terms, giving intuitive
    "every word must appear" semantics, and the quoting makes every token match
    literally instead of being interpreted as syntax.

    Returns ``None`` for empty / whitespace-only input, signalling "no search".
    """
    tokens = query.split()
    if not tokens:
        return None
    return " ".join('"' + token.replace('"', '""') + '"' for token in tokens)


class Repository:
    """CRUD over the vault's ``notes``, ``notebooks``, and ``tags`` tables.

    Construct with an open, keyed, migrated connection (``Vault.connection`` in
    the app; an in-memory ``sqlcipher3`` connection in tests). Every write commits
    before returning, so callers don't manage transactions for single operations.
    """

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    # -- notebooks -----------------------------------------------------------

    def create_notebook(self, name: str, *, parent_id: int | None = None) -> Notebook:
        """Insert a notebook and return it with its generated id and timestamps."""
        cur = self._conn.execute(
            "INSERT INTO notebooks (name, parent_id) VALUES (?, ?)",
            (name, parent_id),
        )
        self._conn.commit()
        created = self.get_notebook(cur.lastrowid)
        assert created is not None  # just inserted within this connection
        return created

    def get_notebook(self, notebook_id: int) -> Notebook | None:
        """Return the notebook with ``notebook_id``, or ``None`` if absent."""
        row = self._conn.execute(
            f"SELECT {_NOTEBOOK_COLUMNS} FROM notebooks WHERE id = ?",
            (notebook_id,),
        ).fetchone()
        return Notebook(*row) if row is not None else None

    def list_notebooks(self) -> list[Notebook]:
        """Return every notebook, ordered by name (case-insensitive) then id."""
        rows = self._conn.execute(
            f"SELECT {_NOTEBOOK_COLUMNS} FROM notebooks ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
        return [Notebook(*row) for row in rows]

    def update_notebook(
        self,
        notebook_id: int,
        *,
        name: str = _UNSET,
        parent_id: int | None = _UNSET,
    ) -> Notebook:
        """Update a notebook's ``name`` and/or ``parent_id``; bump ``updated_at``.

        Only the fields you pass are changed (``parent_id=None`` re-roots it).
        Returns the updated notebook; raises :class:`NotFoundError` if no notebook
        has ``notebook_id``.
        """
        assignments: list[str] = []
        params: list[Any] = []
        if name is not _UNSET:
            assignments.append("name = ?")
            params.append(name)
        if parent_id is not _UNSET:
            assignments.append("parent_id = ?")
            params.append(parent_id)

        if not assignments:
            existing = self.get_notebook(notebook_id)
            if existing is None:
                raise NotFoundError(f"no notebook with id {notebook_id}")
            return existing

        assignments.append("updated_at = datetime('now')")
        params.append(notebook_id)
        cur = self._conn.execute(
            f"UPDATE notebooks SET {', '.join(assignments)} WHERE id = ?",
            params,
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise NotFoundError(f"no notebook with id {notebook_id}")
        updated = self.get_notebook(notebook_id)
        assert updated is not None
        return updated

    def delete_notebook(self, notebook_id: int) -> bool:
        """Delete a notebook; return ``True`` if a row was removed.

        With foreign keys enforced, descendant notebooks cascade away and notes in
        the deleted notebook are orphaned to the root (``notebook_id`` → NULL).
        """
        cur = self._conn.execute("DELETE FROM notebooks WHERE id = ?", (notebook_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # -- notes ---------------------------------------------------------------

    def create_note(
        self,
        *,
        notebook_id: int | None = None,
        title: str = "",
        body: str = "",
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> Note:
        """Insert a note and return it with its generated id and timestamps.

        ``created_at`` / ``updated_at`` normally come from the schema default
        (``datetime('now')``) and should be left unset. They exist so an import
        (:mod:`core.importer`) can preserve a legacy note's original timestamps;
        each is included in the insert only when given, so omitting one keeps its
        default while still setting the other.
        """
        columns = ["notebook_id", "title", "body"]
        values: list[Any] = [notebook_id, title, body]
        if created_at is not None:
            columns.append("created_at")
            values.append(created_at)
        if updated_at is not None:
            columns.append("updated_at")
            values.append(updated_at)

        # Column names are fixed constants we control (never user input); only the
        # values are bound parameters, so the joined column list is not injectable.
        placeholders = ", ".join("?" * len(values))
        cur = self._conn.execute(
            f"INSERT INTO notes ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
        self._conn.commit()
        created = self.get_note(cur.lastrowid)
        assert created is not None
        return created

    def get_note(self, note_id: int) -> Note | None:
        """Return the note with ``note_id``, or ``None`` if absent."""
        row = self._conn.execute(
            f"SELECT {_NOTE_COLUMNS} FROM notes WHERE id = ?",
            (note_id,),
        ).fetchone()
        return Note(*row) if row is not None else None

    def list_notes(
        self,
        *,
        notebook_id: int | None = _UNSET,
        tag_id: int = _UNSET,
    ) -> list[Note]:
        """Return notes, most-recently-updated first (ties broken by newest id).

        With no arguments, returns every note. The filters narrow the result and
        combine (AND-ed together):

        * ``notebook_id`` — an int restricts to that notebook; ``None`` returns
          root notes (those with no notebook).
        * ``tag_id`` — restricts to notes carrying that tag (joins ``note_tags``).
          Each note appears at most once, since a note has a tag only once.
        """
        join = ""
        conditions: list[str] = []
        params: list[Any] = []

        if notebook_id is not _UNSET:
            if notebook_id is None:
                conditions.append("notes.notebook_id IS NULL")
            else:
                conditions.append("notes.notebook_id = ?")
                params.append(notebook_id)

        if tag_id is not _UNSET:
            join = " JOIN note_tags ON note_tags.note_id = notes.id"
            conditions.append("note_tags.tag_id = ?")
            params.append(tag_id)

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self._conn.execute(
            f"SELECT {_NOTE_COLUMNS_QUALIFIED} FROM notes{join}{where} "
            "ORDER BY notes.updated_at DESC, notes.id DESC",
            params,
        ).fetchall()
        return [Note(*row) for row in rows]

    def update_note(
        self,
        note_id: int,
        *,
        title: str = _UNSET,
        body: str = _UNSET,
        notebook_id: int | None = _UNSET,
    ) -> Note:
        """Update a note's ``title`` / ``body`` / ``notebook_id``; bump ``updated_at``.

        Only the fields you pass are changed (``notebook_id=None`` moves the note
        to the root). Returns the updated note; raises :class:`NotFoundError` if no
        note has ``note_id``. The FTS index follows automatically via triggers.
        """
        assignments: list[str] = []
        params: list[Any] = []
        if title is not _UNSET:
            assignments.append("title = ?")
            params.append(title)
        if body is not _UNSET:
            assignments.append("body = ?")
            params.append(body)
        if notebook_id is not _UNSET:
            assignments.append("notebook_id = ?")
            params.append(notebook_id)

        if not assignments:
            existing = self.get_note(note_id)
            if existing is None:
                raise NotFoundError(f"no note with id {note_id}")
            return existing

        assignments.append("updated_at = datetime('now')")
        params.append(note_id)
        cur = self._conn.execute(
            f"UPDATE notes SET {', '.join(assignments)} WHERE id = ?",
            params,
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise NotFoundError(f"no note with id {note_id}")
        updated = self.get_note(note_id)
        assert updated is not None
        return updated

    def delete_note(self, note_id: int) -> bool:
        """Delete a note; return ``True`` if a row was removed.

        The note's tag-join rows cascade away and the FTS index is updated by the
        delete trigger.
        """
        cur = self._conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # -- tags ----------------------------------------------------------------

    def create_tag(self, name: str) -> Tag:
        """Insert a tag and return it with its generated id.

        Tag names are unique (``tags.name`` has a ``UNIQUE`` constraint), so
        inserting a name that already exists raises ``sqlcipher3.IntegrityError`` —
        call :meth:`get_tag_by_name` first for get-or-create behaviour.
        """
        cur = self._conn.execute("INSERT INTO tags (name) VALUES (?)", (name,))
        self._conn.commit()
        created = self.get_tag(cur.lastrowid)
        assert created is not None  # just inserted within this connection
        return created

    def get_tag(self, tag_id: int) -> Tag | None:
        """Return the tag with ``tag_id``, or ``None`` if absent."""
        row = self._conn.execute(
            f"SELECT {_TAG_COLUMNS} FROM tags WHERE id = ?",
            (tag_id,),
        ).fetchone()
        return Tag(*row) if row is not None else None

    def get_tag_by_name(self, name: str) -> Tag | None:
        """Return the tag named ``name``, or ``None`` if no tag has that name.

        The lookup half of a get-or-create when a UI assigns tags by name. The
        ``UNIQUE`` constraint is case-sensitive, so the match is exact.
        """
        row = self._conn.execute(
            f"SELECT {_TAG_COLUMNS} FROM tags WHERE name = ?",
            (name,),
        ).fetchone()
        return Tag(*row) if row is not None else None

    def list_tags(self) -> list[Tag]:
        """Return every tag, ordered by name (case-insensitive) then id."""
        rows = self._conn.execute(
            f"SELECT {_TAG_COLUMNS} FROM tags ORDER BY name COLLATE NOCASE, id"
        ).fetchall()
        return [Tag(*row) for row in rows]

    def delete_tag(self, tag_id: int) -> bool:
        """Delete a tag; return ``True`` if a row was removed.

        The tag's ``note_tags`` join rows cascade away, so it disappears from every
        note that carried it; the notes themselves are untouched.
        """
        cur = self._conn.execute("DELETE FROM tags WHERE id = ?", (tag_id,))
        self._conn.commit()
        return cur.rowcount > 0

    # -- note <-> tag association --------------------------------------------

    def add_tag_to_note(self, note_id: int, tag_id: int) -> None:
        """Attach ``tag_id`` to ``note_id``; a no-op if already attached.

        Idempotent: ``INSERT OR IGNORE`` swallows the composite-PK conflict when the
        tag is already on the note, so re-adding is harmless. A ``note_id`` or
        ``tag_id`` that does not exist still raises ``sqlcipher3.IntegrityError``
        (the foreign keys are enforced).
        """
        self._conn.execute(
            "INSERT OR IGNORE INTO note_tags (note_id, tag_id) VALUES (?, ?)",
            (note_id, tag_id),
        )
        self._conn.commit()

    def remove_tag_from_note(self, note_id: int, tag_id: int) -> bool:
        """Detach ``tag_id`` from ``note_id``; return ``True`` if it was attached."""
        cur = self._conn.execute(
            "DELETE FROM note_tags WHERE note_id = ? AND tag_id = ?",
            (note_id, tag_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def tags_for_note(self, note_id: int) -> list[Tag]:
        """Return the tags attached to ``note_id``, ordered by name.

        An empty list if the note has no tags (or does not exist) — this is a read,
        so a missing note simply has no join rows.
        """
        rows = self._conn.execute(
            "SELECT tags.id, tags.name FROM tags "
            "JOIN note_tags ON note_tags.tag_id = tags.id "
            "WHERE note_tags.note_id = ? "
            "ORDER BY tags.name COLLATE NOCASE, tags.id",
            (note_id,),
        ).fetchall()
        return [Tag(*row) for row in rows]

    # -- secrets (API key store) ---------------------------------------------

    # The secret name used for the AI inference API key.
    _API_KEY_NAME = "ai_api_key"

    def set_api_key(self, key: str) -> None:
        """Store (or replace) the AI API key in the encrypted vault.

        The whole database is SQLCipher-encrypted, so the key is protected at
        rest. It is stored verbatim and is NEVER surfaced to the UI; callers that
        need it for network requests use :meth:`get_api_key` directly.
        """
        self._conn.execute(
            "INSERT OR REPLACE INTO app_secrets (name, value) VALUES (?, ?)",
            (self._API_KEY_NAME, key),
        )
        self._conn.commit()

    def has_api_key(self) -> bool:
        """Return ``True`` if an API key is stored in the vault."""
        row = self._conn.execute(
            "SELECT 1 FROM app_secrets WHERE name = ?",
            (self._API_KEY_NAME,),
        ).fetchone()
        return row is not None

    def get_api_key(self) -> str | None:
        """Return the stored API key, or ``None`` if none is stored.

        For use by the AI client **only** — never pass the return value to the
        UI or log it anywhere.
        """
        row = self._conn.execute(
            "SELECT value FROM app_secrets WHERE name = ?",
            (self._API_KEY_NAME,),
        ).fetchone()
        return row[0] if row is not None else None

    def clear_api_key(self) -> None:
        """Remove the stored API key from the vault (no-op if absent)."""
        self._conn.execute(
            "DELETE FROM app_secrets WHERE name = ?",
            (self._API_KEY_NAME,),
        )
        self._conn.commit()

    # -- full-text search ----------------------------------------------------

    def search_notes(self, query: str, *, limit: int | None = None) -> list[Note]:
        """Full-text search across **all** notes, best matches first.

        Runs an FTS5 ``MATCH`` over the ``notes_fts`` index (which covers each
        note's ``title`` and ``body``) and returns the matching notes as
        :class:`Note` objects, ordered by relevance (FTS5's bm25 ``rank``, best
        first). The index is kept current by the schema's triggers, so results
        always reflect the latest committed edits.

        ``query`` is free text from a search box: it is treated as literal terms
        (see :func:`_fts_match_expr`), so FTS5 metacharacters never raise and a
        multi-word query requires every word to appear (implicit AND). An empty or
        whitespace-only query returns ``[]`` without touching the database.
        ``limit``, if given, caps the number of results returned.

        Read-only — writes nothing.
        """
        match = _fts_match_expr(query)
        if match is None:
            return []

        sql = (
            f"SELECT {_NOTE_COLUMNS_QUALIFIED} FROM notes_fts "
            "JOIN notes ON notes.id = notes_fts.rowid "
            "WHERE notes_fts MATCH ? ORDER BY notes_fts.rank"
        )
        params: list[Any] = [match]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [Note(*row) for row in rows]
