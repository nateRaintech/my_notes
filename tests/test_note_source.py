"""Tests for image paste / drop in the note source pane (#101)."""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.images import ImageStore
from core.repository import Repository

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QMimeData, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QTextCursor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.image_ingest import initial_width  # noqa: E402
from ui.note_source import NoteSourceEdit  # noqa: E402
from ui.tabbed_editor import TabbedEditor  # noqa: E402


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
def store(conn):
    return ImageStore(conn)


def _image(width=400, height=200):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    return image


def _image_mime():
    mime = QMimeData()
    mime.setImageData(_image())
    return mime


def _edit(store):
    edit = NoteSourceEdit()
    edit.image_store = store
    return edit


def test_pasting_an_image_inserts_its_markdown(qapp, store):
    edit = _edit(store)
    edit.setPlainText("see: ")
    edit.moveCursor(QTextCursor.MoveOperation.End)

    edit.insertFromMimeData(_image_mime())

    width = initial_width(400, edit.devicePixelRatioF())
    assert edit.toPlainText() == f"see: ![screenshot](mnimg:1?w={width})"
    assert store.get(1) is not None


def test_ctrl_v_with_a_clipboard_image_pastes_it(qapp, store):
    edit = _edit(store)
    edit.show()
    QGuiApplication.clipboard().setImage(_image())
    QTest.keyClick(edit, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    assert edit.toPlainText().startswith("![screenshot](mnimg:1")


def test_an_image_paste_is_one_undo_step(qapp, store):
    edit = _edit(store)
    edit.insertFromMimeData(_image_mime())
    edit.undo()
    assert edit.toPlainText() == ""


def test_text_wins_when_the_clipboard_has_both(qapp, store):
    mime = _image_mime()
    mime.setText("a1\tb1")
    edit = _edit(store)
    edit.insertFromMimeData(mime)
    assert edit.toPlainText() == "a1\tb1"
    assert store.get(1) is None


def test_without_a_store_an_image_paste_does_nothing(qapp):
    edit = NoteSourceEdit()
    assert edit.canInsertFromMimeData(_image_mime()) is False
    edit.insertFromMimeData(_image_mime())
    assert edit.toPlainText() == ""


def test_dropping_image_files_inserts_each_one(qapp, store, tmp_path):
    first, second = tmp_path / "first.png", tmp_path / "second.jpg"
    assert _image(10, 10).save(str(first), "PNG")
    assert _image(20, 10).save(str(second), "JPEG")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(first)), QUrl.fromLocalFile(str(second))])

    edit = _edit(store)
    assert edit.canInsertFromMimeData(mime) is True
    edit.insertFromMimeData(mime)

    text = edit.toPlainText()
    assert "![first](mnimg:1?w=" in text
    assert "![second](mnimg:2?w=" in text


def test_a_failed_ingest_reports_and_changes_nothing(qapp, store, tmp_path):
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"nope")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(bad))])
    messages = []

    edit = _edit(store)
    edit.status_message.connect(messages.append)
    edit.insertFromMimeData(mime)

    assert edit.toPlainText() == ""
    assert messages and "broken.png" in messages[0]


def test_tabbed_editor_gives_every_tab_the_store(qapp, conn, store):
    repo = Repository(conn)
    editor = TabbedEditor(repo)
    early = editor.open(repo.create_note(title="a", body="a"))

    editor.set_image_store(store)
    late = editor.open(repo.create_note(title="b", body="b"))

    assert early.source.image_store is store
    assert late.source.image_store is store

    editor.set_image_store(None)
    assert early.source.image_store is None


def test_tabbed_editor_re_emits_status_messages(qapp, conn):
    repo = Repository(conn)
    editor = TabbedEditor(repo)
    tab = editor.open(repo.create_note(title="a", body="a"))
    messages = []
    editor.tab_status_message.connect(messages.append)

    tab.source.status_message.emit("hello")

    assert messages == ["hello"]
