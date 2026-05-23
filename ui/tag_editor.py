"""The tag editor dialog: assign and remove tags on a note (ROADMAP.md M6).

A small modal editor for one note's tags: it lists the tags currently attached to
the note (each removable) and offers a name field to assign another. Tags are
*get-or-created* by name, so the same label is reused across notes rather than
duplicated. Changes are **live** — each add / remove commits to the vault
immediately (like the notebook operations), so there is no separate save step;
closing the dialog just closes it.

This is slice 1 of the M6 "Tag UI" capability (assign / remove tags on a note);
filtering the note list by tag is the follow-up slice. The tag data layer
(:mod:`core.repository`: :meth:`~core.repository.Repository.get_tag_by_name`,
:meth:`~core.repository.Repository.create_tag`,
:meth:`~core.repository.Repository.add_tag_to_note`,
:meth:`~core.repository.Repository.remove_tag_from_note`,
:meth:`~core.repository.Repository.tags_for_note`) was built in #29 and is
unchanged here.

Public seams let tests drive the editor without the modal event loop (mirroring
:meth:`ui.settings_dialog.SettingsDialog.apply` /
:meth:`ui.quick_switcher.QuickSwitcher.accept_selection`):
:meth:`assign_tag`, :meth:`remove_tag`, and :meth:`current_tags`.

Per CLAUDE.md's strict layering, the UI may import Qt freely; ``core/`` never
imports this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from core.repository import Repository, Tag

WINDOW_TITLE = "Tags"
_DEFAULT_SIZE = (360, 320)


class TagEditorDialog(QDialog):
    """Assign / remove tags on a single note.

    Construct over the keyed :class:`~core.repository.Repository` and the note's
    id; the dialog seeds with the note's current tags and mutates the vault live
    as the user adds / removes them. Tags are get-or-created by name (reused, not
    duplicated), so assigning a name that already exists attaches the existing
    tag and re-assigning an already-attached tag is idempotent.

    Test seams (no modal loop needed): :meth:`assign_tag` (``name -> Tag | None``),
    :meth:`remove_tag` (``tag_id -> bool``), and :meth:`current_tags`.
    """

    def __init__(
        self,
        repository: Repository,
        note_id: int,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._repository = repository
        self._note_id = note_id

        self.setWindowTitle(WINDOW_TITLE)
        self.resize(*_DEFAULT_SIZE)

        layout = QVBoxLayout(self)

        # The note's current tags; each row carries its Tag in UserRole.
        self.tag_list = QListWidget()
        layout.addWidget(self.tag_list)

        # Name field + Add button to assign a tag (get-or-create by name).
        add_row = QHBoxLayout()
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Add a tag…")
        self.name_input.setClearButtonEnabled(True)
        self.add_button = QPushButton("Add")
        add_row.addWidget(self.name_input)
        add_row.addWidget(self.add_button)
        layout.addLayout(add_row)

        # Detach the highlighted tag from the note (the tag itself is kept).
        self.remove_button = QPushButton("Remove selected")
        layout.addWidget(self.remove_button)

        # Changes are already committed live, so Close just closes the dialog.
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.name_input.returnPressed.connect(self._on_add_clicked)
        self.add_button.clicked.connect(self._on_add_clicked)
        self.remove_button.clicked.connect(self._on_remove_clicked)

        self._refresh()

    def current_tags(self) -> list[Tag]:
        """The tags attached to the note, ordered by name (from the repository)."""
        return self._repository.tags_for_note(self._note_id)

    def assign_tag(self, name: str) -> Tag | None:
        """Get-or-create the tag named ``name`` and attach it to the note.

        Returns the attached :class:`~core.repository.Tag`, or ``None`` if
        ``name`` is blank / whitespace-only (a no-op). The name is stripped. An
        existing tag with that name is reused (so there is no duplicate and no
        ``IntegrityError`` from the ``UNIQUE`` constraint), and re-assigning an
        already-attached tag is idempotent (``add_tag_to_note`` uses
        ``INSERT OR IGNORE``). Refreshes the displayed list.
        """
        name = name.strip()
        if not name:
            return None
        tag = self._repository.get_tag_by_name(name)
        if tag is None:
            tag = self._repository.create_tag(name)
        self._repository.add_tag_to_note(self._note_id, tag.id)
        self._refresh()
        return tag

    def remove_tag(self, tag_id: int) -> bool:
        """Detach the tag from the note; return ``True`` if it was attached.

        Only the note↔tag association is removed — the tag itself is left intact
        (it may be on other notes). Refreshes the displayed list.
        """
        removed = self._repository.remove_tag_from_note(self._note_id, tag_id)
        self._refresh()
        return removed

    def _refresh(self) -> None:
        """Rebuild the tag list from the note's current tags."""
        self.tag_list.clear()
        for tag in self.current_tags():
            item = QListWidgetItem(tag.name)
            item.setData(Qt.ItemDataRole.UserRole, tag)
            self.tag_list.addItem(item)

    def _on_add_clicked(self) -> None:
        """Assign the typed name; clear the field on success (keep it on a no-op)."""
        if self.assign_tag(self.name_input.text()) is not None:
            self.name_input.clear()

    def _on_remove_clicked(self) -> None:
        """Detach the highlighted tag, if any."""
        item = self.tag_list.currentItem()
        if item is None:
            return
        tag = item.data(Qt.ItemDataRole.UserRole)
        if tag is not None:
            self.remove_tag(tag.id)
