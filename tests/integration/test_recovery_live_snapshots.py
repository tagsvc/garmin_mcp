"""Recovery hours from real Training Readiness snapshots.

The two snapshots below are Garmin's GET .../trainingreadiness/2026-10-27
response, captured from Garmin Connect's web client, verbatim except that
userProfilePK and deviceId are replaced (this repository is public).

They are the case that broke the upstream rule. Garmin Connect showed
"Recovery Time 72h, High Need" for the 15:01 snapshot while
get_training_readiness and get_recovery_time_remaining reported 0.0 hours:
the snapshot's recoveryTimeChangePhrase is REACHED_ZERO, and upstream turned
that phrase into 0 regardless of recoveryTime (4320 minutes = 72.0h). The
athlete confirmed the 08:03 wake-up value, ~0, was right.
"""
import json

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import health_wellness

_LIVE_2026_10_27 = [
    {"userProfilePK": 1, "calendarDate": "2026-10-27", "timestamp": "2026-10-27T19:01:16.0",
     "timestampLocal": "2026-10-27T15:01:16.0", "deviceId": 1, "level": "POOR",
     "feedbackLong": "POOR_NAP", "feedbackShort": "IMPROVED_BY_NAPPING", "score": 20,
     "sleepScore": 77, "sleepScoreFactorPercent": 65, "sleepScoreFactorFeedback": "MODERATE",
     "recoveryTime": 4320, "recoveryTimeFactorPercent": 10, "recoveryTimeFactorFeedback": "POOR",
     "acwrFactorPercent": 83, "acwrFactorFeedback": "GOOD", "acuteLoad": 307,
     "stressHistoryFactorPercent": 91, "stressHistoryFactorFeedback": "GOOD",
     "hrvFactorPercent": 87, "hrvFactorFeedback": "GOOD", "hrvWeeklyAverage": 54,
     "sleepHistoryFactorPercent": 76, "sleepHistoryFactorFeedback": "GOOD", "validSleep": True,
     "inputContext": "AFTER_POST_EXERCISE_RESET", "primaryActivityTracker": True,
     "recoveryTimeChangePhrase": "REACHED_ZERO"},
    {"userProfilePK": 1, "calendarDate": "2026-10-27", "timestamp": "2026-10-27T12:03:36.0",
     "timestampLocal": "2026-10-27T08:03:36.0", "deviceId": 1, "level": "HIGH",
     "feedbackLong": "HIGH_RT_HIGHEST_SS_AVAILABLE", "feedbackShort": "WELL_RECOVERED",
     "score": 79, "sleepScore": 77, "sleepScoreFactorPercent": 65,
     "sleepScoreFactorFeedback": "MODERATE", "recoveryTime": 1,
     "recoveryTimeFactorPercent": 99, "recoveryTimeFactorFeedback": "GOOD",
     "acwrFactorPercent": 100, "acwrFactorFeedback": "VERY_GOOD", "acuteLoad": 142,
     "stressHistoryFactorPercent": 91, "stressHistoryFactorFeedback": "GOOD",
     "hrvFactorPercent": 87, "hrvFactorFeedback": "GOOD", "hrvWeeklyAverage": 54,
     "sleepHistoryFactorPercent": 76, "sleepHistoryFactorFeedback": "GOOD", "validSleep": True,
     "inputContext": "AFTER_WAKEUP_RESET", "primaryActivityTracker": True,
     "recoveryTimeChangePhrase": "NO_CHANGE_SLEEP"},
]


@pytest.fixture
def app(mock_garmin_client):
    health_wellness.configure(mock_garmin_client)
    mock_garmin_client.get_training_readiness.return_value = _LIVE_2026_10_27
    return health_wellness.register_tools(FastMCP("recovery"))


async def _call(app, name, args):
    result = await app.call_tool(name, args)
    content = result[0] if isinstance(result, tuple) else result
    return json.loads(content[0].text)


@pytest.mark.asyncio
async def test_post_run_snapshot_reports_garmins_72h_not_zero(app):
    by_context = {
        s["context"]: s
        for s in await _call(app, "get_training_readiness", {"date": "2026-10-27"})
    }
    assert by_context["AFTER_POST_EXERCISE_RESET"]["recovery_time_hours"] == 72.0
    assert by_context["AFTER_POST_EXERCISE_RESET"]["score"] == 20
    # The wake-up snapshot really was ~0 (1 minute), and stays so.
    assert by_context["AFTER_WAKEUP_RESET"]["recovery_time_hours"] == 0.0


@pytest.mark.asyncio
async def test_recovery_time_remaining_after_a_run(app):
    data = await _call(app, "get_recovery_time_remaining", {"date": "2026-10-27"})
    assert data["remaining_hours"] == 72.0
    assert data["state"] == "not_recovered"
    assert data["input_context"] == "AFTER_POST_EXERCISE_RESET"
    assert data["as_of"] == "2026-10-27T15:01:16.0"
    # The phrase is still reported, as what it is: what changed at the event.
    assert data["change_phrase"] == "REACHED_ZERO"
    # 20 is the readiness score, and is labelled as one.
    assert data["readiness_score"] == 20
    assert "recovery_score" not in data


# --- No date given: the most recent snapshot, not the server's "today" --------

import datetime as _dt

from garmin_mcp.health_wellness import _collect_latest_recovery_time

_UTC = _dt.timezone.utc


class _DatedClient:
    """Training Readiness by athlete-local calendar date, as Garmin files it."""

    def __init__(self, by_date, fail_dates=()):
        self.by_date, self.fail_dates, self.asked = by_date, set(fail_dates), []

    def get_training_readiness(self, date):
        self.asked.append(date)
        if date in self.fail_dates:
            raise RuntimeError("503 Service Unavailable")
        return self.by_date.get(date, [])

    def get_morning_training_readiness(self, date):
        return None

    def get_activities(self, start, limit):
        return []


def test_evening_in_the_americas_finds_the_afternoon_snapshot():
    """The live failure: 00:20 UTC on 10-28 is 20:20 EDT on 10-27.

    Upstream looked up the server's date (10-28), found nothing and answered
    "unavailable" while the 72h post-run snapshot was current.
    """
    client = _DatedClient({"2026-10-27": _LIVE_2026_10_27})
    now = _dt.datetime(2026, 10, 28, 0, 20, tzinfo=_UTC)
    data = _collect_latest_recovery_time(client, now)
    assert data["remaining_hours"] == 72.0
    assert data["state"] == "not_recovered"
    assert data["date"] == "2026-10-27"
    assert data["as_of"] == "2026-10-27T15:01:16.0"
    assert data["resolved_as"] == "latest_snapshot"
    assert client.asked == ["2026-10-29", "2026-10-28", "2026-10-27"]


def test_daytime_finds_todays_latest_snapshot():
    client = _DatedClient({"2026-10-27": _LIVE_2026_10_27})
    data = _collect_latest_recovery_time(client, _dt.datetime(2026, 10, 27, 20, 0, tzinfo=_UTC))
    assert data["remaining_hours"] == 72.0
    assert data["input_context"] == "AFTER_POST_EXERCISE_RESET"


def test_the_newest_dated_snapshot_wins():
    """After the next wake-up, that snapshot supersedes yesterday's 72h."""
    wake = dict(_LIVE_2026_10_27[1], calendarDate="2026-10-28",
                timestampLocal="2026-10-28T07:40:00.0", recoveryTime=3000)
    client = _DatedClient({"2026-10-27": _LIVE_2026_10_27, "2026-10-28": [wake]})
    data = _collect_latest_recovery_time(client, _dt.datetime(2026, 10, 28, 12, 0, tzinfo=_UTC))
    assert data["date"] == "2026-10-28"
    assert data["remaining_hours"] == 50.0


def test_an_athlete_east_of_utc_is_found_on_utc_tomorrow():
    """UTC+10 at 22:00 UTC is 08:00 the next local day, filed under that date."""
    wake = dict(_LIVE_2026_10_27[1], calendarDate="2026-10-28",
                timestampLocal="2026-10-28T07:55:00.0", recoveryTime=600)
    client = _DatedClient({"2026-10-28": [wake], "2026-10-27": _LIVE_2026_10_27})
    data = _collect_latest_recovery_time(client, _dt.datetime(2026, 10, 27, 22, 0, tzinfo=_UTC))
    assert data["date"] == "2026-10-28"
    assert data["remaining_hours"] == 10.0


def test_a_failed_newer_lookup_is_reported_not_hidden():
    """The returned snapshot may not be the latest if a newer date failed."""
    client = _DatedClient({"2026-10-27": _LIVE_2026_10_27}, fail_dates={"2026-10-28"})
    data = _collect_latest_recovery_time(client, _dt.datetime(2026, 10, 28, 0, 20, tzinfo=_UTC))
    assert data["remaining_hours"] == 72.0
    assert any("503" in e["error"] for e in data["source_errors"])


def test_nothing_within_a_day_says_so():
    data = _collect_latest_recovery_time(
        _DatedClient({}), _dt.datetime(2026, 10, 28, 0, 20, tzinfo=_UTC)
    )
    assert data["state"] == "unavailable"
    assert data["checked_dates"] == ["2026-10-29", "2026-10-28", "2026-10-27"]


@pytest.mark.asyncio
async def test_the_tool_without_a_date_reports_the_latest_snapshot(app, mock_garmin_client):
    """Snapshots exist only for UTC yesterday -- the evening-in-New-York case.

    Upstream's default looked up only the server's today and so answered
    "unavailable" here.
    """
    yesterday = (_dt.datetime.now(_UTC).date() - _dt.timedelta(days=1)).isoformat()
    mock_garmin_client.get_training_readiness.side_effect = (
        lambda d: _LIVE_2026_10_27 if d == yesterday else []
    )
    mock_garmin_client.get_morning_training_readiness.return_value = None
    mock_garmin_client.get_activities.return_value = []
    data = await _call(app, "get_recovery_time_remaining", {})
    assert data["resolved_as"] == "latest_snapshot"
    assert data["remaining_hours"] == 72.0
