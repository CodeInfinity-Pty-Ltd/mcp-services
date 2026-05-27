"""basecamp — Basecamp 3 read/write for the c8eapps team.

Auth is OAuth 2 against ``launchpad.37signals.com`` (Basecamp's identity
service). The one-time browser flow gives you a long-lived refresh token;
this module swaps it for fresh 2-week access tokens on demand.

API surface mirrors ``basecamp-mcp-server`` (the TypeScript reference at
~/IdeaProjects/basecamp-mcp-server) so existing prompts and integrations
keep working: projects, people, todos, card tables (kanban) with full
CRUD, comments, campfire chat lines, documents, uploads, webhooks, daily
check-ins.

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
USER_AGENT = os.environ.get(
    "BASECAMP_USER_AGENT",
    "mcp-services (c8eapps; andrevanzuydam@gmail.com)",
)

# Refresh ``REFRESH_WINDOW_SECONDS`` before the token actually expires so a
# concurrent request doesn't slip through with a token that's about to die.
REFRESH_WINDOW_SECONDS = 60

# Basecamp paginates list endpoints at 15 items/page. We follow pages
# until we get a short response (or the safety cap, just in case).
PAGE_SIZE = 15
MAX_PAGES = 200

# Module-level cache for the access_token + expiry. Tina4 runs single-
# threaded inside asyncio, so a plain dict is enough.
_access_token: Optional[str] = None
_expires_at: float = 0.0


class BasecampError(Exception):
    """Anything that goes wrong talking to Basecamp."""


# ---------------------------------------------------------------------------
# Auth — refresh + headers
# ---------------------------------------------------------------------------

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
            + ". See the README for the one-time OAuth setup."
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
    _expires_at = time.time() + int(data.get("expires_in", 1209600)) - REFRESH_WINDOW_SECONDS


def _headers(*, json_body: bool = False) -> dict:
    _ensure_configured()
    if _access_token is None or time.time() > _expires_at:
        _refresh_access_token()
    headers = {
        "Authorization": f"Bearer {_access_token}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if json_body:
        headers["Content-Type"] = "application/json"
    return headers


def _account_url(path: str) -> str:
    return f"{API_BASE}/{ACCOUNT_ID}/{path.lstrip('/')}"


# ---------------------------------------------------------------------------
# HTTP helpers (one per verb) with auto-refresh on 401
# ---------------------------------------------------------------------------

def _request(method: str, path: str, *, params=None, json=None) -> Any:
    """Single request with a refresh-and-retry on 401 (covers the case
    where a cached token expires between the clock-check and the call)."""
    url = _account_url(path)
    resp = requests.request(
        method, url, headers=_headers(json_body=json is not None),
        params=params, json=json, timeout=20,
    )
    if resp.status_code == 401:
        _refresh_access_token()
        resp = requests.request(
            method, url, headers=_headers(json_body=json is not None),
            params=params, json=json, timeout=20,
        )
    if resp.status_code == 204:
        return None
    if resp.status_code >= 400:
        raise BasecampError(f"{method} {path} → HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json() if resp.content else None


def _get(path, **kw):    return _request("GET", path, **kw)
def _post(path, **kw):   return _request("POST", path, **kw)
def _put(path, **kw):    return _request("PUT", path, **kw)
def _patch(path, **kw):  return _request("PATCH", path, **kw)
def _delete(path, **kw): return _request("DELETE", path, **kw)


def _paginate(path: str, *, params: Optional[dict] = None) -> list:
    """Follow every page of a paginated list endpoint until we get a short
    response. Matches the TS reference's behaviour (15 items per page;
    stop on partial page or empty)."""
    out: list = []
    page = 1
    while page <= MAX_PAGES:
        merged = dict(params or {}, page=page)
        batch = _get(path, params=merged) or []
        if not isinstance(batch, list):
            # Some endpoints return objects, not lists — pass through and stop.
            return batch
        if not batch:
            break
        out.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    return out


# ---------------------------------------------------------------------------
# Tool implementations — read
# ---------------------------------------------------------------------------

def _list_projects(args: dict, claims: dict) -> dict:
    """All active projects (pass status='archived'|'trashed' to see others).

    Basecamp's projects endpoint only accepts ``status=archived`` or
    ``status=trashed`` — sending ``status=active`` returns HTTP 400. Omit
    the param entirely for the default active list."""
    status = args.get("status")
    params = {"status": status} if status in ("archived", "trashed") else None
    return {"projects": _paginate("projects.json", params=params)}


def _get_project(args: dict, claims: dict) -> dict:
    """One project including its dock (todoset, kanban_board, message_board, ...)."""
    return _get(f"projects/{args['project_id']}.json")


def _list_people(args: dict, claims: dict) -> dict:
    """Every person on the account."""
    return {"people": _paginate("people.json")}


def _list_todosets(args: dict, claims: dict) -> dict:
    pid = args["project_id"]
    project = _get(f"projects/{pid}.json")
    return {"todosets": [d for d in (project.get("dock") or []) if d.get("name") == "todoset"]}


def _list_todolists(args: dict, claims: dict) -> dict:
    """Lists inside a to-do set."""
    pid = args["project_id"]
    set_id = args["todoset_id"]
    return {"todolists": _paginate(f"buckets/{pid}/todosets/{set_id}/todolists.json")}


def _list_todos(args: dict, claims: dict) -> dict:
    """To-dos inside one list."""
    pid = args["project_id"]
    return {"todos": _paginate(f"buckets/{pid}/todolists/{args['todolist_id']}/todos.json")}


def _get_todo(args: dict, claims: dict) -> dict:
    """One to-do by id."""
    return _get(f"buckets/{args['project_id']}/todos/{args['todo_id']}.json")


# ----- Card tables (kanban) -------------------------------------------------

def _list_card_tables(args: dict, claims: dict) -> dict:
    pid = args["project_id"]
    project = _get(f"projects/{pid}.json")
    return {"card_tables": [
        d for d in (project.get("dock") or [])
        if d.get("name") in ("kanban_board", "card_table")
    ]}


def _get_card_table(args: dict, claims: dict) -> dict:
    """Full card-table object with its columns (``lists``)."""
    pid = args["project_id"]
    ctid = args["card_table_id"]
    try:
        return _get(f"buckets/{pid}/card_tables/{ctid}.json")
    except BasecampError as exc:
        # Empty card table returns 204 in some accounts — surface a stub.
        if "HTTP 204" in str(exc):
            return {"id": ctid, "title": "Card Table", "lists": [], "status": "empty"}
        raise


def _list_columns(args: dict, claims: dict) -> dict:
    """All columns in a card table."""
    table = _get_card_table(args, claims)
    return {"columns": table.get("lists", [])}


def _get_column(args: dict, claims: dict) -> dict:
    return _get(f"buckets/{args['project_id']}/card_tables/columns/{args['column_id']}.json")


def _create_column(args: dict, claims: dict) -> dict:
    return _post(
        f"buckets/{args['project_id']}/card_tables/{args['card_table_id']}/columns.json",
        json={"title": args["title"]},
    )


def _update_column(args: dict, claims: dict) -> dict:
    return _put(
        f"buckets/{args['project_id']}/card_tables/columns/{args['column_id']}.json",
        json={"title": args["title"]},
    )


def _move_column(args: dict, claims: dict) -> dict:
    _post(
        f"buckets/{args['project_id']}/card_tables/{args['card_table_id']}/moves.json",
        json={
            "source_id": args["column_id"],
            "target_id": args["card_table_id"],
            "position": args["position"],
        },
    )
    return {"moved": True}


def _update_column_color(args: dict, claims: dict) -> dict:
    return _patch(
        f"buckets/{args['project_id']}/card_tables/columns/{args['column_id']}/color.json",
        json={"color": args["color"]},
    )


# ----- Cards ----------------------------------------------------------------

def _list_cards(args: dict, claims: dict) -> dict:
    return {"cards": _paginate(
        f"buckets/{args['project_id']}/card_tables/lists/{args['column_id']}/cards.json"
    )}


def _get_card(args: dict, claims: dict) -> dict:
    return _get(f"buckets/{args['project_id']}/card_tables/cards/{args['card_id']}.json")


def _create_card(args: dict, claims: dict) -> dict:
    body = {"title": args["title"]}
    for k in ("content", "due_on", "notify"):
        if k in args and args[k] is not None:
            body[k] = args[k]
    return _post(
        f"buckets/{args['project_id']}/card_tables/lists/{args['column_id']}/cards.json",
        json=body,
    )


def _update_card(args: dict, claims: dict) -> dict:
    body = {}
    for k in ("title", "content", "due_on", "assignee_ids"):
        if k in args and args[k] is not None:
            body[k] = args[k]
    return _put(
        f"buckets/{args['project_id']}/card_tables/cards/{args['card_id']}.json",
        json=body,
    )


def _move_card(args: dict, claims: dict) -> dict:
    _post(
        f"buckets/{args['project_id']}/card_tables/cards/{args['card_id']}/moves.json",
        json={"column_id": args["column_id"]},
    )
    return {"moved": True, "column_id": args["column_id"]}


def _complete_card(args: dict, claims: dict) -> dict:
    _post(f"buckets/{args['project_id']}/todos/{args['card_id']}/completion.json")
    return {"completed": True}


# ----- Card steps (sub-tasks on a card) -------------------------------------

def _get_card_steps(args: dict, claims: dict) -> dict:
    card = _get_card(args, claims)
    return {"steps": card.get("steps") or []}


def _create_card_step(args: dict, claims: dict) -> dict:
    body = {"title": args["title"]}
    for k in ("due_on", "assignee_ids"):
        if k in args and args[k] is not None:
            body[k] = args[k]
    return _post(
        f"buckets/{args['project_id']}/card_tables/cards/{args['card_id']}/steps.json",
        json=body,
    )


def _complete_card_step(args: dict, claims: dict) -> dict:
    _post(f"buckets/{args['project_id']}/todos/{args['step_id']}/completion.json")
    return {"completed": True}


# ----- Communications -------------------------------------------------------

def _list_messages(args: dict, claims: dict) -> dict:
    return {"messages": _paginate(
        f"buckets/{args['project_id']}/message_boards/{args['message_board_id']}/messages.json"
    )}


def _list_campfire_lines(args: dict, claims: dict) -> dict:
    """Chat lines in a project's campfire."""
    return {"lines": _paginate(
        f"buckets/{args['project_id']}/chats/{args['campfire_id']}/lines.json"
    )}


def _list_comments(args: dict, claims: dict) -> dict:
    """Comments on any recording (todo, card, message, ...)."""
    return {"comments": _paginate(
        f"buckets/{args['project_id']}/recordings/{args['recording_id']}/comments.json"
    )}


def _list_schedule_entries(args: dict, claims: dict) -> dict:
    return {"entries": _paginate(
        f"buckets/{args['project_id']}/schedules/{args['schedule_id']}/entries.json"
    )}


# ----- Documents + uploads + webhooks --------------------------------------

def _list_documents(args: dict, claims: dict) -> dict:
    return {"documents": _paginate(
        f"buckets/{args['project_id']}/vaults/{args['vault_id']}/documents.json"
    )}


def _get_document(args: dict, claims: dict) -> dict:
    return _get(f"buckets/{args['project_id']}/documents/{args['document_id']}.json")


def _list_uploads(args: dict, claims: dict) -> dict:
    pid = args["project_id"]
    if args.get("vault_id"):
        return {"uploads": _get(f"buckets/{pid}/vaults/{args['vault_id']}/uploads.json")}
    return {"uploads": _get(f"buckets/{pid}/uploads.json")}


def _list_webhooks(args: dict, claims: dict) -> dict:
    return {"webhooks": _get(f"buckets/{args['project_id']}/webhooks.json")}


# ----- Check-ins -----------------------------------------------------------

def _list_daily_check_ins(args: dict, claims: dict) -> dict:
    pid = args["project_id"]
    project = _get(f"projects/{pid}.json")
    questionnaire = next(
        (d for d in (project.get("dock") or []) if d.get("name") == "questionnaire"),
        None,
    )
    if not questionnaire:
        raise BasecampError(f"No questionnaire dock on project {pid}")
    return {"check_ins": _get(
        f"buckets/{pid}/questionnaires/{questionnaire['id']}/questions.json",
        params={"page": args.get("page", 1)},
    )}


def _list_question_answers(args: dict, claims: dict) -> dict:
    return {"answers": _get(
        f"buckets/{args['project_id']}/questions/{args['question_id']}/answers.json",
        params={"page": args.get("page", 1)},
    )}


# ---------------------------------------------------------------------------
# Tool catalogue
# ---------------------------------------------------------------------------

def _obj(props: dict, required: list[str] | None = None) -> dict:
    """Shorthand to build an inputSchema dict."""
    schema = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


_STR  = {"type": "string"}
_INT  = {"type": "integer"}
_BOOL = {"type": "boolean"}
_ARR_S = {"type": "array", "items": {"type": "string"}}


TOOLS: list[dict[str, Any]] = [
    # ---- projects + people ----
    {"name": "basecamp.list_projects",
     "description": "List Basecamp projects (default: active; pass status='archived' or 'trashed').",
     "inputSchema": _obj({"status": {**_STR, "enum": ["active", "archived", "trashed"]}}),
     "handler": _list_projects},
    {"name": "basecamp.get_project",
     "description": "Fetch one project including its dock — dock entries point at todoset_id, message_board_id, schedule_id, kanban_board (card_table) id, etc.",
     "inputSchema": _obj({"project_id": _INT}, ["project_id"]),
     "handler": _get_project},
    {"name": "basecamp.list_people",
     "description": "List everyone on the Basecamp account.",
     "inputSchema": _obj({}),
     "handler": _list_people},

    # ---- todos ----
    {"name": "basecamp.list_todosets",
     "description": "List the todo *sets* on a project's dock. Drill in with list_todolists.",
     "inputSchema": _obj({"project_id": _INT}, ["project_id"]),
     "handler": _list_todosets},
    {"name": "basecamp.list_todolists",
     "description": "List todo *lists* inside a todo set.",
     "inputSchema": _obj({"project_id": _INT, "todoset_id": _INT}, ["project_id", "todoset_id"]),
     "handler": _list_todolists},
    {"name": "basecamp.list_todos",
     "description": "List todos inside one todo list.",
     "inputSchema": _obj({"project_id": _INT, "todolist_id": _INT}, ["project_id", "todolist_id"]),
     "handler": _list_todos},
    {"name": "basecamp.get_todo",
     "description": "Fetch one todo by id (full description, assignees, due_on, completion state).",
     "inputSchema": _obj({"project_id": _INT, "todo_id": _INT}, ["project_id", "todo_id"]),
     "handler": _get_todo},

    # ---- card tables (kanban) ----
    {"name": "basecamp.list_card_tables",
     "description": "List the card-table (kanban) docks for a project.",
     "inputSchema": _obj({"project_id": _INT}, ["project_id"]),
     "handler": _list_card_tables},
    {"name": "basecamp.get_card_table",
     "description": "Full card-table object including its columns (lists).",
     "inputSchema": _obj({"project_id": _INT, "card_table_id": _INT}, ["project_id", "card_table_id"]),
     "handler": _get_card_table},
    {"name": "basecamp.list_columns",
     "description": "List columns (lists) in a card table.",
     "inputSchema": _obj({"project_id": _INT, "card_table_id": _INT}, ["project_id", "card_table_id"]),
     "handler": _list_columns},
    {"name": "basecamp.get_column",
     "description": "Fetch one column with its metadata.",
     "inputSchema": _obj({"project_id": _INT, "column_id": _INT}, ["project_id", "column_id"]),
     "handler": _get_column},
    {"name": "basecamp.create_column",
     "description": "Create a new column in a card table.",
     "inputSchema": _obj(
         {"project_id": _INT, "card_table_id": _INT, "title": _STR},
         ["project_id", "card_table_id", "title"],
     ),
     "handler": _create_column},
    {"name": "basecamp.update_column",
     "description": "Rename a column.",
     "inputSchema": _obj(
         {"project_id": _INT, "column_id": _INT, "title": _STR},
         ["project_id", "column_id", "title"],
     ),
     "handler": _update_column},
    {"name": "basecamp.move_column",
     "description": "Move a column to a new 1-based position on its card table.",
     "inputSchema": _obj(
         {"project_id": _INT, "card_table_id": _INT, "column_id": _INT, "position": _INT},
         ["project_id", "card_table_id", "column_id", "position"],
     ),
     "handler": _move_column},
    {"name": "basecamp.update_column_color",
     "description": "Set a column's color (hex like '#FF0000').",
     "inputSchema": _obj(
         {"project_id": _INT, "column_id": _INT, "color": _STR},
         ["project_id", "column_id", "color"],
     ),
     "handler": _update_column_color},

    # ---- cards ----
    {"name": "basecamp.list_cards",
     "description": "Cards in one column.",
     "inputSchema": _obj({"project_id": _INT, "column_id": _INT}, ["project_id", "column_id"]),
     "handler": _list_cards},
    {"name": "basecamp.get_card",
     "description": "One card with body, assignees, due date and steps.",
     "inputSchema": _obj({"project_id": _INT, "card_id": _INT}, ["project_id", "card_id"]),
     "handler": _get_card},
    {"name": "basecamp.create_card",
     "description": "Create a new card in a column.",
     "inputSchema": _obj(
         {
             "project_id": _INT, "column_id": _INT, "title": _STR,
             "content": _STR, "due_on": _STR, "notify": _BOOL,
         },
         ["project_id", "column_id", "title"],
     ),
     "handler": _create_card},
    {"name": "basecamp.update_card",
     "description": "Update a card's title / content / due_on / assignees.",
     "inputSchema": _obj(
         {
             "project_id": _INT, "card_id": _INT, "title": _STR,
             "content": _STR, "due_on": _STR, "assignee_ids": _ARR_S,
         },
         ["project_id", "card_id"],
     ),
     "handler": _update_card},
    {"name": "basecamp.move_card",
     "description": "Move a card to a different column.",
     "inputSchema": _obj(
         {"project_id": _INT, "card_id": _INT, "column_id": _INT},
         ["project_id", "card_id", "column_id"],
     ),
     "handler": _move_card},
    {"name": "basecamp.complete_card",
     "description": "Mark a card complete.",
     "inputSchema": _obj({"project_id": _INT, "card_id": _INT}, ["project_id", "card_id"]),
     "handler": _complete_card},

    # ---- card steps ----
    {"name": "basecamp.get_card_steps",
     "description": "Sub-tasks (steps) attached to a card.",
     "inputSchema": _obj({"project_id": _INT, "card_id": _INT}, ["project_id", "card_id"]),
     "handler": _get_card_steps},
    {"name": "basecamp.create_card_step",
     "description": "Add a sub-task to a card.",
     "inputSchema": _obj(
         {
             "project_id": _INT, "card_id": _INT, "title": _STR,
             "due_on": _STR, "assignee_ids": _ARR_S,
         },
         ["project_id", "card_id", "title"],
     ),
     "handler": _create_card_step},
    {"name": "basecamp.complete_card_step",
     "description": "Mark a card step (sub-task) complete.",
     "inputSchema": _obj({"project_id": _INT, "step_id": _INT}, ["project_id", "step_id"]),
     "handler": _complete_card_step},

    # ---- comms ----
    {"name": "basecamp.list_messages",
     "description": "Messages on a project's message board.",
     "inputSchema": _obj(
         {"project_id": _INT, "message_board_id": _INT},
         ["project_id", "message_board_id"],
     ),
     "handler": _list_messages},
    {"name": "basecamp.list_campfire_lines",
     "description": "Chat lines in a project's campfire room.",
     "inputSchema": _obj(
         {"project_id": _INT, "campfire_id": _INT},
         ["project_id", "campfire_id"],
     ),
     "handler": _list_campfire_lines},
    {"name": "basecamp.list_comments",
     "description": "Comments on any recording (todo, card, message, document, ...).",
     "inputSchema": _obj(
         {"project_id": _INT, "recording_id": _INT},
         ["project_id", "recording_id"],
     ),
     "handler": _list_comments},
    {"name": "basecamp.list_schedule_entries",
     "description": "Events on a project's schedule.",
     "inputSchema": _obj(
         {"project_id": _INT, "schedule_id": _INT},
         ["project_id", "schedule_id"],
     ),
     "handler": _list_schedule_entries},

    # ---- documents + uploads + webhooks ----
    {"name": "basecamp.list_documents",
     "description": "List documents in a project's vault.",
     "inputSchema": _obj(
         {"project_id": _INT, "vault_id": _INT},
         ["project_id", "vault_id"],
     ),
     "handler": _list_documents},
    {"name": "basecamp.get_document",
     "description": "Fetch one document (title + HTML content).",
     "inputSchema": _obj(
         {"project_id": _INT, "document_id": _INT},
         ["project_id", "document_id"],
     ),
     "handler": _get_document},
    {"name": "basecamp.list_uploads",
     "description": "List files uploaded to a project (optionally scoped to one vault).",
     "inputSchema": _obj(
         {"project_id": _INT, "vault_id": _INT},
         ["project_id"],
     ),
     "handler": _list_uploads},
    {"name": "basecamp.list_webhooks",
     "description": "List webhooks registered on a project.",
     "inputSchema": _obj({"project_id": _INT}, ["project_id"]),
     "handler": _list_webhooks},

    # ---- check-ins ----
    {"name": "basecamp.list_daily_check_ins",
     "description": "List the project's daily-check-in questions (uses the project's questionnaire dock).",
     "inputSchema": _obj(
         {"project_id": _INT, "page": _INT},
         ["project_id"],
     ),
     "handler": _list_daily_check_ins},
    {"name": "basecamp.list_question_answers",
     "description": "List answers to one daily-check-in question.",
     "inputSchema": _obj(
         {"project_id": _INT, "question_id": _INT, "page": _INT},
         ["project_id", "question_id"],
     ),
     "handler": _list_question_answers},
]
