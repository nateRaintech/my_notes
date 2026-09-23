"""Hidden text in the preview: the mask, clicking it, and the clipboard (#113).

A hidden value is referenced from a note as ``![hidden](mnsec:<id>)``
(:mod:`core.hidden_text`). The preview's document renders every such reference
as the same pill — eight dots and a copy glyph — from :func:`mask_image`. The
pill never depends on the value: the dot count doesn't leak its length, and
rendering needs no vault lookup.

The value leaves the vault only through :class:`ClipboardGuard`, which marks
the clipboard entry as excluded from Windows clipboard history and cloud sync,
and clears it after a timeout — but only if the clipboard still holds it, so
something the user copied since is never wiped. The guard keeps a hash of the
value, not the value.

Per CLAUDE.md's layering, the UI may import Qt freely; ``core/`` must never
import this module.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QMimeData, QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QTextDocument
from PySide6.QtWidgets import QTextEdit

from core.hidden_text import parse_url
from ui.preview_images import image_positions, rendered_rect

#: The pill's size in logical pixels.
MASK_SIZE = QSize(105, 18)

#: The mask's text form, for plain-text output (Copy Text).
MASK_TEXT = "•" * 8

_DOTS = 8
_DOT_PITCH = 8
_DOT_DIAMETER = 5
_PAD = 8
_INK = QColor(128, 128, 128)

_WINDOWS_FORMAT = 'application/x-qt-windows-mime;value="{}"'

#: Clipboard formats that keep a copied value out of Windows clipboard history
#: (Win+V) and cloud clipboard sync — the same ones KeePass and browsers'
#: password managers set. Qt passes a ``x-qt-windows-mime`` type straight
#: through as a registered clipboard format of that name.
HISTORY_EXCLUSION_FORMATS = (
    _WINDOWS_FORMAT.format("ExcludeClipboardContentFromMonitorProcessing"),
    _WINDOWS_FORMAT.format("CanIncludeInClipboardHistory"),
    _WINDOWS_FORMAT.format("CanUploadToCloudClipboard"),
)


def mask_image(ratio: float = 1.0) -> QImage:
    """The pill that stands in for every hidden value, sharp at ``ratio``.

    Painted with shapes, not text: the offscreen platform has no fonts on
    Windows, and exports must look like the preview. Mid-grey ink on a faint
    fill reads on both the light and the dark theme.
    """
    image = QImage(
        round(MASK_SIZE.width() * ratio),
        round(MASK_SIZE.height() * ratio),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.fill(Qt.GlobalColor.transparent)
    image.setDevicePixelRatio(ratio)
    width, height = MASK_SIZE.width(), MASK_SIZE.height()

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    fill = QColor(_INK)
    fill.setAlpha(40)
    painter.setBrush(fill)
    painter.setPen(QPen(_INK, 1))
    painter.drawRoundedRect(QRectF(0.5, 0.5, width - 1, height - 1), 5, 5)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_INK)
    top = (height - _DOT_DIAMETER) / 2
    for i in range(_DOTS):
        painter.drawEllipse(QRectF(_PAD + i * _DOT_PITCH, top, _DOT_DIAMETER, _DOT_DIAMETER))

    # A divider, then a copy glyph: two overlapping sheets.
    divider = _PAD + _DOTS * _DOT_PITCH + 3
    painter.setPen(QPen(_INK, 1))
    painter.drawLine(QPoint(divider, 4), QPoint(divider, height - 4))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    glyph = divider + 7
    painter.drawRoundedRect(QRectF(glyph + 3, 3.5, 7, 8), 1.5, 1.5)
    painter.setBrush(QColor(_INK.red(), _INK.green(), _INK.blue(), 90))
    painter.drawRoundedRect(QRectF(glyph, 6.5, 7, 8), 1.5, 1.5)
    painter.end()
    return image


@dataclass(frozen=True)
class PreviewHidden:
    """One hidden-text pill in the rendered preview."""

    hidden_id: int
    position: int
    url: str


def rendered_hidden(document: QTextDocument) -> list[PreviewHidden]:
    """Every ``mnsec:`` pill in ``document``, in order."""
    found = []
    for url, position in image_positions(document):
        hidden_id = parse_url(url)
        if hidden_id is not None:
            found.append(PreviewHidden(hidden_id, position, url))
    return found


def hidden_rect(view: QTextEdit, item: PreviewHidden) -> QRect:
    """Where ``item`` is drawn in ``view``'s viewport (empty if unknown)."""
    return rendered_rect(view, item.position, item.url)


def hidden_at(view: QTextEdit, point: QPoint) -> PreviewHidden | None:
    """The pill under ``point`` (viewport coordinates), if any."""
    for item in rendered_hidden(view.document()):
        if hidden_rect(view, item).contains(point):
            return item
    return None


class PreviewClickFilter(QObject):
    """Makes pills in a read-only ``QTextEdit`` clickable.

    Installed on the view's viewport. A left press and release on the same pill
    emits :attr:`clicked`; both events are consumed so the click doesn't also
    start a text selection. Hovering a pill shows a pointing hand.
    """

    #: A pill was clicked.
    clicked = Signal(object)

    def __init__(self, view: QTextEdit) -> None:
        super().__init__(view)
        self._view = view
        self._pressed: PreviewHidden | None = None
        self._saved_cursor: Qt.CursorShape | None = None
        view.viewport().setMouseTracking(True)
        view.viewport().installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if kind == QEvent.Type.MouseMove and event.buttons() == Qt.MouseButton.NoButton:
            self._update_cursor(hidden_at(self._view, event.position().toPoint()) is not None)
        elif kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._pressed = hidden_at(self._view, event.position().toPoint())
            return self._pressed is not None
        elif (
            kind == QEvent.Type.MouseButtonRelease
            and event.button() == Qt.MouseButton.LeftButton
            and self._pressed is not None
        ):
            pressed, self._pressed = self._pressed, None
            released = hidden_at(self._view, event.position().toPoint())
            if released is not None and released.position == pressed.position:
                self.clicked.emit(released)
            return True
        return False

    def _update_cursor(self, over_pill: bool) -> None:
        viewport = self._view.viewport()
        if over_pill and self._saved_cursor is None:
            self._saved_cursor = viewport.cursor().shape()
            viewport.setCursor(Qt.CursorShape.PointingHandCursor)
        elif not over_pill and self._saved_cursor is not None:
            viewport.setCursor(self._saved_cursor)
            self._saved_cursor = None


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ClipboardGuard(QObject):
    """Puts hidden values on the clipboard and takes them off again."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.expire)
        self._held: str | None = None  # SHA-256 of what we put there

    def copy(self, value: str, *, clear_after_seconds: int) -> None:
        """Put ``value`` on the clipboard; clear it after the timeout (0 = never)."""
        mime = QMimeData()
        mime.setText(value)
        never = struct.pack("<I", 0)
        for fmt in HISTORY_EXCLUSION_FORMATS:
            mime.setData(fmt, never)
        QGuiApplication.clipboard().setMimeData(mime)
        self._held = _digest(value)
        if clear_after_seconds > 0:
            self.timer.start(clear_after_seconds * 1000)
        else:
            self.timer.stop()

    def expire(self) -> None:
        """Clear the clipboard if it still holds the value this guard copied."""
        self.clear_now()

    def clear_now(self) -> None:
        """Same as :meth:`expire`, for the lock path: wipe only our own value."""
        self.timer.stop()
        held, self._held = self._held, None
        if held is None:
            return
        clipboard = QGuiApplication.clipboard()
        if _digest(clipboard.text()) == held:
            clipboard.clear()
