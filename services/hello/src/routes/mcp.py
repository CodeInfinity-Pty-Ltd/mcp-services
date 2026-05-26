"""MCP Streamable HTTP transport on top of Tina4-Python.

MCP-over-HTTP is JSON-RPC 2.0. Clients POST a single request (or a batch)
to ``/mcp``, we route on the ``method`` field, and respond with a JSON
body. We don't bother with SSE streaming yet — Claude.ai's MCP client
works fine with the synchronous JSON response path and most tools are
fast.

Supported methods:

  initialize          handshake; client tells us its protocol version,
                      we tell it ours + our capabilities
  notifications/initialized   client says "I'm ready", no response
  tools/list          enumerate available tools
  tools/call          invoke a tool by name with arguments
  ping                MCP-level liveness (distinct from our ``ping`` tool)

Anything else returns the JSON-RPC ``Method not found`` error (-32601).

Authentication: every request goes through ``validate_request`` which
verifies the Bearer JWT against Keycloak. If validation fails we return
a 401 with the WWW-Authenticate challenge that points back at our
Protected Resource Metadata URL — that's the spec'd way to nudge
unauthenticated clients toward the OAuth dance.
"""
from __future__ import annotations

import json
import logging
import os
import traceback

from tina4_python.core.router import post, get, noauth

from ..auth import validate_request, AuthError
from ..tools import TOOLS_BY_NAME, public_descriptors, server_info


logger = logging.getLogger(__name__)


# Protocol versions we'll happily accept. The MCP spec evolved fast in
# 2024-2025; pinning a single version would break older clients. We tell
# the client which version we picked in the ``initialize`` response.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "https://mcp.c8eapps.co.za")
MCP_BASE_PATH = os.environ.get("MCP_BASE_PATH", "/hello").rstrip("/")


# ---------------------------------------------------------------------------
# JSON-RPC plumbing
# ---------------------------------------------------------------------------

# Error codes (JSON-RPC 2.0 reserved range + MCP-specific)
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


def _jsonrpc_ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _jsonrpc_err(req_id, code: int, message: str, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


# ---------------------------------------------------------------------------
# Method handlers
# ---------------------------------------------------------------------------

def _handle_initialize(req_id, params: dict, claims: dict):
    client_version = (params or {}).get("protocolVersion", DEFAULT_PROTOCOL_VERSION)
    if client_version in SUPPORTED_PROTOCOL_VERSIONS:
        negotiated = client_version
    else:
        # Fall back to our newest — the client should still accept it.
        negotiated = DEFAULT_PROTOCOL_VERSION
        logger.info(
            "client requested unknown MCP protocol version %r — responding with %r",
            client_version, negotiated,
        )
    return _jsonrpc_ok(req_id, {
        "protocolVersion": negotiated,
        "capabilities": {
            # We expose tools but not prompts/resources/sampling for now.
            "tools": {"listChanged": False},
        },
        "serverInfo": server_info(),
    })


def _handle_tools_list(req_id, params: dict, claims: dict):
    return _jsonrpc_ok(req_id, {"tools": public_descriptors()})


def _handle_tools_call(req_id, params: dict, claims: dict):
    name = (params or {}).get("name")
    args = (params or {}).get("arguments") or {}

    if not name:
        return _jsonrpc_err(req_id, _INVALID_PARAMS, "missing 'name'")

    tool = TOOLS_BY_NAME.get(name)
    if not tool:
        return _jsonrpc_err(req_id, _METHOD_NOT_FOUND, f"unknown tool: {name}")

    try:
        result_obj = tool["handler"](args, claims)
    except Exception as exc:
        logger.exception("tool %s raised", name)
        # MCP expects tool errors as a successful response with isError=true
        # rather than a transport-level error.
        return _jsonrpc_ok(req_id, {
            "content": [{
                "type": "text",
                "text": f"{type(exc).__name__}: {exc}",
            }],
            "isError": True,
        })

    return _jsonrpc_ok(req_id, {
        "content": [{
            "type": "text",
            "text": json.dumps(result_obj, default=str, indent=2),
        }],
        "structuredContent": result_obj,
        "isError": False,
    })


def _handle_mcp_ping(req_id, params: dict, claims: dict):
    """MCP-protocol-level ping (not our 'ping' tool). Returns an empty object."""
    return _jsonrpc_ok(req_id, {})


_METHODS = {
    "initialize": _handle_initialize,
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
    "ping": _handle_mcp_ping,
}


def _dispatch(message: dict, claims: dict):
    """Route one JSON-RPC message. Returns a response dict or ``None`` for
    notifications (which expect no reply)."""
    if message.get("jsonrpc") != "2.0":
        return _jsonrpc_err(
            message.get("id"), _INVALID_REQUEST,
            "expected jsonrpc='2.0'",
        )

    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}

    # Notifications have no id — no reply expected.
    is_notification = req_id is None

    if is_notification:
        # Spec says we MUST NOT send a response for notifications. Just log
        # the well-known ones for observability and drop the rest.
        if method == "notifications/initialized":
            logger.info("client signaled initialized")
        else:
            logger.debug("ignoring notification %r", method)
        return None

    handler = _METHODS.get(method)
    if not handler:
        return _jsonrpc_err(req_id, _METHOD_NOT_FOUND, f"method not found: {method}")

    try:
        return handler(req_id, params, claims)
    except Exception as exc:
        logger.exception("handler for %s raised", method)
        return _jsonrpc_err(
            req_id, _INTERNAL_ERROR,
            f"internal error: {type(exc).__name__}: {exc}",
        )


# ---------------------------------------------------------------------------
# HTTP entry points
# ---------------------------------------------------------------------------

def _challenge_response(response, message: str = "authentication required"):
    """Build the 401 response Claude.ai expects so it can kick off OAuth."""
    rm = f'{PUBLIC_BASE_URL}{MCP_BASE_PATH}/.well-known/oauth-protected-resource'
    # The spec wants the resource_metadata URL in the WWW-Authenticate header.
    response._headers.append((
        "www-authenticate",
        f'Bearer realm="mcp", resource_metadata="{rm}"',
    ))
    return response({"error": "unauthorized", "detail": message}, 401)


@noauth()
@post("/mcp")
async def mcp_endpoint(request, response):
    """JSON-RPC entry point for the MCP Streamable HTTP transport.

    @noauth() turns off Tina4's built-in token check — we do our own JWT
    validation against Keycloak below."""
    try:
        claims = validate_request(request)
    except AuthError as exc:
        return _challenge_response(response, str(exc))

    body = request.body
    if isinstance(body, (bytes, bytearray)):
        try:
            body = json.loads(body.decode("utf-8"))
        except Exception as exc:
            return response(
                {"jsonrpc": "2.0", "id": None,
                 "error": {"code": _PARSE_ERROR, "message": f"parse error: {exc}"}},
                400,
            )

    if not isinstance(body, (dict, list)):
        return response(
            {"jsonrpc": "2.0", "id": None,
             "error": {"code": _INVALID_REQUEST, "message": "body must be object or array"}},
            400,
        )

    if isinstance(body, list):
        # Batch request — process in order, skip notifications in the reply.
        replies = [r for r in (_dispatch(m, claims) for m in body) if r is not None]
        return response(replies)

    reply = _dispatch(body, claims)
    if reply is None:
        # Notification — spec says return 202 with no body.
        response.status_code = 202
        response.content = b""
        return response

    return response(reply)


@noauth()
@get("/mcp")
async def mcp_get(request, response):
    """Some MCP clients open a GET first to negotiate server-initiated SSE.
    We don't push events from server → client today, so we return a small
    JSON descriptor and let the client fall back to POST-only."""
    return response({
        "transport": "streamable-http",
        "post_url": f"{PUBLIC_BASE_URL}{MCP_BASE_PATH}/mcp",
        "supports_server_initiated_sse": False,
    })
