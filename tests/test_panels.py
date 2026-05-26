"""Tests for per-panel show/hide, focus mode, and persistence (issue #75).

Covers:
- Each of the 4 panels can be hidden/shown via set_panel_visible() and the
  correct View-menu action stays in sync.
- toggle_panel() flips visibility.
- Focus mode hides notebooks + notelist + preview and restores the prior
  hidden set when toggled off.
- configure_settings() applies saved hidden_panels / panel_sizes /
  editor_sizes on startup.
- A toggle with persistence enabled writes a settings file that round-trips
  back via load_settings().
- Core-level coercion: invalid hidden_panels and invalid sizes fall back to ();
  a valid round-trip preserves all three new fields.
"""

import os  # must be first

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # headless Qt

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.settings import PANEL_KEYS, Settings, load_settings, save_settings  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp():
    """A process-wide QApplication (singleton) for widget construction."""
    yield QApplication.instance() or QApplication([])


def make_window(qapp) -> MainWindow:  # noqa: ARG001 — qapp is a side-effect fixture
    """Return a fresh MainWindow (no vault, no persistence)."""
    return MainWindow()


# ---------------------------------------------------------------------------
# Panel widget / action mapping
# ---------------------------------------------------------------------------


def _widget(window: MainWindow, key: str):
    """Return the widget for panel *key*."""
    return {
        "notebooks": window.notebook_panel,
        "notelist": window.note_pane,
        "source": window.editor.source,
        "preview": window.editor.preview,
    }[key]


def _action(window: MainWindow, key: str):
    """Return the View-menu QAction for panel *key*."""
    return {
        "notebooks": window.toggle_notebooks_action,
        "notelist": window.toggle_notelist_action,
        "source": window.toggle_source_action,
        "preview": window.toggle_preview_action,
    }[key]


# ---------------------------------------------------------------------------
# Per-panel hide / show via set_panel_visible()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", PANEL_KEYS)
def test_set_panel_visible_false_hides_widget(qapp, key):
    # Show the window so parent-visibility is established; then hide the panel
    # and confirm the widget becomes invisible.
    window = make_window(qapp)
    window.show()
    try:
        qapp.processEvents()
        window.set_panel_visible(key, False)
        assert not _widget(window, key).isVisible()
    finally:
        window.close()


@pytest.mark.parametrize("key", PANEL_KEYS)
def test_set_panel_visible_false_unchecks_action(qapp, key):
    window = make_window(qapp)
    window.set_panel_visible(key, False)
    assert not _action(window, key).isChecked()


@pytest.mark.parametrize("key", PANEL_KEYS)
def test_set_panel_visible_true_shows_widget(qapp, key):
    # Show the window, hide a panel, restore it, confirm visibility.
    window = make_window(qapp)
    window.show()
    try:
        qapp.processEvents()
        window.set_panel_visible(key, False)
        window.set_panel_visible(key, True)
        assert _widget(window, key).isVisible()
    finally:
        window.close()


@pytest.mark.parametrize("key", PANEL_KEYS)
def test_set_panel_visible_true_checks_action(qapp, key):
    window = make_window(qapp)
    window.set_panel_visible(key, False)
    window.set_panel_visible(key, True)
    assert _action(window, key).isChecked()


@pytest.mark.parametrize("key", PANEL_KEYS)
def test_is_panel_visible_tracks_hidden_set(qapp, key):
    window = make_window(qapp)
    assert window.is_panel_visible(key)
    window.set_panel_visible(key, False)
    assert not window.is_panel_visible(key)
    window.set_panel_visible(key, True)
    assert window.is_panel_visible(key)


@pytest.mark.parametrize("key", PANEL_KEYS)
def test_toggle_panel_flips_visibility(qapp, key):
    window = make_window(qapp)
    window.toggle_panel(key)  # hide
    assert not window.is_panel_visible(key)
    window.toggle_panel(key)  # show again
    assert window.is_panel_visible(key)


# ---------------------------------------------------------------------------
# Fresh window defaults
# ---------------------------------------------------------------------------


def test_all_panels_visible_by_default(qapp):
    window = make_window(qapp)
    for key in PANEL_KEYS:
        assert window.is_panel_visible(key), f"{key} should be visible by default"


def test_all_actions_checked_by_default(qapp):
    window = make_window(qapp)
    for key in PANEL_KEYS:
        assert _action(window, key).isChecked(), f"action for {key} should be checked by default"


# ---------------------------------------------------------------------------
# Focus mode
# ---------------------------------------------------------------------------


def test_focus_mode_hides_notebooks_notelist_preview(qapp):
    window = make_window(qapp)
    window.set_focus_mode(True)
    assert not window.is_panel_visible("notebooks")
    assert not window.is_panel_visible("notelist")
    assert not window.is_panel_visible("preview")


def test_focus_mode_keeps_source_visible(qapp):
    window = make_window(qapp)
    window.set_focus_mode(True)
    assert window.is_panel_visible("source")


def test_focus_mode_action_checked_when_on(qapp):
    window = make_window(qapp)
    window.set_focus_mode(True)
    assert window.focus_mode_action.isChecked()


def test_focus_mode_off_restores_prior_hidden_set(qapp):
    window = make_window(qapp)
    # Hide one panel before entering focus mode.
    window.set_panel_visible("notelist", False)
    window.set_focus_mode(True)
    window.set_focus_mode(False)
    # notelist was already hidden before focus mode — must remain hidden.
    assert not window.is_panel_visible("notelist")
    # notebooks + preview were visible before — must be visible again.
    assert window.is_panel_visible("notebooks")
    assert window.is_panel_visible("preview")
    assert window.is_panel_visible("source")


def test_focus_mode_off_unchecks_action(qapp):
    window = make_window(qapp)
    window.set_focus_mode(True)
    window.set_focus_mode(False)
    assert not window.focus_mode_action.isChecked()


def test_set_focus_mode_idempotent(qapp):
    """Calling set_focus_mode(True) twice must be a no-op on the second call."""
    window = make_window(qapp)
    # Hide notelist before entering focus mode so the snapshot is non-trivial.
    window.set_panel_visible("notelist", False)
    window.set_focus_mode(True)
    # Second call should be a no-op — focus mode state unchanged.
    window.set_focus_mode(True)
    assert window.is_focus_mode()
    # Disabling focus mode should restore the pre-focus snapshot.
    window.set_focus_mode(False)
    assert not window.is_panel_visible("notelist")  # was hidden before
    assert window.is_panel_visible("notebooks")
    assert window.is_panel_visible("preview")


# ---------------------------------------------------------------------------
# Persistence: configure_settings() applies saved layout
# ---------------------------------------------------------------------------


def test_configure_settings_applies_hidden_panels(qapp):
    window = make_window(qapp)
    s = Settings(hidden_panels=("notebooks", "preview"))
    window.configure_settings(s)
    assert not window.is_panel_visible("notebooks")
    assert not window.is_panel_visible("preview")
    assert window.is_panel_visible("notelist")
    assert window.is_panel_visible("source")


def test_configure_settings_applies_panel_sizes(qapp):
    # Verify that configure_settings() with non-empty panel_sizes calls
    # splitter.setSizes(), changing the proportions away from defaults.
    # We use sizes with a deliberately large third pane (editor) vs defaults so
    # the first pane proportion shrinks noticeably.
    window = make_window(qapp)
    window.show()
    try:
        qapp.processEvents()
        default_sizes = window.splitter.sizes()
        # Give the first pane (notebooks) a much larger share than default.
        s = Settings(panel_sizes=(600, 200, 200))
        window.configure_settings(s)
        qapp.processEvents()
        new_sizes = window.splitter.sizes()
        # The first pane grew relative to the default proportions.
        assert new_sizes[0] > default_sizes[0]
    finally:
        window.close()


def test_configure_settings_applies_editor_sizes(qapp):
    # Verify configure_settings() with editor_sizes changes the editor splitter
    # proportions; exact pixels are not asserted (Qt clamps to available width).
    window = make_window(qapp)
    window.show()
    try:
        qapp.processEvents()
        # Default editor sizes are approximately equal; give source a 3:1 advantage.
        s = Settings(editor_sizes=(900, 100))
        window.configure_settings(s)
        qapp.processEvents()
        ed_sizes = window.editor.splitter.sizes()
        assert ed_sizes[0] > ed_sizes[1]
    finally:
        window.close()


def test_configure_settings_empty_sizes_leave_defaults(qapp):
    window = make_window(qapp)
    original_sizes = window.splitter.sizes()
    s = Settings()  # empty tuples
    window.configure_settings(s)
    assert window.splitter.sizes() == original_sizes


# ---------------------------------------------------------------------------
# Persistence: toggle writes + load_settings reads back
# ---------------------------------------------------------------------------


def test_toggle_panel_writes_settings_file(qapp, tmp_path):
    window = make_window(qapp)
    path = tmp_path / "settings.json"
    window.configure_settings(Settings(), settings_path=path)
    window.set_panel_visible("notebooks", False)
    loaded = load_settings(path)
    assert "notebooks" in loaded.hidden_panels


def test_toggle_panel_visible_removes_from_file(qapp, tmp_path):
    window = make_window(qapp)
    path = tmp_path / "settings.json"
    window.configure_settings(Settings(), settings_path=path)
    window.set_panel_visible("notebooks", False)
    window.set_panel_visible("notebooks", True)
    loaded = load_settings(path)
    assert "notebooks" not in loaded.hidden_panels


# ---------------------------------------------------------------------------
# Shortcuts are wired
# ---------------------------------------------------------------------------


def test_panel_actions_have_expected_shortcuts(qapp):
    window = make_window(qapp)
    from PySide6.QtGui import QKeySequence  # noqa: E402

    assert window.toggle_notebooks_action.shortcut() == QKeySequence("Ctrl+Shift+1")
    assert window.toggle_notelist_action.shortcut() == QKeySequence("Ctrl+Shift+2")
    assert window.toggle_source_action.shortcut() == QKeySequence("Ctrl+Shift+3")
    assert window.toggle_preview_action.shortcut() == QKeySequence("Ctrl+Shift+4")
    assert window.focus_mode_action.shortcut() == QKeySequence("Ctrl+Shift+F")


# ---------------------------------------------------------------------------
# Core-level coercion tests (appended here per the brief)
# ---------------------------------------------------------------------------


def test_invalid_hidden_panels_non_list_falls_back_to_empty(tmp_path):
    path = tmp_path / "s.json"
    import json

    path.write_text(json.dumps({"hidden_panels": "notebooks"}), encoding="utf-8")
    s = load_settings(path)
    assert s.hidden_panels == ()


def test_invalid_hidden_panels_unknown_keys_filtered(tmp_path):
    path = tmp_path / "s.json"
    import json

    path.write_text(json.dumps({"hidden_panels": ["notebooks", "bogus"]}), encoding="utf-8")
    s = load_settings(path)
    assert s.hidden_panels == ("notebooks",)


def test_invalid_hidden_panels_all_unknown_gives_empty(tmp_path):
    path = tmp_path / "s.json"
    import json

    path.write_text(json.dumps({"hidden_panels": ["not_a_panel", 42]}), encoding="utf-8")
    s = load_settings(path)
    assert s.hidden_panels == ()


def test_hidden_panels_deduped(tmp_path):
    path = tmp_path / "s.json"
    import json

    path.write_text(json.dumps({"hidden_panels": ["notebooks", "notebooks", "preview"]}), encoding="utf-8")
    s = load_settings(path)
    assert s.hidden_panels == ("notebooks", "preview")


def test_invalid_panel_sizes_non_list_falls_back_to_empty(tmp_path):
    path = tmp_path / "s.json"
    import json

    path.write_text(json.dumps({"panel_sizes": "wide"}), encoding="utf-8")
    s = load_settings(path)
    assert s.panel_sizes == ()


def test_invalid_panel_sizes_with_non_positive_falls_back_to_empty(tmp_path):
    path = tmp_path / "s.json"
    import json

    path.write_text(json.dumps({"panel_sizes": [200, 0, 400]}), encoding="utf-8")
    s = load_settings(path)
    assert s.panel_sizes == ()


def test_invalid_panel_sizes_with_bool_falls_back_to_empty(tmp_path):
    path = tmp_path / "s.json"
    import json

    path.write_text(json.dumps({"panel_sizes": [True, 300, 400]}), encoding="utf-8")
    s = load_settings(path)
    assert s.panel_sizes == ()


def test_valid_panel_sizes_round_trip(tmp_path):
    path = tmp_path / "s.json"
    s = Settings(panel_sizes=(220, 300, 480))
    save_settings(s, path)
    loaded = load_settings(path)
    assert loaded.panel_sizes == (220, 300, 480)


def test_valid_editor_sizes_round_trip(tmp_path):
    path = tmp_path / "s.json"
    s = Settings(editor_sizes=(350, 350))
    save_settings(s, path)
    loaded = load_settings(path)
    assert loaded.editor_sizes == (350, 350)


def test_valid_hidden_panels_round_trip(tmp_path):
    path = tmp_path / "s.json"
    s = Settings(hidden_panels=("notebooks", "preview"))
    save_settings(s, path)
    loaded = load_settings(path)
    assert loaded.hidden_panels == ("notebooks", "preview")


def test_full_new_fields_round_trip(tmp_path):
    path = tmp_path / "s.json"
    s = Settings(
        hidden_panels=("notelist",),
        panel_sizes=(180, 320, 500),
        editor_sizes=(400, 280),
    )
    save_settings(s, path)
    loaded = load_settings(path)
    assert loaded == s
