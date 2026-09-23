"""Hidden text: values kept out of a note's body, shown only as a mask (#113).

Selecting text in a note and choosing **Hide text** moves it into the
``hidden_texts`` table (schema migration 4) and leaves a reference in its
place — an ordinary Markdown image with a private scheme::

    Password: ![hidden](mnsec:7)

So the plaintext is not in the note body, the source pane, or the full-text
index. The preview draws each reference as a masked pill
(``ui/hidden_text.py``); the value leaves the vault only through the
clipboard.

Like images (:mod:`core.images`), hidden values are vault-global: no foreign key
ties one to a note, and :meth:`HiddenTextStore.sweep_orphans` reclaims values no
note mentions. The app runs it only at unlock, before any tab can open. Unlike
images there is no deduplication — two notes hiding the same password get two
rows, so editing one never silently changes the other.

Pure Python, no Qt (CLAUDE.md).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlcipher3.dbapi2 import Connection

SCHEME = "mnsec"

#: What a hidden reference's alt text is unless the caller names it.
DEFAULT_LABEL = "hidden"

_URL_RE = re.compile(r"^" + SCHEME + r":(\d+)$")
_ANY_ID_RE = re.compile(SCHEME + r":(\d+)")


def hidden_markdown(hidden_id: int, label: str = DEFAULT_LABEL) -> str:
    """The Markdown reference that shows ``hidden_id`` as a mask."""
    cleaned = " ".join(re.sub(r"[\[\]\r\n]+", " ", label).split()) or DEFAULT_LABEL
    return f"![{cleaned}]({SCHEME}:{hidden_id})"


def parse_url(url: str) -> int | None:
    """``"mnsec:7"`` -> ``7``; ``None`` for anything else."""
    match = _URL_RE.match(url)
    return int(match.group(1)) if match else None


def referenced_ids(markdown: str) -> set[int]:
    """Every hidden id ``markdown`` mentions *anywhere*, code blocks included.

    Liberal on purpose, as for images: the sweep uses it, and keeping a value a
    note only mentions costs nothing, while deleting one it needs loses it.
    """
    return {int(found) for found in _ANY_ID_RE.findall(markdown)}


def _require_value(value: str) -> None:
    if not value.strip():
        raise ValueError("There is no text to hide")


class HiddenTextStore:
    """Add, fetch, change and sweep hidden values over an open, migrated connection.

    Every write commits before returning, like :class:`~core.repository.Repository`.
    Values are stored verbatim — whitespace and line breaks included.
    """

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def add(self, value: str) -> int:
        """Store ``value`` and return its id. ``ValueError`` if it is blank."""
        _require_value(value)
        cursor = self._conn.execute("INSERT INTO hidden_texts (value) VALUES (?)", (value,))
        self._conn.commit()
        return cursor.lastrowid

    def get(self, hidden_id: int) -> str | None:
        row = self._conn.execute(
            "SELECT value FROM hidden_texts WHERE id = ?", (hidden_id,)
        ).fetchone()
        return row[0] if row is not None else None

    def update(self, hidden_id: int, value: str) -> bool:
        """Replace the value; ``False`` if the id is unknown, ``ValueError`` if blank."""
        _require_value(value)
        cursor = self._conn.execute(
            "UPDATE hidden_texts SET value = ?, updated_at = datetime('now') WHERE id = ?",
            (value, hidden_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def sweep_orphans(self) -> int:
        """Delete every value no note body mentions; return how many went."""
        referenced: set[int] = set()
        for (body,) in self._conn.execute("SELECT body FROM notes"):
            referenced |= referenced_ids(body)
        doomed = [
            (hidden_id,)
            for (hidden_id,) in self._conn.execute("SELECT id FROM hidden_texts")
            if hidden_id not in referenced
        ]
        self._conn.executemany("DELETE FROM hidden_texts WHERE id = ?", doomed)
        self._conn.commit()
        return len(doomed)
