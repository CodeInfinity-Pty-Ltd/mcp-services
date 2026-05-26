"""hello — minimal template integration.

Three tools that exercise the full stack (MCP transport → OAuth → response
shape) so a developer can confirm the whole chain works before wiring up
their own integration.

Add a new integration: drop a sibling file (``postaction.py``, ``fxcm.py``,
…) that declares a ``TOOLS`` list with the same shape. ``mcp_server.py``
picks them up by importing ``src.integrations`` and reading their TOOLS.

Tool naming convention is ``<integration>.<action>`` — flat MCP namespace,
dotted for human grouping.
"""
from __future__ import annotations

import os
import platform
import time
from typing import Any

from src.app.auth import whoami as _whoami_claims


# ---------------------------------------------------------------------------
# Tool implementations — kept tiny on purpose. Anything heavier belongs in
# its own helper module under src/app/.
# ---------------------------------------------------------------------------

def _ping(args: dict, claims: dict) -> dict:
    """Round-trip probe so a client can confirm the connection is live."""
    return {
        "result": "pong",
        "server_time": int(time.time()),
        "service": os.environ.get("MCP_SERVICE_NAME", "mcp-services"),
        "platform": platform.python_implementation() + " " + platform.python_version(),
    }


def _whoami(args: dict, claims: dict) -> dict:
    """Echo the validated JWT claims so the caller can see who Keycloak
    said they are. Useful first-call sanity check after wiring Claude.ai."""
    return _whoami_claims(claims)


def _echo(args: dict, claims: dict) -> dict:
    """Round-trip whatever the caller sent under ``message``. Demonstrates
    structured-argument handling for new integrations."""
    return {"echo": args.get("message", "")}


# ---------------------------------------------------------------------------
# Public registry — mcp_server imports this list and merges it with every
# other integration's TOOLS.
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "hello.ping",
        "description": "Liveness check — returns 'pong' and the server's "
                       "current time. Use this to confirm the connection.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _ping,
    },
    {
        "name": "hello.whoami",
        "description": "Return the OAuth-authenticated user's claims as "
                       "seen by this MCP server. Confirms the auth chain.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "handler": _whoami,
    },
    {
        "name": "hello.echo",
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
        "handler": _echo,
    },
]
