"""Behavioral tests for the Markdown editor pane (ui/editor.py).

Verifies the editable-source + live-preview widget (ROADMAP.md M3): the preview
re-renders Markdown as the source changes, with no explicit Save/Render action.
Guarded by ``importorskip`` and run headless via the ``offscreen`` Qt platform,
matching ``tests/test_main_window_layout.py`` so the merge gate stays green
wherever the Qt runtime is present.
"""

import os

import pytest

# Select the headless platform before any Qt import instantiates a plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QPlainTextEdit,
    QSplitter,
    QTextEdit,
)

from ui.editor import MarkdownEditor  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """A process-wide QApplication (singleton) for widget construction."""
    yield QApplication.instance() or QApplication([])


def test_exposes_source_and_preview_widgets(qapp):
    editor = MarkdownEditor()
    assert isinstance(editor.source, QPlainTextEdit)
    assert isinstance(editor.preview, QTextEdit)


def test_source_and_preview_are_split_side_by_side(qapp):
    editor = MarkdownEditor()
    assert isinstance(editor.splitter, QSplitter)
    # Source then preview, left to right, and neither can collapse to nothing.
    assert editor.splitter.count() == 2
    assert editor.splitter.widget(0) is editor.source
    assert editor.splitter.widget(1) is editor.preview
    assert editor.splitter.childrenCollapsible() is False


def test_preview_is_read_only(qapp):
    editor = MarkdownEditor()
    assert editor.preview.isReadOnly() is True


def test_source_is_editable(qapp):
    editor = MarkdownEditor()
    assert editor.source.isReadOnly() is False


def test_markdown_returns_source_text_verbatim(qapp):
    editor = MarkdownEditor()
    editor.set_markdown("# Title\n\nsome **raw** markdown")
    # markdown() is the raw source the user typed, not the rendered preview.
    assert editor.markdown() == "# Title\n\nsome **raw** markdown"


def test_preview_renders_heading_live(qapp):
    editor = MarkdownEditor()
    editor.set_markdown("# Hello")
    rendered = editor.preview.toPlainText()
    # The Markdown was rendered, not shown literally: the '#' marker is gone and
    # the heading text is present.
    assert "Hello" in rendered
    assert "#" not in rendered


def test_preview_renders_bold_live(qapp):
    editor = MarkdownEditor()
    editor.set_markdown("**bold**")
    rendered = editor.preview.toPlainText()
    assert "bold" in rendered
    assert "*" not in rendered


def test_preview_updates_on_source_text_change(qapp):
    # Typing into the source (which emits textChanged) drives the preview with
    # no explicit render call — i.e. the preview is live.
    editor = MarkdownEditor()
    editor.source.setPlainText("first")
    assert "first" in editor.preview.toPlainText()

    editor.source.setPlainText("second")
    rendered = editor.preview.toPlainText()
    assert "second" in rendered
    assert "first" not in rendered


def test_empty_source_renders_empty_preview(qapp):
    editor = MarkdownEditor()
    editor.set_markdown("")
    assert editor.preview.toPlainText().strip() == ""
