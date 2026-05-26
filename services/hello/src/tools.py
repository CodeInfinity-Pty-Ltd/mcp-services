"""Tool definitions for the hello-mcp server.

Each tool is a tiny dict declaring its JSON schema for MCP discovery, plus
a Python function that runs it. Keep this file focused on what the tools
*do* — the dispatcher in ``routes/mcp.py`` handles the JSON-RPC envelope.

For a new MCP service: copy this directory, throw out these examples,
register your own tools in ``TOOLS`` below.
"""
from __future__ import annotations

import os
import platform
import time
from typing import Any

from .auth import whoami


def _tool_ping(args: dict, claims: dict) -> dict:
    """Return ``pong`` plus the server's wall-clock time. Useful for
    confirming the round-trip works end-to-end."""
    return {
        "result": "pong",
        "server_time": int(time.time()),
        "service": os.environ.get("MCP_SERVICE_NAME", "hello"),
    }


def _tool_whoami(args: dict, claims: dict) -> dict:
    """Echo the validated JWT claims back to the caller so they can see who
    Keycloak says they are. Handy first-call sanity check after wiring up
    Claude.ai for the first time."""
    return whoami(claims)


def _tool_echo(args: dict, claims: dict) -> dict:
    """Round-trip whatever the caller sent under ``message``. Demonstrates
    how an MCP tool consumes structured arguments."""
    return {"echo": args.get("message", "")}


# Tool catalogue — exported to clients via the MCP ``tools/list`` method
# and dispatched by ``tools/call``. The shape matches the MCP spec verbatim.
TOOLS: list[dict[str, Any]] = [
    {
        "name": "ping",
        "description": "Liveness check — returns 'pong' and the server's "
                       "current time. Use this to confirm the connection.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _tool_ping,
    },
    {
        "name": "whoami",
        "description": "Return the OAuth-authenticated user's claims as "
                       "seen by this MCP server. Confirms the auth chain.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _tool_whoami,
    },
    {
        "name": "echo",
        "description": "Echo back the supplied message. Demonstrates how "
                       "an MCP tool reads structured arguments.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Text to echo back verbatim.",
                },
            },
            "required": ["message"],
            "additionalProperties": False,
        },
        "handler": _tool_echo,
    },
]


# Mirror — name → handler — for O(1) dispatch in tools/call.
TOOLS_BY_NAME: dict[str, Any] = {t["name"]: t for t in TOOLS}


def public_descriptors() -> list[dict[str, Any]]:
    """Strip out the handler reference before sending the catalogue to
    clients — MCP only wants name + description + inputSchema."""
    return [
        {k: v for k, v in tool.items() if k != "handler"}
        for tool in TOOLS
    ]


def server_info() -> dict[str, Any]:
    """Identification block returned during the MCP ``initialize`` handshake."""
    return {
        "name": os.environ.get("MCP_SERVICE_NAME", "hello") + "-mcp",
        "version": "0.1.0",
        "platform": platform.python_implementation()
                    + " " + platform.python_version(),
    }
