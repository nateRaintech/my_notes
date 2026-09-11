"""Turning pasted, dropped and chosen images into vault images (#101).

One ingest path behind three entry points — Ctrl+V and drag & drop (via
``ui/note_source.py``) and **Insert → Image…** (``MainWindow``). Each stores the
image with :class:`~core.images.ImageStore` and returns the Markdown to insert,
``![alt](mnimg:<id>?w=<px>)``.

* Clipboard images are encoded as PNG — lossless, right for screenshots.
* Files keep their **original bytes** when they are PNG, JPEG, GIF or WebP, so a
  200 KB photo does not become a 2 MB PNG; other decodable formats become PNG.
* The initial width is the image's natural logical width capped at
  :data:`PASTE_MAX_WIDTH`, so a 4K screenshot doesn't land as a wall. The stored
  bytes stay full resolution — resizing is display-only.

Per CLAUDE.md's layering, the UI may import Qt freely; ``core/`` must never
import this module.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QBuffer, QIODevice, QMimeData
from PySide6.QtGui import QImage, QImageReader

from core.image_refs import image_markdown
from core.images import MAX_IMAGE_BYTES

if TYPE_CHECKING:
    from core.images import ImageStore

#: The widest (in logical px) a freshly inserted image starts out.
PASTE_MAX_WIDTH = 800

# Qt image formats whose original bytes are stored as-is, and their mime types.
_KEEP_ORIGINAL = {
    b"png": "image/png",
    b"jpeg": "image/jpeg",
    b"gif": "image/gif",
    b"webp": "image/webp",
}


class IngestError(Exception):
    """An image could not be added; the message is shown to the user as-is."""


def natural_logical_width(pixel_width: int, device_pixel_ratio: float) -> int:
    """The width, in logical px, at which one image pixel is one device pixel.

    That is both the sharpest rendering and the size a screenshot appeared on
    screen when it was taken.
    """
    return max(1, round(pixel_width / device_pixel_ratio))


def initial_width(pixel_width: int, device_pixel_ratio: float) -> int:
    """The ``w`` a newly inserted image gets: natural size, capped at 800."""
    return min(natural_logical_width(pixel_width, device_pixel_ratio), PASTE_MAX_WIDTH)


def png_bytes(image: QImage) -> bytes:
    """``image`` encoded as PNG."""
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def ingest_image(
    store: ImageStore,
    image: QImage,
    *,
    device_pixel_ratio: float,
    alt: str = "screenshot",
) -> str:
    """Store a clipboard image as PNG; return the Markdown that shows it."""
    if image.isNull():
        raise IngestError("The clipboard image is empty")
    return _store(store, png_bytes(image), "image/png", image, alt, device_pixel_ratio)


def ingest_file(
    store: ImageStore,
    path: str | os.PathLike[str],
    *,
    device_pixel_ratio: float,
) -> str:
    """Store the image file at ``path``; return the Markdown that shows it."""
    file = Path(path)
    try:
        size = file.stat().st_size
        if size > MAX_IMAGE_BYTES:
            raise IngestError(f"{file.name} is larger than {_max_mb()} MB")
        data = file.read_bytes()
    except OSError as error:
        raise IngestError(f"Can't read {file.name}: {error.strerror}") from error

    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    image_format = bytes(reader.format())
    image = reader.read()
    if image.isNull():
        raise IngestError(f"{file.name} isn't an image this app can read")

    mime = _KEEP_ORIGINAL.get(image_format)
    if mime is None:
        data, mime = png_bytes(image), "image/png"
    return _store(store, data, mime, image, file.stem, device_pixel_ratio)


def local_image_paths(mime: QMimeData) -> list[str]:
    """The dropped files in ``mime`` — but only if *every* URL is a local image.

    A mixed drop (an image plus a text file, or a web link) returns ``[]`` so it
    falls through to Qt's default handling instead of half-applying.
    """
    if not mime.hasUrls():
        return []
    readable = _readable_suffixes()
    paths = []
    for url in mime.urls():
        if not url.isLocalFile():
            return []
        path = url.toLocalFile()
        if Path(path).suffix.lower().lstrip(".") not in readable:
            return []
        paths.append(path)
    return paths


@functools.cache
def _readable_suffixes() -> frozenset[str]:
    """File suffixes Qt can decode — built once, not on every drag-move event."""
    return frozenset(bytes(f).decode().lower() for f in QImageReader.supportedImageFormats())


def wants_image_paste(mime: QMimeData) -> bool:
    """Whether pasting/dropping ``mime`` should insert images rather than text.

    Text wins over an image: Word, Excel and Outlook put both text and a picture
    of it on the clipboard, and pasting a table as a screenshot would be wrong.
    Snipping Tool / Win+Shift+S provide an image and no text.
    """
    if local_image_paths(mime):
        return True
    return mime.hasImage() and not mime.hasText()


def _store(
    store: ImageStore,
    data: bytes,
    mime: str,
    image: QImage,
    alt: str,
    device_pixel_ratio: float,
) -> str:
    if len(data) > MAX_IMAGE_BYTES:
        raise IngestError(f"The image is larger than {_max_mb()} MB")
    try:
        record = store.add(data, mime, image.width(), image.height())
    except ValueError as error:
        raise IngestError(str(error)) from error
    return image_markdown(record.id, alt, initial_width(image.width(), device_pixel_ratio))


def _max_mb() -> int:
    return MAX_IMAGE_BYTES // (1024 * 1024)
