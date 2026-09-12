"""Unit test for auth.py booth-resilience behavior. Run: cd server && uv run python test_auth.py"""
import base64
import json
import os
from unittest.mock import patch

import httpx

import auth

os.environ.setdefault("AUTH0_DOMAIN", "test-tenant.example.auth0.com")
os.environ.setdefault("AUTH0_AUDIENCE", "test-audience")


def b64url(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


# A structurally-valid but unsigned/unverifiable token: enough to reach _jwks().
fake_token = f"{b64url({'alg': 'RS256', 'kid': 'some-kid'})}.{b64url({'sub': 'x'})}.sig"

# (a) no cache yet, JWKS fetch fails -> require_auth returns 503, not a crash.
auth._jwks_cache["keys"] = None
auth._jwks_cache["fetched_at"] = 0.0
with patch.object(httpx, "get", side_effect=httpx.ConnectError("down")):
    try:
        auth.require_auth(f"Bearer {fake_token}")
        assert False, "expected HTTPException"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 503, (type(exc), exc)

# (b) a stale cache exists, refetch fails -> _jwks falls back to the stale keys.
auth._jwks_cache["keys"] = [{"kid": "stale-kid"}]
auth._jwks_cache["fetched_at"] = 0.0  # older than the 1h TTL
with patch.object(httpx, "get", side_effect=httpx.ConnectError("down")):
    assert auth._jwks() == [{"kid": "stale-kid"}]

print("test_auth ok")
