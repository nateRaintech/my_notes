"""Images stored inside the encrypted vault (#101).

Pasted, dropped and inserted images live as blobs in the ``images`` table
(schema migration 3), so they are encrypted at rest with everything else — never
written to disk in the clear. A note refers to one only through an ``mnimg:<id>``
URL in its body (:mod:`core.image_refs`); images are vault-global and
content-addressed by SHA-256, so the same screenshot pasted into five notes is
stored once.

Because no foreign key ties an image to a note, nothing cascades when a note or
an image line is deleted. :meth:`ImageStore.sweep_orphans` reclaims images no
note mentions; the app runs it only at unlock, before any tab can open (see
``app._bind_vault``), which is the one moment it is safe.

Pure Python, no Qt: the store never decodes image data. Callers pass the natural
width and height, because only the UI layer has a ``QImage`` to measure.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.image_refs import referenced_ids

if TYPE_CHECKING:
    from sqlcipher3.dbapi2 import Connection

#: The largest image the vault accepts, in bytes.
MAX_IMAGE_BYTES = 20 * 1024 * 1024

_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}

_COLUMNS = "id, sha256, mime, width, height, byte_size, data, created_at"


def extension_for(mime: str) -> str:
    """The file extension to save an image of ``mime`` with."""
    return _EXTENSIONS.get(mime, "bin")


@dataclass(frozen=True)
class ImageRecord:
    """An immutable snapshot of a row in ``images``. ``width``/``height`` are px."""

    id: int
    sha256: str
    mime: str
    width: int
    height: int
    byte_size: int
    data: bytes
    created_at: str


@dataclass(frozen=True)
class SweepResult:
    """What :meth:`ImageStore.sweep_orphans` removed."""

    count: int
    freed_bytes: int


class ImageStore:
    """Add, fetch and sweep vault images over an open, migrated connection.

    Like :class:`~core.repository.Repository`, every write commits before
    returning.
    """

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def add(self, data: bytes, mime: str, width: int, height: int) -> ImageRecord:
        """Store ``data`` and return its record — or the existing one, if identical.

        Raises :class:`ValueError` for empty data, data over
        :data:`MAX_IMAGE_BYTES`, or a non-positive size.
        """
        if not data:
            raise ValueError("The image is empty")
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"The image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
        if width <= 0 or height <= 0:
            raise ValueError("The image has no size")

        digest = hashlib.sha256(data).hexdigest()
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM images WHERE sha256 = ?", (digest,)
        ).fetchone()
        if row is not None:
            return _record(row)

        cursor = self._conn.execute(
            "INSERT INTO images (sha256, mime, width, height, byte_size, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (digest, mime, width, height, len(data), data),
        )
        self._conn.commit()
        record = self.get(cursor.lastrowid)
        assert record is not None
        return record

    def get(self, image_id: int) -> ImageRecord | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM images WHERE id = ?", (image_id,)
        ).fetchone()
        return _record(row) if row is not None else None

    def sweep_orphans(self) -> SweepResult:
        """Delete every image no note body mentions; report what went.

        Uses :func:`~core.image_refs.referenced_ids`, which counts mentions
        anywhere — code blocks included — so the sweep errs toward keeping.
        Deleting rows does not shrink the vault file: SQLite keeps the freed
        pages and reuses them for later images.
        """
        referenced: set[int] = set()
        for (body,) in self._conn.execute("SELECT body FROM notes"):
            referenced |= referenced_ids(body)

        doomed = [
            (image_id, size)
            for image_id, size in self._conn.execute("SELECT id, byte_size FROM images")
            if image_id not in referenced
        ]
        self._conn.executemany(
            "DELETE FROM images WHERE id = ?", [(image_id,) for image_id, _ in doomed]
        )
        self._conn.commit()
        return SweepResult(count=len(doomed), freed_bytes=sum(size for _, size in doomed))


def _record(row: tuple) -> ImageRecord:
    return ImageRecord(
        id=row[0],
        sha256=row[1],
        mime=row[2],
        width=row[3],
        height=row[4],
        byte_size=row[5],
        data=bytes(row[6]),
        created_at=row[7],
    )
