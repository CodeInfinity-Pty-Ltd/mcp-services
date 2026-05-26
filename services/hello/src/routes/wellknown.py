"""OAuth Protected Resource Metadata + (passthrough) Authorization Server
Metadata for MCP clients.

The OAuth 2.0 Protected Resource Metadata document (RFC 9728) lets an MCP
client like Claude.ai discover **which** authorization server protects
this MCP endpoint. We point it at our Keycloak realm; Claude.ai then
fetches Keycloak's own ``/.well-known/openid-configuration`` to learn the
authorize + token endpoints and run the OAuth flow.

We also re-serve the Authorization Server Metadata at the matching path
on this host. Some clients only resolve well-known docs relative to the
resource server's origin, not the resource_metadata URL we hand back —
proxying through here means it works in both modes.
"""
import os

import requests
from tina4_python.core.router import get, noauth


KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "https://auth.c8eapps.co.za")
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "mcp")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://mcp.c8eapps.co.za")
MCP_BASE_PATH = os.environ.get("MCP_BASE_PATH", "/hello").rstrip("/")
SERVICE_NAME = os.environ.get("MCP_SERVICE_NAME", "hello")


def _resource_url() -> str:
    return f"{PUBLIC_BASE_URL}{MCP_BASE_PATH}/mcp"


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
        "resource_documentation": f"{PUBLIC_BASE_URL}{MCP_BASE_PATH}/",
        "service": SERVICE_NAME,
    })


@noauth()
@get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata(request, response):
    """Proxy Keycloak's OIDC discovery document so clients that resolve
    well-known docs against the resource origin (not the issuer) still
    find the OAuth endpoints. We pass it through verbatim."""
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


@noauth()
@get("/")
async def root(request, response):
    """Tiny landing page so a human hitting the URL knows what this is."""
    return response.html(
        f"<!doctype html><meta charset=\"utf-8\"><title>{SERVICE_NAME} MCP</title>"
        f"<body style=\"font-family:system-ui;background:#0f172a;color:#e2e8f0;padding:3rem\">"
        f"<h1>{SERVICE_NAME} — MCP server</h1>"
        f"<p>This is a remote MCP endpoint. Point an MCP client (e.g. Claude.ai) at:</p>"
        f"<pre style=\"background:#1e293b;padding:1rem;border-radius:0.5rem;display:inline-block\">"
        f"{_resource_url()}</pre>"
        f"<p>Authentication: OAuth 2.1 via "
        f"<a href=\"{_auth_server_issuer()}\" style=\"color:#60a5fa\">{_auth_server_issuer()}</a>.</p>"
        f"</body>"
    )
