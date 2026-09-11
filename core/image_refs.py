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
