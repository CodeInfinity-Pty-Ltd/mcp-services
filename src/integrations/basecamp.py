"""basecamp — Basecamp 3 reads for the c8eapps team.

Auth is OAuth 2 against ``launchpad.37signals.com`` (Basecamp's identity
service). The one-time browser flow gives you a long-lived refresh token;
this module swaps it for fresh 2-week access tokens on demand. See
``plan/add-basecamp-clockify.md`` for the setup walkthrough.

Env vars required (all set via the ``mcp-integration-creds`` k8s secret):

    BASECAMP_CLIENT_ID
    BASECAMP_CLIENT_SECRET
    BASECAMP_REFRESH_TOKEN
    BASECAMP_ACCOUNT_ID

The HTTP layer is just ``requests`` — Basecamp's API is plain JSON over
HTTPS, so no SDK is necessary.

API reference: https://github.com/basecamp/bc3-api
"""
from __future__ import annotations

import os
import time
from typing import Any, Optional

import requests


# ---------------------------------------------------------------------------
# Configuration — pulled from env at import time. Missing values disable
# the integration with a clear error on the first call rather than
# crashing the whole pod (other integrations should still work).
# ---------------------------------------------------------------------------

CLIENT_ID = os.environ.get("BASECAMP_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("BASECAMP_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("BASECAMP_REFRESH_TOKEN", "")
ACCOUNT_ID = os.environ.get("BASECAMP_ACCOUNT_ID", "")

TOKEN_URL = "https://launchpad.37signals.com/authorization/token"
API_BASE = "https://3.basecampapi.com"
USER_AGENT = "mcp-services (c8eapps; andrevanzuydam@gmail.com)"

# Refresh ``REFRESH_WINDOW_SECONDS`` before the token actually expires so a
# concurrent request doesn't slip through with a token that's about to die.
REFRESH_WINDOW_SECONDS = 60

# Module-level cache for the access_token + expiry. Tina4 runs single-
# threaded inside asyncio, so a plain dict is enough.
_access_token: Optional[str] = None
_expires_at: float = 0.0


class BasecampError(Exception):
    """Anything that goes wrong talking to Basecamp."""


def _ensure_configured() -> None:
    missing = [
        name for name, val in (
            ("BASECAMP_CLIENT_ID", CLIENT_ID),
            ("BASECAMP_CLIENT_SECRET", CLIENT_SECRET),
            ("BASECAMP_REFRESH_TOKEN", REFRESH_TOKEN),
            ("BASECAMP_ACCOUNT_ID", ACCOUNT_ID),
        ) if not val
    ]
    if missing:
        raise BasecampError(
            "Basecamp integration is not configured. Missing env vars: "
            + ", ".join(missing)
            + ". See plan/add-basecamp-clockify.md for the one-time OAuth setup."
        )


def _refresh_access_token() -> None:
    """Swap our long-lived refresh_token for a fresh ~2-week access_token."""
    global _access_token, _expires_at
    resp = requests.post(
        TOKEN_URL,
        params={
            "type": "refresh",
            "refresh_token": REFRESH_TOKEN,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    if resp.status_code != 200:
        raise BasecampError(
            f"token refresh failed (HTTP {resp.status_code}): {resp.text[:200]}"
        )
    data = resp.json()
    _access_token = data["access_token"]
    # `expires_in` is seconds. Subtract our refresh window so callers never
    # see a token in its last minute of life.
    _expires_at = time.time() + int(data.get("expires_in", 1209600)) - REFRESH_WINDOW_SECONDS


def _headers() -> dict:
    """Return the auth headers, refreshing the access token if needed."""
    _ensure_configured()
    if _access_token is None or time.time() > _expires_at:
        _refresh_access_token()
    return {
        "Authorization": f"Bearer {_access_token}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }


def _account_url(path: str) -> str:
    """Join a Basecamp API path to the account-scoped base URL."""
    return f"{API_BASE}/{ACCOUNT_ID}/{path.lstrip('/')}"


def _get(path: str, *, params: Optional[dict] = None) -> Any:
    """GET helper with our auth + UA. Used by every tool."""
    resp = requests.get(_account_url(path), headers=_headers(), params=params, timeout=20)
    if resp.status_code == 401:
        # Refresh and retry once — covers the case where the cached token
        # expired between two clock checks.
        _refresh_access_token()
        resp = requests.get(_account_url(path), headers=_headers(), params=params, timeout=20)
    if resp.status_code >= 400:
        raise BasecampError(f"GET {path} → HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.content else None


# ---------------------------------------------------------------------------
# Tool implementations — all read-only.
# ---------------------------------------------------------------------------

def _list_projects(args: dict, claims: dict) -> dict:
    """List active projects. Use ``status=archived|trashed`` to override."""
    status = args.get("status") or "active"
    return {"projects": _get("projects.json", params={"status": status})}


def _get_project(args: dict, claims: dict) -> dict:
    """Fetch a single project by id, including its dock (tools)."""
    pid = args["project_id"]
    return _get(f"projects/{pid}.json")


def _list_people(args: dict, claims: dict) -> dict:
    """List every person on the account."""
    return {"people": _get("people.json")}


def _list_todosets(args: dict, claims: dict) -> dict:
    """List the to-do *sets* visible on a project's dock. Drill into one
    with ``list_todos``."""
    pid = args["project_id"]
    project = _get(f"projects/{pid}.json")
    sets = [d for d in project.get("dock", []) if d.get("name") == "todoset"]
    return {"todosets": sets}


def _list_todos(args: dict, claims: dict) -> dict:
    """List to-dos in a single to-do list. Pass ``todolist_id``."""
    pid = args["project_id"]
    list_id = args["todolist_id"]
    return {"todos": _get(f"buckets/{pid}/todolists/{list_id}/todos.json")}


def _list_card_tables(args: dict, claims: dict) -> dict:
    """List the card-table (kanban board) docks for a project."""
    pid = args["project_id"]
    project = _get(f"projects/{pid}.json")
    tables = [d for d in project.get("dock", []) if d.get("name") == "kanban_board"]
    return {"card_tables": tables}


def _list_cards(args: dict, claims: dict) -> dict:
    """List cards in one column of a card table. Pass ``column_id``."""
    pid = args["project_id"]
    col_id = args["column_id"]
    return {"cards": _get(f"buckets/{pid}/card_tables/lists/{col_id}/cards.json")}


def _get_card(args: dict, claims: dict) -> dict:
    """Fetch one card with its full body, assignees, due_on."""
    pid = args["project_id"]
    card_id = args["card_id"]
    return _get(f"buckets/{pid}/card_tables/cards/{card_id}.json")


def _list_messages(args: dict, claims: dict) -> dict:
    """List messages on a project's message board. Pass ``message_board_id``."""
    pid = args["project_id"]
    board_id = args["message_board_id"]
    return {"messages": _get(f"buckets/{pid}/message_boards/{board_id}/messages.json")}


def _list_comments_on(args: dict, claims: dict) -> dict:
    """List comments attached to any recording (todo, card, message...).
    Pass the recording's ``recording_id`` along with ``project_id``."""
    pid = args["project_id"]
    rec_id = args["recording_id"]
    return {"comments": _get(f"buckets/{pid}/recordings/{rec_id}/comments.json")}


def _list_schedule_entries(args: dict, claims: dict) -> dict:
    """List events on a project's schedule. Pass ``schedule_id``."""
    pid = args["project_id"]
    sched_id = args["schedule_id"]
    return {"entries": _get(f"buckets/{pid}/schedules/{sched_id}/entries.json")}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "basecamp.list_projects",
        "description": "List Basecamp projects. Defaults to active; pass "
                       "status='archived' or 'trashed' to see others.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "archived", "trashed"],
                    "description": "Filter by project status. Default 'active'.",
                },
            },
            "additionalProperties": False,
        },
        "handler": _list_projects,
    },
    {
        "name": "basecamp.get_project",
        "description": "Fetch one project including its dock — the dock "
                       "entries point at todoset_id, message_board_id, "
                       "schedule_id, kanban_board (card_table) id, etc.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer", "description": "Basecamp project id."},
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
        "handler": _get_project,
    },
    {
        "name": "basecamp.list_people",
        "description": "List everyone on the Basecamp account.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": _list_people,
    },
    {
        "name": "basecamp.list_todosets",
        "description": "List the to-do *sets* on a project's dock. Use the "
                       "set's id to drill into individual to-do lists.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "integer"}},
            "required": ["project_id"],
            "additionalProperties": False,
        },
        "handler": _list_todosets,
    },
    {
        "name": "basecamp.list_todos",
        "description": "List to-dos inside one to-do list.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "todolist_id": {"type": "integer"},
            },
            "required": ["project_id", "todolist_id"],
            "additionalProperties": False,
        },
        "handler": _list_todos,
    },
    {
        "name": "basecamp.list_card_tables",
        "description": "List the card-table (kanban board) docks for a "
                       "project. Drill into one with list_cards.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "integer"}},
            "required": ["project_id"],
            "additionalProperties": False,
        },
        "handler": _list_card_tables,
    },
    {
        "name": "basecamp.list_cards",
        "description": "List cards inside one column of a card table.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "column_id": {"type": "integer", "description": "Card table column (list) id."},
            },
            "required": ["project_id", "column_id"],
            "additionalProperties": False,
        },
        "handler": _list_cards,
    },
    {
        "name": "basecamp.get_card",
        "description": "Fetch one card with body, assignees and due date.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "card_id": {"type": "integer"},
            },
            "required": ["project_id", "card_id"],
            "additionalProperties": False,
        },
        "handler": _get_card,
    },
    {
        "name": "basecamp.list_messages",
        "description": "List messages on a project's message board.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "message_board_id": {"type": "integer"},
            },
            "required": ["project_id", "message_board_id"],
            "additionalProperties": False,
        },
        "handler": _list_messages,
    },
    {
        "name": "basecamp.list_comments_on",
        "description": "List comments on any recording (todo, card, "
                       "message, ...). Pass the recording_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "recording_id": {"type": "integer"},
            },
            "required": ["project_id", "recording_id"],
            "additionalProperties": False,
        },
        "handler": _list_comments_on,
    },
    {
        "name": "basecamp.list_schedule_entries",
        "description": "List events on a project's schedule.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "integer"},
                "schedule_id": {"type": "integer"},
            },
            "required": ["project_id", "schedule_id"],
            "additionalProperties": False,
        },
        "handler": _list_schedule_entries,
    },
]
