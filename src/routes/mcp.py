"""MCP Streamable HTTP transport — thin route layer.

All MCP protocol logic lives in ``src.app.mcp_server``; this file is just
the Tina4 glue: parse the body, hand it off, return the result. Keeps
business logic out of the routes per the Tina4 conventions.
"""
from __future__ import annotations

import json
import logging
import os

from tina4_python.core.router import post, get, noauth

from src.app import mcp_server
from src.app.auth import validate_request, AuthError


logger = logging.getLogger(__name__)

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://mcp.c8eapps.co.za")


def _challenge(response, detail: str):
    """Return the 401 the MCP spec wants — points unauthenticated clients
    at the Protected Resource Metadata doc so they can run OAuth."""
    rm = f"{PUBLIC_BASE_URL}/.well-known/oauth-protected-resource"
    response._headers.append((
        "www-authenticate",
        f'Bearer realm="mcp", resource_metadata="{rm}"',
    ))
    return response({"error": "unauthorized", "detail": detail}, 401)


@noauth()
@post("/mcp")
async def mcp_endpoint(request, response):
    # ``@noauth()`` only disables Tina4's built-in token check; we do our
    # own JWT validation below.
    try:
        claims = validate_request(request)
    except AuthError as exc:
        return _challenge(response, str(exc))

    body = request.body
    if isinstance(body, (bytes, bytearray)):
        try:
            body = json.loads(body.decode("utf-8"))
        except Exception as exc:
            return response(mcp_server.jsonrpc_err(
                None, mcp_server.PARSE_ERROR, f"parse error: {exc}",
            ), 400)

    if isinstance(body, list):
        return response(mcp_server.dispatch_batch(body, claims))

    if not isinstance(body, dict):
        return response(mcp_server.jsonrpc_err(
            None, mcp_server.INVALID_REQUEST, "body must be object or array",
        ), 400)

    reply = mcp_server.dispatch(body, claims)
    if reply is None:
        # Notification — spec says 202 No Content.
        response.status_code = 202
        response.content = b""
        return response
    return response(reply)


@noauth()
@get("/mcp")
async def mcp_get(request, response):
    """Some clients open GET first to negotiate server-initiated SSE. We
    don't push events server → client today; return a small descriptor
    and let them fall back to POST-only."""
    return response({
        "transport": "streamable-http",
        "post_url": f"{PUBLIC_BASE_URL}/mcp",
        "supports_server_initiated_sse": False,
    })
