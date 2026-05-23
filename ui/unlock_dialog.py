"""The create-vault / unlock-vault prompt shown at application launch.

This is the keystone that turns the M3 UI shell into a working app: the editor,
auto-save, note list, and search all need a keyed
:class:`~core.repository.Repository`, and a repository needs the live connection
of an *unlocked* :class:`~core.vault.Vault`. This dialog is what unlocks it.

The dialog auto-detects its mode from the filesystem:

* **Create** — neither the vault file nor its ``<path>.meta`` sidecar exists, so
  this is a first run. The user picks a master password (entered twice); on
  accept a fresh encrypted vault is created. Per CLAUDE.md there is **no password
  recovery** — forgetting it means the notes are unrecoverable, by design.
* **Unlock** — a vault already exists. The user enters their master password; a
  wrong password is reported inline and the dialog stays open to retry, with no
  exception escaping and the vault left locked.

On success :attr:`vault` holds the unlocked :class:`~core.vault.Vault` and the
dialog is accepted; :func:`app.main` then builds a repository over its
connection. The actual vault create/unlock/lock logic lives in ``core.vault`` —
this layer only collects the password and surfaces the outcome.

Per CLAUDE.md's strict layering, the UI layer may import Qt freely; ``core/``
must never import this module.
"""

from __future__ import annotations

import os

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from core.crypto import DEFAULT_PARAMS, KdfParams
from core.vault import META_SUFFIX, InvalidPassword, Vault, VaultError


def _vault_exists(vault_path: str | os.PathLike[str]) -> bool:
    """True if a vault is already present at ``vault_path``.

    Mirrors :meth:`core.vault.Vault.create`'s no-clobber guard: a vault counts as
    present if *either* the database file or its plaintext ``.meta`` sidecar
    exists, so a partially-written vault is treated as existing (unlock mode) and
    surfaces a clean error rather than being silently clobbered by create mode.
    """
    return os.path.exists(vault_path) or os.path.exists(str(vault_path) + META_SUFFIX)


class UnlockDialog(QDialog):
    """Prompt for the master password and produce an unlocked vault.

    Construct with the vault path; the dialog picks create or unlock mode itself
    (:attr:`is_create_mode`). Drive it with :meth:`QDialog.exec` in the app, or
    call :meth:`attempt` directly in tests after setting the field text. On
    success :attr:`vault` is the unlocked :class:`~core.vault.Vault`; it is
    ``None`` while the dialog is open or after a cancel.

    Test seams: :attr:`password_edit`, :attr:`confirm_edit` (``None`` in unlock
    mode), and :attr:`error_label` are exposed so headless tests can fill the
    fields and read back the inline error.
    """

    def __init__(
        self,
        vault_path: str | os.PathLike[str],
        *,
        params: KdfParams = DEFAULT_PARAMS,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._vault_path = vault_path
        self._params = params
        self.is_create_mode = not _vault_exists(vault_path)
        self.vault: Vault | None = None

        self.setWindowTitle(
            "my_notes — Create Vault" if self.is_create_mode else "my_notes — Unlock Vault"
        )

        layout = QVBoxLayout(self)

        prompt = (
            "Create a new encrypted vault. Choose a master password — there is no "
            "way to recover your notes if you forget it."
            if self.is_create_mode
            else "Unlock your vault with your master password."
        )
        layout.addWidget(QLabel(prompt))

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.password_edit.setPlaceholderText("Master password")
        layout.addWidget(self.password_edit)

        # Confirm field exists only when creating a vault (catch typos in a
        # password that, once chosen, can never be recovered).
        self.confirm_edit: QLineEdit | None = None
        if self.is_create_mode:
            self.confirm_edit = QLineEdit()
            self.confirm_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.confirm_edit.setPlaceholderText("Confirm master password")
            layout.addWidget(self.confirm_edit)

        self.error_label = QLabel()
        self.error_label.setObjectName("errorLabel")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok_button.setText("Create" if self.is_create_mode else "Unlock")
        ok_button.setDefault(True)
        # Route OK through attempt() rather than the default accept(): a failed
        # create/unlock must keep the dialog open instead of closing it.
        buttons.accepted.connect(self.attempt)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def attempt(self) -> bool:
        """Validate input, create or unlock the vault, and accept on success.

        Returns ``True`` if a vault was created/unlocked (``self.vault`` set and
        the dialog accepted), ``False`` otherwise — in which case an inline
        message is shown in :attr:`error_label` and the dialog stays open. Safe to
        call directly from tests without running the modal event loop.
        """
        password = self.password_edit.text()
        if not password:
            return self._fail("Master password cannot be empty.")

        if self.is_create_mode:
            assert self.confirm_edit is not None  # always built in create mode
            if password != self.confirm_edit.text():
                return self._fail("Passwords do not match.")
            try:
                self.vault = Vault.create(self._vault_path, password, self._params)
            except VaultError as exc:
                return self._fail(str(exc))
        else:
            vault = Vault(self._vault_path)
            try:
                vault.unlock(password)
            except InvalidPassword:
                return self._fail("Incorrect master password.")
            except VaultError as exc:
                return self._fail(str(exc))
            self.vault = vault

        self.error_label.clear()
        self.accept()
        return True

    def _fail(self, message: str) -> bool:
        """Show ``message`` inline and report failure (keeps the dialog open)."""
        self.error_label.setText(message)
        return False
