"""Auth routes — register, login, user profile."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from graphbot.api.deps import get_db
from graphbot.memory.models import AuthResponse, LoginRequest, RegisterRequest
from graphbot.memory.store import MemoryStore

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=AuthResponse)
async def register(body: RegisterRequest, db: MemoryStore = Depends(get_db)):
    """Register a new user."""
    if db.user_exists(body.user_id):
        return AuthResponse(success=False, message="User already exists.")
    db.get_or_create_user(body.user_id, name=body.name)
    return AuthResponse(
        success=True,
        message="User registered.",
        user_id=body.user_id,
        name=body.name,
    )


@router.post("/login", response_model=AuthResponse)
async def login(body: LoginRequest, db: MemoryStore = Depends(get_db)):
    """Login (simple user_id check, no password auth yet)."""
    user = db.get_user(body.user_id)
    if not user:
        return AuthResponse(success=False, message="User not found.")
    return AuthResponse(
        success=True,
        message="Login successful.",
        user_id=user["user_id"],
        name=user.get("name"),
    )


@router.get("/user/{user_id}")
async def user_profile(user_id: str, db: MemoryStore = Depends(get_db)):
    """Get user profile."""
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return dict(user)
