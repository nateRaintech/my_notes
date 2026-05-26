"""Unit tests for core.conversation.Conversation.

Pure Python, no Qt needed — the module is deliberately Qt-free.

Covers:
- add_user / add_assistant append turns in order.
- messages returns a copy of the list (safe to mutate).
- clear resets to empty.
- to_markdown renders the correct Markdown string.
- to_markdown on an empty conversation returns "".
"""

from core.conversation import Conversation


# ---------------------------------------------------------------------------
# add_user / add_assistant
# ---------------------------------------------------------------------------


def test_add_user_appends_user_turn():
    conv = Conversation()
    conv.add_user("Hello")
    assert conv.messages == [{"role": "user", "content": "Hello"}]


def test_add_assistant_appends_assistant_turn():
    conv = Conversation()
    conv.add_assistant("Hi there!")
    assert conv.messages == [{"role": "assistant", "content": "Hi there!"}]


def test_add_user_then_assistant_preserves_order():
    conv = Conversation()
    conv.add_user("Q1")
    conv.add_assistant("A1")
    conv.add_user("Q2")
    assert conv.messages == [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "Q2"},
    ]


# ---------------------------------------------------------------------------
# messages — returns a copy
# ---------------------------------------------------------------------------


def test_messages_returns_copy():
    conv = Conversation()
    conv.add_user("test")
    msgs = conv.messages
    msgs.append({"role": "assistant", "content": "injected"})
    # Original must be unchanged.
    assert len(conv.messages) == 1


def test_messages_empty_on_new_conversation():
    conv = Conversation()
    assert conv.messages == []


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


def test_clear_resets_to_empty():
    conv = Conversation()
    conv.add_user("hello")
    conv.add_assistant("world")
    conv.clear()
    assert conv.messages == []


def test_clear_on_empty_is_no_op():
    conv = Conversation()
    conv.clear()  # must not raise
    assert conv.messages == []


# ---------------------------------------------------------------------------
# to_markdown
# ---------------------------------------------------------------------------


def test_to_markdown_empty_returns_empty_string():
    conv = Conversation()
    assert conv.to_markdown() == ""


def test_to_markdown_single_user_turn():
    conv = Conversation()
    conv.add_user("Hello")
    assert conv.to_markdown() == "**You:** Hello"


def test_to_markdown_single_assistant_turn():
    conv = Conversation()
    conv.add_assistant("Hi!")
    assert conv.to_markdown() == "**AI:** Hi!"


def test_to_markdown_user_then_assistant():
    conv = Conversation()
    conv.add_user("What is 2+2?")
    conv.add_assistant("4")
    assert conv.to_markdown() == "**You:** What is 2+2?\n\n**AI:** 4"


def test_to_markdown_multiple_turns():
    conv = Conversation()
    conv.add_user("Q1")
    conv.add_assistant("A1")
    conv.add_user("Q2")
    conv.add_assistant("A2")
    expected = "**You:** Q1\n\n**AI:** A1\n\n**You:** Q2\n\n**AI:** A2"
    assert conv.to_markdown() == expected


def test_to_markdown_after_clear_returns_empty():
    conv = Conversation()
    conv.add_user("hi")
    conv.add_assistant("there")
    conv.clear()
    assert conv.to_markdown() == ""
