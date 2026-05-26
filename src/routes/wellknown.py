"""OAuth Protected Resource Metadata + Keycloak discovery passthrough.

RFC 9728 — tells an MCP client like Claude.ai which authorization server
protects this MCP endpoint. We point it at our Keycloak realm; Claude.ai
then fetches Keycloak's own ``/.well-known/openid-configuration`` to learn
the authorize + token endpoints and run the OAuth flow.

We also proxy the Authorization Server Metadata on this host because some
clients resolve well-known docs against the resource server's origin (not
the issuer we hand back), and we don't want them to wedge on that.
"""
import os

import requests
from tina4_python.core.router import get, noauth


KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "https://auth.c8eapps.co.za")
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "mcp")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://mcp.c8eapps.co.za")


def _resource_url() -> str:
    return f"{PUBLIC_BASE_URL}/mcp"


def _auth_server_issuer() -> str:
    return f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"


@noauth()
@get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata(request, response):
    """RFC 9728 — tells MCP clients which auth server gates this resource."""
    return response({
        "resource": _resource_url(),
        "authorization_servers": [_auth_server_issuer()],
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{PUBLIC_BASE_URL}/",
    })


@noauth()
@get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata(request, response):
    """Proxy Keycloak's OIDC discovery doc so clients that resolve well-
    known docs against the resource origin still find the OAuth endpoints."""
    try:
        upstream = requests.get(
            f"{_auth_server_issuer()}/.well-known/openid-configuration",
            timeout=10,
        )
        upstream.raise_for_status()
        return response(upstream.json())
    except requests.RequestException as exc:
        return response(
            {"error": "auth_server_unavailable", "detail": str(exc)},
            502,
        )
