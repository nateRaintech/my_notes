# Text Tools Suite & Dark Theme Completion — Design

**Date:** 2026-08-21
**Issues:** #98 (dark theme), #99 (tools suite)
**Milestone:** M9

## Motivation

Two requests from daily use of the shipped app.

1. **Dark mode tab labels are unreadable.** The tab bar arrived with the tabbed
   editor (#94) and was never added to `resources/dark.qss`, so it renders in the
   native Windows style — dark text on our dark background.
2. **A Notepad++-style tools suite.** The reference is the JSTool plugin: select a
   block of valid JSON, invoke the tool, get it formatted and indented in place.
   Generalise that one interaction into a broad suite of text and data tools.

## Part 1 — Tools Suite

### Approach: a declarative registry

Each tool is a `Tool` value object — id, name, category, description, keywords, and a
`str -> str` function. A single registry holds them all; **every UI surface is built
from that registry**. Adding a tool later is one function plus one registry entry, and
it appears in the menu, the context menu, and the palette with no further wiring.

Two alternatives were rejected:

- **Methods on `MainWindow`.** Fine for three tools, unmaintainable at fifty, and it
  puts transformation logic in the UI layer — a direct violation of the project's
  core/ui separation rule.
- **A user-scriptable plugin folder.** Powerful, but an arbitrary-code host inside an
  encrypted-notes application is a security surface not worth opening, and PyInstaller
  discovery of user scripts is fragile. The registry leaves this door open if it is
  ever wanted.

### Module layout

```
core/tools/               pure Python — no Qt, so ~50 tools test in milliseconds
  base.py                 Tool dataclass, ToolError(message, line, column)
  registry.py             ALL_TOOLS, CATEGORIES, get_tool(id), search(query)
  json_tools.py           format / minify / sort keys / validate / escape
  xml_tools.py            format / minify
  sql_tools.py            format / one-line          (lazy: sqlparse)
  md_tools.py             align table / table from CSV / escape Markdown
  encoding.py             base64, URL, HTML entities, hex, JWT, hashes
  text_tools.py           case transforms, line operations, wrapping
  convert.py              JSON<->YAML, timestamps, UUID   (lazy: pyyaml)
ui/
  tool_runner.py          the seam: selection -> tool -> undoable replace
  tools_menu.py           Tools menu + editor context menu, from the registry
  tool_palette.py         Ctrl+Shift+T fuzzy search, reusing core/fuzzy.py
```

`core/tools/` never imports `ui/`, and `ui/tool_runner.py` is the only place that knows
about `QTextCursor`. A tool function is a pure string transformation: it can be tested
with `assert format_json('{"a":1}') == '{\n  "a": 1\n}'` and nothing else.

### The `Tool` contract

```python
@dataclass(frozen=True)
class Tool:
    id: str                     # "json.format" — stable, used by tests and shortcuts
    name: str                   # "Format JSON"
    category: str               # "JSON"
    description: str            # one line, shown in the palette and as a tooltip
    func: Callable[[str], str]
    keywords: tuple[str, ...]   # extra fuzzy-search terms ("beautify", "pretty")
    mode: Literal["transform", "generate"]
```

`mode="generate"` marks the tools that ignore their input and insert at the cursor
(UUID, timestamp); everything else transforms the selected text.

Failures raise `ToolError(message, line=None, column=None)`. Tools never return a
partially transformed string and never raise a bare `ValueError` — the runner relies on
`ToolError` being the single failure channel.

### Runtime behaviour

The behaviour is what makes the suite feel like the Notepad++ plugin rather than a menu
of functions:

- **Scope.** Operates on the selection. With nothing selected, the whole note — so
  "Format JSON" on a note that is entirely JSON needs no selection at all.
- **Undo.** The replacement happens through one `QTextCursor` inside
  `beginEditBlock()` / `endEditBlock()`, so a **single Ctrl+Z** reverts the entire
  operation rather than unwinding it character by character.
- **Chaining.** The result is left selected, so a second tool applies to it directly.
- **Failure is inert.** On `ToolError` the document is left byte-identical and the
  status bar reports where it failed:
  `Invalid JSON: line 4, column 12 — expecting ','`. Nothing is mangled, nothing is
  half-applied.
- **Feedback on success.** The status bar confirms the action, so tools whose effect is
  not visible (hashes, validation) still report.

### Access surfaces

All three are generated from the registry, so they cannot drift apart.

- **Tools menu** — one submenu per category, in registry order.
- **Editor context menu** — the standard right-click menu with a "Tools" submenu
  appended, so the interaction matches Notepad++ muscle memory.
- **Tool Palette (Ctrl+Shift+T)** — fuzzy search over tool names, categories, and
  keywords, using the existing `core/fuzzy.py` matcher that powers the quick-switcher.
  Fifty tools stay findable by typing "js fmt".

### Dependencies

`pyyaml` and `sqlparse`, both pure-Python and bundled without extra PyInstaller flags.
Both are imported **lazily inside the tool function**, not at module import, so a
missing dependency degrades to a `ToolError("SQL formatting requires sqlparse...")`
rather than breaking the import of `core.tools` and taking the whole suite down.

## Part 2 — Dark Theme Completion

`resources/dark.qss` styles about a dozen widget classes; the app instantiates roughly
twice that. Every class the stylesheet does not name falls back to the native Windows
style, which is built for a light palette — the reported tab-bar bug is one instance of
a general gap.

Styled in this pass: `QTabWidget` / `QTabBar` (normal, selected, hover, close button),
`QDockWidget` (title bar and its float/close buttons), `QComboBox`, `QCheckBox`,
`QRadioButton`, `QSpinBox`, `QGroupBox`, `QProgressBar`, `QWizard`, `QMessageBox` /
`QDialogButtonBox`, `QToolButton`, and `QMenu::separator`.

The durable part is the **regression guard**: a test constructs a `MainWindow`, walks
its live widget tree, and asserts that every widget class present is named by a
selector in `dark.qss`. The next widget added without a rule fails the suite instead of
shipping unreadable.

## Testing

Per the project's TDD convention, tests come first at each layer.

- **Core** — behavioural tests per tool module: correct output for valid input, a
  located `ToolError` for invalid input, idempotence where it is meaningful (formatting
  already-formatted JSON), and Unicode safety. Qt-free and fast.
- **Registry** — ids are unique, every tool has a non-empty name/description/category,
  every category is non-empty, and search finds tools by name, category, and keyword.
- **Runner** — selection vs. whole-note scope, single-step undo, result left selected,
  and the invariant that a failing tool leaves the document byte-identical.
- **Surfaces** — the menu contains an action per registered tool; the palette filters
  and invokes.
- **Theme** — the widget-tree coverage guard above.

## Out of scope

Markdown syntax highlighting in the source pane, making dark the default theme,
user-configurable indent widths, and user-supplied tool scripts. Each is a reasonable
follow-up; none is needed for this pass.
