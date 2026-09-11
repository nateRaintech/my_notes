"""Rendering a note for output — the one renderer every export shares (#105).

Exports and Copy Text work from a **fresh** document built from the note's
Markdown, not from the live preview. The preview may be hidden, and its
images are sized for the screen's pixel ratio. :func:`render_document` uses
the same vault-backed :class:`~ui.vault_document.VaultTextDocument` as the
preview, so an export looks like the preview, and anything the preview can
draw (images now; tables and graphs later) exports with no extra work.

:func:`plain_text` walks that document rather than using
``QTextDocument.toPlainText``, which would leave an object-replacement
character where each image was and flatten every table cell onto its own
line.

Per CLAUDE.md's layering, the UI may import Qt freely; ``core/`` must never
import this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QTextBlock, QTextDocument, QTextFrame, QTextListFormat, QTextTable

from ui.vault_document import VaultTextDocument

if TYPE_CHECKING:
    from core.images import ImageStore

#: The character Qt puts in a block's text where an image (or other object) is.
OBJECT_REPLACEMENT = "￼"

# Qt's soft line separator inside a block.
_LINE_SEPARATOR = " "

_BULLETS = {
    QTextListFormat.Style.ListDisc,
    QTextListFormat.Style.ListCircle,
    QTextListFormat.Style.ListSquare,
}


def render_document(
    markdown: str,
    store: ImageStore | None,
    *,
    device_pixel_ratio: float = 1.0,
) -> VaultTextDocument:
    """A new document showing ``markdown``, with images from ``store``.

    ``store`` may be ``None``; images then render as the missing-image
    placeholder. ``device_pixel_ratio`` sets how many pixels images get per
    logical pixel (the PDF export uses 2.0 so images print sharply).
    """
    document = VaultTextDocument(device_pixel_ratio=lambda: device_pixel_ratio)
    document.set_store(store)
    document.setMarkdown(markdown)
    return document


def plain_text(document: QTextDocument) -> str:
    """The note's text as it reads: no Markdown, no images, no tables.

    List items keep their markers (``-`` for bullets, ``1.`` for numbers),
    indented two spaces per nesting level. Runs of blank lines collapse to one,
    and leading and trailing blank lines are dropped.
    """
    lines: list[str] = []
    _collect(document.rootFrame(), lines)

    result: list[str] = []
    for line in lines:
        if not line and (not result or not result[-1]):
            continue  # a leading blank, or a second blank in a row
        result.append(line)
    while result and not result[-1]:
        result.pop()
    return "\n".join(result)


def _collect(frame: QTextFrame, lines: list[str]) -> None:
    it = frame.begin()
    while not it.atEnd():
        child = it.currentFrame()
        if child is None:
            lines.append(_block_line(it.currentBlock()))
        elif not isinstance(child, QTextTable):
            _collect(child, lines)  # e.g. a quote; tables are skipped whole
        it += 1


def _block_line(block: QTextBlock) -> str:
    text = (
        block.text()
        .replace(OBJECT_REPLACEMENT, "")
        .replace(_LINE_SEPARATOR, "\n")
        .rstrip()
    )
    text_list = block.textList()
    if text_list is None or not text:
        return text
    list_format = text_list.format()
    marker = "-" if list_format.style() in _BULLETS else text_list.itemText(block)
    indent = "  " * max(0, list_format.indent() - 1)
    return f"{indent}{marker} {text}"
