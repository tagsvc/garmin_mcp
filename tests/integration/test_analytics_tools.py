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


# --- rest days, the judged day and gaps ----------------------------------------

def _row(date, **values):
    row = {"date": date, **values}
    return row


def test_a_rest_day_counts_as_zero_activity():
    """Averages of activity duration run over every day, not active days only."""
    client = Mock()
    client.get_stats = Mock(return_value={"totalSteps": 5000})
    client.get_sleep_data = Mock(return_value={})
    client.get_training_readiness = Mock(return_value=[])
    client.get_activities_by_date = Mock(
        return_value=[{"startTimeLocal": "2026-11-03 07:00:00", "duration": 7200}]
    )

    rows = analytics._daily_rows(client, end_date="2026-11-07", days=7)

    durations = [row["activity_duration_hours"] for row in rows]
    assert len(rows) == 7
    assert durations.count(0) == 6 and 2.0 in durations
    assert analytics._average(analytics._values(rows, "activity_duration_hours")) == pytest.approx(2 / 7)


def test_a_failed_activity_fetch_leaves_activity_unknown():
    client = Mock()
    client.get_stats = Mock(return_value={"totalSteps": 5000})
    client.get_sleep_data = Mock(return_value={})
    client.get_training_readiness = Mock(return_value=[])
    client.get_activities_by_date = Mock(side_effect=RuntimeError("429"))

    rows = analytics._daily_rows(client, end_date="2026-11-07", days=3)

    assert [row["activity_duration_hours"] for row in rows] == [None, None, None]


def test_the_baseline_leaves_out_the_day_it_judges():
    rows = [
        _row((dt.date(2026, 10, 1) + dt.timedelta(days=n)).isoformat(), resting_hr=50 + n % 2)
        for n in range(27)
    ]
    rows.append(_row("2026-10-28", resting_hr=60))

    metric = analytics._baseline_payload(rows, baseline_window=28)["metrics"]["resting_hr"]

    assert metric["baseline_avg"] == pytest.approx(50.48, abs=0.01)
    assert metric["sample_days"] == 27
    assert metric["z_score"] > 15


def test_lags_pair_calendar_days_across_a_gap():
    """With Nov 5 missing, Nov 4 pairs with nothing a day later, not with Nov 6."""
    rows = [
        _row(f"2026-11-{day:02d}", steps=day * 100, resting_hr=day)
        for day in range(1, 21)
        if day != 5
    ]

    correlations = analytics._lagged_correlations(rows, max_lag_days=1)

    pair = next(c for c in correlations if c["left_metric"] == "steps" and c["right_metric"] == "resting_hr")
    # 19 days of data, 18 consecutive-day pairs, less the two that span Nov 5.
    assert pair["pairs"] == 17


def test_the_weekly_review_takes_calendar_weeks_across_a_gap():
    rows = [
        _row(f"2026-11-{day:02d}", steps=1000 if day > 7 else 9000)
        for day in range(1, 15)
        if day != 10
    ]

    review = analytics._weekly_review(rows)

    steps = review["comparisons"]["steps"]
    assert steps["current_7d_avg"] == 1000
    assert steps["previous_7d_avg"] == 9000


# --- today's unfinished day and Garmin's week ------------------------------------

def _days(first: str, count: int, **values):
    start = dt.date.fromisoformat(first)
    return [
        _row((start + dt.timedelta(days=n)).isoformat(), **values) for n in range(count)
    ]


def test_todays_steps_are_judged_on_yesterday_with_today_so_far():
    rows = _days("2026-10-20", 30, steps=9000, sleep_score=80)
    rows[-1].update(steps=900, sleep_score=40, partial_day=True)

    payload = analytics._baseline_payload(rows, baseline_window=28)

    steps = payload["metrics"]["steps"]
    assert steps["latest"] == 9000 and steps["latest_date"] == rows[-2]["date"]
    assert steps["today_so_far"] == 900
    # Overnight metrics are final once the athlete wakes: today counts.
    sleep = payload["metrics"]["sleep_score"]
    assert sleep["latest"] == 40 and "latest_date" not in sleep
    assert payload["today_partial"]["date"] == rows[-1]["date"]


def test_a_morning_step_count_is_not_an_anomaly():
    rows = _days("2026-10-20", 30, steps=9000, sleep_score=80)
    for n, row in enumerate(rows):
        row["steps"] += (n % 3) * 500
        row["sleep_score"] += n % 3
    rows[-1].update(steps=900, sleep_score=40, partial_day=True)

    flagged = {(a["date"], a["metric"]) for a in analytics._anomalies(rows, 21, 1.5)}

    assert (rows[-1]["date"], "steps") not in flagged
    assert (rows[-1]["date"], "sleep_score") in flagged


def test_todays_steps_stay_out_of_the_weekly_averages():
    rows = _days("2026-10-20", 30, steps=9000)
    rows[-1].update(steps=900, partial_day=True)

    review = analytics._weekly_review(rows)

    assert review["comparisons"]["steps"]["current_7d_avg"] == 9000
    assert review["today_partial"]["still_accumulating"]


@pytest.mark.parametrize(
    "first_day, this_week, last_week",
    [
        ("monday", ("2026-11-16", "2026-11-19"), ("2026-11-09", "2026-11-15")),
        ("sunday", ("2026-11-15", "2026-11-19"), ("2026-11-08", "2026-11-14")),
        ("saturday", ("2026-11-14", "2026-11-19"), ("2026-11-07", "2026-11-13")),
    ],
)
def test_the_garmin_week_follows_the_first_day_of_the_week(first_day, this_week, last_week):
    """Thursday 2026-11-19: this week so far against the same days a week
    earlier, and the last full week."""
    rows = _days("2026-10-16", 35, steps=9000)

    weeks = analytics._weekly_review(rows, first_day=first_day)["garmin_week"]

    so_far = weeks["this_week_so_far"]
    assert (so_far["current"]["start"], so_far["current"]["end"]) == this_week
    earlier = [
        (dt.date.fromisoformat(d) - dt.timedelta(days=7)).isoformat() for d in this_week
    ]
    assert (so_far["compared_with"]["start"], so_far["compared_with"]["end"]) == tuple(earlier)
    full = weeks["last_full_week"]
    assert (full["current"]["start"], full["current"]["end"]) == last_week
    assert full["current"]["days"] == 7
    assert weeks["first_day_of_week"] == first_day


def test_custom_report_weeks_start_on_the_profiles_first_day():
    rows = _days("2026-11-14", 6, steps=1000)  # Saturday to Thursday
    definition = {"metrics": ["steps"], "group_by": "week", "aggregation": "sum", "days": 6}

    sunday = analytics._custom_report(rows, definition, False, first_day="sunday")
    monday = analytics._custom_report(rows, definition, False, first_day="monday")

    assert [(r["group"], r["days"]) for r in sunday["rows"]] == [("2026-11-08", 1), ("2026-11-15", 5)]
    assert [(r["group"], r["days"]) for r in monday["rows"]] == [("2026-11-09", 2), ("2026-11-16", 4)]
    assert sunday["definition"]["first_day_of_week"] == "sunday"


def test_daily_rows_mark_today_partial(monkeypatch):
    monkeypatch.setattr(analytics, "local_today", lambda _client: dt.date(2026, 11, 19))
    client = Mock()
    client.get_stats = Mock(return_value={"totalSteps": 900})
    client.get_sleep_data = Mock(return_value={})
    client.get_training_readiness = Mock(return_value=[])
    client.get_activities_by_date = Mock(return_value=[])

    rows = analytics._daily_rows(client, end_date=None, days=3)

    assert [row.get("partial_day", False) for row in rows] == [False, False, True]
