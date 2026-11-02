"""get_fastest_splits: Garmin's best efforts per distance in a date range.

The payload follows the live reply's shape; the values and ids are synthetic.
"""
import json
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import activity_management
from garmin_mcp.client_resolver import set_global_client

SPLITS = [
    {"distance": 1609, "activityId": 2, "activityDate": "2026-10-20", "time": 545.0},
    {"distance": 1000, "activityId": 1, "activityDate": "2026-10-10", "time": 330.2},
    {"distance": 1000, "activityId": 2, "activityDate": "2026-10-20", "time": 335.0},
    {"distance": 1609, "activityId": 1, "activityDate": "2026-10-10", "time": 540.0},
    {"distance": 5000, "activityId": 3, "activityDate": "2026-10-25", "time": 1800.0},
]


@pytest.fixture
def client():
    client = Mock()
    client.get_unit_system.return_value = "statute_us"
    client.get_fastest_splits.return_value = SPLITS
    return client


@pytest.fixture
def app(client):
    activity_management.configure(client)
    set_global_client(client)
    yield activity_management.register_tools(FastMCP("splits"))
    set_global_client(None)


async def _call(app, args):
    return (await app.call_tool("get_fastest_splits", args))[0][0].text


@pytest.mark.asyncio
async def test_bests_are_grouped_by_distance_and_ranked(app, client):
    payload = json.loads(await _call(app, {"start_date": "2026-10-01", "end_date": "2026-10-31"}))

    client.get_fastest_splits.assert_called_once_with("2026-10-01", "2026-10-31", "running", 3)
    assert [b["distance"] for b in payload["bests"]] == ["1K", "mile", "5K"]
    one_k = payload["bests"][0]
    assert [e["activity_id"] for e in one_k["efforts"]] == [1, 2]
    first = one_k["efforts"][0]
    assert (first["rank"], first["duration"], first["date"]) == (1, "5:30", "2026-10-10")
    assert first["duration_seconds"] == 330.2


@pytest.mark.asyncio
async def test_each_best_has_its_pace_in_the_accounts_units(app):
    payload = json.loads(await _call(app, {"start_date": "2026-10-01", "end_date": "2026-10-31"}))

    mile = payload["bests"][1]
    assert mile["distance_miles"] == 1.0
    assert mile["efforts"][0]["avg_pace_per_mile"] == "9:00"


@pytest.mark.asyncio
async def test_no_bests_is_said_plainly(app, client):
    client.get_fastest_splits.return_value = []

    text = await _call(app, {"start_date": "2026-10-01", "end_date": "2026-10-31"})

    assert text == "No running bests between 2026-10-01 and 2026-10-31."
