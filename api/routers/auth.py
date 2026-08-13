"""Auth endpoints — login + refresh (Phase 6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from db.dao import get_account_by_username
from ..auth import issue_refresh, issue_token, verify_password
from ..dependencies import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh: str


@router.post("/login")
def login(body: LoginRequest, conn=Depends(get_db)) -> dict:
    """Exchange username/password for access + refresh tokens."""
    account = get_account_by_username(conn, body.username)
    if account is None or account.get("disabled"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    if not verify_password(body.password, account["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    return {
        "access": issue_token(account["username"], account["role"]),
        "refresh": issue_refresh(account["username"], account["role"]),
    }


@router.post("/refresh")
def refresh(body: RefreshRequest) -> dict:
    """Swap a valid refresh token for a fresh access token."""
    from ..auth import decode_token

    try:
        payload = decode_token(body.refresh)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired refresh token")
    return {"access": issue_token(payload["sub"], payload["role"])}