"""Small helpers shared by the tool modules.

Two concerns keep recurring across ~50 tools, and getting either wrong is the
difference between a tool that feels native and one that quietly mangles a note:
rejecting empty input with a useful message, and splitting text into lines
*without* losing whether it ended in a newline.
"""

from __future__ import annotations

from .base import ToolError


def require_text(text: str, what: str = "text") -> str:
    """Return ``text`` stripped of surrounding whitespace, or raise.

    Most tools are meaningless on an empty selection, and silently returning
    ``""`` would replace the user's selection with nothing. Raising instead keeps
    the runner's leave-it-untouched guarantee.
    """
    stripped = text.strip()
    if not stripped:
        raise ToolError(f"Nothing to work with — select some {what} first")
    return stripped


def split_lines(text: str) -> tuple[list[str], str]:
    """Split ``text`` into lines plus the trailing newline it ended with (if any).

    ``str.splitlines()`` discards the information that the text ended in a
    newline, so a naive join-on-newline silently eats the final blank line every
    time a line tool runs. Returning the trailing separator
    lets :func:`join_lines` put it back, making the line tools idempotent on
    text that already ends in a newline — the normal case for a selected block.
    """
    if not text:
        return [], ""
    trailing = "\n" if text.endswith(("\n", "\r")) else ""
    return text.splitlines(), trailing


def join_lines(lines: list[str], trailing: str = "") -> str:
    """Inverse of :func:`split_lines`."""
    return "\n".join(lines) + trailing


def lazy_import(module: str, *, tool: str, package: str | None = None):
    """Import ``module`` on demand, or raise a ToolError naming the fix.

    Two tool families need a third-party package (``pyyaml``, ``sqlparse``).
    Importing them at module scope would mean one missing package breaks the
    import of ``core.tools`` and takes all fifty tools down with it. Importing
    inside the tool keeps the blast radius to the one tool that needs it, and
    turns "ModuleNotFoundError" into a message that says what to install.
    """
    package = package or module
    try:
        return __import__(module)
    except ImportError as exc:  # pragma: no cover - exercised only without the dep
        raise ToolError(
            f"{tool} requires the '{package}' package, which isn't installed. "
            f"Run: pip install {package}"
        ) from exc
