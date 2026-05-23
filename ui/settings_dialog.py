"""The application Settings dialog (M5).

A small dialog that lets the user view and change the preferences persisted by
:mod:`core.settings`. This slice edits the two settings whose effect is
unambiguous and takes hold at launch:

* **Theme** — light (native) or dark, applied to the live window when accepted
  and restored on the next launch (it previously reset every run).
* **Vault file location** — where the encrypted vault lives. Changing it takes
  effect the next time the app opens (an open encrypted database cannot be moved
  live), so the field is labelled as such.

The idle-lock timeout and lock-on-minimise settings — though already present on
the :class:`~core.settings.Settings` model — are deliberately *not* exposed here:
they require deciding what the running app does when the vault auto-locks
mid-session, which is the final M5 *Settings* slice. :meth:`apply` therefore
carries those two fields through untouched rather than resetting them.

Test seam mirrors the rest of the UI (``UnlockDialog.attempt`` /
``ImportWizard.run_import``): the public :meth:`apply` reads the controls into a
:class:`~core.settings.Settings`, persists it via
:func:`core.settings.save_settings`, and accepts — so headless tests drive it
without running the modal event loop.

Per CLAUDE.md's strict layering, the UI may import Qt freely and depends on the
Qt-free settings/theme modules; ``core/`` never imports this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.settings import Settings, save_settings
from core.theme import available_themes

if TYPE_CHECKING:
    import os

# Browse-dialog filter: the encrypted vault uses a .vault extension by convention.
_VAULT_FILTER = "Vault files (*.vault);;All files (*)"


class SettingsDialog(QDialog):
    """View and edit the persisted application settings.

    Construct with the current :class:`~core.settings.Settings`; the controls are
    seeded from it. Drive it with :meth:`QDialog.exec` in the app, or call
    :meth:`apply` directly in tests after setting the controls. On accept
    :attr:`settings` holds the chosen :class:`~core.settings.Settings` (also the
    return value of :meth:`apply`), which has been persisted to ``settings_path``
    (or :func:`core.settings.default_settings_path` when ``None``).

    Test seams: :attr:`theme_combo` and :attr:`vault_path_edit` are exposed so
    headless tests can set the controls and read back the result.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        settings_path: str | os.PathLike[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._initial = settings
        self._settings_path = settings_path
        # The chosen settings; replaced by apply(). Defaults to the input so a
        # cancelled dialog still exposes a sensible value.
        self.settings = settings

        self.setWindowTitle("my_notes — Settings")

        form = QFormLayout()

        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(available_themes()))
        self.theme_combo.setCurrentText(settings.theme)
        form.addRow("Theme:", self.theme_combo)

        self.vault_path_edit = QLineEdit(settings.vault_path or "")
        self.vault_path_edit.setPlaceholderText("Default location")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_vault)
        vault_row = QHBoxLayout()
        vault_row.addWidget(self.vault_path_edit)
        vault_row.addWidget(browse)
        form.addRow("Vault file:", vault_row)

        layout = QVBoxLayout(self)
        layout.addLayout(form)

        hint = QLabel(
            "Changing the vault location takes effect the next time you open my_notes."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        # Route OK through apply() (read controls, persist, accept) rather than the
        # default accept(), mirroring UnlockDialog.attempt.
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply(self) -> Settings:
        """Read the controls into a :class:`~core.settings.Settings`, persist, accept.

        The theme and vault-file location come from the controls; the idle-lock
        timeout and lock-on-minimise carry through from the settings the dialog
        was opened with (this dialog does not edit them). A blank vault path means
        "use the default location" (``None``). Returns the chosen settings (also
        stored on :attr:`settings`). Safe to call from tests without the modal loop.
        """
        vault_path = self.vault_path_edit.text().strip() or None
        self.settings = Settings(
            idle_timeout_seconds=self._initial.idle_timeout_seconds,
            vault_path=vault_path,
            lock_on_minimize=self._initial.lock_on_minimize,
            theme=self.theme_combo.currentText(),
        )
        save_settings(self.settings, self._settings_path)
        self.accept()
        return self.settings

    def _browse_vault(self) -> None:
        """Pick a vault file location with a native file dialog."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Choose vault file location",
            self.vault_path_edit.text(),
            _VAULT_FILTER,
        )
        if path:
            self.vault_path_edit.setText(path)
