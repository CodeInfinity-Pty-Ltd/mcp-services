"""Tests for src.integrations.basecamp.

Mocks ``requests`` so the suite stays offline and doesn't need real
Basecamp credentials.

Coverage:
- auth: refresh on first call, missing-env → clear error, 401 retry
- read: paginate() helper, URL construction, dock filtering
- write: POST/PUT/PATCH body construction
- registry: every tool advertised in TOOLS has a handler
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    monkeypatch.setenv("BASECAMP_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("BASECAMP_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("BASECAMP_REFRESH_TOKEN", "test-refresh")
    monkeypatch.setenv("BASECAMP_ACCOUNT_ID", "9999999")

    import importlib
    from src.integrations import basecamp
    importlib.reload(basecamp)

    basecamp._access_token = None
    basecamp._expires_at = 0.0
    yield basecamp


def _resp(status_code: int = 200, json_data=None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.content = b'{}'
    r.json.return_value = json_data if json_data is not None else {}
    r.text = ""
    return r


def _token_response() -> MagicMock:
    return _resp(200, {"access_token": "fresh-token", "expires_in": 1209600})


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def test_first_call_refreshes_access_token(_configure):
    basecamp = _configure
    with patch("src.integrations.basecamp.requests.post") as mock_post, \
         patch("src.integrations.basecamp.requests.request") as mock_req:
        mock_post.return_value = _token_response()
        mock_req.return_value = _resp(200, [{"id": 1}])

        basecamp._list_projects({}, {})

        assert mock_post.call_args.args[0].endswith("/authorization/token")
        assert mock_req.call_args.kwargs["headers"]["Authorization"] == "Bearer fresh-token"


def test_missing_env_raises(_configure, monkeypatch):
    basecamp = _configure
    monkeypatch.delenv("BASECAMP_REFRESH_TOKEN", raising=False)
    import importlib
    importlib.reload(basecamp)
    with pytest.raises(basecamp.BasecampError, match="BASECAMP_REFRESH_TOKEN"):
        basecamp._list_projects({}, {})


def test_401_triggers_refresh_and_retry(_configure):
    basecamp = _configure
    with patch("src.integrations.basecamp.requests.post") as mock_post, \
         patch("src.integrations.basecamp.requests.request") as mock_req:
        mock_post.return_value = _token_response()
        mock_req.side_effect = [_resp(401, None), _resp(200, [{"id": 99}])]
        out = basecamp._list_projects({}, {})
        assert out == {"projects": [{"id": 99}]}
        assert mock_post.call_count == 2   # initial refresh + retry refresh


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------

def test_paginate_walks_until_short_page(_configure):
    basecamp = _configure
    full_page = [{"id": i} for i in range(15)]
    half_page = [{"id": 100}, {"id": 101}]
    with patch("src.integrations.basecamp.requests.post") as mock_post, \
         patch("src.integrations.basecamp.requests.request") as mock_req:
        mock_post.return_value = _token_response()
        mock_req.side_effect = [_resp(200, full_page), _resp(200, half_page)]

        out = basecamp._paginate("things.json")
        assert len(out) == 17
        assert mock_req.call_args_list[0].kwargs["params"]["page"] == 1
        assert mock_req.call_args_list[1].kwargs["params"]["page"] == 2


def test_paginate_stops_on_empty_first_page(_configure):
    basecamp = _configure
    with patch("src.integrations.basecamp.requests.post") as mock_post, \
         patch("src.integrations.basecamp.requests.request") as mock_req:
        mock_post.return_value = _token_response()
        mock_req.return_value = _resp(200, [])
        out = basecamp._paginate("things.json")
        assert out == []
        assert mock_req.call_count == 1


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------

def test_list_todosets_filters_dock(_configure):
    basecamp = _configure
    dock = [
        {"name": "todoset", "id": 100},
        {"name": "kanban_board", "id": 200},
        {"name": "message_board", "id": 300},
    ]
    with patch("src.integrations.basecamp.requests.post") as mock_post, \
         patch("src.integrations.basecamp.requests.request") as mock_req:
        mock_post.return_value = _token_response()
        mock_req.return_value = _resp(200, {"dock": dock})
        out = basecamp._list_todosets({"project_id": 1}, {})
        assert out == {"todosets": [{"name": "todoset", "id": 100}]}


def test_list_card_tables_includes_card_table_alias(_configure):
    """The TS reference accepts both kanban_board AND card_table dock names."""
    basecamp = _configure
    dock = [
        {"name": "kanban_board", "id": 200},
        {"name": "card_table", "id": 201},
        {"name": "schedule", "id": 400},
    ]
    with patch("src.integrations.basecamp.requests.post") as mock_post, \
         patch("src.integrations.basecamp.requests.request") as mock_req:
        mock_post.return_value = _token_response()
        mock_req.return_value = _resp(200, {"dock": dock})
        out = basecamp._list_card_tables({"project_id": 1}, {})
        assert {d["id"] for d in out["card_tables"]} == {200, 201}


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------

def test_create_card_passes_only_set_fields(_configure):
    basecamp = _configure
    with patch("src.integrations.basecamp.requests.post") as mock_post, \
         patch("src.integrations.basecamp.requests.request") as mock_req:
        mock_post.return_value = _token_response()
        mock_req.return_value = _resp(200, {"id": 555, "title": "x"})

        basecamp._create_card({
            "project_id": 1, "column_id": 2,
            "title": "Onboarding doc",
            "content": "Body…",
        }, {})

        body = mock_req.call_args.kwargs["json"]
        # Only the keys we passed land in the body — never ``due_on=None`` etc.
        assert body == {"title": "Onboarding doc", "content": "Body…"}
        assert "/card_tables/lists/2/cards.json" in mock_req.call_args.args[1]


def test_move_card_posts_to_moves_endpoint(_configure):
    basecamp = _configure
    with patch("src.integrations.basecamp.requests.post") as mock_post, \
         patch("src.integrations.basecamp.requests.request") as mock_req:
        mock_post.return_value = _token_response()
        mock_req.return_value = _resp(204, None)

        out = basecamp._move_card({"project_id": 1, "card_id": 88, "column_id": 99}, {})
        assert out == {"moved": True, "column_id": 99}
        url = mock_req.call_args.args[1]
        assert "/cards/88/moves.json" in url
        assert mock_req.call_args.kwargs["json"] == {"column_id": 99}


def test_update_column_color_uses_patch(_configure):
    basecamp = _configure
    with patch("src.integrations.basecamp.requests.post") as mock_post, \
         patch("src.integrations.basecamp.requests.request") as mock_req:
        mock_post.return_value = _token_response()
        mock_req.return_value = _resp(200, {"id": 7, "color": "#FF0000"})

        basecamp._update_column_color({"project_id": 1, "column_id": 7, "color": "#FF0000"}, {})

        assert mock_req.call_args.args[0] == "PATCH"
        assert mock_req.call_args.kwargs["json"] == {"color": "#FF0000"}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_tool_registry_has_no_dangling_handlers(_configure):
    basecamp = _configure
    for tool in basecamp.TOOLS:
        assert callable(tool["handler"]), f"{tool['name']} handler is not callable"
        assert tool["name"].startswith("basecamp."), tool["name"]


def test_tool_registry_covers_expected_capabilities(_configure):
    basecamp = _configure
    names = {t["name"] for t in basecamp.TOOLS}
    must_have = {
        # project/people
        "basecamp.list_projects", "basecamp.get_project", "basecamp.list_people",
        # todos
        "basecamp.list_todosets", "basecamp.list_todolists",
        "basecamp.list_todos", "basecamp.get_todo",
        # card tables (kanban)
        "basecamp.list_card_tables", "basecamp.get_card_table",
        "basecamp.list_columns", "basecamp.get_column",
        "basecamp.create_column", "basecamp.update_column",
        "basecamp.move_column", "basecamp.update_column_color",
        # cards
        "basecamp.list_cards", "basecamp.get_card",
        "basecamp.create_card", "basecamp.update_card",
        "basecamp.move_card", "basecamp.complete_card",
        # card steps
        "basecamp.get_card_steps", "basecamp.create_card_step",
        "basecamp.complete_card_step",
        # comms
        "basecamp.list_messages", "basecamp.list_campfire_lines",
        "basecamp.list_comments", "basecamp.list_schedule_entries",
        # docs + uploads + webhooks
        "basecamp.list_documents", "basecamp.get_document",
        "basecamp.list_uploads", "basecamp.list_webhooks",
        # check-ins
        "basecamp.list_daily_check_ins", "basecamp.list_question_answers",
    }
    missing = must_have - names
    assert not missing, f"missing tools: {sorted(missing)}"


def test_dispatcher_picks_up_basecamp_after_reload(_configure):
    import importlib
    from src.app import mcp_server
    importlib.reload(mcp_server)
    assert "basecamp.list_projects" in mcp_server.TOOLS_BY_NAME
    assert "basecamp.create_card" in mcp_server.TOOLS_BY_NAME
    assert "basecamp.list_campfire_lines" in mcp_server.TOOLS_BY_NAME
