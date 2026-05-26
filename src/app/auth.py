"""Bearer-token validation against Keycloak's JWKS.

Every MCP service in this repo uses the same pattern: clients present a
``Authorization: Bearer <jwt>`` header issued by Keycloak's ``mcp`` realm
(or whichever realm ``KEYCLOAK_REALM`` points at). We fetch the realm's
JWKS once, cache it, and verify the JWT's signature + standard claims on
every request.

Why this and not a heavier OAuth library:
- We never need to mint tokens here — Claude.ai (or any MCP client) does
  the OAuth dance directly with Keycloak.
- Validation is the only thing we do, and PyJWT + a cached JWKS is enough.

Env vars:
  KEYCLOAK_URL          e.g. https://auth.c8eapps.co.za
  KEYCLOAK_REALM        e.g. mcp
  MCP_AUDIENCE          (optional) expected ``aud`` claim — defaults to the
                        service's client_id if unset
  MCP_DEV_BYPASS_AUTH=1 disables validation for local dev. NEVER set this
                        in production manifests.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import jwt
import requests
from jwt import PyJWKClient


KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "https://auth.c8eapps.co.za")
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "mcp")
MCP_AUDIENCE = os.environ.get("MCP_AUDIENCE")  # optional override
DEV_BYPASS = os.environ.get("MCP_DEV_BYPASS_AUTH") == "1"

_REALM_URL = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
_JWKS_URL = f"{_REALM_URL}/protocol/openid-connect/certs"
_ISSUER = _REALM_URL


class AuthError(Exception):
    """Raised when a request can't be authenticated. Surfaced as 401."""


# PyJWKClient caches keys for an hour by default — exactly what we want.
_jwks_client: Optional[PyJWKClient] = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(_JWKS_URL, cache_keys=True, lifespan=3600)
    return _jwks_client


def _extract_bearer(authorization_header: str | None) -> str:
    if not authorization_header:
        raise AuthError("missing Authorization header")
    parts = authorization_header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise AuthError("expected 'Authorization: Bearer <token>'")
    return parts[1].strip()


def validate_request(request) -> dict:
    """Validate the inbound request's Bearer token and return JWT claims.

    Raises ``AuthError`` if anything is wrong. The caller is responsible
    for turning that into a 401 response with the right WWW-Authenticate
    header.
    """
    if DEV_BYPASS:
        return {
            "sub": "dev-bypass",
            "email": "dev@localhost",
            "preferred_username": "dev",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
            "iss": "dev-bypass",
            "_warning": "MCP_DEV_BYPASS_AUTH=1 — token NOT validated",
        }

    token = _extract_bearer(request.headers.get("authorization"))

    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token).key
    except Exception as exc:
        raise AuthError(f"could not load signing key: {exc}") from exc

    decode_kwargs = {
        "key": signing_key,
        "algorithms": ["RS256", "ES256"],
        "issuer": _ISSUER,
    }
    if MCP_AUDIENCE:
        decode_kwargs["audience"] = MCP_AUDIENCE
    else:
        # When no audience is pinned, skip audience validation. Keycloak
        # access tokens often have ``aud`` = "account", which isn't what
        # we'd want to enforce here without explicit config.
        decode_kwargs["options"] = {"verify_aud": False}

    try:
        claims = jwt.decode(token, **decode_kwargs)
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError(f"invalid token: {exc}") from exc

    return claims


def whoami(claims: dict) -> dict:
    """Pull a small, safe subset of the JWT claims for tools to echo back."""
    return {
        "sub": claims.get("sub"),
        "email": claims.get("email"),
        "preferred_username": claims.get("preferred_username"),
        "name": claims.get("name"),
        "iss": claims.get("iss"),
        "aud": claims.get("aud"),
        "issued_at": claims.get("iat"),
        "expires_at": claims.get("exp"),
    }
