"""Persistent application settings (M5).

User-configurable preferences that live *outside* the encrypted vault: the
idle-lock timeout, the vault file location, whether to lock when the window is
minimised, and the chosen theme. They are stored as a small plaintext JSON file
(``~/.my_notes/settings.json`` by default), for the same chicken-and-egg reason
the Argon2 salt sidecar is plaintext (``core/vault.py``): the vault *location* is
needed before the encrypted vault can be opened, so it cannot live inside it.
None of these values are secret.

Pure Python, no Qt: ``core/`` is the unit-testable layer (CLAUDE.md). This module
owns only the settings *model* and its persistence; the UI that edits these
values (a Settings dialog) and the code that *applies* them (handing
:attr:`Settings.idle_timeout_seconds` to :class:`core.vault.Vault`, resolving the
vault path in ``app.main``, the lock-on-minimise window handler, applying the
theme on launch) is the follow-up M5 *Settings* slice.

Loading is deliberately **tolerant**: a missing, corrupt, or partially-invalid
settings file must never crash app startup. :func:`load_settings` falls back to
the per-field default for anything it cannot validate, so the worst a damaged
file can do is reset preferences to their defaults.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from core.theme import DEFAULT_THEME, available_themes

# Environment override for the settings file location (mirrors MY_NOTES_VAULT in
# app.py). Used by tests and power users.
SETTINGS_PATH_ENV = "MY_NOTES_SETTINGS"

# Valid panel key strings — the canonical order matches the View-menu order.
PANEL_KEYS = ("notebooks", "notelist", "source", "preview")


@dataclass(frozen=True)
class Settings:
    """User preferences persisted across launches.

    Defaults preserve the app's current behaviour: auto-lock disabled, the
    default vault location, no lock-on-minimise, and the default theme.
    """

    # Seconds of inactivity before the vault auto-locks; None disables auto-lock
    # (matches Vault.idle_timeout's "None = disabled" convention).
    idle_timeout_seconds: int | None = None
    # Absolute/relative path to the vault file; None => use the default location.
    vault_path: str | None = None
    # Lock the vault when the main window is minimised.
    lock_on_minimize: bool = False
    # Theme name (see core.theme.available_themes()).
    theme: str = DEFAULT_THEME
    # Panel visibility — keys from PANEL_KEYS that are currently hidden.
    hidden_panels: tuple[str, ...] = ()
    # Main splitter pane widths (pixels); empty tuple = use defaults.
    panel_sizes: tuple[int, ...] = ()
    # Editor sub-splitter widths (pixels); empty tuple = use defaults.
    editor_sizes: tuple[int, ...] = ()


DEFAULT_SETTINGS = Settings()


def default_settings_path() -> Path:
    """Where the settings file lives.

    Honours the ``MY_NOTES_SETTINGS`` environment variable if set; otherwise
    defaults to ``~/.my_notes/settings.json`` (beside the default vault).
    """
    override = os.environ.get(SETTINGS_PATH_ENV)
    if override:
        return Path(override)
    return Path.home() / ".my_notes" / "settings.json"


def _coerce_idle_timeout(value: object) -> int | None:
    """A positive ``int`` of seconds, or None (disabled).

    ``bool`` is a subclass of ``int`` but is never a valid timeout, so it is
    rejected explicitly; non-positive values disable auto-lock.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


def _coerce_vault_path(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _coerce_lock_on_minimize(value: object) -> bool:
    return value if isinstance(value, bool) else DEFAULT_SETTINGS.lock_on_minimize


def _coerce_theme(value: object) -> str:
    return value if isinstance(value, str) and value in available_themes() else DEFAULT_THEME


def _coerce_hidden_panels(value: object) -> tuple[str, ...]:
    """Tuple of known PANEL_KEYS values, deduped, preserving order; () on any error."""
    if not isinstance(value, list):
        return ()
    seen: set[str] = set()
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item in PANEL_KEYS and item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


def _coerce_sizes(value: object) -> tuple[int, ...]:
    """Tuple of positive ints; () when the list is missing, empty, or invalid."""
    if not isinstance(value, list) or not value:
        return ()
    result: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            return ()
        result.append(item)
    return tuple(result)


def _from_mapping(data: dict[str, object]) -> Settings:
    """Build a validated :class:`Settings` from a raw JSON mapping.

    Each field is validated independently; anything missing or invalid falls
    back to its default, so a partially-corrupt file yields a usable result.
    """
    return Settings(
        idle_timeout_seconds=_coerce_idle_timeout(data.get("idle_timeout_seconds")),
        vault_path=_coerce_vault_path(data.get("vault_path")),
        lock_on_minimize=_coerce_lock_on_minimize(data.get("lock_on_minimize")),
        theme=_coerce_theme(data.get("theme")),
        hidden_panels=_coerce_hidden_panels(data.get("hidden_panels")),
        panel_sizes=_coerce_sizes(data.get("panel_sizes")),
        editor_sizes=_coerce_sizes(data.get("editor_sizes")),
    )


def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    """Load settings from ``path`` (or :func:`default_settings_path` if None).

    Tolerant by design: a missing file, unreadable file, invalid JSON, or a
    top-level value that is not a JSON object all yield :data:`DEFAULT_SETTINGS`;
    individual invalid fields fall back to their defaults (see
    :func:`_from_mapping`). Never raises for a bad settings file.
    """
    settings_path = Path(path) if path is not None else default_settings_path()
    try:
        raw = settings_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return DEFAULT_SETTINGS
    if not isinstance(data, dict):
        return DEFAULT_SETTINGS
    return _from_mapping(data)


def save_settings(
    settings: Settings, path: str | os.PathLike[str] | None = None
) -> None:
    """Write ``settings`` as pretty JSON to ``path`` (or the default location).

    Creates the parent directory if needed (mirrors ``Vault._write_meta``).
    """
    settings_path = Path(path) if path is not None else default_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
