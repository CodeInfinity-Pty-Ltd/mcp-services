"""Tests for src.integrations.clockify — mocked HTTP, no live API calls."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _configure(monkeypatch):
    monkeypatch.setenv("CLOCKIFY_API_KEY", "test-clockify-key")
    import importlib
    from src.integrations import clockify
    importlib.reload(clockify)
    yield clockify


def _resp(status_code: int = 200, json_data=None) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.content = b'{}'
    r.json.return_value = json_data if json_data is not None else {}
    r.text = ""
    return r


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_missing_api_key_raises(_configure, monkeypatch):
    clockify = _configure
    monkeypatch.delenv("CLOCKIFY_API_KEY", raising=False)
    import importlib
    importlib.reload(clockify)
    with pytest.raises(clockify.ClockifyError, match="CLOCKIFY_API_KEY"):
        clockify._list_workspaces({}, {})


def test_api_key_sent_as_header(_configure):
    clockify = _configure
    with patch("src.integrations.clockify.requests.get") as mock_get:
        mock_get.return_value = _resp(200, [{"id": "ws1"}])
        clockify._list_workspaces({}, {})
        assert mock_get.call_args.kwargs["headers"]["X-Api-Key"] == "test-clockify-key"


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------

def test_list_workspaces(_configure):
    clockify = _configure
    with patch("src.integrations.clockify.requests.get") as mock_get:
        mock_get.return_value = _resp(200, [{"id": "ws1", "name": "Main"}])
        out = clockify._list_workspaces({}, {})
        assert out == {"workspaces": [{"id": "ws1", "name": "Main"}]}
        assert mock_get.call_args.args[0].endswith("/v1/workspaces")


def test_list_projects_passes_archived_param(_configure):
    clockify = _configure
    with patch("src.integrations.clockify.requests.get") as mock_get:
        mock_get.return_value = _resp(200, [])
        clockify._list_projects({"workspace_id": "ws1", "archived": True}, {})
        assert mock_get.call_args.kwargs["params"] == {"archived": "true"}
        assert "/workspaces/ws1/projects" in mock_get.call_args.args[0]


def test_list_time_entries_defaults_user_id(_configure):
    clockify = _configure
    with patch("src.integrations.clockify.requests.get") as mock_get:
        # First call → /user (to resolve current user). Second → /time-entries.
        mock_get.side_effect = [
            _resp(200, {"id": "u42"}),
            _resp(200, [{"id": "te1"}]),
        ]
        out = clockify._list_time_entries(
            {"workspace_id": "ws1", "start": "2026-05-01T00:00:00Z"},
            {},
        )
        assert out["user_id"] == "u42"
        assert out["time_entries"] == [{"id": "te1"}]
        second_call = mock_get.call_args_list[1]
        assert "/workspaces/ws1/user/u42/time-entries" in second_call.args[0]
        assert second_call.kwargs["params"] == {"start": "2026-05-01T00:00:00Z"}


def test_summary_report_posts_to_reports_host(_configure):
    clockify = _configure
    with patch("src.integrations.clockify.requests.post") as mock_post:
        mock_post.return_value = _resp(200, {"totals": []})
        out = clockify._summary_report(
            {"workspace_id": "ws1", "start": "a", "end": "b", "group": "user"},
            {},
        )
        assert out == {"totals": []}
        url = mock_post.call_args.args[0]
        assert url.startswith("https://reports.api.clockify.me/v1/")
        body = mock_post.call_args.kwargs["json"]
        assert body["summaryFilter"]["groups"] == ["USER"]
        assert body["dateRangeStart"] == "a"


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------

def test_http_error_bubbles_as_clockify_error(_configure):
    clockify = _configure
    with patch("src.integrations.clockify.requests.get") as mock_get:
        err = _resp(403, None)
        err.text = "Forbidden"
        mock_get.return_value = err
        with pytest.raises(clockify.ClockifyError, match="HTTP 403"):
            clockify._list_workspaces({}, {})


# ---------------------------------------------------------------------------
# Registry health
# ---------------------------------------------------------------------------

def test_tool_registry_exports_expected_names(_configure):
    clockify = _configure
    names = {t["name"] for t in clockify.TOOLS}
    assert names == {
        "clockify.list_workspaces",
        "clockify.get_current_user",
        "clockify.list_projects",
        "clockify.list_clients",
        "clockify.list_time_entries",
        "clockify.summary_report",
    }
