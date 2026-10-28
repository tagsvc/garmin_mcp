"""Tools add account-unit companions beside Garmin's metric fields, and
workout uploads accept pace text.

Driven through the real MCP tool calls with a mocked Garmin client. The
metric fields stay exactly as before, so each test checks one of them too.
"""
import json
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import (
    activity_management,
    courses,
    gear_management,
    training,
    weight_management,
    workouts,
)
from garmin_mcp.client_resolver import set_global_client

MODULES = (activity_management, weight_management, gear_management, training, courses, workouts)


@pytest.fixture
def app(mock_garmin_client):
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {"workoutScheduleSummariesScalar": []}
    }
    set_global_client(mock_garmin_client)
    app = FastMCP("Test account units")
    for module in MODULES:
        module.configure(mock_garmin_client)
        app = module.register_tools(app)
    return app


async def _call(app, tool, arguments=None):
    result = await app.call_tool(tool, arguments or {})
    text = result[0][0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pytest.fail(f"{tool} returned text, not JSON: {text}")


def _unit_system(client, value):
    client.get_unit_system.return_value = value


ACTIVITY = {
    "activityId": 24873123388,
    "activityName": "Westwood - W4 Tue Hills",
    "activityTypeDTO": {"typeKey": "running"},
    "summaryDTO": {
        "distance": 3494.780029296875,
        "duration": 1401.156005859375,
        "averageSpeed": 2.4942,
        "averageMovingSpeed": 2.508,
        "maxSpeed": 4.189000129699707,
        "elevationGain": 44.0,
        "elevationLoss": 30.0,
    },
}


@pytest.mark.asyncio
async def test_get_activity_statute_us(app, mock_garmin_client):
    _unit_system(mock_garmin_client, "statute_us")
    mock_garmin_client.get_activity.return_value = ACTIVITY

    data = await _call(app, "get_activity", {"activity_id": 24873123388})

    assert data["distance_meters"] == 3494.780029296875
    assert data["distance_miles"] == 2.17
    assert data["avg_pace_per_mile"] == "10:45"
    assert data["avg_moving_speed_mps"] == 2.508
    assert data["avg_moving_pace_per_mile"] == "10:42"
    assert data["max_pace_per_mile"] == "6:24"
    assert data["avg_speed_mph"] == 5.6
    assert data["elevation_gain_feet"] == 144


@pytest.mark.asyncio
async def test_get_activity_metric(app, mock_garmin_client):
    _unit_system(mock_garmin_client, "metric")
    mock_garmin_client.get_activity.return_value = ACTIVITY

    data = await _call(app, "get_activity", {"activity_id": 24873123388})

    assert data["distance_km"] == 3.49
    assert data["avg_pace_per_km"] == "6:41"
    assert "distance_miles" not in data
    assert "elevation_gain_feet" not in data


@pytest.mark.asyncio
async def test_unknown_unit_system_leaves_output_as_before(app, mock_garmin_client):
    # The shared mock's get_unit_system returns a Mock, not a unit system.
    mock_garmin_client.get_activity.return_value = ACTIVITY

    data = await _call(app, "get_activity", {"activity_id": 24873123388})

    assert data["distance_meters"] == 3494.780029296875
    assert not [key for key in data if key.endswith(("_miles", "_km", "_feet", "_mph", "_kmh"))
                or "_pace_" in key]


ACTIVITY_LIST = [
    {"activityId": 1, "activityName": "Run", "activityType": {"typeKey": "running"},
     "distance": 7260.52978515625, "duration": 2899.157958984375, "averageSpeed": 2.504,
     "elevationGain": 37.0},
    {"activityId": 2, "activityName": "Pool", "activityType": {"typeKey": "lap_swimming"},
     "distance": 1371.6, "duration": 1800.0},
]


@pytest.mark.asyncio
async def test_activity_lists_convert_runs_and_leave_swims_in_meters(app, mock_garmin_client):
    _unit_system(mock_garmin_client, "statute_us")
    mock_garmin_client.connectapi.return_value = ACTIVITY_LIST
    mock_garmin_client.get_activities.return_value = ACTIVITY_LIST

    by_date = await _call(app, "get_activities_by_date",
                          {"start_date": "2026-10-20", "end_date": "2026-10-27"})
    paged = await _call(app, "get_activities", {"limit": 2})

    for data in (by_date, paged):
        run, swim = data["activities"]
        assert run["distance_miles"] == 4.51
        assert run["avg_speed_mps"] == 2.504
        assert run["avg_pace_per_mile"] == "10:43"
        assert run["elevation_gain_feet"] == 121
        assert swim["distance_meters"] == 1371.6
        assert "distance_miles" not in swim


@pytest.mark.asyncio
async def test_get_activities_fordate(app, mock_garmin_client):
    _unit_system(mock_garmin_client, "statute_us")
    mock_garmin_client.get_activities_fordate.return_value = {
        "ActivitiesForDay": {"payload": ACTIVITY_LIST[:1]}
    }

    data = await _call(app, "get_activities_fordate", {"date": "2026-10-24"})

    assert data["activities"][0]["distance_miles"] == 4.51
    assert data["activities"][0]["avg_pace_per_mile"] == "10:43"


@pytest.mark.asyncio
async def test_get_activity_splits_run_laps(app, mock_garmin_client):
    _unit_system(mock_garmin_client, "statute_us")
    mock_garmin_client.get_activity_splits.return_value = {
        "activityId": 1,
        "lapDTOs": [{"lapIndex": 1, "distance": 1609.344, "duration": 642.0,
                     "averageSpeed": 2.507, "averageMovingSpeed": 2.52,
                     "elevationGain": 12.0}],
    }

    data = await _call(app, "get_activity_splits", {"activity_id": 1})

    lap = data["laps"][0]
    assert lap["distance_meters"] == 1609.344
    assert lap["distance_miles"] == 1.0
    assert lap["avg_pace_per_mile"] == "10:42"
    assert lap["avg_moving_pace_per_mile"] == "10:39"
    assert lap["elevation_gain_feet"] == 39


@pytest.mark.asyncio
async def test_get_activity_splits_swim_laps_keep_meters(app, mock_garmin_client):
    _unit_system(mock_garmin_client, "statute_us")
    mock_garmin_client.get_activity_splits.return_value = {
        "activityId": 1,
        "lapDTOs": [{"lapIndex": 1, "distance": 45.72, "averageSpeed": 0.7,
                     "averageSwimCadence": 22.0,
                     "lengthDTOs": [{"lengthIndex": 1, "distance": 22.86, "averageSpeed": 0.7}]}],
    }

    data = await _call(app, "get_activity_splits", {"activity_id": 1})

    lap = data["laps"][0]
    assert "distance_miles" not in lap and "avg_pace_per_mile" not in lap
    assert "distance_miles" not in lap["lengths"][0]


RAW_SPLIT = {"distance": 1245.35, "averageSpeed": 2.409, "maxSpeed": 2.631,
             "elevationGain": 4.0, "type": "RWD_RUN"}


@pytest.mark.asyncio
async def test_typed_splits_and_split_summaries_get_camelcase_companions(app, mock_garmin_client):
    _unit_system(mock_garmin_client, "statute_us")
    mock_garmin_client.get_activity_typed_splits.return_value = {"activityId": 1, "splits": [RAW_SPLIT]}
    mock_garmin_client.get_activity_split_summaries.return_value = {
        "activityId": 1, "splitSummaries": [{**RAW_SPLIT, "splitType": "RWD_RUN"}]
    }

    typed = await _call(app, "get_activity_typed_splits", {"activity_id": 1})
    summaries = await _call(app, "get_activity_split_summaries", {"activity_id": 1})

    for split in (typed["splits"][0], summaries["splitSummaries"][0]):
        assert split["distance"] == 1245.35
        assert split["distanceMiles"] == 0.77
        assert split["averagePacePerMile"] == "11:08"
        assert split["averageSpeedMph"] == 5.4
        assert split["elevationGainFeet"] == 13


@pytest.mark.asyncio
async def test_weigh_ins_get_pounds(app, mock_garmin_client):
    _unit_system(mock_garmin_client, "statute_us")
    weigh_in = {"calendarDate": "2026-10-27", "weight": 81234.0, "boneMass": 3400.0,
                "muscleMass": 36000.0}
    mock_garmin_client.get_weigh_ins.return_value = {
        "dailyWeightSummaries": [{"allWeightMetrics": [weigh_in]}],
        "totalAverage": {"weight": 81234.0},
    }
    mock_garmin_client.get_daily_weigh_ins.return_value = {
        "dateWeightList": [weigh_in], "totalAverage": {"weight": 81234.0},
    }

    ranged = await _call(app, "get_weigh_ins", {"start_date": "2026-10-20", "end_date": "2026-10-27"})
    daily = await _call(app, "get_daily_weigh_ins", {"date": "2026-10-27"})

    for data in (ranged, daily):
        measurement = data["measurements"][0]
        assert measurement["weight_kg"] == 81.23
        assert measurement["weight_lb"] == 179.1
        assert measurement["bone_mass_lb"] == 7.5
        assert measurement["muscle_mass_lb"] == 79.4
        assert data["average_weight_lb"] == 179.1


@pytest.mark.asyncio
async def test_weigh_ins_metric_account_gets_no_pounds(app, mock_garmin_client):
    _unit_system(mock_garmin_client, "metric")
    mock_garmin_client.get_daily_weigh_ins.return_value = {
        "dateWeightList": [{"weight": 81234.0}],
    }

    data = await _call(app, "get_daily_weigh_ins", {"date": "2026-10-27"})

    assert "weight_lb" not in data["measurements"][0]


def _mock_gear(client, total_meters):
    client.get_device_last_used.return_value = {"userProfileNumber": 42}
    client.get_gear.return_value = [{
        "uuid": "abc", "displayName": "Pegasus", "gearStatusName": "active",
        "maximumMeters": 804672.0,
    }]
    client.connectapi.return_value = []
    client.get_gear_defaults.return_value = []
    client.get_gear_stats.return_value = {"totalActivities": 30, "totalDistance": total_meters}


@pytest.mark.asyncio
async def test_get_gear_miles_come_from_meters_not_rounded_km(app, mock_garmin_client):
    _unit_system(mock_garmin_client, "statute_us")
    # 150,050 m: the km field rounds to 150.1, which is 93.3 mi; the meters
    # themselves are 93.24 mi, so 93.2.
    _mock_gear(mock_garmin_client, 150050.0)

    data = await _call(app, "get_gear", {})

    item = data["gear"][0]
    assert item["stats"]["total_distance_km"] == 150.1
    assert item["stats"]["total_distance_miles"] == 93.2
    assert item["max_distance_miles"] == 500.0


@pytest.mark.asyncio
async def test_get_gear_metric_account_has_no_miles(app, mock_garmin_client):
    _unit_system(mock_garmin_client, "metric")
    _mock_gear(mock_garmin_client, 160950.0)

    data = await _call(app, "get_gear", {})

    item = data["gear"][0]
    assert "total_distance_miles" not in item["stats"]
    assert "max_distance_miles" not in item


@pytest.mark.asyncio
async def test_get_gear_activities(app, mock_garmin_client):
    _unit_system(mock_garmin_client, "statute_us")
    mock_garmin_client.get_gear_activities.return_value = [
        {"activityId": 1, "distance": 3218.688, "activityType": {"typeKey": "running"}},
    ]

    data = await _call(app, "get_gear_activities", {"gear_uuid": "abc"})

    assert data["total_distance_miles"] == 2.0
    assert data["activities"][0]["distance_miles"] == 2.0


@pytest.mark.asyncio
async def test_running_tolerance_and_trend_get_miles(app, mock_garmin_client):
    _unit_system(mock_garmin_client, "statute_us")
    mock_garmin_client.get_running_tolerance.return_value = [{
        "calendarDate": "2026-10-27", "acuteTolerance": 25000.0,
        "acuteImpactLoad": 10000.0, "acuteDistance": 8046.72,
        "tolerance": 25000.0, "totalImpactLoad": 10000.0, "totalDistance": 8046.72,
    }]

    day = await _call(app, "get_running_tolerance", {"date": "2026-10-27"})
    trend = await _call(app, "get_running_tolerance_trend",
                        {"start_date": "2026-10-01", "end_date": "2026-10-27"})

    assert day["tolerance_km"] == 25.0
    assert day["tolerance_miles"] == 15.53
    assert day["distance_miles"] == 5.0
    assert day["load_ratio"] == 1.24
    assert trend["latest_tolerance_miles"] == 15.53
    assert trend["trend"][0]["acute_load_miles"] == 6.21


@pytest.mark.asyncio
async def test_lactate_threshold_gets_pace(app, mock_garmin_client):
    _unit_system(mock_garmin_client, "statute_us")
    mock_garmin_client.get_lactate_threshold.return_value = {
        "speed_and_heart_rate": {"speed": 0.3888878, "heartRate": 170},
        "power": {"weight": 73.0},
    }

    data = await _call(app, "get_lactate_threshold")

    assert data["lactate_threshold_speed_mps"] == pytest.approx(3.888878)
    assert data["lactate_threshold_pace_per_mile"] == "6:54"
    assert data["weight_lb"] == 160.9


@pytest.mark.asyncio
async def test_courses_get_miles_and_feet(app, mock_garmin_client):
    _unit_system(mock_garmin_client, "statute_us")
    course = {"courseId": 7, "courseName": "Loop", "distanceInMeters": 5000.0,
              "elevationGainInMeters": 30.48, "elevationLossInMeters": 30.48,
              "activityType": {"typeKey": "running"}}
    mock_garmin_client.connectapi.return_value = [course]
    mock_garmin_client.client.connectapi.return_value = {
        **course, "coursePoints": [{"name": "Water", "distance": 1609.344}],
    }

    listed = await _call(app, "get_courses")
    details = await _call(app, "get_course_details", {"course_id": 7})

    assert listed["courses"][0]["distance_miles"] == 3.11
    assert listed["courses"][0]["elevation_gain_feet"] == 100
    assert details["distance_miles"] == 3.11
    assert details["waypoints"][0]["distance_miles"] == 1.0


PACE_WORKOUT = {
    "workoutId": 99,
    "workoutName": "Tempo",
    "sportType": {"sportTypeKey": "running"},
    "estimatedDistanceInMeters": 8046.72,
    "workoutSegments": [{
        "segmentOrder": 1,
        "sportType": {"sportTypeKey": "running"},
        "workoutSteps": [{
            "type": "ExecutableStepDTO",
            "stepOrder": 1,
            "stepType": {"stepTypeKey": "interval"},
            "endCondition": {"conditionTypeKey": "distance"},
            "endConditionValue": 1609.344,
            "targetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"},
            "targetValueOne": 2.777,
            "targetValueTwo": 2.472,
        }],
    }],
}


@pytest.mark.asyncio
async def test_get_workout_by_id_shows_target_paces(app, mock_garmin_client):
    _unit_system(mock_garmin_client, "statute_us")
    mock_garmin_client.get_workout_by_id.return_value = PACE_WORKOUT

    data = await _call(app, "get_workout_by_id", {"workout_id": 99})

    assert data["estimated_distance_miles"] == 5.0
    step = data["segments"][0]["steps"][0]
    assert step["target_value_low"] == 2.777
    assert step["target_pace_low_per_mile"] == "9:40"
    assert step["target_pace_high_per_mile"] == "10:51"
    assert step["end_condition_value_miles"] == 1.0


@pytest.mark.asyncio
async def test_get_workout_by_id_raw_is_garmins_json_unchanged(app, mock_garmin_client):
    _unit_system(mock_garmin_client, "statute_us")
    mock_garmin_client.get_workout_by_id.return_value = PACE_WORKOUT

    data = await _call(app, "get_workout_by_id", {"workout_id": 99, "raw": True})

    assert data == PACE_WORKOUT


# --- pace text in workout uploads ------------------------------------------


def _pace_workout(**bounds):
    step = {
        "type": "ExecutableStepDTO",
        "stepOrder": 1,
        "stepType": {"stepTypeId": 3, "stepTypeKey": "interval"},
        "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time"},
        "endConditionValue": 600,
        "targetType": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"},
        **bounds,
    }
    return {
        "workoutName": "Pace text",
        "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": {"sportTypeId": 1, "sportTypeKey": "running"},
            "workoutSteps": [step],
        }],
    }


def _arguments(tool, payload):
    return {
        "upload_workout": {"workout_data": payload},
        "upload_workouts": {"workouts": [payload]},
        "schedule_workouts": {"schedules": [{"calendar_date": "2026-11-02", "workout_data": payload}]},
    }[tool]


def _sent_step(client):
    return client.upload_workout.call_args.args[0]["workoutSegments"][0]["workoutSteps"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["upload_workout", "upload_workouts", "schedule_workouts"])
async def test_pace_text_is_uploaded_as_meters_per_second(app, mock_garmin_client, tool):
    mock_garmin_client.upload_workout.return_value = {"workoutId": 123}
    mock_garmin_client.client.post.return_value = Mock(status_code=200)

    await _call(app, tool, _arguments(
        tool, _pace_workout(targetValueOne="10:00/mi", targetValueTwo="11:00 min/mi")
    ))

    step = _sent_step(mock_garmin_client)
    assert step["targetValueOne"] == 2.68224
    assert step["targetValueTwo"] == 2.4384
    # Garmin's order is kept, not sorted by speed.
    assert step["targetValueOne"] > step["targetValueTwo"]


@pytest.mark.asyncio
async def test_pace_text_nested_in_the_target_block_moves_to_the_step(app, mock_garmin_client):
    mock_garmin_client.upload_workout.return_value = {"workoutId": 123}
    payload = _pace_workout()
    payload["workoutSegments"][0]["workoutSteps"][0]["targetType"].update(
        targetValueOne="5:00/km", targetValueTwo="5:30/km"
    )

    await _call(app, "upload_workout", {"workout_data": payload})

    step = _sent_step(mock_garmin_client)
    assert step["targetValueOne"] == 3.3333333
    assert step["targetValueTwo"] == 3.030303
    assert "targetValueOne" not in step["targetType"]


@pytest.mark.asyncio
async def test_secondary_pace_target_accepts_pace_text(app, mock_garmin_client):
    mock_garmin_client.upload_workout.return_value = {"workoutId": 123}
    payload = _pace_workout(targetValueOne=2.7, targetValueTwo=2.5)
    step = payload["workoutSegments"][0]["workoutSteps"][0]
    step["targetType"] = {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"}
    step.update(targetValueOne=140, targetValueTwo=150)
    step["secondaryTargetType"] = {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"}
    step.update(secondaryTargetValueOne="10:00/mi", secondaryTargetValueTwo="11:00/mi")

    await _call(app, "upload_workout", {"workout_data": payload})

    sent = _sent_step(mock_garmin_client)
    assert sent["secondaryTargetValueOne"] == 2.68224
    assert sent["targetValueOne"] == 140


@pytest.mark.asyncio
async def test_numbers_and_numeric_strings_pass_through(app, mock_garmin_client):
    mock_garmin_client.upload_workout.return_value = {"workoutId": 123}

    await _call(app, "upload_workout", {
        "workout_data": _pace_workout(targetValueOne=2.777, targetValueTwo="2.472")
    })

    step = _sent_step(mock_garmin_client)
    assert step["targetValueOne"] == 2.777
    assert step["targetValueTwo"] == "2.472"


@pytest.mark.asyncio
async def test_pace_without_a_unit_is_rejected_before_upload(app, mock_garmin_client):
    payload = _pace_workout(targetValueOne="10:00/mi", targetValueTwo="11:00")

    result = await app.call_tool("upload_workout", {"workout_data": payload})

    text = result[0][0].text
    assert "workoutSegments[0].workoutSteps[0].targetValueTwo" in text
    assert "'11:00' is not a pace" in text
    mock_garmin_client.upload_workout.assert_not_called()
    # Checked before anything changed: the valid bound was not converted either.
    assert payload["workoutSegments"][0]["workoutSteps"][0]["targetValueOne"] == "10:00/mi"


@pytest.mark.asyncio
async def test_pace_text_on_a_non_pace_target_is_left_for_garmin(app, mock_garmin_client):
    mock_garmin_client.upload_workout.return_value = {"workoutId": 123}
    payload = _pace_workout(targetValueOne="10:00/mi")
    payload["workoutSegments"][0]["workoutSteps"][0]["targetType"] = {
        "workoutTargetTypeId": 6, "workoutTargetTypeKey": "power.between"
    }

    await _call(app, "upload_workout", {"workout_data": payload})

    assert _sent_step(mock_garmin_client)["targetValueOne"] == "10:00/mi"


@pytest.mark.asyncio
async def test_a_bad_pace_in_a_later_step_leaves_earlier_steps_unconverted(app, mock_garmin_client):
    payload = _pace_workout(targetValueOne="10:00/mi", targetValueTwo="11:00/mi")
    steps = payload["workoutSegments"][0]["workoutSteps"]
    steps.append({**steps[0], "stepOrder": 2, "targetValueOne": "9:00", "targetValueTwo": None})

    result = await app.call_tool("upload_workout", {"workout_data": payload})

    assert "workoutSteps[1].targetValueOne" in result[0][0].text
    mock_garmin_client.upload_workout.assert_not_called()
    assert steps[0]["targetValueOne"] == "10:00/mi"
