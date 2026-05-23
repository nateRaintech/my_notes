"""Tests for the Qt-free theme registry and QSS loader (``core/theme.py``, M5).

These run without Qt — ``core/`` stays import-free of PySide6 (CLAUDE.md
layering). They cover the loader's *contract* (which themes exist, that dark
carries styling while light is the native/empty look, and that an unknown name
is rejected), deliberately not the specific colors in the stylesheet, which are
editorial rather than behavioral.
"""

from __future__ import annotations

import pytest

from core import theme


def test_available_themes_includes_light_and_dark():
    names = theme.available_themes()
    assert "light" in names
    assert "dark" in names


def test_default_theme_is_a_known_theme():
    assert theme.DEFAULT_THEME in theme.available_themes()


def test_load_dark_stylesheet_is_non_empty():
    qss = theme.load_stylesheet("dark")
    assert isinstance(qss, str)
    assert qss.strip()  # the dark theme actually carries styling


def test_load_light_stylesheet_is_empty_native_look():
    assert theme.load_stylesheet("light") == ""


def test_load_unknown_theme_raises():
    with pytest.raises(ValueError):
        theme.load_stylesheet("solarized")


def test_every_available_theme_loads():
    # Loading any advertised theme must succeed (no missing resource file).
    for name in theme.available_themes():
        assert isinstance(theme.load_stylesheet(name), str)
