"""The Note menu: enabled only with an active note; Copy Text and exports (#105)."""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.images import ImageStore
from core.repository import Repository

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtGui import QColor, QGuiApplication, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.image_ingest import png_bytes  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


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
    w.show()
    qapp.processEvents()
    yield w
    w.hide()


def _open(qapp, window, repo, body):
    window.load_note(repo.create_note(title="n", body=body))
    qapp.processEvents()
    return window.tabbed_editor.active_tab


def _enabled(window):
    return [action.isEnabled() for action in window._note_actions]


def test_note_menu_follows_view(window):
    titles = [action.text() for action in window.menuBar().actions()]
    assert titles.index("&Note") == titles.index("&View") + 1


def test_note_menu_items(window):
    assert [a.text() for a in window._note_actions] == [
        "Copy &Text",
        "Export to &HTML…",
        "Export to &PDF…",
    ]


def test_note_actions_are_disabled_without_a_note(window):
    assert _enabled(window) == [False, False, False]


def test_note_actions_enable_when_a_note_opens_and_disable_on_lock(qapp, window, repo):
    _open(qapp, window, repo, "hello")
    assert _enabled(window) == [True, True, True]
    window.lock_session()
    assert _enabled(window) == [False, False, False]


def test_a_new_blank_note_enables_the_menu(qapp, window):
    window.new_note()
    qapp.processEvents()
    assert _enabled(window) == [True, True, True]


def test_copy_text_puts_the_readable_text_on_the_clipboard(qapp, window, repo, store):
    image = QImage(40, 20, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    record = store.add(png_bytes(image), "image/png", 40, 20)
    _open(
        qapp, window, repo,
        f"# Plan\n\n- **ship** it\n\n![s](mnimg:{record.id})\n\n| a | b |\n|---|---|\n| 1 | 2 |\n",
    )
    assert window.copy_note_text() is True
    assert QGuiApplication.clipboard().text() == "Plan\n- ship it"
    assert window.statusBar().currentMessage() == "Copied 14 characters"


def _export_to(monkeypatch, window, tmp_path, offered):
    def choose(default_name, file_filter):
        offered.append((default_name, file_filter))
        return str(tmp_path / default_name)

    monkeypatch.setattr(window, "_choose_export_path", choose)


def test_export_html_writes_through_the_seam(qapp, window, repo, tmp_path, monkeypatch):
    offered = []
    _export_to(monkeypatch, window, tmp_path, offered)
    _open(qapp, window, repo, "# Q3: plan\n\nbody")

    assert window.export_note_html() is True
    assert offered == [("Q3_ plan.html", "HTML files (*.html)")]
    assert "body" in (tmp_path / "Q3_ plan.html").read_text(encoding="utf-8")
    assert window.statusBar().currentMessage() == "Exported Q3_ plan.html"


def test_export_pdf_writes_a_pdf(qapp, window, repo, tmp_path, monkeypatch):
    offered = []
    _export_to(monkeypatch, window, tmp_path, offered)
    _open(qapp, window, repo, "# Report\n\nbody")

    assert window.export_note_pdf() is True
    assert offered == [("Report.pdf", "PDF files (*.pdf)")]
    assert (tmp_path / "Report.pdf").read_bytes().startswith(b"%PDF")


def test_a_cancelled_export_writes_nothing(qapp, window, repo, tmp_path, monkeypatch):
    monkeypatch.setattr(window, "_choose_export_path", lambda name, file_filter: "")
    _open(qapp, window, repo, "x")
    assert window.export_note_pdf() is False
    assert list(tmp_path.iterdir()) == []


def test_a_failed_export_is_reported(qapp, window, repo, tmp_path, monkeypatch):
    bad = str(tmp_path / "missing" / "x.pdf")
    monkeypatch.setattr(window, "_choose_export_path", lambda name, file_filter: bad)
    _open(qapp, window, repo, "x")
    assert window.export_note_pdf() is False
    assert window.statusBar().currentMessage().startswith("Couldn't export")


def test_a_failed_export_reports_the_reason_without_the_path(
    qapp, window, repo, tmp_path, monkeypatch
):
    bad = str(tmp_path / "missing" / "x.html")
    monkeypatch.setattr(window, "_choose_export_path", lambda name, file_filter: bad)
    _open(qapp, window, repo, "x")

    assert window.export_note_html() is False
    message = window.statusBar().currentMessage()
    assert message == "Couldn't export: No such file or directory"
    assert os.sep not in message


def test_an_unexpected_export_failure_is_reported_instead_of_raised(
    qapp, window, repo, tmp_path, monkeypatch
):
    _export_to(monkeypatch, window, tmp_path, [])
    _open(qapp, window, repo, "x")

    def boom(*args, **kwargs):
        raise RuntimeError("the vault connection is closed")

    monkeypatch.setattr("ui.main_window.write_html", boom)

    assert window.export_note_html() is False
    message = window.statusBar().currentMessage()
    assert message == "Couldn't export: the vault connection is closed"


def test_an_export_is_cancelled_when_the_vault_locks_during_the_dialog(
    qapp, window, repo, tmp_path, monkeypatch
):
    """The file dialog runs a nested event loop, so the idle-lock can fire inside it."""
    path = tmp_path / "note.html"

    def lock_then_choose(default_name, file_filter):
        window.lock_session()
        return str(path)

    monkeypatch.setattr(window, "_choose_export_path", lock_then_choose)
    _open(qapp, window, repo, "# Secret\n\nthe decrypted body")

    assert window.export_note_html() is False
    assert not path.exists()
    assert "lock" in window.statusBar().currentMessage().lower()


def test_note_actions_do_nothing_without_a_note(window):
    assert window.copy_note_text() is False
    assert window.export_note_html() is False
