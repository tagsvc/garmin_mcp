"""Garmin Coach plan curation, pinned to a real adaptive-plan payload.

The fixture below is the shape Garmin actually returns for an enrolled ATP
plan. Two things in it are load-bearing:

  * ``planName`` is None. Garmin does not name adaptive plans.
  * there is no ``trainingType`` key. Upstream reads only that key, so it
    resolves to None, is dropped by the "strip None" filter, and the entire
    trainingPlanDetailsDTO -- the race goal included -- goes with it, leaving
    nothing but an id and a classification.

Also covers get_gear_activities.
"""
import json

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import gear_management, workouts
from garmin_mcp.client_resolver import set_global_client

# Verbatim shape of a live adaptive plan (values are this fork's own account).
_LIVE_ATP_PLAN = {
    "planName": None,
    "trainingPlanId": 1789330190,
    "trainingPlanClassification": "ATP",
    "trainingPlanDetailsDTO": {
        "athletePlanId": 1789330190,
        "athleteRace": {
            "raceDay": "2026-11-26",
            "raceName": "River Vale 5K",
            "raceUrl": None,
        },
        "remainingAtpWorkoutsForWeek": 1,
        "workoutsPerWeek": 3,
        "registrationDate": "2026-09-13T20:09:50.515",
        "planCompleted": False,
        "workoutPlanTypeId": 1,
    },
    # Verbatim from the live feed. Note what is NOT here: workoutPhrase,
    # estimatedDurationInSecs, trainingPlanId and fbtAdaptivePlanId all exist in
    # the schema but Garmin leaves them null on an ATP plan -- the plan id
    # arrives in atpPlanId instead.
    "workoutScheduleSummaries": [
        {
            "scheduledWorkoutId": 1776111029,
            "workoutId": 1696625519,
            "workoutName": "Benchmark Run",
            "workoutType": "running",
            "scheduleDate": "2026-09-14",
            "atpPlanId": 1789330190,
            "associatedActivityId": 24359243048,
            "associatedActivityDateTime": "2026-09-14T11:54:36.0",
            "protected": True,
            "race": False,
        },
        {
            "scheduledWorkoutId": 1780843621,
            "workoutId": 1701453828,
            "workoutName": "Run Walk Run\u00ae",
            "workoutType": "running",
            "scheduleDate": "2026-09-19",
            "atpPlanId": 1789330190,
            "protected": False,
            "race": False,
        },
    ],
}


def _app(client):
    workouts.configure(client)
    set_global_client(client)
    return workouts.register_tools(FastMCP("t"))


def _graphql(plans):
    return {"data": {"trainingPlanScalar": {"trainingPlanWorkoutScheduleDTOS": plans}}}


async def _plan(app, client):
    client.query_garmin_graphql.return_value = _graphql([_LIVE_ATP_PLAN])
    text = (await app.call_tool("get_garmin_coach_workouts",
                                {"calendar_date": "2026-09-19"}))[0][0].text
    return json.loads(text)["plans"][0]


@pytest.mark.asyncio
async def test_race_goal_survives_curation(mock_garmin_client):
    """The goal is the point of an adaptive plan."""
    plan = await _plan(_app(mock_garmin_client), mock_garmin_client)
    assert plan["race_name"] == "River Vale 5K"
    assert plan["race_day"] == "2026-11-26"


@pytest.mark.asyncio
async def test_plan_shape_survives_curation(mock_garmin_client):
    plan = await _plan(_app(mock_garmin_client), mock_garmin_client)
    assert plan["workouts_per_week"] == 3
    assert plan["remaining_workouts_this_week"] == 1
    assert plan["registration_date"].startswith("2026-09-13")
    assert plan["plan_completed"] is False, "False must survive the strip-None filter"


@pytest.mark.asyncio
async def test_identity_fields_still_present(mock_garmin_client):
    plan = await _plan(_app(mock_garmin_client), mock_garmin_client)
    assert plan["training_plan_id"] == 1789330190
    assert plan["classification"] == "ATP"
    # Garmin sends no name for adaptive plans; absent beats a null.
    assert "name" not in plan


@pytest.mark.asyncio
async def test_absent_details_do_not_crash_or_emit_nulls(mock_garmin_client):
    """A plan family without the DTO must degrade, not fail."""
    bare = {"trainingPlanId": 42, "trainingPlanClassification": "PHASED",
            "workoutScheduleSummaries": []}
    mock_garmin_client.query_garmin_graphql.return_value = _graphql([bare])
    text = (await _app(mock_garmin_client).call_tool(
        "get_garmin_coach_workouts", {"calendar_date": "2026-09-19"}))[0][0].text
    if "No training plan workouts" not in text:
        plan = json.loads(text)["plans"][0]
        assert plan["training_plan_id"] == 42
        assert not any(k.startswith("race_") for k in plan)


@pytest.mark.asyncio
async def test_removed_training_plan_tools_are_gone(mock_garmin_client):
    """trainingplan-service REST returns empty or 404 for an adaptive plan.

    Re-adding them on that path would look like a feature and behave like a bug.
    """
    names = {t.name for t in _app(mock_garmin_client)._tool_manager.list_tools()}
    assert not names & {
        "get_training_plans",
        "get_training_plan_details",
        "get_adaptive_training_plan_details",
    }


# ─── get_gear_activities ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gear_activities_totals_distance(mock_garmin_client):
    gear_management.configure(mock_garmin_client)
    set_global_client(mock_garmin_client)
    app = gear_management.register_tools(FastMCP("g"))
    mock_garmin_client.get_gear_activities.return_value = [
        {"activityId": 1, "distance": 2864.6, "activityType": {"typeKey": "walking"}},
        {"activityId": 2, "distance": 788.6, "activityType": {"typeKey": "walking"}},
    ]
    payload = json.loads(
        (await app.call_tool("get_gear_activities", {"gear_uuid": "abc"}))[0][0].text
    )
    assert payload["activity_count"] == 2
    assert payload["total_distance_m"] == pytest.approx(3653.2)
    mock_garmin_client.get_gear_activities.assert_called_once_with("abc", limit=1000)


@pytest.mark.asyncio
async def test_gear_activities_tolerates_null_distance(mock_garmin_client):
    gear_management.configure(mock_garmin_client)
    set_global_client(mock_garmin_client)
    app = gear_management.register_tools(FastMCP("g"))
    mock_garmin_client.get_gear_activities.return_value = [
        {"activityId": 1, "distance": None}, {"activityId": 2, "distance": 100.0},
    ]
    payload = json.loads(
        (await app.call_tool("get_gear_activities", {"gear_uuid": "g"}))[0][0].text
    )
    assert payload["total_distance_m"] == 100.0


# ─── workout-level curation, pinned to the same live payload ──────────────

async def _workouts(app, client):
    client.query_garmin_graphql.return_value = _graphql([_LIVE_ATP_PLAN])
    text = (await app.call_tool("get_garmin_coach_workouts",
                                {"calendar_date": "2026-09-19"}))[0][0].text
    return json.loads(text)["workouts"]


@pytest.mark.asyncio
async def test_plan_id_is_read_from_atp_plan_id(mock_garmin_client):
    """ATP plans carry the plan id in atpPlanId; trainingPlanId is null.

    Reading only trainingPlanId and fbtAdaptivePlanId loses the per-workout
    linkage on an adaptive plan entirely.
    """
    done, upcoming = await _workouts(_app(mock_garmin_client), mock_garmin_client)
    assert done["training_plan_id"] == 1789330190
    assert upcoming["training_plan_id"] == 1789330190


@pytest.mark.asyncio
async def test_performed_at_distinguishes_prescribed_from_actual(mock_garmin_client):
    """scheduleDate is when the plan asked; performed_at is when it happened."""
    done, upcoming = await _workouts(_app(mock_garmin_client), mock_garmin_client)
    assert done["date"] == "2026-09-14"
    assert done["performed_at"] == "2026-09-14T11:54:36.0"
    assert done["completed"] is True
    assert done["activity_id"] == 24359243048
    assert "performed_at" not in upcoming and upcoming["completed"] is False


@pytest.mark.asyncio
async def test_protected_marks_a_fixed_point_in_the_plan(mock_garmin_client):
    """Garmin will not reshuffle a protected workout when the plan adapts."""
    done, upcoming = await _workouts(_app(mock_garmin_client), mock_garmin_client)
    assert done["protected"] is True
    # False is omitted rather than reported -- most workouts are unprotected.
    assert "protected" not in upcoming


@pytest.mark.asyncio
async def test_owner_id_is_not_exposed(mock_garmin_client):
    """ownerId is an account identifier with no analytical value."""
    for w in await _workouts(_app(mock_garmin_client), mock_garmin_client):
        assert "owner_id" not in w and "ownerId" not in w


# ─── get_coach_plan_details, pinned to the live atp-api payload ───────────

# Verbatim from GET /atp-api/atp/athlete/plan?lang=en&athletePlanId=…
# captured from Garmin Connect's own web client.
_LIVE_ATP_DETAIL = {
    "athletePlanId": 1789330190,
    "userPk": 2688052,
    "registrationDate": "2026-09-13T20:09:50.515+00:00",
    "athleteRace": {
        "raceDay": "2026-11-26",
        "raceName": "River Vale 5K",
        "raceUrl": None,
        "raceEventId": 29974702,
        "raceEventUuid": None,
    },
    "goalTimeSeconds": 1860,
    "planCompleted": False,
    "workoutPlanId": 14,
    "workoutPlanTypeId": 1,
    "workoutCalendarDays": ["MONDAY", "TUESDAY", "THURSDAY", "SATURDAY"],
    "longRunDay": "SATURDAY",
    "workoutsPerWeek": 3,
    "pause": None,
    "quit": None,
    "confidence": 78,
    "prePlanWeeklyMileage": 0,
    "prePlanTrainingPace": 617,
    "athletePlanGoal": {"goal": "PACE", "localizedGoal": "Run with a time goal"},
    "coach": {
        "coachId": 2,
        "contentfulModel": {
            "contentType": "COACH",
            "contentfulContentTypeId": "ATPCoach",
            "contentfulContentId": "JeffGalloway",
        },
    },
    "action": None,
}


async def _detail(client, payload=None):
    client.connectapi.return_value = _LIVE_ATP_DETAIL if payload is None else payload
    app = _app(client)
    text = (await app.call_tool("get_coach_plan_details",
                                {"plan_id": 1789330190}))[0][0].text
    return json.loads(text), client


@pytest.mark.asyncio
async def test_calls_atp_api_not_trainingplan_service(mock_garmin_client):
    """trainingplan-service returns [] for an adaptive plan and 404s by id.

    Verified from an authenticated browser session: the adaptive plans live
    behind atp-api. Pointing this at trainingplan-service would report an
    enrolled plan as no plan.
    """
    _, client = await _detail(mock_garmin_client)
    url = client.connectapi.call_args[0][0]
    assert "/atp-api/atp/athlete/plan" in url
    assert "athletePlanId=1789330190" in url
    assert "trainingplan-service" not in url


@pytest.mark.asyncio
async def test_surfaces_the_goal_and_garmins_confidence_in_it(mock_garmin_client):
    payload, _ = await _detail(mock_garmin_client)
    assert payload["race_name"] == "River Vale 5K"
    assert payload["race_day"] == "2026-11-26"
    assert payload["goal_time_seconds"] == 1860
    assert payload["goal_time"] == "31:00", "seconds are unreadable as a race goal"
    assert payload["goal_type"] == "PACE"
    assert payload["confidence"] == 78


@pytest.mark.asyncio
async def test_surfaces_the_training_week_and_coach(mock_garmin_client):
    payload, _ = await _detail(mock_garmin_client)
    assert payload["workout_days"] == ["MONDAY", "TUESDAY", "THURSDAY", "SATURDAY"]
    assert payload["long_run_day"] == "SATURDAY"
    assert payload["workouts_per_week"] == 3
    assert payload["coach_key"] == "JeffGalloway"


@pytest.mark.asyncio
async def test_pause_and_quit_are_reported_as_booleans(mock_garmin_client):
    """Garmin sends an object when set and null otherwise, not a flag."""
    payload, _ = await _detail(mock_garmin_client)
    assert payload["paused"] is False and payload["quit"] is False

    stopped = dict(_LIVE_ATP_DETAIL, pause={"date": "2026-10-01"})
    payload, _ = await _detail(mock_garmin_client, stopped)
    assert payload["paused"] is True


@pytest.mark.asyncio
async def test_account_identifier_is_not_exposed(mock_garmin_client):
    """userPk identifies the account and has no analytical value."""
    payload, _ = await _detail(mock_garmin_client)
    assert "userPk" not in payload and "user_pk" not in payload


@pytest.mark.asyncio
async def test_missing_plan_reports_rather_than_returning_an_empty_shell(
    mock_garmin_client,
):
    """An empty reply says no plan was found, rather than an empty shell."""
    payload_text = None
    mock_garmin_client.connectapi.return_value = {}
    app = _app(mock_garmin_client)
    payload_text = (await app.call_tool("get_coach_plan_details",
                                        {"plan_id": 999}))[0][0].text
    assert "No adaptive training plan found" in payload_text
