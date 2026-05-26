"""Dialog for setting, replacing, or clearing the stored AI API key.

The key is write-only once stored: this dialog **never** reveals it to the
user.  The current state ("A key is stored." / "No key stored yet.") is shown
as a read-only label; a masked QLineEdit accepts a new or replacement key.

Public seams
------------
:meth:`save_key`          — validate and store a new key (no modal loop).
:meth:`clear_key`         — delete the stored key (no modal loop).
:meth:`current_state_text` — the human-readable state label text.

These let headless tests drive the logic without spinning the event loop,
mirroring the ``UnlockDialog.attempt`` / ``SettingsDialog.apply`` pattern.

Per CLAUDE.md's strict layering, this module imports Qt freely; ``core/``
never imports it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from core.repository import Repository

_STATE_HAS_KEY = "A key is stored."
_STATE_NO_KEY = "No key stored yet."


class APIKeyDialog(QDialog):
    """Let the user store, replace, or clear the AI API key.

    Construct with the current :class:`~core.repository.Repository`.  Drive it
    with :meth:`QDialog.exec` in the app, or call :meth:`save_key` /
    :meth:`clear_key` directly in tests.

    The stored key is **never** displayed — :attr:`key_edit` is password-masked
    and there is no "reveal" button.

    Test seams: :attr:`state_label`, :attr:`key_edit`, :attr:`save_button`,
    :attr:`clear_button`, and :attr:`error_label` are public.
    """

    def __init__(
        self,
        repository: Repository,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository

        self.setWindowTitle("my_notes — AI API Key")

        layout = QVBoxLayout(self)

        # Current state indicator — read-only, never shows the key value.
        self.state_label = QLabel(self.current_state_text())
        layout.addWidget(self.state_label)

        layout.addWidget(QLabel("Enter a new or replacement key:"))

        # Masked input — the user types the key but it is never echoed.
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("Paste API key here…")
        layout.addWidget(self.key_edit)

        self.error_label = QLabel()
        self.error_label.setObjectName("errorLabel")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        # Save / Clear / Close buttons.
        self.save_button = QPushButton("Save")
        self.save_button.setDefault(True)
        self.save_button.clicked.connect(self._on_save_clicked)

        self.clear_button = QPushButton("Clear stored key")
        self.clear_button.clicked.connect(self._on_clear_clicked)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout.addWidget(self.save_button)
        layout.addWidget(self.clear_button)
        layout.addWidget(buttons)

    # -------------------------------------------------------------------------
    # Public seams (callable without the modal event loop)
    # -------------------------------------------------------------------------

    def current_state_text(self) -> str:
        """Return the human-readable state string for the current repository state."""
        return _STATE_HAS_KEY if self._repository.has_api_key() else _STATE_NO_KEY

    def save_key(self, text: str) -> bool:
        """Validate ``text`` and store it as the API key.

        Returns ``True`` on success; ``False`` when ``text`` is empty (an inline
        error message is set on :attr:`error_label`).  Does NOT close the dialog
        so the modal loop can keep running after a headless call.
        """
        stripped = text.strip()
        if not stripped:
            self.error_label.setText("API key cannot be empty.")
            return False
        self._repository.set_api_key(stripped)
        self.error_label.clear()
        self.key_edit.clear()
        self.state_label.setText(self.current_state_text())
        return True

    def clear_key(self) -> None:
        """Remove the stored API key and refresh the state label."""
        self._repository.clear_api_key()
        self.error_label.clear()
        self.state_label.setText(self.current_state_text())

    # -------------------------------------------------------------------------
    # Internal slots
    # -------------------------------------------------------------------------

    def _on_save_clicked(self) -> None:
        self.save_key(self.key_edit.text())

    def _on_clear_clicked(self) -> None:
        self.clear_key()
