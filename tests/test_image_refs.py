"""Unit tests for ``core.image_refs`` — image references inside note Markdown.

Pure Python, no Qt: these pin the text-level contract the preview and the
resize/reset edits rely on. The key property is that ``find_refs`` lists exactly
the images the preview renders, in order, with spans that slice out the URL.
"""

import pytest

from core.image_refs import (
    ImageRef,
    clean_alt,
    find_refs,
    image_markdown,
    image_url,
    parse_url,
    referenced_ids,
    resolve_ref,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("mnimg:7?w=600", (7, 600)),
        ("mnimg:7", (7, None)),
        ("mnimg:7?w=0", (7, None)),
        ("mnimg:7?w=abc", (7, None)),
        ("mnimg:7?foo=1&w=300", (7, 300)),
        ("mnimg:x", None),
        ("mnimg:", None),
        ("http://example.com/a.png", None),
    ],
)
def test_parse_url(url, expected):
    assert parse_url(url) == expected


def test_image_url_round_trips():
    assert image_url(7) == "mnimg:7"
    assert image_url(7, 600) == "mnimg:7?w=600"
    assert parse_url(image_url(7, 600)) == (7, 600)


def test_clean_alt_removes_brackets_and_line_breaks():
    assert clean_alt("shot [1]\nfinal") == "shot 1 final"
    assert clean_alt("  ") == "image"


def test_image_markdown():
    assert image_markdown(3, "screenshot", 800) == "![screenshot](mnimg:3?w=800)"
    assert image_markdown(3, "a]b") == "![a b](mnimg:3)"


def _urls(markdown):
    return [markdown[r.url_start:r.url_end] for r in find_refs(markdown)]


def test_find_refs_spans_slice_out_the_url():
    md = "before ![s](mnimg:7?w=600) after"
    (ref,) = find_refs(md)
    assert ref == ImageRef(image_id=7, width=600, url_start=12, url_end=25)
    assert md[ref.url_start:ref.url_end] == "mnimg:7?w=600"


def test_find_refs_keeps_document_order_and_duplicates():
    md = "![a](mnimg:1?w=100)\n\ntext ![b](mnimg:1?w=300) ![c](mnimg:2)"
    assert [(r.image_id, r.width) for r in find_refs(md)] == [(1, 100), (1, 300), (2, None)]


def test_find_refs_offsets_are_right_on_later_lines():
    md = "line one\nline two ![s](mnimg:9)\n"
    assert _urls(md) == ["mnimg:9"]


def test_find_refs_skips_fenced_code_blocks():
    md = "```\n![s](mnimg:1)\n```\n![s](mnimg:2)\n~~~md\n![s](mnimg:3)\n~~~\n"
    assert [r.image_id for r in find_refs(md)] == [2]


def test_find_refs_fence_needs_a_matching_closer():
    # A ~~~ line does not close a ``` fence, and an unclosed fence runs to the end.
    md = "```\n~~~\n![s](mnimg:1)\n"
    assert find_refs(md) == []


def test_find_refs_skips_inline_code():
    md = "`![s](mnimg:1)` and ![s](mnimg:2)"
    assert [r.image_id for r in find_refs(md)] == [2]


def test_find_refs_ignores_other_images_and_malformed_ids():
    md = "![a](http://x/y.png) ![b](mnimg:abc) ![c](mnimg:4)"
    assert [r.image_id for r in find_refs(md)] == [4]


def test_resolve_ref_uses_the_ordinal_when_the_id_matches():
    md = "![a](mnimg:1?w=100) ![b](mnimg:1?w=100)"
    assert resolve_ref(md, 1, 1, 100) == find_refs(md)[1]


def test_resolve_ref_falls_back_to_the_unique_id_and_width_match():
    md = "![a](mnimg:1?w=100) ![b](mnimg:2?w=50)"
    # The ordinal is wrong (the preview counted something find_refs didn't), but
    # only one ref has id 2 at width 50, so it is still unambiguous.
    assert resolve_ref(md, 0, 2, 50) == find_refs(md)[1]


def test_resolve_ref_declines_when_ambiguous():
    md = "![a](mnimg:1?w=100) ![b](mnimg:1?w=100)"
    assert resolve_ref(md, 5, 1, 100) is None


def test_resolve_ref_declines_when_absent():
    assert resolve_ref("no images", 0, 1, None) is None


def test_referenced_ids_is_liberal():
    md = "```\n![s](mnimg:1)\n```\n![s](mnimg:2)\nbare mnimg:3 mention"
    assert referenced_ids(md) == {1, 2, 3}
