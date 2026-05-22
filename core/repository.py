"""CRUD data-access layer for notes and notebooks in the encrypted vault.

This is the typed Python API the UI calls instead of writing SQL: the editor,
note list, and notebook tree all go through :class:`Repository`. It turns the raw
``notes`` / ``notebooks`` tables created by :mod:`core.schema` into immutable
:class:`Note` / :class:`Notebook` value objects.

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
sync automatically, so this layer never writes to it directly. (Search *queries*
over that index are M4; this layer just must not break the triggers.)

Scope: CRUD for notes and notebooks. Tag assignment/filtering, full-text search,
and Markdown title derivation (:func:`core.text.derive_title`) are deliberately
elsewhere — the repository stores whatever ``title`` / ``body`` it is given.
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


class RepositoryError(Exception):
    """Base class for repository errors."""


class NotFoundError(RepositoryError):
    """An update targeted a row id that does not exist."""


# Column lists are fixed constants we control (never user input), selected in the
# exact order of the dataclass fields so a row maps with ``Notebook(*row)``.
_NOTEBOOK_COLUMNS = "id, name, parent_id, created_at, updated_at"
_NOTE_COLUMNS = "id, notebook_id, title, body, created_at, updated_at"


class Repository:
    """CRUD over the vault's ``notes`` and ``notebooks`` tables.

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
    ) -> Note:
        """Insert a note and return it with its generated id and timestamps."""
        cur = self._conn.execute(
            "INSERT INTO notes (notebook_id, title, body) VALUES (?, ?, ?)",
            (notebook_id, title, body),
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

    def list_notes(self, *, notebook_id: int | None = _UNSET) -> list[Note]:
        """Return notes, most-recently-updated first (ties broken by newest id).

        With no argument, returns every note. Pass ``notebook_id`` to filter:
        an int restricts to that notebook, ``None`` returns root notes (those with
        no notebook).
        """
        order = "ORDER BY updated_at DESC, id DESC"
        if notebook_id is _UNSET:
            rows = self._conn.execute(
                f"SELECT {_NOTE_COLUMNS} FROM notes {order}"
            ).fetchall()
        elif notebook_id is None:
            rows = self._conn.execute(
                f"SELECT {_NOTE_COLUMNS} FROM notes WHERE notebook_id IS NULL {order}"
            ).fetchall()
        else:
            rows = self._conn.execute(
                f"SELECT {_NOTE_COLUMNS} FROM notes WHERE notebook_id = ? {order}",
                (notebook_id,),
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
