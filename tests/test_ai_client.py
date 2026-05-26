"""Tests for ``core.ai_client`` — all network I/O is replaced via monkeypatch.

Every test patches ``core.ai_client._post`` so no real HTTP calls are made.
The seam returns ``(status_code, body_text)``, matching the real implementation.

Pure Python, no Qt.
"""

import json
import socket
import urllib.error

import pytest

import core.ai_client as ai_client
from core.ai_client import (
    AIAuthError,
    AIError,
    AINetworkError,
    AITimeoutError,
    chat,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MESSAGES = [{"role": "user", "content": "Reply with OK"}]
_KEY = "test-api-key"


def _ok_body(content: str, reasoning: str | None = None) -> str:
    """Build a minimal valid response body."""
    msg: dict = {"content": content}
    if reasoning is not None:
        msg["reasoning"] = reasoning
    return json.dumps({"choices": [{"message": msg}]})


# ---------------------------------------------------------------------------
# Success cases
# ---------------------------------------------------------------------------


def test_chat_returns_content_on_200(monkeypatch):
    monkeypatch.setattr(ai_client, "_post", lambda *a, **kw: (200, _ok_body("Hello!")))
    result = chat(_KEY, _MESSAGES)
    assert result == "Hello!"


def test_chat_ignores_reasoning_field(monkeypatch):
    body = _ok_body("just the content", reasoning="some chain-of-thought")
    monkeypatch.setattr(ai_client, "_post", lambda *a, **kw: (200, body))
    result = chat(_KEY, _MESSAGES)
    assert result == "just the content"


def test_chat_passes_model_and_messages_in_payload(monkeypatch):
    """Verify the outgoing payload contains the configured model and messages."""
    captured = {}

    def fake_post(url, *, headers, payload, timeout):
        captured["payload"] = json.loads(payload)
        captured["headers"] = headers
        return 200, _ok_body("OK")

    monkeypatch.setattr(ai_client, "_post", fake_post)
    chat(_KEY, _MESSAGES, timeout=5.0)

    assert captured["payload"]["model"] == ai_client.AI_MODEL
    assert captured["payload"]["messages"] == _MESSAGES
    assert captured["headers"]["X-API-Key"] == _KEY
    assert captured["headers"]["Content-Type"] == "application/json"


def test_chat_passes_timeout_to_post(monkeypatch):
    captured = {}

    def fake_post(url, *, headers, payload, timeout):
        captured["timeout"] = timeout
        return 200, _ok_body("OK")

    monkeypatch.setattr(ai_client, "_post", fake_post)
    chat(_KEY, _MESSAGES, timeout=42.0)
    assert captured["timeout"] == 42.0


# ---------------------------------------------------------------------------
# HTTP error mapping
# ---------------------------------------------------------------------------


def test_401_raises_ai_auth_error(monkeypatch):
    monkeypatch.setattr(ai_client, "_post", lambda *a, **kw: (401, "Unauthorized"))
    with pytest.raises(AIAuthError):
        chat(_KEY, _MESSAGES)


def test_403_raises_ai_auth_error(monkeypatch):
    monkeypatch.setattr(ai_client, "_post", lambda *a, **kw: (403, "Forbidden"))
    with pytest.raises(AIAuthError):
        chat(_KEY, _MESSAGES)


def test_non_auth_http_error_raises_ai_error(monkeypatch):
    monkeypatch.setattr(ai_client, "_post", lambda *a, **kw: (500, "Server error"))
    with pytest.raises(AIError) as exc_info:
        chat(_KEY, _MESSAGES)
    assert "500" in str(exc_info.value)
    # Must NOT be an AIAuthError subclass.
    assert not isinstance(exc_info.value, AIAuthError)


# ---------------------------------------------------------------------------
# Network / timeout errors raised by _post
# ---------------------------------------------------------------------------


def test_url_error_raises_ai_network_error(monkeypatch):
    def fake_post(*a, **kw):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(ai_client, "_post", fake_post)
    with pytest.raises(AINetworkError):
        chat(_KEY, _MESSAGES)


def test_url_error_wrapping_socket_timeout_raises_ai_timeout_error(monkeypatch):
    def fake_post(*a, **kw):
        raise urllib.error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr(ai_client, "_post", fake_post)
    with pytest.raises(AITimeoutError):
        chat(_KEY, _MESSAGES)


def test_socket_timeout_raises_ai_timeout_error(monkeypatch):
    def fake_post(*a, **kw):
        raise socket.timeout("timed out")

    monkeypatch.setattr(ai_client, "_post", fake_post)
    with pytest.raises(AITimeoutError):
        chat(_KEY, _MESSAGES)


# ---------------------------------------------------------------------------
# Malformed response body
# ---------------------------------------------------------------------------


def test_invalid_json_body_raises_ai_error(monkeypatch):
    monkeypatch.setattr(ai_client, "_post", lambda *a, **kw: (200, "not json at all"))
    with pytest.raises(AIError):
        chat(_KEY, _MESSAGES)


def test_missing_choices_key_raises_ai_error(monkeypatch):
    monkeypatch.setattr(ai_client, "_post", lambda *a, **kw: (200, json.dumps({"id": "x"})))
    with pytest.raises(AIError):
        chat(_KEY, _MESSAGES)


def test_empty_choices_list_raises_ai_error(monkeypatch):
    monkeypatch.setattr(ai_client, "_post", lambda *a, **kw: (200, json.dumps({"choices": []})))
    with pytest.raises(AIError):
        chat(_KEY, _MESSAGES)


def test_missing_content_field_raises_ai_error(monkeypatch):
    body = json.dumps({"choices": [{"message": {"role": "assistant"}}]})
    monkeypatch.setattr(ai_client, "_post", lambda *a, **kw: (200, body))
    with pytest.raises(AIError):
        chat(_KEY, _MESSAGES)


def test_empty_body_raises_ai_error(monkeypatch):
    monkeypatch.setattr(ai_client, "_post", lambda *a, **kw: (200, ""))
    with pytest.raises(AIError):
        chat(_KEY, _MESSAGES)


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------


def test_ai_auth_error_is_subclass_of_ai_error():
    assert issubclass(AIAuthError, AIError)


def test_ai_network_error_is_subclass_of_ai_error():
    assert issubclass(AINetworkError, AIError)


def test_ai_timeout_error_is_subclass_of_ai_error():
    assert issubclass(AITimeoutError, AIError)
