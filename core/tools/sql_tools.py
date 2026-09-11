"""SQL formatting tools, backed by ``sqlparse``.

``sqlparse`` is a pure-Python, dialect-agnostic formatter: it never validates the
SQL, so it will happily reindent a fragment or a dialect it has never seen. That
suits notes — the text being formatted is as often a snippet as a whole
statement.
"""

from __future__ import annotations

import re

from ._util import lazy_import, require_text


def format_sql(text: str) -> str:
    """Reindent SQL and upper-case its keywords."""
    sqlparse = lazy_import("sqlparse", tool="SQL formatting")
    source = require_text(text, "SQL")
    return sqlparse.format(
        source,
        reindent=True,
        keyword_case="upper",
        identifier_case=None,
        strip_comments=False,
        use_space_around_operators=True,
    ).strip()


def minify_sql(text: str) -> str:
    """Collapse SQL onto one line, dropping comments and redundant whitespace."""
    sqlparse = lazy_import("sqlparse", tool="SQL formatting")
    source = require_text(text, "SQL")
    collapsed = sqlparse.format(
        source, reindent=False, strip_comments=True, keyword_case="upper"
    )
    return re.sub(r"\s+", " ", collapsed).strip()
