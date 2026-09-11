"""Unit tests for ``core.images`` — images stored inside the vault (#101).

Pure Python, no Qt. The store never decodes image data, so plain byte strings
stand in for real PNGs here.
"""

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

import core.images as images_module
from core import schema
from core.images import ImageStore, SweepResult, extension_for
from core.repository import Repository


@pytest.fixture
def conn():
    c = sqlcipher.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    schema.migrate(c)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def store(conn):
    return ImageStore(conn)


def _count(conn):
    return conn.execute("SELECT count(*) FROM images").fetchone()[0]


def test_add_stores_and_returns_the_record(store):
    record = store.add(b"\x89PNG-a", "image/png", 400, 200)
    assert record.id > 0
    assert (record.mime, record.width, record.height) == ("image/png", 400, 200)
    assert record.byte_size == 6
    assert record.data == b"\x89PNG-a"
    assert len(record.sha256) == 64
    assert store.get(record.id) == record


def test_add_dedups_identical_bytes(conn, store):
    first = store.add(b"same", "image/png", 1, 1)
    second = store.add(b"same", "image/png", 1, 1)
    assert second.id == first.id
    assert _count(conn) == 1


def test_add_keeps_different_bytes_apart(store):
    assert store.add(b"a", "image/png", 1, 1).id != store.add(b"b", "image/png", 1, 1).id


def test_get_missing_is_none(store):
    assert store.get(999) is None


@pytest.mark.parametrize(
    ("data", "width", "height"),
    [(b"", 1, 1), (b"x", 0, 1), (b"x", 1, -1)],
)
def test_add_rejects_invalid_input(store, data, width, height):
    with pytest.raises(ValueError):
        store.add(data, "image/png", width, height)


def test_add_rejects_oversized_data(store, monkeypatch):
    monkeypatch.setattr(images_module, "MAX_IMAGE_BYTES", 4)
    with pytest.raises(ValueError, match="larger than"):
        store.add(b"12345", "image/png", 1, 1)


def test_sweep_removes_only_images_no_note_mentions(conn, store):
    repo = Repository(conn)
    kept = store.add(b"kept", "image/png", 1, 1)
    orphan = store.add(b"orphan!", "image/png", 1, 1)
    in_code = store.add(b"code", "image/png", 1, 1)
    repo.create_note(title="a", body=f"![s](mnimg:{kept.id}?w=100)")
    repo.create_note(title="b", body=f"```\n![s](mnimg:{in_code.id})\n```")

    result = store.sweep_orphans()

    assert result == SweepResult(count=1, freed_bytes=len(b"orphan!"))
    assert store.get(orphan.id) is None
    assert store.get(kept.id) is not None
    assert store.get(in_code.id) is not None  # liberal: a mention in code keeps it


def test_sweep_with_nothing_to_do(store):
    assert store.sweep_orphans() == SweepResult(count=0, freed_bytes=0)


def test_extension_for():
    assert extension_for("image/png") == "png"
    assert extension_for("image/jpeg") == "jpg"
    assert extension_for("image/gif") == "gif"
    assert extension_for("image/webp") == "webp"
    assert extension_for("application/x-unknown") == "bin"
