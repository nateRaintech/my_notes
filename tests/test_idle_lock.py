"""Behavioral tests for idle auto-lock (the M5 Settings idle-lock runtime wiring).

Three layers are exercised:

* :class:`ui.idle_lock.IdleLockController` against a *real* unlocked
  :class:`core.vault.Vault` in ``tmp_path`` with an injected :class:`FakeClock`
  driving its idle policy — so locking, the activity reset, and the
  ``about_to_lock`` -> ``locked`` ordering are deterministic with no real waiting.
  The controller's ``QTimer`` only ever calls :meth:`check_now`, which the tests
  call directly.
* :class:`ui.main_window.MainWindow` session seams (:meth:`lock_session`,
  :meth:`flush_pending`) over a real in-memory SQLCipher repository.
* The ``app`` composition helpers (``_bind_vault`` / ``_shutdown``) that rebind a
  fresh repository after a re-unlock and lock the current vault on shutdown.

Guarded by ``importorskip`` and run headless via the ``offscreen`` Qt platform,
matching the rest of the UI tests. A fast Argon2 cost keeps key derivation cheap.
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

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import app as app_module  # noqa: E402
from ui.idle_lock import IdleLockController  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

# Minimal valid Argon2 params (memory_cost floor is 8 * parallelism) — fast.
FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
PASSWORD = "correct horse battery staple"


class FakeClock:
    """A controllable monotonic clock for driving the vault's idle policy."""

    def __init__(self, start: float = 1000.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


@pytest.fixture(scope="module")
def qapp():
    """A process-wide QApplication (singleton) for widget construction."""
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def make_unlocked_vault(tmp_path):
    """Factory for real unlocked vaults with an injected clock + idle timeout.

    Each vault gets its own file under ``tmp_path``; all are locked at teardown.
    """
    created = []

    def _make(*, clock, idle_timeout):
        path = tmp_path / f"vault{len(created)}.vault"
        vault = Vault.create(
            path, PASSWORD, FAST, idle_timeout=idle_timeout, clock=clock
        )
        created.append(vault)
        return vault

    yield _make
    for vault in created:
        vault.lock()


@pytest.fixture
def idle_controllers(qapp):
    """Factory that builds IdleLockControllers and removes their filters at teardown.

    The controller installs an application-wide event filter; stopping it on
    teardown keeps a stale filter from touching a torn-down vault in later tests.
    """
    created = []

    def _make(vault, **kwargs):
        controller = IdleLockController(vault, app=qapp, **kwargs)
        created.append(controller)
        return controller

    yield _make
    for controller in created:
        controller.stop()


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


# -- IdleLockController ------------------------------------------------------


def test_locks_after_idle_timeout(qapp, make_unlocked_vault, idle_controllers):
    clock = FakeClock(1000.0)
    vault = make_unlocked_vault(clock=clock, idle_timeout=60)
    controller = idle_controllers(vault)

    assert vault.is_locked is False
    clock.advance(60)  # reach the timeout exactly (is_idle_expired uses >=)
    assert controller.check_now() is True
    assert vault.is_locked is True


def test_does_not_lock_before_timeout(qapp, make_unlocked_vault, idle_controllers):
    clock = FakeClock(1000.0)
    vault = make_unlocked_vault(clock=clock, idle_timeout=60)
    controller = idle_controllers(vault)

    clock.advance(59)  # just shy of the timeout
    assert controller.check_now() is False
    assert vault.is_locked is False


def test_activity_defers_idle_lock(qapp, make_unlocked_vault, idle_controllers):
    clock = FakeClock(1000.0)
    vault = make_unlocked_vault(clock=clock, idle_timeout=300)
    controller = idle_controllers(vault)

    clock.advance(200)  # t=1200, < 300 since unlock
    assert controller.check_now() is False

    # User input resets the idle clock (records activity at t=1200).
    assert controller.eventFilter(qapp, QEvent(QEvent.Type.KeyPress)) is False

    clock.advance(200)  # t=1400 — only 200s since the activity
    assert controller.check_now() is False
    assert vault.is_locked is False

    clock.advance(150)  # t=1550 — now 350s since activity, past the timeout
    assert controller.check_now() is True
    assert vault.is_locked is True


def test_emits_about_to_lock_before_locked(qapp, make_unlocked_vault, idle_controllers):
    clock = FakeClock(1000.0)
    vault = make_unlocked_vault(clock=clock, idle_timeout=60)
    controller = idle_controllers(vault)

    # Record the lock state observed when each signal fires: about_to_lock must
    # fire while still unlocked (so a listener can flush over the live connection),
    # locked after the key is wiped.
    observed = []
    controller.about_to_lock.connect(
        lambda: observed.append(("about_to_lock", vault.is_locked))
    )
    controller.locked.connect(lambda: observed.append(("locked", vault.is_locked)))

    clock.advance(60)
    assert controller.check_now() is True
    assert observed == [("about_to_lock", False), ("locked", True)]


def test_eventfilter_ignores_non_activity_events(
    qapp, make_unlocked_vault, idle_controllers
):
    clock = FakeClock(1000.0)
    vault = make_unlocked_vault(clock=clock, idle_timeout=60)
    controller = idle_controllers(vault)

    clock.advance(60)
    # A non-input event does not count as activity, so the vault stays idle.
    assert controller.eventFilter(qapp, QEvent(QEvent.Type.Paint)) is False
    assert controller.check_now() is True


def test_set_vault_switches_the_watched_vault(
    qapp, make_unlocked_vault, idle_controllers
):
    clock1 = FakeClock(1000.0)
    clock2 = FakeClock(5000.0)
    first = make_unlocked_vault(clock=clock1, idle_timeout=60)
    second = make_unlocked_vault(clock=clock2, idle_timeout=60)
    controller = idle_controllers(first)

    controller.set_vault(second)
    assert controller.vault is second

    clock1.advance(120)  # the no-longer-watched vault would be idle
    assert controller.check_now() is False
    assert first.is_locked is False

    clock2.advance(60)  # the now-watched vault goes idle -> it locks
    assert controller.check_now() is True
    assert second.is_locked is True
    assert first.is_locked is False


def test_locked_vault_is_not_relocked(qapp, make_unlocked_vault, idle_controllers):
    clock = FakeClock(1000.0)
    vault = make_unlocked_vault(clock=clock, idle_timeout=60)
    controller = idle_controllers(vault)

    clock.advance(60)
    assert controller.check_now() is True
    # An already-locked vault is never idle-expired, so further checks are no-ops.
    clock.advance(1000)
    assert controller.check_now() is False


# -- MainWindow session seams ------------------------------------------------


def test_lock_session_clears_decrypted_content(qapp, repo):
    note = repo.create_note(title="Secret", body="top secret body")
    window = MainWindow()
    window.bind_autosave(repo)
    window.refresh_notes()
    window.load_note(note)

    assert window.editor.markdown() == "top secret body"
    assert window.note_list.count() == 1
    assert window.notebook_tree.topLevelItemCount() >= 1  # at least "All Notes"

    window.lock_session()

    assert window.repository is None
    assert window.autosave is None
    assert window.tabbed_editor.count() == 0  # all tabs wiped on lock
    assert window.note_list.count() == 0
    assert window.notebook_tree.topLevelItemCount() == 0
    assert window.search_input.text() == ""


def test_flush_pending_persists_dirty_edit(qapp, repo):
    note = repo.create_note(title="Note", body="")
    window = MainWindow()
    # A long debounce so nothing auto-flushes — flush_pending must be what writes.
    window.bind_autosave(repo, debounce=999)
    window.load_note(note)
    window.editor.source.setPlainText("typed but not yet debounced")

    window.flush_pending()
    assert repo.get_note(note.id).body == "typed but not yet debounced"


def test_flush_pending_is_a_no_op_without_autosave(qapp):
    MainWindow().flush_pending()  # must not raise when no vault is bound


# -- app composition helpers -------------------------------------------------


def test_bind_vault_populates_window_from_vault(qapp, tmp_path):
    path = tmp_path / "notes.vault"
    vault = Vault.create(path, PASSWORD, FAST)
    try:
        Repository(vault.connection).create_note(title="Hello", body="hi there")
        window = MainWindow()
        repository = app_module._bind_vault(window, vault)

        assert window.repository is repository
        assert window.tabbed_editor._repository is repository  # tabs will autosave
        assert window.note_list.count() == 1
    finally:
        vault.lock()


def test_relock_cycle_rebinds_a_working_session(qapp, tmp_path):
    """lock_session then _bind_vault on a re-unlocked vault restores the session."""
    path = tmp_path / "notes.vault"
    vault = Vault.create(path, PASSWORD, FAST)
    Repository(vault.connection).create_note(title="A", body="aaa")
    window = MainWindow()
    app_module._bind_vault(window, vault)
    assert window.note_list.count() == 1

    # Simulate an idle auto-lock: flush, lock the vault, clear the UI.
    window.flush_pending()
    vault.lock()
    window.lock_session()
    assert window.repository is None
    assert window.note_list.count() == 0

    # Re-unlock and rebind — the note persisted and the session is usable again.
    reopened = Vault(path)
    reopened.unlock(PASSWORD)
    try:
        app_module._bind_vault(window, reopened)
        assert window.repository is not None
        assert window.note_list.count() == 1
    finally:
        reopened.lock()


def test_arm_idle_lock_disabled_returns_none(qapp, tmp_path):
    path = tmp_path / "notes.vault"
    vault = Vault.create(path, PASSWORD, FAST)
    window = MainWindow()
    session = app_module._Session(vault)
    try:
        idle = app_module._arm_idle_lock(
            qapp, window, session, Settings(idle_timeout_seconds=None), path
        )
        assert idle is None
        assert vault.idle_timeout is None  # untouched when disabled
    finally:
        vault.lock()


def test_arm_idle_lock_enabled_sets_timeout_and_returns_controller(qapp, tmp_path):
    path = tmp_path / "notes.vault"
    vault = Vault.create(path, PASSWORD, FAST)
    window = MainWindow()
    session = app_module._Session(vault)
    idle = None
    try:
        idle = app_module._arm_idle_lock(
            qapp, window, session, Settings(idle_timeout_seconds=120), path
        )
        assert idle is not None
        assert vault.idle_timeout == 120
    finally:
        if idle is not None:
            idle.stop()
        vault.lock()


def test_shutdown_locks_the_current_vault(qapp, tmp_path):
    path = tmp_path / "notes.vault"
    vault = Vault.create(path, PASSWORD, FAST)
    window = MainWindow()
    app_module._bind_vault(window, vault)
    session = app_module._Session(vault)

    app_module._shutdown(window, session, None)
    assert vault.is_locked is True
