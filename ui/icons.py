"""Small icons painted at runtime, so the theme can colour them.

Qt draws a tab's close button and a dock's title-bar buttons from the *native*
style's pixmaps. Those pixmaps are dark glyphs meant for a light window, so under
the dark theme they become invisible against the dark chrome — the same bug as
the unreadable tab labels (#98), just in icon form.

A stylesheet can override them only with ``image: url(...)``, and a QSS url is
resolved against the process's working directory, which a frozen ``--onefile``
exe cannot rely on. Painting the two glyphs here instead needs no asset, no image
plugin, and no path resolution: the colour is simply passed in by whoever applies
the theme.

Per CLAUDE.md's layering, the UI may import Qt freely; ``core/`` never imports
this module.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

#: Glyph colours for the two themes. The dark value sits a little below the body
#: text so the buttons read as chrome rather than as content.
DARK_GLYPH = "#b0b0b0"
LIGHT_GLYPH = "#404040"


def glyph_color(theme: str) -> str:
    """The icon colour to use for a theme name."""
    return DARK_GLYPH if theme == "dark" else LIGHT_GLYPH


def _painter(size: int) -> tuple[QPixmap, QPainter]:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    return pixmap, painter


def _pen(color: str, width: float) -> QPen:
    pen = QPen(QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


def cross_icon(color: str, size: int = 12) -> QIcon:
    """An X, for close buttons."""
    pixmap, painter = _painter(size)
    try:
        painter.setPen(_pen(color, 1.4))
        near, far = size * 0.28, size * 0.72
        painter.drawLine(QPointF(near, near), QPointF(far, far))
        painter.drawLine(QPointF(far, near), QPointF(near, far))
    finally:
        painter.end()
    return QIcon(pixmap)


def float_icon(color: str, size: int = 12) -> QIcon:
    """Two offset rectangles, for a dock's float/restore button."""
    pixmap, painter = _painter(size)
    try:
        painter.setPen(_pen(color, 1.1))
        painter.drawRect(QRectF(size * 0.16, size * 0.34, size * 0.5, size * 0.46))
        # The back rectangle, drawn as its two visible edges.
        painter.drawLine(QPointF(size * 0.34, size * 0.2), QPointF(size * 0.84, size * 0.2))
        painter.drawLine(QPointF(size * 0.84, size * 0.2), QPointF(size * 0.84, size * 0.66))
    finally:
        painter.end()
    return QIcon(pixmap)
