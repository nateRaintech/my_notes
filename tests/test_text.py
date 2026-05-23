"""Unit tests for ``core.text`` — pure-Python, no Qt.

This is the project's first *real* test: it runs unconditionally (no
``importorskip``), so it executes and passes under ``pytest`` everywhere,
including CI where the Qt runtime is not installed. Per CLAUDE.md's strict
layering, ``core/`` is the Qt-free, unit-testable layer.
"""

from core.text import count_words, derive_title


def test_atx_heading_becomes_title():
    assert derive_title("# Hello World") == "Hello World"


def test_multiple_hashes_are_stripped():
    assert derive_title("### My Note") == "My Note"


def test_plain_first_line_is_used():
    assert derive_title("just a note\nsecond line") == "just a note"


def test_leading_blank_lines_are_skipped():
    assert derive_title("\n\n  # Title\n\nbody") == "Title"


def test_empty_input_returns_fallback():
    assert derive_title("") == "Untitled"


def test_whitespace_only_input_returns_fallback():
    assert derive_title("   \n\t\n  ") == "Untitled"


def test_internal_whitespace_is_collapsed():
    assert derive_title("#   Hello    World") == "Hello World"


def test_hash_without_space_is_literal_not_a_heading():
    # CommonMark: an ATX heading requires a space after the '#' run.
    assert derive_title("#hashtag") == "#hashtag"


def test_seven_hashes_is_not_a_heading():
    # CommonMark allows at most 6 '#'; 7 is literal text.
    assert derive_title("####### too many") == "####### too many"


def test_long_title_is_truncated_with_ellipsis():
    title = derive_title("abcdefghij klmnop", max_length=10)
    assert len(title) <= 10
    assert title.endswith("…")


def test_custom_fallback_is_respected():
    assert derive_title("   ", fallback="(no title)") == "(no title)"


def test_empty_heading_falls_back():
    assert derive_title("#") == "Untitled"


# -- count_words -------------------------------------------------------------


def test_count_words_empty_is_zero():
    assert count_words("") == 0


def test_count_words_whitespace_only_is_zero():
    assert count_words("   \n\t\n  ") == 0


def test_count_words_simple_sentence():
    assert count_words("hello world") == 2


def test_count_words_collapses_runs_of_whitespace():
    # Multiple spaces, tabs, and newlines all act as a single separator.
    assert count_words("one   two\tthree\n\nfour") == 4


def test_count_words_ignores_lone_markdown_punctuation():
    # A heading marker, a bullet, and a horizontal rule are not words on their
    # own — only the actual text counts.
    assert count_words("# Heading") == 1
    assert count_words("- item one") == 2
    assert count_words("---") == 0


def test_count_words_counts_word_wrapped_in_markup_once():
    assert count_words("**bold** and _italic_") == 3


def test_count_words_counts_numbers_and_alphanumerics():
    assert count_words("page 42 of 100") == 4


def test_count_words_counts_unicode_letters():
    # str.isalnum() is Unicode-aware, so accented words still count.
    assert count_words("café déjà vu") == 3
