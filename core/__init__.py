"""Pure-Python core for my_notes.

Everything in this package is Qt-free and unit-testable in isolation. Per
CLAUDE.md's strict layering rule, ``core/`` must never import from ``ui/`` or
PySide6 — the UI depends on core, never the reverse.
"""
