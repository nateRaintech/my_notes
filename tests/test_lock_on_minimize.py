"""Behavioral tests for lock-on-minimize (the final M5 Settings slice).

Three layers, mirroring ``tests/test_idle_lock.py``:

* :class:`ui.main_window.MainWindow.handle_window_state_change` -- the public seam
  that decides whether a minimise/restore transition should emit
  :attr:`~ui.main_window.MainWindow.lock_on_minimize_requested` /
  :attr:`~ui.main_window.MainWindow.restore_requested`. Driven directly (no real
  window events), so the decision is deterministic headless.
* The ``app`` helpers ``_lock_for_minimize`` / ``_reprompt_and_rebind`` over a real
  vault in ``tmp_path`` -- locking flushes + clears, re-prompt rebinds or quits.
* ``app._wire_lock_on_minimize`` end-to-end: a minimise locks and clears the
  session, a restore (re-prompt stubbed to succeed) rebinds a working session.

Guarded by ``importorskip`` and run headless via the ``offscreen`` Qt platform.
A fast Argon2 cost keeps key derivation cheap.
"""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.crypto import KdfParams
from core.repository import Repository
from core.settings import Settings
from core.vault import Vault

# Select the headless platform before any Qt import instantiates a plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

import app as app_module  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

# Minimal valid Argon2 params (memory_cost floor is 8 * parallelism) -- fast.
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="module")
def qapp():
    """A process-wide QApplication (singleton) for widget construction."""
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def repo():
    """A repository over a migrated, FK-enforcing in-memory connection."""
    conn = sqlcipher.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    schema.migrate(conn)
    try:
        yield Repository(conn)
    finally:
        conn.close()


# -- MainWindow.handle_window_state_change seam ------------------------------


def test_minimize_emits_lock_request_when_enabled(qapp, repo):
    window = MainWindow()
    window.settings = Settings(lock_on_minimize=True)
    window.bind_autosave(repo)
    fired = []
    window.lock_on_minimize_requested.connect(lambda: fired.append(True))

    window.handle_window_state_change(minimized=True)

    assert fired == [True]
    assert window._minimize_locked is True


def test_minimize_does_not_emit_when_disabled(qapp, repo):
    window = MainWindow()  # default settings: lock_on_minimize is False
    window.bind_autosave(repo)
    fired = []
    window.lock_on_minimize_requested.connect(lambda: fired.append(True))

    window.handle_window_state_change(minimized=True)

    assert fired == []
    assert window._minimize_locked is False


def test_minimize_does_not_emit_without_an_unlocked_session(qapp):
    window = MainWindow()
    window.settings = Settings(lock_on_minimize=True)
    # No bind_autosave, so repository is None -- the session is locked / not bound.
    fired = []
    window.lock_on_minimize_requested.connect(lambda: fired.append(True))

    window.handle_window_state_change(minimized=True)

    assert fired == []
    assert window._minimize_locked is False


def test_minimize_fires_once_until_restore(qapp, repo):
    window = MainWindow()
    window.settings = Settings(lock_on_minimize=True)
    window.bind_autosave(repo)
    fired = []
    window.lock_on_minimize_requested.connect(lambda: fired.append(True))

    window.handle_window_state_change(minimized=True)
    window.handle_window_state_change(minimized=True)  # still minimised; no re-fire

    assert fired == [True]


def test_restore_emits_after_minimize_lock(qapp, repo):
    window = MainWindow()
    window.settings = Settings(lock_on_minimize=True)
    window.bind_autosave(repo)
    window.handle_window_state_change(minimized=True)
    assert window._minimize_locked is True

    restored = []
    window.restore_requested.connect(lambda: restored.append(True))
    window.handle_window_state_change(minimized=False)

    assert restored == [True]
    assert window._minimize_locked is False


def test_restore_without_a_minimize_lock_is_silent(qapp, repo):
    window = MainWindow()
    window.settings = Settings(lock_on_minimize=True)
    window.bind_autosave(repo)
    restored = []
    window.restore_requested.connect(lambda: restored.append(True))

    window.handle_window_state_change(minimized=False)  # was never minimise-locked

    assert restored == []


# -- app._lock_for_minimize / _reprompt_and_rebind ---------------------------


def test_lock_for_minimize_flushes_locks_and_clears(qapp, tmp_path):
    path = tmp_path / "notes.vault"
    vault = Vault.create(path, PASSWORD, FAST)
    note = Repository(vault.connection).create_note(title="N", body="")
    window = MainWindow()
    # A long debounce so nothing auto-flushes -- the minimise flush must be what writes.
    window.bind_autosave(Repository(vault.connection), debounce=999)
    window.load_note(note)
    window.editor.source.setPlainText("pending edit")
    session = app_module._Session(vault)

    app_module._lock_for_minimize(window, session)

    assert vault.is_locked is True
    assert window.repository is None
    assert window.editor.markdown() == ""

    # The pending edit was flushed before the vault locked: reopen and confirm.
    reopened = Vault(path)
    reopened.unlock(PASSWORD)
    try:
        assert Repository(reopened.connection).get_note(note.id).body == "pending edit"
    finally:
        reopened.lock()


def test_lock_for_minimize_is_a_noop_when_already_locked(qapp, tmp_path):
    path = tmp_path / "notes.vault"
    vault = Vault.create(path, PASSWORD, FAST)
    window = MainWindow()
    session = app_module._Session(vault)
    vault.lock()

    # Must not raise or attempt to flush over the closed connection.
    app_module._lock_for_minimize(window, session)
    assert vault.is_locked is True


def test_reprompt_and_rebind_rebinds_on_success(qapp, tmp_path, monkeypatch):
    path = tmp_path / "notes.vault"
    vault = Vault.create(path, PASSWORD, FAST)
    Repository(vault.connection).create_note(title="A", body="aaa")
    window = MainWindow()
    session = app_module._Session(vault)
    # Simulate the post-minimise state: vault locked, session cleared.
    vault.lock()
    window.lock_session()

    reopened = Vault(path)
    reopened.unlock(PASSWORD)
    monkeypatch.setattr(app_module, "_open_vault", lambda p: reopened)
    try:
        app_module._reprompt_and_rebind(
            window, session, None, Settings(lock_on_minimize=True), path
        )
        assert session.vault is reopened
        assert window.repository is not None
        assert window.note_list.count() == 1
    finally:
        reopened.lock()


def test_reprompt_and_rebind_closes_window_on_cancel(qapp, tmp_path, monkeypatch):
    path = tmp_path / "notes.vault"
    vault = Vault.create(path, PASSWORD, FAST)
    window = MainWindow()
    session = app_module._Session(vault)
    vault.lock()  # the minimise already locked it

    closed = []
    monkeypatch.setattr(window, "close", lambda: closed.append(True))
    monkeypatch.setattr(app_module, "_open_vault", lambda p: None)  # user cancels

    app_module._reprompt_and_rebind(
        window, session, None, Settings(lock_on_minimize=True), path
    )

    assert closed == [True]
    assert window.repository is None


# -- app._wire_lock_on_minimize (end to end) ---------------------------------


def test_wire_lock_on_minimize_full_cycle(qapp, tmp_path, monkeypatch):
    path = tmp_path / "notes.vault"
    vault = Vault.create(path, PASSWORD, FAST)
    Repository(vault.connection).create_note(title="Hi", body="hello body")
    window = MainWindow()
    window.settings = Settings(lock_on_minimize=True)
    session = app_module._Session(vault)
    app_module._bind_vault(window, vault)
    assert window.note_list.count() == 1

    app_module._wire_lock_on_minimize(window, session, None, window.settings, path)

    # Minimise -> lock + clear the session.
    window.handle_window_state_change(minimized=True)
    assert session.vault.is_locked is True
    assert window.repository is None
    assert window.note_list.count() == 0

    # Restore -> re-prompt (stubbed to succeed) rebinds a working session.
    reopened = Vault(path)
    reopened.unlock(PASSWORD)
    monkeypatch.setattr(app_module, "_open_vault", lambda p: reopened)
    try:
        window.handle_window_state_change(minimized=False)
        assert window.repository is not None
        assert session.vault is reopened
        assert window.note_list.count() == 1
    finally:
        reopened.lock()


def test_wire_lock_on_minimize_is_a_noop_when_disabled(qapp, tmp_path):
    path = tmp_path / "notes.vault"
    vault = Vault.create(path, PASSWORD, FAST)
    window = MainWindow()
    session = app_module._Session(vault)
    try:
        app_module._wire_lock_on_minimize(
            window, session, None, Settings(lock_on_minimize=False), path
        )
        # Nothing was connected, so emitting the lock request does nothing.
        window.lock_on_minimize_requested.emit()
        assert vault.is_locked is False
    finally:
        vault.lock()
