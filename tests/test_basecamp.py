"""Tests for src.integrations.basecamp.

Mocks the ``requests`` layer so the test suite stays offline and doesn't
need real Basecamp credentials. We exercise the auth refresh path, the
URL construction, and a representative subset of the tool handlers.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Force a known config before importing the module under test. Without
# this, the module reads empty env vars at import time and every call
# bails out with "Basecamp integration is not configured".
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    monkeypatch.setenv("BASECAMP_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("BASECAMP_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("BASECAMP_REFRESH_TOKEN", "test-refresh")
    monkeypatch.setenv("BASECAMP_ACCOUNT_ID", "9999999")

    # Re-import so the module-level constants pick up the env we just set.
    import importlib
    from src.integrations import basecamp
    importlib.reload(basecamp)

    # Reset the token cache so each test starts from "no token yet" and
    # forces the refresh-then-call code path.
    basecamp._access_token = None
    basecamp._expires_at = 0.0
    yield basecamp


def _mock_response(status_code: int = 200, json_data=None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = b'{}'
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = ""
    return resp


def _token_response() -> MagicMock:
    return _mock_response(200, {"access_token": "new-access-token", "expires_in": 1209600})


# ---------------------------------------------------------------------------
# Auth — refresh flow
# ---------------------------------------------------------------------------

def test_first_call_refreshes_access_token(_configure):
    basecamp = _configure
    with patch("src.integrations.basecamp.requests.post") as mock_post, \
         patch("src.integrations.basecamp.requests.get") as mock_get:
        mock_post.return_value = _token_response()
        mock_get.return_value = _mock_response(200, [{"id": 1, "name": "Demo"}])

        out = basecamp._list_projects({}, {})

        # Refresh hit first, then the API call.
        assert mock_post.call_args.args[0].endswith("/authorization/token")
        api_call = mock_get.call_args
        assert "9999999/projects.json" in api_call.args[0]
        assert api_call.kwargs["headers"]["Authorization"] == "Bearer new-access-token"
        assert out == {"projects": [{"id": 1, "name": "Demo"}]}


def test_missing_env_raises_configured_error(_configure, monkeypatch):
    basecamp = _configure
    monkeypatch.delenv("BASECAMP_REFRESH_TOKEN", raising=False)
    import importlib
    importlib.reload(basecamp)
    with pytest.raises(basecamp.BasecampError, match="BASECAMP_REFRESH_TOKEN"):
        basecamp._list_projects({}, {})


def test_401_triggers_one_refresh_and_retry(_configure):
    basecamp = _configure
    with patch("src.integrations.basecamp.requests.post") as mock_post, \
         patch("src.integrations.basecamp.requests.get") as mock_get:
        mock_post.return_value = _token_response()
        # First GET → 401, second GET → 200 after the refresh.
        mock_get.side_effect = [
            _mock_response(401, None),
            _mock_response(200, [{"id": 5}]),
        ]
        out = basecamp._list_projects({}, {})
        assert mock_post.call_count == 2   # initial refresh + retry refresh
        assert out == {"projects": [{"id": 5}]}


# ---------------------------------------------------------------------------
# Tool surface — URL construction + arg parsing
# ---------------------------------------------------------------------------

def test_get_project_uses_path(_configure):
    basecamp = _configure
    with patch("src.integrations.basecamp.requests.post") as mock_post, \
         patch("src.integrations.basecamp.requests.get") as mock_get:
        mock_post.return_value = _token_response()
        mock_get.return_value = _mock_response(200, {"id": 42, "dock": []})

        out = basecamp._get_project({"project_id": 42}, {})
        assert "/9999999/projects/42.json" in mock_get.call_args.args[0]
        assert out["id"] == 42


def test_list_todosets_filters_dock_entries(_configure):
    basecamp = _configure
    dock = [
        {"name": "todoset", "id": 100},
        {"name": "kanban_board", "id": 200},
        {"name": "message_board", "id": 300},
    ]
    with patch("src.integrations.basecamp.requests.post") as mock_post, \
         patch("src.integrations.basecamp.requests.get") as mock_get:
        mock_post.return_value = _token_response()
        mock_get.return_value = _mock_response(200, {"dock": dock})

        out = basecamp._list_todosets({"project_id": 1}, {})
        assert out == {"todosets": [{"name": "todoset", "id": 100}]}


def test_list_card_tables_only_returns_kanban_docks(_configure):
    basecamp = _configure
    dock = [
        {"name": "kanban_board", "id": 200},
        {"name": "schedule", "id": 400},
    ]
    with patch("src.integrations.basecamp.requests.post") as mock_post, \
         patch("src.integrations.basecamp.requests.get") as mock_get:
        mock_post.return_value = _token_response()
        mock_get.return_value = _mock_response(200, {"dock": dock})

        out = basecamp._list_card_tables({"project_id": 1}, {})
        assert out == {"card_tables": [{"name": "kanban_board", "id": 200}]}


# ---------------------------------------------------------------------------
# Registry health — the dispatcher picks these up via pkgutil
# ---------------------------------------------------------------------------

def test_tool_registry_exports_expected_names(_configure):
    basecamp = _configure
    names = {t["name"] for t in basecamp.TOOLS}
    expected = {
        "basecamp.list_projects",
        "basecamp.get_project",
        "basecamp.list_people",
        "basecamp.list_todosets",
        "basecamp.list_todos",
        "basecamp.list_card_tables",
        "basecamp.list_cards",
        "basecamp.get_card",
        "basecamp.list_messages",
        "basecamp.list_comments_on",
        "basecamp.list_schedule_entries",
    }
    assert expected == names


def test_dispatcher_picks_up_basecamp_after_reload():
    """The MCP dispatcher's auto-discovery should see the basecamp tools
    once the integrations package is re-imported."""
    import importlib
    from src.app import mcp_server
    importlib.reload(mcp_server)
    assert "basecamp.list_projects" in mcp_server.TOOLS_BY_NAME
