"""Auth0 JWT verification: RS256 signature against the tenant's JWKS, plus aud/iss checks."""
import logging
import os
import time

import httpx
from fastapi import Header, HTTPException
from jose import jwt

logger = logging.getLogger("suitcase")
_jwks_cache = {"keys": None, "fetched_at": 0.0}


def _jwks() -> list[dict]:
    """Auth0 signing keys, cached for an hour (they rotate rarely)."""
    if _jwks_cache["keys"] is None or time.time() - _jwks_cache["fetched_at"] > 3600:
        domain = os.environ["AUTH0_DOMAIN"]
        r = httpx.get(f"https://{domain}/.well-known/jwks.json", timeout=10)
        r.raise_for_status()
        _jwks_cache["keys"] = r.json()["keys"]
        _jwks_cache["fetched_at"] = time.time()
    return _jwks_cache["keys"]


def verify_token(token: str) -> dict:
    """Verify signature, audience and issuer; return the token claims. Raises jose.JWTError on failure."""
    header = jwt.get_unverified_header(token)
    key = next((k for k in _jwks() if k["kid"] == header["kid"]), None)
    if key is None:
        raise jwt.JWTError("signing key not found in JWKS")
    domain = os.environ["AUTH0_DOMAIN"]
    return jwt.decode(token, key, algorithms=["RS256"], audience=os.environ["AUTH0_AUDIENCE"], issuer=f"https://{domain}/")


def open_mode() -> bool:
    """No Auth0 tenant configured, so there is nothing to verify a token against."""
    return not (os.environ.get("AUTH0_DOMAIN") and os.environ.get("AUTH0_AUDIENCE"))


def require_auth(authorization: str = Header(None)) -> dict:
    """FastAPI dependency: bearer token -> verified claims, or 401. Unconfigured -> one shared local user."""
    if open_mode():
        return {"sub": "local", "email": None}
    if not authorization or not authorization.startswith("Bearer "):
        logger.warning("auth rejected: missing bearer token")
        raise HTTPException(401, "missing bearer token")
    try:
        return verify_token(authorization.removeprefix("Bearer "))
    except jwt.JWTError as exc:
        logger.warning("auth rejected: invalid token: %s", exc)
        raise HTTPException(401, f"invalid token: {exc}")
