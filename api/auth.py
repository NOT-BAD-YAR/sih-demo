"""Authentication — JWT issue/verify + password hashing (Phase 6).

Tokens are HS256 JWTs with `sub` = username and a `role` claim, expiring after
TOKEN_TTL_MINUTES (access) or REFRESH_TTL_DAYS (refresh). Secrets come from
the environment (`JWT_SECRET`) with a documented dev fallback.

Password hashing reuses `db.passwords` (PBKDF2-HMAC-SHA256) so tokens issued
for accounts seeded by Phase 3 verify identically.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

from db.passwords import hash_password, verify_password  # noqa: F401  (re-exported)

ALGORITHM = "HS256"
TOKEN_TTL_MINUTES = 30
REFRESH_TTL_DAYS = 7


def jwt_secret() -> str:
    return os.environ.get("JWT_SECRET", "dev-secret-change-me")


def issue_token(
    username: str,
    role: str,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Issue a signed JWT with sub/role claims."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + (expires_delta or timedelta(minutes=TOKEN_TTL_MINUTES)),
    }
    return jwt.encode(payload, jwt_secret(), algorithm=ALGORITHM)


def issue_refresh(username: str, role: str) -> str:
    return issue_token(username, role, timedelta(days=REFRESH_TTL_DAYS))


def decode_token(token: str) -> dict:
    """Decode + verify a token; raises JWTError on any tampering/expiry."""
    return jwt.decode(token, jwt_secret(), algorithms=[ALGORITHM])


__all__ = [
    "ALGORITHM",
    "TOKEN_TTL_MINUTES",
    "REFRESH_TTL_DAYS",
    "jwt_secret",
    "issue_token",
    "issue_refresh",
    "decode_token",
    "hash_password",
    "verify_password",
]