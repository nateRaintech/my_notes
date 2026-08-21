"""The text-tools suite: ~60 pure string transformations for the editor.

Modelled on Notepad++'s tool plugins — select a block of text, invoke a tool, get
the transformed text back. Everything in this package is pure Python, so the whole
suite is testable without Qt and, per CLAUDE.md's layering rule, ``core/`` stays
free of UI imports.

The public surface is the registry::

    from core.tools import get_tool
    get_tool("json.format").run('{"b":2,"a":1}')

:mod:`ui.tool_runner` is the only code that turns a tool into an edit.
"""

from .base import Tool, ToolError, ToolMode
from .registry import ALL_TOOLS, CATEGORIES, get_tool, search, tools_in

__all__ = [
    "ALL_TOOLS",
    "CATEGORIES",
    "Tool",
    "ToolError",
    "ToolMode",
    "get_tool",
    "search",
    "tools_in",
]
