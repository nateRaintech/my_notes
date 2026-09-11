"""Behavioural tests for the tool suite's transformations (`core/tools/`).

Pure Python — no ``QApplication``, no vault, no repository. That is the payoff of
keeping the transformations in ``core/``: the whole suite is exercised in
milliseconds, and a broken tool fails here rather than in a Qt test that is
slower and harder to read.

The recurring assertions across every family are: correct output for good input,
a **located** ``ToolError`` for bad input, and — where it is meaningful —
idempotence, because a formatter that changes its own output on a second run
makes a note churn every time it is touched.
"""

import json

import pytest

from core.tools import ALL_TOOLS, CATEGORIES, ToolError, get_tool, search, tools_in
from core.tools import convert, encoding, json_tools, md_tools, text_tools, xml_tools


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_every_tool_id_is_unique():
    ids = [tool.id for tool in ALL_TOOLS]
    assert len(ids) == len(set(ids))


def test_every_tool_is_fully_described():
    for tool in ALL_TOOLS:
        assert tool.id and tool.name and tool.description, tool.id
        assert tool.category in CATEGORIES, f"{tool.id} has category {tool.category!r}"
        assert callable(tool.func), tool.id
        assert tool.mode in ("transform", "generate", "inspect"), tool.id


def test_every_category_has_at_least_one_tool():
    for category in CATEGORIES:
        assert tools_in(category), f"{category} is empty"


def test_get_tool_finds_by_id_and_rejects_unknown():
    assert get_tool("json.format").name == "Format JSON"
    with pytest.raises(ToolError, match="No such tool"):
        get_tool("json.nope")


def test_search_matches_name_category_and_keywords():
    # By name...
    assert get_tool("json.format") in search("format json")
    # ...by an abbreviation the fuzzy matcher can subsequence...
    assert get_tool("json.format") in search("fmtjsn")
    # ...and by a keyword that appears in neither the name nor the category.
    assert get_tool("insert.uuid") in search("guid")


def test_empty_search_returns_every_tool_in_registry_order():
    assert search("") == list(ALL_TOOLS)


def test_search_respects_its_limit():
    assert len(search("s", limit=5)) == 5


def test_run_wraps_an_unexpected_error_as_a_tool_error():
    """A tool that raises something other than ToolError must not crash the app."""
    from core.tools.base import Tool

    exploding = Tool(
        "test.boom", "Boom", "JSON", "raises",
        lambda _text: (_ for _ in ()).throw(RuntimeError("kaboom")),
    )
    with pytest.raises(ToolError, match="Boom failed: RuntimeError: kaboom"):
        exploding.run("anything")


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

MESSY_JSON = '{"b":2,"a":[1,2,{"c":null}]}'


def test_format_json_indents_with_two_spaces_and_keeps_key_order():
    assert json_tools.format_json('{"b":1,"a":2}') == '{\n  "b": 1,\n  "a": 2\n}'


def test_format_json_is_idempotent():
    once = json_tools.format_json(MESSY_JSON)
    assert json_tools.format_json(once) == once


def test_format_json_4_uses_four_spaces():
    assert json_tools.format_json_4('{"a":1}') == '{\n    "a": 1\n}'


def test_format_json_tab_uses_tabs():
    assert json_tools.format_json_tab('{"a":1}') == '{\n\t"a": 1\n}'


def test_minify_json_drops_every_optional_space():
    assert json_tools.minify_json('{\n  "a": [1, 2]\n}') == '{"a":[1,2]}'


def test_minify_then_format_round_trips():
    formatted = json_tools.format_json(MESSY_JSON)
    assert json_tools.format_json(json_tools.minify_json(formatted)) == formatted


def test_sort_json_keys_sorts_nested_objects_too():
    result = json_tools.sort_json_keys('{"b":1,"a":{"z":1,"y":2}}')
    assert json.loads(result) == {"a": {"y": 2, "z": 1}, "b": 1}
    assert result.index('"a"') < result.index('"b"')
    assert result.index('"y"') < result.index('"z"')


def test_format_json_keeps_non_ascii_readable():
    """ensure_ascii=False — an accented name stays legible in a note."""
    assert "café" in json_tools.format_json('{"name":"café"}')


def test_invalid_json_raises_with_a_line_and_column():
    with pytest.raises(ToolError) as excinfo:
        json_tools.format_json('{\n  "a": 1\n  "b": 2\n}')
    error = excinfo.value
    assert error.line == 3
    assert error.column is not None
    assert "Invalid JSON" in str(error)
    assert f"line {error.line}" in str(error)


def test_empty_selection_is_refused_rather_than_blanked():
    with pytest.raises(ToolError, match="Nothing to work with"):
        json_tools.format_json("   \n  ")


def test_validate_json_describes_the_document():
    assert "object with 2 keys" in json_tools.validate_json('{"a":1,"b":2}')
    assert "array of 3 items" in json_tools.validate_json("[1,2,3]")


def test_validate_json_reports_where_it_breaks():
    with pytest.raises(ToolError, match="Invalid JSON"):
        json_tools.validate_json("{oops}")


def test_json_string_escape_round_trips():
    raw = 'he said "hi"\nthen left\ttab'
    escaped = json_tools.escape_json_string(raw)
    assert escaped.startswith('"') and escaped.endswith('"')
    assert json_tools.unescape_json_string(escaped) == raw


def test_unescape_rejects_json_that_is_not_a_string():
    with pytest.raises(ToolError, match="Not a JSON string literal"):
        json_tools.unescape_json_string("[1, 2]")


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------

def test_format_xml_indents_nested_elements():
    result = xml_tools.format_xml("<root><a>hi</a><b/></root>")
    assert "  <a>hi</a>" in result
    assert "  <b/>" in result


def test_format_xml_is_idempotent():
    once = xml_tools.format_xml("<root><a><b>deep</b></a></root>")
    assert xml_tools.format_xml(once) == once


def test_format_xml_preserves_comments_and_attributes():
    result = xml_tools.format_xml('<root><!-- keep me --><a id="1"/></root>')
    assert "<!-- keep me -->" in result
    assert 'id="1"' in result


def test_minify_xml_collapses_to_one_line():
    result = xml_tools.minify_xml("<root>\n  <a>hi</a>\n</root>")
    assert "\n" not in result
    assert "<a>hi</a>" in result


def test_invalid_xml_raises_a_tool_error():
    with pytest.raises(ToolError, match="Invalid XML"):
        xml_tools.format_xml("<root><a></root>")


def test_xml_with_entity_declarations_is_refused():
    """A billion-laughs payload must not be expanded — it would hang the editor."""
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]>'
        "<lolz>&lol;</lolz>"
    )
    with pytest.raises(ToolError, match="entity declarations"):
        xml_tools.format_xml(bomb)


# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

def test_format_sql_upper_cases_keywords_and_breaks_clauses():
    result = get_tool("sql.format").run("select a from t where x=1")
    assert "SELECT" in result
    assert "FROM" in result
    assert "\n" in result


def test_minify_sql_collapses_to_one_line_and_drops_comments():
    result = get_tool("sql.minify").run("SELECT a -- a comment\nFROM t")
    assert "\n" not in result
    assert "comment" not in result


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def test_align_table_pads_every_column_to_the_same_width():
    result = md_tools.align_table("| a | bbbb |\n|---|---|\n| cccc | d |")
    widths = {len(line) for line in result.splitlines()}
    assert len(widths) == 1, result


def test_align_table_preserves_declared_column_alignment():
    """Centre and right markers survive; left normalises to Markdown's default."""
    result = md_tools.align_table(
        "| a | bbbbb | ccccc |\n|:--|:-:|--:|\n| 1 | 2 | 3 |"
    )
    header, separator, row = result.splitlines()
    assert separator.split("|")[1].strip() == "---"      # left -> the default marker
    assert separator.split("|")[2].strip().startswith(":")   # centre
    assert separator.split("|")[2].strip().endswith(":")
    assert separator.split("|")[3].strip().endswith(":")     # right
    # Padding follows the declared alignment: the left cell's filler sits after
    # its value, the right cell's before it.
    left_cell, right_cell = row.split("|")[1][1:-1], row.split("|")[3][1:-1]
    assert left_cell.startswith("1") and left_cell.endswith(" ")
    assert right_cell.startswith(" ") and right_cell.endswith("3")
    assert header.split("|")[2].strip() == "bbbbb"


def test_align_table_is_idempotent():
    once = md_tools.align_table("|a|bb|\n|---|---|\n|ccc|d|")
    assert md_tools.align_table(once) == once


def test_align_table_pads_ragged_rows_instead_of_failing():
    result = md_tools.align_table("| a | b | c |\n|---|---|---|\n| 1 |")
    assert len(result.splitlines()[2].split("|")) == len(
        result.splitlines()[0].split("|")
    )


def test_align_table_rejects_text_that_is_not_a_table():
    with pytest.raises(ToolError, match="Markdown table"):
        md_tools.align_table("just some prose\nwith no pipes")


def test_table_from_csv_uses_the_first_row_as_the_header():
    result = md_tools.table_from_delimited("name,role\nNate,lead")
    lines = result.splitlines()
    assert lines[0].startswith("| name")
    assert set(lines[1].replace("|", "").replace(" ", "")) == {"-"}
    assert "Nate" in lines[2]


def test_table_from_tsv_detects_the_tab_delimiter():
    result = md_tools.table_from_delimited("a\tb\n1\t2")
    assert "| a" in result and "| b" in result


def test_table_from_csv_respects_quoted_fields():
    result = md_tools.table_from_delimited('name,note\n"Doe, John",hi')
    assert "Doe, John" in result


def test_markdown_escape_round_trips():
    raw = "a *b* [c](d) # e"
    assert md_tools.unescape_markdown(md_tools.escape_markdown(raw)) == raw


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("encode", "decode"),
    [
        (encoding.base64_encode, encoding.base64_decode),
        (encoding.base64url_encode, encoding.base64url_decode),
        (encoding.url_encode, encoding.url_decode),
        (encoding.html_escape, encoding.html_unescape),
        (encoding.hex_encode, encoding.hex_decode),
        (encoding.hex_encode_spaced, encoding.hex_decode),
    ],
)
def test_encoders_round_trip(encode, decode):
    raw = "Hello, wörld! <a href=\"x\">& 100% ✓</a>"
    assert decode(encode(raw)) == raw


def test_base64_encode_matches_the_known_value():
    assert encoding.base64_encode("hello world") == "aGVsbG8gd29ybGQ="


def test_base64_decode_tolerates_missing_padding_and_line_breaks():
    assert encoding.base64_decode("aGVsbG8g\nd29ybGQ") == "hello world"


def test_base64url_encode_strips_padding():
    assert not encoding.base64url_encode("hello world").endswith("=")


def test_base64_decode_rejects_non_base64():
    with pytest.raises(ToolError, match="Not valid Base64"):
        encoding.base64_decode("!!!not base64!!!")


def test_base64_decode_refuses_bytes_that_are_not_utf8():
    """A decoder that 'succeeds' by corrupting its output is worse than one that refuses."""
    with pytest.raises(ToolError, match="isn't valid UTF-8"):
        encoding.base64_decode("//79")


def test_url_encode_escapes_slashes_too():
    assert encoding.url_encode("a/b") == "a%2Fb"


def test_url_decode_treats_plus_as_a_space():
    assert encoding.url_decode("a+b") == "a b"


def test_hex_decode_ignores_prefixes_whitespace_and_commas():
    assert encoding.hex_decode("0x68, 0x69") == "hi"


def test_hex_decode_rejects_an_odd_digit_count():
    with pytest.raises(ToolError, match="even number of digits"):
        encoding.hex_decode("abc")


JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ."
    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)


def test_jwt_decode_returns_header_and_payload_as_json():
    decoded = json.loads(encoding.jwt_decode(JWT))
    assert decoded["header"] == {"alg": "HS256", "typ": "JWT"}
    assert decoded["payload"]["name"] == "John Doe"


def test_jwt_decode_says_the_signature_is_unverified():
    """Decoding is not verification, and the output must never imply otherwise."""
    assert "NOT verified" in encoding.jwt_decode(JWT)


def test_jwt_decode_rejects_something_that_is_not_a_token():
    with pytest.raises(ToolError, match="expected 3 dot-separated parts"):
        encoding.jwt_decode("not.a.jwt.at.all")


@pytest.mark.parametrize(
    ("tool_id", "expected"),
    [
        ("hash.md5", "5d41402abc4b2a76b9719d911017c592"),
        ("hash.sha1", "aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d"),
        ("hash.sha256", "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"),
    ],
)
def test_hashes_match_the_known_digests_of_hello(tool_id, expected):
    assert expected in get_tool(tool_id).run("hello")


# ---------------------------------------------------------------------------
# Case
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("tool_id", "expected"),
    [
        ("case.camel", "helloWorldExample"),
        ("case.pascal", "HelloWorldExample"),
        ("case.snake", "hello_world_example"),
        ("case.kebab", "hello-world-example"),
        ("case.constant", "HELLO_WORLD_EXAMPLE"),
    ],
)
@pytest.mark.parametrize(
    "source",
    ["hello world example", "helloWorldExample", "hello_world_example", "Hello-World-Example"],
)
def test_identifier_case_converts_from_any_starting_case(tool_id, expected, source):
    assert get_tool(tool_id).run(source) == expected


def test_identifier_case_converts_each_line_independently():
    """A selected column of names converts name by name, not into one identifier."""
    assert text_tools.to_snake_case("First Name\nLast Name") == "first_name\nlast_name"


def test_identifier_case_splits_acronyms_sensibly():
    assert text_tools.to_snake_case("HTTPServerError") == "http_server_error"


def test_identifier_case_leaves_a_line_with_no_words_alone():
    assert text_tools.to_snake_case("---") == "---"


def test_title_case_does_not_mangle_apostrophes():
    """str.title() would produce "Don'T" — the apostrophe is part of the word."""
    assert text_tools.to_title("don't stop") == "Don't Stop"


def test_sentence_case_capitalises_after_terminal_punctuation():
    assert text_tools.to_sentence("HELLO there. HOW are you?") == (
        "Hello there. How are you?"
    )


def test_upper_and_lower_leave_structure_alone():
    assert text_tools.to_upper("a\nb") == "A\nB"
    assert text_tools.to_lower("A\nB") == "a\nb"


# ---------------------------------------------------------------------------
# Lines
# ---------------------------------------------------------------------------

def test_sort_lines_ascending_and_descending():
    assert text_tools.sort_lines_asc("c\na\nb") == "a\nb\nc"
    assert text_tools.sort_lines_desc("a\nc\nb") == "c\nb\na"


def test_sort_case_insensitively_interleaves_the_cases():
    assert text_tools.sort_lines_ci("b\nA\na\nB") == "A\na\nb\nB"


def test_sort_numerically_orders_by_value_not_by_digit():
    assert text_tools.sort_lines_numeric("10\n9\n100") == "9\n10\n100"


def test_sort_numerically_puts_non_numbers_last():
    assert text_tools.sort_lines_numeric("zebra\n2\n1") == "1\n2\nzebra"


def test_sort_naturally_orders_embedded_numbers():
    assert text_tools.sort_lines_natural("file10\nfile2\nfile1") == (
        "file1\nfile2\nfile10"
    )


def test_line_tools_preserve_a_trailing_newline():
    """Without this, every run of a line tool would quietly eat the final newline."""
    assert text_tools.sort_lines_asc("b\na\n") == "a\nb\n"
    assert text_tools.reverse_lines("a\nb\n") == "b\na\n"


def test_line_tools_do_not_invent_a_trailing_newline():
    assert text_tools.sort_lines_asc("b\na") == "a\nb"


def test_remove_duplicates_keeps_the_first_occurrence_and_the_order():
    assert text_tools.remove_duplicate_lines("b\na\nb\nc\na") == "b\na\nc"


def test_remove_blank_lines_drops_whitespace_only_lines_too():
    assert text_tools.remove_blank_lines("a\n\n   \nb") == "a\nb"


def test_trim_trailing_whitespace_leaves_indentation_alone():
    assert text_tools.trim_trailing_whitespace("  a  \n\tb\t") == "  a\n\tb"


def test_number_lines_right_aligns_the_numbers():
    numbered = text_tools.number_lines("\n".join("x" * 1 for _ in range(10)))
    lines = numbered.splitlines()
    assert lines[0].startswith(" 1. ")
    assert lines[9].startswith("10. ")


def test_join_lines_collapses_to_one_line():
    assert text_tools.join_lines_tool("a\n  b  \n\nc") == "a b c"


def test_wrap_keeps_paragraph_breaks_and_respects_the_width():
    source = ("word " * 40).strip() + "\n\n" + ("other " * 40).strip()
    wrapped = text_tools.wrap_lines(source)
    assert "\n\n" in wrapped
    assert max(len(line) for line in wrapped.splitlines()) <= 80


def test_text_stats_counts_what_it_claims_to():
    stats = text_tools.text_stats("Two words.\n\nAnother paragraph here.\n")
    assert "5 words" in stats
    assert "2 paragraphs" in stats


# ---------------------------------------------------------------------------
# Convert
# ---------------------------------------------------------------------------

def test_json_to_yaml_nests_sequences_under_their_key():
    result = convert.json_to_yaml('{"a":1,"b":[1,2]}')
    assert result == "a: 1\nb:\n  - 1\n  - 2"


def test_json_to_yaml_preserves_key_order():
    assert convert.json_to_yaml('{"z":1,"a":2}').startswith("z:")


def test_yaml_to_json_round_trips():
    original = '{"a": 1, "b": [1, 2], "c": {"d": "e"}}'
    assert json.loads(convert.yaml_to_json(convert.json_to_yaml(original))) == (
        json.loads(original)
    )


def test_yaml_to_json_reports_where_the_yaml_breaks():
    with pytest.raises(ToolError) as excinfo:
        convert.yaml_to_json("a: 1\n  b: [unclosed\n")
    assert "Invalid YAML" in str(excinfo.value)


def test_yaml_to_json_refuses_arbitrary_object_tags():
    """safe_load, not load — a converter must not become a code-execution primitive."""
    with pytest.raises(ToolError, match="Invalid YAML"):
        convert.yaml_to_json("!!python/object/apply:os.system ['echo pwned']")


def test_epoch_to_iso_reads_seconds():
    assert convert.epoch_to_iso("0").startswith("1970-01-01T00:00:00Z")


def test_epoch_to_iso_detects_milliseconds_by_magnitude():
    result = convert.epoch_to_iso("1516239022000")
    assert result.startswith("2018-01-18")
    assert "milliseconds" in result


def test_epoch_to_iso_rejects_text():
    with pytest.raises(ToolError, match="Not a Unix timestamp"):
        convert.epoch_to_iso("yesterday")


def test_iso_to_epoch_handles_the_zulu_suffix():
    assert convert.iso_to_epoch("1970-01-01T00:00:00Z").startswith("0 ")


def test_iso_to_epoch_accepts_a_space_separated_timestamp():
    assert convert.iso_to_epoch("1970-01-01 00:00:00+00:00").startswith("0 ")


def test_iso_to_epoch_rejects_a_non_timestamp():
    with pytest.raises(ToolError, match="Not an ISO-8601 timestamp"):
        convert.iso_to_epoch("last Tuesday")


def test_epoch_and_iso_round_trip():
    iso = convert.epoch_to_iso("1516239022").split()[0]
    assert convert.iso_to_epoch(iso).startswith("1516239022 ")


# ---------------------------------------------------------------------------
# Insert (generators)
# ---------------------------------------------------------------------------

def test_uuid_generator_produces_a_distinct_uuid_each_time():
    import uuid as uuid_module

    first, second = convert.new_uuid(), convert.new_uuid()
    assert first != second
    assert uuid_module.UUID(first).version == 4


def test_braced_uuid_is_upper_case_and_wrapped():
    value = convert.new_uuid_upper()
    assert value.startswith("{") and value.endswith("}")
    assert value.upper() == value


def test_generators_ignore_their_input():
    """A 'generate' tool must not read, echo, or choke on the selection."""
    generators = [tool for tool in ALL_TOOLS if tool.mode == "generate"]
    assert generators, "no generate-mode tools registered"
    for tool in generators:
        with_input = tool.run("some selected text")
        assert with_input, tool.id
        assert "some selected text" not in with_input, tool.id
        # And it works just as well on an empty selection.
        assert tool.run(""), tool.id


def test_today_is_an_iso_date():
    import datetime

    datetime.date.fromisoformat(convert.today())
