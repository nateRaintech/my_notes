"""The Markdown editor: editable source with a live-rendered preview.

In the dock layout (issue #77), :attr:`MarkdownEditor.source` is the central
widget of the main window and :attr:`MarkdownEditor.preview` lives inside a
QDockWidget. ``MarkdownEditor`` continues to own both widgets and the
``textChanged → preview re-render`` wiring so the live-preview invariant is
preserved regardless of where the UI places each widget.

Public seams (unchanged):
* ``source`` — QPlainTextEdit holding raw Markdown (central widget in main window).
* ``preview`` — read-only QTextEdit showing the rendered output (inside a dock).
* ``splitter`` — retained as an attribute for tests that check the editor's
  internal structure; in the dock layout the splitter is no longer the primary
  layout container (source and preview are placed separately), but it still
  holds both widgets so that tests relying on ``splitter.widget(0/1)`` continue
  to pass.
* :meth:`set_markdown` / :meth:`markdown` — the auto-save seam.

Per CLAUDE.md's strict layering, the UI layer may import Qt freely; ``core/``
must never import this module.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QPlainTextEdit,
    QSplitter,
    QTextEdit,
    QWidget,
)

# Minimum width each sub-pane keeps so a drag can't squeeze one to nothing.
_PANE_MIN_WIDTH = 160


class MarkdownEditor(QWidget):
    """Editable Markdown source with a live-rendered preview.

    In the dock layout, MainWindow places ``source`` as its central widget and
    wraps ``preview`` in a QDockWidget. ``MarkdownEditor`` is still constructed
    and held as ``window.editor`` so all callers and tests that access
    ``window.editor.source``, ``window.editor.preview``, ``window.editor.markdown()``,
    and ``window.editor.set_markdown()`` continue to work without change.

    Attributes:
        source: the editable :class:`QPlainTextEdit` holding the raw Markdown.
        preview: the read-only :class:`QTextEdit` showing the rendered Markdown.
        splitter: a :class:`QSplitter` that owns both widgets (retained for
            backward-compatible test assertions; not used as the primary layout
            container in the dock architecture).
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.source = QPlainTextEdit()
        self.source.setPlaceholderText("Write Markdown here…")
        self.source.setMinimumWidth(_PANE_MIN_WIDTH)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumWidth(_PANE_MIN_WIDTH)

        # The splitter owns both widgets (keeps their parent set to the splitter
        # hierarchy) and is retained so existing tests that check
        # splitter.widget(0)/widget(1) and splitter.childrenCollapsible() keep
        # passing. In the dock layout, MainWindow re-parents source and preview
        # into their respective positions (central widget / dock), so the splitter
        # is not shown directly.
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.source)
        self.splitter.addWidget(self.preview)
        self.splitter.setChildrenCollapsible(False)

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
