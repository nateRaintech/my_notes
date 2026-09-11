# Screenshots & Images — Slice 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paste, drop, or insert images into notes; store them inside the encrypted vault; render them sharply in the preview; copy, save, or reset them from the preview; sweep unreferenced images on unlock.

**Architecture:** Images are content-hashed blobs in a new `images` table (migration 3). Notes reference them with Markdown images whose URL is `mnimg:<id>?w=<logical px>`. The preview's `QTextDocument` is subclassed so `loadResource` resolves those URLs from the vault through a byte-bounded cache. All parsing of references is pure Python in `core/`. Qt work lives in small single-purpose `ui/` modules.

**Tech Stack:** Python 3.12, PySide6 (Qt 6), sqlcipher3, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-11-screenshot-images-design.md` (issue #101). Slice 2 (drag handles, #102) is **out of scope**.

## Global Constraints

- **`core/` never imports PySide6.** CLAUDE.md's hard rule. `core/images.py` and `core/image_refs.py` are pure Python.
- **Interpreter:** always `/c/Users/Nate/anaconda3/envs/playground/python.exe` (abbreviated `$PY` below). Bare `python` resolves to the wrong conda env on this machine.
  - Tests: `$PY -m pytest …`
  - Lint: `$PY -m ruff check .`
- **URL format:** `mnimg:<id>` or `mnimg:<id>?w=<width>`, where the width is in **logical pixels**. The scheme constant is `SCHEME = "mnimg"` — never `vault:` (it would pollute full-text search).
- **Initial width on insert:** `min(natural logical width, 800)`. Natural logical width is `round(pixel_width / device_pixel_ratio)`.
- **Size cap:** `MAX_IMAGE_BYTES = 20 * 1024 * 1024`.
- **Stored bytes:**
  - PNG, JPEG, GIF and WebP files keep their original bytes and mime.
  - Every other decodable format, and every clipboard image, is stored as PNG.
- **Paste precedence:**
  1. Local image files in the mime data.
  2. Otherwise, if there is any text: default text paste.
  3. Otherwise, if there is an image: image paste.
- **Decoded-image cache:** a 128 MB budget. It is cleared, and the store detached, in `MainWindow.lock_session`.
- **Orphan sweep:** runs only in `app._bind_vault`, before `window.bind_autosave`. Never mid-session.
- **The clipboard is NOT cleared on lock.**
- **Width edits:** a `QTextCursor` span replacement inside `beginEditBlock`/`endEditBlock`. Never `setPlainText`.
- **Commits:**
  - End each subject with `(refs #101)`.
  - End each message with:
    ```
    Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
    Claude-Session: https://claude.ai/code/session_01UpJoMa4T4ra499LUyQnAk7
    ```
  - Work happens on branch `feature/101-screenshot-images`.
- **Never kill the user's running `my_notes`.** They run `dist/my_notes.exe` daily. Build the test exe into `build/dist-101/`, and stop only processes by the PID you captured.

## File Structure

| File | Status | Responsibility |
|---|---|---|
| `core/schema.py` | modify | Migration 3: the `images` table |
| `core/image_refs.py` | create | Parse, build, find and resolve `mnimg:` references in Markdown text |
| `core/images.py` | create | `ImageStore`: add (dedup), get, sweep orphans |
| `ui/image_ingest.py` | create | QImage/file → vault image + Markdown snippet; clipboard/drop mime routing |
| `ui/note_source.py` | create | `NoteSourceEdit`: the note's source pane, image-aware paste/drop |
| `ui/note_tab.py` | modify | Use `NoteSourceEdit`; forward the image store and status messages |
| `ui/tabbed_editor.py` | modify | Hold the image store for every tab; re-emit status messages |
| `ui/vault_document.py` | create | `VaultTextDocument` (`loadResource` for `mnimg:`) + `ImageCache` |
| `ui/preview_images.py` | create | Locate rendered images; apply a width to the source as an undoable span edit |
| `ui/main_window.py` | modify | Preview document, Insert menu, preview context menu, bind/detach the store |
| `app.py` | modify | Build the `ImageStore`, sweep before binding |
| `tests/test_schema.py` | modify | Migration 3 tests |
| `tests/test_image_refs.py` | create | |
| `tests/test_images.py` | create | |
| `tests/test_image_ingest.py` | create | |
| `tests/test_note_source.py` | create | |
| `tests/test_vault_document.py` | create | |
| `tests/test_preview_images.py` | create | |
| `tests/test_image_window.py` | create | |
| `tests/test_image_binding.py` | create | |

---

### Task 1: Migration 3 — the `images` table

**Files:**
- Modify: `core/schema.py` (the module docstring's first paragraph, `SCHEMA_VERSION`, the new `_MIGRATION_3`, `_MIGRATIONS`)
- Test: `tests/test_schema.py` (append)

**Interfaces:**
- Produces: table `images(id, sha256 UNIQUE, mime, width, height, byte_size, data, created_at)`; `schema.SCHEMA_VERSION == 3`; `schema._MIGRATION_1`, `schema._MIGRATION_2` (existing, used by the upgrade test).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_schema.py`:

```python
# -- migration 3: images (#101) ---------------------------------------------


def test_migrate_creates_images_table(conn):
    schema.migrate(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(images)")}
    assert columns == {
        "id", "sha256", "mime", "width", "height", "byte_size", "data", "created_at",
    }


def test_images_sha256_is_unique(conn):
    schema.migrate(conn)
    insert = (
        "INSERT INTO images (sha256, mime, width, height, byte_size, data) "
        "VALUES (?, 'image/png', 1, 1, 1, x'00')"
    )
    conn.execute(insert, ("abc",))
    with pytest.raises(sqlcipher.IntegrityError):
        conn.execute(insert, ("abc",))


def test_migration_3_upgrades_a_v2_vault_and_keeps_its_notes(conn):
    # Build a vault exactly as schema v2 left it, with a note in it.
    conn.executescript(schema._MIGRATION_1)
    conn.executescript(schema._MIGRATION_2)
    conn.execute("PRAGMA user_version = 2")
    conn.execute("INSERT INTO notes (title, body) VALUES ('kept', 'body')")
    conn.commit()

    assert schema.migrate(conn) == 3
    assert "images" in _table_names(conn)
    assert conn.execute("SELECT title FROM notes").fetchall() == [("kept",)]
    assert schema.migrate(conn) == 3  # idempotent
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/test_schema.py -v -k "images or migration_3"`
Expected: 3 FAIL (`no such table: images`, and `migrate` returns 2).

- [ ] **Step 3: Implement** — in `core/schema.py`:

Replace the first sentence of the module docstring:

```python
The vault stores everything in five tables — ``notebooks``, ``notes``, ``tags``,
``note_tags`` and an ``notes_fts`` full-text index — created automatically the
first time a vault is opened.
```

with:

```python
The vault's core is five tables — ``notebooks``, ``notes``, ``tags``,
``note_tags`` and an ``notes_fts`` full-text index — created automatically the
first time a vault is opened; later migrations add ``app_secrets`` (2) and
``images`` (3).
```

Change `SCHEMA_VERSION = 2` to `SCHEMA_VERSION = 3`. After `_MIGRATION_2`, add:

```python
# Migration 3 — images pasted, dropped or inserted into notes (#101).
#   images  vault-global blobs, content-addressed by sha256 so the same image
#           pasted into several notes is stored once. There is deliberately no
#           note_id: a note refers to an image only through `mnimg:<id>` URLs in
#           its body (core.image_refs), and images no body mentions are removed
#           by core.images.ImageStore.sweep_orphans. width/height cache the
#           natural pixel size so it never has to be decoded just to be known.
_MIGRATION_3 = """
CREATE TABLE IF NOT EXISTS images (
    id         INTEGER PRIMARY KEY,
    sha256     TEXT    NOT NULL UNIQUE,
    mime       TEXT    NOT NULL,
    width      INTEGER NOT NULL,
    height     INTEGER NOT NULL,
    byte_size  INTEGER NOT NULL,
    data       BLOB    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""
```

and add `(3, _MIGRATION_3),` as the last entry of `_MIGRATIONS`.

- [ ] **Step 4: Run to verify they pass, plus the whole schema/vault suite**

Run: `$PY -m pytest tests/test_schema.py tests/test_vault.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core/schema.py tests/test_schema.py
git commit -m "add images table as schema migration 3 (refs #101)" -m "<trailers>"
```

---

### Task 2: `core/image_refs.py` — references in Markdown text

**Files:**
- Create: `core/image_refs.py`
- Test: `tests/test_image_refs.py`

**Interfaces:**
- Produces:
  - `SCHEME = "mnimg"`
  - `@dataclass(frozen=True) ImageRef(image_id: int, width: int | None, url_start: int, url_end: int)` — Python string indices of the URL
  - `parse_url(url: str) -> tuple[int, int | None] | None`
  - `image_url(image_id: int, width: int | None = None) -> str`
  - `clean_alt(text: str) -> str`
  - `image_markdown(image_id: int, alt: str, width: int | None = None) -> str`
  - `find_refs(markdown: str) -> list[ImageRef]`
  - `resolve_ref(markdown: str, ordinal: int, image_id: int, width: int | None) -> ImageRef | None`
  - `referenced_ids(markdown: str) -> set[int]`

- [ ] **Step 1: Write the failing tests** — `tests/test_image_refs.py`:

```python
"""Unit tests for ``core.image_refs`` — image references inside note Markdown.

Pure Python, no Qt: these pin the text-level contract the preview and the
resize/reset edits rely on. The key property is that ``find_refs`` lists exactly
the images the preview renders, in order, with spans that slice out the URL.
"""

import pytest

from core.image_refs import (
    ImageRef,
    clean_alt,
    find_refs,
    image_markdown,
    image_url,
    parse_url,
    referenced_ids,
    resolve_ref,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("mnimg:7?w=600", (7, 600)),
        ("mnimg:7", (7, None)),
        ("mnimg:7?w=0", (7, None)),
        ("mnimg:7?w=abc", (7, None)),
        ("mnimg:7?foo=1&w=300", (7, 300)),
        ("mnimg:x", None),
        ("mnimg:", None),
        ("http://example.com/a.png", None),
    ],
)
def test_parse_url(url, expected):
    assert parse_url(url) == expected


def test_image_url_round_trips():
    assert image_url(7) == "mnimg:7"
    assert image_url(7, 600) == "mnimg:7?w=600"
    assert parse_url(image_url(7, 600)) == (7, 600)


def test_clean_alt_removes_brackets_and_line_breaks():
    assert clean_alt("shot [1]\nfinal") == "shot 1 final"
    assert clean_alt("  ") == "image"


def test_image_markdown():
    assert image_markdown(3, "screenshot", 800) == "![screenshot](mnimg:3?w=800)"
    assert image_markdown(3, "a]b") == "![a b](mnimg:3)"


def _urls(markdown):
    return [markdown[r.url_start:r.url_end] for r in find_refs(markdown)]


def test_find_refs_spans_slice_out_the_url():
    md = "before ![s](mnimg:7?w=600) after"
    (ref,) = find_refs(md)
    assert ref == ImageRef(image_id=7, width=600, url_start=12, url_end=25)
    assert md[ref.url_start:ref.url_end] == "mnimg:7?w=600"


def test_find_refs_keeps_document_order_and_duplicates():
    md = "![a](mnimg:1?w=100)\n\ntext ![b](mnimg:1?w=300) ![c](mnimg:2)"
    assert [(r.image_id, r.width) for r in find_refs(md)] == [(1, 100), (1, 300), (2, None)]


def test_find_refs_offsets_are_right_on_later_lines():
    md = "line one\nline two ![s](mnimg:9)\n"
    assert _urls(md) == ["mnimg:9"]


def test_find_refs_skips_fenced_code_blocks():
    md = "```\n![s](mnimg:1)\n```\n![s](mnimg:2)\n~~~md\n![s](mnimg:3)\n~~~\n"
    assert [r.image_id for r in find_refs(md)] == [2]


def test_find_refs_fence_needs_a_matching_closer():
    # A ~~~ line does not close a ``` fence, and an unclosed fence runs to the end.
    md = "```\n~~~\n![s](mnimg:1)\n"
    assert find_refs(md) == []


def test_find_refs_skips_inline_code():
    md = "`![s](mnimg:1)` and ![s](mnimg:2)"
    assert [r.image_id for r in find_refs(md)] == [2]


def test_find_refs_ignores_other_images_and_malformed_ids():
    md = "![a](http://x/y.png) ![b](mnimg:abc) ![c](mnimg:4)"
    assert [r.image_id for r in find_refs(md)] == [4]


def test_resolve_ref_uses_the_ordinal_when_the_id_matches():
    md = "![a](mnimg:1?w=100) ![b](mnimg:1?w=100)"
    assert resolve_ref(md, 1, 1, 100) == find_refs(md)[1]


def test_resolve_ref_falls_back_to_the_unique_id_and_width_match():
    md = "![a](mnimg:1?w=100) ![b](mnimg:2?w=50)"
    # The ordinal is wrong (the preview counted something find_refs didn't), but
    # only one ref has id 2 at width 50, so it is still unambiguous.
    assert resolve_ref(md, 0, 2, 50) == find_refs(md)[1]


def test_resolve_ref_declines_when_ambiguous():
    md = "![a](mnimg:1?w=100) ![b](mnimg:1?w=100)"
    assert resolve_ref(md, 5, 1, 100) is None


def test_resolve_ref_declines_when_absent():
    assert resolve_ref("no images", 0, 1, None) is None


def test_referenced_ids_is_liberal():
    md = "```\n![s](mnimg:1)\n```\n![s](mnimg:2)\nbare mnimg:3 mention"
    assert referenced_ids(md) == {1, 2, 3}
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/test_image_refs.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'core.image_refs'`).

- [ ] **Step 3: Implement** — `core/image_refs.py`:

```python
"""Image references inside note Markdown — finding, parsing and building them.

A note refers to an image stored in the vault (:mod:`core.images`) with an
ordinary Markdown image whose URL uses a private scheme::

    ![screenshot](mnimg:7?w=600)

``7`` is the ``images.id`` and ``w`` the display width in logical pixels; with no
``w`` the image renders at its natural size. The width lives in the Markdown, so a
copied line keeps its size and a resize is an ordinary, undoable text edit.

The scheme is ``mnimg`` rather than something readable like ``vault`` because the
full-text index tokenises URLs into words: a ``vault:`` scheme would make every
note with an image match a search for "vault".

Pure Python, no Qt (CLAUDE.md). The UI renders these references
(``ui/vault_document.py``) and edits them (``ui/preview_images.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

SCHEME = "mnimg"

# ![alt](mnimg:...) — group 1 is the URL. Alt text can't span lines or hold "]".
_IMAGE_RE = re.compile(r"!\[[^\]\n]*\]\((" + SCHEME + r":[^)\s]*)\)")
_URL_RE = re.compile(r"^" + SCHEME + r":(\d+)(?:\?(.*))?$")
_WIDTH_RE = re.compile(r"(?:^|&)w=(\d+)(?:&|$)")
_ANY_ID_RE = re.compile(SCHEME + r":(\d+)")
# A fence opener/closer: up to three spaces of indent, then 3+ backticks or tildes.
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
# An inline code span: a run of backticks, content, the same-length run again.
_CODE_SPAN_RE = re.compile(r"(`+)(?!`)(.+?)(?<!`)\1(?!`)")


@dataclass(frozen=True)
class ImageRef:
    """One image reference as it appears in a note's Markdown.

    ``url_start`` / ``url_end`` delimit the URL (``mnimg:7?w=600``) as Python
    string indices, so a width change replaces exactly that span and nothing
    around it.
    """

    image_id: int
    width: int | None
    url_start: int
    url_end: int


def parse_url(url: str) -> tuple[int, int | None] | None:
    """``"mnimg:7?w=600"`` -> ``(7, 600)``; ``"mnimg:7"`` -> ``(7, None)``.

    ``None`` for anything that is not an ``mnimg`` URL with a numeric id. A
    missing, zero or non-numeric ``w`` means "natural size", not an error.
    """
    match = _URL_RE.match(url)
    if match is None:
        return None
    width = None
    if match.group(2):
        found = _WIDTH_RE.search(match.group(2))
        if found and int(found.group(1)) > 0:
            width = int(found.group(1))
    return int(match.group(1)), width


def image_url(image_id: int, width: int | None = None) -> str:
    """The URL for ``image_id`` at ``width`` logical px (``None``: natural size)."""
    if width is None:
        return f"{SCHEME}:{image_id}"
    return f"{SCHEME}:{image_id}?w={width}"


def clean_alt(text: str) -> str:
    """``text`` made safe as image alt text: no brackets, no line breaks."""
    cleaned = " ".join(re.sub(r"[\[\]\r\n]+", " ", text).split())
    return cleaned or "image"


def image_markdown(image_id: int, alt: str, width: int | None = None) -> str:
    """The Markdown that shows ``image_id`` — what an insert puts in the note."""
    return f"![{clean_alt(alt)}]({image_url(image_id, width)})"


def find_refs(markdown: str) -> list[ImageRef]:
    """Every image reference the preview renders as an image, in document order.

    Skips references inside fenced code blocks and inline code spans, which the
    preview shows as literal text. Indented (four-space) code blocks are *not*
    detected — telling them from nested list items needs a full Markdown parser —
    so callers mapping a rendered image back to its text go through
    :func:`resolve_ref`, which verifies the match.
    """
    refs: list[ImageRef] = []
    offset = 0
    fence: str | None = None
    # splitlines(keepends=True) concatenates back to `markdown` exactly, so the
    # running offset stays a true index into the original string.
    for line in markdown.splitlines(keepends=True):
        fence_match = _FENCE_RE.match(line)
        if fence is not None:
            if fence_match and _closes(line, fence):
                fence = None
        elif fence_match:
            fence = fence_match.group(1)
        else:
            refs.extend(_refs_in_line(line, offset))
        offset += len(line)
    return refs


def _closes(line: str, fence: str) -> bool:
    """Whether ``line`` closes a block opened by ``fence`` (CommonMark rules)."""
    stripped = line.strip()
    return len(stripped) >= len(fence) and set(stripped) == {fence[0]}


def _refs_in_line(line: str, offset: int) -> list[ImageRef]:
    code_spans = [m.span() for m in _CODE_SPAN_RE.finditer(line)]
    refs = []
    for match in _IMAGE_RE.finditer(line):
        if any(start <= match.start() < end for start, end in code_spans):
            continue
        parsed = parse_url(match.group(1))
        if parsed is None:
            continue
        refs.append(
            ImageRef(parsed[0], parsed[1], offset + match.start(1), offset + match.end(1))
        )
    return refs


def resolve_ref(
    markdown: str, ordinal: int, image_id: int, width: int | None
) -> ImageRef | None:
    """The ref for the ``ordinal``-th rendered image, verified — or ``None``.

    The preview identifies an image by its position among rendered images. That
    normally equals its index in :func:`find_refs`, but the two parsers can
    disagree on edge cases, so the index is trusted only if the ids match.
    Otherwise the one ref with the same id *and* width is used, and if that is
    ambiguous nothing is returned: editing the wrong image is worse than
    declining.
    """
    refs = find_refs(markdown)
    if 0 <= ordinal < len(refs) and refs[ordinal].image_id == image_id:
        return refs[ordinal]
    candidates = [r for r in refs if r.image_id == image_id and r.width == width]
    return candidates[0] if len(candidates) == 1 else None


def referenced_ids(markdown: str) -> set[int]:
    """Every image id ``markdown`` mentions *anywhere*, code blocks included.

    Deliberately more liberal than :func:`find_refs`: the orphan sweep uses it,
    and keeping an image a note only mentions costs a little space, while
    deleting one it still needs loses it for good.
    """
    return {int(found) for found in _ANY_ID_RE.findall(markdown)}
```

- [ ] **Step 4: Run to verify they pass**

Run: `$PY -m pytest tests/test_image_refs.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core/image_refs.py tests/test_image_refs.py
git commit -m "add mnimg image-reference parsing for note Markdown (refs #101)" -m "<trailers>"
```

---

### Task 3: `core/images.py` — the image store

**Files:**
- Create: `core/images.py`
- Test: `tests/test_images.py`

**Interfaces:**
- Consumes: `core.image_refs.referenced_ids` (Task 2); the `images` table (Task 1).
- Produces:
  - `MAX_IMAGE_BYTES = 20 * 1024 * 1024`
  - `extension_for(mime: str) -> str`
  - `@dataclass(frozen=True) ImageRecord(id: int, sha256: str, mime: str, width: int, height: int, byte_size: int, data: bytes, created_at: str)`
  - `@dataclass(frozen=True) SweepResult(count: int, freed_bytes: int)`
  - `ImageStore(connection)` with:
    - `.add(data: bytes, mime: str, width: int, height: int) -> ImageRecord` — raises `ValueError` on empty data, data over the cap, or a non-positive size
    - `.get(image_id: int) -> ImageRecord | None`
    - `.sweep_orphans() -> SweepResult`

- [ ] **Step 1: Write the failing tests** — `tests/test_images.py`:

```python
"""Unit tests for ``core.images`` — images stored inside the vault (#101).

Pure Python, no Qt. The store never decodes image data, so plain byte strings
stand in for real PNGs here.
"""

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

import core.images as images_module
from core import schema
from core.images import ImageStore, SweepResult, extension_for
from core.repository import Repository


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


def _count(conn):
    return conn.execute("SELECT count(*) FROM images").fetchone()[0]


def test_add_stores_and_returns_the_record(store):
    record = store.add(b"\x89PNG-a", "image/png", 400, 200)
    assert record.id > 0
    assert (record.mime, record.width, record.height) == ("image/png", 400, 200)
    assert record.byte_size == 6
    assert record.data == b"\x89PNG-a"
    assert len(record.sha256) == 64
    assert store.get(record.id) == record


def test_add_dedups_identical_bytes(conn, store):
    first = store.add(b"same", "image/png", 1, 1)
    second = store.add(b"same", "image/png", 1, 1)
    assert second.id == first.id
    assert _count(conn) == 1


def test_add_keeps_different_bytes_apart(store):
    assert store.add(b"a", "image/png", 1, 1).id != store.add(b"b", "image/png", 1, 1).id


def test_get_missing_is_none(store):
    assert store.get(999) is None


@pytest.mark.parametrize(
    ("data", "width", "height"),
    [(b"", 1, 1), (b"x", 0, 1), (b"x", 1, -1)],
)
def test_add_rejects_invalid_input(store, data, width, height):
    with pytest.raises(ValueError):
        store.add(data, "image/png", width, height)


def test_add_rejects_oversized_data(store, monkeypatch):
    monkeypatch.setattr(images_module, "MAX_IMAGE_BYTES", 4)
    with pytest.raises(ValueError, match="larger than"):
        store.add(b"12345", "image/png", 1, 1)


def test_sweep_removes_only_images_no_note_mentions(conn, store):
    repo = Repository(conn)
    kept = store.add(b"kept", "image/png", 1, 1)
    orphan = store.add(b"orphan!", "image/png", 1, 1)
    in_code = store.add(b"code", "image/png", 1, 1)
    repo.create_note(title="a", body=f"![s](mnimg:{kept.id}?w=100)")
    repo.create_note(title="b", body=f"```\n![s](mnimg:{in_code.id})\n```")

    result = store.sweep_orphans()

    assert result == SweepResult(count=1, freed_bytes=len(b"orphan!"))
    assert store.get(orphan.id) is None
    assert store.get(kept.id) is not None
    assert store.get(in_code.id) is not None  # liberal: a mention in code keeps it


def test_sweep_with_nothing_to_do(store):
    assert store.sweep_orphans() == SweepResult(count=0, freed_bytes=0)


def test_extension_for():
    assert extension_for("image/png") == "png"
    assert extension_for("image/jpeg") == "jpg"
    assert extension_for("image/gif") == "gif"
    assert extension_for("image/webp") == "webp"
    assert extension_for("application/x-unknown") == "bin"
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/test_images.py -v`
Expected: FAIL (`No module named 'core.images'`).

- [ ] **Step 3: Implement** — `core/images.py`:

```python
"""Images stored inside the encrypted vault (#101).

Pasted, dropped and inserted images live as blobs in the ``images`` table
(schema migration 3), so they are encrypted at rest with everything else — never
written to disk in the clear. A note refers to one only through an ``mnimg:<id>``
URL in its body (:mod:`core.image_refs`); images are vault-global and
content-addressed by SHA-256, so the same screenshot pasted into five notes is
stored once.

Because no foreign key ties an image to a note, nothing cascades when a note or
an image line is deleted. :meth:`ImageStore.sweep_orphans` reclaims images no
note mentions; the app runs it only at unlock, before any tab can open (see
``app._bind_vault``), which is the one moment it is safe.

Pure Python, no Qt: the store never decodes image data. Callers pass the natural
width and height, because only the UI layer has a ``QImage`` to measure.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from core.image_refs import referenced_ids

if TYPE_CHECKING:
    from sqlcipher3.dbapi2 import Connection

#: The largest image the vault accepts, in bytes.
MAX_IMAGE_BYTES = 20 * 1024 * 1024

_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
}

_COLUMNS = "id, sha256, mime, width, height, byte_size, data, created_at"


def extension_for(mime: str) -> str:
    """The file extension to save an image of ``mime`` with."""
    return _EXTENSIONS.get(mime, "bin")


@dataclass(frozen=True)
class ImageRecord:
    """An immutable snapshot of a row in ``images``. ``width``/``height`` are px."""

    id: int
    sha256: str
    mime: str
    width: int
    height: int
    byte_size: int
    data: bytes
    created_at: str


@dataclass(frozen=True)
class SweepResult:
    """What :meth:`ImageStore.sweep_orphans` removed."""

    count: int
    freed_bytes: int


class ImageStore:
    """Add, fetch and sweep vault images over an open, migrated connection.

    Like :class:`~core.repository.Repository`, every write commits before
    returning.
    """

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def add(self, data: bytes, mime: str, width: int, height: int) -> ImageRecord:
        """Store ``data`` and return its record — or the existing one, if identical.

        Raises :class:`ValueError` for empty data, data over
        :data:`MAX_IMAGE_BYTES`, or a non-positive size.
        """
        if not data:
            raise ValueError("The image is empty")
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"The image is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MB")
        if width <= 0 or height <= 0:
            raise ValueError("The image has no size")

        digest = hashlib.sha256(data).hexdigest()
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM images WHERE sha256 = ?", (digest,)
        ).fetchone()
        if row is not None:
            return _record(row)

        cursor = self._conn.execute(
            "INSERT INTO images (sha256, mime, width, height, byte_size, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (digest, mime, width, height, len(data), data),
        )
        self._conn.commit()
        record = self.get(cursor.lastrowid)
        assert record is not None
        return record

    def get(self, image_id: int) -> ImageRecord | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM images WHERE id = ?", (image_id,)
        ).fetchone()
        return _record(row) if row is not None else None

    def sweep_orphans(self) -> SweepResult:
        """Delete every image no note body mentions; report what went.

        Uses :func:`~core.image_refs.referenced_ids`, which counts mentions
        anywhere — code blocks included — so the sweep errs toward keeping.
        Deleting rows does not shrink the vault file: SQLite keeps the freed
        pages and reuses them for later images.
        """
        referenced: set[int] = set()
        for (body,) in self._conn.execute("SELECT body FROM notes"):
            referenced |= referenced_ids(body)

        doomed = [
            (image_id, size)
            for image_id, size in self._conn.execute("SELECT id, byte_size FROM images")
            if image_id not in referenced
        ]
        self._conn.executemany(
            "DELETE FROM images WHERE id = ?", [(image_id,) for image_id, _ in doomed]
        )
        self._conn.commit()
        return SweepResult(count=len(doomed), freed_bytes=sum(size for _, size in doomed))


def _record(row: tuple) -> ImageRecord:
    return ImageRecord(
        id=row[0],
        sha256=row[1],
        mime=row[2],
        width=row[3],
        height=row[4],
        byte_size=row[5],
        data=bytes(row[6]),
        created_at=row[7],
    )
```

- [ ] **Step 4: Run to verify they pass**

Run: `$PY -m pytest tests/test_images.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core/images.py tests/test_images.py
git commit -m "add ImageStore for content-addressed images in the vault (refs #101)" -m "<trailers>"
```

---

### Task 4: `ui/image_ingest.py` — turning images and files into vault images

**Files:**
- Create: `ui/image_ingest.py`
- Test: `tests/test_image_ingest.py`

**Interfaces:**
- Consumes: `ImageStore.add` and `MAX_IMAGE_BYTES` (Task 3); `image_markdown` (Task 2).
- Produces:
  - `PASTE_MAX_WIDTH = 800`
  - `class IngestError(Exception)`
  - `natural_logical_width(pixel_width: int, device_pixel_ratio: float) -> int`
  - `initial_width(pixel_width: int, device_pixel_ratio: float) -> int`
  - `png_bytes(image: QImage) -> bytes`
  - `ingest_image(store, image: QImage, *, device_pixel_ratio: float, alt: str = "screenshot") -> str` — returns the Markdown snippet
  - `ingest_file(store, path: str | os.PathLike, *, device_pixel_ratio: float) -> str` — returns the Markdown snippet
  - `local_image_paths(mime: QMimeData) -> list[str]`
  - `wants_image_paste(mime: QMimeData) -> bool`

- [ ] **Step 1: Write the failing tests** — `tests/test_image_ingest.py`:

```python
"""Tests for ``ui.image_ingest`` — images and files into the vault (#101)."""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.images import ImageStore

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import ui.image_ingest as ingest_module  # noqa: E402
from ui.image_ingest import (  # noqa: E402
    IngestError,
    ingest_file,
    ingest_image,
    initial_width,
    local_image_paths,
    natural_logical_width,
    wants_image_paste,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


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


def _image(width, height, color="red"):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


def test_natural_logical_width_divides_by_the_scale():
    assert natural_logical_width(1200, 1.5) == 800
    assert natural_logical_width(400, 1.0) == 400


@pytest.mark.parametrize(
    ("pixels", "ratio", "expected"),
    [(3840, 1.0, 800), (300, 1.0, 300), (1200, 1.5, 800), (900, 1.5, 600)],
)
def test_initial_width_caps_at_800_logical_px(pixels, ratio, expected):
    assert initial_width(pixels, ratio) == expected


def test_ingest_image_stores_png_and_returns_markdown(qapp, store):
    markdown = ingest_image(store, _image(400, 200), device_pixel_ratio=1.0)
    assert markdown == "![screenshot](mnimg:1?w=400)"
    record = store.get(1)
    assert record.mime == "image/png"
    assert (record.width, record.height) == (400, 200)
    assert record.data.startswith(PNG_SIGNATURE)


def test_ingest_image_twice_stores_one_blob(qapp, store):
    first = ingest_image(store, _image(10, 10), device_pixel_ratio=1.0)
    second = ingest_image(store, _image(10, 10), device_pixel_ratio=1.0)
    assert first == second


def test_ingest_image_rejects_a_null_image(qapp, store):
    with pytest.raises(IngestError):
        ingest_image(store, QImage(), device_pixel_ratio=1.0)


def test_ingest_file_keeps_jpeg_bytes(qapp, store, tmp_path):
    path = tmp_path / "holiday photo.jpg"
    assert _image(64, 32).save(str(path), "JPEG")
    markdown = ingest_file(store, path, device_pixel_ratio=1.0)
    assert markdown == "![holiday photo](mnimg:1?w=64)"
    record = store.get(1)
    assert record.mime == "image/jpeg"
    assert record.data == path.read_bytes()


def test_ingest_file_reencodes_other_formats_as_png(qapp, store, tmp_path):
    path = tmp_path / "old.bmp"
    assert _image(8, 8).save(str(path), "BMP")
    ingest_file(store, path, device_pixel_ratio=1.0)
    record = store.get(1)
    assert record.mime == "image/png"
    assert record.data.startswith(PNG_SIGNATURE)


def test_ingest_file_rejects_something_that_is_not_an_image(qapp, store, tmp_path):
    path = tmp_path / "fake.png"
    path.write_bytes(b"not an image at all")
    with pytest.raises(IngestError, match="fake.png"):
        ingest_file(store, path, device_pixel_ratio=1.0)


def test_ingest_file_rejects_oversized_files(qapp, store, tmp_path, monkeypatch):
    monkeypatch.setattr(ingest_module, "MAX_IMAGE_BYTES", 10)
    path = tmp_path / "big.png"
    assert _image(50, 50).save(str(path), "PNG")
    with pytest.raises(IngestError, match="larger than"):
        ingest_file(store, path, device_pixel_ratio=1.0)


def test_local_image_paths_needs_every_url_to_be_a_local_image(qapp, tmp_path):
    png, jpg, txt = tmp_path / "a.png", tmp_path / "b.JPG", tmp_path / "c.txt"
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(png)), QUrl.fromLocalFile(str(jpg))])
    expected = [QUrl.fromLocalFile(str(p)).toLocalFile() for p in (png, jpg)]
    assert local_image_paths(mime) == expected

    mime.setUrls([QUrl.fromLocalFile(str(png)), QUrl.fromLocalFile(str(txt))])
    assert local_image_paths(mime) == []

    mime.setUrls([QUrl("https://example.com/a.png")])
    assert local_image_paths(mime) == []


def test_wants_image_paste_lets_text_win(qapp):
    image_only = QMimeData()
    image_only.setImageData(_image(4, 4))
    assert wants_image_paste(image_only) is True

    # Word / Excel / Outlook put text AND a picture of it on the clipboard.
    both = QMimeData()
    both.setImageData(_image(4, 4))
    both.setText("a1\tb1")
    assert wants_image_paste(both) is False

    text_only = QMimeData()
    text_only.setText("hello")
    assert wants_image_paste(text_only) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/test_image_ingest.py -v`
Expected: FAIL (`No module named 'ui.image_ingest'`).

- [ ] **Step 3: Implement** — `ui/image_ingest.py`:

```python
"""Turning pasted, dropped and chosen images into vault images (#101).

One ingest path behind three entry points — Ctrl+V and drag & drop (via
``ui/note_source.py``) and **Insert → Image…** (``MainWindow``). Each stores the
image with :class:`~core.images.ImageStore` and returns the Markdown to insert,
``![alt](mnimg:<id>?w=<px>)``.

* Clipboard images are encoded as PNG — lossless, right for screenshots.
* Files keep their **original bytes** when they are PNG, JPEG, GIF or WebP, so a
  200 KB photo does not become a 2 MB PNG; other decodable formats become PNG.
* The initial width is the image's natural logical width capped at
  :data:`PASTE_MAX_WIDTH`, so a 4K screenshot doesn't land as a wall. The stored
  bytes stay full resolution — resizing is display-only.

Per CLAUDE.md's layering, the UI may import Qt freely; ``core/`` must never
import this module.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QBuffer, QIODevice, QMimeData
from PySide6.QtGui import QImage, QImageReader

from core.image_refs import image_markdown
from core.images import MAX_IMAGE_BYTES

if TYPE_CHECKING:
    from core.images import ImageStore

#: The widest (in logical px) a freshly inserted image starts out.
PASTE_MAX_WIDTH = 800

# Qt image formats whose original bytes are stored as-is, and their mime types.
_KEEP_ORIGINAL = {
    b"png": "image/png",
    b"jpeg": "image/jpeg",
    b"gif": "image/gif",
    b"webp": "image/webp",
}


class IngestError(Exception):
    """An image could not be added; the message is shown to the user as-is."""


def natural_logical_width(pixel_width: int, device_pixel_ratio: float) -> int:
    """The width, in logical px, at which one image pixel is one device pixel.

    That is both the sharpest rendering and the size a screenshot appeared on
    screen when it was taken.
    """
    return max(1, round(pixel_width / device_pixel_ratio))


def initial_width(pixel_width: int, device_pixel_ratio: float) -> int:
    """The ``w`` a newly inserted image gets: natural size, capped at 800."""
    return min(natural_logical_width(pixel_width, device_pixel_ratio), PASTE_MAX_WIDTH)


def png_bytes(image: QImage) -> bytes:
    """``image`` encoded as PNG."""
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def ingest_image(
    store: ImageStore,
    image: QImage,
    *,
    device_pixel_ratio: float,
    alt: str = "screenshot",
) -> str:
    """Store a clipboard image as PNG; return the Markdown that shows it."""
    if image.isNull():
        raise IngestError("The clipboard image is empty")
    return _store(store, png_bytes(image), "image/png", image, alt, device_pixel_ratio)


def ingest_file(
    store: ImageStore,
    path: str | os.PathLike[str],
    *,
    device_pixel_ratio: float,
) -> str:
    """Store the image file at ``path``; return the Markdown that shows it."""
    file = Path(path)
    try:
        size = file.stat().st_size
        if size > MAX_IMAGE_BYTES:
            raise IngestError(f"{file.name} is larger than {_max_mb()} MB")
        data = file.read_bytes()
    except OSError as error:
        raise IngestError(f"Can't read {file.name}: {error.strerror}") from error

    buffer = QBuffer()
    buffer.setData(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    image_format = bytes(reader.format())
    image = reader.read()
    if image.isNull():
        raise IngestError(f"{file.name} isn't an image this app can read")

    mime = _KEEP_ORIGINAL.get(image_format)
    if mime is None:
        data, mime = png_bytes(image), "image/png"
    return _store(store, data, mime, image, file.stem, device_pixel_ratio)


def local_image_paths(mime: QMimeData) -> list[str]:
    """The dropped files in ``mime`` — but only if *every* URL is a local image.

    A mixed drop (an image plus a text file, or a web link) returns ``[]`` so it
    falls through to Qt's default handling instead of half-applying.
    """
    if not mime.hasUrls():
        return []
    readable = {bytes(f).decode().lower() for f in QImageReader.supportedImageFormats()}
    paths = []
    for url in mime.urls():
        if not url.isLocalFile():
            return []
        path = url.toLocalFile()
        if Path(path).suffix.lower().lstrip(".") not in readable:
            return []
        paths.append(path)
    return paths


def wants_image_paste(mime: QMimeData) -> bool:
    """Whether pasting/dropping ``mime`` should insert images rather than text.

    Text wins over an image: Word, Excel and Outlook put both text and a picture
    of it on the clipboard, and pasting a table as a screenshot would be wrong.
    Snipping Tool / Win+Shift+S provide an image and no text.
    """
    if local_image_paths(mime):
        return True
    return mime.hasImage() and not mime.hasText()


def _store(
    store: ImageStore,
    data: bytes,
    mime: str,
    image: QImage,
    alt: str,
    device_pixel_ratio: float,
) -> str:
    if len(data) > MAX_IMAGE_BYTES:
        raise IngestError(f"The image is larger than {_max_mb()} MB")
    try:
        record = store.add(data, mime, image.width(), image.height())
    except ValueError as error:
        raise IngestError(str(error)) from error
    return image_markdown(record.id, alt, initial_width(image.width(), device_pixel_ratio))


def _max_mb() -> int:
    return MAX_IMAGE_BYTES // (1024 * 1024)
```

- [ ] **Step 4: Run to verify they pass**

Run: `$PY -m pytest tests/test_image_ingest.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/image_ingest.py tests/test_image_ingest.py
git commit -m "add image ingest for clipboard images and image files (refs #101)" -m "<trailers>"
```

---

### Task 5: `NoteSourceEdit` — image-aware paste and drop in every tab

**Files:**
- Create: `ui/note_source.py`
- Modify: `ui/note_tab.py` (imports; `__init__` builds `NoteSourceEdit`; a new `status_message` signal; a new `set_image_store`)
- Modify: `ui/tabbed_editor.py` (`tab_status_message` signal, `_image_store`, `set_image_store`, `_make_tab`)
- Test: `tests/test_note_source.py`

**Interfaces:**
- Consumes: `wants_image_paste`, `local_image_paths`, `ingest_image`, `ingest_file`, `IngestError`, `initial_width` (Task 4).
- Produces:
  - `NoteSourceEdit(QPlainTextEdit)` with attribute `image_store: ImageStore | None` and signal `status_message(str)`
  - `NoteTab.status_message(str)` and `NoteTab.set_image_store(store | None)`
  - `TabbedEditor.tab_status_message(str)` and `TabbedEditor.set_image_store(store | None)`

- [ ] **Step 1: Write the failing tests** — `tests/test_note_source.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/test_note_source.py -v`
Expected: FAIL (`No module named 'ui.note_source'`).

- [ ] **Step 3: Implement `ui/note_source.py`**

```python
"""The note's editable source pane, with image paste and drop (#101).

A plain :class:`QPlainTextEdit` except for :meth:`insertFromMimeData`, the one
hook Qt routes both Ctrl+V and drag & drop through. When the incoming data is an
image — or local image files — and a vault is bound (``image_store``), it is
stored in the vault and replaced by Markdown that shows it. Anything else, and
everything while no vault is bound, goes to Qt's default handling unchanged.

Failures (an unreadable or oversized file) never raise out of a paste: they are
reported through :attr:`status_message` and the note is left untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QMimeData, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QPlainTextEdit, QWidget

from ui.image_ingest import (
    IngestError,
    ingest_file,
    ingest_image,
    local_image_paths,
    wants_image_paste,
)

if TYPE_CHECKING:
    from core.images import ImageStore


class NoteSourceEdit(QPlainTextEdit):
    """Markdown source editor that turns pasted/dropped images into vault images."""

    #: A message for the status bar (e.g. why an image couldn't be added).
    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        #: The vault's image store, or ``None`` while no vault is bound.
        self.image_store: ImageStore | None = None

    def canInsertFromMimeData(self, source: QMimeData) -> bool:
        if self.image_store is not None and wants_image_paste(source):
            return True
        return super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source: QMimeData) -> None:
        if self.image_store is None or not wants_image_paste(source):
            super().insertFromMimeData(source)
            return
        try:
            markdown = self._ingest(source)
        except IngestError as error:
            self.status_message.emit(str(error))
            return
        # insertPlainText is a single undoable edit at the caret.
        self.insertPlainText(markdown)

    def _ingest(self, source: QMimeData) -> str:
        assert self.image_store is not None
        ratio = self.devicePixelRatioF()
        paths = local_image_paths(source)
        if paths:
            return "\n\n".join(
                ingest_file(self.image_store, path, device_pixel_ratio=ratio)
                for path in paths
            )
        image = source.imageData()
        if isinstance(image, QPixmap):
            image = image.toImage()
        if not isinstance(image, QImage):
            raise IngestError("The clipboard doesn't hold a readable image")
        return ingest_image(self.image_store, image, device_pixel_ratio=ratio)
```


- [ ] **Step 4: Wire it into `ui/note_tab.py`**

Change the Qt import line to:

```python
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget
```

Add `from ui.note_source import NoteSourceEdit` after `from ui.autosave import AutoSaveController`. Under `TYPE_CHECKING`, add `from core.images import ImageStore`.

Add a signal after `context_menu_requested = Signal(QPoint)`:

```python
    #: Re-emitted from the source pane: a status-bar message (e.g. an image
    #: that couldn't be added, #101).
    status_message = Signal(str)
```

In `__init__`, replace `self.source = QPlainTextEdit()` with `self.source = NoteSourceEdit()`, and after `self.source.textChanged.connect(self.text_changed)` add:

```python
        self.source.status_message.connect(self.status_message)
```

Add to the public API section:

```python
    def set_image_store(self, store: ImageStore | None) -> None:
        """Bind (or with ``None``, detach) the vault images paste/drop go into."""
        self.source.image_store = store
```

- [ ] **Step 5: Wire it into `ui/tabbed_editor.py`**

Under `TYPE_CHECKING`, add `from core.images import ImageStore`. After `tab_context_menu_requested = Signal(QPoint)`, add:

```python
    #: A tab's status-bar message, re-emitted for the window (#101).
    tab_status_message = Signal(str)
```

In `__init__`, after `self._clock = clock`:

```python
        # The vault's image store, handed to every tab (None while locked).
        self._image_store: ImageStore | None = None
```

After `set_repository`:

```python
    def set_image_store(self, store: ImageStore | None) -> None:
        """Give every open tab — and every later one — the vault's image store."""
        self._image_store = store
        for index in range(self._tabs.count()):
            tab = self._tabs.widget(index)
            if isinstance(tab, NoteTab):
                tab.set_image_store(store)
```

In `_make_tab`, before `return tab`:

```python
        tab.set_image_store(self._image_store)
        tab.status_message.connect(self.tab_status_message)
```

- [ ] **Step 6: Run the new tests, then the existing editor/tab suites**

Run: `$PY -m pytest tests/test_note_source.py tests/test_note_tab.py tests/test_tabbed_editor.py tests/test_editor.py -v`
Expected: all PASS. (`test_note_tab` asserts `isinstance(tab.source, QPlainTextEdit)`, which a subclass satisfies.)

- [ ] **Step 7: Commit**

```bash
git add ui/note_source.py ui/note_tab.py ui/tabbed_editor.py tests/test_note_source.py
git commit -m "paste and drop images into the note source pane (refs #101)" -m "<trailers>"
```

---

### Task 6: `ui/vault_document.py` — rendering `mnimg:` images, cached

**Files:**
- Create: `ui/vault_document.py`
- Test: `tests/test_vault_document.py`

**Interfaces:**
- Consumes: `parse_url`, `SCHEME` (Task 2); `ImageStore.get` (Task 3); `natural_logical_width`, `png_bytes` (Task 4; tests use `png_bytes`).
- Produces:
  - `CACHE_BUDGET_BYTES = 128 * 1024 * 1024`
  - `ImageCache(budget_bytes: int = CACHE_BUDGET_BYTES)` with `.get(key)`, `.put(key, image)`, `.clear()`, `.total_bytes`, and `len()`
  - `VaultTextDocument(parent=None, *, device_pixel_ratio: Callable[[], float] = lambda: 1.0)` with:
    - `.set_store(store | None)` — also clears the cache
    - `.cache -> ImageCache`
    - `.natural_image(image_id) -> QImage | None`
    - `.placeholder() -> QImage`
    - the `loadResource` override

**Why the cache matters (verified 2026-09-11):** a `loadResource` override bypasses Qt's own resource cache, and Qt calls it roughly six times per render (once per layout and paint pass). The preview re-renders on every keystroke, so without this cache each keystroke would decode every image in the note several times.

- [ ] **Step 1: Write the failing tests** — `tests/test_vault_document.py`:

```python
"""Tests for ``ui.vault_document`` — rendering vault images in the preview (#101)."""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.images import ImageStore

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtGui import QColor, QImage, QTextDocument  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.image_ingest import png_bytes  # noqa: E402
from ui.vault_document import ImageCache, VaultTextDocument  # noqa: E402

IMAGE = QTextDocument.ResourceType.ImageResource


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


class CountingStore:
    """Wraps a store and counts ``get`` calls, to observe cache hits."""

    def __init__(self, store):
        self._store = store
        self.gets = 0

    def get(self, image_id):
        self.gets += 1
        return self._store.get(image_id)


def _image(width, height):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    return image


def _add(store, width=400, height=200):
    return store.add(png_bytes(_image(width, height)), "image/png", width, height)


def _doc(store, ratio=1.0):
    doc = VaultTextDocument(device_pixel_ratio=lambda: ratio)
    doc.set_store(store)
    return doc


def test_scales_to_the_requested_width(qapp, store):
    record = _add(store)
    image = _doc(store).loadResource(IMAGE, QUrl(f"mnimg:{record.id}?w=100"))
    assert (image.width(), image.height()) == (100, 50)
    assert image.devicePixelRatio() == 1.0


def test_renders_sharp_on_a_scaled_display(qapp, store):
    record = _add(store)
    image = _doc(store, ratio=1.5).loadResource(IMAGE, QUrl(f"mnimg:{record.id}?w=100"))
    # 150 device pixels, tagged 1.5, so Qt lays it out at 100 logical px.
    assert image.width() == 150
    assert image.devicePixelRatio() == 1.5
    assert image.deviceIndependentSize().width() == 100


def test_no_width_means_one_image_pixel_per_device_pixel(qapp, store):
    record = _add(store)
    image = _doc(store, ratio=2.0).loadResource(IMAGE, QUrl(f"mnimg:{record.id}"))
    assert image.width() == 400
    assert image.deviceIndependentSize().width() == 200


def test_repeated_renders_hit_the_cache(qapp, store):
    record = _add(store)
    counting = CountingStore(store)
    doc = _doc(counting)
    url = QUrl(f"mnimg:{record.id}?w=100")
    doc.loadResource(IMAGE, url)
    doc.loadResource(IMAGE, url)
    doc.loadResource(IMAGE, QUrl(f"mnimg:{record.id}?w=200"))  # new size, same decode
    assert counting.gets == 1


def test_unknown_or_unbound_images_show_the_placeholder(qapp, store):
    doc = _doc(store)
    assert doc.loadResource(IMAGE, QUrl("mnimg:999")) is doc.placeholder()
    unbound = VaultTextDocument()
    assert unbound.loadResource(IMAGE, QUrl("mnimg:1")) is unbound.placeholder()


def test_set_store_clears_decoded_images(qapp, store):
    record = _add(store)
    doc = _doc(store)
    doc.loadResource(IMAGE, QUrl(f"mnimg:{record.id}?w=100"))
    assert len(doc.cache) > 0
    doc.set_store(None)
    assert len(doc.cache) == 0
    assert doc.natural_image(record.id) is None


def test_set_markdown_renders_through_the_vault(qapp, store):
    record = _add(store)
    doc = _doc(store)
    doc.setMarkdown(f"![s](mnimg:{record.id}?w=100)")
    doc.size()  # force layout
    assert len(doc.cache) == 2  # the natural image and the 100 px rendering


def test_image_cache_evicts_least_recently_used_by_bytes(qapp):
    tiny = _image(10, 10)  # 400 bytes as RGB32
    cache = ImageCache(budget_bytes=900)
    cache.put("a", tiny)
    cache.put("b", tiny)
    cache.get("a")  # "a" is now most recent
    cache.put("c", tiny)  # over budget: evict the least recent, "b"
    assert cache.get("b") is None
    assert cache.get("a") is not None
    assert cache.get("c") is not None
    assert cache.total_bytes == 800
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/test_vault_document.py -v`
Expected: FAIL (`No module named 'ui.vault_document'`).

- [ ] **Step 3: Implement** — `ui/vault_document.py`:

```python
"""The preview's document: renders ``mnimg:`` images from the vault (#101).

``QTextDocument.setMarkdown`` resolves image URLs through :meth:`loadResource`.
:class:`VaultTextDocument` answers ``mnimg:<id>?w=<px>`` URLs from the vault's
:class:`~core.images.ImageStore`, scaled to ``w × device pixel ratio`` and tagged
with that ratio, so Qt lays the image out at ``w`` logical pixels and it stays
sharp on a scaled display.

The cache is a requirement, not polish. The preview re-renders on every
keystroke, and an overridden ``loadResource`` bypasses Qt's own resource cache —
Qt calls it several times per render (each layout and paint pass). So decoded
images, and each scaled rendering, are kept in an :class:`ImageCache` bounded by
total bytes. Decoded images are decrypted content: :meth:`set_store` clears the
cache, and ``MainWindow.lock_session`` detaches the store on lock.

Per CLAUDE.md's layering, the UI may import Qt freely; ``core/`` must never
import this module.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import TYPE_CHECKING, Callable, Hashable

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QColor, QImage, QPainter, QTextDocument

from core.image_refs import SCHEME, parse_url
from ui.image_ingest import natural_logical_width

if TYPE_CHECKING:
    from core.images import ImageStore

#: Upper bound on decoded image bytes held in memory for the preview.
CACHE_BUDGET_BYTES = 128 * 1024 * 1024

_IMAGE_RESOURCE = QTextDocument.ResourceType.ImageResource.value


class ImageCache:
    """Least-recently-used ``QImage`` cache, bounded by total decoded bytes."""

    def __init__(self, budget_bytes: int = CACHE_BUDGET_BYTES) -> None:
        self._budget = budget_bytes
        self._items: OrderedDict[Hashable, QImage] = OrderedDict()
        self.total_bytes = 0

    def __len__(self) -> int:
        return len(self._items)

    def get(self, key: Hashable) -> QImage | None:
        image = self._items.get(key)
        if image is not None:
            self._items.move_to_end(key)
        return image

    def put(self, key: Hashable, image: QImage) -> None:
        old = self._items.pop(key, None)
        if old is not None:
            self.total_bytes -= old.sizeInBytes()
        self._items[key] = image
        self.total_bytes += image.sizeInBytes()
        # Always keep the newest entry, even if it alone exceeds the budget.
        while self.total_bytes > self._budget and len(self._items) > 1:
            _, evicted = self._items.popitem(last=False)
            self.total_bytes -= evicted.sizeInBytes()

    def clear(self) -> None:
        self._items.clear()
        self.total_bytes = 0


class VaultTextDocument(QTextDocument):
    """A ``QTextDocument`` that resolves ``mnimg:`` image URLs from the vault."""

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        device_pixel_ratio: Callable[[], float] = lambda: 1.0,
    ) -> None:
        super().__init__(parent)
        self._store: ImageStore | None = None
        self._device_pixel_ratio = device_pixel_ratio
        self._cache = ImageCache()
        self._placeholder: QImage | None = None

    @property
    def cache(self) -> ImageCache:
        return self._cache

    def set_store(self, store: ImageStore | None) -> None:
        """Bind (or detach) the vault's images; always drops decoded images."""
        self._store = store
        self._cache.clear()

    def natural_image(self, image_id: int) -> QImage | None:
        """The full-resolution decoded image, or ``None`` if it can't be had."""
        key = ("natural", image_id)
        image = self._cache.get(key)
        if image is not None:
            return image
        if self._store is None:
            return None
        record = self._store.get(image_id)
        if record is None:
            return None
        image = QImage.fromData(record.data)
        if image.isNull():
            return None
        self._cache.put(key, image)
        return image

    def placeholder(self) -> QImage:
        """What a missing or unreadable image renders as — visible, never blank."""
        if self._placeholder is None:
            image = QImage(160, 90, QImage.Format.Format_ARGB32)
            image.fill(QColor(128, 128, 128, 40))
            painter = QPainter(image)
            painter.setPen(QColor(128, 128, 128))
            painter.drawRect(0, 0, 159, 89)
            painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, "missing image")
            painter.end()
            self._placeholder = image
        return self._placeholder

    def loadResource(self, type_: int, url: QUrl) -> object:
        kind = getattr(type_, "value", type_)
        if kind != _IMAGE_RESOURCE or url.scheme() != SCHEME:
            return super().loadResource(type_, url)

        parsed = parse_url(url.toString())
        if parsed is None:
            return self.placeholder()
        image_id, width = parsed
        natural = self.natural_image(image_id)
        if natural is None:
            return self.placeholder()

        ratio = self._device_pixel_ratio()
        if width is None:
            width = natural_logical_width(natural.width(), ratio)
        key = ("scaled", image_id, width, ratio)
        scaled = self._cache.get(key)
        if scaled is None:
            scaled = natural.scaledToWidth(
                max(1, round(width * ratio)), Qt.TransformationMode.SmoothTransformation
            )
            scaled.setDevicePixelRatio(ratio)
            self._cache.put(key, scaled)
        return scaled
```

- [ ] **Step 4: Run to verify they pass**

Run: `$PY -m pytest tests/test_vault_document.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add ui/vault_document.py tests/test_vault_document.py
git commit -m "render mnimg images in the preview from the vault, cached (refs #101)" -m "<trailers>"
```

---

### Task 7: `ui/preview_images.py` — finding a rendered image, editing its width

**Files:**
- Create: `ui/preview_images.py`
- Test: `tests/test_preview_images.py`

**Interfaces:**
- Consumes: `parse_url`, `image_url`, `ImageRef`, `find_refs` (Task 2); `VaultTextDocument` (Task 6, in tests).
- Produces:
  - `@dataclass(frozen=True) PreviewImage(image_id: int, width: int | None, ordinal: int, position: int, url: str)`
  - `rendered_images(document: QTextDocument) -> list[PreviewImage]`
  - `image_rect(view: QTextEdit, image: PreviewImage) -> QRect` — viewport coordinates
  - `image_at(view: QTextEdit, point: QPoint) -> PreviewImage | None` — `point` in viewport coordinates
  - `utf16_offset(text: str, index: int) -> int`
  - `apply_image_width(source: QPlainTextEdit, ref: ImageRef, width: int | None) -> None`

**Gotcha this task exists to handle:** `ImageRef` spans are Python string indices, but `QTextDocument` positions count UTF-16 code units. One emoji before an image shifts every later position by one. `apply_image_width` converts via `utf16_offset`.

- [ ] **Step 1: Write the failing tests** — `tests/test_preview_images.py`:

```python
"""Tests for ``ui.preview_images`` — rendered-image lookup and width edits (#101)."""

import os

import pytest
from sqlcipher3 import dbapi2 as sqlcipher

from core import schema
from core.image_refs import find_refs
from core.images import ImageStore

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtGui import QColor, QImage, QTextCursor  # noqa: E402
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QTextEdit  # noqa: E402

from ui.image_ingest import png_bytes  # noqa: E402
from ui.preview_images import (  # noqa: E402
    apply_image_width,
    image_at,
    image_rect,
    rendered_images,
    utf16_offset,
)
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


def _add(store, color="red"):
    image = QImage(400, 200, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return store.add(png_bytes(image), "image/png", 400, 200)


def _preview(qapp, store, markdown):
    view = QTextEdit()
    doc = VaultTextDocument(view, device_pixel_ratio=lambda: 1.0)
    doc.set_store(store)
    view.setDocument(doc)
    view.setReadOnly(True)
    view.resize(700, 600)
    view.show()
    doc.setMarkdown(markdown)
    qapp.processEvents()
    return view


def test_rendered_images_lists_vault_images_in_order(qapp, store):
    a, b = _add(store, "red"), _add(store, "blue")
    md = (
        f"![a](mnimg:{a.id}?w=100)\n\n![web](http://example.com/x.png)\n\n"
        f"```\n![c](mnimg:{a.id})\n```\n\n![b](mnimg:{b.id})"
    )
    images = rendered_images(_preview(qapp, store, md).document())
    assert [(i.image_id, i.width, i.ordinal) for i in images] == [(a.id, 100, 0), (b.id, None, 1)]


def test_rendered_images_counts_adjacent_duplicates_separately(qapp, store):
    a = _add(store)
    md = f"![a](mnimg:{a.id}?w=50)![a](mnimg:{a.id}?w=50)"
    images = rendered_images(_preview(qapp, store, md).document())
    assert [i.ordinal for i in images] == [0, 1]
    assert images[0].position != images[1].position


def test_image_rect_matches_the_rendered_size(qapp, store):
    a = _add(store)
    view = _preview(qapp, store, f"![a](mnimg:{a.id}?w=100)")
    (image,) = rendered_images(view.document())
    rect = image_rect(view, image)
    assert (rect.width(), rect.height()) == (100, 50)


def test_image_at_finds_the_image_under_a_point(qapp, store):
    a, b = _add(store, "red"), _add(store, "blue")
    view = _preview(qapp, store, f"![a](mnimg:{a.id}?w=100)\n\n![b](mnimg:{b.id}?w=100)")
    first, second = rendered_images(view.document())

    assert image_at(view, image_rect(view, second).center()).ordinal == 1
    assert image_at(view, image_rect(view, first).center()).ordinal == 0
    viewport = view.viewport()
    assert image_at(view, QPoint(viewport.width() - 2, viewport.height() - 2)) is None


def test_utf16_offset_counts_surrogate_pairs():
    assert utf16_offset("a😀b", 0) == 0
    assert utf16_offset("a😀b", 2) == 3
    assert utf16_offset("abc", 3) == 3


def _source(text):
    source = QPlainTextEdit()
    source.setPlainText(text)
    return source


def test_apply_image_width_replaces_only_the_url(qapp):
    source = _source("x ![s](mnimg:1?w=600) y")
    (ref,) = find_refs(source.toPlainText())
    apply_image_width(source, ref, 300)
    assert source.toPlainText() == "x ![s](mnimg:1?w=300) y"


def test_apply_image_width_none_resets_to_natural(qapp):
    source = _source("![s](mnimg:1?w=600)")
    (ref,) = find_refs(source.toPlainText())
    apply_image_width(source, ref, None)
    assert source.toPlainText() == "![s](mnimg:1)"


def test_apply_image_width_is_one_undo_step(qapp):
    source = _source("![s](mnimg:1?w=600)")
    (ref,) = find_refs(source.toPlainText())
    apply_image_width(source, ref, 300)
    source.undo()
    assert source.toPlainText() == "![s](mnimg:1?w=600)"


def test_apply_image_width_after_an_emoji(qapp):
    source = _source("😀 ![s](mnimg:1?w=600) end")
    (ref,) = find_refs(source.toPlainText())
    apply_image_width(source, ref, 300)
    assert source.toPlainText() == "😀 ![s](mnimg:1?w=300) end"


def test_apply_image_width_leaves_the_caret_alone(qapp):
    source = _source("![s](mnimg:1?w=600) tail")
    cursor = source.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    source.setTextCursor(cursor)
    (ref,) = find_refs(source.toPlainText())
    apply_image_width(source, ref, 30)
    assert source.textCursor().position() == len(source.toPlainText())
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/test_preview_images.py -v`
Expected: FAIL (`No module named 'ui.preview_images'`).

- [ ] **Step 3: Implement** — `ui/preview_images.py`:

```python
"""Images as the preview shows them, and editing their width in the source (#101).

The preview is a rendering of the active tab's Markdown. To act on an image the
user clicked there — copy it, reset its size, and (slice 2, #102) drag it — this
module maps the click to a :class:`PreviewImage`. Callers then find the matching
text with :func:`core.image_refs.resolve_ref`, and :func:`apply_image_width`
rewrites just that URL as one undoable edit.

Two coordinate systems meet here, which is why this lives in one place:

* :func:`image_rect` and :func:`image_at` work in the preview's **viewport**
  coordinates — what ``customContextMenuRequested`` reports for a scroll area,
  and what ``QTextEdit.cursorRect`` returns.
* :class:`~core.image_refs.ImageRef` spans are **Python string indices**, while
  ``QTextDocument`` positions count UTF-16 code units; :func:`utf16_offset`
  converts, so an emoji earlier in the note can't shift an edit.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QRect, QUrl
from PySide6.QtGui import QImage, QTextCursor, QTextDocument
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit

from core.image_refs import ImageRef, image_url, parse_url


@dataclass(frozen=True)
class PreviewImage:
    """One ``mnimg:`` image in the rendered preview.

    ``ordinal`` is its index among the rendered vault images — the value
    :func:`core.image_refs.resolve_ref` expects. ``position`` is the document
    position of the image's placeholder character.
    """

    image_id: int
    width: int | None
    ordinal: int
    position: int
    url: str


def rendered_images(document: QTextDocument) -> list[PreviewImage]:
    """Every ``mnimg:`` image in ``document``, in order."""
    images: list[PreviewImage] = []
    block = document.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            fragment = it.fragment()
            if fragment.isValid() and fragment.charFormat().isImageFormat():
                url = fragment.charFormat().toImageFormat().name()
                parsed = parse_url(url)
                if parsed is not None:
                    # Identical adjacent images share one fragment; each
                    # character in it is a separate image.
                    for offset in range(fragment.length()):
                        images.append(
                            PreviewImage(
                                image_id=parsed[0],
                                width=parsed[1],
                                ordinal=len(images),
                                position=fragment.position() + offset,
                                url=url,
                            )
                        )
            it += 1
        block = block.next()
    return images


def image_rect(view: QTextEdit, image: PreviewImage) -> QRect:
    """Where ``image`` is drawn in ``view``'s viewport (empty if unknown)."""
    cursor = QTextCursor(view.document())
    cursor.setPosition(image.position)
    caret = view.cursorRect(cursor)
    rendered = view.document().resource(
        QTextDocument.ResourceType.ImageResource, QUrl(image.url)
    )
    if not isinstance(rendered, QImage) or rendered.isNull():
        return QRect()
    return QRect(caret.topLeft(), rendered.deviceIndependentSize().toSize())


def image_at(view: QTextEdit, point: QPoint) -> PreviewImage | None:
    """The rendered image under ``point`` (viewport coordinates), if any."""
    for image in rendered_images(view.document()):
        if image_rect(view, image).contains(point):
            return image
    return None


def utf16_offset(text: str, index: int) -> int:
    """Python string index into ``text`` -> ``QTextDocument`` position."""
    return len(text[:index].encode("utf-16-le")) // 2


def apply_image_width(source: QPlainTextEdit, ref: ImageRef, width: int | None) -> None:
    """Set ``ref``'s display width in ``source`` (``None``: natural size).

    ``ref`` must come from ``source``'s current text. Replaces only the URL span,
    through a separate cursor inside one edit block: Ctrl+Z undoes it in one
    step, the user's caret stays where it was, and auto-save picks it up like
    any other edit. (``setPlainText`` would wipe the undo stack.)
    """
    text = source.toPlainText()
    cursor = QTextCursor(source.document())
    cursor.setPosition(utf16_offset(text, ref.url_start))
    cursor.setPosition(utf16_offset(text, ref.url_end), QTextCursor.MoveMode.KeepAnchor)
    cursor.beginEditBlock()
    try:
        cursor.insertText(image_url(ref.image_id, width))
    finally:
        cursor.endEditBlock()
```

- [ ] **Step 4: Run to verify they pass**

Run: `$PY -m pytest tests/test_preview_images.py -v`
Expected: all PASS. If `test_apply_image_width_leaves_the_caret_alone` fails because the caret sat *exactly* at the edit's end, that means Qt moved it with the insertion. Fix it by saving `source.textCursor().position()` before the edit and restoring it with `setTextCursor` afterwards, adjusted by the length change — don't weaken the test.

- [ ] **Step 5: Commit**

```bash
git add ui/preview_images.py tests/test_preview_images.py
git commit -m "locate preview images and edit their width as one undo step (refs #101)" -m "<trailers>"
```

---

### Task 8: `MainWindow` — preview document, Insert menu, preview menu, lock

**Files:**
- Modify: `ui/main_window.py`:
  - imports
  - `__init__` (the preview block at ~193; menus at ~268; status-bar wiring at ~320)
  - new methods after `_active_markdown`
  - `lock_session` (~581)
- Test: `tests/test_image_window.py`

**Interfaces:**
- Consumes:
  - `VaultTextDocument` (Task 6)
  - `image_at`, `apply_image_width`, `PreviewImage` (Task 7)
  - `resolve_ref` (Task 2)
  - `ingest_file`, `IngestError` (Task 4)
  - `extension_for`, `ImageStore` (Task 3)
  - `TabbedEditor.set_image_store` and `tab_status_message` (Task 5)
- Produces, on `MainWindow`:
  - Attributes: `preview_document`, `image_store`, `insert_image_action`
  - `bind_images(store)`
  - `insert_image_from_file()`
  - `insert_image_files(paths: list[str]) -> bool`
  - `build_preview_context_menu(pos: QPoint) -> QMenu`
  - `copy_preview_image(image) -> bool`
  - `save_preview_image(image) -> bool`
  - `reset_preview_image_size(image) -> bool`
  - Test seams: `_choose_image_files() -> list[str]` and `_choose_save_path(default_name: str) -> str`

- [ ] **Step 1: Write the failing tests** — `tests/test_image_window.py`:

```python
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
    window.preview_document.natural_image(record.id)
    assert len(window.preview_document.cache) > 0

    window.lock_session()

    assert len(window.preview_document.cache) == 0
    assert window.image_store is None
    assert window.preview_document.natural_image(record.id) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/test_image_window.py -v`
Expected: FAIL (`AttributeError: 'MainWindow' object has no attribute 'bind_images'`).

- [ ] **Step 3: Implement — imports.** In `ui/main_window.py`:

Add `from pathlib import Path` after `from dataclasses import replace`.

Change the QtGui import to:

```python
from PySide6.QtGui import QAction, QGuiApplication, QImageReader, QKeySequence, QShortcut
```

Add `QFileDialog` to the `PySide6.QtWidgets` import list (keep it alphabetical).

After `from core.autosave import DEFAULT_DEBOUNCE_SECONDS`, add:

```python
from core.image_refs import resolve_ref
from core.images import extension_for
```

After `from ui.icons import …`, add:

```python
from ui.image_ingest import IngestError, ingest_file
```

After `from ui.import_wizard import ImportWizard`, add:

```python
from ui.preview_images import PreviewImage, apply_image_width, image_at
```

After `from ui.tools_menu import …`, add:

```python
from ui.vault_document import VaultTextDocument
```

Under `TYPE_CHECKING`, add `from core.images import ImageStore`.

Near the module's other constants, add:

```python
# How long an image-related status message stays up (ms); errors match run_tool's.
_IMAGE_STATUS_MS = 8000
```

- [ ] **Step 4: Implement — `__init__`.** Replace the preview block:

```python
        # Shared Markdown preview (in a dock); always renders the active tab.
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumWidth(_EDITOR_MIN_WIDTH)
```

with:

```python
        # Shared Markdown preview (in a dock); always renders the active tab.
        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumWidth(_EDITOR_MIN_WIDTH)
        # Resolves `mnimg:` images from the vault, sharp at the display's scale
        # (#101). No store until a vault is bound: images show a placeholder.
        self.image_store: ImageStore | None = None
        self.preview_document = VaultTextDocument(
            self.preview, device_pixel_ratio=self.preview.devicePixelRatioF
        )
        self.preview.setDocument(self.preview_document)
        self.preview.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.preview.customContextMenuRequested.connect(self._show_preview_context_menu)
```

In the menus section, right after the File menu's `self.settings_action.triggered.connect(self.open_settings)` line, add:

```python
        insert_menu = self.menuBar().addMenu("&Insert")
        self.insert_image_action = insert_menu.addAction("&Image…")
        self.insert_image_action.triggered.connect(self.insert_image_from_file)
```

In the status-bar section, after the `tab_context_menu_requested.connect(...)` statement, add:

```python
        self.tabbed_editor.tab_status_message.connect(
            lambda message: self.statusBar().showMessage(message, _IMAGE_STATUS_MS)
        )
```

- [ ] **Step 5: Implement — methods.** Add this new section after `_active_markdown`:

```python
    # -------------------------------------------------------------------------
    # Images (#101)
    # -------------------------------------------------------------------------

    def bind_images(self, store: ImageStore) -> None:
        """Bind the vault's images: tabs paste into it, the preview renders from it."""
        self.image_store = store
        self.tabbed_editor.set_image_store(store)
        self.preview_document.set_store(store)
        self._render_preview()

    def insert_image_from_file(self) -> None:
        """**Insert → Image…**: pick image files and insert them at the caret."""
        if self.tabbed_editor.active_tab is None:
            self.statusBar().showMessage("Open a note first", _IMAGE_STATUS_MS)
            return
        paths = self._choose_image_files()
        if paths:
            self.insert_image_files(paths)

    def insert_image_files(self, paths: list[str]) -> bool:
        """Store each file in ``paths`` and insert its Markdown at the caret."""
        tab = self.tabbed_editor.active_tab
        if tab is None or self.image_store is None:
            self.statusBar().showMessage("Open a note first", _IMAGE_STATUS_MS)
            return False
        ratio = tab.source.devicePixelRatioF()
        try:
            snippets = [
                ingest_file(self.image_store, path, device_pixel_ratio=ratio)
                for path in paths
            ]
        except IngestError as error:
            self.statusBar().showMessage(str(error), _IMAGE_STATUS_MS)
            return False
        tab.source.insertPlainText("\n\n".join(snippets))
        return True

    def _choose_image_files(self) -> list[str]:
        """The file picker — the seam tests replace to avoid a modal dialog."""
        patterns = " ".join(
            f"*.{bytes(f).decode()}" for f in QImageReader.supportedImageFormats()
        )
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Insert image", "", f"Images ({patterns})"
        )
        return paths

    def build_preview_context_menu(self, pos: QPoint) -> QMenu:
        """The preview's right-click menu; image actions on top when over an image.

        ``pos`` is in viewport coordinates, as ``customContextMenuRequested``
        reports it for a scroll area. Separate from the show method so tests can
        inspect the menu without a modal loop.
        """
        menu = self.preview.createStandardContextMenu()
        image = image_at(self.preview, pos)
        if image is None:
            return menu

        copy = QAction("Copy image", menu)
        copy.triggered.connect(lambda: self.copy_preview_image(image))
        save = QAction("Save image as…", menu)
        save.triggered.connect(lambda: self.save_preview_image(image))
        reset = QAction("Reset to original size", menu)
        reset.setEnabled(image.width is not None)
        reset.triggered.connect(lambda: self.reset_preview_image_size(image))

        first = menu.actions()[0] if menu.actions() else None
        menu.insertActions(first, [copy, save, reset])
        menu.insertSeparator(first)
        return menu

    def _show_preview_context_menu(self, pos: QPoint) -> None:
        self.build_preview_context_menu(pos).exec(self.preview.viewport().mapToGlobal(pos))

    def copy_preview_image(self, image: PreviewImage) -> bool:
        """Put the full-resolution original of ``image`` on the clipboard."""
        natural = self.preview_document.natural_image(image.image_id)
        clipboard = QGuiApplication.clipboard()
        if natural is None or clipboard is None:
            return False
        clipboard.setImage(natural)
        self.statusBar().showMessage("Image copied", _IMAGE_STATUS_MS)
        return True

    def save_preview_image(self, image: PreviewImage) -> bool:
        """Write ``image``'s stored bytes, unchanged, to a file the user picks."""
        record = self.image_store.get(image.image_id) if self.image_store else None
        if record is None:
            return False
        path = self._choose_save_path(f"image-{record.id}.{extension_for(record.mime)}")
        if not path:
            return False
        try:
            Path(path).write_bytes(record.data)
        except OSError as error:
            self.statusBar().showMessage(f"Couldn't save the image: {error.strerror}", _IMAGE_STATUS_MS)
            return False
        self.statusBar().showMessage(f"Saved {Path(path).name}", _IMAGE_STATUS_MS)
        return True

    def _choose_save_path(self, default_name: str) -> str:
        """The save dialog — the seam tests replace to avoid a modal dialog."""
        path, _ = QFileDialog.getSaveFileName(self, "Save image as", default_name)
        return path

    def reset_preview_image_size(self, image: PreviewImage) -> bool:
        """Drop ``image``'s width so it renders at natural size — one undo step."""
        tab = self.tabbed_editor.active_tab
        if tab is None:
            return False
        ref = resolve_ref(tab.markdown(), image.ordinal, image.image_id, image.width)
        if ref is None:
            self.statusBar().showMessage(
                "Couldn't tell which image that is — edit its width in the note text",
                _IMAGE_STATUS_MS,
            )
            return False
        apply_image_width(tab.source, ref, None)
        return True
```

- [ ] **Step 6: Implement — `lock_session`.** After `self.tabbed_editor.clear_all()`, add:

```python
        # Decoded images are decrypted content too: drop them and the store.
        self.image_store = None
        self.tabbed_editor.set_image_store(None)
        self.preview_document.set_store(None)
```

- [ ] **Step 7: Run the new tests, then every window-level suite**

Run: `$PY -m pytest tests/test_image_window.py tests/test_main_window_layout.py tests/test_theme_coverage.py tests/test_idle_lock.py tests/test_lock_on_minimize.py tests/test_tools_ui.py -v`
Expected: all PASS. `test_theme_coverage` must stay green: this task adds no new widget class, and the `QMenu` the preview uses is already styled.

- [ ] **Step 8: Commit**

```bash
git add ui/main_window.py tests/test_image_window.py
git commit -m "add Insert > Image and the preview image menu; drop images on lock (refs #101)" -m "<trailers>"
```

---

### Task 9: `app._bind_vault` — bind the store, sweep orphans first

**Files:**
- Modify: `app.py` (imports, `_bind_vault` at ~135, a new `_sweep_orphan_images`)
- Test: `tests/test_image_binding.py`

**Interfaces:**
- Consumes: `ImageStore.sweep_orphans` (Task 3); `MainWindow.bind_images` (Task 8).
- Produces: `app._sweep_orphan_images(images: ImageStore) -> None`. `_bind_vault` keeps its signature and return value.

- [ ] **Step 1: Write the failing tests** — `tests/test_image_binding.py`:

```python
"""The app binds vault images at unlock and sweeps orphans first (#101)."""

import os

import pytest

from core.crypto import KdfParams
from core.images import ImageStore
from core.repository import Repository
from core.vault import Vault

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

import app as app_module  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402

FAST = KdfParams(time_cost=1, memory_cost=8, parallelism=1)
PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture
def vault(tmp_path):
    v = Vault.create(tmp_path / "notes.vault", PASSWORD, FAST)
    try:
        yield v
    finally:
        v.lock()


def test_bind_vault_sweeps_orphans_and_keeps_referenced_images(qapp, vault):
    store = ImageStore(vault.connection)
    kept = store.add(b"kept", "image/png", 1, 1)
    orphan = store.add(b"orphan", "image/png", 1, 1)
    Repository(vault.connection).create_note(title="n", body=f"![s](mnimg:{kept.id})")

    app_module._bind_vault(MainWindow(), vault)

    assert store.get(orphan.id) is None
    assert store.get(kept.id) is not None


def test_bind_vault_binds_images_to_the_window(qapp, vault):
    window = MainWindow()
    app_module._bind_vault(window, vault)
    assert window.image_store is not None
    assert window.preview_document._store is window.image_store


def test_a_failed_sweep_never_blocks_unlock(qapp, vault, monkeypatch):
    def explode(self):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(ImageStore, "sweep_orphans", explode)
    window = MainWindow()
    repository = app_module._bind_vault(window, vault)
    assert window.repository is repository
    assert window.image_store is not None


def test_relock_rebinds_images(qapp, tmp_path):
    path = tmp_path / "notes.vault"
    vault = Vault.create(path, PASSWORD, FAST)
    window = MainWindow()
    app_module._bind_vault(window, vault)
    vault.lock()
    window.lock_session()
    assert window.image_store is None

    reopened = Vault(path)
    reopened.unlock(PASSWORD)
    try:
        app_module._bind_vault(window, reopened)
        assert window.image_store is not None
    finally:
        reopened.lock()
```

(`Vault(path)` followed by `.unlock(PASSWORD)` is how `tests/test_idle_lock.py` reopens a locked vault.)

- [ ] **Step 2: Run to verify they fail**

Run: `$PY -m pytest tests/test_image_binding.py -v`
Expected: FAIL — the orphan survives, and `window.image_store is None`.

- [ ] **Step 3: Implement** — in `app.py`:

Add `import logging` with the stdlib imports. Add `from core.images import ImageStore` next to `from core.repository import Repository`. After the imports, add:

```python
_log = logging.getLogger(__name__)
```

Replace `_bind_vault` with:

```python
def _bind_vault(window: MainWindow, vault: Vault) -> Repository:
    """Bind a fresh repository and image store over ``vault`` and populate the panes.

    Builds a :class:`~core.repository.Repository` and an
    :class:`~core.images.ImageStore` on the vault's keyed connection, sweeps
    unreferenced images, attaches debounced auto-save (which also populates the
    notebook tree), binds images, and refreshes the note list. Used at launch and
    again after a re-unlock.
    """
    repository = Repository(vault.connection)
    images = ImageStore(vault.connection)
    # The sweep must run here, before any tab can open, and nowhere else. At
    # launch there are no tabs; on re-unlock lock_session has already flushed and
    # closed them all. So every reference to an image is on disk and no undo
    # stack can bring back text naming a swept one. Mid-session, a sweep could
    # delete a just-pasted image whose note hasn't auto-saved yet.
    _sweep_orphan_images(images)
    window.bind_autosave(repository)
    window.bind_images(images)
    window.refresh_notes()
    return repository


def _sweep_orphan_images(images: ImageStore) -> None:
    """Reclaim images no note mentions; a failure is logged, never raised."""
    try:
        images.sweep_orphans()
    except Exception:  # an unlock must never fail over housekeeping
        _log.exception("image sweep failed; unreferenced images kept")
```

- [ ] **Step 4: Run the new tests and the existing app-binding suites**

Run: `$PY -m pytest tests/test_image_binding.py tests/test_idle_lock.py tests/test_lock_on_minimize.py tests/test_app_launch.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_image_binding.py
git commit -m "bind vault images at unlock and sweep unreferenced ones first (refs #101)" -m "<trailers>"
```

---

### Task 10: Full verification, packaged exe, docs, PR

**Files:**
- Modify: `LESSONS.md` (append), `ROADMAP.md` (check the #101 box)

- [ ] **Step 1: Full suite and lint**

Run: `$PY -m pytest` then `$PY -m ruff check .`
Expected: every test passes (the count is the previous total plus this slice's new tests) and ruff reports nothing. Fix anything red before continuing.

- [ ] **Step 2: Build the exe into a separate folder** (the user's daily `dist/my_notes.exe` stays untouched):

```bash
cd /c/Users/Nate/anaconda3/envs/playground/my_notes
$PY -m PyInstaller my_notes.spec --noconfirm --distpath build/dist-101
```

Expected: `build/dist-101/my_notes.exe` exists.

- [ ] **Step 3: Audit the build**

```bash
grep -o "'[^']*envs.[A-Za-z_]*.[^']*'" build/my_notes/Analysis-00.toc | grep -v playground
grep -ohE "q(jpeg|webp|gif)\.dll" build/my_notes/*.toc | sort -u
```

Expected:
- The first command prints **nothing** (no Office_MCP leak).
- The second prints `qgif.dll`, `qjpeg.dll` and `qwebp.dll`. If any is missing, the exe can't decode that format: stop and report it rather than shipping.

- [ ] **Step 4: Headless launch smoke test** (PowerShell; stops only the PID it started):

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
$env:MY_NOTES_VAULT = "$env:TEMP\my_notes_smoke_101.vault"
$p = Start-Process -FilePath ".\build\dist-101\my_notes.exe" -PassThru `
      -RedirectStandardOutput "$env:TEMP\mn101.out" -RedirectStandardError "$env:TEMP\mn101.err"
try { Wait-Process -Id $p.Id -Timeout 10 -ErrorAction Stop } catch {}
Get-Content "$env:TEMP\mn101.out", "$env:TEMP\mn101.err"
if (-not $p.HasExited) { Stop-Process -Id $p.Id -Confirm:$false }
```

Expected: both files are empty. A still-running process is not proof on its own; the output is.

- [ ] **Step 5: Append to `LESSONS.md`**

```markdown
- 2026-09-11 — **A `QTextDocument.loadResource` override bypasses Qt's resource cache**, and Qt calls it ~6× per render (each layout and paint pass). Anything expensive in it (decoding an image) needs its own cache, or a document that re-renders per keystroke stalls. See `ui/vault_document.py`. (#101)
- 2026-09-11 — **Python string indices are not `QTextDocument` positions.** Qt counts UTF-16 code units, so one emoji shifts every later position by one. Convert with `ui.preview_images.utf16_offset` before placing a `QTextCursor` from a Python-side regex match. (#101)
- 2026-09-11 — **Office apps put text *and* a picture of it on the clipboard.** An image-paste handler that prefers `hasImage()` turns every pasted Excel range into a screenshot; let `hasText()` win (`ui.image_ingest.wants_image_paste`). (#101)
```

- [ ] **Step 6: Check the ROADMAP box** — in `ROADMAP.md` under M10, change the #101 line's `- [ ]` to `- [x]`, and append this after the text, before `(#101)`:

```
_(done: `images` table (schema v3) + `core/images.py` / `core/image_refs.py`; paste, drop and Insert → Image via `ui/note_source.py` / `ui/image_ingest.py`; preview rendering via `ui/vault_document.py`; copy / save / reset from the preview; orphan sweep in `app._bind_vault`.)_
```

- [ ] **Step 7: Commit, push, open the PR, move the card**

```bash
git add LESSONS.md ROADMAP.md
git commit -m "docs: lessons and roadmap for images slice 1 (refs #101)" -m "<trailers>"
git push
gh pr create --title "Paste, drop, and insert screenshots into notes (images slice 1)" --body "<body below>"
```

PR body:

```markdown
## What
Images in notes, stored inside the encrypted vault (slice 1 of 2).

- Ctrl+V a screenshot, drag & drop image files, or **Insert → Image…**
- The preview renders images sharply at the display's scale, with a byte-bounded cache
- Right-click an image in the preview: **Copy image** / **Save image as…** / **Reset to original size**
- Images no note mentions are swept at unlock, before any tab opens

## Why
A daily-use request: paste screenshots straight into notes. Design: `docs/superpowers/specs/2026-09-11-screenshot-images-design.md`.

## Notable decisions
- The `mnimg:` scheme, not `vault:`, so the search index isn't polluted.
- Text wins over an image on paste (Office puts both on the clipboard).
- The sweep runs only in `_bind_vault`: a mid-session sweep could delete a just-pasted image before auto-save writes its reference.
- The width lives in the Markdown and is edited as one undoable span edit.

## Test plan
- [x] `pytest` and `ruff check .` green
- [x] Packaged exe built to `build/dist-101`; no foreign-env DLLs; qjpeg/qwebp/qgif bundled; headless launch prints nothing
- [ ] **Manual, in the exe (owner):**
  - [ ] Win+Shift+S → Ctrl+V pastes an image
  - [ ] A browser's "Copy image" → Ctrl+V pastes an image
  - [ ] An Excel range → Ctrl+V pastes text
  - [ ] Drop a JPEG; Insert → Image a WebP
  - [ ] Copy image, Save image as…, and Reset to original size all work
  - [ ] Lock and unlock: every image is still there

Closes #101

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01UpJoMa4T4ra499LUyQnAk7
```

Then move the #101 card to **In Review**:

```bash
ID=$(gh project item-list 6 --owner nateRaintech --limit 100000 --format json --jq '.items[] | select(.content.number==101) | .id')
gh project item-edit --project-id PVT_kwHODNNZlM4BYaSW --id "$ID" --field-id PVTSSF_lAHODNNZlM4BYaSWzhTgWZw --single-select-option-id 41e378cb
```

**Do not merge.** Merging waits for green CI and the owner's manual exe checks. One of those checks — what a browser's "Copy image" puts on the clipboard — may change the paste rule; if it pastes a URL instead of the image, file that as a follow-up rather than guessing.
