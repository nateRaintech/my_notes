"""Images as the preview shows them, and editing their width in the source (#101).

The preview is a rendering of the active tab's Markdown. To act on an image the
user clicked there — copy it, reset its size, and (slice 2, #102) drag it — this
module maps the click to a :class:`PreviewImage`. Callers then find the matching
text with :func:`core.image_refs.resolve_ref`, and :func:`apply_image_width`
rewrites just that URL as one undoable edit.

Two coordinate systems meet here, which is why this lives in one place:

* :func:`image_rect` and :func:`image_at` work in the preview's **viewport**
  coordinates — what ``customContextMenuRequested`` reports for a scroll area,
  and what ``QTextEdit.cursorRect`` returns.
* :class:`~core.image_refs.ImageRef` spans are **Python string indices**, while
  ``QTextDocument`` positions count UTF-16 code units; :func:`utf16_offset`
  converts, so an emoji earlier in the note can't shift an edit.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QUrl
from PySide6.QtGui import QImage, QTextCursor, QTextDocument
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit

from core.image_refs import ImageRef, image_url, parse_url


@dataclass(frozen=True)
class PreviewImage:
    """One ``mnimg:`` image in the rendered preview.

    ``ordinal`` is its index among the rendered vault images — the value
    :func:`core.image_refs.resolve_ref` expects. ``position`` is the document
    position of the image's placeholder character.
    """

    image_id: int
    width: int | None
    ordinal: int
    position: int
    url: str


def rendered_images(document: QTextDocument) -> list[PreviewImage]:
    """Every ``mnimg:`` image in ``document``, in order."""
    images: list[PreviewImage] = []
    block = document.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid() and fragment.charFormat().isImageFormat():
                url = fragment.charFormat().toImageFormat().name()
                parsed = parse_url(url)
                if parsed is not None:
                    # Identical adjacent images share one fragment; each
                    # character in it is a separate image.
                    for offset in range(fragment.length()):
                        images.append(
                            PreviewImage(
                                image_id=parsed[0],
                                width=parsed[1],
                                ordinal=len(images),
                                position=fragment.position() + offset,
                                url=url,
                            )
                        )
            it += 1
        block = block.next()
    return images


def image_rect(view: QTextEdit, image: PreviewImage) -> QRect:
    """Where ``image`` is drawn in ``view``'s viewport (empty if unknown)."""
    cursor = QTextCursor(view.document())
    cursor.setPosition(image.position)
    caret = view.cursorRect(cursor)
    rendered = view.document().resource(
        QTextDocument.ResourceType.ImageResource, QUrl(image.url)
    )
    if not isinstance(rendered, QImage) or rendered.isNull():
        return QRect()
    return QRect(caret.topLeft(), rendered.deviceIndependentSize().toSize())


def image_at(view: QTextEdit, point: QPoint) -> PreviewImage | None:
    """The rendered image under ``point`` (viewport coordinates), if any."""
    for image in rendered_images(view.document()):
        if image_rect(view, image).contains(point):
            return image
    return None


def utf16_offset(text: str, index: int) -> int:
    """Python string index into ``text`` -> ``QTextDocument`` position."""
    return len(text[:index].encode("utf-16-le")) // 2


def apply_image_width(source: QPlainTextEdit, ref: ImageRef, width: int | None) -> None:
    """Set ``ref``'s display width in ``source`` (``None``: natural size).

    ``ref`` must come from ``source``'s current text. Replaces only the URL span,
    through a separate cursor inside one edit block: Ctrl+Z undoes it in one
    step, the user's caret stays where it was, and auto-save picks it up like
    any other edit. (``setPlainText`` would wipe the undo stack.)
    """
    text = source.toPlainText()
    cursor = QTextCursor(source.document())
    cursor.setPosition(utf16_offset(text, ref.url_start))
    cursor.setPosition(utf16_offset(text, ref.url_end), QTextCursor.MoveMode.KeepAnchor)
    cursor.beginEditBlock()
    try:
        cursor.insertText(image_url(ref.image_id, width))
    finally:
        cursor.endEditBlock()
