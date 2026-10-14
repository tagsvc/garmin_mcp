"""Tools built from captured Garmin Connect traffic on 2026-09-19.

Every fixture here is a real response body, taken from a HAR of Garmin
Connect's own web client. That matters: three tools were deleted earlier the
same day for being built on endpoints inferred from library method names, which
returned an empty list and a 404 against a live plan.
"""
import json

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import devices, workouts
from garmin_mcp.client_resolver import set_global_client


def _app(module, client):
    module.configure(client)
    set_global_client(client)
    return module.register_tools(FastMCP("t"))


async def _text(app, name, args):
    return (await app.call_tool(name, args))[0][0].text


# ─── get_coach_plan_progress ──────────────────────────────────────────────

# GET /atp-api/atp/athlete/calendar?athletePlanId=…&startDate=…&endDate=…
_LIVE_CALENDAR = [
    {
        "scheduledWorkoutDate": "2026-09-14", "workoutId": 1696625519,
        "scheduleWorkoutId": 1776111029, "activityId": 24359243048,
        "performanceRating": {"rating": "GOOD", "localizedRating": "Good Job"},
        "race": False, "benchmark": True,
    },
    {
        "scheduledWorkoutDate": "2026-09-17", "workoutId": 1698439941,
        "scheduleWorkoutId": 1777930137, "activityId": 24395968870,
        "performanceRating": {"rating": "GOOD", "localizedRating": "Good Job"},
        "race": False, "benchmark": False,
    },
    {
        "scheduledWorkoutDate": "2026-09-19", "workoutId": 1701453828,
        "scheduleWorkoutId": 1780843621, "activityId": None,
        "performanceRating": None, "race": False, "benchmark": False,
    },
]


async def _progress(client, payload=None):
    client.connectapi.return_value = _LIVE_CALENDAR if payload is None else payload
    app = _app(workouts, client)
    text = await _text(app, "get_coach_plan_progress",
                       {"plan_id": 1789330190, "start_date": "2026-09-13",
                        "end_date": "2026-09-19"})
    return json.loads(text), client


@pytest.mark.asyncio
async def test_surfaces_garmins_grade_for_each_completed_workout(mock_garmin_client):
    """The rating exists nowhere else in this server.

    It is Garmin Coach's assessment of execution against the prescription --
    not the watch's training effect, which measures physiological load.
    """
    payload, _ = await _progress(mock_garmin_client)
    done = payload["workouts"][0]
    assert done["rating"] == "GOOD"
    assert done["rating_text"] == "Good Job"
    assert payload["graded_count"] == 2


@pytest.mark.asyncio
async def test_incomplete_workout_has_no_grade_and_no_activity(mock_garmin_client):
    payload, _ = await _progress(mock_garmin_client)
    upcoming = payload["workouts"][2]
    assert upcoming["completed"] is False
    assert "rating" not in upcoming and "activity_id" not in upcoming


@pytest.mark.asyncio
async def test_benchmark_flagged_only_when_true(mock_garmin_client):
    """Garmin marks its own checkpoints; ordinary workouts stay uncluttered."""
    payload, _ = await _progress(mock_garmin_client)
    assert payload["workouts"][0]["benchmark"] is True
    assert "benchmark" not in payload["workouts"][1]
    assert not any("race" in w for w in payload["workouts"]), "race False must be omitted"


@pytest.mark.asyncio
async def test_calls_atp_api_with_the_date_range(mock_garmin_client):
    _, client = await _progress(mock_garmin_client)
    url = client.connectapi.call_args[0][0]
    assert "/atp-api/atp/athlete/calendar" in url
    assert "trainingplan-service" not in url
    assert "startDate=2026-09-13" in url and "endDate=2026-09-19" in url


@pytest.mark.asyncio
async def test_empty_range_says_so_rather_than_returning_a_shell(mock_garmin_client):
    payload_text = None
    mock_garmin_client.connectapi.return_value = []
    app = _app(workouts, mock_garmin_client)
    payload_text = await _text(app, "get_coach_plan_progress",
                               {"plan_id": 1, "start_date": "2026-01-01",
                                "end_date": "2026-01-07"})
    assert "No plan workouts" in payload_text


@pytest.mark.asyncio
async def test_bad_date_is_rejected(mock_garmin_client):
    app = _app(workouts, mock_garmin_client)
    out = await _text(app, "get_coach_plan_progress",
                      {"plan_id": 1, "start_date": "13-09-2026",
                       "end_date": "2026-09-19"})
    assert "Error" in out or "format" in out.lower()


# ─── get_coach_plans ──────────────────────────────────────────────────────

_LIVE_ACTIVE = [{
    "athletePlanId": 1789330190,
    "athleteRace": {"raceDay": "2026-11-26", "raceName": "River Vale 5K"},
    "goalTimeSeconds": 1860, "planCompleted": False, "workoutsPerWeek": 3,
    "confidence": 78, "registrationDate": "2026-09-13T20:09:50.515+00:00",
}]


@pytest.mark.asyncio
async def test_lists_active_plans_with_goal_and_confidence(mock_garmin_client):
    mock_garmin_client.connectapi.side_effect = [_LIVE_ACTIVE, []]
    payload = json.loads(
        await _text(_app(workouts, mock_garmin_client), "get_coach_plans", {})
    )
    plan = payload["plans"][0]
    assert plan["plan_id"] == 1789330190
    assert plan["status"] == "active"
    assert plan["goal_time"] == "31:00"
    assert plan["confidence"] == 78


@pytest.mark.asyncio
async def test_completed_plans_are_labelled_and_skippable(mock_garmin_client):
    finished = [dict(_LIVE_ACTIVE[0], athletePlanId=111, planCompleted=True, quit=None)]
    mock_garmin_client.connectapi.side_effect = [_LIVE_ACTIVE, finished]
    payload = json.loads(
        await _text(_app(workouts, mock_garmin_client), "get_coach_plans", {})
    )
    assert {p["status"] for p in payload["plans"]} == {"active", "completed"}

    mock_garmin_client.connectapi.side_effect = [_LIVE_ACTIVE]
    payload = json.loads(
        await _text(_app(workouts, mock_garmin_client), "get_coach_plans",
                    {"include_completed": False})
    )
    assert payload["count"] == 1 and payload["plans"][0]["status"] == "active"


# Observed 2026-10-14 on plan 1789330190 after it was cancelled: the plan-detail
# payload carried a non-null `quit` AND planCompleted=true, and the plan was
# listed under /completed. Only quit's non-null-ness is relied on; the value's
# own shape was not observed, hence the placeholder.
_QUIT_MARKER = "non-null"


@pytest.mark.asyncio
async def test_a_quit_plan_is_not_reported_as_completed(mock_garmin_client):
    """Garmin's /completed list holds cancelled plans too; status must say quit."""
    quit_plan = [dict(_LIVE_ACTIVE[0], planCompleted=True, quit=_QUIT_MARKER)]
    mock_garmin_client.connectapi.side_effect = [[], quit_plan]
    payload = json.loads(
        await _text(_app(workouts, mock_garmin_client), "get_coach_plans", {})
    )
    assert payload["plans"][0]["status"] == "quit"


@pytest.mark.asyncio
async def test_quit_is_read_from_the_detail_when_the_list_entry_lacks_it(
    mock_garmin_client,
):
    """The list entry's shape is unverified; fall back to the detail, which carries quit."""
    entry = [dict(_LIVE_ACTIVE[0], planCompleted=True)]  # no `quit` key at all
    assert "quit" not in entry[0]
    detail = {"athletePlanId": 1789330190, "planCompleted": True, "quit": _QUIT_MARKER}
    mock_garmin_client.connectapi.side_effect = [[], entry, detail]
    payload = json.loads(
        await _text(_app(workouts, mock_garmin_client), "get_coach_plans", {})
    )
    assert payload["plans"][0]["status"] == "quit"
    assert "athletePlanId=1789330190" in mock_garmin_client.connectapi.call_args_list[2].args[0]

    detail_finished = dict(detail, quit=None)
    mock_garmin_client.connectapi.side_effect = [[], entry, detail_finished]
    payload = json.loads(
        await _text(_app(workouts, mock_garmin_client), "get_coach_plans", {})
    )
    assert payload["plans"][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_an_undeterminable_ended_plan_is_not_called_completed(mock_garmin_client):
    """If quit cannot be read, say "ended" -- "completed" is the mislabel being fixed."""
    entry = [dict(_LIVE_ACTIVE[0], planCompleted=True)]
    mock_garmin_client.connectapi.side_effect = [[], entry, RuntimeError("503")]
    payload = json.loads(
        await _text(_app(workouts, mock_garmin_client), "get_coach_plans", {})
    )
    assert payload["count"] == 1
    assert payload["plans"][0]["status"] == "ended"


@pytest.mark.asyncio
async def test_no_plans_reports_plainly(mock_garmin_client):
    """The deleted tool returned an empty list here, reading as a real answer."""
    mock_garmin_client.connectapi.side_effect = [[], []]
    out = await _text(_app(workouts, mock_garmin_client), "get_coach_plans", {})
    assert "No Garmin Coach plans found" in out


# ─── get_sensors ──────────────────────────────────────────────────────────

# GET /gc-api/device-service/sensors — Garmin repeats a sensor per paired device
_LIVE_SENSORS = [
    {"deviceName": "HRM 600", "batteryStatus": "NEW", "batteryLevel": 93,
     "sensorType": "HEART_RATE", "prioritySensor": False, "serialNumber": 33554456,
     "softwareVersion": "5.2", "lastConnected": "2026-09-17T14:05:35.0",
     "manufacturer": "GARMIN", "rechargeableSensorCapable": True},
    {"deviceName": "HRM 600", "batteryStatus": "NEW", "batteryLevel": 93,
     "sensorType": "HEART_RATE", "prioritySensor": False, "serialNumber": 33554456,
     "softwareVersion": "5.2", "lastConnected": "2026-09-17T14:05:35.0",
     "manufacturer": "GARMIN", "rechargeableSensorCapable": True},
]


@pytest.mark.asyncio
async def test_sensor_battery_is_surfaced(mock_garmin_client):
    """get_devices returns names and serials but no battery state."""
    mock_garmin_client.connectapi.return_value = _LIVE_SENSORS
    payload = json.loads(
        await _text(_app(devices, mock_garmin_client), "get_sensors", {})
    )
    s = payload["sensors"][0]
    assert s["name"] == "HRM 600"
    assert s["battery_level"] == 93
    assert s["last_connected"] == "2026-09-17T14:05:35.0"


@pytest.mark.asyncio
async def test_duplicate_pairings_are_collapsed(mock_garmin_client):
    """Garmin lists the same strap once per paired device."""
    mock_garmin_client.connectapi.return_value = _LIVE_SENSORS
    payload = json.loads(
        await _text(_app(devices, mock_garmin_client), "get_sensors", {})
    )
    assert payload["count"] == 1, "the same serial must not appear twice"


@pytest.mark.asyncio
async def test_no_sensors_reports_plainly(mock_garmin_client):
    mock_garmin_client.connectapi.return_value = []
    out = await _text(_app(devices, mock_garmin_client), "get_sensors", {})
    assert "No paired sensors found" in out


# ─── reserved-but-unfilled days ("Stay Tuned for Details") ───────────────

# An adaptive plan reserves days before it generates workouts for them. The ATP
# calendar returns those days; the GraphQL workout feed does not, so reading the
# feed alone under-counts the week. Shape confirmed against plan 1789330190 over
# 2026-09-13..2026-09-26: 7 calendar entries against 5 generated workouts.
_PLACEHOLDER_DAY = {
    "scheduledWorkoutDate": "2026-09-24", "workoutId": None,
    "scheduleWorkoutId": None, "activityId": None,
    "performanceRating": None, "race": False, "benchmark": False,
}


@pytest.mark.asyncio
async def test_reserved_days_are_returned_with_a_date_and_nothing_else(
    mock_garmin_client,
):
    """The week's real shape is only visible here.

    A reserved day has no workout_id and no scheduled_workout_id -- there is no
    workout yet. It must still be counted, or a caller planning the week sees
    fewer sessions than the plan holds.
    """
    payload, _ = await _progress(
        mock_garmin_client, _LIVE_CALENDAR + [_PLACEHOLDER_DAY]
    )
    reserved = payload["workouts"][-1]
    assert reserved["date"] == "2026-09-24"
    assert reserved["completed"] is False
    assert "workout_id" not in reserved
    assert "scheduled_workout_id" not in reserved
    assert "activity_id" not in reserved


@pytest.mark.asyncio
async def test_reserved_days_count_toward_the_week_but_not_the_grades(
    mock_garmin_client,
):
    payload, _ = await _progress(
        mock_garmin_client, _LIVE_CALENDAR + [_PLACEHOLDER_DAY]
    )
    assert payload["count"] == 4, "a reserved day is part of the week"
    assert payload["graded_count"] == 2, "but it cannot be graded"
