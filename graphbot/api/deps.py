"""FastAPI dependency injection — pull singletons from app.state."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from graphbot.agent.runner import GraphRunner
from graphbot.core.config.schema import Config
from graphbot.memory.store import MemoryStore

_bearer_scheme = HTTPBearer(auto_error=False)


def get_config(request: Request) -> Config:
    """Get Config singleton from app state."""
    return request.app.state.config


def get_db(request: Request) -> MemoryStore:
    """Get MemoryStore singleton from app state."""
    return request.app.state.db


def get_runner(request: Request) -> GraphRunner:
    """Get GraphRunner singleton from app state."""
    return request.app.state.runner


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    x_api_key: str | None = Header(None),
) -> str:
    """Extract user_id from JWT token or API key.

    When auth is disabled (jwt_secret_key=""), returns owner_user_id or "default".
    """
    config: Config = request.app.state.config

    # Auth disabled → pass-through
    if not config.auth_enabled:
        return config.owner_user_id or "default"

    # 1. JWT Bearer token
    if credentials:
        from graphbot.api.auth import decode_token

        user_id = decode_token(
            credentials.credentials,
            config.auth.jwt_secret_key,
            config.auth.jwt_algorithm,
        )
        return user_id

    # 2. API key (X-API-Key header)
    if x_api_key:
        from graphbot.api.auth import verify_password

        db: MemoryStore = request.app.state.db
        with db._get_conn() as conn:
            rows = conn.execute(
                "SELECT key_id, user_id, key_hash FROM api_keys "
                "WHERE is_active = TRUE "
                "AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)"
            ).fetchall()

        for row in rows:
            if verify_password(x_api_key, row["key_hash"]):
                return row["user_id"]

        raise HTTPException(status_code=401, detail="Invalid API key")

    raise HTTPException(status_code=401, detail="Not authenticated")
