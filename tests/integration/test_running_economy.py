"""get_running_economy / get_running_economy_history: Garmin's running economy score.

The payloads follow the live replies' shapes; the values are synthetic.
"""
import datetime
import json
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import training
from garmin_mcp.client_resolver import set_global_client

pytestmark = pytest.mark.integration

LATEST = {"calendarDate": "2026-10-03", "score": 220, "classification": "INTERMEDIATE"}
DAILY = [
    {"calendarDate": "2026-10-01", "score": None, "classification": None},
    {"calendarDate": "2026-10-02", "score": 234, "classification": "INTERMEDIATE"},
    {"calendarDate": "2026-10-03", "score": None, "classification": None},
    {"calendarDate": "2026-10-04", "score": 230, "classification": "INTERMEDIATE"},
]
WEEKLY = [
    {"startDate": "2026-09-26", "endDate": "2026-10-02", "score": None, "classification": None},
    {"startDate": "2026-10-03", "endDate": "2026-10-09", "score": 231, "classification": "INTERMEDIATE"},
]


@pytest.fixture
def client():
    client = Mock()
    client.get_running_economy.return_value = LATEST
    client.get_running_economy_range.side_effect = (
        lambda start, end, aggregation: DAILY if aggregation == "daily" else WEEKLY
    )
    training.configure(client)
    set_global_client(client)
    yield client
    set_global_client(None)


@pytest.fixture
def app(client):
    return training.register_tools(FastMCP("economy"))


async def _call(app, name, args):
    return (await app.call_tool(name, args))[0][0].text


# ─── latest ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_latest_score_and_rating_as_of_the_date(app, client):
    payload = json.loads(await _call(app, "get_running_economy", {"date": "2026-10-05"}))

    assert payload == {
        "as_of": "2026-10-05", "date": "2026-10-03", "score": 220,
        "classification": "INTERMEDIATE",
        "band": {"name": "Intermediate", "low": 215, "high": 224},
        "next_band": {"name": "Trained", "at_or_below": 214, "points_to_go": 6},
    }
    client.get_running_economy.assert_called_once_with("2026-10-05")


@pytest.mark.asyncio
async def test_latest_defaults_to_today(app, client):
    payload = json.loads(await _call(app, "get_running_economy", {}))

    today = datetime.date.today().isoformat()
    assert payload["as_of"] == today
    client.get_running_economy.assert_called_once_with(today)


@pytest.mark.asyncio
async def test_no_score_yet_says_what_garmin_needs(app, client):
    """Garmin sends {} before an account has a score."""
    client.get_running_economy.return_value = {}

    text = await _call(app, "get_running_economy", {"date": "2026-09-01"})

    assert text.startswith("No running economy score as of 2026-09-01.")
    assert "step speed loss" in text


@pytest.mark.asyncio
async def test_a_key_garmin_adds_is_passed_through(app, client):
    client.get_running_economy.return_value = {**LATEST, "trend": "IMPROVING", "unused": None}

    payload = json.loads(await _call(app, "get_running_economy", {"date": "2026-10-05"}))

    assert payload["trend"] == "IMPROVING"
    assert "unused" not in payload


@pytest.mark.asyncio
async def test_latest_rejects_a_malformed_date(app, client):
    text = await _call(app, "get_running_economy", {"date": "10/05/2026"})

    assert text.startswith("Invalid date format")
    client.get_running_economy.assert_not_called()


# ─── history ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_daily_history_lists_only_scored_days_and_counts_the_rest(app, client):
    payload = json.loads(await _call(app, "get_running_economy_history",
                                     {"start_date": "2026-10-01", "end_date": "2026-10-04"}))

    assert payload["aggregation"] == "daily"
    assert payload["periods_requested"] == 4 and payload["periods_with_score"] == 2
    assert payload["scores"] == [
        {"date": "2026-10-02", "score": 234, "classification": "INTERMEDIATE"},
        {"date": "2026-10-04", "score": 230, "classification": "INTERMEDIATE"},
    ]
    assert payload["change"] == -4
    client.get_running_economy_range.assert_called_once_with("2026-10-01", "2026-10-04", "daily")


@pytest.mark.asyncio
async def test_weekly_history_names_each_week(app, client):
    payload = json.loads(await _call(app, "get_running_economy_history",
                                     {"start_date": "2026-09-26", "end_date": "2026-10-09",
                                      "aggregation": "weekly"}))

    assert payload["scores"] == [{"week_start": "2026-10-03", "week_end": "2026-10-09", "score": 231,
                                  "classification": "INTERMEDIATE"}]
    assert "change" not in payload  # one score has nothing to compare against


@pytest.mark.asyncio
async def test_a_range_with_no_scores_says_so(app, client):
    client.get_running_economy_range.side_effect = None
    client.get_running_economy_range.return_value = [DAILY[0], DAILY[2]]

    text = await _call(app, "get_running_economy_history",
                       {"start_date": "2026-10-01", "end_date": "2026-10-03"})

    assert text.startswith("No running economy scores between 2026-10-01 and 2026-10-03.")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args, message",
    [
        ({"start_date": "2025-11-09", "end_date": "2026-11-10"}, "Date range too large"),
        ({"start_date": "2026-10-04", "end_date": "2026-10-01"}, "end_date must be on or after"),
        ({"start_date": "2026-10-01", "end_date": "2026-13-01"}, "Invalid date format"),
    ],
)
async def test_history_refuses_a_bad_range_without_calling_garmin(app, client, args, message):
    text = await _call(app, "get_running_economy_history", args)

    assert text.startswith(message)
    client.get_running_economy_range.assert_not_called()


@pytest.mark.asyncio
async def test_a_full_year_is_accepted(app, client):
    """Garmin accepts a range ending on the same date a year later, and no more."""
    await _call(app, "get_running_economy_history",
                {"start_date": "2025-11-10", "end_date": "2026-11-10"})

    client.get_running_economy_range.assert_called_once()


def test_a_year_after_a_leap_day_is_the_28th():
    assert training._one_year_after(datetime.date(2028, 2, 29)) == datetime.date(2029, 2, 28)
    assert training._one_year_after(datetime.date(2026, 11, 10)) == datetime.date(2027, 11, 10)


# ─── Garmin Connect's bands ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "score, band, next_band",
    [
        (184, {"name": "Elite", "high": 184}, None),
        (185, {"name": "Superior", "low": 185, "high": 189},
         {"name": "Elite", "at_or_below": 184, "points_to_go": 1}),
        (204, {"name": "Well-Trained", "low": 195, "high": 204},
         {"name": "Expert", "at_or_below": 194, "points_to_go": 10}),
        (214, {"name": "Trained", "low": 205, "high": 214},
         {"name": "Well-Trained", "at_or_below": 204, "points_to_go": 10}),
        (215, {"name": "Intermediate", "low": 215, "high": 224},
         {"name": "Trained", "at_or_below": 214, "points_to_go": 1}),
        (225, {"name": "Recreational", "low": 225},
         {"name": "Intermediate", "at_or_below": 224, "points_to_go": 1}),
        (184.5, {"name": "Superior", "low": 185, "high": 189},
         {"name": "Elite", "at_or_below": 184, "points_to_go": 0.5}),
    ],
)
def test_each_score_falls_in_garmins_band(score, band, next_band):
    result = training._running_economy_band(score)

    assert result["band"] == band
    assert result.get("next_band") == next_band


def test_no_score_has_no_band():
    assert training._running_economy_band(None) == {}
