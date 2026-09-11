"""Tests for ``ui.preview_images`` — rendered-image lookup and width edits (#101)."""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.image_refs import find_refs
from core.images import ImageStore

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QColor, QImage, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QTextEdit  # noqa: E402

from ui.image_ingest import png_bytes  # noqa: E402
from ui.preview_images import (  # noqa: E402
    apply_image_width,
    image_at,
    image_rect,
    rendered_images,
    utf16_offset,
)
from ui.vault_document import VaultTextDocument  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def store():
    conn = sqlcipher.connect(":memory:")
    schema.migrate(conn)
    try:
        yield ImageStore(conn)
    finally:
        conn.close()


def _add(store, color="red"):
    image = QImage(400, 200, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return store.add(png_bytes(image), "image/png", 400, 200)


def _preview(qapp, store, markdown):
    view = QTextEdit()
    doc = VaultTextDocument(view, device_pixel_ratio=lambda: 1.0)
    doc.set_store(store)
    view.setDocument(doc)
    view.setReadOnly(True)
    view.resize(700, 600)
    view.show()
    doc.setMarkdown(markdown)
    qapp.processEvents()
    return view


def test_rendered_images_lists_vault_images_in_order(qapp, store):
    a, b = _add(store, "red"), _add(store, "blue")
    md = (
        f"![a](mnimg:{a.id}?w=100)\n\n![web](http://example.com/x.png)\n\n"
        f"```\n![c](mnimg:{a.id})\n```\n\n![b](mnimg:{b.id})"
    )
    # Bound to a name: an inline `_preview(...).document()` lets Python garbage
    # collect the (unparented, top-level) QTextEdit before use, deleting its document.
    view = _preview(qapp, store, md)
    images = rendered_images(view.document())
    assert [(i.image_id, i.width, i.ordinal) for i in images] == [(a.id, 100, 0), (b.id, None, 1)]


def test_rendered_images_counts_adjacent_duplicates_separately(qapp, store):
    a = _add(store)
    md = f"![a](mnimg:{a.id}?w=50)![a](mnimg:{a.id}?w=50)"
    view = _preview(qapp, store, md)
    images = rendered_images(view.document())
    assert [i.ordinal for i in images] == [0, 1]
    assert images[0].position != images[1].position


def test_image_rect_matches_the_rendered_size(qapp, store):
    a = _add(store)
    view = _preview(qapp, store, f"![a](mnimg:{a.id}?w=100)")
    (image,) = rendered_images(view.document())
    rect = image_rect(view, image)
    assert (rect.width(), rect.height()) == (100, 50)


def test_image_at_finds_the_image_under_a_point(qapp, store):
    a, b = _add(store, "red"), _add(store, "blue")
    view = _preview(qapp, store, f"![a](mnimg:{a.id}?w=100)\n\n![b](mnimg:{b.id}?w=100)")
    first, second = rendered_images(view.document())

    assert image_at(view, image_rect(view, second).center()).ordinal == 1
    assert image_at(view, image_rect(view, first).center()).ordinal == 0
    viewport = view.viewport()
    assert image_at(view, QPoint(viewport.width() - 2, viewport.height() - 2)) is None


def test_utf16_offset_counts_surrogate_pairs():
    assert utf16_offset("a😀b", 0) == 0
    assert utf16_offset("a😀b", 2) == 3
    assert utf16_offset("abc", 3) == 3


def _source(text):
    source = QPlainTextEdit()
    source.setPlainText(text)
    return source


def test_apply_image_width_replaces_only_the_url(qapp):
    source = _source("x ![s](mnimg:1?w=600) y")
    (ref,) = find_refs(source.toPlainText())
    apply_image_width(source, ref, 300)
    assert source.toPlainText() == "x ![s](mnimg:1?w=300) y"


def test_apply_image_width_none_resets_to_natural(qapp):
    source = _source("![s](mnimg:1?w=600)")
    (ref,) = find_refs(source.toPlainText())
    apply_image_width(source, ref, None)
    assert source.toPlainText() == "![s](mnimg:1)"


def test_apply_image_width_is_one_undo_step(qapp):
    source = _source("![s](mnimg:1?w=600)")
    (ref,) = find_refs(source.toPlainText())
    apply_image_width(source, ref, 300)
    source.undo()
    assert source.toPlainText() == "![s](mnimg:1?w=600)"


def test_apply_image_width_after_an_emoji(qapp):
    source = _source("😀 ![s](mnimg:1?w=600) end")
    (ref,) = find_refs(source.toPlainText())
    apply_image_width(source, ref, 300)
    assert source.toPlainText() == "😀 ![s](mnimg:1?w=300) end"


def test_apply_image_width_leaves_the_caret_alone(qapp):
    source = _source("![s](mnimg:1?w=600) tail")
    cursor = source.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    source.setTextCursor(cursor)
    (ref,) = find_refs(source.toPlainText())
    apply_image_width(source, ref, 30)
    assert source.textCursor().position() == len(source.toPlainText())
