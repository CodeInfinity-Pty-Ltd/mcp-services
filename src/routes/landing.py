"""Tiny landing page for browsers at https://mcp.c8eapps.co.za/.

Machines hit ``/.well-known/oauth-protected-resource`` directly. This page
is just so a curious operator who opens the URL knows what they've found
and how to connect.

Thin per Tina4 conventions — render a Frond template; no inline styles.
"""
import os

from tina4_python.core.router import get, noauth

from src.app import mcp_server


KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "https://auth.c8eapps.co.za")
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "mcp")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://mcp.c8eapps.co.za")


def _integrations_loaded() -> list[str]:
    """Distinct integration prefixes from the registered MCP tools."""
    prefixes = set()
    for name in mcp_server.TOOLS_BY_NAME:
        if "." in name:
            prefixes.add(name.split(".", 1)[0])
    return sorted(prefixes)


@noauth()
@get("/")
async def landing(request, response):
    return response.render("landing.twig", {
        "title": "mcp-services — c8eapps",
        "mcp_url": f"{PUBLIC_BASE_URL}/mcp",
        "issuer": f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}",
        "integrations": _integrations_loaded(),
    })
