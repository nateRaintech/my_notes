# Hidden Text for Credentials — Design

**Date:** 2026-09-23
**Issue:** #113
**Milestone:** M12

## Motivation

Notes hold credentials. When a note is on screen — in a demo, or with someone
behind you — every password in it is readable. The ask: select text, choose
**Hide text**, and from then on it shows as asterisks with a copy button.

## Decisions

| Question | Decision |
|---|---|
| Where does the plaintext live? | **Outside the note**, in a new vault table. The note keeps only a reference, so the secret is not in the source pane, the note body, or the search index. |
| What does the preview show? | A drawn pill: eight dots and a copy glyph. The real characters are never rendered, and the dot count does not reveal the length. |
| Reveal / peek? | **None.** After hiding, the plaintext never appears on screen again. |
| Editing | **Edit hidden text…** — a masked password field. There is no "unhide". |
| Copying | Click the pill, or right-click → **Copy hidden text**. |
| Clipboard | Cleared after N seconds (Settings, default 30, 0 = never) if it still holds the secret, and on lock. Marked as excluded from Windows clipboard history and cloud sync. |
| Where can you hide? | Source pane selection only (v1). Mapping a preview selection back to Markdown is fragile. |
| Exports | HTML and PDF show the pill; Copy Text writes `••••••••`. The plaintext never leaves via an export. |

## Approach — the image pattern, reused

Hidden text copies the design of vault images (`2026-09-11-screenshot-images-design.md`).
A note refers to a hidden value with an ordinary Markdown image whose URL uses a
private scheme:

```
Password: ![hidden](mnsec:7)
```

The preview's `VaultTextDocument.loadResource` answers `mnsec:` URLs with the
pill image. Rendering needs no lookup — the pill is the same for every value —
so a dangling reference still renders; copying it reports that the text no
longer exists.

The rejected alternative — inline syntax such as `||hunter2||` masked by a
highlighter — keeps the plaintext in the body: indexed by FTS, exposed by the
caret, carried by every copy of the note text.

## Storage — migration 4, `core/hidden_text.py`

```sql
CREATE TABLE IF NOT EXISTS hidden_texts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Named `hidden_texts`, not `secrets`, to stay clear of migration 2's unused
`app_secrets`. `AUTOINCREMENT` for the same reason as images: a stale
`mnsec:<id>` must never copy an unrelated value that inherited a freed rowid.
No deduplication: two notes hiding the same password get two rows, so editing
one doesn't silently change the other.

`HiddenTextStore(connection)`: `add(value) -> int`, `get(id) -> str | None`,
`update(id, value) -> bool`, `sweep_orphans() -> int`. The sweep runs where the
image sweep runs — at unlock, before any tab opens — using the same liberal
"any mention anywhere" id scan.

## References — `core/hidden_refs.py`

`hidden_markdown(id, label)`, `parse_url`, `find_refs`, `resolve_ref`
(ordinal-verified, as for images), `referenced_ids`. The "which image links does
the preview actually render" scan (skipping fenced code and inline code) is
factored out of `core/image_refs.py` into a shared `rendered_links(markdown)`
so both schemes use one scanner.

## UI — `ui/hidden_text.py`

- `mask_image(ratio)` — the pill, painted with shapes (no fonts: the offscreen
  test platform on Windows has none, and the pill must look identical in exports).
- `rendered_hidden(document)` / `hidden_at(view, point)` — the preview-side
  mapping, parallel to `rendered_images` / `image_at`.
- `PreviewClickFilter` — an event filter on the preview viewport: a left click
  on a pill emits `clicked`; hovering one shows a pointing-hand cursor.
- `ClipboardGuard` — puts a value on the clipboard with the Windows
  history/cloud exclusion formats, remembers only its SHA-256, clears the
  clipboard after the timeout if it still holds that value, and clears on lock.

`MainWindow`: **Hide text** in the source context menu (enabled with a
non-blank selection and a bound vault); **Copy hidden text** / **Edit hidden
text…** in the preview context menu over a pill; `bind_hidden_text(store)`;
`lock_session` detaches the store and clears the clipboard if it holds a secret.
Hiding is one undoable edit. Undo brings the plaintext back into the source —
that is the user's own undo, and the row stays until the next unlock's sweep.

## Testing

Pure-Python tests for the store, migration and refs; headless Qt tests for the
pill rendering, hit-testing, hiding from the context menu, copying (and the
timed clear), editing, lock behaviour, Copy Text, and HTML/PDF export never
containing the plaintext. Final check: the packaged exe launches clean.
