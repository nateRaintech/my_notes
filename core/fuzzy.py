"""Fuzzy subsequence matching for the quick-switcher (ROADMAP.md M4).

Pure Python, no Qt — ``core/`` is the unit-testable layer (CLAUDE.md). The
quick-switcher dialog (:mod:`ui.quick_switcher`) ranks note titles through this
module so the user can jump to a note by typing a loose abbreviation of its title.

"Fuzzy" here means *subsequence* matching, the standard "Go to anything"
interaction (VS Code Ctrl+P, Sublime): a query matches a candidate when every
query character appears in the candidate, in order, but not necessarily adjacent.
Matching is case-insensitive. Among matches, a score orders the best first —
matches at the start of the string, at word boundaries, and in consecutive runs
score higher, while skipped characters cost a little. This is a heuristic tuned
for short titles, not an optimal alignment: it is deterministic and good enough
to float the obvious choice to the top. This is deliberately distinct from
:meth:`core.repository.Repository.search_notes`, which is FTS5 *word* search over
title and body; the quick-switcher matches titles only and never touches the DB.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")

# Characters that begin a new "word" inside a title, so a query char landing
# right after one is a strong (word-boundary) match — e.g. "mw" -> "Main Window".
_SEPARATORS = frozenset(" \t\n-_/\\.:|,;()[]{}")

# Scoring weights. Bonuses reward "obvious" placements; penalties discourage
# scattered ones. Tuned so prefix > word-boundary > consecutive > scattered.
_START_BONUS = 12          # query char matches at index 0 of the candidate
_BOUNDARY_BONUS = 9        # match immediately after a separator / camelCase hump
_CONSECUTIVE_BONUS = 6     # match immediately follows the previous matched char
_GAP_PENALTY = 1           # per skipped char between two matches
_LEADING_GAP_PENALTY = 1   # per skipped char before the first match
_MAX_LEADING_GAP = 10      # cap the leading-gap penalty so deep matches still rank


@dataclass(frozen=True)
class FuzzyMatch:
    """The result of matching a query against one candidate string.

    ``score`` orders matches (higher is better); ``positions`` are the indices in
    the candidate that the query characters matched, in order (useful for
    highlighting). An empty query yields ``FuzzyMatch(0, ())`` — it matches every
    candidate so the quick-switcher shows all notes before anything is typed.
    """

    score: int
    positions: tuple[int, ...]


def _is_boundary(candidate: str, index: int) -> bool:
    """True if ``candidate[index]`` begins a new word.

    Either the preceding character is a separator, or this is a lower->upper
    camelCase hump (``mainWindow`` — the ``W`` begins a word). ``index == 0`` is
    handled by the start bonus, not here.
    """
    if index == 0:
        return True
    previous = candidate[index - 1]
    if previous in _SEPARATORS:
        return True
    return previous.islower() and candidate[index].isupper()


def fuzzy_match(query: str, candidate: str) -> FuzzyMatch | None:
    """Match ``query`` against ``candidate`` as a case-insensitive subsequence.

    Returns a :class:`FuzzyMatch` (score + matched positions) when every character
    of ``query`` appears in ``candidate`` in order, else ``None``. An empty query
    matches everything with a baseline score of 0. Greedy earliest-match is used:
    it always finds a subsequence when one exists, and the scoring favours
    start-of-string, word-boundary, and consecutive matches.
    """
    if query == "":
        return FuzzyMatch(0, ())

    lowered_query = query.lower()
    lowered_candidate = candidate.lower()

    positions: list[int] = []
    score = 0
    search_from = 0
    previous = -1

    for query_char in lowered_query:
        index = lowered_candidate.find(query_char, search_from)
        if index == -1:
            return None

        # Reward "obvious" placements.
        if index == 0:
            score += _START_BONUS
        elif _is_boundary(candidate, index):
            score += _BOUNDARY_BONUS
        if previous != -1 and index == previous + 1:
            score += _CONSECUTIVE_BONUS

        # Penalise skipped characters (the leading gap is capped so a match deep
        # in a long title is not buried).
        if previous == -1:
            score -= min(index, _MAX_LEADING_GAP) * _LEADING_GAP_PENALTY
        else:
            score -= (index - previous - 1) * _GAP_PENALTY

        positions.append(index)
        previous = index
        search_from = index + 1

    return FuzzyMatch(score, tuple(positions))


def fuzzy_filter(
    query: str,
    items: Iterable[T],
    *,
    key: Callable[[T], str] = str,
    limit: int | None = None,
) -> list[T]:
    """Return the items whose ``key`` fuzzily matches ``query``, best first.

    Each item is scored via :func:`fuzzy_match` against ``key(item)``; items that
    do not match are dropped. The result is ordered by score descending and is
    *stable* on ties — items with equal scores keep their input order, so a caller
    passing notes newest-first gets newest-first within each score tier (and, for
    an empty query, the whole list unchanged). ``limit``, if given, caps the count.
    """
    scored: list[tuple[int, T]] = []
    for item in items:
        match = fuzzy_match(query, key(item))
        if match is not None:
            scored.append((match.score, item))

    # sorted() / list.sort() is stable, so equal scores retain their input order
    # even with reverse=True.
    scored.sort(key=lambda pair: pair[0], reverse=True)
    result = [item for _score, item in scored]
    if limit is not None:
        result = result[:limit]
    return result
