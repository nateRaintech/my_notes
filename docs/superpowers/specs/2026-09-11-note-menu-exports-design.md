# Note Menu, Copy Text, HTML & PDF Export — Design

**Date:** 2026-09-11
**Issue:** #105 (M11 part 1 of 6)
**Milestone:** M11 — Note Menu, Exports & Rich Inserts

## Motivation

The owner wants to act on the current note as a whole: copy its text, and export it to HTML
and PDF (DOCX and Outlook follow in parts 2 and 6). Every M11 insert type — images today;
tables, graphs and PDF imports later — must export cleanly. This part lays the rendering
foundation that all of them share.

## M11 decomposition (for context)

| Part | Issue | Scope |
|---|---|---|
| **1** | **#105** | **Note menu, Copy Text, HTML + PDF export** (this spec) |
| 2 | #106 | Export to DOCX (`python-docx`) |
| 3 | #107 | Insert → Table |
| 4 | #108 | Insert → Graph: line, pie, bar, histogram — editable data in the note, drawn with QtCharts |
| 5 | #109 | Insert → PDF: text and images into a note (`pypdf`, BSD; not PyMuPDF, AGPL) |
| 6 | #110 | Export to Outlook: a draft email with the note as its HTML body (`pywin32`, classic Outlook) |

Each part gets its own spec, plan and build.

## Decisions

| Question | Decision |
|---|---|
| When is the Note menu enabled? | Whenever a note tab is active: a note has been opened, or started with Ctrl+N. Disabled at launch, when no tab is open, and after a lock. |
| What do exports render from? | A **fresh document built from the note's Markdown** with the vault-backed renderer, not the live preview. The preview may be hidden (it is in the owner's layout) and is tied to the screen's pixel ratio. |
| PDF page | US Letter, 0.75" margins, Qt's default page-number footer, note title in the PDF metadata. Images rendered at 2× so they print sharp. Text is real, selectable text. |
| HTML | One self-contained `.html` file. `mnimg:` images become `data:` URIs holding the original stored bytes, at the note's display width. |
| Copy Text | Plain text as the note reads: no Markdown symbols; images, tables and (later) graphs omitted; list markers kept (`- ` / `1. `); blank-line runs collapsed. |

## Feasibility (probed 2026-09-11, playground env, real Windows platform)

- `VaultTextDocument.print_(QPdfWriter)` produces a valid PDF. The image is embedded (600×300 px for `?w=300` at ratio 2.0), the page is 612×792 pt (Letter), the title metadata is set, and the text extracts cleanly with Segoe UI embedded.
- On the **offscreen** test platform on Windows there are **no fonts** (`QFontDatabase.families()` is empty), so a PDF exported there has no text. Tests that assert PDF text skip when no fonts are available.
- `doc.toHtml()` emits `<img src="mnimg:1?w=300" alt="s" title="" />` — no width attribute — so the export rewrites both `src` and `width`.
- Walking `rootFrame()` visits paragraphs as blocks and tables as `QTextTable` frames, which can be skipped whole. An image paragraph's text is U+FFFC. List blocks report `textList()` with the style (`ListDisc`, `ListDecimal`) and the item number.

## Design

### `core/text.py` — `safe_filename(title) -> str`

Pure Python. Turns a note title into a default export filename stem. It replaces
`< > : " / \ | ? *` and control characters with `_`, trims trailing dots and spaces
(Windows rejects them), and caps the length at 100 characters. It falls back to `note`.

### `ui/note_render.py` — the shared renderer

- `render_document(markdown, store, *, device_pixel_ratio=1.0) -> VaultTextDocument`: a fresh document, bound to `store` (or `None`, which renders placeholders), with `setMarkdown(markdown)` applied.
- `plain_text(document) -> str`: walks the root frame.
  - Table frames are skipped whole. Other nested frames are walked recursively.
  - U+FFFC (images, and later graphs) is removed from each block's text, and trailing whitespace is trimmed (leading whitespace is kept, for code).
  - List items get a marker: `QTextList.itemText(block)` for numbered styles (`1.`), `-` for bullets. Each nesting level adds a two-space indent.
  - Runs of blank lines collapse to one, and leading and trailing blank lines are trimmed.

### `ui/note_export.py` — writing files

- `note_html(markdown, store, *, title, image_src=data_uri) -> str`: renders, calls `toHtml()`, inserts `<title>`, and rewrites every `mnimg:` `<img>` tag.
  - `src` becomes `image_src(record)`.
  - `width` becomes the ref's `w`, or the natural pixel width when there is no `w`.
  - A missing image gets `src=""`, so the browser shows the alt text.
  - `image_src` is a parameter so part 6 (Outlook) can pass `cid:` references for embedded email attachments.
- `write_html(markdown, store, path, *, title)`: writes UTF-8 and raises `OSError` on failure.
- `write_pdf(markdown, store, path, *, title)`: renders at ratio 2.0, then sets up a `QPdfWriter` with Letter pages, 0.75" margins, resolution 96, title and creator set. It calls `print_`, then releases the writer so the file is flushed. If the file is missing or empty afterwards, it raises `OSError`.

### `ui/main_window.py` — the Note menu

- A **Note** menu is added after View. It holds Copy Text, a separator, Export to HTML… and Export to PDF…. The actions are kept in `_note_actions`. Parts 2 and 6 append DOCX and Outlook to it.
- `_update_note_actions()` enables them iff `tabbed_editor.active_tab is not None`. It is called at startup and from `_on_active_tab_changed`. The lock path clears all tabs, which emits `active_tab_changed`, so a lock disables the menu with no extra code.
- `copy_note_text()` puts `plain_text(render_document(...))` on the clipboard, then shows "Copied N characters" in the status bar.
- `export_note_html()` / `export_note_pdf()` offer a default name of `safe_filename(derive_title(markdown)) + ext` through a new seam `_choose_export_path(default_name, file_filter) -> str`. They write the file and report "Exported <name>" or the error in the status bar.

### Dependencies

No new runtime dependency. `pypdf` is added to `requirements.txt` and to the CI install line: the PDF export tests read the file back with it, and part 5 needs it at runtime anyway.

## Testing

- **`core`:** `safe_filename` replaces reserved characters, trims trailing dots and spaces, caps the length, and falls back to `note`.
- **`plain_text`:**
  - omits the image paragraph and the table
  - keeps `- ` and `1. ` markers and code-line indentation
  - collapses blank runs
  - has no U+FFFC and no Markdown symbols
- **`note_html`:**
  - contains no `mnimg:`
  - the `data:image/png;base64,` payload decodes to exactly the stored bytes
  - the display width is set
  - the table and `<title>` are present
  - a missing id gets `src=""`
  - a custom `image_src` is honoured
- **`write_pdf`:** `pypdf` reads one page with one image and the title metadata. When fonts are available, the normalised text contains the note's words; otherwise the text check is skipped. A write to an impossible path raises `OSError`.
- **Window:**
  - The Note menu comes after View, and is disabled with no tab, enabled after a note opens, and disabled again after a lock.
  - Copy Text puts the expected text on the clipboard.
  - Export HTML/PDF write through the seam and report success; a cancelled dialog writes nothing.
  - The new menu passes `test_theme_coverage.py`.
- **Packaged exe:** built to `build/dist-105`, audited, and smoke-launched. It is stopped by exact path, because a `--onefile` exe is a bootloader plus a child process.

## Out of scope

- DOCX and Outlook (parts 2 and 6), and tables and graphs as insert types (parts 3 and 4). They inherit this rendering path.
- Custom PDF styling (fonts, headers, themes). The export uses the document's default look.
- Exporting several notes or a whole notebook at once.
