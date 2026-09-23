"""Window-level hidden text: hide, copy, edit, lock, and output paths (#113)."""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.hidden_text import HiddenTextStore, hidden_markdown
from core.images import ImageStore
from core.repository import Repository
from core.settings import Settings

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.hidden_text import MASK_TEXT, hidden_rect, rendered_hidden  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.note_export import note_html  # noqa: E402

SECRET = "hunter2-Xq9"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def conn():
    c = sqlcipher.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    schema.migrate(c)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture
def repo(conn):
    return Repository(conn)


@pytest.fixture
def hidden(conn):
    return HiddenTextStore(conn)


@pytest.fixture
def window(qapp, conn, repo, hidden):
    w = MainWindow()
    w.bind_autosave(repo, debounce=999)
    w.bind_images(ImageStore(conn))
    w.bind_hidden_text(hidden)
    w.resize(1200, 800)
    w.show()
    qapp.processEvents()
    yield w
    w.hide()


@pytest.fixture
def clipboard(qapp):
    board = QGuiApplication.clipboard()
    board.clear()
    yield board
    board.clear()


def _open(qapp, window, repo, body):
    window.load_note(repo.create_note(title="n", body=body))
    qapp.processEvents()
    return window.tabbed_editor.active_tab


def _select(tab, text):
    body = tab.markdown()
    start = body.index(text)
    cursor = tab.source.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(start + len(text), QTextCursor.MoveMode.KeepAnchor)
    tab.source.setTextCursor(cursor)


def _action(menu, text):
    return next((a for a in menu.actions() if a.text() == text), None)


def _pill(window):
    return rendered_hidden(window.preview.document())[0]


# -- hiding -------------------------------------------------------------------------


def test_hide_selection_moves_the_text_into_the_vault(qapp, window, repo, hidden):
    tab = _open(qapp, window, repo, f"vpn password: {SECRET}\nnext line")
    _select(tab, SECRET)

    assert window.hide_selection() is True

    body = tab.markdown()
    assert SECRET not in body
    assert body.startswith("vpn password: ![hidden](mnsec:")
    assert body.endswith("\nnext line")
    hidden_id = _pill(window).hidden_id
    assert hidden.get(hidden_id) == SECRET


def test_hidden_text_never_reaches_the_saved_note_or_the_preview(qapp, window, repo):
    tab = _open(qapp, window, repo, f"pw: {SECRET}")
    _select(tab, SECRET)
    window.hide_selection()
    tab.flush()
    qapp.processEvents()

    saved = repo.get_note(tab.note_id)
    assert SECRET not in saved.body and SECRET not in saved.title
    assert repo.search_notes(SECRET) == []
    document = window.preview.document()
    assert SECRET not in document.toPlainText()
    assert SECRET not in document.toHtml()


def test_hiding_is_one_undo_step(qapp, window, repo):
    tab = _open(qapp, window, repo, f"pw: {SECRET}")
    _select(tab, SECRET)
    window.hide_selection()
    tab.source.undo()
    assert tab.markdown() == f"pw: {SECRET}"


def test_multi_line_selections_keep_their_line_breaks(qapp, window, repo, hidden):
    tab = _open(qapp, window, repo, "key:\nline-a\nline-b\nend")
    _select(tab, "line-a\nline-b")
    window.hide_selection()
    assert hidden.get(_pill(window).hidden_id) == "line-a\nline-b"


def test_blank_selection_hides_nothing(qapp, window, repo):
    tab = _open(qapp, window, repo, "a    b")
    _select(tab, "    ")
    assert window.hide_selection() is False
    assert tab.markdown() == "a    b"


def test_hide_text_menu_item_follows_the_selection(qapp, window, repo):
    tab = _open(qapp, window, repo, f"pw: {SECRET}")
    assert not _action(window.build_editor_context_menu(tab.source), "Hide text").isEnabled()

    _select(tab, SECRET)
    action = _action(window.build_editor_context_menu(tab.source), "Hide text")
    assert action.isEnabled()
    action.trigger()
    assert SECRET not in tab.markdown()


def test_hide_text_is_disabled_after_lock(qapp, window, repo):
    tab = _open(qapp, window, repo, f"pw: {SECRET}")
    window.bind_hidden_text(None)
    _select(tab, SECRET)
    assert not _action(window.build_editor_context_menu(tab.source), "Hide text").isEnabled()
    assert window.hide_selection() is False


# -- copying --------------------------------------------------------------------------


def test_copy_hidden_puts_the_value_on_the_clipboard_with_a_timeout(
    qapp, window, repo, hidden, clipboard
):
    window.settings = Settings(clipboard_clear_seconds=12)
    _open(qapp, window, repo, f"pw: {hidden_markdown(hidden.add(SECRET))}")

    assert window.copy_hidden(_pill(window)) is True
    assert clipboard.text() == SECRET
    assert window.clipboard_guard.timer.isActive()
    assert window.clipboard_guard.timer.interval() == 12_000
    assert "12" in window.statusBar().currentMessage()


def test_clicking_the_pill_copies(qapp, window, repo, hidden, clipboard):
    _open(qapp, window, repo, f"pw: {hidden_markdown(hidden.add(SECRET))}")
    rect = hidden_rect(window.preview, _pill(window))
    QTest.mouseClick(window.preview.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert clipboard.text() == SECRET


def test_copying_a_dangling_reference_reports_it(qapp, window, repo, clipboard):
    _open(qapp, window, repo, "pw: ![hidden](mnsec:999)")
    assert window.copy_hidden(_pill(window)) is False
    assert clipboard.text() == ""
    assert "no longer exists" in window.statusBar().currentMessage()


def test_preview_menu_over_a_pill_offers_copy_and_edit(qapp, window, repo, hidden):
    _open(qapp, window, repo, f"pw: {hidden_markdown(hidden.add(SECRET))}")
    rect = hidden_rect(window.preview, _pill(window))
    menu = window.build_preview_context_menu(rect.center())
    assert _action(menu, "Copy hidden text") is not None
    assert _action(menu, "Edit hidden text…") is not None
    assert _action(menu, "Copy image") is None


# -- editing ---------------------------------------------------------------------------


def test_edit_hidden_replaces_the_value(qapp, window, repo, hidden, monkeypatch):
    hidden_id = hidden.add(SECRET)
    _open(qapp, window, repo, f"pw: {hidden_markdown(hidden_id)}")
    monkeypatch.setattr(window, "_prompt_hidden_value", lambda: "rotated-pw")

    assert window.edit_hidden(_pill(window)) is True
    assert hidden.get(hidden_id) == "rotated-pw"


def test_cancelling_or_blank_edit_changes_nothing(qapp, window, repo, hidden, monkeypatch):
    hidden_id = hidden.add(SECRET)
    _open(qapp, window, repo, f"pw: {hidden_markdown(hidden_id)}")
    for answer in (None, "   "):
        monkeypatch.setattr(window, "_prompt_hidden_value", lambda answer=answer: answer)
        assert window.edit_hidden(_pill(window)) is False
    assert hidden.get(hidden_id) == SECRET


def test_edit_is_dropped_if_the_vault_locked_during_the_dialog(
    qapp, window, repo, hidden, monkeypatch
):
    hidden_id = hidden.add(SECRET)
    _open(qapp, window, repo, f"pw: {hidden_markdown(hidden_id)}")
    item = _pill(window)

    def lock_then_answer():
        window.lock_session()
        return "too-late"

    monkeypatch.setattr(window, "_prompt_hidden_value", lock_then_answer)
    assert window.edit_hidden(item) is False
    assert hidden.get(hidden_id) == SECRET


# -- lock ------------------------------------------------------------------------------


def test_lock_clears_a_copied_secret_and_detaches_the_store(
    qapp, window, repo, hidden, clipboard
):
    _open(qapp, window, repo, f"pw: {hidden_markdown(hidden.add(SECRET))}")
    window.copy_hidden(_pill(window))

    window.lock_session()

    assert clipboard.text() == ""
    assert window.hidden_store is None


# -- output paths ------------------------------------------------------------------------


def test_copy_text_writes_the_mask(qapp, window, repo, hidden, clipboard):
    _open(qapp, window, repo, f"pw: {hidden_markdown(hidden.add(SECRET))} ok")
    window.copy_note_text()
    assert clipboard.text() == f"pw: {MASK_TEXT} ok"


def test_html_export_shows_the_mask_not_the_value(qapp, hidden):
    markdown = f"pw: {hidden_markdown(hidden.add(SECRET))}"
    html = note_html(markdown, None, title="t")
    assert SECRET not in html
    assert "mnsec:" not in html  # a browser can't resolve it: the pill is embedded
    assert "data:image/png;base64," in html
