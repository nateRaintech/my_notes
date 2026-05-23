"""The application Settings dialog (M5).

A small dialog that lets the user view and change all four preferences persisted
by :mod:`core.settings`:

* **Theme** — light (native) or dark, applied to the live window when accepted
  and restored on the next launch (it previously reset every run).
* **Vault file location** — where the encrypted vault lives. Changing it takes
  effect the next time the app opens (an open encrypted database cannot be moved
  live), so the field is labelled as such.
* **Auto-lock after inactivity** — an enable checkbox plus a minutes spinbox. The
  model stores the timeout in seconds (``None`` = disabled); the dialog presents
  whole minutes. Honoured at runtime by :class:`ui.idle_lock.IdleLockController`.
* **Lock when minimised** — lock the vault and re-prompt when the window is
  minimised (the runtime behaviour lives in :class:`ui.main_window.MainWindow` +
  ``app``).

This is the final M5 *Settings* slice: the idle-lock timeout and lock-on-minimise
were deferred from the earlier theme/vault slice until the mid-session auto-lock
UX was settled (lock-and-reprompt-in-place, the KeePass convention). :meth:`apply`
now reads all four fields from the controls.

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
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.settings import Settings, save_settings
from core.theme import available_themes

if TYPE_CHECKING:
    import os

# Browse-dialog filter: the encrypted vault uses a .vault extension by convention.
_VAULT_FILTER = "Vault files (*.vault);;All files (*)"

# The idle-lock timeout is presented in whole minutes (the model stores seconds).
_SECONDS_PER_MINUTE = 60
# Spinbox bounds: 1 minute up to 24 hours.
_MIN_IDLE_MINUTES = 1
_MAX_IDLE_MINUTES = 24 * 60
# Minutes shown when enabling auto-lock on a vault that had it disabled.
_DEFAULT_IDLE_MINUTES = 5


def _seconds_to_minutes(seconds: int | None) -> int:
    """Convert a stored timeout (seconds) to whole minutes for the spinbox.

    Rounds to the nearest minute and clamps into the spinbox range, so any stored
    value (including a legacy sub-minute one) maps to a valid, displayable minute
    count. ``None`` / non-positive falls back to the default.
    """
    if not seconds or seconds <= 0:
        return _DEFAULT_IDLE_MINUTES
    minutes = round(seconds / _SECONDS_PER_MINUTE)
    return max(_MIN_IDLE_MINUTES, min(_MAX_IDLE_MINUTES, minutes))


class SettingsDialog(QDialog):
    """View and edit the persisted application settings.

    Construct with the current :class:`~core.settings.Settings`; the controls are
    seeded from it. Drive it with :meth:`QDialog.exec` in the app, or call
    :meth:`apply` directly in tests after setting the controls. On accept
    :attr:`settings` holds the chosen :class:`~core.settings.Settings` (also the
    return value of :meth:`apply`), which has been persisted to ``settings_path``
    (or :func:`core.settings.default_settings_path` when ``None``).

    Test seams: :attr:`theme_combo`, :attr:`vault_path_edit`,
    :attr:`idle_lock_checkbox`, :attr:`idle_lock_minutes`, and
    :attr:`lock_on_minimize_checkbox` are exposed so headless tests can set the
    controls and read back the result.
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

        # Auto-lock after inactivity: a checkbox enabling a minutes spinbox. The
        # model stores seconds (None = disabled); the spinbox shows whole minutes.
        idle_enabled = bool(settings.idle_timeout_seconds)
        self.idle_lock_checkbox = QCheckBox("Auto-lock after inactivity")
        self.idle_lock_checkbox.setChecked(idle_enabled)
        self.idle_lock_minutes = QSpinBox()
        self.idle_lock_minutes.setRange(_MIN_IDLE_MINUTES, _MAX_IDLE_MINUTES)
        self.idle_lock_minutes.setSuffix(" min")
        self.idle_lock_minutes.setValue(
            _seconds_to_minutes(settings.idle_timeout_seconds)
            if idle_enabled
            else _DEFAULT_IDLE_MINUTES
        )
        self.idle_lock_minutes.setEnabled(idle_enabled)
        # The minutes field is only meaningful while auto-lock is enabled.
        self.idle_lock_checkbox.toggled.connect(self.idle_lock_minutes.setEnabled)
        idle_row = QHBoxLayout()
        idle_row.addWidget(self.idle_lock_checkbox)
        idle_row.addWidget(self.idle_lock_minutes)
        idle_row.addStretch()
        form.addRow("Auto-lock:", idle_row)

        # Lock the vault and re-prompt when the main window is minimised.
        self.lock_on_minimize_checkbox = QCheckBox("Lock when the window is minimised")
        self.lock_on_minimize_checkbox.setChecked(settings.lock_on_minimize)
        form.addRow("", self.lock_on_minimize_checkbox)

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

        All four fields come from the controls. A blank vault path means "use the
        default location" (``None``); an unchecked auto-lock checkbox means
        ``idle_timeout_seconds = None`` (disabled), otherwise the minutes spinbox is
        converted to seconds. Returns the chosen settings (also stored on
        :attr:`settings`). Safe to call from tests without the modal loop.
        """
        vault_path = self.vault_path_edit.text().strip() or None
        if self.idle_lock_checkbox.isChecked():
            idle_timeout_seconds: int | None = (
                self.idle_lock_minutes.value() * _SECONDS_PER_MINUTE
            )
        else:
            idle_timeout_seconds = None
        self.settings = Settings(
            idle_timeout_seconds=idle_timeout_seconds,
            vault_path=vault_path,
            lock_on_minimize=self.lock_on_minimize_checkbox.isChecked(),
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
