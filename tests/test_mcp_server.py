"""Unit tests for src.app.mcp_server.

These cover the JSON-RPC dispatcher in isolation — no HTTP layer, no
Keycloak. The route layer (src/routes/mcp.py) is thin enough that exercising
the dispatcher gives us most of the coverage we need.

Run with: uv run pytest -q
"""
from __future__ import annotations

import pytest

from src.app import mcp_server


# A fake set of JWT claims the dispatcher hands to tool handlers. Real
# requests get this from src.app.auth.validate_request; here we just
# stub it.
FAKE_CLAIMS = {
    "sub": "test-user",
    "email": "test@example.com",
    "preferred_username": "tester",
    "iss": "test",
}


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------

def test_initialize_negotiates_known_protocol_version():
    msg = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18"},
    }
    reply = mcp_server.dispatch(msg, FAKE_CLAIMS)
    assert reply["id"] == 1
    assert reply["result"]["protocolVersion"] == "2025-06-18"
    assert reply["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert reply["result"]["serverInfo"]["name"]


def test_initialize_falls_back_to_default_for_unknown_version():
    msg = {
        "jsonrpc": "2.0", "id": 2, "method": "initialize",
        "params": {"protocolVersion": "1999-01-01"},
    }
    reply = mcp_server.dispatch(msg, FAKE_CLAIMS)
    assert reply["result"]["protocolVersion"] == mcp_server.DEFAULT_PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# tools/list — must contain the namespaced hello.* tools
# ---------------------------------------------------------------------------

def test_tools_list_includes_hello_namespace():
    msg = {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
    reply = mcp_server.dispatch(msg, FAKE_CLAIMS)
    names = {t["name"] for t in reply["result"]["tools"]}
    assert {"hello.ping", "hello.whoami", "hello.echo"} <= names


def test_tools_list_omits_handler_field():
    """Handler refs are an internal implementation detail and must not leak
    over the wire."""
    msg = {"jsonrpc": "2.0", "id": 4, "method": "tools/list"}
    reply = mcp_server.dispatch(msg, FAKE_CLAIMS)
    for tool in reply["result"]["tools"]:
        assert "handler" not in tool


# ---------------------------------------------------------------------------
# tools/call
# ---------------------------------------------------------------------------

def test_tools_call_hello_ping_returns_pong():
    msg = {
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "hello.ping", "arguments": {}},
    }
    reply = mcp_server.dispatch(msg, FAKE_CLAIMS)
    assert reply["result"]["isError"] is False
    assert reply["result"]["structuredContent"]["result"] == "pong"


def test_tools_call_hello_whoami_echoes_claims():
    msg = {
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "hello.whoami", "arguments": {}},
    }
    reply = mcp_server.dispatch(msg, FAKE_CLAIMS)
    body = reply["result"]["structuredContent"]
    assert body["sub"] == FAKE_CLAIMS["sub"]
    assert body["email"] == FAKE_CLAIMS["email"]


def test_tools_call_hello_echo_round_trips_message():
    msg = {
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "hello.echo", "arguments": {"message": "hi there"}},
    }
    reply = mcp_server.dispatch(msg, FAKE_CLAIMS)
    assert reply["result"]["structuredContent"]["echo"] == "hi there"


def test_tools_call_unknown_tool_is_method_not_found():
    msg = {
        "jsonrpc": "2.0", "id": 8, "method": "tools/call",
        "params": {"name": "doesnotexist", "arguments": {}},
    }
    reply = mcp_server.dispatch(msg, FAKE_CLAIMS)
    assert reply["error"]["code"] == mcp_server.METHOD_NOT_FOUND


def test_tools_call_missing_name_is_invalid_params():
    msg = {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {}}
    reply = mcp_server.dispatch(msg, FAKE_CLAIMS)
    assert reply["error"]["code"] == mcp_server.INVALID_PARAMS


# ---------------------------------------------------------------------------
# Notifications never get a reply
# ---------------------------------------------------------------------------

def test_notification_returns_none():
    msg = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    reply = mcp_server.dispatch(msg, FAKE_CLAIMS)
    assert reply is None


# ---------------------------------------------------------------------------
# JSON-RPC envelope validation
# ---------------------------------------------------------------------------

def test_missing_jsonrpc_version_is_invalid_request():
    msg = {"id": 10, "method": "initialize"}
    reply = mcp_server.dispatch(msg, FAKE_CLAIMS)
    assert reply["error"]["code"] == mcp_server.INVALID_REQUEST


def test_unknown_method_is_method_not_found():
    msg = {"jsonrpc": "2.0", "id": 11, "method": "this/does/not/exist"}
    reply = mcp_server.dispatch(msg, FAKE_CLAIMS)
    assert reply["error"]["code"] == mcp_server.METHOD_NOT_FOUND


# ---------------------------------------------------------------------------
# Batch dispatch — notifications are dropped from the reply array
# ---------------------------------------------------------------------------

def test_batch_drops_notification_slots():
    batch = [
        {"jsonrpc": "2.0", "id": 100, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},  # notification
        {"jsonrpc": "2.0", "id": 101, "method": "tools/list"},
    ]
    replies = mcp_server.dispatch_batch(batch, FAKE_CLAIMS)
    assert len(replies) == 2
    assert {r["id"] for r in replies} == {100, 101}


# ---------------------------------------------------------------------------
# Tool name collisions across integrations must be caught at import time
# ---------------------------------------------------------------------------

def test_no_duplicate_tool_names():
    """Sanity check that no two integrations registered the same tool name."""
    seen = set()
    for name in mcp_server.TOOLS_BY_NAME:
        assert name not in seen, f"duplicate tool name: {name}"
        seen.add(name)
