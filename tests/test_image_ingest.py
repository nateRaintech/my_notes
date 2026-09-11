"""Tests for ``ui.image_ingest`` — images and files into the vault (#101)."""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.images import ImageStore

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import ui.image_ingest as ingest_module  # noqa: E402
from ui.image_ingest import (  # noqa: E402
    IngestError,
    ingest_file,
    ingest_image,
    initial_width,
    local_image_paths,
    natural_logical_width,
    wants_image_paste,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


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


def _image(width, height, color="red"):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


def test_natural_logical_width_divides_by_the_scale():
    assert natural_logical_width(1200, 1.5) == 800
    assert natural_logical_width(400, 1.0) == 400


@pytest.mark.parametrize(
    ("pixels", "ratio", "expected"),
    [(3840, 1.0, 800), (300, 1.0, 300), (1200, 1.5, 800), (900, 1.5, 600)],
)
def test_initial_width_caps_at_800_logical_px(pixels, ratio, expected):
    assert initial_width(pixels, ratio) == expected


def test_ingest_image_stores_png_and_returns_markdown(qapp, store):
    markdown = ingest_image(store, _image(400, 200), device_pixel_ratio=1.0)
    assert markdown == "![screenshot](mnimg:1?w=400)"
    record = store.get(1)
    assert record.mime == "image/png"
    assert (record.width, record.height) == (400, 200)
    assert record.data.startswith(PNG_SIGNATURE)


def test_ingest_image_twice_stores_one_blob(qapp, store):
    first = ingest_image(store, _image(10, 10), device_pixel_ratio=1.0)
    second = ingest_image(store, _image(10, 10), device_pixel_ratio=1.0)
    assert first == second


def test_ingest_image_rejects_a_null_image(qapp, store):
    with pytest.raises(IngestError):
        ingest_image(store, QImage(), device_pixel_ratio=1.0)


def test_ingest_file_keeps_jpeg_bytes(qapp, store, tmp_path):
    path = tmp_path / "holiday photo.jpg"
    assert _image(64, 32).save(str(path), "JPEG")
    markdown = ingest_file(store, path, device_pixel_ratio=1.0)
    assert markdown == "![holiday photo](mnimg:1?w=64)"
    record = store.get(1)
    assert record.mime == "image/jpeg"
    assert record.data == path.read_bytes()


def test_ingest_file_reencodes_other_formats_as_png(qapp, store, tmp_path):
    path = tmp_path / "old.bmp"
    assert _image(8, 8).save(str(path), "BMP")
    ingest_file(store, path, device_pixel_ratio=1.0)
    record = store.get(1)
    assert record.mime == "image/png"
    assert record.data.startswith(PNG_SIGNATURE)


def test_ingest_file_rejects_something_that_is_not_an_image(qapp, store, tmp_path):
    path = tmp_path / "fake.png"
    path.write_bytes(b"not an image at all")
    with pytest.raises(IngestError, match="fake.png"):
        ingest_file(store, path, device_pixel_ratio=1.0)


def test_ingest_file_rejects_oversized_files(qapp, store, tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_module, "MAX_IMAGE_BYTES", 10)
    path = tmp_path / "big.png"
    assert _image(50, 50).save(str(path), "PNG")
    with pytest.raises(IngestError, match="larger than"):
        ingest_file(store, path, device_pixel_ratio=1.0)


def test_local_image_paths_needs_every_url_to_be_a_local_image(qapp, tmp_path):
    png, jpg, txt = tmp_path / "a.png", tmp_path / "b.JPG", tmp_path / "c.txt"
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(png)), QUrl.fromLocalFile(str(jpg))])
    expected = [QUrl.fromLocalFile(str(p)).toLocalFile() for p in (png, jpg)]
    assert local_image_paths(mime) == expected

    mime.setUrls([QUrl.fromLocalFile(str(png)), QUrl.fromLocalFile(str(txt))])
    assert local_image_paths(mime) == []

    mime.setUrls([QUrl("https://example.com/a.png")])
    assert local_image_paths(mime) == []


def test_wants_image_paste_lets_text_win(qapp):
    image_only = QMimeData()
    image_only.setImageData(_image(4, 4))
    assert wants_image_paste(image_only) is True

    # Word / Excel / Outlook put text AND a picture of it on the clipboard.
    both = QMimeData()
    both.setImageData(_image(4, 4))
    both.setText("a1\tb1")
    assert wants_image_paste(both) is False

    text_only = QMimeData()
    text_only.setText("hello")
    assert wants_image_paste(text_only) is False
