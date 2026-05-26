"""clockify — Clockify v1 reads for the c8eapps team.

Auth is dead simple: ``X-Api-Key: <your_key>`` on every request. The key
comes from Clockify → Profile → API, and lives in the
``mcp-integration-creds`` k8s secret as ``CLOCKIFY_API_KEY``.

API reference: https://docs.clockify.me/
"""
from __future__ import annotations

import os
from typing import Any, Optional

import requests


API_KEY = os.environ.get("CLOCKIFY_API_KEY", "")
API_BASE = "https://api.clockify.me/api/v1"
REPORTS_API_BASE = "https://reports.api.clockify.me/v1"
USER_AGENT = "mcp-services (c8eapps; andrevanzuydam@gmail.com)"


class ClockifyError(Exception):
    """Anything that goes wrong talking to Clockify."""


def _ensure_configured() -> None:
    if not API_KEY:
        raise ClockifyError(
            "Clockify integration is not configured. Set CLOCKIFY_API_KEY "
            "in the mcp-integration-creds secret."
        )


def _headers() -> dict:
    _ensure_configured()
    return {
        "X-Api-Key": API_KEY,
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
    }


def _get(url: str, *, params: Optional[dict] = None) -> Any:
    resp = requests.get(url, headers=_headers(), params=params, timeout=20)
    if resp.status_code >= 400:
        raise ClockifyError(f"GET {url} → HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.content else None


def _post(url: str, *, json: dict) -> Any:
    resp = requests.post(url, headers=_headers(), json=json, timeout=30)
    if resp.status_code >= 400:
        raise ClockifyError(f"POST {url} → HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.content else None


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _list_workspaces(args: dict, claims: dict) -> dict:
    """All workspaces the API key has access to."""
    return {"workspaces": _get(f"{API_BASE}/workspaces")}


def _get_current_user(args: dict, claims: dict) -> dict:
    """The user the API key belongs to — useful for time-entry queries
    that default to ``user_id`` from this."""
    return _get(f"{API_BASE}/user")


def _list_projects(args: dict, claims: dict) -> dict:
    """List projects in a workspace. Optional ``archived`` filter."""
    ws = args["workspace_id"]
    params = {}
    if "archived" in args:
        params["archived"] = "true" if args["archived"] else "false"
    return {"projects": _get(f"{API_BASE}/workspaces/{ws}/projects", params=params)}


def _list_clients(args: dict, claims: dict) -> dict:
    """List clients in a workspace."""
    ws = args["workspace_id"]
    return {"clients": _get(f"{API_BASE}/workspaces/{ws}/clients")}


def _list_time_entries(args: dict, claims: dict) -> dict:
    """List a user's time entries in a date range. If ``user_id`` is
    omitted, uses the API key's owner."""
    ws = args["workspace_id"]
    user_id = args.get("user_id")
    if not user_id:
        user_id = _get(f"{API_BASE}/user")["id"]

    params = {}
    for src, dst in (("start", "start"), ("end", "end"), ("page_size", "page-size"), ("page", "page")):
        if args.get(src) is not None:
            params[dst] = args[src]

    entries = _get(f"{API_BASE}/workspaces/{ws}/user/{user_id}/time-entries", params=params)
    return {"time_entries": entries, "user_id": user_id}


def _summary_report(args: dict, claims: dict) -> dict:
    """Run a Clockify Summary Report — totals by ``group`` (PROJECT, USER,
    CLIENT, TASK, DATE, MONTH, ...) over a date range. POSTs to the
    Reports API which has a different host."""
    ws = args["workspace_id"]
    group = (args.get("group") or "PROJECT").upper()
    body = {
        "dateRangeStart": args["start"],
        "dateRangeEnd": args["end"],
        "summaryFilter": {"groups": [group]},
        "exportType": "JSON",
    }
    return _post(f"{REPORTS_API_BASE}/workspaces/{ws}/reports/summary", json=body)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "clockify.list_workspaces",
        "description": "List Clockify workspaces accessible to the configured API key.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": _list_workspaces,
    },
    {
        "name": "clockify.get_current_user",
        "description": "Return the user the configured API key belongs to. "
                       "Use the returned id for user-scoped queries.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": _get_current_user,
    },
    {
        "name": "clockify.list_projects",
        "description": "List projects in a workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "archived": {"type": "boolean", "description": "Filter by archived state."},
            },
            "required": ["workspace_id"],
            "additionalProperties": False,
        },
        "handler": _list_projects,
    },
    {
        "name": "clockify.list_clients",
        "description": "List clients in a workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {"workspace_id": {"type": "string"}},
            "required": ["workspace_id"],
            "additionalProperties": False,
        },
        "handler": _list_clients,
    },
    {
        "name": "clockify.list_time_entries",
        "description": "List a user's time entries in a date range. If "
                       "user_id is omitted, the API key's owner is used.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "user_id": {"type": "string", "description": "Optional. Defaults to API key owner."},
                "start": {"type": "string", "description": "ISO-8601 start, e.g. 2026-05-01T00:00:00Z"},
                "end":   {"type": "string", "description": "ISO-8601 end, e.g. 2026-05-31T23:59:59Z"},
                "page":      {"type": "integer", "description": "1-based page number."},
                "page_size": {"type": "integer", "description": "Items per page (max 5000)."},
            },
            "required": ["workspace_id"],
            "additionalProperties": False,
        },
        "handler": _list_time_entries,
    },
    {
        "name": "clockify.summary_report",
        "description": "Run a Clockify summary report — totals by PROJECT, "
                       "USER, CLIENT, TASK, DATE or MONTH over a date range.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_id": {"type": "string"},
                "start": {"type": "string", "description": "ISO-8601 start datetime."},
                "end":   {"type": "string", "description": "ISO-8601 end datetime."},
                "group": {
                    "type": "string",
                    "enum": ["PROJECT", "USER", "CLIENT", "TASK", "DATE", "MONTH", "TAG"],
                    "description": "What to group totals by. Default PROJECT.",
                },
            },
            "required": ["workspace_id", "start", "end"],
            "additionalProperties": False,
        },
        "handler": _summary_report,
    },
]
