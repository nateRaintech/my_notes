"""Theme registry and Qt Style Sheet (QSS) loader (M5).

Pure Python — no Qt — so the theme catalogue and stylesheet loading are
unit-testable without a running ``QApplication``. The UI layer
(:meth:`ui.main_window.MainWindow.apply_theme`) imports this to fetch a theme's
QSS text and apply it to the window; per CLAUDE.md's layering, ``core/`` never
imports ``ui``.

Two themes ship: ``"light"`` is the native Qt look — an *empty* stylesheet, so
toggling back to it clears any styling — and ``"dark"`` is a hand-written dark
stylesheet stored in ``resources/dark.qss``. :data:`DEFAULT_THEME` is the theme a
freshly opened window uses; persisting the user's choice across launches is the
later M5 *Settings* capability, not this module's concern.
"""

from __future__ import annotations

from pathlib import Path

# resources/ sits beside core/ at the project root (core/ -> parent -> root). A
# __file__-relative path works for source runs; bundling resources/dark.qss for
# a PyInstaller build is handled by the M5 packaging capability.
_RESOURCES_DIR = Path(__file__).resolve().parent.parent / "resources"

DEFAULT_THEME = "light"

# Theme name -> its QSS file under resources/, or None for the native Qt look.
_THEME_FILES: dict[str, str | None] = {
    "light": None,
    "dark": "dark.qss",
}


def available_themes() -> tuple[str, ...]:
    """The selectable theme names, in display order."""
    return tuple(_THEME_FILES)


def load_stylesheet(name: str) -> str:
    """Return the Qt Style Sheet text for the theme ``name``.

    ``"light"`` returns ``""`` (the native Qt look — no stylesheet); ``"dark"``
    returns the contents of ``resources/dark.qss``. Raises :class:`ValueError`
    for an unknown theme name.
    """
    if name not in _THEME_FILES:
        choices = ", ".join(available_themes())
        raise ValueError(f"unknown theme {name!r}; choose one of {choices}")
    filename = _THEME_FILES[name]
    if filename is None:
        return ""
    return (_RESOURCES_DIR / filename).read_text(encoding="utf-8")
