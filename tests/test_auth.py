"""Tests for auth & API security (Faz 11)."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage

from graphbot.api.app import create_app
from graphbot.api.auth import create_access_token, hash_password, verify_password
from graphbot.core.config import Config
from graphbot.memory.store import MemoryStore


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    return MemoryStore(str(tmp_path / "test.db"))


@pytest.fixture
def _app_no_auth(tmp_path):
    """App with auth DISABLED (default)."""
    config = Config(
        assistant={"system_prompt": "TestBot.", "owner": {"username": "owner", "name": "Owner"}},
        database={"path": str(tmp_path / "test.db")},
    )
    application = create_app()
    db = MemoryStore(str(tmp_path / "test.db"))
    db.get_or_create_user("owner", name="Owner")

    ai_msg = AIMessage(content="ok", response_metadata={"usage": {"total_tokens": 10}})
    with patch("graphbot.agent.nodes.llm_provider.achat", new_callable=AsyncMock, return_value=ai_msg):
        from graphbot.agent.runner import GraphRunner
        runner = GraphRunner(config, db)

    application.state.config = config
    application.state.db = db
    application.state.runner = runner
    return application


@pytest.fixture
def _app_with_auth(tmp_path):
    """App with auth ENABLED (jwt_secret_key set)."""
    config = Config(
        assistant={"system_prompt": "TestBot.", "owner": {"username": "owner", "name": "Owner"}},
        auth={"jwt_secret_key": "test-secret-key", "access_token_expire_minutes": 60},
        database={"path": str(tmp_path / "test.db")},
    )
    application = create_app()
    db = MemoryStore(str(tmp_path / "test.db"))
    db.get_or_create_user("owner", name="Owner")
    db.set_password("owner", hash_password("ownerpass"))

    ai_msg = AIMessage(content="ok", response_metadata={"usage": {"total_tokens": 10}})
    with patch("graphbot.agent.nodes.llm_provider.achat", new_callable=AsyncMock, return_value=ai_msg):
        from graphbot.agent.runner import GraphRunner
        runner = GraphRunner(config, db)

    application.state.config = config
    application.state.db = db
    application.state.runner = runner
    return application


@pytest.fixture
async def client_no_auth(_app_no_auth):
    async with AsyncClient(
        transport=ASGITransport(app=_app_no_auth), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def client_auth(_app_with_auth):
    async with AsyncClient(
        transport=ASGITransport(app=_app_with_auth), base_url="http://test"
    ) as c:
        yield c


# ── Unit: password hash ─────────────────────────────────────


def test_password_hash_verify():
    hashed = hash_password("secret123")
    assert verify_password("secret123", hashed)
    assert not verify_password("wrong", hashed)


# ── Unit: JWT ────────────────────────────────────────────────


def test_jwt_create_decode():
    from graphbot.api.auth import decode_token

    token = create_access_token("user1", "mysecret", "HS256", 60)
    assert decode_token(token, "mysecret", "HS256") == "user1"


def test_jwt_expired():
    from graphbot.api.auth import decode_token

    token = create_access_token("user1", "mysecret", "HS256", expire_minutes=-1)
    with pytest.raises(Exception):
        decode_token(token, "mysecret", "HS256")


# ── Unit: store auth CRUD ────────────────────────────────────


def test_store_password(db):
    db.get_or_create_user("u1", name="Alice")
    assert db.get_password_hash("u1") is None

    db.set_password("u1", "hashed_pw")
    assert db.get_password_hash("u1") == "hashed_pw"


def test_store_api_keys(db):
    db.get_or_create_user("u1")
    db.create_api_key("k1", "u1", "hash1", "My Key", None)
    db.create_api_key("k2", "u1", "hash2", "Temp Key", "2099-01-01T00:00:00")

    keys = db.list_api_keys("u1")
    assert len(keys) == 2

    key = db.get_api_key("k1")
    assert key["user_id"] == "u1"
    assert key["name"] == "My Key"

    found = db.find_api_key_by_hash("hash1")
    assert found["user_id"] == "u1"

    db.deactivate_api_key("k1")
    assert db.find_api_key_by_hash("hash1") is None


# ── Integration: auth disabled (backward compat) ─────────────


@pytest.mark.asyncio
async def test_auth_disabled_passthrough(client_no_auth):
    """When auth disabled, all endpoints open without token."""
    ai_msg = AIMessage(content="hello", response_metadata={"usage": {"total_tokens": 10}})
    with patch("graphbot.agent.nodes.llm_provider.achat", new_callable=AsyncMock, return_value=ai_msg):
        resp = await client_no_auth.post("/chat", json={"message": "hi"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_register_no_auth(client_no_auth):
    """Register works when auth disabled."""
    resp = await client_no_auth.post(
        "/auth/register",
        json={"user_id": "newuser", "password": "pass123", "name": "New"},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


# ── Integration: auth enabled ────────────────────────────────


@pytest.mark.asyncio
async def test_auth_enabled_no_token(client_auth):
    """Without token → 401."""
    resp = await client_auth.post("/chat", json={"message": "hi"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_login_success(client_auth):
    """Correct password → token returned."""
    resp = await client_auth.post(
        "/auth/login", json={"user_id": "owner", "password": "ownerpass"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["token"] is not None


@pytest.mark.asyncio
async def test_login_wrong_password(client_auth):
    """Wrong password → 401."""
    resp = await client_auth.post(
        "/auth/login", json={"user_id": "owner", "password": "wrong"}
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_auth_enabled_with_token(client_auth):
    """Login → token → authenticated /chat request."""
    # Login
    resp = await client_auth.post(
        "/auth/login", json={"user_id": "owner", "password": "ownerpass"}
    )
    token = resp.json()["token"]

    # Use token
    ai_msg = AIMessage(content="authed!", response_metadata={"usage": {"total_tokens": 10}})
    with patch("graphbot.agent.nodes.llm_provider.achat", new_callable=AsyncMock, return_value=ai_msg):
        resp = await client_auth.post(
            "/chat",
            json={"message": "hi"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200
    assert resp.json()["response"] == "authed!"


@pytest.mark.asyncio
async def test_register_requires_owner(client_auth):
    """Register requires owner token when auth enabled."""
    # No token → 401
    resp = await client_auth.post(
        "/auth/register",
        json={"user_id": "newuser", "password": "pass", "name": "New"},
    )
    assert resp.status_code == 401

    # Owner token → success
    login = await client_auth.post(
        "/auth/login", json={"user_id": "owner", "password": "ownerpass"}
    )
    token = login.json()["token"]
    resp = await client_auth.post(
        "/auth/register",
        json={"user_id": "newuser", "password": "pass", "name": "New"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_api_key_crud(client_auth):
    """Create, list, delete API key."""
    # Login as owner
    login = await client_auth.post(
        "/auth/login", json={"user_id": "owner", "password": "ownerpass"}
    )
    token = login.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create
    resp = await client_auth.post(
        "/auth/api-keys", json={"name": "test-key"}, headers=headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "test-key"
    assert "key" in data
    key_id = data["key_id"]

    # List
    resp = await client_auth.get("/auth/api-keys", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    # Delete
    resp = await client_auth.delete(f"/auth/api-keys/{key_id}", headers=headers)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_api_key_auth(client_auth, _app_with_auth):
    """Authenticate with API key instead of JWT."""
    # Create key via owner
    login = await client_auth.post(
        "/auth/login", json={"user_id": "owner", "password": "ownerpass"}
    )
    token = login.json()["token"]
    resp = await client_auth.post(
        "/auth/api-keys",
        json={"name": "my-key"},
        headers={"Authorization": f"Bearer {token}"},
    )
    raw_key = resp.json()["key"]

    # Use API key to access /chat
    ai_msg = AIMessage(content="api-key!", response_metadata={"usage": {"total_tokens": 10}})
    with patch("graphbot.agent.nodes.llm_provider.achat", new_callable=AsyncMock, return_value=ai_msg):
        resp = await client_auth.post(
            "/chat",
            json={"message": "hi"},
            headers={"X-API-Key": raw_key},
        )
    assert resp.status_code == 200
    assert resp.json()["response"] == "api-key!"


@pytest.mark.asyncio
async def test_rate_limiting(_app_with_auth):
    """Exceed rate limit → 429."""
    # Set very low limit for testing
    _app_with_auth.state.config.auth.rate_limit.requests_per_minute = 3

    async with AsyncClient(
        transport=ASGITransport(app=_app_with_auth), base_url="http://test"
    ) as client:
        for _ in range(3):
            await client.get("/auth/user/nobody")
        resp = await client.get("/auth/user/nobody")
        assert resp.status_code == 429
