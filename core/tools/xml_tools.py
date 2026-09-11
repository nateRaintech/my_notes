"""XML formatting tools.

``xml.dom.minidom`` is used rather than ``ElementTree`` because it preserves
comments and processing instructions, which people keep in the XML they paste
into notes. Its ``toprettyxml`` alone is not enough — it treats the existing
inter-element whitespace as text and compounds it on every run — so
whitespace-only text nodes are removed first, which also makes the tool
idempotent: formatting already-formatted XML returns the same string.
"""

from __future__ import annotations

import re
from xml.dom.minidom import Document, Node, parseString  # noqa: S408 - see _parse
from xml.parsers.expat import ExpatError

from ._util import require_text
from .base import ToolError

# A DOCTYPE that declares entities. Python's XML parsers expand these, so a
# pasted "billion laughs" payload would hang the editor. Notes are the user's own
# text, but hanging the app on a paste is a bad enough outcome to be worth one
# cheap check — and nobody formats an entity-declaring DOCTYPE in a note.
_ENTITY_DECL = re.compile(r"<!DOCTYPE[^>]*?<!ENTITY", re.IGNORECASE | re.DOTALL)


def _parse(text: str) -> Document:
    source = require_text(text, "XML")
    if _ENTITY_DECL.search(source):
        raise ToolError(
            "XML with entity declarations is refused — expanding them can hang "
            "the editor. Remove the <!ENTITY> declarations and try again."
        )
    try:
        return parseString(source)  # noqa: S318 - entity declarations rejected above
    except ExpatError as exc:
        raise ToolError(
            f"Invalid XML: {exc.args[0].split(':')[0] if exc.args else 'parse error'}",
            line=getattr(exc, "lineno", None),
            column=(getattr(exc, "offset", None) or 0) + 1 or None,
        ) from exc


def _strip_whitespace_nodes(node: Node) -> None:
    """Drop whitespace-only text nodes so pretty-printing starts from a clean tree.

    Text that is *not* pure whitespace is left byte-for-byte alone, so element
    content is never altered — only the indentation between elements, which is
    exactly what the caller asked to rewrite.
    """
    for child in list(node.childNodes):
        if child.nodeType == Node.TEXT_NODE:
            if not child.data.strip():
                node.removeChild(child)
        else:
            _strip_whitespace_nodes(child)


def format_xml(text: str) -> str:
    """Pretty-print XML with two-space indentation."""
    doc = _parse(text)
    _strip_whitespace_nodes(doc)
    pretty = doc.toprettyxml(indent="  ")
    # minidom emits its own declaration and can leave blank lines behind; drop
    # the empties so the result is stable under repeated application.
    lines = [line for line in pretty.splitlines() if line.strip()]
    return "\n".join(lines)


def minify_xml(text: str) -> str:
    """Collapse XML to a single line, dropping whitespace between elements."""
    doc = _parse(text)
    _strip_whitespace_nodes(doc)
    return doc.toxml()
