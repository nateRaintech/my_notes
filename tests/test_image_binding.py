"""The app binds vault images at unlock and sweeps orphans first (#101)."""

import os

import pytest

from core.crypto import KdfParams
from core.images import ImageStore
from core.repository import Repository
from core.vault import Vault

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

import app as app_module  # noqa: E402
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
