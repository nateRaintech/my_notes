"""Dockable AI chat panel widget.

:class:`AiChatPanel` is a self-contained ``QWidget`` that presents:

* A read-only conversation view (``QTextEdit``, rendered as Markdown).
* A multiline input (``QPlainTextEdit``).
* Buttons: **Send**, **Cancel**, **Clear**, **Save as note**.
* A status / "Thinking…" label.

Decoupled via injected seams
-----------------------------
The panel is deliberately decoupled from the network and the repository so it
is unit-testable without a real event loop, a real AI endpoint, or a real
vault.  Two callable seams are injected at construction time (or set
afterwards):

``run_chat_fn``
    Called with the current ``messages`` list
    ``(list[dict]) -> None``.  MainWindow injects a closure that spins an
    :class:`~ui.ai_worker.AiWorker` on a ``QThread``; tests inject a simple
    lambda that immediately calls :meth:`_on_reply` or :meth:`_on_error`.

``save_note_fn``
    Called with the conversation's Markdown text ``(str) -> None``.  MainWindow
    injects a closure over ``repository.create_note``; tests inject a simple
    recorder.

Both default to ``None``; if a seam is ``None`` when the corresponding button
is pressed, the action is silently skipped (guarded by the caller).

Cancel behaviour
----------------
Pressing **Cancel** while a request is in-flight sets ``_cancelled = True``,
immediately re-enables the input widgets and clears the "Thinking…" label.
The eventual ``_on_reply`` / ``_on_error`` callback checks ``_cancelled`` and
discards the result.  The underlying network call may still complete in the
background — truly killing a ``urllib`` request mid-flight is out of scope.

Slice-4 seam
------------
:meth:`start_with_context` seeds the conversation with a context snippet and
an optional user prompt, then sends the first turn.  Slice 4 ("Analyze note /
selection") calls this method; the panel is already wired in MainWindow.

Per CLAUDE.md's strict layering, ``core/`` is never imported here; only
``core.conversation`` is used (pure Python, zero Qt).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.conversation import Conversation


class AiChatPanel(QWidget):
    """The AI chat panel: conversation view, input, and action buttons.

    Parameters
    ----------
    run_chat_fn:
        Injected seam — called with the current message list when **Send** is
        pressed.  Signature: ``(list[dict]) -> None``.  MainWindow wires this
        to a closure that creates an :class:`~ui.ai_worker.AiWorker`.
    save_note_fn:
        Injected seam — called with the Markdown text of the conversation
        when **Save as note** is pressed.  Signature: ``(str) -> None``.
    parent:
        Optional parent widget.

    Public seams for headless testing (no modal loop / real network needed):

    * :meth:`send`              — append a user turn and invoke ``run_chat_fn``.
    * :meth:`_on_reply`         — append an assistant turn, clear "Thinking…".
    * :meth:`_on_error`         — show an error message, clear "Thinking…".
    * :meth:`clear`             — reset the conversation.
    * :meth:`save_as_note`      — call ``save_note_fn`` with the Markdown text.
    * :meth:`start_with_context` — seed + send (slice-4 entry point).
    * :attr:`conversation`      — the underlying :class:`~core.conversation.Conversation`.
    * :attr:`input_edit`        — the ``QPlainTextEdit`` input field.
    * :attr:`conversation_view` — the read-only ``QTextEdit`` view.
    * :attr:`status_label`      — the "Thinking…" / error label.
    * :attr:`send_button`, :attr:`cancel_button`, :attr:`clear_button`,
      :attr:`save_button`       — the four action buttons.
    """

    def __init__(
        self,
        *,
        run_chat_fn: Callable[[list[dict]], None] | None = None,
        save_note_fn: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        self.conversation = Conversation()
        self._cancelled = False
        self._pending = False

        # Injected seams — may be replaced after construction.
        self.run_chat_fn = run_chat_fn
        self.save_note_fn = save_note_fn

        # --- Conversation view -----------------------------------------------

        self.conversation_view = QTextEdit()
        self.conversation_view.setReadOnly(True)
        self.conversation_view.setPlaceholderText("Conversation will appear here…")

        # --- Input -----------------------------------------------------------

        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText("Type a message…")
        self.input_edit.setMaximumHeight(80)
        self.input_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

        # --- Buttons ---------------------------------------------------------

        self.send_button = QPushButton("Send")
        self.send_button.setDefault(True)
        self.send_button.clicked.connect(self._on_send_clicked)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._on_cancel_clicked)

        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear)

        self.save_button = QPushButton("Save as note")
        self.save_button.clicked.connect(self.save_as_note)

        button_row = QHBoxLayout()
        button_row.addWidget(self.send_button)
        button_row.addWidget(self.cancel_button)
        button_row.addWidget(self.clear_button)
        button_row.addWidget(self.save_button)

        # --- Status label ----------------------------------------------------

        self.status_label = QLabel()
        self.status_label.setObjectName("aiChatStatus")
        self.status_label.setWordWrap(True)

        # --- Layout ----------------------------------------------------------

        layout = QVBoxLayout(self)
        layout.addWidget(self.conversation_view, stretch=1)
        layout.addWidget(self.input_edit)
        layout.addLayout(button_row)
        layout.addWidget(self.status_label)

    # -------------------------------------------------------------------------
    # Public seams
    # -------------------------------------------------------------------------

    def send(self, text: str) -> None:
        """Append ``text`` as a user turn and invoke ``run_chat_fn``.

        Sets the panel to "Thinking…" state, disables the input, and calls the
        injected ``run_chat_fn`` with the current message list.  If
        ``run_chat_fn`` is ``None`` (not yet wired), the turn is appended but
        no request is made.

        This is the primary entry point for tests: inject a ``run_chat_fn``
        that immediately calls :meth:`_on_reply` to drive the panel without a
        real event loop.
        """
        stripped = text.strip()
        if not stripped:
            return
        self._cancelled = False
        self._pending = True
        self.conversation.add_user(stripped)
        self._refresh_view()
        self._set_thinking(True)
        if self.run_chat_fn is not None:
            self.run_chat_fn(self.conversation.messages)

    def _on_reply(self, text: str) -> None:
        """Append ``text`` as an assistant turn and clear "Thinking…".

        Called by MainWindow's ``AiWorker.finished`` connection (or by tests).
        Discarded silently when a Cancel is pending.
        """
        if self._cancelled:
            return
        self._pending = False
        self.conversation.add_assistant(text)
        self._refresh_view()
        self._set_thinking(False)

    def _on_error(self, message: str) -> None:
        """Show ``message`` as an error in the status label and clear Thinking.

        Called by MainWindow's ``AiWorker.error`` connection (or by tests).
        Discarded silently when a Cancel is pending.
        """
        if self._cancelled:
            return
        self._pending = False
        self.status_label.setText(f"Error: {message}")
        self._set_thinking(False)

    def clear(self) -> None:
        """Reset the conversation to empty and clear the view."""
        self.conversation.clear()
        self._refresh_view()
        self.status_label.clear()

    def save_as_note(self) -> None:
        """Call ``save_note_fn`` with the conversation's Markdown text.

        No-op when ``save_note_fn`` is ``None`` or the conversation is empty.
        """
        if self.save_note_fn is None:
            return
        md = self.conversation.to_markdown()
        if not md:
            return
        self.save_note_fn(md)

    def start_with_context(self, context_text: str, user_prompt: str = "") -> None:
        """Seed the conversation with ``context_text`` and send the first turn.

        Used by slice 4 (Analyze selection / note).  ``context_text`` is the
        text of the selection or note to analyse.  ``user_prompt`` is the
        user's question; if blank, a default "Please summarise this." prompt
        is used.

        The context is prepended to the prompt so the AI receives it all in a
        single user turn: callers don't need to know about the underlying
        message structure.

        Clears any prior conversation before seeding.
        """
        self.clear()
        prompt = user_prompt.strip() or "Please summarise this."
        combined = f"{context_text.strip()}\n\n{prompt}" if context_text.strip() else prompt
        self.send(combined)

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _on_send_clicked(self) -> None:
        text = self.input_edit.toPlainText()
        self.input_edit.clear()
        self.send(text)

    def _on_cancel_clicked(self) -> None:
        """Abandon the in-flight request.

        Sets ``_cancelled`` so the eventual ``_on_reply`` / ``_on_error``
        result is discarded.  The underlying urllib network call may continue
        in the background (stopping it mid-flight is out of scope).
        """
        self._cancelled = True
        self._pending = False
        self._set_thinking(False)

    def _set_thinking(self, thinking: bool) -> None:
        """Toggle the "Thinking…" state: disable/enable input and buttons."""
        self.send_button.setEnabled(not thinking)
        self.cancel_button.setEnabled(thinking)
        self.input_edit.setEnabled(not thinking)
        if thinking:
            self.status_label.setText("Thinking…")
        else:
            # Only clear "Thinking…"; error messages are set by _on_error.
            if self.status_label.text() == "Thinking…":
                self.status_label.clear()

    def _refresh_view(self) -> None:
        """Re-render the conversation into the read-only view."""
        self.conversation_view.setMarkdown(self.conversation.to_markdown())
