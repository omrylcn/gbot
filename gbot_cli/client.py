"""GraphBotClient — sync httpx wrapper for the GraphBot API."""

from __future__ import annotations

from typing import Any

import httpx


class APIError(Exception):
    """Raised when the API returns a non-2xx response."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class GraphBotClient:
    """Synchronous HTTP client for the GraphBot REST API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        token: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._api_key = api_key
        self._http = httpx.Client(base_url=self._base_url, timeout=timeout)

    # ── Internal ─────────────────────────────────────────────

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Send request with auth headers, raise APIError on failure."""
        headers = self._build_headers()
        headers.update(kwargs.pop("headers", {}))
        resp = self._http.request(method, path, headers=headers, **kwargs)
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise APIError(resp.status_code, detail)
        return resp.json()

    def set_token(self, token: str) -> None:
        """Update the bearer token."""
        self._token = token

    # ── Auth ─────────────────────────────────────────────────

    def login(self, user_id: str, password: str) -> dict:
        """POST /auth/login."""
        return self._request("POST", "/auth/login", json={"user_id": user_id, "password": password})

    # ── Health ───────────────────────────────────────────────

    def health(self) -> dict:
        """GET /health."""
        return self._request("GET", "/health")

    def server_status(self) -> dict:
        """GET /admin/status."""
        return self._request("GET", "/admin/status")

    # ── Chat ─────────────────────────────────────────────────

    def chat(self, message: str, session_id: str | None = None) -> dict:
        """POST /chat."""
        payload: dict[str, Any] = {"message": message}
        if session_id:
            payload["session_id"] = session_id
        return self._request("POST", "/chat", json=payload)

    # ── Sessions ─────────────────────────────────────────────

    def list_sessions(self, user_id: str, limit: int = 10) -> list:
        """GET /sessions/{user_id}."""
        return self._request("GET", f"/sessions/{user_id}", params={"limit": limit})

    def session_history(self, session_id: str) -> dict:
        """GET /session/{session_id}/history."""
        return self._request("GET", f"/session/{session_id}/history")

    def end_session(self, session_id: str) -> dict:
        """POST /session/{session_id}/end."""
        return self._request("POST", f"/session/{session_id}/end")

    # ── User ─────────────────────────────────────────────────

    def user_context(self, user_id: str) -> dict:
        """GET /user/{user_id}/context."""
        return self._request("GET", f"/user/{user_id}/context")

    def get_events(self, user_id: str) -> list:
        """GET /events/{user_id}."""
        data = self._request("GET", f"/events/{user_id}")
        return data.get("events", [])

    # ── Admin ────────────────────────────────────────────────

    def admin_config(self) -> dict:
        """GET /admin/config."""
        return self._request("GET", "/admin/config")

    def admin_users(self) -> list:
        """GET /admin/users."""
        return self._request("GET", "/admin/users")

    def admin_skills(self) -> list:
        """GET /admin/skills."""
        return self._request("GET", "/admin/skills")

    def admin_cron_jobs(self) -> list:
        """GET /admin/crons."""
        return self._request("GET", "/admin/crons")

    def admin_remove_cron(self, job_id: str) -> dict:
        """DELETE /admin/crons/{job_id}."""
        return self._request("DELETE", f"/admin/crons/{job_id}")

    def admin_logs(self, limit: int = 50) -> list:
        """GET /admin/logs."""
        return self._request("GET", "/admin/logs", params={"limit": limit})

    # ── Cleanup ──────────────────────────────────────────────

    def close(self) -> None:
        """Close underlying httpx client."""
        self._http.close()
