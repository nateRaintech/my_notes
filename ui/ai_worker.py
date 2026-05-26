"""Background worker for AI inference calls.

Running ``core.ai_client.chat`` on the main thread would freeze the UI for the
duration of the (potentially ~30 s) network round-trip.  :class:`AiWorker`
moves the call to a ``QThread`` so the window stays responsive.

Pattern
-------
``AiWorker`` is a ``QObject`` that lives on a dedicated ``QThread``.  The
caller:

1. Creates an ``AiWorker`` instance.
2. Moves it to a new ``QThread`` with ``worker.moveToThread(thread)``.
3. Connects ``thread.started`` → ``worker.run``.
4. Connects ``worker.finished`` / ``worker.error`` to UI slots.
5. Starts the thread.

:meth:`AiWorker.run` calls ``chat()`` and emits either ``finished(reply)``
on success or ``error(message)`` on any ``AIError``/other exception.

Testability seam
----------------
:meth:`AiWorker.run_with` accepts an injectable ``chat_fn`` so tests can pass
a mock without spinning a real thread or touching the network, mirroring the
``_post`` seam in ``core.ai_client``.

Per CLAUDE.md's strict layering, this module is UI; ``core/`` never imports it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    pass


class AiWorker(QObject):
    """Execute one ``ai_client.chat`` call on a background thread.

    Signals
    -------
    finished(str)
        Emitted with the assistant reply on success.
    error(str)
        Emitted with a human-readable error message on failure.
    """

    finished = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        api_key: str,
        messages: list[dict],
        *,
        timeout: float = 120.0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._api_key = api_key
        self._messages = messages
        self._timeout = timeout

    # -------------------------------------------------------------------------
    # Slots
    # -------------------------------------------------------------------------

    def run(self) -> None:
        """Call ``ai_client.chat`` and emit ``finished`` or ``error``.

        Designed to be connected to ``QThread.started`` so it runs on the
        worker thread.  Uses the real ``ai_client.chat`` implementation.
        """
        import core.ai_client as ai_client  # local import keeps it testable

        self.run_with(ai_client.chat)

    def run_with(self, chat_fn: Callable[[str, list[dict]], str]) -> None:
        """Call ``chat_fn(api_key, messages)`` and emit the appropriate signal.

        This is the **testability seam**: tests pass a mock ``chat_fn`` so the
        worker logic is exercised without a real network call or QThread.
        ``chat_fn`` must accept positional ``(api_key, messages)`` plus an
        optional ``timeout`` keyword — the same signature as
        :func:`core.ai_client.chat`.
        """
        try:
            reply = chat_fn(self._api_key, self._messages, timeout=self._timeout)
            self.finished.emit(reply)
        except Exception as exc:  # noqa: BLE001 — surface all errors to the UI
            self.error.emit(str(exc))
