"""Encoding, decoding, and hashing tools.

Everything here round-trips through UTF-8 bytes: the editor holds text, these
formats describe bytes, and UTF-8 is the bridge. Decoders are strict — bytes that
aren't valid UTF-8 raise rather than being silently replaced with U+FFFD, because
a decoder that "succeeds" by corrupting its output is worse than one that
refuses.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import html
import json
import re
import urllib.parse

from ._util import require_text
from .base import ToolError

_WHITESPACE = re.compile(r"\s+")


def _to_text(data: bytes, label: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError(
            f"The decoded {label} isn't valid UTF-8 text (byte {exc.start} is "
            f"0x{data[exc.start]:02x}), so it can't be shown in the editor."
        ) from exc


def _b64_decode(text: str, *, urlsafe: bool, label: str = "Base64") -> bytes:
    """Decode Base64, tolerating missing padding and embedded whitespace.

    Both are near-universal in text people paste: line-wrapped Base64 carries
    newlines, and URL-safe Base64 (as used by JWTs) conventionally strips the
    ``=`` padding. Neither is a reason to fail.
    """
    source = _WHITESPACE.sub("", require_text(text, label))
    padded = source + "=" * (-len(source) % 4)
    decoder = base64.urlsafe_b64decode if urlsafe else base64.b64decode
    try:
        return decoder(padded)
    except (binascii.Error, ValueError) as exc:
        raise ToolError(f"Not valid {label}: {exc}") from exc


# -- Base64 ------------------------------------------------------------------

def base64_encode(text: str) -> str:
    """Encode the selection as standard Base64."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def base64_decode(text: str) -> str:
    """Decode standard Base64 back to text."""
    return _to_text(_b64_decode(text, urlsafe=False), "Base64")


def base64url_encode(text: str) -> str:
    """Encode as URL-safe Base64 with the padding stripped (the JWT convention)."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def base64url_decode(text: str) -> str:
    """Decode URL-safe Base64, with or without padding."""
    return _to_text(_b64_decode(text, urlsafe=True, label="URL-safe Base64"), "Base64")


# -- URL ---------------------------------------------------------------------

def url_encode(text: str) -> str:
    """Percent-encode the selection, escaping every reserved character.

    ``safe=""`` means slashes are escaped too — the right default when encoding a
    *value* to put into a URL, which is what a selection almost always is.
    """
    return urllib.parse.quote(text, safe="")


def url_decode(text: str) -> str:
    """Decode percent-encoding, treating ``+`` as a space (form encoding)."""
    return urllib.parse.unquote_plus(text)


# -- HTML --------------------------------------------------------------------

def html_escape(text: str) -> str:
    """Escape ``& < > " '`` as HTML entities."""
    return html.escape(text, quote=True)


def html_unescape(text: str) -> str:
    """Convert HTML entities (named or numeric) back to characters."""
    return html.unescape(text)


# -- Hex ---------------------------------------------------------------------

def hex_encode(text: str) -> str:
    """Encode the selection as continuous lowercase hex."""
    return text.encode("utf-8").hex()


def hex_encode_spaced(text: str) -> str:
    """Encode as space-separated hex byte pairs, the way a hex dump reads."""
    return " ".join(f"{byte:02x}" for byte in text.encode("utf-8"))


def hex_decode(text: str) -> str:
    """Decode hex back to text, ignoring whitespace, ``0x`` prefixes, and commas."""
    source = require_text(text, "hex")
    cleaned = re.sub(r"0[xX]|[\s,]+", "", source)
    if len(cleaned) % 2:
        raise ToolError(
            f"Hex needs an even number of digits — got {len(cleaned)}"
        )
    try:
        return _to_text(bytes.fromhex(cleaned), "hex")
    except ValueError as exc:
        raise ToolError(f"Not valid hex: {exc}") from exc


# -- JWT ---------------------------------------------------------------------

def jwt_decode(text: str) -> str:
    """Decode a JSON Web Token's header and payload into readable JSON.

    The signature is reported but **not verified** — verification needs the
    signing key, which a notes app has no business holding. The output says so,
    so a decoded token is never mistaken for a validated one.
    """
    token = _WHITESPACE.sub("", require_text(text, "JWT"))
    parts = token.split(".")
    if len(parts) != 3:
        raise ToolError(
            f"Not a JWT — expected 3 dot-separated parts, found {len(parts)}"
        )

    def segment(raw: str, name: str) -> object:
        decoded = _to_text(_b64_decode(raw, urlsafe=True, label=f"JWT {name}"), name)
        try:
            return json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise ToolError(f"The JWT {name} isn't valid JSON: {exc.msg}") from exc

    result = {
        "header": segment(parts[0], "header"),
        "payload": segment(parts[1], "payload"),
        "signature": f"{parts[2]}  (NOT verified — no signing key)",
    }
    return json.dumps(result, indent=2, ensure_ascii=False)


# -- Hashes ------------------------------------------------------------------

def _hash(algorithm: str):
    def run(text: str) -> str:
        digest = hashlib.new(algorithm, text.encode("utf-8")).hexdigest()
        return f"{algorithm.upper()}: {digest}"

    return run


md5 = _hash("md5")
sha1 = _hash("sha1")
sha256 = _hash("sha256")
sha512 = _hash("sha512")
