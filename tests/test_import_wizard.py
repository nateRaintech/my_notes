"""Behavioral tests for the legacy-``notes.db`` import wizard (ROADMAP.md M4).

Completes the "Import wizard" capability: the UI front-end
(:class:`ui.import_wizard.ImportWizard`) over the Qt-free import engine merged in
#43 (:mod:`core.importer`), plus the main-window File-menu wiring. As with the
other Qt tests, the modal wizard loop isn't exercised — the tests drive the public
seams (:meth:`~ui.import_wizard.ImportWizard.set_source_path`,
:meth:`~ui.import_wizard.ImportWizard.load_preview`,
:meth:`~ui.import_wizard.ImportWizard.run_import`) directly, against a real
in-memory SQLCipher ``Repository`` and a real legacy SQLite file built in
``tmp_path``.

Guarded by ``importorskip`` and run headless via the ``offscreen`` Qt platform,
matching the other Qt tests so the merge gate stays green wherever Qt is present.
"""

import os
import sqlite3

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.importer import ImportResult
from core.repository import Repository

# Select the headless platform before any Qt import instantiates a plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from ui.import_wizard import ImportWizard  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """A process-wide QApplication (singleton) for widget construction."""
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


@pytest.fixture
def repo():
    """A repository over a migrated, FK-enforcing in-memory connection."""
    conn = sqlcipher.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    schema.migrate(conn)
    try:
        yield Repository(conn)
    finally:
        conn.close()


# Three importable rows (one category, "Work", repeated) + one blank-content row
# that the engine skips. So a clean import = 3 notes, 2 notebooks, 1 skipped.
_LEGACY_ROWS = [
    ("# Meeting\nagenda and notes", "Work", "markdown", "2020-01-01 09:30:00"),
    ("Buy milk and eggs", "Personal", "plain", "1577871000"),
    ("Another work note", "Work", "markdown", None),
    ("   ", "Work", "plain", None),  # blank body -> skipped by the engine
]


def _make_legacy_db(path) -> None:
    """Write a plain (unencrypted) legacy ``notes.db`` at ``path`` with sample rows."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE notes (content TEXT, category TEXT, format TEXT, timestamp TEXT)"
    )
    conn.executemany(
        "INSERT INTO notes (content, category, format, timestamp) VALUES (?, ?, ?, ?)",
        _LEGACY_ROWS,
    )
    conn.commit()
    conn.close()


def _note_labels(window: MainWindow) -> list[str]:
    return [window.note_list.item(i).text() for i in range(window.note_list.count())]


def _tree_labels(window: MainWindow) -> list[str]:
    """Every notebook-tree row's text (depth-first), including "All Notes"."""
    tree = window.notebook_tree
    labels: list[str] = []
    stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    while stack:
        item = stack.pop()
        labels.append(item.text(0))
        stack.extend(item.child(i) for i in range(item.childCount()))
    return labels


# -- preview -----------------------------------------------------------------


def test_preview_reads_a_good_file(qapp, repo, tmp_path):
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)
    wizard = ImportWizard(repo)
    wizard.set_source_path(db)

    assert wizard.load_preview() is True
    assert wizard.legacy_notes is not None
    assert len(wizard.legacy_notes) == len(_LEGACY_ROWS)
    assert wizard.error_message == ""
    # The summary reports the 3 importable rows (the blank one is excluded).
    assert "3 will be imported" in wizard.preview_text()


def test_preview_missing_file_shows_inline_error(qapp, repo, tmp_path):
    wizard = ImportWizard(repo)
    wizard.set_source_path(tmp_path / "does_not_exist.db")

    assert wizard.load_preview() is False
    assert wizard.legacy_notes is None
    assert wizard.error_message != ""
    # The error is surfaced in the preview text, not raised.
    assert wizard.error_message in wizard.preview_text()


def test_preview_file_without_content_column_shows_error(qapp, repo, tmp_path):
    db = tmp_path / "bad.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE notes (body TEXT, category TEXT)")
    conn.commit()
    conn.close()
    wizard = ImportWizard(repo)
    wizard.set_source_path(db)

    assert wizard.load_preview() is False
    assert "content" in wizard.error_message.lower()


def test_preview_with_no_path_shows_error(qapp, repo):
    wizard = ImportWizard(repo)
    assert wizard.source_path() == ""
    assert wizard.load_preview() is False
    assert wizard.error_message != ""


# -- run_import --------------------------------------------------------------


def test_run_import_writes_notes_and_notebooks(qapp, repo, tmp_path):
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)
    wizard = ImportWizard(repo)
    wizard.set_source_path(db)
    assert wizard.load_preview() is True

    result = wizard.run_import()

    assert isinstance(result, ImportResult)
    assert result.notes_imported == 3
    assert result.notebooks_created == 2
    assert result.rows_skipped == 1
    assert wizard.result is result
    # The notes and notebooks are really in the vault now.
    assert len(repo.list_notes()) == 3
    assert sorted(nb.name for nb in repo.list_notebooks()) == ["Personal", "Work"]


def test_run_import_preserves_a_legacy_timestamp(qapp, repo, tmp_path):
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)
    wizard = ImportWizard(repo)
    wizard.set_source_path(db)
    wizard.load_preview()
    wizard.run_import()

    # The "Buy milk" row carried epoch 1577871000 -> 2020-01-01 09:30:00 UTC.
    bodies = {note.body: note for note in repo.list_notes()}
    milk = bodies["Buy milk and eggs"]
    assert milk.created_at == "2020-01-01 09:30:00"


def test_run_import_returns_none_without_a_preview(qapp, repo):
    wizard = ImportWizard(repo)
    # Never previewed -> nothing to import.
    assert wizard.run_import() is None
    assert repo.list_notes() == []


def test_run_import_returns_none_after_a_failed_preview(qapp, repo, tmp_path):
    wizard = ImportWizard(repo)
    wizard.set_source_path(tmp_path / "nope.db")
    assert wizard.load_preview() is False
    assert wizard.run_import() is None
    assert repo.list_notes() == []


def test_result_text_summarises_the_import(qapp, repo, tmp_path):
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)
    wizard = ImportWizard(repo)
    wizard.set_source_path(db)
    wizard.load_preview()
    wizard.run_import()

    text = wizard.result_text()
    assert "3" in text  # notes imported
    assert "2" in text  # notebooks created


# -- main-window wiring ------------------------------------------------------


def test_make_import_wizard_is_none_without_a_repository(qapp):
    window = MainWindow()
    assert window._make_import_wizard() is None


def test_make_import_wizard_with_a_repository(qapp, repo):
    window = MainWindow()
    window.bind_autosave(repo)
    assert isinstance(window._make_import_wizard(), ImportWizard)


def test_file_menu_exposes_the_import_action(qapp):
    window = MainWindow()
    assert window.import_action is not None
    assert "import" in window.import_action.text().lower()


def test_imported_content_appears_in_the_window_after_refresh(qapp, repo, tmp_path):
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)
    window = MainWindow()
    window.bind_autosave(repo)

    wizard = window._make_import_wizard()
    assert wizard is not None
    wizard.set_source_path(db)
    wizard.load_preview()
    wizard.run_import()

    # open_import_wizard repopulates the panes on success — do the same here, as
    # the modal exec() can't run headless.
    window._populate_notebook_tree()
    window.refresh_notes()

    assert len(_note_labels(window)) == 3
    labels = _tree_labels(window)
    assert "Work" in labels
    assert "Personal" in labels


def test_imported_notebook_filters_the_note_list(qapp, repo, tmp_path):
    db = tmp_path / "legacy.db"
    _make_legacy_db(db)
    window = MainWindow()
    window.bind_autosave(repo)
    wizard = window._make_import_wizard()
    wizard.set_source_path(db)
    wizard.load_preview()
    wizard.run_import()
    window._populate_notebook_tree()

    # Filtering to the imported "Personal" notebook shows only its one note.
    personal = next(nb for nb in repo.list_notebooks() if nb.name == "Personal")
    window.select_notebook(personal.id)
    assert _note_labels(window) == [window.note_list.item(0).text()]
    assert window.note_list.count() == 1
    # And that note carries the imported notebook id.
    note = window.note_list.item(0).data(Qt.ItemDataRole.UserRole)
    assert note.notebook_id == personal.id
