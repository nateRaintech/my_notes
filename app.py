"""my_notes — application entry point.

Run ``python app.py`` to open the app. Launch first shows the create/unlock
vault dialog (:class:`ui.unlock_dialog.UnlockDialog`); only once a vault is
unlocked is the main window opened, with debounced auto-save bound to a
:class:`~core.repository.Repository` over the keyed vault connection. Cancelling
the dialog exits cleanly without opening a window.

If the user configured an idle-lock timeout (``settings.idle_timeout_seconds``),
an :class:`~ui.idle_lock.IdleLockController` auto-locks the vault after that much
inactivity: it flushes pending edits, wipes the key, clears the on-screen session
(:meth:`~ui.main_window.MainWindow.lock_session`), and re-prompts. A correct
re-unlock rebinds a fresh repository and resumes; cancelling quits — the
conventional KeePass lock-and-reprompt behaviour. With no timeout configured (the
default) there is no auto-lock and launch behaves exactly as before.

If ``settings.lock_on_minimize`` is set, minimising the window locks the vault and
clears the session the same way, but defers the re-prompt until the window is
restored — so the app can sit minimised-and-locked instead of popping a modal at
once (see :func:`_lock_for_minimize` / :func:`_reprompt_and_rebind`).

The Qt application is created here in :func:`main`; all window logic lives in the
``ui`` package and all persistence in ``core``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QApplication, QDialog

from core.repository import Repository
from core.settings import load_settings
from core.vault import Vault
from ui.idle_lock import IdleLockController
from ui.main_window import MainWindow
from ui.unlock_dialog import UnlockDialog

if TYPE_CHECKING:
    from core.settings import Settings

# Environment override for the vault location, used by tests and power users. It
# takes precedence over the location configured in Settings (see resolve_vault_path).
VAULT_PATH_ENV = "MY_NOTES_VAULT"


def _builtin_default_vault_path() -> Path:
    """The vault location with no overrides: ``~/.my_notes/notes.vault``."""
    return Path.home() / ".my_notes" / "notes.vault"


def default_vault_path() -> Path:
    """The vault location honouring only the ``MY_NOTES_VAULT`` env override.

    Returns the override if set, else the built-in default. ``app.main`` uses
    :func:`resolve_vault_path`, which also considers the persisted settings; this
    function remains for the env-or-default case.
    """
    override = os.environ.get(VAULT_PATH_ENV)
    return Path(override) if override else _builtin_default_vault_path()


def resolve_vault_path(settings: Settings) -> Path:
    """Resolve the vault location with precedence: env > settings > built-in default.

    The ``MY_NOTES_VAULT`` environment override always wins (tests and power
    users); failing that, the user's configured
    :attr:`~core.settings.Settings.vault_path`; failing that, the built-in
    ``~/.my_notes/notes.vault``.
    """
    override = os.environ.get(VAULT_PATH_ENV)
    if override:
        return Path(override)
    if settings.vault_path:
        return Path(settings.vault_path)
    return _builtin_default_vault_path()


class _Session:
    """Mutable holder for the currently-unlocked vault.

    The vault is *replaced* when the user re-unlocks after an idle auto-lock, so
    the shutdown handler reads it indirectly through this holder — a directly
    captured vault reference would point at the stale, already-locked one.
    """

    def __init__(self, vault: Vault) -> None:
        self.vault = vault


def main() -> int:
    """Create the Qt application, unlock a vault, and run the main window."""
    app = QApplication(sys.argv)
    app.setApplicationName("my_notes")

    settings = load_settings()
    vault_path = resolve_vault_path(settings)
    vault_path.parent.mkdir(parents=True, exist_ok=True)

    vault = _open_vault(vault_path)
    if vault is None:
        # User cancelled (or closed) the prompt — nothing to open.
        return 0

    window = MainWindow()
    window.configure_settings(settings)  # apply the saved theme; persist changes
    session = _Session(vault)
    _bind_vault(window, session.vault)  # repository + auto-save + populated panes

    idle = _arm_idle_lock(app, window, session, settings, vault_path)
    _wire_lock_on_minimize(window, session, idle, settings, vault_path)

    # Flush any pending edit and lock the (current) vault, wiping the key, on quit.
    app.aboutToQuit.connect(lambda: _shutdown(window, session, idle))
    window.show()

    return app.exec()


def _open_vault(vault_path: str | os.PathLike[str]) -> Vault | None:
    """Show the create/unlock prompt and return the unlocked vault, or None.

    ``None`` means the user cancelled (or closed) the dialog. Used both at launch
    and to re-prompt after an idle auto-lock.
    """
    dialog = UnlockDialog(vault_path)
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.vault is None:
        return None
    return dialog.vault


def _bind_vault(window: MainWindow, vault: Vault) -> Repository:
    """Bind a fresh repository over ``vault`` to ``window`` and populate the panes.

    Builds a :class:`~core.repository.Repository` on the vault's keyed connection,
    attaches debounced auto-save (which also populates the notebook tree), and
    refreshes the note list. Used at launch and again after a re-unlock.
    """
    repository = Repository(vault.connection)
    window.bind_autosave(repository)
    window.refresh_notes()
    return repository


def _arm_idle_lock(
    app: QApplication,
    window: MainWindow,
    session: _Session,
    settings: Settings,
    vault_path: str | os.PathLike[str],
) -> IdleLockController | None:
    """Wire up idle auto-lock if a timeout is configured; return the controller.

    Returns ``None`` when ``settings.idle_timeout_seconds`` is unset (auto-lock
    disabled — no timer, no event filter, behaviour unchanged). Otherwise applies
    the timeout to the live vault and connects the controller's signals so that on
    idle the app flushes, locks, clears the session, and re-prompts.
    """
    if not settings.idle_timeout_seconds:
        return None
    session.vault.idle_timeout = settings.idle_timeout_seconds
    idle = IdleLockController(session.vault, app=app, parent=window)
    # Flush while the connection is still open, then react once it has closed.
    idle.about_to_lock.connect(window.flush_pending)
    idle.locked.connect(
        lambda: _relock(window, session, idle, settings, vault_path)
    )
    return idle


def _relock(
    window: MainWindow,
    session: _Session,
    idle: IdleLockController,
    settings: Settings,
    vault_path: str | os.PathLike[str],
) -> None:
    """Clear the locked session and re-prompt; rebind on unlock, quit on cancel.

    Runs when the vault has just idle-locked. Clears the decrypted UI, then asks
    for the master password again (see :func:`_reprompt_and_rebind`).
    """
    window.lock_session()
    _reprompt_and_rebind(window, session, idle, settings, vault_path)


def _reprompt_and_rebind(
    window: MainWindow,
    session: _Session,
    idle: IdleLockController | None,
    settings: Settings,
    vault_path: str | os.PathLike[str],
) -> None:
    """Re-prompt for the master password and rebind a fresh session, or quit on cancel.

    The vault is already locked and the on-screen session already cleared when this
    runs (by the idle :func:`_relock` or the minimise :func:`_lock_for_minimize`).
    A correct password rebinds a fresh repository and re-arms the idle timer (when
    one is armed); cancelling closes the window (which quits the app — locking the
    already-locked vault is a harmless no-op). ``idle`` is ``None`` when only
    lock-on-minimise is configured (no idle timer to re-arm).
    """
    vault = _open_vault(vault_path)
    if vault is None:
        window.close()
        return
    if settings.idle_timeout_seconds:
        vault.idle_timeout = settings.idle_timeout_seconds
    session.vault = vault
    if idle is not None:
        idle.set_vault(vault)
        idle.start()
    _bind_vault(window, vault)


def _lock_for_minimize(window: MainWindow, session: _Session) -> None:
    """Lock the vault and clear the session when the window is minimised.

    Flushes any pending edit over the still-open connection, locks the vault
    (wiping the key), and clears the decrypted UI. Unlike idle-lock, the re-prompt
    is deferred until the window is restored (see :func:`_reprompt_and_rebind`),
    so the app can sit minimised-and-locked rather than popping a modal at once.
    Any armed idle timer keeps ticking harmlessly — a locked vault never idle-
    expires — and is re-pointed at the fresh vault on restore. A no-op if the
    vault is already locked (e.g. an idle-lock got there first).
    """
    if session.vault.is_locked:
        return
    window.flush_pending()
    session.vault.lock()
    window.lock_session()


def _wire_lock_on_minimize(
    window: MainWindow,
    session: _Session,
    idle: IdleLockController | None,
    settings: Settings,
    vault_path: str | os.PathLike[str],
) -> None:
    """Connect the window's minimise/restore signals to lock + re-prompt, if enabled.

    A no-op unless ``settings.lock_on_minimize`` is set. When enabled, minimising
    the window locks the vault and clears the session (:func:`_lock_for_minimize`);
    restoring it re-prompts and rebinds (:func:`_reprompt_and_rebind`), re-arming
    the idle timer when one is present.
    """
    if not settings.lock_on_minimize:
        return
    window.lock_on_minimize_requested.connect(
        lambda: _lock_for_minimize(window, session)
    )
    window.restore_requested.connect(
        lambda: _reprompt_and_rebind(window, session, idle, settings, vault_path)
    )


def _shutdown(
    window: MainWindow, session: _Session, idle: IdleLockController | None
) -> None:
    """Stop the idle timer, flush auto-save, and lock the vault as the app quits."""
    if idle is not None:
        idle.stop()
    window.flush_pending()  # persist every open tab's pending edit
    session.vault.lock()


if __name__ == "__main__":
    sys.exit(main())
