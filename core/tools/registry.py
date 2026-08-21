"""The tool registry — the single source of truth every UI surface is built from.

The Tools menu, the editor's right-click menu, and the Ctrl+Shift+T palette are
all generated from :data:`ALL_TOOLS`. That is the point of the design: adding a
tool is one function in a sibling module plus one entry here, and it appears in
every surface at once with no further wiring, so the surfaces cannot drift apart.

Tool ids are stable dotted strings (``"json.format"``). Names and descriptions are
free to change; ids are not, because tests and any future keybinding preferences
key off them.
"""

from __future__ import annotations

from core.fuzzy import fuzzy_filter

from . import convert, encoding, json_tools, md_tools, sql_tools, text_tools, xml_tools
from .base import Tool, ToolError

#: Category display order — how the Tools menu lays out its submenus.
CATEGORIES: tuple[str, ...] = (
    "JSON",
    "XML",
    "SQL",
    "Markdown",
    "Encode / Decode",
    "Hash",
    "Case",
    "Lines",
    "Convert",
    "Insert",
)

ALL_TOOLS: tuple[Tool, ...] = (
    # -- JSON ---------------------------------------------------------------
    Tool(
        "json.format", "Format JSON", "JSON",
        "Re-indent JSON with two spaces per level",
        json_tools.format_json,
        keywords=("beautify", "pretty", "print", "indent", "jstool", "tidy"),
    ),
    Tool(
        "json.format4", "Format JSON (4-space)", "JSON",
        "Re-indent JSON with four spaces per level",
        json_tools.format_json_4,
        keywords=("beautify", "pretty", "indent"),
    ),
    Tool(
        "json.formattab", "Format JSON (tabs)", "JSON",
        "Re-indent JSON with tabs",
        json_tools.format_json_tab,
        keywords=("beautify", "pretty", "indent", "tab"),
    ),
    Tool(
        "json.minify", "Minify JSON", "JSON",
        "Collapse JSON onto one line, dropping all optional whitespace",
        json_tools.minify_json,
        keywords=("compact", "compress", "one line", "shrink"),
    ),
    Tool(
        "json.sortkeys", "Sort JSON keys", "JSON",
        "Re-indent JSON with every object's keys in alphabetical order",
        json_tools.sort_json_keys,
        keywords=("alphabetical", "order", "canonical"),
    ),
    Tool(
        "json.validate", "Validate JSON", "JSON",
        "Check the JSON parses, and report its shape — or where it breaks",
        json_tools.validate_json,
        keywords=("check", "lint", "verify", "parse"),
        mode="inspect",
    ),
    Tool(
        "json.escape", "Escape as JSON string", "JSON",
        "Wrap the selection in quotes as a JSON string literal",
        json_tools.escape_json_string,
        keywords=("quote", "stringify", "literal"),
    ),
    Tool(
        "json.unescape", "Unescape JSON string", "JSON",
        "Turn a quoted JSON string literal back into raw text",
        json_tools.unescape_json_string,
        keywords=("unquote", "parse", "literal"),
    ),
    # -- XML ----------------------------------------------------------------
    Tool(
        "xml.format", "Format XML", "XML",
        "Re-indent XML with two spaces per level",
        xml_tools.format_xml,
        keywords=("beautify", "pretty", "indent", "tidy"),
    ),
    Tool(
        "xml.minify", "Minify XML", "XML",
        "Collapse XML onto one line, dropping whitespace between elements",
        xml_tools.minify_xml,
        keywords=("compact", "compress", "one line"),
    ),
    # -- SQL ----------------------------------------------------------------
    Tool(
        "sql.format", "Format SQL", "SQL",
        "Reindent SQL and upper-case its keywords",
        sql_tools.format_sql,
        keywords=("beautify", "pretty", "indent", "query", "tidy"),
    ),
    Tool(
        "sql.minify", "Minify SQL", "SQL",
        "Collapse SQL onto one line and strip its comments",
        sql_tools.minify_sql,
        keywords=("compact", "one line", "query"),
    ),
    # -- Markdown -----------------------------------------------------------
    Tool(
        "md.aligntable", "Align table", "Markdown",
        "Re-pad a Markdown table so every pipe lines up",
        md_tools.align_table,
        keywords=("table", "pipe", "format", "tidy", "column"),
    ),
    Tool(
        "md.tablefromcsv", "Table from CSV / TSV", "Markdown",
        "Turn pasted comma- or tab-separated rows into a Markdown table",
        md_tools.table_from_delimited,
        keywords=("csv", "tsv", "spreadsheet", "excel", "paste", "table"),
    ),
    Tool(
        "md.escape", "Escape Markdown", "Markdown",
        "Backslash-escape every Markdown special character",
        md_tools.escape_markdown,
        keywords=("literal", "verbatim", "quote"),
    ),
    Tool(
        "md.unescape", "Unescape Markdown", "Markdown",
        "Remove backslash escapes from Markdown special characters",
        md_tools.unescape_markdown,
        keywords=("literal", "unquote"),
    ),
    # -- Encode / Decode ----------------------------------------------------
    Tool(
        "b64.encode", "Base64 encode", "Encode / Decode",
        "Encode the selection as standard Base64",
        encoding.base64_encode, keywords=("base64", "b64"),
    ),
    Tool(
        "b64.decode", "Base64 decode", "Encode / Decode",
        "Decode standard Base64 back to text",
        encoding.base64_decode, keywords=("base64", "b64"),
    ),
    Tool(
        "b64url.encode", "Base64-URL encode", "Encode / Decode",
        "Encode as URL-safe Base64 with the padding stripped",
        encoding.base64url_encode, keywords=("base64", "urlsafe", "jwt"),
    ),
    Tool(
        "b64url.decode", "Base64-URL decode", "Encode / Decode",
        "Decode URL-safe Base64, with or without padding",
        encoding.base64url_decode, keywords=("base64", "urlsafe", "jwt"),
    ),
    Tool(
        "url.encode", "URL encode", "Encode / Decode",
        "Percent-encode the selection, escaping every reserved character",
        encoding.url_encode, keywords=("percent", "escape", "querystring", "uri"),
    ),
    Tool(
        "url.decode", "URL decode", "Encode / Decode",
        "Decode percent-encoding, treating + as a space",
        encoding.url_decode, keywords=("percent", "unescape", "uri"),
    ),
    Tool(
        "html.escape", "HTML escape", "Encode / Decode",
        "Escape the five XML/HTML special characters as entities",
        encoding.html_escape, keywords=("entity", "entities", "amp"),
    ),
    Tool(
        "html.unescape", "HTML unescape", "Encode / Decode",
        "Convert HTML entities back to characters",
        encoding.html_unescape, keywords=("entity", "entities", "amp"),
    ),
    Tool(
        "hex.encode", "Hex encode", "Encode / Decode",
        "Encode the selection as continuous lowercase hex",
        encoding.hex_encode, keywords=("hexadecimal", "bytes"),
    ),
    Tool(
        "hex.encodespaced", "Hex encode (spaced)", "Encode / Decode",
        "Encode as space-separated hex byte pairs",
        encoding.hex_encode_spaced, keywords=("hexadecimal", "dump", "bytes"),
    ),
    Tool(
        "hex.decode", "Hex decode", "Encode / Decode",
        "Decode hex back to text, ignoring whitespace and 0x prefixes",
        encoding.hex_decode, keywords=("hexadecimal", "bytes"),
    ),
    Tool(
        "jwt.decode", "Decode JWT", "Encode / Decode",
        "Decode a JSON Web Token's header and payload (signature NOT verified)",
        encoding.jwt_decode, keywords=("token", "bearer", "auth", "jwt", "claims"),
    ),
    # -- Hash ---------------------------------------------------------------
    Tool(
        "hash.md5", "MD5", "Hash",
        "MD5 digest of the selection, copied to the clipboard",
        encoding.md5, keywords=("digest", "checksum", "hash"), mode="inspect",
    ),
    Tool(
        "hash.sha1", "SHA-1", "Hash",
        "SHA-1 digest of the selection, copied to the clipboard",
        encoding.sha1, keywords=("digest", "checksum", "hash"), mode="inspect",
    ),
    Tool(
        "hash.sha256", "SHA-256", "Hash",
        "SHA-256 digest of the selection, copied to the clipboard",
        encoding.sha256, keywords=("digest", "checksum", "hash"), mode="inspect",
    ),
    Tool(
        "hash.sha512", "SHA-512", "Hash",
        "SHA-512 digest of the selection, copied to the clipboard",
        encoding.sha512, keywords=("digest", "checksum", "hash"), mode="inspect",
    ),
    # -- Case ---------------------------------------------------------------
    Tool(
        "case.upper", "UPPER CASE", "Case", "Upper-case the selection",
        text_tools.to_upper, keywords=("capitals", "caps", "shout"),
    ),
    Tool(
        "case.lower", "lower case", "Case", "Lower-case the selection",
        text_tools.to_lower, keywords=("small",),
    ),
    Tool(
        "case.title", "Title Case", "Case",
        "Capitalise the first letter of every word",
        text_tools.to_title, keywords=("capitalise", "capitalize", "headline"),
    ),
    Tool(
        "case.sentence", "Sentence case", "Case",
        "Lower-case, then capitalise the first letter of each sentence",
        text_tools.to_sentence, keywords=("capitalise", "capitalize", "prose"),
    ),
    Tool(
        "case.camel", "camelCase", "Case", "Convert each line to camelCase",
        text_tools.to_camel_case, keywords=("identifier", "variable", "code"),
    ),
    Tool(
        "case.pascal", "PascalCase", "Case", "Convert each line to PascalCase",
        text_tools.to_pascal_case,
        keywords=("identifier", "class", "upper camel", "code"),
    ),
    Tool(
        "case.snake", "snake_case", "Case", "Convert each line to snake_case",
        text_tools.to_snake_case,
        keywords=("identifier", "underscore", "python", "code"),
    ),
    Tool(
        "case.kebab", "kebab-case", "Case", "Convert each line to kebab-case",
        text_tools.to_kebab_case,
        keywords=("identifier", "dash", "hyphen", "slug", "css"),
    ),
    Tool(
        "case.constant", "CONSTANT_CASE", "Case",
        "Convert each line to CONSTANT_CASE",
        text_tools.to_constant_case,
        keywords=("identifier", "screaming", "env", "code"),
    ),
    # -- Lines --------------------------------------------------------------
    Tool(
        "lines.sortasc", "Sort A to Z", "Lines",
        "Sort the lines in ascending order",
        text_tools.sort_lines_asc, keywords=("alphabetical", "order", "ascending"),
    ),
    Tool(
        "lines.sortdesc", "Sort Z to A", "Lines",
        "Sort the lines in descending order",
        text_tools.sort_lines_desc,
        keywords=("alphabetical", "order", "descending", "reverse"),
    ),
    Tool(
        "lines.sortci", "Sort (ignore case)", "Lines",
        "Sort the lines, case-insensitively",
        text_tools.sort_lines_ci, keywords=("alphabetical", "order", "insensitive"),
    ),
    Tool(
        "lines.sortnumeric", "Sort numerically", "Lines",
        "Sort by each line's leading number, non-numbers last",
        text_tools.sort_lines_numeric, keywords=("number", "order", "numeric"),
    ),
    Tool(
        "lines.sortnatural", "Sort naturally", "Lines",
        "Sort with digit runs compared as numbers, so file2 precedes file10",
        text_tools.sort_lines_natural,
        keywords=("natural", "human", "version", "order"),
    ),
    Tool(
        "lines.dedupe", "Remove duplicate lines", "Lines",
        "Keep only the first occurrence of each line, preserving order",
        text_tools.remove_duplicate_lines,
        keywords=("unique", "dedupe", "distinct"),
    ),
    Tool(
        "lines.removeblank", "Remove blank lines", "Lines",
        "Drop every empty or whitespace-only line",
        text_tools.remove_blank_lines, keywords=("empty", "compact", "squeeze"),
    ),
    Tool(
        "lines.trim", "Trim trailing whitespace", "Lines",
        "Strip trailing spaces and tabs from every line",
        text_tools.trim_trailing_whitespace,
        keywords=("strip", "clean", "whitespace"),
    ),
    Tool(
        "lines.reverse", "Reverse line order", "Lines",
        "Reverse the order of the lines",
        text_tools.reverse_lines, keywords=("flip", "invert", "backwards"),
    ),
    Tool(
        "lines.number", "Number lines", "Lines",
        "Prefix each line with its 1-based number",
        text_tools.number_lines, keywords=("enumerate", "index", "count"),
    ),
    Tool(
        "lines.join", "Join lines", "Lines",
        "Join every line into one, separated by spaces",
        text_tools.join_lines_tool,
        keywords=("merge", "unwrap", "one line", "concatenate"),
    ),
    Tool(
        "lines.wrap", "Wrap at 80 columns", "Lines",
        "Re-wrap the selection to 80 columns, keeping paragraph breaks",
        text_tools.wrap_lines, keywords=("reflow", "fill", "hard wrap", "format"),
    ),
    Tool(
        "lines.stats", "Text statistics", "Lines",
        "Count the characters, words, lines, and paragraphs in the selection",
        text_tools.text_stats, keywords=("count", "words", "length", "measure"),
        mode="inspect",
    ),
    # -- Convert ------------------------------------------------------------
    Tool(
        "convert.json2yaml", "JSON to YAML", "Convert",
        "Convert JSON to YAML, preserving key order",
        convert.json_to_yaml, keywords=("yaml", "yml", "json", "convert"),
    ),
    Tool(
        "convert.yaml2json", "YAML to JSON", "Convert",
        "Convert YAML to formatted JSON",
        convert.yaml_to_json, keywords=("yaml", "yml", "json", "convert"),
    ),
    Tool(
        "convert.epoch2iso", "Epoch to ISO-8601", "Convert",
        "Convert a Unix timestamp (seconds or milliseconds) to a readable date",
        convert.epoch_to_iso,
        keywords=("unix", "timestamp", "date", "time", "convert"), mode="inspect",
    ),
    Tool(
        "convert.iso2epoch", "ISO-8601 to Epoch", "Convert",
        "Convert an ISO-8601 timestamp to Unix seconds",
        convert.iso_to_epoch,
        keywords=("unix", "timestamp", "date", "time", "convert"), mode="inspect",
    ),
    # -- Insert -------------------------------------------------------------
    Tool(
        "insert.uuid", "UUID (v4)", "Insert", "Insert a random version-4 UUID",
        convert.new_uuid, keywords=("guid", "uuid", "random", "id"), mode="generate",
    ),
    Tool(
        "insert.uuidupper", "UUID (braced, upper)", "Insert",
        "Insert a random UUID in upper case with braces",
        convert.new_uuid_upper, keywords=("guid", "uuid", "registry"), mode="generate",
    ),
    Tool(
        "insert.now", "Timestamp (local)", "Insert",
        "Insert the current local time as an ISO-8601 timestamp",
        convert.now_iso, keywords=("date", "time", "now", "stamp"), mode="generate",
    ),
    Tool(
        "insert.utcnow", "Timestamp (UTC)", "Insert",
        "Insert the current UTC time as an ISO-8601 timestamp",
        convert.now_utc, keywords=("date", "time", "now", "stamp", "zulu"),
        mode="generate",
    ),
    Tool(
        "insert.today", "Today's date", "Insert",
        "Insert today's date as YYYY-MM-DD",
        convert.today, keywords=("date", "day", "now"), mode="generate",
    ),
)

_BY_ID: dict[str, Tool] = {tool.id: tool for tool in ALL_TOOLS}


def get_tool(tool_id: str) -> Tool:
    """Look a tool up by id, raising :class:`ToolError` if there is no such tool."""
    try:
        return _BY_ID[tool_id]
    except KeyError:
        raise ToolError(f"No such tool: {tool_id!r}") from None


def tools_in(category: str) -> tuple[Tool, ...]:
    """Every tool in ``category``, in registry order."""
    return tuple(tool for tool in ALL_TOOLS if tool.category == category)


def search(query: str, *, limit: int | None = None) -> list[Tool]:
    """Fuzzily rank the tools against ``query``, best first.

    Matches against name, category, and keywords together (``Tool.searchable``),
    which is what lets "js fmt" find *Format JSON* and "guid" find *UUID (v4)*.
    An empty query returns every tool in registry order — the palette's initial
    state.
    """
    return fuzzy_filter(query, ALL_TOOLS, key=lambda tool: tool.searchable, limit=limit)
