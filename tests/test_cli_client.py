"""Tests for graphbot.cli.client (Faz 16)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from gbot_cli.client import APIError, GraphBotClient


def _mock_response(status_code: int = 200, json_data: dict | list | None = None):
    """Build a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = str(json_data)
    return resp


def test_health():
    """Client.health() calls GET /health."""
    client = GraphBotClient()
    with patch.object(client._http, "request", return_value=_mock_response(200, {"status": "ok"})):
        result = client.health()
    assert result["status"] == "ok"
    client.close()


def test_chat():
    """Client.chat() posts message and returns response."""
    client = GraphBotClient()
    payload = {"response": "Merhaba!", "session_id": "s1"}
    with patch.object(client._http, "request", return_value=_mock_response(200, payload)):
        result = client.chat("selam", session_id="s1")
    assert result["response"] == "Merhaba!"
    client.close()


def test_login():
    """Client.login() posts credentials."""
    client = GraphBotClient()
    payload = {"success": True, "token": "jwt-abc"}
    with patch.object(client._http, "request", return_value=_mock_response(200, payload)):
        result = client.login("user1", "pass")
    assert result["token"] == "jwt-abc"
    client.close()


def test_auth_header_bearer():
    """Bearer token is sent when set."""
    client = GraphBotClient(token="my-token")
    headers = client._build_headers()
    assert headers["Authorization"] == "Bearer my-token"
    client.close()


def test_auth_header_api_key():
    """X-API-Key header is sent when set."""
    client = GraphBotClient(api_key="key-123")
    headers = client._build_headers()
    assert headers["X-API-Key"] == "key-123"
    client.close()


def test_error_handling():
    """APIError raised on 4xx/5xx."""
    client = GraphBotClient()
    resp = _mock_response(404, {"detail": "Not found"})
    with patch.object(client._http, "request", return_value=resp):
        with pytest.raises(APIError) as exc_info:
            client.health()
    assert exc_info.value.status_code == 404
    assert "Not found" in exc_info.value.detail
    client.close()


def test_session_ops():
    """Session list, history, end work correctly."""
    client = GraphBotClient()
    with patch.object(
        client._http, "request",
        return_value=_mock_response(200, [{"session_id": "s1"}]),
    ):
        sessions = client.list_sessions("u1")
    assert len(sessions) == 1

    with patch.object(
        client._http, "request",
        return_value=_mock_response(200, {"session_id": "s1", "messages": []}),
    ):
        hist = client.session_history("s1")
    assert hist["session_id"] == "s1"

    with patch.object(
        client._http, "request",
        return_value=_mock_response(200, {"status": "closed"}),
    ):
        ended = client.end_session("s1")
    assert ended["status"] == "closed"
    client.close()


def test_set_token():
    """set_token updates the bearer token."""
    client = GraphBotClient()
    assert client._token is None
    client.set_token("new-token")
    assert client._build_headers()["Authorization"] == "Bearer new-token"
    client.close()
