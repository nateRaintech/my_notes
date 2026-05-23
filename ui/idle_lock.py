"""Drives the vault's idle auto-lock from the Qt event loop (Qt layer).

Mirrors :class:`ui.autosave.AutoSaveController`: the *policy* lives in
:class:`core.vault.Vault` (``idle_timeout`` / :meth:`~core.vault.Vault.touch` /
:meth:`~core.vault.Vault.is_idle_expired` / :meth:`~core.vault.Vault.lock_if_idle`
— Qt-free and fake-clock-testable). This class supplies the Qt heartbeat (a
repeating ``QTimer``) and the activity source, and turns "the vault went idle"
into UI-level signals.

Why an application-wide event filter rather than connecting to widget signals:
database access through :class:`core.repository.Repository` uses a connection
captured once at bind time, so it does **not** call :meth:`Vault.touch`. Without
an independent activity source the vault would idle-lock ``idle_timeout`` seconds
after launch no matter how actively the user types or navigates. Installing an
event filter on the ``QApplication`` sees every key/mouse event for every widget,
which is the comprehensive, presence-based activity signal idle-lock needs.

On idle the controller emits :attr:`about_to_lock` **before** locking — while the
connection is still open, so a listener can flush pending writes — then locks the
vault (wiping the key) and emits :attr:`locked`, so the UI can clear the now
un-decryptable session and re-prompt.

Per CLAUDE.md's strict layering, the UI layer may import Qt freely; ``core/`` must
never import this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QTimer, Signal

if TYPE_CHECKING:
    from core.vault import Vault

# How often the timer checks whether the vault has gone idle. Coarser than the
# auto-save tick (idle timeouts are minutes, not sub-second), so the vault locks
# within a few seconds of its deadline without polling needlessly often.
_TICK_MS = 5_000

# Input event types that count as user activity (presence). Mouse movement is
# included so reading a note — moving or scrolling without clicking — also defers
# the lock.
_ACTIVITY_EVENTS = frozenset(
    {
        QEvent.Type.KeyPress,
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseMove,
        QEvent.Type.Wheel,
    }
)


class IdleLockController(QObject):
    """Locks an unlocked :class:`~core.vault.Vault` after user inactivity.

    Construct it with the unlocked vault (whose ``idle_timeout`` has been set) and
    the application to watch; it installs an activity event filter and starts a
    check timer immediately. It emits :attr:`about_to_lock` just before locking
    (connection still open — flush pending writes here) and :attr:`locked`
    immediately after (clear the session and re-prompt here). After the user
    re-unlocks, call :meth:`set_vault` then :meth:`start` to re-arm against the
    fresh vault.

    :meth:`check_now` is the seam tests drive directly (the ``QTimer`` merely calls
    it each tick): with a fake clock on the vault, advancing the clock and calling
    :meth:`check_now` exercises the lock decision deterministically, with no real
    waiting.
    """

    #: Emitted just before the vault is locked (its connection is still open).
    about_to_lock = Signal()
    #: Emitted immediately after the vault is locked (key wiped, connection closed).
    locked = Signal()

    def __init__(
        self,
        vault: Vault,
        *,
        app: QCoreApplication | None = None,
        interval_ms: int = _TICK_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._vault = vault
        self._app = app if app is not None else QCoreApplication.instance()

        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.check_now)

        # A filter on the application object receives every event for every
        # widget, so any key/mouse activity anywhere counts as presence.
        if self._app is not None:
            self._app.installEventFilter(self)
        self._timer.start()

    @property
    def vault(self) -> Vault:
        """The vault currently being watched."""
        return self._vault

    def set_vault(self, vault: Vault) -> None:
        """Watch a different (freshly unlocked) vault, e.g. after a re-unlock."""
        self._vault = vault

    def check_now(self) -> bool:
        """Lock the vault iff it has been idle past its timeout; report whether it locked.

        On a lock it stops the timer (no further ticks until :meth:`start` re-arms
        it) and emits :attr:`about_to_lock` then :attr:`locked`, in that order, so
        listeners can flush while the connection is open and then react once it is
        closed. Called every tick by the timer and directly by tests.
        """
        if not self._vault.is_idle_expired():
            return False
        self._timer.stop()
        self.about_to_lock.emit()
        self._vault.lock()
        self.locked.emit()
        return True

    def start(self) -> None:
        """(Re)start the idle-check timer, e.g. after re-unlocking the vault."""
        self._timer.start()

    def stop(self) -> None:
        """Stop checking and remove the activity event filter (e.g. on shutdown)."""
        self._timer.stop()
        if self._app is not None:
            self._app.removeEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Record user input as activity; never consumes the event.

        Returning ``False`` lets the event continue to its target untouched — this
        filter only observes, it does not intercept.
        """
        if event.type() in _ACTIVITY_EVENTS:
            self._vault.touch()
        return False
