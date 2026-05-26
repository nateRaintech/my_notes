"""Tests for dock-based panel visibility, focus mode, and persistence (issue #77).

Covers:
- The three QDockWidgets exist with the correct objectNames and contain the
  expected widgets.
- Each dock's toggleViewAction() hides/shows the dock correctly.
- set_focus_mode(True) hides all three docks; set_focus_mode(False) restores them.
- is_focus_mode() and the focus_mode_action stay in sync.
- Idempotency: calling set_focus_mode(True) twice is a no-op.
- configure_settings() with a saved window_state calls restoreState() without error.
- A closeEvent with persistence enabled writes window_state/window_geometry to a
  temp settings file that round-trips back via load_settings().
- Core-level coercion: invalid window_state/window_geometry fall back to None.
- Valid base64 strings round-trip correctly through Settings.
"""

import os  # must be first

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # headless Qt

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from core.settings import Settings, load_settings, save_settings  # noqa: E402
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
# Dock structure: objectNames and contained widgets
# ---------------------------------------------------------------------------


def test_dock_notebooks_exists_with_correct_objectname(qapp):
    window = make_window(qapp)
    assert window.dock_notebooks.objectName() == "dock_notebooks"


def test_dock_notelist_exists_with_correct_objectname(qapp):
    window = make_window(qapp)
    assert window.dock_notelist.objectName() == "dock_notelist"


def test_dock_preview_exists_with_correct_objectname(qapp):
    window = make_window(qapp)
    assert window.dock_preview.objectName() == "dock_preview"


def test_dock_notebooks_contains_notebook_tree(qapp):
    window = make_window(qapp)
    # The dock wraps the notebook_tree widget directly.
    assert window.dock_notebooks.widget() is window.notebook_tree


def test_dock_preview_contains_editor_preview(qapp):
    window = make_window(qapp)
    assert window.dock_preview.widget() is window.editor.preview


def test_dock_notelist_contains_search_input_and_note_list(qapp):
    window = make_window(qapp)
    container = window.dock_notelist.widget()
    assert container is not None
    # The search_input and note_list should be descendants of the container.
    assert window.search_input.parent() is container
    assert window.note_list.parent() is container


def test_central_widget_is_editor_source(qapp):
    window = make_window(qapp)
    assert window.centralWidget() is window.editor.source


def test_dock_nesting_enabled(qapp):
    window = make_window(qapp)
    assert window.isDockNestingEnabled()


# ---------------------------------------------------------------------------
# Dock features: movable, floatable, closable
# ---------------------------------------------------------------------------


def test_docks_have_required_features(qapp):
    from PySide6.QtWidgets import QDockWidget

    required = (
        QDockWidget.DockWidgetFeature.DockWidgetMovable
        | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        | QDockWidget.DockWidgetFeature.DockWidgetClosable
    )
    window = make_window(qapp)
    for dock in (window.dock_notebooks, window.dock_notelist, window.dock_preview):
        assert dock.features() & required == required


# ---------------------------------------------------------------------------
# toggleViewAction() hides/shows each dock
# ---------------------------------------------------------------------------


def test_toggle_view_action_hides_notebooks_dock(qapp):
    window = make_window(qapp)
    window.show()
    try:
        qapp.processEvents()
        window.dock_notebooks.toggleViewAction().trigger()
        qapp.processEvents()
        assert not window.dock_notebooks.isVisible()
    finally:
        window.close()


def test_toggle_view_action_shows_notebooks_dock_again(qapp):
    window = make_window(qapp)
    window.show()
    try:
        qapp.processEvents()
        # Hide then show.
        window.dock_notebooks.toggleViewAction().trigger()
        qapp.processEvents()
        window.dock_notebooks.toggleViewAction().trigger()
        qapp.processEvents()
        assert window.dock_notebooks.isVisible()
    finally:
        window.close()


def test_toggle_view_action_hides_notelist_dock(qapp):
    window = make_window(qapp)
    window.show()
    try:
        qapp.processEvents()
        window.dock_notelist.toggleViewAction().trigger()
        qapp.processEvents()
        assert not window.dock_notelist.isVisible()
    finally:
        window.close()


def test_toggle_view_action_hides_preview_dock(qapp):
    window = make_window(qapp)
    window.show()
    try:
        qapp.processEvents()
        window.dock_preview.toggleViewAction().trigger()
        qapp.processEvents()
        assert not window.dock_preview.isVisible()
    finally:
        window.close()


# ---------------------------------------------------------------------------
# View-menu toggle actions wired to docks
# ---------------------------------------------------------------------------


def test_toggle_notebooks_action_is_docks_toggle_view_action(qapp):
    window = make_window(qapp)
    assert window.toggle_notebooks_action is window.dock_notebooks.toggleViewAction()


def test_toggle_notelist_action_is_docks_toggle_view_action(qapp):
    window = make_window(qapp)
    assert window.toggle_notelist_action is window.dock_notelist.toggleViewAction()


def test_toggle_preview_action_is_docks_toggle_view_action(qapp):
    window = make_window(qapp)
    assert window.toggle_preview_action is window.dock_preview.toggleViewAction()


# ---------------------------------------------------------------------------
# Keyboard shortcuts on dock toggle actions
# ---------------------------------------------------------------------------


def test_dock_toggle_shortcuts(qapp):
    from PySide6.QtGui import QKeySequence

    window = make_window(qapp)
    assert window.toggle_notebooks_action.shortcut() == QKeySequence("Ctrl+Shift+1")
    assert window.toggle_notelist_action.shortcut() == QKeySequence("Ctrl+Shift+2")
    assert window.toggle_preview_action.shortcut() == QKeySequence("Ctrl+Shift+4")
    # Ctrl+Shift+3 is intentionally absent (editor source is the central widget).
    assert window.focus_mode_action.shortcut() == QKeySequence("Ctrl+Shift+F")


# ---------------------------------------------------------------------------
# Focus mode
# ---------------------------------------------------------------------------


def test_focus_mode_hides_all_docks(qapp):
    window = make_window(qapp)
    window.show()
    try:
        qapp.processEvents()
        window.set_focus_mode(True)
        qapp.processEvents()
        assert not window.dock_notebooks.isVisible()
        assert not window.dock_notelist.isVisible()
        assert not window.dock_preview.isVisible()
    finally:
        window.close()


def test_focus_mode_central_widget_remains_accessible(qapp):
    window = make_window(qapp)
    window.set_focus_mode(True)
    # The central widget (editor source) is always present.
    assert window.centralWidget() is window.editor.source


def test_focus_mode_action_checked_when_on(qapp):
    window = make_window(qapp)
    window.set_focus_mode(True)
    assert window.focus_mode_action.isChecked()


def test_focus_mode_off_unchecks_action(qapp):
    window = make_window(qapp)
    window.set_focus_mode(True)
    window.set_focus_mode(False)
    assert not window.focus_mode_action.isChecked()


def test_is_focus_mode_tracks_state(qapp):
    window = make_window(qapp)
    assert not window.is_focus_mode()
    window.set_focus_mode(True)
    assert window.is_focus_mode()
    window.set_focus_mode(False)
    assert not window.is_focus_mode()


def test_set_focus_mode_idempotent(qapp):
    """Calling set_focus_mode(True) twice must be a no-op on the second call."""
    window = make_window(qapp)
    window.set_focus_mode(True)
    window.set_focus_mode(True)  # second call — no-op
    assert window.is_focus_mode()


def test_focus_mode_off_restores_docks(qapp):
    """After exiting focus mode, previously-visible docks become visible again."""
    window = make_window(qapp)
    window.show()
    try:
        qapp.processEvents()
        window.set_focus_mode(True)
        qapp.processEvents()
        window.set_focus_mode(False)
        qapp.processEvents()
        # All three docks should be restored.
        assert window.dock_notebooks.isVisible()
        assert window.dock_notelist.isVisible()
        assert window.dock_preview.isVisible()
    finally:
        window.close()


def test_focus_mode_off_when_dock_was_hidden_before_keeps_it_hidden(qapp):
    """A dock hidden before focus mode is entered should remain hidden after exit."""
    window = make_window(qapp)
    window.show()
    try:
        qapp.processEvents()
        # Hide the notelist dock before entering focus mode.
        window.dock_notelist.hide()
        qapp.processEvents()
        window.set_focus_mode(True)
        qapp.processEvents()
        window.set_focus_mode(False)
        qapp.processEvents()
        # The saved state before focus mode had notelist hidden — restoreState
        # should restore that, leaving notelist hidden.
        assert not window.dock_notelist.isVisible()
        # notebooks and preview were visible before — should be visible again.
        assert window.dock_notebooks.isVisible()
        assert window.dock_preview.isVisible()
    finally:
        window.close()


# ---------------------------------------------------------------------------
# Persistence: configure_settings() applies saved layout
# ---------------------------------------------------------------------------


def test_configure_settings_with_saved_window_state_does_not_raise(qapp, tmp_path):
    """configure_settings() with a valid base64 window_state calls restoreState()."""
    # Get a real state from a window.
    source_window = make_window(qapp)
    source_window.show()
    qapp.processEvents()
    state_b64 = MainWindow._encode_state(source_window.saveState())
    geom_b64 = MainWindow._encode_state(source_window.saveGeometry())
    source_window.close()

    # Apply to a fresh window via configure_settings().
    path = tmp_path / "settings.json"
    s = Settings(window_state=state_b64, window_geometry=geom_b64)
    target_window = make_window(qapp)
    # Must not raise.
    target_window.configure_settings(s, settings_path=path)


def test_configure_settings_none_state_does_not_raise(qapp, tmp_path):
    """configure_settings() with None state/geometry should just use defaults."""
    path = tmp_path / "settings.json"
    window = make_window(qapp)
    window.configure_settings(Settings(), settings_path=path)  # both None — no-op


# ---------------------------------------------------------------------------
# Persistence: closeEvent writes window_state / window_geometry
# ---------------------------------------------------------------------------


def test_close_with_persistence_writes_window_state(qapp, tmp_path):
    path = tmp_path / "settings.json"
    window = make_window(qapp)
    window.show()
    try:
        qapp.processEvents()
        window.configure_settings(Settings(), settings_path=path)
        window.close()
        loaded = load_settings(path)
        assert loaded.window_state is not None
        assert loaded.window_geometry is not None
    finally:
        pass  # window already closed


def test_close_writes_valid_base64_that_reloads(qapp, tmp_path):
    """The persisted state is valid base64 that can be decoded without error."""
    import base64

    path = tmp_path / "settings.json"
    window = make_window(qapp)
    window.show()
    qapp.processEvents()
    window.configure_settings(Settings(), settings_path=path)
    window.close()
    loaded = load_settings(path)
    # Must decode without raising.
    assert loaded.window_state is not None
    decoded = base64.b64decode(loaded.window_state)
    assert len(decoded) > 0


# ---------------------------------------------------------------------------
# Core-level settings coercion for window_state / window_geometry
# ---------------------------------------------------------------------------


def test_invalid_window_state_non_string_falls_back_to_none(tmp_path):
    import json

    path = tmp_path / "s.json"
    path.write_text(json.dumps({"window_state": 42}), encoding="utf-8")
    s = load_settings(path)
    assert s.window_state is None


def test_invalid_window_geometry_non_string_falls_back_to_none(tmp_path):
    import json

    path = tmp_path / "s.json"
    path.write_text(json.dumps({"window_geometry": ["not", "a", "string"]}), encoding="utf-8")
    s = load_settings(path)
    assert s.window_geometry is None


def test_null_window_state_falls_back_to_none(tmp_path):
    import json

    path = tmp_path / "s.json"
    path.write_text(json.dumps({"window_state": None}), encoding="utf-8")
    s = load_settings(path)
    assert s.window_state is None


def test_valid_window_state_round_trips(tmp_path):
    path = tmp_path / "s.json"
    s = Settings(window_state="dGVzdA==", window_geometry="Z2VvbQ==")
    save_settings(s, path)
    loaded = load_settings(path)
    assert loaded.window_state == "dGVzdA=="
    assert loaded.window_geometry == "Z2VvbQ=="


def test_full_settings_round_trip(tmp_path):
    path = tmp_path / "s.json"
    s = Settings(
        idle_timeout_seconds=600,
        theme="dark",
        window_state="dGVzdA==",
        window_geometry="Z2VvbQ==",
    )
    save_settings(s, path)
    loaded = load_settings(path)
    assert loaded == s
