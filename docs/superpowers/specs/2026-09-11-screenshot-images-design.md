# Screenshots & Images in Notes — Design

**Date:** 2026-09-11
**Issues:** #101 (slice 1 — store, insert, render, copy out), #102 (slice 2 — drag-handle resize)
**Milestone:** M10

## Motivation

A request from daily use: paste a screenshot straight from the clipboard into a note,
and resize it there. Today a note is Markdown text only — the vault has never stored
binary data, and the preview only renders what `QTextDocument.setMarkdown()` can
resolve on its own, which does not include anything inside the encrypted vault.

## Decisions

| Question | Decision |
|---|---|
| What does "resize" change? | **Display size only.** Stored bytes are never re-encoded; every resize is reversible to full sharpness. |
| How is resizing done? | **Drag corner handles on the image in the preview** (slice 2). |
| Ways in | Ctrl+V, drag & drop an image file, **Insert → Image…** |
| Ways out | Right-click an image → **Copy image** / **Save image as…** |
| Lifecycle of unreferenced images | **Swept on unlock**, never mid-session (see *Orphan sweep*). |
| Where images live | **Inside the SQLCipher vault** as blobs. Writing them to disk in the clear would break the product's core guarantee. |

## Approach

Images are rows in a new `images` table, and a note refers to one with an ordinary
Markdown image whose URL uses a private scheme: `![screenshot](mnimg:7?w=600)`. The
preview's `QTextDocument` is subclassed so that `loadResource()` resolves `mnimg:` URLs
from the vault and returns an image already scaled to the requested width.

The display width travels **inside the Markdown**, so there is one source of truth: a
copied line keeps its size, undo covers resizes, and auto-save needs no new save path.

Two alternatives were rejected:

- **Base64 data URIs in the note body.** No schema change, but a 400 KB screenshot
  becomes ~550 KB of base64 in `body`: the source pane fills with noise, auto-save
  rewrites megabytes on every debounce, and `notes_fts` indexes the whole blob.
- **A `QWebEngineView` preview.** Real HTML sizing and JS handles, but it contradicts
  the locked "no heavy WebEngine" decision, adds well over 100 MB to the exe, and the
  spike below shows it is not needed.

### Feasibility spike (2026-08-31, PySide6 in the `playground` env)

Confirmed headless against the real Qt:

1. `setMarkdown()` calls `loadResource(ImageResource, "mnimg:…")` for a custom scheme,
   and lays the image out at the returned `QImage`'s size.
2. An image's on-screen rect is exactly `cursorRect(pos)` → `cursorRect(pos + 1)`
   (a `?w=150` image on a 400×200 source measured 150×75).
3. `documentLayout().hitTest(point, ExactHit)` inside the image returns its document
   position — so a click can be mapped to the image under it.
4. A returned image tagged with `setDevicePixelRatio(1.5)` and scaled to 450 px is laid
   out at 300 logical px — Qt honours device pixel ratio, so HiDPI rendering is sharp.

## Storage — migration 3 and `core/images.py`

`SCHEMA_VERSION` → 3. Forward-only, idempotent:

```sql
CREATE TABLE IF NOT EXISTS images (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256     TEXT    NOT NULL UNIQUE,
    mime       TEXT    NOT NULL,
    width      INTEGER NOT NULL,   -- natural size in pixels, cached at insert
    height     INTEGER NOT NULL,
    byte_size  INTEGER NOT NULL,
    data       BLOB    NOT NULL,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
```

Images are **vault-global, not owned by a note** — there is no `note_id` column. A note
refers to an image only through the URL text in its `body`. This is what lets the same
screenshot pasted into five notes cost one blob (`sha256` is UNIQUE), and it is why
unreferenced images are reclaimed by a sweep rather than a cascade.

`id` is `AUTOINCREMENT`, so an id is **never reused** after a sweep. Without it SQLite
hands the highest freed rowid to the next insert, and a stale `mnimg:<id>` (a line cut
before a lock and pasted back after, say) would silently show a different, unrelated
screenshot instead of the "missing image" placeholder.

`core/images.py` is a new module rather than more methods on the 506-line `Repository`.
`ImageStore(connection)`:

- `add(data, mime, width, height) -> ImageRecord` — hashes `data`; if the hash exists,
  returns the existing record without writing. `width`/`height` come from the caller,
  because only the UI layer has a `QImage` to measure — `core/` stays Qt-free.
- `get(image_id) -> ImageRecord | None`
- `sweep_orphans() -> SweepResult(count, freed_bytes)` — see *Orphan sweep*.

## Reference format — `core/image_refs.py`

Pure text, no Qt: `![alt](mnimg:<id>)` or `![alt](mnimg:<id>?w=<logical px>)`.

The scheme is **`mnimg:`, not `vault:`**. FTS5 tokenises the URL into words, and
"vault" is an everyday search term for this user (Veeam, Azure Key Vault); a `vault:`
scheme would make every note containing an image match it. `mnimg` collides with
nothing. The tokens `w` and the numbers are still indexed — accepted as minor noise.
Stripping references from the index would need triggers that call a Python function,
which is fragile and not worth it.

Functions:

- `find_refs(markdown) -> list[ImageRef]` — every `mnimg:` image **Qt would render**, in
  document order, each with `image_id`, `width | None`, and the character span of its
  URL. Skips fenced code blocks and inline code, which the preview shows as literal
  text. Indented (four-space) code blocks are *not* detected: telling them from nested
  list items needs a full Markdown parser, and the verification below covers the gap.
- `image_url(image_id, width | None) -> str` — the replacement for a ref's URL span;
  `None` drops `?w=`. The UI applies it as a surgical edit over that span.
- `resolve_ref(markdown, ordinal, image_id, width) -> ImageRef | None` — the
  verified lookup described below.
- `referenced_ids(markdown) -> set[int]` — deliberately **liberal**: any `mnimg:<id>`
  anywhere, including inside code. The sweep uses this, and over-keeping an image is
  harmless while under-keeping one destroys it.

### Mapping a preview image back to its source text

A click in the preview identifies an image fragment; the edit has to land on the right
span in the source. The same image can appear twice at different widths, so it is
located by **ordinal** — the count of `mnimg:` image fragments before it in the rendered
document — and `find_refs(source)[ordinal]`.

`find_refs` and Qt's Markdown parser can disagree on edge cases (reference-style image
links, raw HTML). So the match is **verified**: the ref at that ordinal must have the
clicked image's id and width. If it doesn't, fall back to the only ref with that id and the same
current width; if that is still ambiguous, show a status-bar message and edit nothing.
Resizing the wrong image is worse than declining.

## Getting images in — `ui/image_ingest.py`

One ingest function behind three entry points. `NoteTab.source` becomes a small
`QPlainTextEdit` subclass overriding `canInsertFromMimeData` / `insertFromMimeData`.

- **Ctrl+V.** Paste an image **only when the clipboard has no text.** Word, Excel and
  Outlook put both text *and* a rendered picture of it on the clipboard; preferring the
  image would turn every pasted table into a screenshot. Snipping Tool / Win+Shift+S
  supply no text, so they paste as images. **To verify during implementation:** what a
  browser's "Copy image" puts on the clipboard — if it includes a text/URL format, this
  rule pastes the URL, and the rule needs a narrower test (e.g. text that is only an
  image URL). The image is encoded as PNG (lossless — right for screenshots).
- **Drag & drop.** When every dropped URL is a local file Qt can decode, each is
  ingested and inserted on its own line; anything else falls through to Qt's default.
  PNG, JPEG, GIF and WebP keep their **original bytes and mime**, so a 200 KB JPEG does
  not become a 2 MB PNG. Other decodable formats (BMP, TIFF) are re-encoded to PNG.
- **Insert → Image…** A `QFileDialog` in `MainWindow`, then the same path as a drop.

A file that won't decode, or is over **20 MB**, is refused with a status-bar message.

Inserted Markdown is `![screenshot](mnimg:7?w=…)`, or the file's stem as alt text
(stripped of `[` and `]`). The initial `w` is **`min(natural logical width, 800)`**, so a
4K screenshot doesn't land as a wall; the stored bytes stay full resolution.

**Width is in logical pixels.** An image's *natural* logical width is its pixel width
divided by the preview's device pixel ratio — one image pixel per device pixel, which is
both the sharpest rendering and the size the screenshot appeared on screen. A ref with no
`?w=` renders at natural width.

## Rendering — `ui/vault_document.py`

`VaultTextDocument(QTextDocument)` overrides `loadResource`: parse the `mnimg:` URL, fetch
the blob, decode, scale to `w × devicePixelRatio`, tag the result with that ratio, and
return it. Qt lays it out at `w` logical pixels, sharp at any display scaling.

`w` is text the user types, and the preview re-renders on every keystroke — typing
`?w=1000000` passes through `?w=100000` on the way. So the device-pixel width is
**clamped** before it reaches Qt: at most 4 × the image's natural pixel width and at most
16384 px, at least 1. Unclamped, a 400 × 200 image at `?w=100000` crashed the process and
at `?w=30000` pinned 1.7 GB. If Qt still returns a null image, the placeholder is shown.

`MainWindow.preview` is built before any vault is unlocked, so the document takes its
store late via `set_store(store | None)`. Before a store is set, or for an id that no
longer exists, it returns a small **"missing image"** placeholder — a visible failure,
never a silent blank.

**The cache is a requirement, not polish.** `_on_active_text_changed → _render_preview →
setMarkdown` rebuilds the whole document on every keystroke. Without a cache, each
character typed re-queries SQLite, re-decodes and re-scales every image in the note.

**Only scaled renderings are cached**, keyed by `(id, requested width or None, device
pixel ratio)` and looked up *before* anything is fetched or decoded, so a hit costs
neither a query nor a decode. On a miss the full-resolution image is decoded
transiently, scaled, and only the scaled result is kept. Caching the naturals too was
tried and thrashed: four 4K screenshots (~33 MB decoded each) overflow the budget, after
which every keystroke re-queried and re-decoded (~0.5 s each). **Copy image** decodes the
original on demand, uncached. The ratio is part of the key, so after the window moves to
a different-DPI monitor the image re-renders at the new ratio at the next edit or tab
switch.

Bounded by total decoded bytes (128 MB), least-recently-used first out. **`lock_session`
clears it and detaches the store** — before it closes the tabs, since both lock paths
have already closed the vault connection and each tab removal re-renders the next tab.
Decoded images are decrypted content, and the vault's guarantee is that none survives a
lock. `loadResource` is a Qt virtual, so any failure to fetch, decode or scale renders
the placeholder rather than raising.

## Getting images out — preview context menu

Right-clicking an image in the preview adds, above Qt's standard items:

- **Copy image** — the full-resolution original, not the scaled display copy.
- **Save image as…** — the stored bytes verbatim, with the matching extension
  (`image-7.png`); no re-encode.
- **Reset to original size** — removes `?w=` through the same undoable edit as a resize.

The clipboard is **not** cleared on lock. It was considered and rejected: with
lock-on-minimise enabled, copying a screenshot out and minimising to paste it elsewhere
would lose it.

## Editing the source — undoable span replacement

Both *Reset to original size* (slice 1) and a drag resize (slice 2) change a width. The
change is applied to the active tab's `source` by replacing **only the URL span**
through a `QTextCursor` inside `beginEditBlock()` / `endEditBlock()`, following
`ui/tool_runner.py`. Rewriting with `setPlainText` would be simpler but wipes the undo
stack and moves the cursor to the top of the note on every nudge. With a span edit,
Ctrl+Z undoes a resize like any other edit, and auto-save picks it up on its debounce.

## Orphan sweep

`ImageStore.sweep_orphans()` unions `referenced_ids()` over every note body and deletes
images outside that set. It runs **in `app._bind_vault`**, before `bind_autosave` — the
single path taken at launch and after every re-unlock — and **never mid-session**.

That timing is what makes the sweep safe. A paste commits the blob at once, but the note
text naming it is saved only after auto-save's 0.8 s debounce; a sweep in that gap would
delete a brand-new screenshot. Likewise, deleting an image line, letting auto-save run,
then pressing Ctrl+Z restores text pointing at a blob a mid-session sweep would have
removed. At `_bind_vault` neither can happen: `lock_session` flushes and closes every tab
(`tabbed_editor.clear_all()`), so no unsaved edits and no undo stacks exist — every
reference anywhere in the process is on disk.

Remaining edge, accepted: cut an image line, lock, unlock, paste it back — the image was
swept, and the note shows the "missing image" placeholder.

A sweep failure is logged and never blocks unlock. Deleting blobs does **not** shrink the
vault file: SQLite keeps the freed pages and reuses them for later images. Reclaiming the
space would mean a `VACUUM` that rewrites the whole encrypted file; not done.

## Slice 2 — drag handles — `ui/image_resize.py`

Split by responsibility: an **event filter** on `preview.viewport()` decides, and a
transparent **overlay** (`WA_TransparentForMouseEvents`) only paints.

1. Press on an image: `hitTest(ExactHit)` → position → the fragment's image format →
   URL and ordinal → rect from `cursorRect`. The filter consumes the press so the
   read-only `QTextEdit` doesn't begin a text selection.
2. The overlay paints an outline and four corner handles.
3. Dragging a corner moves a rubber band only — aspect locked from the natural size,
   clamped to `[32 px, viewport width]`, with **no document re-render mid-drag**.
4. Release applies the width through the undoable span edit; the preview re-renders and
   the same image stays selected.

Escape or a click on empty space deselects; double-click resets to natural size; the
overlay recomputes its rect on scroll and on viewport resize.

Accepted limitations: resizing needs the preview dock visible, so it is unavailable in
focus mode and when the dock is toggled off (the width is still editable as text); and
an image shown as code inside a code block is text, so it has no handles.

## Testing

**`core/` (no Qt):**
- `image_refs`: fenced code blocks and inline code are skipped; indented code blocks are
  not detected (see *Reference format*), and the ordinal verification covers the gap;
  duplicates are addressed by ordinal; malformed and missing `?w=`; non-`mnimg:` images left alone;
  `with_width` add / change / remove; `referenced_ids` includes refs inside code.
- `ImageStore`: add, dedup by hash, get, sweep removes only unreferenced images.
- Migration 3 upgrades a v2 vault and is idempotent.

**UI, headless (`QT_QPA_PLATFORM=offscreen`, as the existing suite runs):**
- Ctrl+V with an image inserts a ref at `min(natural, 800)`; with image + text, pastes text.
- Pasting the same image twice stores one blob.
- A dropped JPEG is stored with its original bytes and mime; a non-image drop is untouched.
- `loadResource` returns an image at `w × ratio` tagged with the ratio; a second render
  hits the cache; an unknown id yields the placeholder; `lock_session` empties the cache.
- Copy image / Save image as… / Reset to original size; reset is undoable.
- The ordinal-verification fallback edits the right ref, or none.
- Slice 2: the hit-test selects the right image among duplicates; a drag rewrites only
  that ref's width, in one undo step.
- New widgets pass `tests/test_theme_coverage.py`.

**Manual, real clipboard** — Win+Shift+S, a browser's "Copy image", and a range copied
from Excel each paste as the right thing (image, image, text).

**Packaged exe** — built with the `playground` env's Python explicitly (the Office_MCP
env leaks into bare `python` and PyInstaller's DLL resolution; see `LESSONS.md`). In the
exe: paste a screenshot, drop a JPEG, insert a WebP, resize, lock, unlock, and confirm
every image is still there. JPEG and WebP decoding are separate Qt plugins; this is the
only check that proves the exe bundles them.

## Out of scope

- Images don't shrink to fit a preview narrower than their width (horizontal scroll).
- No downscale-on-paste option; bytes are always stored as received.
- No `VACUUM` / "compact vault" command.
- Images are not included in any export — there is no export feature yet.
- An image whose decoded size exceeds Qt's allocation limit (about 256 MB decoded, e.g.
  a very large photo well under the 20 MB file cap) shows the "missing image"
  placeholder even though it was stored.
