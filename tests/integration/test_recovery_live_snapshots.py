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
