"""The Markdown editor pane: editable source beside a live-rendered preview.

The right pane of the app shell (``ui/main_window.py``). A :class:`MarkdownEditor`
holds two side-by-side widgets in a non-collapsible :class:`QSplitter`:

* ``source`` — an editable :class:`QPlainTextEdit` holding the raw Markdown.
* ``preview`` — a read-only :class:`QTextEdit` that re-renders the source via
  :meth:`QTextEdit.setMarkdown` (which uses ``QTextDocument.setMarkdown()``
  underneath) on every edit. The preview tracks the source *live* — there is no
  Save/Render button.

Loading and persisting note bodies is deliberately **out of scope** here: the
auto-save capability and the note-list/unlock flow wire this widget to
``core.repository.Repository`` in later M3/M4 work (ROADMAP.md). This widget only
edits text and shows its rendered form; :meth:`set_markdown` / :meth:`markdown`
are the seams those capabilities will drive.

Per CLAUDE.md's strict layering, the UI layer may import Qt freely; ``core/``
must never import this module.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSplitter,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

# Minimum width each sub-pane keeps so a drag can't squeeze one to nothing
# (paired with setChildrenCollapsible(False) to keep both source and preview
# visible).
_PANE_MIN_WIDTH = 160


def _make_collapse_button(label: str, tooltip: str) -> QToolButton:
    """Return a small flat QToolButton used as a panel collapse affordance."""
    btn = QToolButton()
    btn.setText(label)
    btn.setToolTip(tooltip)
    btn.setAutoRaise(True)
    btn.setFixedSize(20, 20)
    return btn


def _make_header_row(title: str, btn: QToolButton) -> QWidget:
    """Return a thin header widget containing a label and a collapse button."""
    row = QWidget()
    row.setFixedHeight(22)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(4, 0, 2, 0)
    layout.setSpacing(2)
    lbl = QLabel(title)
    layout.addWidget(lbl)
    layout.addStretch()
    layout.addWidget(btn)
    return row


class MarkdownEditor(QWidget):
    """Editable Markdown source with a live-rendered preview beside it.

    Attributes:
        source: the editable :class:`QPlainTextEdit` holding the raw Markdown.
        preview: the read-only :class:`QTextEdit` showing the rendered Markdown.
        splitter: the horizontal :class:`QSplitter` holding source | preview.
        collapse_source_btn: small button to collapse the source sub-pane.
        collapse_preview_btn: small button to collapse the preview sub-pane.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.source = QPlainTextEdit()
        self.source.setPlaceholderText("Write Markdown here…")
        self.source.setMinimumWidth(_PANE_MIN_WIDTH)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumWidth(_PANE_MIN_WIDTH)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.source)
        self.splitter.addWidget(self.preview)
        # Keep both sub-panes visible: a drag can resize but not collapse one.
        self.splitter.setChildrenCollapsible(False)

        # Thin toolbar row with per-pane collapse buttons above the splitter.
        self.collapse_source_btn = _make_collapse_button("‹", "Hide editor source (restore via View menu)")
        self.collapse_preview_btn = _make_collapse_button("›", "Hide preview (restore via View menu)")
        toolbar = QWidget()
        toolbar.setFixedHeight(22)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(4, 0, 2, 0)
        toolbar_layout.setSpacing(4)
        toolbar_layout.addWidget(QLabel("Editor"))
        toolbar_layout.addStretch()
        toolbar_layout.addWidget(self.collapse_source_btn)
        toolbar_layout.addWidget(self.collapse_preview_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(toolbar)
        layout.addWidget(self.splitter)

        # Live preview: re-render on every edit, with no explicit render action.
        self.source.textChanged.connect(self._render_preview)

    def _render_preview(self) -> None:
        """Re-render the preview from the current source text."""
        self.preview.setMarkdown(self.source.toPlainText())

    def set_markdown(self, text: str) -> None:
        """Replace the source text; the preview refreshes from it.

        Setting the source emits ``textChanged``, so the preview re-renders
        automatically — callers don't need to refresh it separately.
        """
        self.source.setPlainText(text)

    def markdown(self) -> str:
        """Return the current Markdown *source* text, verbatim.

        This is the raw Markdown the user typed — not the rendered preview — so
        it is the value the auto-save capability will persist.
        """
        return self.source.toPlainText()
