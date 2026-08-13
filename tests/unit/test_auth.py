"""Phase 6 — JWT + password-hash unit tests (pure logic, no DB).

Covers token issue/decode round-trip, expiry claims, tamper rejection, and
the PBKDF2 password hashing reused from `db.passwords`.
"""

from datetime import datetime, timedelta, timezone

import pytest
from jose import JWTError

from api.auth import (
    ALGORITHM,
    REFRESH_TTL_DAYS,
    TOKEN_TTL_MINUTES,
    decode_token,
    issue_refresh,
    issue_token,
    hash_password,
    verify_password,
)


class TestJwt:
    def test_roundtrip_carries_sub_and_role(self):
        token = issue_token("bob", "analyst")
        payload = decode_token(token)
        assert payload["sub"] == "bob"
        assert payload["role"] == "analyst"

    def test_exp_claim_is_30_minutes(self):
        payload = decode_token(issue_token("bob", "analyst"))
        iat = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        assert exp - iat == timedelta(minutes=TOKEN_TTL_MINUTES)

    def test_refresh_expires_in_days(self):
        payload = decode_token(issue_refresh("bob", "admin"))
        iat = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        assert exp - iat == timedelta(days=REFRESH_TTL_DAYS)

    def test_tampered_token_rejected(self):
        token = issue_token("bob", "analyst")
        with pytest.raises(JWTError):
            decode_token(token + "x")
        pos = len(token.split(".")[0]) + 1
        char = "A" if token[pos] != "A" else "B"
        with pytest.raises(JWTError):
            decode_token(token[:pos] + char + token[pos + 1:])

    def test_algorithm_is_hs256(self):
        assert ALGORITHM == "HS256"

    def test_garbage_rejected(self):
        with pytest.raises(JWTError):
            decode_token("not.a.jwt")


class TestPasswords:
    def test_hash_verify_roundtrip(self):
        hashed = hash_password("s3cret!")
        assert verify_password("s3cret!", hashed)
        assert not verify_password("wrong", hashed)

    def test_hashes_are_salted(self):
        assert hash_password("same") != hash_password("same")

    def test_garbage_stored_hash_returns_false(self):
        assert not verify_password("anything", "nope-not-a-hash")
        assert not verify_password("anything", "pbkdf2_sha256$5$zz$nothex!")