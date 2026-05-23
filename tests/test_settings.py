"""Tests for the Qt-free app settings model + JSON persistence (``core/settings.py``, M5).

These run without Qt — ``core/`` stays import-free of PySide6 (CLAUDE.md
layering). They cover the loader/saver *contract*: a clean round-trip of a
non-default ``Settings``, and — most importantly — that loading is **tolerant**,
falling back to per-field defaults for a missing file, corrupt JSON, the wrong
top-level type, unknown keys, and invalid field values, so a bad settings file
can never crash app startup. No editorial pins on specific default values beyond
the behavioural guarantees (e.g. the default idle timeout disables auto-lock).
"""

from __future__ import annotations

import json

from core import settings
from core.theme import DEFAULT_THEME


# --- defaults ---------------------------------------------------------------


def test_default_settings_disable_autolock_and_use_default_locations():
    s = settings.DEFAULT_SETTINGS
    assert s.idle_timeout_seconds is None  # auto-lock disabled by default
    assert s.vault_path is None  # None => use the default vault location
    assert s.lock_on_minimize is False
    assert s.theme == DEFAULT_THEME


def test_default_theme_is_a_known_theme():
    # Reuse core.theme as the single source of valid theme names.
    from core.theme import available_themes

    assert settings.DEFAULT_SETTINGS.theme in available_themes()


def test_settings_is_immutable():
    s = settings.Settings()
    try:
        s.theme = "dark"  # type: ignore[misc]
    except AttributeError:
        return  # frozen dataclass — expected
    raise AssertionError("Settings should be immutable (frozen)")


# --- round-trip -------------------------------------------------------------


def test_save_then_load_round_trips_non_default(tmp_path):
    path = tmp_path / "settings.json"
    original = settings.Settings(
        idle_timeout_seconds=900,
        vault_path="/some/where/notes.vault",
        lock_on_minimize=True,
        theme="dark",
    )
    settings.save_settings(original, path)
    assert settings.load_settings(path) == original


def test_save_writes_a_json_object(tmp_path):
    path = tmp_path / "settings.json"
    settings.save_settings(settings.Settings(theme="dark"), path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data["theme"] == "dark"


def test_save_creates_missing_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "settings.json"
    settings.save_settings(settings.Settings(), path)
    assert path.exists()


# --- tolerant loading -------------------------------------------------------


def test_load_missing_file_returns_defaults(tmp_path):
    assert settings.load_settings(tmp_path / "absent.json") == settings.DEFAULT_SETTINGS


def test_load_corrupt_json_returns_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert settings.load_settings(path) == settings.DEFAULT_SETTINGS


def test_load_non_object_json_returns_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert settings.load_settings(path) == settings.DEFAULT_SETTINGS


def test_partial_file_fills_missing_fields_with_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    loaded = settings.load_settings(path)
    assert loaded.theme == "dark"
    assert loaded.idle_timeout_seconds is None
    assert loaded.vault_path is None
    assert loaded.lock_on_minimize is False


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"theme": "dark", "totally_unknown": 123}), encoding="utf-8"
    )
    assert settings.load_settings(path).theme == "dark"


def test_unknown_theme_falls_back_to_default(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"theme": "solarized"}), encoding="utf-8")
    assert settings.load_settings(path).theme == DEFAULT_THEME


def test_valid_idle_timeout_round_trips(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"idle_timeout_seconds": 600}), encoding="utf-8")
    assert settings.load_settings(path).idle_timeout_seconds == 600


def test_non_positive_idle_timeout_disables_autolock(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"idle_timeout_seconds": 0}), encoding="utf-8")
    assert settings.load_settings(path).idle_timeout_seconds is None


def test_non_integer_idle_timeout_falls_back_to_none(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"idle_timeout_seconds": "fifteen"}), encoding="utf-8")
    assert settings.load_settings(path).idle_timeout_seconds is None


def test_bool_idle_timeout_is_rejected(tmp_path):
    # bool is a subclass of int — True must not sneak through as a "1 second" timeout.
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"idle_timeout_seconds": True}), encoding="utf-8")
    assert settings.load_settings(path).idle_timeout_seconds is None


def test_non_bool_lock_on_minimize_falls_back_to_default(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"lock_on_minimize": "yes"}), encoding="utf-8")
    assert settings.load_settings(path).lock_on_minimize is False


def test_non_string_vault_path_falls_back_to_none(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"vault_path": 42}), encoding="utf-8")
    assert settings.load_settings(path).vault_path is None


# --- default path + env override --------------------------------------------


def test_default_settings_path_honors_env(monkeypatch, tmp_path):
    override = tmp_path / "custom-settings.json"
    monkeypatch.setenv(settings.SETTINGS_PATH_ENV, str(override))
    assert settings.default_settings_path() == override


def test_default_settings_path_without_env(monkeypatch):
    monkeypatch.delenv(settings.SETTINGS_PATH_ENV, raising=False)
    path = settings.default_settings_path()
    assert path.name == "settings.json"
    assert path.parent.name == ".my_notes"


def test_save_and_load_use_default_path_when_none(monkeypatch, tmp_path):
    # With no explicit path, save/load go through default_settings_path().
    target = tmp_path / "settings.json"
    monkeypatch.setenv(settings.SETTINGS_PATH_ENV, str(target))
    original = settings.Settings(idle_timeout_seconds=300, theme="dark")
    settings.save_settings(original)
    assert target.exists()
    assert settings.load_settings() == original
