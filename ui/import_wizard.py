"""The legacy-``notes.db`` import wizard.

A guided dialog that brings notes from the old tkinter prototype's plain SQLite
``notes.db`` into the current encrypted vault. It is the UI front-end for the
Qt-free import *engine* (:mod:`core.importer`): the engine reads and writes the
data, this wizard collects the file, previews what will be imported, runs it, and
reports the result.

Three pages:

#. **Choose file** — pick the legacy ``notes.db`` (a path field + a Browse
   button backed by :class:`QFileDialog`).
#. **Preview** — read the file (:func:`core.importer.read_legacy_notes`) and show
   how many notes were found / will be skipped / how many categories map to
   notebooks. An unreadable file or one without a ``content`` column raises
   :class:`~core.importer.LegacyDatabaseError`, which is shown inline so the user
   can go back and choose another file rather than the wizard crashing.
#. **Result** — write the previewed notes into the vault
   (:func:`core.importer.import_legacy_notes`) and show the
   :class:`~core.importer.ImportResult` counts.

Test seams mirror the rest of the UI (``UnlockDialog.attempt`` /
``QuickSwitcher.accept_selection``): the real logic lives in the public methods
:meth:`ImportWizard.set_source_path`, :meth:`ImportWizard.load_preview`, and
:meth:`ImportWizard.run_import`, with the parsed notes and the final
:class:`~core.importer.ImportResult` exposed as attributes — so headless tests
drive an import end to end without running the modal wizard loop.

Per CLAUDE.md's strict layering, the UI may import Qt freely and depends on the
engine; ``core/`` never imports this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from core.importer import (
    LegacyDatabaseError,
    import_legacy_notes,
    read_legacy_notes,
)

if TYPE_CHECKING:
    import os

    from core.importer import ImportResult, LegacyNote
    from core.repository import Repository

WINDOW_TITLE = "Import legacy notes"

# File filter for the Browse dialog: the prototype's file is a plain SQLite db.
_FILE_FILTER = "SQLite databases (*.db *.sqlite *.sqlite3);;All files (*)"


class _FilePage(QWizardPage):
    """First page: choose the legacy ``notes.db`` to import from.

    Exposes :attr:`path_edit` (the chosen path); the page is complete — so the
    wizard's Next button enables — only once a non-empty path is entered.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Choose a legacy notes database")
        self.setSubTitle(
            "Select the notes.db file from the old app. It is opened read-only — "
            "the original file is never modified."
        )

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Path to legacy notes.db")
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)

        row = QHBoxLayout()
        row.addWidget(self.path_edit)
        row.addWidget(browse)

        layout = QVBoxLayout(self)
        layout.addLayout(row)

        # Re-evaluate completeness (enable/disable Next) as the path is typed.
        self.path_edit.textChanged.connect(self.completeChanged)

    def isComplete(self) -> bool:  # noqa: N802 - Qt override name
        return bool(self.path_edit.text().strip())

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select legacy notes database", "", _FILE_FILTER
        )
        if path:
            self.path_edit.setText(path)


class _PreviewPage(QWizardPage):
    """Second page: read the file and summarise what will be imported.

    On entry it asks the wizard to read the legacy database; the page is complete
    (Next/Finish enabled) only when the read succeeded, so a bad file cannot be
    imported — its error is shown inline and the user goes back to pick another.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Preview")
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary_label)
        self._complete = False

    def initializePage(self) -> None:  # noqa: N802 - Qt override name
        wizard = cast("ImportWizard", self.wizard())
        self._complete = wizard.load_preview()
        self.summary_label.setText(wizard.preview_text())
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802 - Qt override name
        return self._complete


class _ResultPage(QWizardPage):
    """Final page: run the import and report the result counts."""

    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Import complete")
        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.addWidget(self.result_label)

    def initializePage(self) -> None:  # noqa: N802 - Qt override name
        wizard = cast("ImportWizard", self.wizard())
        wizard.run_import()
        self.result_label.setText(wizard.result_text())


class ImportWizard(QWizard):
    """Guided import of a legacy ``notes.db`` into the open vault.

    Construct with the keyed :class:`~core.repository.Repository` of the unlocked
    vault. Drive it with :meth:`QWizard.exec` in the app; in tests, drive the
    logic directly: :meth:`set_source_path` then :meth:`load_preview` then
    :meth:`run_import`, reading back :attr:`legacy_notes`, :attr:`error_message`,
    and the final :attr:`result` without the modal loop.

    :attr:`legacy_notes` holds the parsed rows after a successful preview (``None``
    before/after a failed one); :attr:`error_message` carries the inline reason a
    preview failed; :attr:`result` is the :class:`~core.importer.ImportResult` once
    :meth:`run_import` has run (``None`` until then).
    """

    def __init__(self, repository: Repository, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._repository = repository
        self.legacy_notes: list[LegacyNote] | None = None
        self.error_message: str = ""
        self.result: ImportResult | None = None

        self.setWindowTitle(WINDOW_TITLE)

        self._file_page = _FilePage()
        self._preview_page = _PreviewPage()
        self._result_page = _ResultPage()
        self.addPage(self._file_page)
        self.addPage(self._preview_page)
        self.addPage(self._result_page)

    # -- logic seams (driven directly by tests and by the page transitions) --

    def source_path(self) -> str:
        """The legacy database path the user has entered (stripped)."""
        return self._file_page.path_edit.text().strip()

    def set_source_path(self, path: str | os.PathLike[str]) -> None:
        """Set the source path (the seam tests use instead of the Browse dialog)."""
        self._file_page.path_edit.setText(str(path))

    def load_preview(self) -> bool:
        """Read the legacy database for the preview; ``True`` on success.

        On success :attr:`legacy_notes` holds the parsed rows and
        :attr:`error_message` is cleared. On failure (no path, or a
        :class:`~core.importer.LegacyDatabaseError` — missing/unreadable file, or
        no ``content`` column) :attr:`legacy_notes` is ``None`` and
        :attr:`error_message` describes why, for inline display. Safe to call from
        tests without the modal loop.
        """
        self.legacy_notes = None
        self.error_message = ""
        path = self.source_path()
        if not path:
            self.error_message = "Choose a legacy notes database first."
            return False
        try:
            self.legacy_notes = read_legacy_notes(path)
        except LegacyDatabaseError as exc:
            self.legacy_notes = None
            self.error_message = str(exc)
            return False
        return True

    def run_import(self) -> ImportResult | None:
        """Write the previewed notes into the vault and store the result.

        Returns the :class:`~core.importer.ImportResult` (also stored on
        :attr:`result`), or ``None`` if there is nothing previewed to import
        (:meth:`load_preview` was not run, or it failed).
        """
        if self.legacy_notes is None:
            return None
        self.result = import_legacy_notes(self._repository, self.legacy_notes)
        return self.result

    # -- display text for the pages -----------------------------------------

    def preview_text(self) -> str:
        """Human-readable summary of the preview, or the inline error."""
        if self.legacy_notes is None:
            return f"Cannot read this file:\n\n{self.error_message}"
        total = len(self.legacy_notes)
        importable = [n for n in self.legacy_notes if (n.content or "").strip()]
        skipped = total - len(importable)
        categories = {(n.category or "").strip() for n in importable}
        categories.discard("")

        parts = [f"Found {total} note(s); {len(importable)} will be imported."]
        if skipped:
            parts.append(f"{skipped} empty note(s) will be skipped.")
        if categories:
            parts.append(
                f"{len(categories)} category folder(s) will be created or reused."
            )
        parts.append("Click Next to import them into your vault.")
        return "\n".join(parts)

    def result_text(self) -> str:
        """Human-readable summary of the completed import."""
        if self.result is None:
            return "Nothing was imported."
        result = self.result
        parts = [f"Imported {result.notes_imported} note(s)."]
        if result.notebooks_created:
            parts.append(f"Created {result.notebooks_created} new notebook(s).")
        if result.rows_skipped:
            parts.append(f"Skipped {result.rows_skipped} empty row(s).")
        return "\n".join(parts)
