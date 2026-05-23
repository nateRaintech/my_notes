"""my_notes — application entry point.

Run ``python app.py`` to open the app. Launch first shows the create/unlock
vault dialog (:class:`ui.unlock_dialog.UnlockDialog`); only once a vault is
unlocked is the main window opened, with debounced auto-save bound to a
:class:`~core.repository.Repository` over the keyed vault connection. Cancelling
the dialog exits cleanly without opening a window.

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


def main() -> int:
    """Create the Qt application, unlock a vault, and run the main window."""
    app = QApplication(sys.argv)
    app.setApplicationName("my_notes")

    settings = load_settings()
    vault_path = resolve_vault_path(settings)
    vault_path.parent.mkdir(parents=True, exist_ok=True)

    dialog = UnlockDialog(vault_path)
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.vault is None:
        # User cancelled (or closed) the prompt — nothing to open.
        return 0
    vault = dialog.vault

    repository = Repository(vault.connection)
    window = MainWindow()
    window.configure_settings(settings)  # apply the saved theme; persist changes
    window.bind_autosave(repository)
    window.refresh_notes()  # populate the note list from the vault on launch
    # Flush any pending edit and lock the vault (wiping the key) on shutdown.
    app.aboutToQuit.connect(lambda: _shutdown(window, vault))
    window.show()

    return app.exec()


def _shutdown(window: MainWindow, vault: Vault) -> None:
    """Flush auto-save and lock the vault as the application quits."""
    if window.autosave is not None:
        window.autosave.stop()
    vault.lock()


if __name__ == "__main__":
    sys.exit(main())
