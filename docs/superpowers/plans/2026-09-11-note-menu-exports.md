# Note Menu, Copy Text & HTML/PDF Export — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Note menu (after View) with Copy Text, Export to HTML… and Export to PDF…, enabled only while a note tab is active. The exports render through the vault-backed preview document.

**Architecture:**
- A pure `core/text.safe_filename` names export files.
- `ui/note_render.py` builds a fresh `VaultTextDocument` from a note's Markdown and turns it into plain text.
- `ui/note_export.py` writes self-contained HTML (images as `data:` URIs) and Letter-size PDFs through `QPdfWriter`.
- `MainWindow` hosts the menu and a file-dialog seam.

**Tech Stack:** Python 3.12, PySide6 (Qt 6), sqlcipher3, pytest, ruff; `pypdf` (tests only in this part).

**Spec:** `docs/superpowers/specs/2026-09-11-note-menu-exports-design.md` (issue #105).

## Global Constraints

- **`core/` never imports PySide6.** `safe_filename` is pure Python.
- **Interpreter:** always `/c/Users/Nate/anaconda3/envs/playground/python.exe` (`$PY` below).
  - Tests: `$PY -m pytest …` (`pyproject.toml` already sets `-q`; don't add another).
  - Lint: `$PY -m ruff check .`
- **Exports render from a fresh document** built from the note's Markdown (`render_document`), never from the live preview widget.
- **PDF:**
  - US Letter, 0.75" margins on all sides
  - `QPdfWriter` resolution 96, title set to the note's title, creator `my_notes`
  - images rendered at device pixel ratio 2.0
  - Qt's default page-number footer is kept
- **HTML:**
  - One self-contained file.
  - Every `mnimg:` `<img>` gets `src` = `data:<mime>;base64,<stored bytes>` and `width` = the ref's `w`, or the natural pixel width when there is no `w`.
  - A missing image gets `src=""`.
  - A `<title>` is inserted.
- **Copy Text:**
  - no Markdown symbols
  - tables skipped
  - U+FFFC removed
  - bullets `- `, numbered items `QTextList.itemText` (e.g. `1.`), two spaces of indent per nesting level
  - runs of blank lines collapsed to one; leading and trailing blanks trimmed
- **The Note menu is enabled iff `tabbed_editor.active_tab is not None`.**
- **The offscreen Qt platform on Windows has no fonts**, so PDFs exported there contain no text. Tests that assert PDF text must `pytest.skip` when `QFontDatabase.families()` is empty.
- **Commits:**
  - Subjects end with `(refs #105)`.
  - Messages end with:
    ```
    Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
    Claude-Session: https://claude.ai/code/session_01UpJoMa4T4ra499LUyQnAk7
    ```
  - Work happens on branch `feature/105-note-menu-exports`. Never push.
- **Never touch the user's running my_notes** (it runs from `%LOCALAPPDATA%\my_notes\my_notes.exe`).
  - Build test exes into `build/dist-105`.
  - A `--onefile` exe is a bootloader plus a child process: stop test runs by exact path, never by name.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `core/text.py` | modify | add `safe_filename` |
| `ui/note_render.py` | create | `render_document`, `plain_text` |
| `ui/note_export.py` | create | `data_uri`, `note_html`, `write_html`, `write_pdf` |
| `ui/main_window.py` | modify | Note menu, `_update_note_actions`, copy/export methods, `_choose_export_path` seam; rename `_IMAGE_STATUS_MS` → `_STATUS_MS` |
| `tests/test_text.py` | modify | `safe_filename` tests |
| `tests/test_note_render.py` | create | |
| `tests/test_note_export.py` | create | |
| `tests/test_note_menu.py` | create | |

---

### Task 1: `core/text.safe_filename`

**Files:**
- Modify: `core/text.py` (append after `count_words`)
- Test: `tests/test_text.py` (append)

**Interfaces:**
- Produces: `safe_filename(title: str, *, max_length: int = 100, fallback: str = "note") -> str`, a filename stem with no extension.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_text.py` (check its existing import line and add `safe_filename` to it):

```python
# -- safe_filename (#105) -----------------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Weekly notes", "Weekly notes"),
        ("Q3: plan/draft?", "Q3_ plan_draft_"),
        ('a<b>c"d\\e|f*g', "a_b_c_d_e_f_g"),
        ("tab\there", "tab_here"),
        ("Report. . ", "Report"),
        ("  spaced   out  ", "spaced out"),
    ],
)
def test_safe_filename_replaces_what_windows_rejects(title, expected):
    assert safe_filename(title) == expected


@pytest.mark.parametrize("title", ["", "   ", "...", ". ."])
def test_safe_filename_falls_back(title):
    assert safe_filename(title) == "note"


def test_safe_filename_caps_the_length():
    assert len(safe_filename("x" * 300)) == 100
    assert safe_filename("ab" * 80, max_length=10) == "ababababab"
```

If `tests/test_text.py` doesn't already `import pytest`, add it.

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/test_text.py -v -k safe_filename`
Expected: FAIL (`ImportError: cannot import name 'safe_filename'`).

- [ ] **Step 3: Implement** — in `core/text.py`, next to the other module-level regexes (the module already imports `re`; add the import if it doesn't):

```python
# Characters Windows forbids in file names, plus ASCII control characters.
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
```

and append after `count_words`:

```python
def safe_filename(title: str, *, max_length: int = 100, fallback: str = "note") -> str:
    """Turn a note title into a default file name stem (no extension).

    Replaces the characters Windows rejects in file names — and control
    characters — with ``_``, collapses whitespace, caps the length, and trims
    trailing dots and spaces (Windows silently drops them, which would change
    the name the user saw in the dialog). Returns ``fallback`` if nothing usable
    is left. Used for the export dialogs' suggested file names (#105).
    """
    stem = " ".join(_UNSAFE_FILENAME.sub("_", title).split())
    stem = stem[:max_length].rstrip(" .")
    return stem or fallback
```

- [ ] **Step 4: Run to verify they pass**

Run: `$PY -m pytest tests/test_text.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core/text.py tests/test_text.py
git commit -m "add safe_filename for export file names (refs #105)" -m "<trailers>"
```

---

### Task 2: `ui/note_render.py` — the shared renderer and plain text

**Files:**
- Create: `ui/note_render.py`
- Test: `tests/test_note_render.py`

**Interfaces:**
- Consumes: `VaultTextDocument(parent=None, *, device_pixel_ratio=callable)` with `.set_store(store | None)` (`ui/vault_document.py`); `ImageStore` (`core/images.py`); `png_bytes` (`ui/image_ingest.py`, tests only).
- Produces:
  - `OBJECT_REPLACEMENT = "\ufffc"`
  - `render_document(markdown: str, store: ImageStore | None, *, device_pixel_ratio: float = 1.0) -> VaultTextDocument`
  - `plain_text(document: QTextDocument) -> str`

- [ ] **Step 1: Write the failing tests** — `tests/test_note_render.py`:

```python
"""Tests for ``ui.note_render`` — rendering a note for output (#105)."""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.images import ImageStore

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.image_ingest import png_bytes  # noqa: E402
from ui.note_render import OBJECT_REPLACEMENT, plain_text, render_document  # noqa: E402
from ui.vault_document import VaultTextDocument  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def store():
    conn = sqlcipher.connect(":memory:")
    schema.migrate(conn)
    try:
        yield ImageStore(conn)
    finally:
        conn.close()


def _add_image(store):
    image = QImage(400, 200, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    return store.add(png_bytes(image), "image/png", 400, 200)


def test_render_document_is_a_fresh_vault_document(qapp, store):
    record = _add_image(store)
    document = render_document(f"![s](mnimg:{record.id}?w=100)", store)
    assert isinstance(document, VaultTextDocument)
    document.size()  # force layout, which loads the image through the vault
    assert len(document.cache) == 1


def test_plain_text_reads_like_the_note(qapp, store):
    record = _add_image(store)
    markdown = (
        f"# Title\n\nSome **bold** text.\n\n![s](mnimg:{record.id}?w=300)\n\n"
        "- one\n- two\n\n1. first\n2. second\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n```\ncode line\n```\n\nEnd.\n"
    )
    text = plain_text(render_document(markdown, store))
    assert text == (
        "Title\nSome bold text.\n\n- one\n- two\n1. first\n2. second\n\ncode line\nEnd."
    )
    assert OBJECT_REPLACEMENT not in text


def test_plain_text_indents_nested_lists(qapp):
    text = plain_text(render_document("- outer\n  - inner\n- back\n", None))
    assert text == "- outer\n  - inner\n- back"


def test_plain_text_keeps_code_indentation(qapp):
    text = plain_text(render_document("```\n    indented\n```\n", None))
    assert text == "    indented"


def test_plain_text_without_a_store_still_drops_images(qapp):
    text = plain_text(render_document("before\n\n![s](mnimg:5)\n\nafter", None))
    assert text == "before\n\nafter"


def test_plain_text_of_an_empty_note(qapp):
    assert plain_text(render_document("", None)) == ""
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/test_note_render.py -v`
Expected: FAIL (`No module named 'ui.note_render'`).

- [ ] **Step 3: Implement** — `ui/note_render.py`:

```python
"""Rendering a note for output — the one renderer every export shares (#105).

Exports and Copy Text work from a **fresh** document built from the note's
Markdown, not from the live preview. The preview may be hidden, and its
images are sized for the screen's pixel ratio. :func:`render_document` uses
the same vault-backed :class:`~ui.vault_document.VaultTextDocument` as the
preview, so an export looks like the preview, and anything the preview can
draw (images now; tables and graphs later) exports with no extra work.

:func:`plain_text` walks that document rather than using
``QTextDocument.toPlainText``, which would leave an object-replacement
character where each image was and flatten every table cell onto its own
line.

Per CLAUDE.md's layering, the UI may import Qt freely; ``core/`` must never
import this module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QTextBlock, QTextDocument, QTextFrame, QTextListFormat, QTextTable

from ui.vault_document import VaultTextDocument

if TYPE_CHECKING:
    from core.images import ImageStore

#: The character Qt puts in a block's text where an image (or other object) is.
OBJECT_REPLACEMENT = "\ufffc"

# Qt's soft line separator inside a block.
_LINE_SEPARATOR = "\u2028"

_BULLETS = {
    QTextListFormat.Style.ListDisc,
    QTextListFormat.Style.ListCircle,
    QTextListFormat.Style.ListSquare,
}


def render_document(
    markdown: str,
    store: ImageStore | None,
    *,
    device_pixel_ratio: float = 1.0,
) -> VaultTextDocument:
    """A new document showing ``markdown``, with images from ``store``.

    ``store`` may be ``None``; images then render as the missing-image
    placeholder. ``device_pixel_ratio`` sets how many pixels images get per
    logical pixel (the PDF export uses 2.0 so images print sharply).
    """
    document = VaultTextDocument(device_pixel_ratio=lambda: device_pixel_ratio)
    document.set_store(store)
    document.setMarkdown(markdown)
    return document


def plain_text(document: QTextDocument) -> str:
    """The note's text as it reads: no Markdown, no images, no tables.

    List items keep their markers (``-`` for bullets, ``1.`` for numbers),
    indented two spaces per nesting level. Runs of blank lines collapse to one,
    and leading and trailing blank lines are dropped.
    """
    lines: list[str] = []
    _collect(document.rootFrame(), lines)

    result: list[str] = []
    for line in lines:
        if not line and (not result or not result[-1]):
            continue  # a leading blank, or a second blank in a row
        result.append(line)
    while result and not result[-1]:
        result.pop()
    return "\n".join(result)


def _collect(frame: QTextFrame, lines: list[str]) -> None:
    it = frame.begin()
    while not it.atEnd():
        child = it.currentFrame()
        if child is None:
            lines.append(_block_line(it.currentBlock()))
        elif not isinstance(child, QTextTable):
            _collect(child, lines)  # e.g. a quote; tables are skipped whole
        it += 1


def _block_line(block: QTextBlock) -> str:
    text = (
        block.text()
        .replace(OBJECT_REPLACEMENT, "")
        .replace(_LINE_SEPARATOR, "\n")
        .rstrip()
    )
    text_list = block.textList()
    if text_list is None or not text:
        return text
    list_format = text_list.format()
    marker = "-" if list_format.style() in _BULLETS else text_list.itemText(block)
    indent = "  " * max(0, list_format.indent() - 1)
    return f"{indent}{marker} {text}"
```

- [ ] **Step 4: Run to verify they pass**

Run: `$PY -m pytest tests/test_note_render.py -v`
Expected: all PASS.

If Qt's actual structure differs from what the tests expect (for example, how nested list indent is reported, or whether a fenced code block keeps its leading spaces), fix the implementation, not the expected text. If the expected text itself turns out to be unachievable, report DONE_WITH_CONCERNS and explain.

- [ ] **Step 5: Commit**

```bash
git add ui/note_render.py tests/test_note_render.py
git commit -m "render notes for output and extract their plain text (refs #105)" -m "<trailers>"
```

---

### Task 3: `ui/note_export.py` — HTML and PDF files

**Files:**
- Create: `ui/note_export.py`
- Test: `tests/test_note_export.py`

**Interfaces:**
- Consumes:
  - `render_document` (Task 2)
  - `parse_url` (`core/image_refs.py`)
  - `ImageStore.get` → `ImageRecord(.id, .mime, .width, .data)` (`core/images.py`)
- Produces:
  - `data_uri(record: ImageRecord) -> str`
  - `note_html(markdown: str, store: ImageStore | None, *, title: str, image_src: Callable[[ImageRecord], str] = data_uri) -> str`
  - `write_html(markdown, store, path, *, title) -> None` (raises `OSError`)
  - `write_pdf(markdown, store, path, *, title) -> None` (raises `OSError`)
  - constants `PDF_RESOLUTION = 96`, `PDF_IMAGE_RATIO = 2.0`, `PDF_MARGIN_INCHES = 0.75`

- [ ] **Step 1: Write the failing tests** — `tests/test_note_export.py`:

```python
"""Tests for ``ui.note_export`` — HTML and PDF files (#105)."""

import base64
import os
import re

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.images import ImageStore

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pypdf = pytest.importorskip("pypdf")

from PySide6.QtGui import QColor, QFontDatabase, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.image_ingest import png_bytes  # noqa: E402
from ui.note_export import note_html, write_html, write_pdf  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def store():
    conn = sqlcipher.connect(":memory:")
    schema.migrate(conn)
    try:
        yield ImageStore(conn)
    finally:
        conn.close()


@pytest.fixture
def record(store):
    image = QImage(400, 200, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    return store.add(png_bytes(image), "image/png", 400, 200)


def _note(record):
    return (
        f"# Title\n\nSome **bold** text.\n\n![s](mnimg:{record.id}?w=300)\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n"
    )


def test_note_html_embeds_images_as_data_uris(qapp, store, record):
    html = note_html(_note(record), store, title="Title")
    assert "mnimg:" not in html
    payload = re.search(r'src="data:image/png;base64,([^"]+)"', html)
    assert payload is not None
    assert base64.b64decode(payload.group(1)) == record.data
    assert 'width="300"' in html
    assert "<table" in html
    assert "<title>Title</title>" in html


def test_note_html_uses_the_natural_width_when_the_ref_has_none(qapp, store, record):
    html = note_html(f"![s](mnimg:{record.id})", store, title="t")
    assert 'width="400"' in html


def test_note_html_blanks_a_missing_image_but_keeps_its_alt(qapp, store):
    html = note_html("![gone](mnimg:999)", store, title="t")
    assert 'src=""' in html
    assert 'alt="gone"' in html


def test_note_html_accepts_a_custom_image_source(qapp, store, record):
    html = note_html(_note(record), store, title="t", image_src=lambda r: f"cid:img{r.id}")
    assert f'src="cid:img{record.id}"' in html


def test_note_html_escapes_the_title(qapp, store):
    assert "<title>A &amp; B</title>" in note_html("x", store, title="A & B")


def test_write_html_writes_utf8(qapp, store, record, tmp_path):
    path = tmp_path / "note.html"
    write_html(_note(record) + "\nCafé", store, path, title="Café")
    content = path.read_text(encoding="utf-8")
    assert "<title>Café</title>" in content
    assert "Café" in content.split("</title>", 1)[1]


def test_write_pdf_writes_one_letter_page_with_the_image(qapp, store, record, tmp_path):
    path = tmp_path / "note.pdf"
    write_pdf(_note(record), store, path, title="Title")

    reader = pypdf.PdfReader(str(path))
    assert len(reader.pages) == 1
    page = reader.pages[0]
    assert (float(page.mediabox.width), float(page.mediabox.height)) == (612.0, 792.0)
    assert len(page.images) == 1
    assert reader.metadata.title == "Title"


def test_write_pdf_text_is_real_text(qapp, store, record, tmp_path):
    if not QFontDatabase.families():
        pytest.skip("this Qt platform has no fonts, so the PDF has no text")
    path = tmp_path / "note.pdf"
    write_pdf(_note(record), store, path, title="Title")
    text = " ".join(pypdf.PdfReader(str(path)).pages[0].extract_text().split())
    assert "Some bold text." in text


def test_write_pdf_to_a_missing_folder_raises(qapp, store, tmp_path):
    with pytest.raises(OSError):
        write_pdf("x", store, tmp_path / "no-such-folder" / "note.pdf", title="t")
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/test_note_export.py -v`
Expected: FAIL (`No module named 'ui.note_export'`).

- [ ] **Step 3: Implement** — `ui/note_export.py`:

```python
"""Writing a note to HTML and PDF files (#105).

Both formats render through :func:`ui.note_render.render_document`, the same
vault-backed document the preview uses, so an export looks like the preview.

* **HTML** is one self-contained file. A browser can't read the vault, so each
  ``mnimg:`` image becomes a ``data:`` URI holding its original stored bytes,
  at the note's display width. :func:`note_html` takes the image source as a
  parameter, so the Outlook export (#110) can use ``cid:`` references to
  embedded attachments instead.
* **PDF** is US Letter with 0.75" margins and Qt's page-number footer, and has
  the note's title in its metadata. It is written at 96 dpi, so widths match
  the preview, with images rendered at 2x so they print sharply. The text is
  real, selectable text.

Per CLAUDE.md's layering, the UI may import Qt freely; ``core/`` must never
import this module.
"""

from __future__ import annotations

import base64
import html
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter

from core.image_refs import parse_url
from ui.note_render import render_document

if TYPE_CHECKING:
    from core.images import ImageRecord, ImageStore

#: Logical pixels per inch in the PDF — 96 matches the screen, so widths agree.
PDF_RESOLUTION = 96
#: Device pixels per logical pixel for images in the PDF, so they print sharply.
PDF_IMAGE_RATIO = 2.0
PDF_MARGIN_INCHES = 0.75

_IMG_TAG = re.compile(r"<img\b[^>]*>")
_SRC_ATTR = re.compile(r'\bsrc="([^"]*)"')


def data_uri(record: ImageRecord) -> str:
    """A ``data:`` URI carrying ``record``'s original bytes."""
    payload = base64.b64encode(record.data).decode("ascii")
    return f"data:{record.mime};base64,{payload}"


def note_html(
    markdown: str,
    store: ImageStore | None,
    *,
    title: str,
    image_src: Callable[[ImageRecord], str] = data_uri,
) -> str:
    """The note as a complete HTML document, with vault images resolved."""
    source = render_document(markdown, store).toHtml()
    source = source.replace("<head>", f"<head><title>{html.escape(title)}</title>", 1)
    return _IMG_TAG.sub(lambda match: _rewrite_img(match.group(0), store, image_src), source)


def _rewrite_img(
    tag: str, store: ImageStore | None, image_src: Callable[[ImageRecord], str]
) -> str:
    src = _SRC_ATTR.search(tag)
    if src is None:
        return tag
    parsed = parse_url(html.unescape(src.group(1)))
    if parsed is None:
        return tag  # not a vault image; leave it alone
    image_id, width = parsed
    record = store.get(image_id) if store is not None else None
    if record is None:
        new_src = ""  # the browser shows the alt text
    else:
        new_src = image_src(record)
        width = width or record.width
    tag = tag[: src.start(1)] + html.escape(new_src, quote=True) + tag[src.end(1) :]
    if width is not None:
        tag = tag.replace("<img", f'<img width="{width}"', 1)
    return tag


def write_html(
    markdown: str,
    store: ImageStore | None,
    path: str | os.PathLike[str],
    *,
    title: str,
) -> None:
    """Write the note to ``path`` as self-contained UTF-8 HTML."""
    Path(path).write_text(note_html(markdown, store, title=title), encoding="utf-8")


def write_pdf(
    markdown: str,
    store: ImageStore | None,
    path: str | os.PathLike[str],
    *,
    title: str,
) -> None:
    """Write the note to ``path`` as a Letter-size PDF; ``OSError`` on failure."""
    target = Path(path)
    if not target.parent.is_dir():
        raise OSError(f"The folder for {target.name} doesn't exist")

    document = render_document(markdown, store, device_pixel_ratio=PDF_IMAGE_RATIO)
    writer = QPdfWriter(str(target))
    margins = QMarginsF(PDF_MARGIN_INCHES, PDF_MARGIN_INCHES, PDF_MARGIN_INCHES, PDF_MARGIN_INCHES)
    writer.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.Letter),
            QPageLayout.Orientation.Portrait,
            margins,
            QPageLayout.Unit.Inch,
        )
    )
    writer.setResolution(PDF_RESOLUTION)
    writer.setTitle(title)
    writer.setCreator("my_notes")
    document.print_(writer)
    del writer  # the file is finished and closed when the writer is destroyed

    if not target.is_file() or target.stat().st_size == 0:
        raise OSError(f"Couldn't write {target.name}")
```

- [ ] **Step 4: Run to verify they pass**

Run: `$PY -m pytest tests/test_note_export.py -v`
Expected: all PASS, except that `test_write_pdf_text_is_real_text` SKIPS on Windows offscreen (no fonts). That skip is expected; say so in your report.

- [ ] **Step 5: Commit**

```bash
git add ui/note_export.py tests/test_note_export.py
git commit -m "export notes to self-contained HTML and Letter-size PDF (refs #105)" -m "<trailers>"
```

---

### Task 4: `MainWindow` — the Note menu

**Files:**
- Modify: `ui/main_window.py`
  - imports
  - rename the `_IMAGE_STATUS_MS` constant (line ~80) to `_STATUS_MS`, replacing every use
  - `__init__`: the Note menu after the View menu's focus-mode block (~line 341), and `_update_note_actions()` at the end of `__init__`
  - `_on_active_tab_changed` (~line 380)
  - a new "Note menu (#105)" section after the Images section
- Test: `tests/test_note_menu.py`

**Interfaces:**
- Consumes:
  - `safe_filename`, `derive_title` (`core/text.py`)
  - `render_document`, `plain_text` (Task 2)
  - `write_html`, `write_pdf` (Task 3)
  - existing: `self.tabbed_editor.active_tab`, `self.image_store`, `QGuiApplication`, `QFileDialog`, `Path`
- Produces, on `MainWindow`:
  - `copy_text_action`, `export_html_action`, `export_pdf_action`
  - `_note_actions: list[QAction]`
  - `_update_note_actions()`
  - `copy_note_text() -> bool`
  - `export_note_html() -> bool`, `export_note_pdf() -> bool`
  - seam `_choose_export_path(default_name: str, file_filter: str) -> str`

- [ ] **Step 1: Write the failing tests** — `tests/test_note_menu.py`:

```python
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


def test_note_actions_do_nothing_without_a_note(window):
    assert window.copy_note_text() is False
    assert window.export_note_html() is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/test_note_menu.py -v`
Expected: FAIL (`AttributeError: 'MainWindow' object has no attribute '_note_actions'`).

- [ ] **Step 3: Implement — the status constant.** In `ui/main_window.py`, rename `_IMAGE_STATUS_MS` to `_STATUS_MS` at its definition and at every use (a replace-all). Change its comment to:

```python
# How long a status-bar message from an image or Note-menu action stays up (ms);
# errors match run_tool's.
```

- [ ] **Step 4: Implement — imports.**
- Change `from core.text import count_words, derive_title` to `from core.text import count_words, derive_title, safe_filename`.
- Add `from ui.note_export import write_html, write_pdf` and `from ui.note_render import plain_text, render_document`, keeping the `ui.*` imports alphabetical.

- [ ] **Step 5: Implement — the menu.** Directly after the View menu's focus-mode block (the `self.focus_mode_action.triggered.connect(...)` statement), before `# --- Status bar`, add:

```python
        # Note menu: actions on the whole active note (#105). All of them need a
        # note, so they share one enabled state (see _update_note_actions).
        note_menu = self.menuBar().addMenu("&Note")
        self.copy_text_action = note_menu.addAction("Copy &Text")
        self.copy_text_action.triggered.connect(self.copy_note_text)
        note_menu.addSeparator()
        self.export_html_action = note_menu.addAction("Export to &HTML…")
        self.export_html_action.triggered.connect(self.export_note_html)
        self.export_pdf_action = note_menu.addAction("Export to &PDF…")
        self.export_pdf_action.triggered.connect(self.export_note_pdf)
        self._note_actions = [
            self.copy_text_action,
            self.export_html_action,
            self.export_pdf_action,
        ]
```

At the very end of `__init__` (after `self.apply_theme(DEFAULT_THEME)`), add:

```python
        self._update_note_actions()
```

In `_on_active_tab_changed`, add `self._update_note_actions()` as its last line.

- [ ] **Step 6: Implement — the methods.** After the Images (#101) section, add:

```python
    # -------------------------------------------------------------------------
    # Note menu (#105)
    # -------------------------------------------------------------------------

    def _update_note_actions(self) -> None:
        """Enable the Note menu exactly while a note tab is active.

        Runs on every active-tab change — including the one the lock path
        causes when it closes every tab — so the menu is disabled at launch,
        with no tab open, and after a lock, with no extra wiring.
        """
        enabled = self.tabbed_editor.active_tab is not None
        for action in self._note_actions:
            action.setEnabled(enabled)

    def copy_note_text(self) -> bool:
        """Put the active note's readable text on the clipboard (no images or tables)."""
        tab = self.tabbed_editor.active_tab
        clipboard = QGuiApplication.clipboard()
        if tab is None or clipboard is None:
            return False
        text = plain_text(render_document(tab.markdown(), self.image_store))
        clipboard.setText(text)
        self.statusBar().showMessage(f"Copied {len(text)} characters", _STATUS_MS)
        return True

    def export_note_html(self) -> bool:
        """**Note → Export to HTML…**: a self-contained HTML file."""
        return self._export_note(".html", "HTML files (*.html)", write_html)

    def export_note_pdf(self) -> bool:
        """**Note → Export to PDF…**: a Letter-size PDF."""
        return self._export_note(".pdf", "PDF files (*.pdf)", write_pdf)

    def _export_note(self, extension: str, file_filter: str, writer) -> bool:
        tab = self.tabbed_editor.active_tab
        if tab is None:
            return False
        markdown = tab.markdown()
        title = derive_title(markdown)
        path = self._choose_export_path(safe_filename(title) + extension, file_filter)
        if not path:
            return False
        try:
            writer(markdown, self.image_store, path, title=title)
        except OSError as error:
            self.statusBar().showMessage(f"Couldn't export: {error}", _STATUS_MS)
            return False
        self.statusBar().showMessage(f"Exported {Path(path).name}", _STATUS_MS)
        return True

    def _choose_export_path(self, default_name: str, file_filter: str) -> str:
        """The export save dialog — the seam tests replace to avoid a modal dialog."""
        path, _ = QFileDialog.getSaveFileName(self, "Export note", default_name, file_filter)
        return path
```

- [ ] **Step 7: Run the new tests, then the window-level suites**

Run: `$PY -m pytest tests/test_note_menu.py tests/test_image_window.py tests/test_main_window_layout.py tests/test_theme_coverage.py tests/test_idle_lock.py tests/test_new_note.py -v`
Expected: all PASS.

If `test_copy_text_puts_the_readable_text_on_the_clipboard` shows a different character count, recount `"Plan\n- ship it"` (14 characters). The expected text is what matters; don't change it to fit.

- [ ] **Step 8: Commit**

```bash
git add ui/main_window.py tests/test_note_menu.py
git commit -m "add the Note menu with Copy Text and HTML/PDF export (refs #105)" -m "<trailers>"
```

---

### Task 5: Verification, packaged exe, docs

**Files:**
- Modify: `ROADMAP.md` (the #105 box), `LESSONS.md` (one entry)

- [ ] **Step 1: Full suite and lint** — `$PY -m pytest` then `$PY -m ruff check .`. Both must be green; the PDF text test may skip on Windows offscreen.

- [ ] **Step 2: Build the exe** (never into `dist/`):

```bash
$PY -m PyInstaller my_notes.spec --noconfirm --distpath build/dist-105
```

- [ ] **Step 3: Audit** — both must hold:

```bash
grep -o "'[^']*envs.[A-Za-z_]*.[^']*'" build/my_notes/Analysis-00.toc | grep -v playground   # prints nothing
grep -ohE "q(jpeg|webp|gif|pdf)\.dll" build/my_notes/*.toc | sort -u                          # qgif, qjpeg, qpdf, qwebp
```

`qpdf.dll` is Qt's PDF *print-support* plugin. If it isn't listed, check whether the PDF export uses it at all: `QPdfWriter` lives in QtGui and needs no plugin. Report what you find rather than editing the spec file.

- [ ] **Step 4: Headless smoke test** (PowerShell) — stop processes by exact path:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
$env:MY_NOTES_VAULT = "$env:TEMP\my_notes_smoke_105.vault"
$exe = (Resolve-Path ".\build\dist-105\my_notes.exe").Path
$p = Start-Process -FilePath $exe -PassThru -RedirectStandardOutput "$env:TEMP\mn105.out" -RedirectStandardError "$env:TEMP\mn105.err"
try { Wait-Process -Id $p.Id -Timeout 12 -ErrorAction Stop } catch {}
Get-Content "$env:TEMP\mn105.out", "$env:TEMP\mn105.err"
Get-Process my_notes -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe } | ForEach-Object { Stop-Process -Id $_.Id -Confirm:$false }
```

Expected: both output files empty.

- [ ] **Step 5: `LESSONS.md`** — add at the **top** of the dated "Gotchas" section (the file's convention is newest first):

```markdown
- 2026-09-11 — **The `offscreen` Qt platform on Windows has no fonts** (`QFontDatabase.families()` is empty), so a PDF printed there has no text at all — and no fonts embedded. It is not an export bug; the real Windows platform embeds Segoe UI and the text extracts cleanly. Tests asserting PDF text must skip when no fonts are available (`tests/test_note_export.py`). (#105)
```

- [ ] **Step 6: `ROADMAP.md`** — under M11, change `- [ ] Note menu with Copy Text and export to HTML and PDF (#105)` to:

```
- [x] Note menu with Copy Text and export to HTML and PDF _(done: `ui/note_render.py` (fresh vault-backed document + plain text), `ui/note_export.py` (self-contained HTML with data-URI images; Letter PDF via QPdfWriter, images at 2x), Note menu in `ui/main_window.py`, `core.text.safe_filename`.)_ (#105)
```

- [ ] **Step 7: Commit** (no push; the controller opens the PR after the final review):

```bash
git add LESSONS.md ROADMAP.md
git commit -m "docs: lessons and roadmap for the Note menu (refs #105)" -m "<trailers>"
```
