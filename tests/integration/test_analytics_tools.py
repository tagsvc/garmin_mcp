"""
Integration tests for the analytics module MCP tools.

Tests registration and basic behaviour of the historical analytics tools using
a mocked Garmin client that produces synthetic daily history.
"""
import datetime as dt
import json

import pytest
from unittest.mock import Mock
from mcp.server.fastmcp import FastMCP

from garmin_mcp import analytics
from garmin_mcp.client_resolver import set_global_client


ANALYTICS_TOOLS = {
    "get_health_baselines",
    "get_wellness_anomalies",
    "get_lagged_health_correlations",
    "get_weekly_health_review",
    "list_health_report_metrics",
    "run_custom_health_report",
    "save_custom_health_report",
    "list_saved_health_reports",
}


def _stats_for(date_str):
    """Synthetic daily stats that vary by date so baselines have signal."""
    ordinal = dt.date.fromisoformat(date_str).toordinal()
    return {
        "totalSteps": 4000 + (ordinal % 7) * 600,
        "activeKilocalories": 300 + (ordinal % 5) * 40,
        "totalDistanceMeters": 3000 + (ordinal % 7) * 400,
        "restingHeartRate": 48 + (ordinal % 5),
        "averageStressLevel": 25 + (ordinal % 10),
        "maxStressLevel": 70,
        "bodyBatteryHighestValue": 80 + (ordinal % 6),
        "bodyBatteryLowestValue": 15,
    }


@pytest.fixture
def analytics_client():
    client = Mock()
    client.get_stats = Mock(side_effect=_stats_for)
    client.get_sleep_data = Mock(
        return_value={
            "dailySleepDTO": {
                "sleepTimeSeconds": 27000,
                "deepSleepSeconds": 5400,
                "remSleepSeconds": 6300,
                "sleepScores": {"overall": {"value": 80}},
                "avgSleepStress": 18,
            },
            "avgOvernightHrv": 45,
        }
    )
    client.get_training_readiness = Mock(return_value=[{"score": 70}])
    client.get_activities_by_date = Mock(return_value=[])
    return client


@pytest.fixture
def app_with_analytics(analytics_client):
    analytics.configure(analytics_client)
    set_global_client(analytics_client)
    app = FastMCP("Test Analytics")
    app = analytics.register_tools(app)
    return app


@pytest.fixture
def reports_path(tmp_path, monkeypatch):
    path = tmp_path / "reports.json"
    monkeypatch.setenv("GARMIN_REPORTS_PATH", str(path))
    return path


def _text(result):
    return result[0][0].text


@pytest.mark.asyncio
async def test_all_analytics_tools_registered(app_with_analytics):
    tools = {t.name for t in await app_with_analytics.list_tools()}
    assert ANALYTICS_TOOLS.issubset(tools)


@pytest.mark.asyncio
async def test_list_health_report_metrics(app_with_analytics):
    result = await app_with_analytics.call_tool("list_health_report_metrics", {})
    payload = json.loads(_text(result))
    assert "steps" in payload["metrics"]
    assert "avg" in payload["aggregations"]
    assert "week" in payload["groups"]


@pytest.mark.asyncio
async def test_get_health_baselines(app_with_analytics):
    result = await app_with_analytics.call_tool(
        "get_health_baselines", {"days": 40}
    )
    payload = json.loads(_text(result))
    assert payload["window"]["days"] > 0
    assert "steps" in payload["metrics"]
    assert payload["metrics"]["steps"]["latest"] is not None


@pytest.mark.asyncio
async def test_get_health_baselines_no_data(app_with_analytics, analytics_client):
    analytics_client.get_stats = Mock(return_value={})
    analytics_client.get_sleep_data = Mock(return_value={})
    analytics_client.get_training_readiness = Mock(return_value=[])
    result = await app_with_analytics.call_tool("get_health_baselines", {"days": 10})
    assert "No health data" in _text(result)


@pytest.mark.asyncio
async def test_run_custom_health_report(app_with_analytics):
    result = await app_with_analytics.call_tool(
        "run_custom_health_report",
        {"days": 30, "metrics": "steps,sleep_score", "group_by": "week"},
    )
    payload = json.loads(_text(result))
    assert payload["columns"][:2] == ["group", "days"]
    assert "steps" in payload["definition"]["metrics"]
    assert len(payload["rows"]) > 0


@pytest.mark.asyncio
async def test_run_custom_health_report_rejects_unknown_metric(app_with_analytics):
    result = await app_with_analytics.call_tool(
        "run_custom_health_report", {"days": 10, "metrics": "not_a_metric"}
    )
    assert "Unsupported metrics" in _text(result)


@pytest.mark.asyncio
async def test_save_and_list_saved_reports(app_with_analytics, reports_path):
    save = await app_with_analytics.call_tool(
        "save_custom_health_report",
        {"name": "My Weekly", "metrics": "steps,resting_hr", "group_by": "week"},
    )
    assert json.loads(_text(save))["saved"] is True

    listing = json.loads(
        _text(await app_with_analytics.call_tool("list_saved_health_reports", {}))
    )
    assert listing["count"] == 1
    assert listing["reports"][0]["name"] == "My Weekly"


@pytest.mark.asyncio
async def test_weekly_review_and_anomalies_and_correlations(app_with_analytics):
    for tool in (
        "get_weekly_health_review",
        "get_wellness_anomalies",
        "get_lagged_health_correlations",
    ):
        result = await app_with_analytics.call_tool(tool, {})
        # Each returns valid JSON (not an "Error ..." string).
        json.loads(_text(result))
