"""AI inference client — pure Python, no Qt, no new dependencies.

Calls the RainTech inference endpoint using only ``urllib.request`` from the
stdlib.  The API key is accepted as a plain string but is **never logged** —
callers obtain it from :meth:`~core.repository.Repository.get_api_key` and
pass it straight here without touching the UI.

Error hierarchy
---------------
``AIError``          — base; also raised for unexpected HTTP status or malformed body.
``AIAuthError``      — HTTP 401 or 403 (bad / expired key).
``AINetworkError``   — network-level failure (``urllib.error.URLError``).
``AITimeoutError``   — request timed out (``socket.timeout``).

Testability seam
----------------
All network I/O is isolated in :func:`_post`, which returns ``(status_code,
body_text)``.  Tests monkeypatch ``ai_client._post`` to inject canned responses
or errors without touching the real network.  ``chat()`` calls nothing else.

Pure Python, no Qt: ``core/`` is the unit-testable layer (CLAUDE.md).
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

AI_ENDPOINT = "https://inference.rain.tech/v1/chat/completions"
AI_MODEL = "gpt-oss:120b"
# The model is slow; give it plenty of time before giving up.
DEFAULT_TIMEOUT = 120.0

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AIError(Exception):
    """Base class for all AI client errors."""


class AIAuthError(AIError):
    """HTTP 401 or 403 — bad or missing API key."""


class AINetworkError(AIError):
    """Network-level failure (DNS, connection refused, etc.)."""


class AITimeoutError(AIError):
    """The request timed out before a response was received."""


# ---------------------------------------------------------------------------
# Network seam (monkeypatched by tests)
# ---------------------------------------------------------------------------


def _post(url: str, *, headers: dict[str, str], payload: bytes, timeout: float) -> tuple[int, str]:
    """POST ``payload`` to ``url`` and return ``(status_code, body_text)``.

    This is the **only** function that touches the network.  Tests replace it
    with a monkeypatch so ``chat()`` is exercisable without real HTTP calls.
    The api_key travels inside ``headers`` and is never logged here.
    """
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        # HTTPError carries the response body; re-read it as text.
        try:
            body = exc.read().decode("utf-8")
        except Exception:
            body = ""
        return exc.code, body


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def chat(
    api_key: str,
    messages: list[dict],
    *,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Send ``messages`` to the AI endpoint and return the assistant reply.

    Parameters
    ----------
    api_key:
        The stored API key.  Never logged.
    messages:
        OpenAI-style message list, e.g. ``[{"role": "user", "content": "Hi"}]``.
    timeout:
        Seconds before giving up (default :data:`DEFAULT_TIMEOUT`).

    Returns
    -------
    str
        The text of ``choices[0].message.content`` from the JSON response.
        Any ``message.reasoning`` field is silently ignored.

    Raises
    ------
    AIAuthError
        HTTP 401 or 403.
    AINetworkError
        Any ``urllib.error.URLError`` that is not a timeout.
    AITimeoutError
        ``socket.timeout`` or a ``urllib.error.URLError`` wrapping one.
    AIError
        Any other HTTP error, or a malformed / empty response body.
    """
    payload = json.dumps({"model": AI_MODEL, "messages": messages}).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        # api_key intentionally NOT logged anywhere in this module.
        "X-API-Key": api_key,
    }

    try:
        status, body = _post(AI_ENDPOINT, headers=headers, payload=payload, timeout=timeout)
    except socket.timeout as exc:
        raise AITimeoutError("request timed out") from exc
    except urllib.error.URLError as exc:
        # URLError wraps socket.timeout on some Python versions.
        if isinstance(exc.reason, socket.timeout):
            raise AITimeoutError("request timed out") from exc
        raise AINetworkError(f"network error: {exc.reason}") from exc

    if status in (401, 403):
        raise AIAuthError(f"authentication failed (HTTP {status})")
    if status != 200:
        raise AIError(f"unexpected HTTP {status}")

    # Parse the response body.
    try:
        data = json.loads(body)
        content = data["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise AIError(f"malformed response body: {exc}") from exc

    if not isinstance(content, str):
        raise AIError("response content is not a string")

    return content
