"""Tests for ``ui.note_render`` — rendering a note for output (#105)."""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.images import ImageStore

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.image_ingest import png_bytes  # noqa: E402
from ui.note_render import OBJECT_REPLACEMENT, plain_text, render_document  # noqa: E402
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


def _add_image(store):
    image = QImage(400, 200, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    return store.add(png_bytes(image), "image/png", 400, 200)


def test_render_document_is_a_fresh_vault_document(qapp, store):
    record = _add_image(store)
    document = render_document(f"![s](mnimg:{record.id}?w=100)", store)
    assert isinstance(document, VaultTextDocument)
    document.size()  # force layout, which loads the image through the vault
    assert len(document.cache) == 1


def test_plain_text_reads_like_the_note(qapp, store):
    record = _add_image(store)
    markdown = (
        f"# Title\n\nSome **bold** text.\n\n![s](mnimg:{record.id}?w=300)\n\n"
        "- one\n- two\n\n1. first\n2. second\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncode line\n```\n\nEnd.\n"
    )
    text = plain_text(render_document(markdown, store))
    assert text == (
        "Title\nSome bold text.\n\n- one\n- two\n1. first\n2. second\n\ncode line\nEnd."
    )
    assert OBJECT_REPLACEMENT not in text


def test_plain_text_indents_nested_lists(qapp):
    text = plain_text(render_document("- outer\n  - inner\n- back\n", None))
    assert text == "- outer\n  - inner\n- back"


def test_plain_text_keeps_code_indentation(qapp):
    text = plain_text(render_document("```\n    indented\n```\n", None))
    assert text == "    indented"


def test_plain_text_without_a_store_still_drops_images(qapp):
    text = plain_text(render_document("before\n\n![s](mnimg:5)\n\nafter", None))
    assert text == "before\n\nafter"


def test_plain_text_of_an_empty_note(qapp):
    assert plain_text(render_document("", None)) == ""
