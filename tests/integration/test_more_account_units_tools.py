"""The remaining tools gain account-unit companions; raw payloads keep their
shape and gain companions in their own camelCase style.

Driven through the real MCP tool calls with a mocked Garmin client and the
athlete's real values where the audit captured them.
"""
import json

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import (
    activity_analysis,
    activity_management,
    analytics,
    calendar_events,
    challenges,
    health_wellness,
    nutrition,
    training,
    user_profile,
    workouts,
)
from garmin_mcp.client_resolver import set_global_client
from tests.integration.test_calendar_events_tools import _month, _race

MODULES = (
    activity_analysis, activity_management, analytics, calendar_events, challenges,
    health_wellness, nutrition, training, user_profile, workouts,
)


@pytest.fixture
def app(mock_garmin_client):
    mock_garmin_client.query_garmin_graphql.return_value = {
        "data": {"workoutScheduleSummariesScalar": []}
    }
    set_global_client(mock_garmin_client)
    app = FastMCP("Test more account units")
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


def _units(client, value="statute_us"):
    client.get_unit_system.return_value = value


# --- activity payloads ------------------------------------------------------------

SPLIT = {"distance": 1245.35, "averageTemperature": 25.0, "maxTemperature": 28.0,
         "strideLength": 72.0, "verticalOscillation": 8.05, "avgStepLength": 0.72,
         "type": "RWD_RUN"}


@pytest.mark.asyncio
async def test_split_temperatures_and_stride_get_us_companions(app, mock_garmin_client):
    """The watch reports degrees Celsius with no unit: 25 read as Fahrenheit
    is a freezing run on a 63 F day."""
    _units(mock_garmin_client)
    mock_garmin_client.get_activity_typed_splits.return_value = {"splits": [dict(SPLIT)]}

    split = (await _call(app, "get_activity_typed_splits", {"activity_id": 1}))["splits"][0]

    assert split["averageTemperature"] == 25.0
    assert split["averageTemperatureFahrenheit"] == 77.0
    assert split["maxTemperatureFahrenheit"] == 82.4
    assert split["strideLengthInches"] == 28.3
    assert split["verticalOscillationInches"] == 3.2
    assert "avgStepLengthInches" not in split


@pytest.mark.asyncio
async def test_uk_and_metric_accounts_keep_celsius(app, mock_garmin_client):
    _units(mock_garmin_client, "statute_uk")
    mock_garmin_client.get_activity_typed_splits.return_value = {"splits": [dict(SPLIT)]}
    uk = (await _call(app, "get_activity_typed_splits", {"activity_id": 1}))["splits"][0]
    _units(mock_garmin_client, "metric")
    mock_garmin_client.get_activity_typed_splits.return_value = {"splits": [dict(SPLIT)]}
    metric = (await _call(app, "get_activity_typed_splits", {"activity_id": 1}))["splits"][0]

    assert "averageTemperatureFahrenheit" not in uk
    assert uk["strideLengthInches"] == 28.3
    assert "averageTemperatureFahrenheit" not in metric
    assert "strideLengthInches" not in metric


@pytest.mark.asyncio
async def test_get_activity_stride_and_oscillation_in_inches(app, mock_garmin_client):
    _units(mock_garmin_client)
    mock_garmin_client.get_activity.return_value = {
        "activityId": 1, "activityTypeDTO": {"typeKey": "running"},
        "summaryDTO": {"strideLength": 78.54, "verticalOscillation": 7.67},
    }

    data = await _call(app, "get_activity", {"activity_id": 1})

    assert data["avg_stride_length_in"] == 30.9
    assert data["avg_vertical_oscillation_in"] == 3.0


@pytest.mark.asyncio
async def test_activity_gear_maximum_distance_in_miles(app, mock_garmin_client):
    _units(mock_garmin_client)
    mock_garmin_client.get_activity_gear.return_value = [{"uuid": "a", "maximumMeters": 804672.0}]

    data = await _call(app, "get_activity_gear", {"activity_id": 1})

    assert data[0]["maximumMiles"] == 500.0


@pytest.mark.asyncio
async def test_exercise_set_weights_are_kg_and_lb(app, mock_garmin_client):
    """Garmin's set weight is bare grams; -1 means unweighted."""
    _units(mock_garmin_client)
    mock_garmin_client.get_activity_exercise_sets.return_value = {"exerciseSets": [
        {"setType": "ACTIVE", "weight": 20411.65},
        {"setType": "ACTIVE", "weight": -1},
    ]}

    data = await _call(app, "get_activity_exercise_sets", {"activity_id": 1})

    weighted, bodyweight = data["exerciseSets"]
    assert weighted["weightKg"] == 20.41
    assert weighted["weightLb"] == 45.0
    assert "weightKg" not in bodyweight and "weightLb" not in bodyweight


# --- daily stats and body -----------------------------------------------------------

@pytest.mark.asyncio
async def test_daily_distance_totals_get_miles(app, mock_garmin_client):
    _units(mock_garmin_client)
    mock_garmin_client.get_stats.return_value = {"totalDistanceMeters": 8046.72}
    mock_garmin_client.get_daily_steps.return_value = [
        {"calendarDate": "2026-10-27", "totalSteps": 9000, "totalDistance": 8046.72},
    ]
    mock_garmin_client.get_user_summary.return_value = {
        "totalDistanceMeters": 8046.72, "floorsAscendedInMeters": 30.48,
    }

    stats = await _call(app, "get_stats", {"date": "2026-10-27"})
    daily = await _call(app, "get_daily_steps", {"start_date": "2026-10-27", "end_date": "2026-10-27"})
    summary = await _call(app, "get_user_summary", {"date": "2026-10-27"})

    assert stats["distance_miles"] == 5.0
    assert daily[0]["totalDistanceMiles"] == 5.0
    assert summary["totalDistanceMiles"] == 5.0
    assert summary["floorsAscendedFeet"] == 100


@pytest.mark.asyncio
async def test_weekly_step_distances_get_miles(app, mock_garmin_client):
    _units(mock_garmin_client)
    mock_garmin_client.get_weekly_steps.return_value = [
        {"calendarDate": "2026-10-26", "values": {"totalSteps": 40000, "averageSteps": 5714,
                                                  "totalDistance": 32186.88, "averageDistance": 4598.13}},
    ]

    data = await _call(app, "get_weekly_steps", {"end_date": "2026-10-27", "weeks": 1})

    week = data["weekly_data"][0]
    assert week["total_distance_miles"] == 20.0
    assert week["average_distance_miles"] == 2.86


@pytest.mark.asyncio
async def test_body_composition_grams_get_pounds(app, mock_garmin_client):
    """79900.0 is grams, under a key with no unit."""
    _units(mock_garmin_client)
    mock_garmin_client.get_body_composition.return_value = {
        "dateWeightList": [{"calendarDate": "2026-10-27", "weight": 79900.0,
                            "boneMass": 3400.0, "muscleMass": 36000.0}],
        "totalAverage": {"weight": 79900.0},
    }

    data = await _call(app, "get_body_composition", {"start_date": "2026-10-27", "end_date": "2026-10-27"})

    entry = data["dateWeightList"][0]
    assert entry["weight"] == 79900.0
    assert entry["weightLb"] == 176.1
    assert entry["boneMassLb"] == 7.5
    assert entry["muscleMassLb"] == 79.4
    assert data["totalAverage"]["weightLb"] == 176.1


@pytest.mark.asyncio
async def test_stats_and_body_grams_get_pounds(app, mock_garmin_client):
    _units(mock_garmin_client)
    mock_garmin_client.get_stats_and_body.return_value = {
        "totalDistanceMeters": 1609.344, "weight": 79900.0,
    }

    data = await _call(app, "get_stats_and_body", {"date": "2026-10-27"})

    assert data["weightLb"] == 176.1
    assert data["totalDistanceMiles"] == 1.0


@pytest.mark.asyncio
async def test_metric_account_gets_no_pounds(app, mock_garmin_client):
    _units(mock_garmin_client, "metric")
    mock_garmin_client.get_body_composition.return_value = {
        "dateWeightList": [{"weight": 79900.0}],
    }

    data = await _call(app, "get_body_composition", {"start_date": "2026-10-27", "end_date": "2026-10-27"})

    assert "weightLb" not in data["dateWeightList"][0]


@pytest.mark.asyncio
async def test_hydration_follows_the_profiles_own_hydration_unit(app, mock_garmin_client):
    """The athlete's account is statute_us but logs water in milliliters:
    the hydration unit is its own setting."""
    _units(mock_garmin_client)
    mock_garmin_client.get_userprofile_settings.return_value = {
        "measurementSystem": "statute_us", "hydrationMeasurementUnit": "milliliter",
    }
    mock_garmin_client.get_hydration_data.return_value = {"valueInML": 2000.0, "goalInML": 2500.0}

    data = await _call(app, "get_hydration_data", {"date": "2026-10-27"})

    assert data == {"valueInML": 2000.0, "goalInML": 2500.0}


@pytest.mark.asyncio
async def test_hydration_in_ounces_gets_fluid_ounces(app, mock_garmin_client):
    mock_garmin_client.get_userprofile_settings.return_value = {"hydrationMeasurementUnit": "ounce"}
    mock_garmin_client.get_hydration_data.return_value = {"valueInML": 2000.0, "goalInML": None}

    data = await _call(app, "get_hydration_data", {"date": "2026-10-27"})

    assert data["valueInFlOz"] == 67.6
    assert "goalInFlOz" not in data


@pytest.mark.asyncio
async def test_skin_temperature_keys_say_deviation(app, mock_garmin_client):
    """Both values are deviations from the baseline, which the key did not
    say; Garmin sends each unit itself."""
    mock_garmin_client.connectapi.return_value = {
        "calendarDate": "2026-10-28",
        "metrics": [
            {"type": "SKIN_TEMP_C", "value": 0.1, "status": "IN_RANGE"},
            {"type": "SKIN_TEMP_F", "value": 0.2, "status": "IN_RANGE"},
        ],
    }

    data = await _call(app, "get_health_status", {"date": "2026-10-28"})

    assert data["skin_temperature_deviation_celsius"]["value"] == 0.1
    assert data["skin_temperature_deviation_fahrenheit"]["value"] == 0.2
    assert "skin_temperature_celsius" not in data and "skin_temp_f" not in data


@pytest.mark.asyncio
async def test_acclimation_altitude_in_feet(app, mock_garmin_client):
    _units(mock_garmin_client)
    mock_garmin_client.get_max_metrics.return_value = [{"heatAltitudeAcclimation": {
        "calendarDate": "2026-10-27", "altitudeAcclimation": 1524, "currentAltitude": 30,
    }}]

    data = await _call(app, "get_acclimation", {"date": "2026-10-27"})

    assert data["altitude_acclimation_feet"] == 5000
    assert data["current_altitude_feet"] == 98


# --- profile and nutrition ------------------------------------------------------------

@pytest.mark.asyncio
async def test_profile_units_are_added_beside_garmins(app, mock_garmin_client):
    """Live values: 79900 g, 177.8 cm, threshold speed 0.2694 (m/s / 10:
    read as m/s it is a 99 min/mile pace), and a FIT-epoch date."""
    _units(mock_garmin_client)
    mock_garmin_client.get_user_profile.return_value = {"userData": {
        "weight": 79900.0, "height": 177.8000030517578,
        "lactateThresholdSpeed": 0.26944369, "firstbeatRunningLtTimestamp": 1161544001,
    }}

    data = (await _call(app, "get_user_profile"))["userData"]

    assert data["weightLb"] == 176.1
    assert data["heightInches"] == 70.0
    assert data["heightFeetInches"] == "5'10\""
    assert data["lactateThresholdSpeedMps"] == 2.6944
    assert data["lactateThresholdPacePerMile"] == "9:57"
    assert data["firstbeatRunningLtTimeGmt"] == "2026-10-21T19:06:41Z"


@pytest.mark.asyncio
async def test_nutrition_weight_targets_in_pounds(app, mock_garmin_client):
    _units(mock_garmin_client)
    mock_garmin_client.connectapi.return_value = {
        "startingWeight": 79959, "targetWeightGoal": 77110, "weightChangeRate": 227,
    }

    data = await _call(app, "get_nutrition_daily_settings", {"date": "2026-10-27"})

    assert data["startingWeightLb"] == 176.3
    assert data["targetWeightGoalLb"] == 170.0
    assert data["weightChangeRateLb"] == 0.5


# --- records, badges, goals, calendar, workouts ---------------------------------------

@pytest.mark.asyncio
async def test_personal_record_distances_and_elevation_get_companions(app, mock_garmin_client):
    _units(mock_garmin_client)
    mock_garmin_client.get_personal_record.return_value = [
        {"typeId": 7, "value": 7260.52978515625, "prStartTimeLocalFormatted": "2026-10-24 10:37:12"},
        {"typeId": 9, "value": 2466.0, "prStartTimeLocalFormatted": "2013-09-08 08:00:00"},
        {"typeId": 17, "value": 1000.0, "prStartTimeLocalFormatted": "2020-01-01 08:00:00"},
    ]

    data = await _call(app, "get_personal_record")

    by_type = {record["type_id"]: record for record in data}
    assert by_type[7]["value"] == "7.26 km"
    assert by_type[7]["distance_miles"] == 4.51
    assert by_type[9]["elevation_feet"] == 8091
    assert "distance_meters" not in by_type[17]  # pool swim: Garmin's pool units


@pytest.mark.asyncio
async def test_badge_distances_get_companions(app, mock_garmin_client):
    _units(mock_garmin_client)
    mock_garmin_client.get_non_completed_badge_challenges.return_value = [{
        "uuid": "a", "badgeChallengeName": "30 mi", "badgeUnitId": 1,
        "badgeProgressValue": 16093.44, "badgeTargetValue": 48280.32,
    }]

    data = await _call(app, "get_non_completed_badge_challenges")

    badge = data["challenges"][0]
    assert badge["target"] == "48.28 km"
    assert badge["target_distance_miles"] == 30.0
    assert badge["progress_distance_miles"] == 10.0


@pytest.mark.asyncio
async def test_virtual_challenge_gets_miles(app, mock_garmin_client):
    _units(mock_garmin_client)
    mock_garmin_client.get_inprogress_virtual_challenges.return_value = [{
        "uuid": "v", "badgeChallengeName": "Camino", "badgeUnitId": 1,
        "badgeProgressValue": 16093.44, "badgeTargetValue": 804672.0,
    }]

    data = await _call(app, "get_inprogress_virtual_challenges")

    challenge = data["challenges"][0]
    assert challenge["progress_miles"] == 10.0
    assert challenge["target_miles"] == 500.0


@pytest.mark.asyncio
async def test_calendar_race_distance_in_miles(app, mock_garmin_client):
    _units(mock_garmin_client)
    mock_garmin_client.get_scheduled_workouts.return_value = _month(_race("2026-11-26"))

    data = await _call(app, "get_calendar_events", {"start_date": "2026-11-01", "end_date": "2026-11-30"})

    assert data["events"][0]["distance_miles"] == 26.22


@pytest.mark.asyncio
async def test_workout_list_estimated_distance_in_miles(app, mock_garmin_client):
    _units(mock_garmin_client)
    mock_garmin_client.get_workouts.return_value = [{
        "workoutId": 1, "workoutName": "Easy", "sportType": {"sportTypeKey": "running"},
        "estimatedDistanceInMeters": 3621.0,
    }]

    data = await _call(app, "get_workouts")

    assert data["workouts"][0]["estimated_distance_miles"] == 2.25


# --- analytics -----------------------------------------------------------------------

def test_baseline_hours_get_hm_text():
    rows = [{"date": f"2026-10-{day:02d}", "sleep_hours": 6.37} for day in range(1, 15)]

    payload = analytics._baseline_payload(rows, baseline_window=7)

    sleep = payload["metrics"]["sleep_hours"]
    assert sleep["latest"] == 6.37
    assert sleep["latest_hm"] == "6:22"
    assert sleep["baseline_avg_hm"] == "6:22"
    assert "latest_hm" not in payload["metrics"]["steps"]


def test_report_rows_get_hm_text():
    rows = [{"date": "2026-10-26", "sleep_hours": 6.0}, {"date": "2026-10-27", "sleep_hours": 7.5}]
    definition = {"metrics": ["sleep_hours"], "group_by": "week", "aggregation": "sum", "days": 2}

    report = analytics._custom_report(rows, definition, include_daily_rows=True)

    assert report["rows"][0]["sleep_hours"] == 13.5
    assert report["rows"][0]["sleep_hm"] == "13:30"
    assert [r["sleep_hm"] for r in report["daily_rows"]] == ["6:00", "7:30"]


@pytest.mark.asyncio
async def test_skin_temperature_range_keys_say_deviation(app, mock_garmin_client):
    mock_garmin_client.connectapi.return_value = [{
        "calendarDate": "2026-10-28",
        "metrics": [{"type": "SKIN_TEMP_C", "value": 0.1}, {"type": "SKIN_TEMP_F", "value": 0.2}],
    }]

    data = await _call(app, "get_health_status_range", {"start_date": "2026-10-28", "end_date": "2026-10-28"})

    day = data[0] if isinstance(data, list) else data["days"][0]
    assert day["skin_temperature_deviation_celsius"]["value"] == 0.1
    assert day["skin_temperature_deviation_fahrenheit"]["value"] == 0.2


@pytest.mark.asyncio
async def test_exercise_set_pounds_are_for_a_us_account_only(app, mock_garmin_client):
    _units(mock_garmin_client, "metric")
    mock_garmin_client.get_activity_exercise_sets.return_value = {"exerciseSets": [
        {"setType": "ACTIVE", "weight": 20411.65},
    ]}

    data = await _call(app, "get_activity_exercise_sets", {"activity_id": 1})

    assert data["exerciseSets"][0]["weightKg"] == 20.41
    assert "weightLb" not in data["exerciseSets"][0]


@pytest.mark.asyncio
async def test_scheduled_workout_estimated_distance_in_miles(app, mock_garmin_client):
    _units(mock_garmin_client)
    mock_garmin_client.query_garmin_graphql.return_value = {"data": {"workoutScheduleSummariesScalar": [
        {"scheduleDate": "2026-10-29", "workoutId": 1, "workoutName": "Easy",
         "workoutType": "running", "estimatedDistanceInMeters": 3621.0},
    ]}}

    data = await _call(app, "get_scheduled_workouts", {"start_date": "2026-10-29", "end_date": "2026-10-29"})

    assert data["scheduled_workouts"][0]["estimated_distance_miles"] == 2.25


@pytest.mark.asyncio
async def test_energy_balance_weight_change_in_pounds(app, mock_garmin_client):
    _units(mock_garmin_client)
    intake = {"dailyNutritionSummaries": [
        {"mealDate": "2024-01-02", "mealDetails": [{"loggedFoods": [{}] * 5}],
         "dailyNutritionContent": {"calories": 2000}},
    ]}
    calories = {"values": [{"calendarDate": "2024-01-02", "values": {"totalCalories": 2500}}]}
    mock_garmin_client.connectapi.side_effect = [intake, calories]
    mock_garmin_client.get_body_composition.return_value = {"dateWeightList": [
        {"calendarDate": "2024-01-01", "weight": 80000.0, "bodyFat": 20.0},
        {"calendarDate": "2024-01-25", "weight": 79000.0, "bodyFat": 18.75},
    ]}

    data = await _call(app, "get_energy_balance", {"start_date": "2024-01-01", "end_date": "2024-01-25"})

    composition = data["body_composition"]
    assert composition["weight_change_kg"] == -1.0
    assert composition["weight_change_lb"] == -2.2
