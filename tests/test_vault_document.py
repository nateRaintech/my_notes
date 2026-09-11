"""Tests for ``ui.vault_document`` — rendering vault images in the preview (#101)."""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.images import ImageStore

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtGui import QColor, QImage, QTextDocument  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.image_ingest import png_bytes  # noqa: E402
from ui.vault_document import ImageCache, VaultTextDocument  # noqa: E402

IMAGE = QTextDocument.ResourceType.ImageResource


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


class CountingStore:
    """Wraps a store and counts ``get`` calls, to observe cache hits."""

    def __init__(self, store):
        self._store = store
        self.gets = 0

    def get(self, image_id):
        self.gets += 1
        return self._store.get(image_id)


def _image(width, height):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    return image


def _add(store, width=400, height=200):
    return store.add(png_bytes(_image(width, height)), "image/png", width, height)


def _doc(store, ratio=1.0):
    doc = VaultTextDocument(device_pixel_ratio=lambda: ratio)
    doc.set_store(store)
    return doc


def test_scales_to_the_requested_width(qapp, store):
    record = _add(store)
    image = _doc(store).loadResource(IMAGE, QUrl(f"mnimg:{record.id}?w=100"))
    assert (image.width(), image.height()) == (100, 50)
    assert image.devicePixelRatio() == 1.0


def test_renders_sharp_on_a_scaled_display(qapp, store):
    record = _add(store)
    image = _doc(store, ratio=1.5).loadResource(IMAGE, QUrl(f"mnimg:{record.id}?w=100"))
    # 150 device pixels, tagged 1.5, so Qt lays it out at 100 logical px.
    assert image.width() == 150
    assert image.devicePixelRatio() == 1.5
    assert image.deviceIndependentSize().width() == 100


def test_no_width_means_one_image_pixel_per_device_pixel(qapp, store):
    record = _add(store)
    image = _doc(store, ratio=2.0).loadResource(IMAGE, QUrl(f"mnimg:{record.id}"))
    assert image.width() == 400
    assert image.deviceIndependentSize().width() == 200


class FailingStore:
    """A store whose connection has gone away, as after ``vault.lock()``."""

    def get(self, image_id):
        raise sqlcipher.ProgrammingError("Cannot operate on a closed database.")


@pytest.mark.parametrize("width", ["1000000", "9" * 25])
def test_a_huge_typed_width_renders_bounded(qapp, store, width):
    record = _add(store)
    doc = _doc(store)
    image = doc.loadResource(IMAGE, QUrl(f"mnimg:{record.id}?w={width}"))
    assert image is not doc.placeholder()
    assert not image.isNull()
    assert image.width() <= 16384
    assert image.width() == 400 * 4  # never more than 4x the natural width


def test_repeated_renders_hit_the_cache(qapp, store):
    record = _add(store)
    counting = CountingStore(store)
    doc = _doc(counting)
    url = QUrl(f"mnimg:{record.id}?w=100")
    doc.loadResource(IMAGE, url)
    doc.loadResource(IMAGE, url)
    assert counting.gets == 1


def test_repeated_natural_size_renders_hit_the_cache(qapp, store):
    record = _add(store)
    counting = CountingStore(store)
    doc = _doc(counting)
    doc.loadResource(IMAGE, QUrl(f"mnimg:{record.id}"))
    doc.loadResource(IMAGE, QUrl(f"mnimg:{record.id}"))
    assert counting.gets == 1


def test_a_new_width_decodes_again(qapp, store):
    record = _add(store)
    counting = CountingStore(store)
    doc = _doc(counting)
    doc.loadResource(IMAGE, QUrl(f"mnimg:{record.id}?w=100"))
    doc.loadResource(IMAGE, QUrl(f"mnimg:{record.id}?w=200"))
    assert counting.gets == 2


def test_the_cache_holds_only_scaled_renderings(qapp, store):
    record = _add(store)
    doc = _doc(store)
    doc.loadResource(IMAGE, QUrl(f"mnimg:{record.id}?w=100"))
    doc.loadResource(IMAGE, QUrl(f"mnimg:{record.id}"))
    assert len(doc.cache) == 2
    assert doc.cache.total_bytes == (100 * 50 + 400 * 200) * 4  # no natural decode


def test_natural_image_decodes_on_demand_without_caching(qapp, store):
    record = _add(store)
    doc = _doc(store)
    natural = doc.natural_image(record.id)
    assert natural.size().toTuple() == (400, 200)
    assert len(doc.cache) == 0


def test_a_failing_store_shows_the_placeholder(qapp):
    doc = _doc(FailingStore())
    assert doc.loadResource(IMAGE, QUrl("mnimg:1?w=100")) is doc.placeholder()
    assert doc.natural_image(1) is None


def test_unknown_or_unbound_images_show_the_placeholder(qapp, store):
    doc = _doc(store)
    assert doc.loadResource(IMAGE, QUrl("mnimg:999")) is doc.placeholder()
    unbound = VaultTextDocument()
    assert unbound.loadResource(IMAGE, QUrl("mnimg:1")) is unbound.placeholder()


def test_set_store_clears_decoded_images(qapp, store):
    record = _add(store)
    doc = _doc(store)
    doc.loadResource(IMAGE, QUrl(f"mnimg:{record.id}?w=100"))
    assert len(doc.cache) > 0
    doc.set_store(None)
    assert len(doc.cache) == 0
    assert doc.natural_image(record.id) is None


def test_set_markdown_renders_through_the_vault(qapp, store):
    record = _add(store)
    doc = _doc(store)
    doc.setMarkdown(f"![s](mnimg:{record.id}?w=100)")
    doc.size()  # force layout
    assert len(doc.cache) == 1  # just the 100 px rendering; the natural isn't kept


def test_image_cache_evicts_least_recently_used_by_bytes(qapp):
    tiny = _image(10, 10)  # 400 bytes as RGB32
    cache = ImageCache(budget_bytes=900)
    cache.put("a", tiny)
    cache.put("b", tiny)
    cache.get("a")  # "a" is now most recent
    cache.put("c", tiny)  # over budget: evict the least recent, "b"
    assert cache.get("b") is None
    assert cache.get("a") is not None
    assert cache.get("c") is not None
    assert cache.total_bytes == 800
