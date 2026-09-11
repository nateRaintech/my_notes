"""The note's editable source pane, with image paste and drop (#101).

A plain :class:`QPlainTextEdit` except for :meth:`insertFromMimeData`, the one
hook Qt routes both Ctrl+V and drag & drop through. When the incoming data is an
image — or local image files — and a vault is bound (``image_store``), it is
stored in the vault and replaced by Markdown that shows it. Anything else, and
everything while no vault is bound, goes to Qt's default handling unchanged.

Dropped files go on their own line (:meth:`insert_on_own_line`); a clipboard
image goes at the caret, like any paste.

Failures never raise out of a paste — ``insertFromMimeData`` is a Qt virtual.
An unreadable or oversized file, or a store whose database fails underneath it,
is reported through :attr:`status_message` and the note is left untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QMimeData, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from ui.image_ingest import (
    IngestError,
    ingest_file,
    ingest_image,
    local_image_paths,
    wants_image_paste,
)

if TYPE_CHECKING:
    from core.images import ImageStore


class NoteSourceEdit(QPlainTextEdit):
    """Markdown source editor that turns pasted/dropped images into vault images."""

    #: A message for the status bar (e.g. why an image couldn't be added).
    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        #: The vault's image store, or ``None`` while no vault is bound.
        self.image_store: ImageStore | None = None

    def canInsertFromMimeData(self, source: QMimeData) -> bool:
        if self.image_store is not None and wants_image_paste(source):
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source: QMimeData) -> None:
        if self.image_store is None or not wants_image_paste(source):
            super().insertFromMimeData(source)
            return
        paths = local_image_paths(source)
        try:
            markdown = self._ingest(source, paths)
        except IngestError as error:
            self.status_message.emit(str(error))
            return
        except Exception as error:  # e.g. sqlite: never escape a Qt virtual
            self.status_message.emit(f"Couldn't add the image: {error}")
            return
        if paths:
            self.insert_on_own_line(markdown)
        else:
            # insertPlainText is a single undoable edit at the caret.
            self.insertPlainText(markdown)

    def insert_on_own_line(self, text: str) -> None:
        """Insert ``text`` at the caret, starting a new line if mid-line.

        One undoable edit. A selection is replaced, as by any insert.
        """
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.removeSelectedText()
        if not cursor.atBlockStart():
            cursor.insertText("\n")
        cursor.insertText(text)
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _ingest(self, source: QMimeData, paths: list[str]) -> str:
        assert self.image_store is not None
        ratio = self.devicePixelRatioF()
        if paths:
            return "\n\n".join(
                ingest_file(self.image_store, path, device_pixel_ratio=ratio)
                for path in paths
            )
        image = source.imageData()
        if isinstance(image, QPixmap):
            image = image.toImage()
        if not isinstance(image, QImage):
            raise IngestError("The clipboard doesn't hold a readable image")
        return ingest_image(self.image_store, image, device_pixel_ratio=ratio)
