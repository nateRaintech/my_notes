"""Window-level image behaviour: preview, Insert menu, context menu, lock (#101)."""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.images import ImageStore
from core.repository import Repository

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.image_ingest import png_bytes  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.preview_images import PreviewImage, image_rect, rendered_images  # noqa: E402

IMAGE_ACTIONS = ["Copy image", "Save image as…", "Reset to original size"]


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
def store(conn):
    return ImageStore(conn)


@pytest.fixture
def window(qapp, repo, store):
    w = MainWindow()
    w.bind_autosave(repo, debounce=999)
    w.bind_images(store)
    w.resize(1200, 800)
    w.show()
    qapp.processEvents()
    yield w
    w.hide()


def _image(width=400, height=200):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    return image


def _add(store):
    return store.add(png_bytes(_image()), "image/png", 400, 200)


def _open(qapp, window, repo, body):
    window.load_note(repo.create_note(title="n", body=body))
    qapp.processEvents()
    return window.tabbed_editor.active_tab


def _first_image(window):
    return rendered_images(window.preview.document())[0]


def _texts(menu):
    return [action.text() for action in menu.actions()]


def test_preview_renders_vault_images(qapp, window, repo, store):
    record = _add(store)
    _open(qapp, window, repo, f"![s](mnimg:{record.id}?w=100)")
    image = _first_image(window)
    assert image_rect(window.preview, image).size().width() == 100


def test_preview_menu_offers_image_actions_on_an_image(qapp, window, repo, store):
    record = _add(store)
    _open(qapp, window, repo, f"![s](mnimg:{record.id}?w=100)")
    point = image_rect(window.preview, _first_image(window)).center()
    assert _texts(window.build_preview_context_menu(point))[:3] == IMAGE_ACTIONS


def test_preview_menu_is_standard_off_an_image(qapp, window, repo, store):
    record = _add(store)
    _open(qapp, window, repo, f"text\n\n![s](mnimg:{record.id}?w=100)")
    viewport = window.preview.viewport()
    menu = window.build_preview_context_menu(QPoint(viewport.width() - 2, viewport.height() - 2))
    assert not set(IMAGE_ACTIONS) & set(_texts(menu))


def test_reset_is_disabled_for_an_image_already_at_natural_size(qapp, window, repo, store):
    record = _add(store)
    _open(qapp, window, repo, f"![s](mnimg:{record.id})")
    point = image_rect(window.preview, _first_image(window)).center()
    reset = window.build_preview_context_menu(point).actions()[2]
    assert reset.text() == "Reset to original size"
    assert reset.isEnabled() is False


def test_copy_image_copies_the_full_resolution_original(qapp, window, repo, store):
    record = _add(store)
    _open(qapp, window, repo, f"![s](mnimg:{record.id}?w=100)")
    assert window.copy_preview_image(_first_image(window)) is True
    assert QGuiApplication.clipboard().image().size().toTuple() == (400, 200)


def test_save_image_writes_the_stored_bytes(qapp, window, repo, store, tmp_path, monkeypatch):
    record = _add(store)
    _open(qapp, window, repo, f"![s](mnimg:{record.id}?w=100)")
    offered = []

    def choose(default_name):
        offered.append(default_name)
        return str(tmp_path / default_name)

    monkeypatch.setattr(window, "_choose_save_path", choose)

    assert window.save_preview_image(_first_image(window)) is True
    assert offered == [f"image-{record.id}.png"]
    assert (tmp_path / offered[0]).read_bytes() == record.data


def test_save_image_cancelled_writes_nothing(qapp, window, repo, store, monkeypatch):
    record = _add(store)
    _open(qapp, window, repo, f"![s](mnimg:{record.id}?w=100)")
    monkeypatch.setattr(window, "_choose_save_path", lambda name: "")
    assert window.save_preview_image(_first_image(window)) is False


def test_reset_removes_the_width_undoably(qapp, window, repo, store):
    record = _add(store)
    tab = _open(qapp, window, repo, f"![s](mnimg:{record.id}?w=100)")
    assert window.reset_preview_image_size(_first_image(window)) is True
    assert tab.markdown() == f"![s](mnimg:{record.id})"
    tab.source.undo()
    assert tab.markdown() == f"![s](mnimg:{record.id}?w=100)"


def test_reset_declines_when_the_image_is_ambiguous(qapp, window, repo, store):
    record = _add(store)
    body = f"![a](mnimg:{record.id}?w=100) ![b](mnimg:{record.id}?w=100)"
    tab = _open(qapp, window, repo, body)
    stale = PreviewImage(image_id=record.id, width=100, ordinal=7, position=0, url="")
    assert window.reset_preview_image_size(stale) is False
    assert tab.markdown() == body
    assert window.statusBar().currentMessage()


def test_insert_image_files_inserts_at_the_caret(qapp, window, repo, tmp_path):
    path = tmp_path / "diagram.png"
    assert _image(40, 20).save(str(path), "PNG")
    tab = _open(qapp, window, repo, "before ")
    tab.source.moveCursor(QTextCursor.MoveOperation.End)
    assert window.insert_image_files([str(path)]) is True
    assert tab.markdown().startswith("before ![diagram](mnimg:")


def test_insert_image_files_reports_failures(qapp, window, repo, tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"nope")
    tab = _open(qapp, window, repo, "unchanged")
    assert window.insert_image_files([str(bad)]) is False
    assert tab.markdown() == "unchanged"
    assert "bad.png" in window.statusBar().currentMessage()


def test_insert_image_without_a_note_says_so(qapp, window, monkeypatch):
    def fail():
        raise AssertionError("no file picker without a note")

    monkeypatch.setattr(window, "_choose_image_files", fail)
    window.insert_image_from_file()
    assert window.statusBar().currentMessage() == "Open a note first"


def test_insert_menu_has_an_image_action(window):
    assert "&Insert" in [action.text() for action in window.menuBar().actions()]
    assert window.insert_image_action.text() == "&Image…"


def test_tab_status_messages_reach_the_status_bar(qapp, window, repo):
    tab = _open(qapp, window, repo, "x")
    tab.source.status_message.emit("boom")
    assert window.statusBar().currentMessage() == "boom"


def test_lock_session_drops_decoded_images_and_detaches_the_store(qapp, window, repo, store):
    record = _add(store)
    _open(qapp, window, repo, f"![s](mnimg:{record.id}?w=100)")
    assert len(window.preview_document.cache) > 0  # populated by the preview's render

    window.lock_session()

    assert len(window.preview_document.cache) == 0
    assert window.image_store is None
    assert window.preview_document.natural_image(record.id) is None
