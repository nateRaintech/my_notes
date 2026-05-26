"""Ordered conversation model for the AI chat panel.

Pure Python, no Qt.  A :class:`Conversation` holds an ordered list of
``{"role": "user"|"assistant", "content": str}`` message dicts — exactly the
format :func:`core.ai_client.chat` (and the underlying OpenAI-style API)
expects.

Public API
----------
:meth:`add_user`      — append a user turn.
:meth:`add_assistant` — append an assistant turn.
:meth:`clear`         — reset to an empty conversation.
:attr:`messages`      — read-only view of the list (a copy, safe to pass to the AI client).
:meth:`to_markdown`   — render the full exchange as Markdown for "Save as note".

Design constraint: this module must stay Qt-free so it can be unit-tested
without a QApplication and imported from core without violating the layering
rule in CLAUDE.md.
"""

from __future__ import annotations


class Conversation:
    """An ordered sequence of user/assistant message turns.

    Each turn is a ``dict`` with ``"role"`` (``"user"`` or ``"assistant"``)
    and ``"content"`` (the text of that turn).

    Typical lifecycle::

        conv = Conversation()
        conv.add_user("Hello!")
        # ... send conv.messages to ai_client.chat ...
        conv.add_assistant("Hi there!")
        md = conv.to_markdown()   # "**You:** Hello!\\n\\n**AI:** Hi there!"
        conv.clear()              # back to empty
    """

    def __init__(self) -> None:
        self._messages: list[dict[str, str]] = []

    # -------------------------------------------------------------------------
    # Mutation
    # -------------------------------------------------------------------------

    def add_user(self, text: str) -> None:
        """Append a user turn with ``text`` as the content."""
        self._messages.append({"role": "user", "content": text})

    def add_assistant(self, text: str) -> None:
        """Append an assistant turn with ``text`` as the content."""
        self._messages.append({"role": "assistant", "content": text})

    def clear(self) -> None:
        """Remove all turns, resetting the conversation to empty."""
        self._messages.clear()

    # -------------------------------------------------------------------------
    # Read
    # -------------------------------------------------------------------------

    @property
    def messages(self) -> list[dict[str, str]]:
        """A shallow copy of the message list, safe to pass to the AI client.

        Returns a new list each call so callers cannot mutate internal state.
        """
        return list(self._messages)

    def to_markdown(self) -> str:
        """Render the conversation as a Markdown string.

        Each user turn is rendered as ``**You:** <text>`` and each assistant
        turn as ``**AI:** <text>``.  Turns are separated by a blank line.

        Returns an empty string for an empty conversation.
        """
        parts: list[str] = []
        for msg in self._messages:
            role = msg["role"]
            content = msg["content"]
            if role == "user":
                parts.append(f"**You:** {content}")
            else:
                parts.append(f"**AI:** {content}")
        return "\n\n".join(parts)
