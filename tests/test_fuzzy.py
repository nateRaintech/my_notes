"""Behavioral tests for the fuzzy subsequence matcher (``core/fuzzy.py``).

Pure Python — these run in CI without Qt. They cover subsequence detection, the
ranking heuristic (prefix > word-boundary > consecutive > scattered),
case-insensitivity, the empty-query "match everything" rule, and ``fuzzy_filter``'s
stable tie ordering. These are real behavioral guards, not editorial pins.
"""

from core.fuzzy import FuzzyMatch, fuzzy_filter, fuzzy_match


# --- fuzzy_match: subsequence detection -------------------------------------


def test_subsequence_matches_contiguous_and_scattered():
    assert fuzzy_match("abc", "abc") is not None
    assert fuzzy_match("abc", "aXbXc") is not None  # not necessarily adjacent


def test_non_subsequence_returns_none():
    assert fuzzy_match("abc", "acb") is None  # right chars, wrong order
    assert fuzzy_match("xyz", "abc") is None  # missing chars
    assert fuzzy_match("abcd", "abc") is None  # query longer than candidate


def test_match_is_case_insensitive():
    assert fuzzy_match("ABC", "abc") is not None
    assert fuzzy_match("abc", "ABC") is not None


def test_empty_query_matches_everything_with_zero_score():
    assert fuzzy_match("", "anything") == FuzzyMatch(0, ())


def test_positions_are_the_matched_indices():
    match = fuzzy_match("ac", "abc")
    assert match is not None
    assert match.positions == (0, 2)


# --- fuzzy_match: ranking heuristic -----------------------------------------


def test_prefix_match_outscores_scattered_match():
    prefix = fuzzy_match("read", "README")
    scattered = fuzzy_match("read", "thread reader")
    assert prefix is not None and scattered is not None
    assert prefix.score > scattered.score


def test_word_boundary_outscores_midword():
    # "mw": Main [W]indow matches the W at a word boundary; Micro[w]ave mid-word.
    boundary = fuzzy_match("mw", "Main Window")
    midword = fuzzy_match("mw", "Microwave")
    assert boundary is not None and midword is not None
    assert boundary.score > midword.score


def test_consecutive_outscores_gapped():
    consecutive = fuzzy_match("ab", "abc")
    gapped = fuzzy_match("ab", "axbxc")
    assert consecutive is not None and gapped is not None
    assert consecutive.score > gapped.score


# --- fuzzy_filter ------------------------------------------------------------


def test_filter_drops_non_matches_and_ranks_best_first():
    titles = ["Microwave", "Main Window", "totally unrelated"]
    # "unrelated" has no 'm' so it is dropped; the word-boundary match ranks first.
    assert fuzzy_filter("mw", titles) == ["Main Window", "Microwave"]


def test_filter_empty_query_returns_all_in_input_order():
    titles = ["Gamma", "Alpha", "Beta"]
    assert fuzzy_filter("", titles) == titles


def test_filter_is_stable_on_ties():
    # Each candidate is a clean prefix match, so all score equally -> input order.
    titles = ["abx", "aby", "abz"]
    assert fuzzy_filter("ab", titles) == ["abx", "aby", "abz"]


def test_filter_key_extracts_the_match_string():
    items = [{"t": "README"}, {"t": "license"}]
    assert fuzzy_filter("read", items, key=lambda d: d["t"]) == [{"t": "README"}]


def test_filter_limit_caps_the_result_count():
    titles = ["aa", "aaa", "aaaa"]
    assert len(fuzzy_filter("a", titles, limit=2)) == 2
