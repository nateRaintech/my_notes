"""Behavioral tests for the Settings dialog and its launch/window wiring (M5).

Drives :class:`ui.settings_dialog.SettingsDialog` and the :class:`ui.main_window.MainWindow`
seams headlessly (offscreen Qt, matching ``tests/test_theme_toggle.py``). The
dialog edits the two settings whose effect is unambiguous and takes hold at
launch -- the theme and the vault file location; persistence itself is the
Qt-free :mod:`core.settings` (covered in ``tests/test_settings.py``), so these
tests cover the UI seam and the ``app.main`` vault-path precedence.

A ``settings_path`` in ``tmp_path`` is always supplied so a test never reads or
writes the real ``~/.my_notes/settings.json``.
"""

import os

import pytest

# Select the headless platform before any Qt import instantiates a plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from core import theme  # noqa: E402
from core.settings import Settings, load_settings  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.settings_dialog import SettingsDialog  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """A process-wide QApplication (singleton) for widget construction."""
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def settings_file(tmp_path):
    return tmp_path / "settings.json"


# -- dialog: shows current settings -----------------------------------------


def test_dialog_shows_current_theme_and_vault_path(qapp, settings_file):
    current = Settings(theme="dark", vault_path="/some/where.vault")
    dialog = SettingsDialog(current, settings_path=settings_file)
    assert dialog.theme_combo.currentText() == "dark"
    assert dialog.vault_path_edit.text() == "/some/where.vault"


def test_dialog_blank_vault_path_when_default_location(qapp, settings_file):
    dialog = SettingsDialog(Settings(), settings_path=settings_file)
    assert dialog.vault_path_edit.text() == ""


def test_theme_combo_lists_exactly_the_available_themes(qapp, settings_file):
    dialog = SettingsDialog(Settings(), settings_path=settings_file)
    items = [dialog.theme_combo.itemText(i) for i in range(dialog.theme_combo.count())]
    assert items == list(theme.available_themes())


# -- dialog: apply() persists & accepts -------------------------------------


def test_apply_persists_changes_and_round_trips(qapp, settings_file):
    dialog = SettingsDialog(Settings(), settings_path=settings_file)
    dialog.theme_combo.setCurrentText("dark")
    dialog.vault_path_edit.setText("/vault/here.vault")

    result = dialog.apply()

    assert result.theme == "dark"
    assert result.vault_path == "/vault/here.vault"
    # Persisted to disk and reloads identically.
    assert load_settings(settings_file) == result
    assert dialog.settings == result
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_apply_empty_vault_path_means_default_location(qapp, settings_file):
    dialog = SettingsDialog(Settings(vault_path="/old.vault"), settings_path=settings_file)
    dialog.vault_path_edit.setText("   ")  # whitespace -> default (None)

    result = dialog.apply()

    assert result.vault_path is None
    assert load_settings(settings_file).vault_path is None


def test_apply_round_trips_seeded_idle_and_minimize_fields(qapp, settings_file):
    # The idle timeout and lock-on-minimize controls are seeded from the current
    # settings; leaving them untouched and changing only the theme must round-trip
    # those fields unchanged (300s -> 5 min -> 300s; lock_on_minimize stays True).
    current = Settings(
        idle_timeout_seconds=300,
        vault_path=None,
        lock_on_minimize=True,
        theme="light",
    )
    dialog = SettingsDialog(current, settings_path=settings_file)
    dialog.theme_combo.setCurrentText("dark")

    result = dialog.apply()

    assert result.idle_timeout_seconds == 300
    assert result.lock_on_minimize is True
    assert result.theme == "dark"
    assert load_settings(settings_file) == result


# -- dialog: idle-lock + lock-on-minimize controls --------------------------


def test_dialog_shows_idle_timeout_and_lock_on_minimize(qapp, settings_file):
    current = Settings(idle_timeout_seconds=600, lock_on_minimize=True)
    dialog = SettingsDialog(current, settings_path=settings_file)
    assert dialog.idle_lock_checkbox.isChecked()
    assert dialog.idle_lock_minutes.isEnabled()
    assert dialog.idle_lock_minutes.value() == 10  # 600s -> 10 min
    assert dialog.lock_on_minimize_checkbox.isChecked()


def test_dialog_idle_disabled_unchecks_and_disables_spinbox(qapp, settings_file):
    dialog = SettingsDialog(Settings(idle_timeout_seconds=None), settings_path=settings_file)
    assert not dialog.idle_lock_checkbox.isChecked()
    assert not dialog.idle_lock_minutes.isEnabled()


def test_toggling_idle_checkbox_enables_the_spinbox(qapp, settings_file):
    dialog = SettingsDialog(Settings(idle_timeout_seconds=None), settings_path=settings_file)
    assert not dialog.idle_lock_minutes.isEnabled()
    dialog.idle_lock_checkbox.setChecked(True)
    assert dialog.idle_lock_minutes.isEnabled()


def test_apply_enabled_idle_lock_writes_seconds(qapp, settings_file):
    dialog = SettingsDialog(Settings(idle_timeout_seconds=None), settings_path=settings_file)
    dialog.idle_lock_checkbox.setChecked(True)
    dialog.idle_lock_minutes.setValue(3)

    result = dialog.apply()

    assert result.idle_timeout_seconds == 180  # 3 min -> 180s
    assert load_settings(settings_file).idle_timeout_seconds == 180


def test_apply_disabled_idle_lock_writes_none(qapp, settings_file):
    dialog = SettingsDialog(Settings(idle_timeout_seconds=300), settings_path=settings_file)
    dialog.idle_lock_checkbox.setChecked(False)

    result = dialog.apply()

    assert result.idle_timeout_seconds is None
    assert load_settings(settings_file).idle_timeout_seconds is None


def test_apply_lock_on_minimize_round_trips(qapp, settings_file):
    dialog = SettingsDialog(Settings(lock_on_minimize=False), settings_path=settings_file)
    dialog.lock_on_minimize_checkbox.setChecked(True)

    result = dialog.apply()

    assert result.lock_on_minimize is True
    assert load_settings(settings_file).lock_on_minimize is True


def test_cancel_persists_nothing(qapp, settings_file):
    dialog = SettingsDialog(Settings(), settings_path=settings_file)
    dialog.theme_combo.setCurrentText("dark")
    dialog.reject()
    assert not settings_file.exists()


# -- MainWindow wiring ------------------------------------------------------


def test_configure_settings_applies_saved_theme(qapp, settings_file):
    window = MainWindow()
    window.configure_settings(Settings(theme="dark"), settings_path=settings_file)
    assert window.current_theme == "dark"
    assert window.dark_theme_action.isChecked()


def test_make_settings_dialog_reflects_window_settings_and_path(qapp, settings_file):
    window = MainWindow()
    window.configure_settings(
        Settings(theme="dark", vault_path="/v.vault"), settings_path=settings_file
    )
    dialog = window._make_settings_dialog()
    assert dialog.theme_combo.currentText() == "dark"
    assert dialog.vault_path_edit.text() == "/v.vault"

    # Applying through the window's dialog persists to the window's settings path.
    dialog.theme_combo.setCurrentText("light")
    dialog.apply()
    loaded = load_settings(settings_file)
    assert loaded.theme == "light"
    assert loaded.vault_path == "/v.vault"  # untouched field carried through


def test_apply_settings_result_applies_theme_to_live_window(qapp):
    window = MainWindow()
    chosen = Settings(theme="dark", vault_path="/x.vault")
    window._apply_settings_result(chosen)
    assert window.current_theme == "dark"
    assert window.settings == chosen
    assert window.dark_theme_action.isChecked()


def test_view_toggle_persists_theme_when_configured(qapp, settings_file):
    window = MainWindow()
    window.configure_settings(
        Settings(idle_timeout_seconds=120, vault_path="/v.vault", lock_on_minimize=True),
        settings_path=settings_file,
    )
    # The window starts on the (light) saved theme; trigger the View toggle.
    assert window.current_theme == "light"
    window.dark_theme_action.trigger()

    assert window.current_theme == "dark"
    loaded = load_settings(settings_file)
    assert loaded.theme == "dark"
    # The fields the toggle doesn't touch are preserved.
    assert loaded.idle_timeout_seconds == 120
    assert loaded.vault_path == "/v.vault"
    assert loaded.lock_on_minimize is True


def test_view_toggle_does_not_persist_on_an_unconfigured_window(qapp, tmp_path, monkeypatch):
    # A bare MainWindow() (e.g. another unit test) has no settings binding, so
    # toggling the theme must NOT write a settings file -- not even to the
    # default location (pointed here at a tmp path to prove nothing is written).
    settings_file = tmp_path / "should_not_exist.json"
    monkeypatch.setenv("MY_NOTES_SETTINGS", str(settings_file))
    window = MainWindow()
    window.dark_theme_action.trigger()
    assert window.current_theme == "dark"  # the toggle still works in-memory
    assert not settings_file.exists()


# -- app.main vault-path precedence -----------------------------------------


def test_resolve_vault_path_prefers_env_override(monkeypatch, tmp_path):
    import app

    override = tmp_path / "env.vault"
    monkeypatch.setenv(app.VAULT_PATH_ENV, str(override))
    settings = Settings(vault_path=str(tmp_path / "settings.vault"))
    assert app.resolve_vault_path(settings) == override


def test_resolve_vault_path_uses_settings_when_no_env(monkeypatch, tmp_path):
    import app

    monkeypatch.delenv(app.VAULT_PATH_ENV, raising=False)
    configured = tmp_path / "settings.vault"
    settings = Settings(vault_path=str(configured))
    assert app.resolve_vault_path(settings) == configured


def test_resolve_vault_path_defaults_when_neither(monkeypatch):
    import app

    monkeypatch.delenv(app.VAULT_PATH_ENV, raising=False)
    path = app.resolve_vault_path(Settings(vault_path=None))
    assert path.name == "notes.vault"
    assert path.parent.name == ".my_notes"
