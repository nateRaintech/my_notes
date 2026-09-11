"""Tests for ``ui.note_export`` — HTML and PDF files (#105)."""

import base64
import os
import re
import stat

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.images import ImageStore

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pypdf = pytest.importorskip("pypdf")

from PySide6.QtGui import QColor, QFontDatabase, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.image_ingest import png_bytes  # noqa: E402
from ui.note_export import note_html, write_html, write_pdf  # noqa: E402


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


@pytest.fixture
def record(store):
    image = QImage(400, 200, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    return store.add(png_bytes(image), "image/png", 400, 200)


def _note(record):
    return (
        f"# Title\n\nSome **bold** text.\n\n![s](mnimg:{record.id}?w=300)\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n"
    )


def test_note_html_embeds_images_as_data_uris(qapp, store, record):
    html = note_html(_note(record), store, title="Title")
    assert "mnimg:" not in html
    payload = re.search(r'src="data:image/png;base64,([^"]+)"', html)
    assert payload is not None
    assert base64.b64decode(payload.group(1)) == record.data
    assert 'width="300"' in html
    assert "<table" in html
    assert "<title>Title</title>" in html


def test_note_html_uses_the_natural_width_when_the_ref_has_none(qapp, store, record):
    html = note_html(f"![s](mnimg:{record.id})", store, title="t")
    assert 'width="400"' in html


def test_note_html_blanks_a_missing_image_but_keeps_its_alt(qapp, store):
    html = note_html("![gone](mnimg:999)", store, title="t")
    assert 'src=""' in html
    assert 'alt="gone"' in html


def test_note_html_blanks_an_image_the_store_cannot_fetch(qapp):
    class FailingStore:
        def get(self, image_id):
            raise RuntimeError("the vault connection is closed")

    html = note_html("![gone](mnimg:1)", FailingStore(), title="t")
    assert 'src=""' in html
    assert 'alt="gone"' in html


def test_note_html_accepts_a_custom_image_source(qapp, store, record):
    html = note_html(_note(record), store, title="t", image_src=lambda r: f"cid:img{r.id}")
    assert f'src="cid:img{record.id}"' in html


def test_note_html_escapes_the_title(qapp, store):
    assert "<title>A &amp; B</title>" in note_html("x", store, title="A & B")


def test_write_html_writes_utf8(qapp, store, record, tmp_path):
    path = tmp_path / "note.html"
    write_html(_note(record) + "\nCafé", store, path, title="Café")
    content = path.read_text(encoding="utf-8")
    assert "<title>Café</title>" in content
    assert "Café" in content.split("</title>", 1)[1]


def test_write_pdf_writes_one_letter_page_with_the_image(qapp, store, record, tmp_path):
    path = tmp_path / "note.pdf"
    write_pdf(_note(record), store, path, title="Title")

    reader = pypdf.PdfReader(str(path))
    assert len(reader.pages) == 1
    page = reader.pages[0]
    assert (float(page.mediabox.width), float(page.mediabox.height)) == (612.0, 792.0)
    assert len(page.images) == 1
    assert reader.metadata.title == "Title"


def test_write_pdf_text_is_real_text(qapp, store, record, tmp_path):
    if not QFontDatabase.families():
        pytest.skip("this Qt platform has no fonts, so the PDF has no text")
    path = tmp_path / "note.pdf"
    write_pdf(_note(record), store, path, title="Title")
    text = " ".join(pypdf.PdfReader(str(path)).pages[0].extract_text().split())
    assert "Some bold text." in text


def test_write_pdf_numbers_the_pages_of_a_long_note(qapp, store, tmp_path):
    path = tmp_path / "long.pdf"
    write_pdf("\n\n".join(f"Paragraph {i}." for i in range(400)), store, path, title="Long")

    reader = pypdf.PdfReader(str(path))
    assert len(reader.pages) > 1
    if not QFontDatabase.families():
        pytest.skip("this Qt platform has no fonts, so the PDF has no text")
    assert "2" in reader.pages[1].extract_text()


def test_write_pdf_to_a_missing_folder_raises(qapp, store, tmp_path):
    with pytest.raises(OSError):
        write_pdf("x", store, tmp_path / "no-such-folder" / "note.pdf", title="t")


def test_write_pdf_over_a_read_only_file_raises_and_keeps_the_original(qapp, store, tmp_path):
    path = tmp_path / "locked.pdf"
    original = b"the original file" * 3
    path.write_bytes(original)
    os.chmod(path, stat.S_IREAD)
    try:
        with pytest.raises(OSError):
            write_pdf("# Title\n\nSome text.", store, path, title="Title")
        assert path.read_bytes() == original
    finally:
        os.chmod(path, stat.S_IWRITE)
