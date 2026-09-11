"""Format conversion and value generation.

Conversions between JSON and YAML, and between epoch and ISO-8601 timestamps —
plus the two generators that ignore their input entirely and insert a fresh value
at the cursor.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from ._util import lazy_import, require_text
from .base import ToolError

# Epoch values with this many digits or more are milliseconds, not seconds:
# 1e11 seconds is the year 5138, while 1e11 milliseconds is 1973 — so any
# 12-or-more-digit value is overwhelmingly likely to be milliseconds.
_MILLISECOND_THRESHOLD = 100_000_000_000


def json_to_yaml(text: str) -> str:
    """Convert JSON to YAML, preserving key order."""
    yaml = lazy_import("yaml", tool="YAML conversion", package="pyyaml")
    source = require_text(text, "JSON")
    try:
        value = json.loads(source)
    except json.JSONDecodeError as exc:
        raise ToolError(
            f"Invalid JSON: {exc.msg}", line=exc.lineno, column=exc.colno
        ) from exc
    # PyYAML's default emitter puts list items at the same indent as their key
    # ("b:\n- 1"), which is valid YAML but reads badly and is not what any other
    # tool emits. Overriding increase_indent to ignore `indentless` gives the
    # conventional nested form ("b:\n  - 1").
    class _NestedDumper(yaml.SafeDumper):
        def increase_indent(self, flow=False, indentless=False):  # noqa: ARG002
            return super().increase_indent(flow, False)

    return yaml.dump(
        value,
        Dumper=_NestedDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=100,
    ).rstrip("\n")


def yaml_to_json(text: str) -> str:
    """Convert YAML to formatted JSON.

    Uses ``safe_load``, so YAML's arbitrary-object construction tags are refused
    rather than executed — the difference between a converter and a code
    execution primitive.
    """
    yaml = lazy_import("yaml", tool="YAML conversion", package="pyyaml")
    source = require_text(text, "YAML")
    try:
        value = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        raise ToolError(
            f"Invalid YAML: {getattr(exc, 'problem', None) or exc}",
            line=(mark.line + 1) if mark else None,
            column=(mark.column + 1) if mark else None,
        ) from exc
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def epoch_to_iso(text: str) -> str:
    """Convert a Unix timestamp to ISO-8601, in both UTC and local time.

    Accepts seconds or milliseconds (detected by magnitude) and tolerates a
    fractional part.
    """
    source = require_text(text, "timestamp")
    if not re.fullmatch(r"[+-]?\d+(\.\d+)?", source):
        raise ToolError(f"Not a Unix timestamp: {source!r}")
    value = float(source)
    unit = "seconds"
    if abs(value) >= _MILLISECOND_THRESHOLD:
        value /= 1000.0
        unit = "milliseconds"
    try:
        moment = datetime.fromtimestamp(value, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ToolError(f"Timestamp out of range: {source}") from exc
    local = moment.astimezone()
    return (
        f"{moment.isoformat().replace('+00:00', 'Z')}  "
        f"(local: {local.isoformat()}, read as {unit})"
    )


def iso_to_epoch(text: str) -> str:
    """Convert an ISO-8601 timestamp to Unix seconds.

    A timestamp with no timezone is read as local time, which is what someone
    typing ``2026-08-21 14:30`` into a note means.
    """
    source = require_text(text, "ISO timestamp")
    candidate = source.replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        moment = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ToolError(
            f"Not an ISO-8601 timestamp: {source!r}. "
            "Try something like 2026-08-21T14:30:00Z"
        ) from exc
    if moment.tzinfo is None:
        moment = moment.astimezone()
    seconds = moment.timestamp()
    whole = int(seconds)
    return f"{whole}  ({whole * 1000} ms)"


def new_uuid(_text: str = "") -> str:
    """Generate a random (version 4) UUID."""
    return str(uuid.uuid4())


def new_uuid_upper(_text: str = "") -> str:
    """Generate a random UUID in upper case, braces included."""
    return "{" + str(uuid.uuid4()).upper() + "}"


def now_iso(_text: str = "") -> str:
    """Insert the current local time as an ISO-8601 timestamp."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def now_utc(_text: str = "") -> str:
    """Insert the current UTC time as an ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def today(_text: str = "") -> str:
    """Insert today's date as ``YYYY-MM-DD``."""
    return datetime.now().astimezone().strftime("%Y-%m-%d")
