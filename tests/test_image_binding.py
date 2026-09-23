"""The app binds vault images at unlock and sweeps orphans first (#101)."""

import os
import sys

import pytest

from core.crypto import KdfParams
from core.hidden_text import HiddenTextStore
from core.images import ImageStore
from core.repository import Repository
from core.vault import Vault

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import app as app_module  # noqa: E402
from ui.image_ingest import png_bytes  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def vault(tmp_path):
    v = Vault.create(tmp_path / "notes.vault", PASSWORD, FAST)
    try:
        yield v
    finally:
        v.lock()


def test_bind_vault_sweeps_orphans_and_keeps_referenced_images(qapp, vault):
    store = ImageStore(vault.connection)
    kept = store.add(b"kept", "image/png", 1, 1)
    orphan = store.add(b"orphan", "image/png", 1, 1)
    Repository(vault.connection).create_note(title="n", body=f"![s](mnimg:{kept.id})")

    app_module._bind_vault(MainWindow(), vault)

    assert store.get(orphan.id) is None
    assert store.get(kept.id) is not None


def test_bind_vault_binds_images_to_the_window(qapp, vault):
    window = MainWindow()
    app_module._bind_vault(window, vault)
    assert window.image_store is not None
    assert window.preview_document._store is window.image_store


def test_a_failed_sweep_never_blocks_unlock(qapp, vault, monkeypatch):
    def explode(self):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(ImageStore, "sweep_orphans", explode)
    window = MainWindow()
    repository = app_module._bind_vault(window, vault)
    assert window.repository is repository
    assert window.image_store is not None


def _png(color):
    image = QImage(40, 20, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return png_bytes(image)


def test_lock_never_queries_the_closed_database(qapp, tmp_path, monkeypatch):
    vault = Vault.create(tmp_path / "notes.vault", PASSWORD, FAST)
    store = ImageStore(vault.connection)
    repo = Repository(vault.connection)
    notes = [
        repo.create_note(
            title=color, body=f"![s](mnimg:{store.add(_png(color), 'image/png', 40, 20).id})"
        )
        for color in ("red", "blue")
    ]
    window = MainWindow()
    app_module._bind_vault(window, vault)
    for note in notes:
        window.load_note(note)
        qapp.processEvents()
    # As if the budget had evicted them: the next render has to go to the store.
    window.preview_document.cache.clear()

    queried_while_locked = []
    real_get = ImageStore.get

    def spy_get(self, image_id):
        if vault.is_locked:
            queried_while_locked.append(image_id)
        return real_get(self, image_id)

    escaped = []
    monkeypatch.setattr(ImageStore, "get", spy_get)
    monkeypatch.setattr(sys, "excepthook", lambda *exc_info: escaped.append(exc_info))

    # The real lock order (app._lock_now, IdleLockController): close, then clear.
    window.flush_pending()
    vault.lock()
    window.lock_session()
    qapp.processEvents()

    assert escaped == []
    assert queried_while_locked == []
    assert len(window.preview_document.cache) == 0
    assert window.image_store is None


def test_relock_rebinds_images(qapp, tmp_path):
    path = tmp_path / "notes.vault"
    vault = Vault.create(path, PASSWORD, FAST)
    window = MainWindow()
    app_module._bind_vault(window, vault)
    vault.lock()
    window.lock_session()
    assert window.image_store is None

    reopened = Vault(path)
    reopened.unlock(PASSWORD)
    try:
        app_module._bind_vault(window, reopened)
        assert window.image_store is not None
    finally:
        reopened.lock()


def test_bind_vault_sweeps_and_binds_hidden_text(qapp, vault):
    hidden = HiddenTextStore(vault.connection)
    kept = hidden.add("kept")
    orphan = hidden.add("orphan")
    Repository(vault.connection).create_note(title="n", body=f"pw ![hidden](mnsec:{kept})")

    window = MainWindow()
    app_module._bind_vault(window, vault)

    assert hidden.get(orphan) is None
    assert hidden.get(kept) == "kept"
    assert window.hidden_store is not None


def test_a_failed_image_sweep_still_sweeps_hidden_text(qapp, vault, monkeypatch):
    def explode(self):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(ImageStore, "sweep_orphans", explode)
    orphan = HiddenTextStore(vault.connection).add("orphan")
    app_module._bind_vault(MainWindow(), vault)
    assert HiddenTextStore(vault.connection).get(orphan) is None
