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

from PySide6.QtWidgets import QApplication, QDialog

from core.repository import Repository
from core.vault import Vault
from ui.main_window import MainWindow
from ui.unlock_dialog import UnlockDialog

# Environment override for the vault location, used by tests and power users.
# A configurable vault path in the UI is an M5 (Settings) capability.
VAULT_PATH_ENV = "MY_NOTES_VAULT"


def default_vault_path() -> Path:
    """Where the encrypted vault lives.

    Honours the ``MY_NOTES_VAULT`` environment variable if set (absolute or
    relative path to the vault file); otherwise defaults to
    ``~/.my_notes/notes.vault``.
    """
    override = os.environ.get(VAULT_PATH_ENV)
    if override:
        return Path(override)
    return Path.home() / ".my_notes" / "notes.vault"


def main() -> int:
    """Create the Qt application, unlock a vault, and run the main window."""
    app = QApplication(sys.argv)
    app.setApplicationName("my_notes")

    vault_path = default_vault_path()
    vault_path.parent.mkdir(parents=True, exist_ok=True)

    dialog = UnlockDialog(vault_path)
    if dialog.exec() != QDialog.DialogCode.Accepted or dialog.vault is None:
        # User cancelled (or closed) the prompt — nothing to open.
        return 0
    vault = dialog.vault

    repository = Repository(vault.connection)
    window = MainWindow()
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
