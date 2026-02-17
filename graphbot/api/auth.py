"""Auth routes — register, login, JWT token, API keys."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException

from graphbot.api.deps import get_config, get_current_user, get_db
from graphbot.core.config.schema import Config
from graphbot.memory.models import (
    APIKeyCreate,
    APIKeyInfo,
    APIKeyResponse,
    AuthResponse,
    LoginRequest,
    RegisterRequest,
)
from graphbot.memory.store import MemoryStore

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Helpers ──────────────────────────────────────────────────


def hash_password(password: str) -> str:
    """Hash a plain-text password with bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain-text password against a bcrypt hash."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(
    user_id: str, secret: str, algorithm: str, expire_minutes: int
) -> str:
    """Create a JWT access token."""
    payload = {
        "sub": user_id,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=expire_minutes),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_token(token: str, secret: str, algorithm: str) -> str:
    """Decode a JWT token and return user_id. Raises on invalid/expired."""
    try:
        payload = jwt.decode(token, secret, algorithms=[algorithm])
        user_id: str | None = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token payload")
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


# ── Endpoints ────────────────────────────────────────────────


@router.post("/register", response_model=AuthResponse)
async def register(
    body: RegisterRequest,
    current_user: str = Depends(get_current_user),
    db: MemoryStore = Depends(get_db),
    config: Config = Depends(get_config),
):
    """Register a new user. Requires owner/admin auth (or auth disabled)."""
    # Only owner can register new users via API
    if config.auth_enabled and current_user != config.owner_user_id:
        raise HTTPException(status_code=403, detail="Only owner can register users")
    if db.user_exists(body.user_id):
        return AuthResponse(success=False, message="User already exists.")

    db.get_or_create_user(body.user_id, name=body.name)
    db.set_password(body.user_id, hash_password(body.password))

    token = None
    if config.auth_enabled:
        token = create_access_token(
            body.user_id,
            config.auth.jwt_secret_key,
            config.auth.jwt_algorithm,
            config.auth.access_token_expire_minutes,
        )

    return AuthResponse(
        success=True,
        message="User registered.",
        user_id=body.user_id,
        name=body.name,
        token=token,
    )


@router.post("/login", response_model=AuthResponse)
async def login(
    body: LoginRequest,
    db: MemoryStore = Depends(get_db),
    config: Config = Depends(get_config),
):
    """Login with user_id + password → JWT token."""
    user = db.get_user(body.user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    stored_hash = user.get("password_hash")
    if not stored_hash or not verify_password(body.password, stored_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = None
    if config.auth_enabled:
        token = create_access_token(
            body.user_id,
            config.auth.jwt_secret_key,
            config.auth.jwt_algorithm,
            config.auth.access_token_expire_minutes,
        )

    return AuthResponse(
        success=True,
        message="Login successful.",
        user_id=user["user_id"],
        name=user.get("name"),
        token=token,
    )


@router.post("/token", response_model=AuthResponse)
async def token(
    body: LoginRequest,
    db: MemoryStore = Depends(get_db),
    config: Config = Depends(get_config),
):
    """OAuth2-compatible token endpoint (alias for /login)."""
    return await login(body, db, config)


@router.get("/user/{user_id}")
async def user_profile(
    user_id: str,
    current_user: str = Depends(get_current_user),
    db: MemoryStore = Depends(get_db),
    config: Config = Depends(get_config),
):
    """Get user profile. Users can only view their own profile, unless they are the owner."""
    # Authorization: only allow viewing own profile or owner can view all
    if current_user != user_id and current_user != config.owner_user_id:
        raise HTTPException(
            status_code=403, detail="You can only view your own profile"
        )

    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    safe = {k: v for k, v in dict(user).items() if k != "password_hash"}
    return safe


# ── API Key Management ───────────────────────────────────────


@router.post("/api-keys", response_model=APIKeyResponse)
async def create_api_key(
    body: APIKeyCreate,
    current_user: str = Depends(get_current_user),
    db: MemoryStore = Depends(get_db),
):
    """Create a new API key for the authenticated user."""
    key_id = str(uuid.uuid4())
    raw_key = secrets.token_urlsafe(32)
    key_hash = hash_password(raw_key)

    expires_at = None
    if body.expires_in_days:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)
        ).isoformat()

    db.create_api_key(key_id, current_user, key_hash, body.name, expires_at)

    return APIKeyResponse(
        key_id=key_id,
        key=raw_key,
        name=body.name,
        created_at=datetime.now(timezone.utc).isoformat(),
        expires_at=expires_at,
    )


@router.get("/api-keys", response_model=list[APIKeyInfo])
async def list_api_keys(
    current_user: str = Depends(get_current_user),
    db: MemoryStore = Depends(get_db),
):
    """List all API keys for the authenticated user."""
    rows = db.list_api_keys(current_user)
    return [APIKeyInfo(**r) for r in rows]


@router.delete("/api-keys/{key_id}")
async def delete_api_key(
    key_id: str,
    current_user: str = Depends(get_current_user),
    db: MemoryStore = Depends(get_db),
):
    """Deactivate an API key."""
    key = db.get_api_key(key_id)
    if not key or key["user_id"] != current_user:
        raise HTTPException(status_code=404, detail="API key not found")
    db.deactivate_api_key(key_id)
    return {"status": "deactivated", "key_id": key_id}
