"""Tests for ``ui.hidden_text`` — the mask, preview hit-testing, the clipboard (#113)."""

import os
import struct

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QTextDocument  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QTextEdit  # noqa: E402

from ui.hidden_text import (  # noqa: E402
    HISTORY_EXCLUSION_FORMATS,
    MASK_SIZE,
    ClipboardGuard,
    PreviewClickFilter,
    hidden_at,
    hidden_rect,
    mask_image,
    rendered_hidden,
)
from ui.vault_document import VaultTextDocument  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def view(qapp):
    v = QTextEdit()
    v.setReadOnly(True)
    v.setDocument(VaultTextDocument(v))
    v.resize(500, 300)
    v.show()
    qapp.processEvents()
    yield v
    v.hide()


def _show(qapp, view, markdown):
    view.setMarkdown(markdown)
    qapp.processEvents()


# -- the mask -------------------------------------------------------------------


def test_mask_is_painted_at_the_logical_size_for_any_ratio(qapp):
    for ratio in (1.0, 1.5, 2.0):
        image = mask_image(ratio)
        assert not image.isNull()
        assert image.devicePixelRatio() == ratio
        assert image.deviceIndependentSize().toSize() == MASK_SIZE


def test_mask_is_not_blank(qapp):
    image = mask_image(1.0)
    colours = {image.pixel(x, y) for x in range(image.width()) for y in range(image.height())}
    assert len(colours) > 2


def test_document_renders_hidden_refs_as_the_mask_without_any_store(qapp):
    document = VaultTextDocument()
    document.setMarkdown("pw ![hidden](mnsec:3)")
    rendered = document.resource(QTextDocument.ResourceType.ImageResource, QUrl("mnsec:3"))
    assert isinstance(rendered, QImage)
    assert rendered.deviceIndependentSize().toSize() == MASK_SIZE


# -- finding hidden text in the preview -----------------------------------------


def test_rendered_hidden_lists_hidden_refs_only_in_order(qapp, view):
    _show(qapp, view, "a ![hidden](mnsec:4) ![shot](mnimg:1)\n\nb ![hidden](mnsec:2)")
    found = rendered_hidden(view.document())
    assert [h.hidden_id for h in found] == [4, 2]


def test_hidden_at_finds_the_pill_under_a_point(qapp, view):
    _show(qapp, view, "pw ![hidden](mnsec:9)")
    item = rendered_hidden(view.document())[0]
    rect = hidden_rect(view, item)
    assert rect.size() == MASK_SIZE
    assert hidden_at(view, rect.center()).hidden_id == 9
    assert hidden_at(view, QPoint(rect.right() + 40, rect.center().y())) is None


def test_clicking_a_pill_emits_clicked(qapp, view):
    _show(qapp, view, "pw ![hidden](mnsec:5)")
    click_filter = PreviewClickFilter(view)
    got = []
    click_filter.clicked.connect(got.append)
    rect = hidden_rect(view, rendered_hidden(view.document())[0])

    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(400, 250))

    assert [h.hidden_id for h in got] == [5]


def test_hovering_a_pill_shows_a_pointing_hand(qapp, view):
    _show(qapp, view, "pw ![hidden](mnsec:5)")
    PreviewClickFilter(view)
    rect = hidden_rect(view, rendered_hidden(view.document())[0])

    QTest.mouseMove(view.viewport(), rect.center())
    assert view.viewport().cursor().shape() == Qt.CursorShape.PointingHandCursor
    QTest.mouseMove(view.viewport(), QPoint(400, 250))
    assert view.viewport().cursor().shape() != Qt.CursorShape.PointingHandCursor


# -- the clipboard ----------------------------------------------------------------


@pytest.fixture
def clipboard(qapp):
    board = QGuiApplication.clipboard()
    board.clear()
    yield board
    board.clear()


def test_copy_puts_the_value_on_the_clipboard_excluded_from_history(clipboard):
    guard = ClipboardGuard()
    guard.copy("hunter2", clear_after_seconds=30)
    assert clipboard.text() == "hunter2"
    mime = clipboard.mimeData()
    for fmt in HISTORY_EXCLUSION_FORMATS:
        assert mime.hasFormat(fmt)
    assert guard.timer.isActive()
    assert guard.timer.interval() == 30_000


def test_history_formats_say_no(clipboard):
    ClipboardGuard().copy("x", clear_after_seconds=30)
    mime = clipboard.mimeData()
    for fmt in HISTORY_EXCLUSION_FORMATS:
        if "CanInclude" in fmt or "CanUpload" in fmt:
            assert struct.unpack("<I", bytes(mime.data(fmt)))[0] == 0


def test_expiry_clears_the_clipboard_if_it_still_holds_the_value(clipboard):
    guard = ClipboardGuard()
    guard.copy("hunter2", clear_after_seconds=30)
    guard.expire()
    assert clipboard.text() == ""
    assert not guard.timer.isActive()


def test_expiry_leaves_something_the_user_copied_since(clipboard):
    guard = ClipboardGuard()
    guard.copy("hunter2", clear_after_seconds=30)
    clipboard.setText("my own thing")
    guard.expire()
    assert clipboard.text() == "my own thing"


def test_zero_seconds_never_arms_the_timer(clipboard):
    guard = ClipboardGuard()
    guard.copy("hunter2", clear_after_seconds=0)
    assert clipboard.text() == "hunter2"
    assert not guard.timer.isActive()


def test_clear_now_wipes_a_held_secret_even_with_no_timer(clipboard):
    guard = ClipboardGuard()
    guard.copy("hunter2", clear_after_seconds=0)
    guard.clear_now()
    assert clipboard.text() == ""


def test_clear_now_with_nothing_copied_leaves_the_clipboard(clipboard):
    clipboard.setText("unrelated")
    ClipboardGuard().clear_now()
    assert clipboard.text() == "unrelated"


def test_the_guard_does_not_keep_the_plaintext(clipboard):
    guard = ClipboardGuard()
    guard.copy("hunter2", clear_after_seconds=30)
    assert "hunter2" not in repr(vars(guard))
