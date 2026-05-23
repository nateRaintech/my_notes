"""Behavioral tests for the create/unlock vault dialog and the launch wiring.

These drive :class:`ui.unlock_dialog.UnlockDialog` against a *real*
:class:`core.vault.Vault` in ``tmp_path`` (so the create/unlock/wrong-password
paths exercise actual SQLCipher + Argon2, not a mock), plus the
``app.default_vault_path`` resolution. Guarded by ``importorskip`` and run
headless via the ``offscreen`` Qt platform, matching the rest of the UI tests.

A fast Argon2 cost keeps key derivation cheap — the KDF itself is covered by
``test_crypto.py``.
"""

import os

import pytest

# Select the headless platform before any Qt import instantiates a plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from core.crypto import KdfParams  # noqa: E402
from core.vault import Vault  # noqa: E402
from ui.unlock_dialog import UnlockDialog  # noqa: E402

# Minimal valid Argon2 params (memory_cost floor is 8 * parallelism) — fast.
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="module")
def qapp():
    """A process-wide QApplication (singleton) for widget construction."""
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def vault_path(tmp_path):
    return tmp_path / "notes.vault"


def _make_existing_vault(path, password=PASSWORD):
    """Create an encrypted vault at ``path`` and lock it, leaving it on disk."""
    Vault.create(path, password, FAST).lock()


# -- mode detection ----------------------------------------------------------


def test_create_mode_when_no_vault(qapp, vault_path):
    dialog = UnlockDialog(vault_path, params=FAST)
    assert dialog.is_create_mode is True
    assert dialog.confirm_edit is not None


def test_unlock_mode_when_vault_exists(qapp, vault_path):
    _make_existing_vault(vault_path)
    dialog = UnlockDialog(vault_path, params=FAST)
    assert dialog.is_create_mode is False
    assert dialog.confirm_edit is None


def test_unlock_mode_when_only_meta_sidecar_present(qapp, vault_path):
    # A stray .meta sidecar (partial vault) must NOT be treated as a first run —
    # create mode would refuse to clobber it, so unlock mode is the honest state.
    meta = vault_path.parent / (vault_path.name + ".meta")
    meta.write_text("{}", encoding="utf-8")
    dialog = UnlockDialog(vault_path, params=FAST)
    assert dialog.is_create_mode is False


# -- create flow -------------------------------------------------------------


def test_create_with_matching_passwords_creates_unlocked_vault(qapp, vault_path):
    dialog = UnlockDialog(vault_path, params=FAST)
    dialog.password_edit.setText(PASSWORD)
    dialog.confirm_edit.setText(PASSWORD)

    assert dialog.attempt() is True
    try:
        assert dialog.vault is not None
        assert dialog.vault.is_locked is False
        assert vault_path.exists()  # a real encrypted file was written
        assert dialog.error_label.text() == ""
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        if dialog.vault is not None:
            dialog.vault.lock()


def test_create_empty_password_shows_error_and_creates_nothing(qapp, vault_path):
    dialog = UnlockDialog(vault_path, params=FAST)
    dialog.password_edit.setText("")
    dialog.confirm_edit.setText("")

    assert dialog.attempt() is False
    assert dialog.vault is None
    assert dialog.error_label.text() != ""
    assert not vault_path.exists()


def test_create_mismatched_passwords_shows_error_and_creates_nothing(qapp, vault_path):
    dialog = UnlockDialog(vault_path, params=FAST)
    dialog.password_edit.setText(PASSWORD)
    dialog.confirm_edit.setText("different")

    assert dialog.attempt() is False
    assert dialog.vault is None
    assert "match" in dialog.error_label.text().lower()
    assert not vault_path.exists()


# -- unlock flow -------------------------------------------------------------


def test_unlock_with_correct_password(qapp, vault_path):
    _make_existing_vault(vault_path)
    dialog = UnlockDialog(vault_path, params=FAST)
    dialog.password_edit.setText(PASSWORD)

    assert dialog.attempt() is True
    try:
        assert dialog.vault is not None
        assert dialog.vault.is_locked is False
        # The connection is live and usable (schema present).
        dialog.vault.connection.execute("SELECT count(*) FROM notes").fetchone()
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        if dialog.vault is not None:
            dialog.vault.lock()


def test_unlock_with_wrong_password_stays_open_and_can_retry(qapp, vault_path):
    _make_existing_vault(vault_path)
    dialog = UnlockDialog(vault_path, params=FAST)

    dialog.password_edit.setText("wrong password")
    assert dialog.attempt() is False
    assert dialog.vault is None
    assert dialog.error_label.text() != ""
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert vault_path.exists()  # the vault was not damaged

    # The dialog is still usable: the correct password now succeeds.
    dialog.password_edit.setText(PASSWORD)
    assert dialog.attempt() is True
    try:
        assert dialog.vault is not None
        assert dialog.vault.is_locked is False
    finally:
        if dialog.vault is not None:
            dialog.vault.lock()


# -- launch wiring -----------------------------------------------------------


def test_default_vault_path_honours_env_override(monkeypatch, tmp_path):
    import app

    override = tmp_path / "custom" / "my.vault"
    monkeypatch.setenv(app.VAULT_PATH_ENV, str(override))
    assert app.default_vault_path() == override


def test_default_vault_path_defaults_under_home(monkeypatch):
    import app

    monkeypatch.delenv(app.VAULT_PATH_ENV, raising=False)
    path = app.default_vault_path()
    assert path.name == "notes.vault"
    assert path.parent.name == ".my_notes"
