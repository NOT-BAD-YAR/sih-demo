"""Admin endpoints â€” account management + threshold tuning (Phase 6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from db.dao import create_account, get_account_by_username, list_accounts, list_settings, upsert_setting
from ..auth import hash_password
from ..dependencies import get_db, require_role

router = APIRouter(prefix="/admin", tags=["admin"])

VALID_ROLES = ("analyst", "admin")


class CreateUserRequest(BaseModel):
    username: str
    role: str
    password: str


class ThresholdsRequest(BaseModel):
    k: float | None = None
    dormancy_days: int | None = None
    band_critical: int | None = None


@router.post("/users")
def create_user(
    body: CreateUserRequest,
    conn=Depends(get_db),
    _admin: dict = Depends(require_role("admin")),
) -> dict:
    """Create a dashboard account (admin only)."""
    if body.role not in VALID_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"role must be one of {VALID_ROLES}")
    if not body.username or len(body.password) < 4:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            "username required and password >= 4 chars")
    if get_account_by_username(conn, body.username) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"user {body.username!r} exists")
    account_id = create_account(conn, body.username, hash_password(body.password), body.role)
    return {"id": account_id, "username": body.username, "role": body.role, "disabled": False}


@router.get("/users")
def list_users(
    conn=Depends(get_db),
    _admin: dict = Depends(require_role("admin")),
) -> list[dict]:
    """All dashboard accounts (admin only)."""
    return list_accounts(conn)


@router.put("/thresholds")
def put_thresholds(
    body: ThresholdsRequest,
    conn=Depends(get_db),
    _admin: dict = Depends(require_role("admin")),
) -> dict:
    """Tune engine thresholds; stored in `settings` (admin only)."""
    if body.k is not None:
        upsert_setting(conn, "RULE_VOLUME_K", float(body.k))
    if body.dormancy_days is not None:
        upsert_setting(conn, "DORMANCY_DAYS", int(body.dormancy_days))
    if body.band_critical is not None:
        upsert_setting(conn, "RISK_BAND_CRITICAL", int(body.band_critical))
    return {"settings": {s["key"]: s["value"] for s in list_settings(conn)}}


@router.get("/thresholds")
def get_thresholds(
    conn=Depends(get_db),
    _admin: dict = Depends(require_role("admin")),
) -> dict:
    """Current engine thresholds (admin only)."""
    return {"settings": {s["key"]: s["value"] for s in list_settings(conn)}}