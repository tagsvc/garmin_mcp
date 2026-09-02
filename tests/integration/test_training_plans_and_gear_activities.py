"""Tools added 2026-09-02: training-plan discovery and per-gear activity lists."""
import json

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import gear_management, workouts
from garmin_mcp.client_resolver import set_global_client


def _app(module, client):
    module.configure(client)
    set_global_client(client)
    return module.register_tools(FastMCP("t"))


async def _text(app, name, args=None):
    return (await app.call_tool(name, args or {}))[0][0].text


# ─── training plan discovery ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_training_plans_curates_the_list(mock_garmin_client):
    mock_garmin_client.get_training_plans.return_value = {
        "trainingPlanList": [
            {
                "trainingPlanId": 11,
                "planName": "Base Build",
                "planType": "running",
                "isAdaptive": False,
                "startDate": "2026-09-01",
                "endDate": "2026-10-27",
                "active": True,
            },
            {"trainingPlanId": 12, "name": "Coach 10K", "adaptive": True},
        ]
    }
    payload = json.loads(await _text(_app(workouts, mock_garmin_client), "get_training_plans"))

    assert payload["count"] == 2
    first = payload["plans"][0]
    assert first["plan_id"] == 11 and first["name"] == "Base Build"
    assert first["adaptive"] is False
    # The adaptive flag drives which detail tool the caller should use next,
    # and Garmin spells it two different ways across plan families.
    assert payload["plans"][1]["adaptive"] is True
    assert payload["plans"][1]["name"] == "Coach 10K"


@pytest.mark.asyncio
async def test_get_training_plans_passes_through_unexpected_shapes(mock_garmin_client):
    """Never swallow a payload we don't recognise -- return it for inspection."""
    mock_garmin_client.get_training_plans.return_value = {"unexpected": "shape"}
    assert "unexpected" in await _text(_app(workouts, mock_garmin_client), "get_training_plans")


@pytest.mark.asyncio
async def test_plan_detail_tools_call_their_own_endpoints(mock_garmin_client):
    """Adaptive plans are served by a different endpoint; don't cross the wires."""
    app = _app(workouts, mock_garmin_client)
    mock_garmin_client.get_training_plan_by_id.return_value = {"id": 11}
    mock_garmin_client.get_adaptive_training_plan_by_id.return_value = {"id": 12}

    assert "11" in await _text(app, "get_training_plan_details", {"plan_id": 11})
    mock_garmin_client.get_training_plan_by_id.assert_called_once_with(11)
    mock_garmin_client.get_adaptive_training_plan_by_id.assert_not_called()

    assert "12" in await _text(app, "get_adaptive_training_plan_details", {"plan_id": 12})
    mock_garmin_client.get_adaptive_training_plan_by_id.assert_called_once_with(12)


@pytest.mark.asyncio
async def test_plan_detail_reports_errors_instead_of_raising(mock_garmin_client):
    mock_garmin_client.get_training_plan_by_id.side_effect = RuntimeError("nope")
    out = await _text(_app(workouts, mock_garmin_client), "get_training_plan_details", {"plan_id": 1})
    assert "Error getting training plan 1" in out and "nope" in out


# ─── per-gear activity list ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_gear_activities_totals_distance_for_reconciliation(mock_garmin_client):
    """The total is the point: it reconciles against what get_gear reports.

    A mis-recorded activity (GPS dropout logging 0.49 mi for a 1.78 mi walk)
    is only findable when the individual activities behind the total are listed.
    """
    mock_garmin_client.get_gear_activities.return_value = [
        {
            "activityId": 1,
            "activityName": "Westwood Timed Activity",
            "startTimeLocal": "2026-08-31 11:18:00",
            "distance": 2864.6,
            "duration": 2066.0,
            "activityType": {"typeKey": "walking"},
        },
        {
            "activityId": 2,
            "activityName": "River Vale Timed Activity",
            "startTimeLocal": "2026-08-31 12:23:00",
            "distance": 788.6,
            "duration": 2074.0,
            "activityType": {"typeKey": "walking"},
        },
    ]
    payload = json.loads(
        await _text(
            _app(gear_management, mock_garmin_client),
            "get_gear_activities",
            {"gear_uuid": "abc-123"},
        )
    )

    assert payload["activity_count"] == 2
    assert payload["total_distance_m"] == pytest.approx(3653.2)
    assert payload["activities"][0]["name"] == "Westwood Timed Activity"
    assert payload["activities"][1]["activity_type"] == "walking"
    mock_garmin_client.get_gear_activities.assert_called_once_with("abc-123", limit=1000)


@pytest.mark.asyncio
async def test_get_gear_activities_tolerates_missing_distance(mock_garmin_client):
    """A null distance must not blow up the sum."""
    mock_garmin_client.get_gear_activities.return_value = [
        {"activityId": 1, "distance": None},
        {"activityId": 2, "distance": 100.0},
    ]
    payload = json.loads(
        await _text(
            _app(gear_management, mock_garmin_client),
            "get_gear_activities",
            {"gear_uuid": "g"},
        )
    )
    assert payload["total_distance_m"] == 100.0


@pytest.mark.asyncio
async def test_get_gear_activities_honours_limit(mock_garmin_client):
    mock_garmin_client.get_gear_activities.return_value = []
    await _text(
        _app(gear_management, mock_garmin_client),
        "get_gear_activities",
        {"gear_uuid": "g", "limit": 25},
    )
    mock_garmin_client.get_gear_activities.assert_called_once_with("g", limit=25)
