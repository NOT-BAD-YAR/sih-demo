"""Shared dependencies — DB connection + auth guards (Phase 6).

`get_db` yields a psycopg2 connection per request (overridable in tests).
`get_current_user` resolves the bearer token to a live, enabled account.
`require_role` builds the RBAC guard the routers attach to their routes.
"""

from __future__ import annotations

from typing import Iterator

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from db.conn import connect
from db.dao import get_account_by_username

_bearer = HTTPBearer(auto_error=False)


def get_db() -> Iterator[object]:
    """Open a fresh connection for the request lifecycle."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    conn=Depends(get_db),
) -> dict:
    """Resolve the bearer token to an enabled account dict {username, role}."""
    from .auth import decode_token

    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    try:
        payload = decode_token(credentials.credentials)
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")

    account = get_account_by_username(conn, payload.get("sub", ""))
    if account is None or account.get("disabled"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "account disabled or unknown")
    return {"username": account["username"], "role": account["role"]}


def require_role(*roles: str):
    """Dependency factory: reject unless the current user holds one role."""

    def guard(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"role '{user['role']}' cannot access this endpoint",
            )
        return user

    return guard


__all__ = ["get_db", "get_current_user", "require_role"]