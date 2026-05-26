"""MCP protocol logic — pure functions, no Tina4 imports.

This is the brains the route layer delegates to. Keeping it framework-free
means it's trivially unit-testable (see ``tests/test_mcp_server.py``) and
swapping Tina4 for something else later only touches ``src/routes/mcp.py``.

The route side handles:
    - reading the HTTP body
    - validating the OAuth bearer token (delegated to ``src.app.auth``)
    - returning a 401 with WWW-Authenticate when validation fails

This module handles:
    - JSON-RPC 2.0 envelope (single + batch)
    - method dispatch (``initialize``, ``tools/list``, ``tools/call``, ``ping``,
      ``notifications/initialized``)
    - aggregating ``TOOLS`` lists from every integration in ``src/integrations/``
    - converting tool results into MCP content blocks
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import pkgutil
import platform
from typing import Any, Callable


logger = logging.getLogger(__name__)


# Protocol versions we'll accept. The spec evolved fast in 2024–2025; we
# negotiate to whatever the client requested if we know it, otherwise we
# answer with our newest and let the client decide whether to continue.
SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]


# JSON-RPC 2.0 reserved error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


# ---------------------------------------------------------------------------
# Tool registry — assembled at import time from every src/integrations/* module
# ---------------------------------------------------------------------------

def _discover_tools() -> dict[str, dict[str, Any]]:
    """Auto-import every module under ``src.integrations`` and collect each
    one's ``TOOLS`` list into a single name → spec map.

    Convention over config: an integration is "registered" the moment you
    drop a file into ``src/integrations/`` and give it a ``TOOLS`` list.
    """
    registry: dict[str, dict[str, Any]] = {}
    import src.integrations as integrations_pkg
    for module_info in pkgutil.iter_modules(integrations_pkg.__path__):
        module = importlib.import_module(f"src.integrations.{module_info.name}")
        for tool in getattr(module, "TOOLS", []):
            name = tool["name"]
            if name in registry:
                raise RuntimeError(
                    f"duplicate MCP tool name {name!r} (already registered)"
                )
            registry[name] = tool
    logger.info(
        "loaded %d MCP tools from integrations: %s",
        len(registry), sorted(registry.keys()),
    )
    return registry


TOOLS_BY_NAME = _discover_tools()


def public_descriptors() -> list[dict[str, Any]]:
    """Strip the handler ref before sending the catalogue to MCP clients —
    they only want name + description + inputSchema."""
    return [
        {k: v for k, v in tool.items() if k != "handler"}
        for tool in TOOLS_BY_NAME.values()
    ]


def server_info() -> dict[str, Any]:
    return {
        "name": os.environ.get("MCP_SERVICE_NAME", "mcp-services"),
        "version": "0.2.0",
        "platform": platform.python_implementation()
                    + " " + platform.python_version(),
    }


# ---------------------------------------------------------------------------
# JSON-RPC envelope helpers
# ---------------------------------------------------------------------------

def jsonrpc_ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def jsonrpc_err(req_id, code: int, message: str, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


# ---------------------------------------------------------------------------
# Method handlers
# ---------------------------------------------------------------------------

def _handle_initialize(req_id, params: dict, claims: dict):
    client_version = (params or {}).get("protocolVersion", DEFAULT_PROTOCOL_VERSION)
    negotiated = client_version if client_version in SUPPORTED_PROTOCOL_VERSIONS \
        else DEFAULT_PROTOCOL_VERSION
    if negotiated != client_version:
        logger.info(
            "client requested unknown MCP protocol %r — answering with %r",
            client_version, negotiated,
        )
    return jsonrpc_ok(req_id, {
        "protocolVersion": negotiated,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": server_info(),
    })


def _handle_tools_list(req_id, params: dict, claims: dict):
    return jsonrpc_ok(req_id, {"tools": public_descriptors()})


def _handle_tools_call(req_id, params: dict, claims: dict):
    name = (params or {}).get("name")
    args = (params or {}).get("arguments") or {}

    if not name:
        return jsonrpc_err(req_id, INVALID_PARAMS, "missing 'name'")

    tool = TOOLS_BY_NAME.get(name)
    if not tool:
        return jsonrpc_err(req_id, METHOD_NOT_FOUND, f"unknown tool: {name}")

    try:
        result_obj = tool["handler"](args, claims)
    except Exception as exc:
        # MCP wants tool-level errors as a successful response with
        # isError=true, not a transport-level JSON-RPC error.
        logger.exception("tool %s raised", name)
        return jsonrpc_ok(req_id, {
            "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
            "isError": True,
        })

    return jsonrpc_ok(req_id, {
        "content": [{
            "type": "text",
            "text": json.dumps(result_obj, default=str, indent=2),
        }],
        "structuredContent": result_obj,
        "isError": False,
    })


def _handle_ping(req_id, params: dict, claims: dict):
    """MCP protocol-level ping (distinct from the ``hello.ping`` tool)."""
    return jsonrpc_ok(req_id, {})


_METHODS: dict[str, Callable] = {
    "initialize": _handle_initialize,
    "tools/list": _handle_tools_list,
    "tools/call": _handle_tools_call,
    "ping": _handle_ping,
}


# ---------------------------------------------------------------------------
# Public dispatch entry point — called by src/routes/mcp.py
# ---------------------------------------------------------------------------

def dispatch(message: dict, claims: dict):
    """Route one JSON-RPC message. Returns a response dict, or ``None`` if
    the message was a notification (which expects no reply per spec)."""
    if not isinstance(message, dict):
        return jsonrpc_err(None, INVALID_REQUEST, "message must be a JSON object")

    if message.get("jsonrpc") != "2.0":
        return jsonrpc_err(message.get("id"), INVALID_REQUEST, "expected jsonrpc='2.0'")

    method = message.get("method")
    req_id = message.get("id")
    params = message.get("params") or {}

    is_notification = req_id is None

    if is_notification:
        if method == "notifications/initialized":
            logger.info("client signaled initialized")
        else:
            logger.debug("ignoring notification %r", method)
        return None

    handler = _METHODS.get(method)
    if not handler:
        return jsonrpc_err(req_id, METHOD_NOT_FOUND, f"method not found: {method}")

    try:
        return handler(req_id, params, claims)
    except Exception as exc:
        logger.exception("handler for %s raised", method)
        return jsonrpc_err(
            req_id, INTERNAL_ERROR,
            f"internal error: {type(exc).__name__}: {exc}",
        )


def dispatch_batch(messages: list, claims: dict) -> list:
    """Apply ``dispatch`` to a batch, dropping notification slots from the
    reply since the spec says we MUST NOT respond to those."""
    return [r for r in (dispatch(m, claims) for m in messages) if r is not None]
