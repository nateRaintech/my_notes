"""The preview's document: renders ``mnimg:`` images from the vault (#101).

``QTextDocument.setMarkdown`` resolves image URLs through :meth:`loadResource`.
:class:`VaultTextDocument` answers ``mnimg:<id>?w=<px>`` URLs from the vault's
:class:`~core.images.ImageStore`, scaled to ``w × device pixel ratio`` and tagged
with that ratio, so Qt lays the image out at ``w`` logical pixels and it stays
sharp on a scaled display.

The cache is a requirement, not polish. The preview re-renders on every
keystroke, and an overridden ``loadResource`` bypasses Qt's own resource cache —
Qt calls it several times per render (each layout and paint pass). So each
scaled rendering is kept in an :class:`ImageCache` bounded by total bytes, keyed
by the width the Markdown asks for, and looked up *before* anything is decoded.
Full-resolution decodes are transient and never cached: a few 4K screenshots
(~33 MB each) would crowd the scaled renderings out of the budget and turn every
keystroke into a re-decode. Decoded images are decrypted content:
:meth:`set_store` clears the cache, and ``MainWindow.lock_session`` detaches the
store on lock.

The width comes from text the user types, so the device width is clamped (see
:func:`device_width`) before it reaches Qt: an unbounded ``scaledToWidth`` pins
gigabytes or crashes the process mid-keystroke. And ``loadResource`` is a Qt
virtual — an exception there must never escape, so any failure to fetch, decode
or scale an image renders the "missing image" placeholder instead.

Per CLAUDE.md's layering, the UI may import Qt freely; ``core/`` must never
import this module.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, Callable, Hashable

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QColor, QImage, QPainter, QTextDocument

from core.image_refs import SCHEME, parse_url
from ui.image_ingest import natural_logical_width

if TYPE_CHECKING:
    from core.images import ImageStore

#: Upper bound on decoded image bytes held in memory for the preview.
CACHE_BUDGET_BYTES = 128 * 1024 * 1024

#: The widest, in device pixels, the preview ever renders an image.
MAX_DEVICE_WIDTH = 16384

#: How far past its natural width an image may be enlarged.
MAX_UPSCALE = 4

_IMAGE_RESOURCE = QTextDocument.ResourceType.ImageResource.value


def device_width(width: int, ratio: float, natural_px_width: int) -> int:
    """The device-pixel width to render ``width`` logical px at, clamped.

    At most :data:`MAX_UPSCALE` × the natural width and :data:`MAX_DEVICE_WIDTH`,
    at least 1. ``width`` is clamped *before* it is multiplied, so a typed
    ``?w=`` with 25 digits never becomes a number Qt can't take.
    """
    limit = max(1, min(natural_px_width * MAX_UPSCALE, MAX_DEVICE_WIDTH))
    return max(1, min(round(min(width, limit) * ratio), limit))


class ImageCache:
    """Least-recently-used ``QImage`` cache, bounded by total decoded bytes."""

    def __init__(self, budget_bytes: int = CACHE_BUDGET_BYTES) -> None:
        self._budget = budget_bytes
        self._items: OrderedDict[Hashable, QImage] = OrderedDict()
        self.total_bytes = 0

    def __len__(self) -> int:
        return len(self._items)

    def get(self, key: Hashable) -> QImage | None:
        image = self._items.get(key)
        if image is not None:
            self._items.move_to_end(key)
        return image

    def put(self, key: Hashable, image: QImage) -> None:
        old = self._items.pop(key, None)
        if old is not None:
            self.total_bytes -= old.sizeInBytes()
        self._items[key] = image
        self.total_bytes += image.sizeInBytes()
        # Always keep the newest entry, even if it alone exceeds the budget.
        while self.total_bytes > self._budget and len(self._items) > 1:
            _, evicted = self._items.popitem(last=False)
            self.total_bytes -= evicted.sizeInBytes()

    def clear(self) -> None:
        self._items.clear()
        self.total_bytes = 0


class VaultTextDocument(QTextDocument):
    """A ``QTextDocument`` that resolves ``mnimg:`` image URLs from the vault."""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        device_pixel_ratio: Callable[[], float] = lambda: 1.0,
    ) -> None:
        super().__init__(parent)
        self._store: ImageStore | None = None
        self._device_pixel_ratio = device_pixel_ratio
        self._cache = ImageCache()
        self._placeholder: QImage | None = None

    @property
    def cache(self) -> ImageCache:
        return self._cache

    def set_store(self, store: ImageStore | None) -> None:
        """Bind (or detach) the vault's images; always drops decoded images."""
        self._store = store
        self._cache.clear()

    def natural_image(self, image_id: int) -> QImage | None:
        """The full-resolution image, decoded now and not cached — or ``None``.

        ``None`` when no store is bound, the id is unknown, the bytes don't
        decode, or the store fails (e.g. its connection was closed by a lock).
        """
        if self._store is None:
            return None
        try:
            record = self._store.get(image_id)
            if record is None:
                return None
            image = QImage.fromData(record.data)
        except Exception:
            return None
        return None if image.isNull() else image

    def placeholder(self) -> QImage:
        """What a missing or unreadable image renders as — visible, never blank."""
        if self._placeholder is None:
            image = QImage(160, 90, QImage.Format.Format_ARGB32)
            image.fill(QColor(128, 128, 128, 40))
            painter = QPainter(image)
            painter.setPen(QColor(128, 128, 128))
            painter.drawRect(0, 0, 159, 89)
            painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, "missing image")
            painter.end()
            self._placeholder = image
        return self._placeholder

    def loadResource(self, type_: int, url: QUrl) -> object:
        kind = getattr(type_, "value", type_)
        if kind != _IMAGE_RESOURCE or url.scheme() != SCHEME:
            return super().loadResource(type_, url)

        parsed = parse_url(url.toString())
        if parsed is None:
            return self.placeholder()
        image_id, width = parsed
        ratio = self._device_pixel_ratio()
        # Keyed by the width as written (None = natural size), so a hit costs
        # neither a query nor a decode.
        key = ("scaled", image_id, width, ratio)
        scaled = self._cache.get(key)
        if scaled is not None:
            return scaled
        try:
            scaled = self._render(image_id, width, ratio)
        except Exception:
            scaled = None  # never let an exception escape a Qt virtual
        if scaled is None:
            return self.placeholder()
        self._cache.put(key, scaled)
        return scaled

    def _render(self, image_id: int, width: int | None, ratio: float) -> QImage | None:
        """``image_id`` scaled for ``width`` logical px at ``ratio``, or ``None``."""
        natural = self.natural_image(image_id)
        if natural is None:
            return None
        if width is None:
            width = natural_logical_width(natural.width(), ratio)
        scaled = natural.scaledToWidth(
            device_width(width, ratio, natural.width()),
            Qt.TransformationMode.SmoothTransformation,
        )
        if scaled.isNull():
            return None
        scaled.setDevicePixelRatio(ratio)
        return scaled
