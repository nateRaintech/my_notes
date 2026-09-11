"""Writing a note to HTML and PDF files (#105).

Both formats render through :func:`ui.note_render.render_document`, the same
vault-backed document the preview uses, so an export looks like the preview.

* **HTML** is one self-contained file. A browser can't read the vault, so each
  ``mnimg:`` image becomes a ``data:`` URI holding its original stored bytes,
  at the note's display width. :func:`note_html` takes the image source as a
  parameter, so the Outlook export (#110) can use ``cid:`` references to
  embedded attachments instead.
* **PDF** is US Letter with 0.75" margins and Qt's page-number footer, and has
  the note's title in its metadata. It is written at 96 dpi, so widths match
  the preview, with images rendered at 2x so they print sharply. The text is
  real, selectable text.

Per CLAUDE.md's layering, the UI may import Qt freely; ``core/`` must never
import this module.
"""

from __future__ import annotations

import base64
import html
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter

from core.image_refs import parse_url
from ui.note_render import render_document

if TYPE_CHECKING:
    from core.images import ImageRecord, ImageStore

#: Logical pixels per inch in the PDF — 96 matches the screen, so widths agree.
PDF_RESOLUTION = 96
#: Device pixels per logical pixel for images in the PDF, so they print sharply.
PDF_IMAGE_RATIO = 2.0
PDF_MARGIN_INCHES = 0.75

_IMG_TAG = re.compile(r"<img\b[^>]*>")
_SRC_ATTR = re.compile(r'\bsrc="([^"]*)"')


def data_uri(record: ImageRecord) -> str:
    """A ``data:`` URI carrying ``record``'s original bytes."""
    payload = base64.b64encode(record.data).decode("ascii")
    return f"data:{record.mime};base64,{payload}"


def note_html(
    markdown: str,
    store: ImageStore | None,
    *,
    title: str,
    image_src: Callable[[ImageRecord], str] = data_uri,
) -> str:
    """The note as a complete HTML document, with vault images resolved."""
    source = render_document(markdown, store).toHtml()
    source = source.replace("<head>", f"<head><title>{html.escape(title)}</title>", 1)
    return _IMG_TAG.sub(lambda match: _rewrite_img(match.group(0), store, image_src), source)


def _rewrite_img(
    tag: str, store: ImageStore | None, image_src: Callable[[ImageRecord], str]
) -> str:
    src = _SRC_ATTR.search(tag)
    if src is None:
        return tag
    parsed = parse_url(html.unescape(src.group(1)))
    if parsed is None:
        return tag  # not a vault image; leave it alone
    image_id, width = parsed
    record = store.get(image_id) if store is not None else None
    if record is None:
        new_src = ""  # the browser shows the alt text
    else:
        new_src = image_src(record)
        width = width or record.width
    tag = tag[: src.start(1)] + html.escape(new_src, quote=True) + tag[src.end(1) :]
    if width is not None:
        tag = tag.replace("<img", f'<img width="{width}"', 1)
    return tag


def write_html(
    markdown: str,
    store: ImageStore | None,
    path: str | os.PathLike[str],
    *,
    title: str,
) -> None:
    """Write the note to ``path`` as self-contained UTF-8 HTML."""
    Path(path).write_text(note_html(markdown, store, title=title), encoding="utf-8")


def write_pdf(
    markdown: str,
    store: ImageStore | None,
    path: str | os.PathLike[str],
    *,
    title: str,
) -> None:
    """Write the note to ``path`` as a Letter-size PDF; ``OSError`` on failure."""
    target = Path(path)
    if not target.parent.is_dir():
        raise OSError(f"The folder for {target.name} doesn't exist")

    document = render_document(markdown, store, device_pixel_ratio=PDF_IMAGE_RATIO)
    writer = QPdfWriter(str(target))
    margins = QMarginsF(PDF_MARGIN_INCHES, PDF_MARGIN_INCHES, PDF_MARGIN_INCHES, PDF_MARGIN_INCHES)
    writer.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.Letter),
            QPageLayout.Orientation.Portrait,
            margins,
            QPageLayout.Unit.Inch,
        )
    )
    writer.setResolution(PDF_RESOLUTION)
    writer.setTitle(title)
    writer.setCreator("my_notes")
    document.print_(writer)
    del writer  # the file is finished and closed when the writer is destroyed

    if not target.is_file() or target.stat().st_size == 0:
        raise OSError(f"Couldn't write {target.name}")
