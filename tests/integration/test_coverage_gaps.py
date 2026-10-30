"""Fields the curated tools left out, found by the 2026-10-30 field-coverage run.

scripts/field_coverage.py compared each tool's output with the reply Garmin
sent it. Each test here is one gap it found: a value Garmin sends that the
tool's output lacked, so a reader would conclude it did not exist. Field
names are Garmin's, from that run; the values are synthetic.
"""
import datetime
import json

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import activity_management, health_wellness
from garmin_mcp.client_resolver import set_global_client
from garmin_mcp.health_wellness import _offset_from_minutes

D = "2026-10-29"


@pytest.fixture
def app(mock_garmin_client):
    set_global_client(mock_garmin_client)
    mock_garmin_client.get_unit_system.return_value = "statute_us"
    mock_garmin_client.get_userprofile_settings.return_value = {"timeZone": "America/New_York"}
    app = FastMCP("Coverage gaps")
    for module in (activity_management, health_wellness):
        module.configure(mock_garmin_client)
        app = module.register_tools(app)
    yield app
    set_global_client(None)


async def _call(app, tool, arguments):
    result = await app.call_tool(tool, arguments)
    return json.loads(result[0][0].text)


# --- sleep ------------------------------------------------------------------

NIGHT = {
    "dailySleepDTO": {
        "calendarDate": D, "sleepTimeSeconds": 27000,
        # 03:17:13 GMT, 11:17:13 PM the evening before in New York.
        "sleepStartTimestampGMT": 1793157433000, "sleepStartTimestampLocal": 1793143033000,
        "avgHeartRate": 52.0, "averageRespirationValue": 14.0,
        "lowestRespirationValue": 10.0, "highestRespirationValue": 18.0,
        "averageSpO2Value": 95, "lowestSpO2Value": 88, "highestSpO2Value": 99,
        "breathingDisruptionSeverity": "LOW",
        "sleepScoreFeedback": "POSITIVE_LONG_AND_DEEP", "sleepScoreInsight": "NONE",
        "sleepScores": {
            "overall": {"value": 81, "qualifierKey": "GOOD"},
            "totalDuration": {"qualifierKey": "GOOD", "optimalStart": 28800.0, "optimalEnd": 28800.0},
            "stress": {"qualifierKey": "FAIR", "optimalStart": 0.0, "optimalEnd": 15.0},
            "deepPercentage": {"value": 20, "qualifierKey": "GOOD",
                               "optimalStart": 16.0, "optimalEnd": 33.0},
        },
        "sleepNeed": {"actual": 490, "baseline": 480, "feedback": "INCREASED"},
        "nextSleepNeed": {"actual": 470, "baseline": 480, "feedback": "DECREASED"},
        "dailyNapDTOS": [
            {"napStartTimestampGMT": "2026-10-29T17:58:51", "napEndTimestampGMT": "2026-10-29T18:28:51",
             "napStartTimeOffset": -240, "napEndTimeOffset": -240, "napFeedback": "IDEAL_TIMING"},
            {"napStartTimestampGMT": "2026-10-29T21:00:00", "napTimeSec": 1200},
        ],
    },
    "bodyBatteryChange": 49, "hrvStatus": "BALANCED",
    "avgSkinTempDeviationC": -0.3, "avgSkinTempDeviationF": -0.5,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, arguments", [
    ("get_sleep_summary", {"date": D}),
    ("get_sleep_summary_range", {"start_date": D, "end_date": D}),
])
async def test_sleep_summary_gives_sleep_need_and_the_nights_vitals(app, mock_garmin_client, tool, arguments):
    mock_garmin_client.get_sleep_data.return_value = NIGHT

    night = await _call(app, tool, arguments)
    night = night["nights"][0] if isinstance(night, dict) and "nights" in night else night

    assert (night["sleep_need_minutes"], night["sleep_need_baseline_minutes"]) == (490, 480)
    assert night["sleep_need_feedback"] == "INCREASED"
    assert night["next_sleep_need_minutes"] == 470
    assert night["body_battery_change"] == 49
    assert night["avg_sleep_heart_rate_bpm"] == 52.0
    assert (night["avg_respiration_brpm"], night["lowest_respiration_brpm"],
            night["highest_respiration_brpm"]) == (14.0, 10.0, 18.0)
    assert night["highest_spo2_percent"] == 99
    assert night["breathing_disruption_severity"] == "LOW"
    assert night["hrv_status"] == "BALANCED"
    assert night["skin_temperature_deviation_celsius"] == -0.3
    assert night["skin_temperature_deviation_fahrenheit"] == -0.5  # Garmin's, not recomputed
    assert night["sleep_score_feedback"] == "POSITIVE_LONG_AND_DEEP"
    assert (night["duration_rating"], night["stress_rating"], night["deep_sleep_rating"]) == \
        ("GOOD", "FAIR", "GOOD")
    assert night["deep_sleep_target_range_percent"] == [16.0, 33.0]
    # Only the stages' targets are shares of the night.
    assert "duration_target_range_percent" not in night
    assert "stress_target_range_percent" not in night


@pytest.mark.asyncio
async def test_sleep_summary_falls_back_to_the_nights_own_spo2(app, mock_garmin_client):
    """With no wellnessSpO2SleepSummaryDTO, dailySleepDTO's own SpO2 counts."""
    mock_garmin_client.get_sleep_data.return_value = NIGHT

    night = await _call(app, "get_sleep_summary", {"date": D})

    assert (night["avg_spo2_percent"], night["lowest_spo2_percent"]) == (95, 88)


@pytest.mark.asyncio
async def test_sleep_summary_lists_naps_with_their_own_local_times(app, mock_garmin_client):
    mock_garmin_client.get_sleep_data.return_value = NIGHT

    naps = (await _call(app, "get_sleep_summary", {"date": D}))["naps"]

    assert naps[0] == {
        "start_gmt": "2026-10-29T17:58:51Z", "start_local": "2026-10-29T13:58:51-04:00",
        "end_gmt": "2026-10-29T18:28:51Z", "end_local": "2026-10-29T14:28:51-04:00",
        "duration_minutes": 30, "feedback": "IDEAL_TIMING",
    }
    # No offset of its own: the night's (-04:00); length from napTimeSec.
    assert naps[1]["start_local"] == "2026-10-29T17:00:00-04:00"
    assert naps[1]["duration_minutes"] == 20


@pytest.mark.parametrize("value, hours", [(-240, -4), (330, 5.5), (-4, -4), (-14400, -4), (-14400000, -4)])
def test_a_nap_offset_is_read_in_whichever_unit_its_size_says(value, hours):
    assert _offset_from_minutes(value) == datetime.timedelta(hours=hours)


@pytest.mark.parametrize("value", [None, True, "−240"])
def test_a_nap_offset_that_is_not_a_number_is_none(value):
    assert _offset_from_minutes(value) is None


# --- daily stats ------------------------------------------------------------

DAY = {
    "calendarDate": D,
    "wellnessStartTimeGmt": "2026-10-29T04:00:00.0", "wellnessStartTimeLocal": "2026-10-29T00:00:00.0",
    "bodyBatteryMostRecentValue": 62, "bodyBatteryAtWakeTime": 89, "bodyBatteryDuringSleep": 49,
    "bodyBatteryDynamicFeedbackEvent": {"bodyBatteryLevel": "MODERATE",
                                        "feedbackLongType": "DAY_RECOVERING_AND_INACTIVE"},
    "restStressDuration": 25260, "restStressPercentage": 35.1,
    "lowStressDuration": 15000, "lowStressPercentage": 20.8,
    "mediumStressDuration": 6000, "mediumStressPercentage": 8.3,
    "highStressDuration": 1200, "highStressPercentage": 1.7,
    "activityStressDuration": 3600, "activityStressPercentage": 5.0,
    "latestSpo2": 96, "latestSpo2ReadingTimeGmt": "2026-10-29T10:59:00.0",
    "latestSpo2ReadingTimeLocal": "2026-10-29T06:59:00.0",
    "latestRespirationValue": 15.0, "latestRespirationTimeGMT": "2026-10-29T13:00:00.0",
    "minAvgHeartRate": 47, "maxAvgHeartRate": 131,
    "floorsAscendedInMeters": 30.48, "floorsDescendedInMeters": 27.0,
    "remainingKilocalories": 412.0, "netCalorieGoal": 2200,
}


@pytest.mark.asyncio
async def test_daily_stats_give_body_battery_at_wake_and_stress_by_level(app, mock_garmin_client):
    mock_garmin_client.get_stats.return_value = DAY

    day = await _call(app, "get_stats", {"date": D})

    assert (day["body_battery_at_wake"], day["body_battery_during_sleep"]) == (89, 49)
    assert day["body_battery_band"] == "MODERATE"
    assert day["body_battery_feedback"] == "DAY_RECOVERING_AND_INACTIVE"
    assert (day["stress_rest_seconds"], day["stress_rest_percent"]) == (25260, 35.1)
    assert (day["stress_high_seconds"], day["stress_activity_percent"]) == (1200, 5.0)
    assert (day["min_avg_heart_rate_bpm"], day["max_avg_heart_rate_bpm"]) == (47, 131)
    assert day["remaining_calories"] == 412.0
    assert day["net_calorie_goal"] == 2200


@pytest.mark.asyncio
async def test_daily_stats_give_the_latest_readings_and_when_they_were_taken(app, mock_garmin_client):
    mock_garmin_client.get_stats.return_value = DAY

    day = await _call(app, "get_stats", {"date": D})

    assert day["latest_spo2_percent"] == 96
    assert day["latest_spo2_time_local"] == "2026-10-29T06:59:00-04:00"
    assert day["latest_respiration_brpm"] == 15.0
    assert day["latest_respiration_time_gmt"] == "2026-10-29T13:00:00Z"
    assert day["latest_respiration_time_local"] == "2026-10-29T09:00:00-04:00"  # the day's offset


@pytest.mark.asyncio
async def test_daily_floors_as_height_get_feet_for_a_us_account(app, mock_garmin_client):
    mock_garmin_client.get_stats.return_value = DAY

    day = await _call(app, "get_stats", {"date": D})

    assert day["floors_ascended_meters"] == 30.48
    assert day["floors_ascended_feet"] == 100
    assert day["floors_descended_feet"] == 89


# --- readiness and body battery ----------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["get_training_readiness", "get_morning_training_readiness"])
async def test_readiness_gives_garmins_full_feedback(app, mock_garmin_client, tool):
    snapshot = {"calendarDate": D, "score": 71, "feedbackShort": "WELL_RECOVERED",
                "feedbackLong": "WELL_RECOVERED_RESTED", "validSleep": True,
                "inputContext": "AFTER_WAKEUP_RESET",
                "recoveryTimeChangePhrase": "REACHED_ZERO"}
    mock_garmin_client.get_training_readiness.return_value = [snapshot]
    mock_garmin_client.get_morning_training_readiness.return_value = snapshot

    data = await _call(app, tool, {"date": D})
    entry = data[0] if isinstance(data, list) else data

    assert entry["feedback_long"] == "WELL_RECOVERED_RESTED"
    assert entry["valid_sleep"] is True
    # FORK.md invariant: recovery comes from recoveryTime, never the phrase.
    assert "REACHED_ZERO" not in json.dumps(data)


@pytest.mark.asyncio
async def test_body_battery_gives_garmins_end_of_day_verdict(app, mock_garmin_client):
    mock_garmin_client.get_body_battery.return_value = [{
        "date": D, "charged": 51, "drained": 40,
        "startTimestampGMT": "2026-10-29T04:00:00.0", "startTimestampLocal": "2026-10-29T00:00:00.0",
        "endOfDayBodyBatteryDynamicFeedbackEvent": {
            "eventTimestampGmt": "2026-10-30T03:59:00.0", "bodyBatteryLevel": "MODERATE",
            "feedbackShortType": "BALANCED_DAY", "feedbackLongType": "BALANCED_DAY_WITH_RECOVERY"},
    }]

    day = (await _call(app, "get_body_battery", {"start_date": D, "end_date": D}))[0]

    assert day["end_of_day_level_band"] == "MODERATE"
    assert day["end_of_day_feedback"] == "BALANCED_DAY"
    assert day["end_of_day_feedback_long"] == "BALANCED_DAY_WITH_RECOVERY"
    assert day["end_of_day_feedback_time_local"] == "2026-10-29T23:59:00-04:00"


# --- activities ---------------------------------------------------------------

ACTIVITY = {
    "activityId": 1, "activityName": "Morning Run", "locationName": "Brooklyn",
    "activityTypeDTO": {"typeKey": "running"},
    "timeZoneUnitDTO": {"unitKey": "America/New_York", "timeZone": "America/New_York"},
    "metadataDTO": {"personalRecord": True, "lapCount": 5},
    "summaryDTO": {
        "startTimeGMT": "2026-10-29T11:00:00.0", "startTimeLocal": "2026-10-29T07:00:00.0",
        "distance": 8046.72, "duration": 2700.0, "averageSpeed": 2.98,
        "aerobicTrainingEffectMessage": "IMPROVING_AEROBIC_BASE_8",
        "anaerobicTrainingEffectMessage": "NO_ANAEROBIC_BENEFIT_0",
        "impactLoad": 41.2,
        "beginPotentialStamina": 100.0, "endPotentialStamina": 72.0, "minAvailableStamina": 68.0,
        "avgRespirationRate": 32.5, "minRespirationRate": 18.0, "maxRespirationRate": 44.0,
        "waterEstimated": 591.47,
        "verticalRatio": 8.1, "groundContactBalanceLeft": 49.6, "stepSpeedLossPercent": 4.2,
        "avgGradeAdjustedSpeed": 3.05, "minPower": 120,
        "averageTemperature": 12.0, "minTemperature": 10.0, "maxTemperature": 15.0,
    },
}


@pytest.mark.asyncio
async def test_activity_gives_stamina_breathing_sweat_and_where_it_was(app, mock_garmin_client):
    mock_garmin_client.get_activity.return_value = ACTIVITY

    run = await _call(app, "get_activity", {"activity_id": 1})

    assert (run["stamina_start_percent"], run["stamina_end_percent"],
            run["stamina_min_available_percent"]) == (100.0, 72.0, 68.0)
    assert (run["avg_respiration_brpm"], run["min_respiration_brpm"],
            run["max_respiration_brpm"]) == (32.5, 18.0, 44.0)
    assert run["sweat_loss_ml"] == 591.47
    assert run["sweat_loss_fl_oz"] == 20.0
    assert run["impact_load"] == 41.2
    assert run["training_effect_message"] == "IMPROVING_AEROBIC_BASE_8"
    assert run["anaerobic_training_effect_message"] == "NO_ANAEROBIC_BENEFIT_0"
    assert (run["avg_vertical_ratio_percent"], run["avg_ground_contact_balance_left_percent"],
            run["step_speed_loss_percent"]) == (8.1, 49.6, 4.2)
    assert run["min_power_watts"] == 120
    assert run["location_name"] == "Brooklyn"
    assert run["time_zone"] == "America/New_York"
    assert run["personal_record"] is True


@pytest.mark.asyncio
async def test_activity_temperature_and_grade_adjusted_pace_get_us_companions(app, mock_garmin_client):
    mock_garmin_client.get_activity.return_value = ACTIVITY

    run = await _call(app, "get_activity", {"activity_id": 1})

    assert (run["min_temperature_c"], run["max_temperature_c"]) == (10.0, 15.0)
    assert (run["min_temperature_f"], run["max_temperature_f"]) == (50.0, 59.0)
    assert run["avg_temperature_f"] == 53.6
    assert run["avg_grade_adjusted_speed_mps"] == 3.05
    assert run["avg_grade_adjusted_pace_per_mile"] == "8:48"  # 1609.344 / 3.05 = 527.7 s


@pytest.mark.asyncio
async def test_activity_sweat_loss_stays_in_ml_for_a_metric_account(app, mock_garmin_client):
    mock_garmin_client.get_unit_system.return_value = "metric"
    mock_garmin_client.get_activity.return_value = ACTIVITY

    run = await _call(app, "get_activity", {"activity_id": 1})

    assert run["sweat_loss_ml"] == 591.47
    assert "sweat_loss_fl_oz" not in run


LISTED = {
    "activityId": 1, "activityName": "Morning Run", "activityType": {"typeKey": "running"},
    "startTimeGMT": "2026-10-29 11:00:00", "startTimeLocal": "2026-10-29 07:00:00",
    "distance": 8046.72, "duration": 2700.0, "averageSpeed": 2.98,
    "aerobicTrainingEffect": 3.4, "anaerobicTrainingEffect": 0.8,
    "trainingEffectLabel": "AEROBIC_BASE", "activityTrainingLoad": 112.5,
    "differenceBodyBattery": -14, "vO2MaxValue": 51.0,
    "moderateIntensityMinutes": 12, "vigorousIntensityMinutes": 30,
    "avgRespirationRate": 32.5, "averageRunningCadenceInStepsPerMinute": 168.0, "avgPower": 255.0,
    "lapCount": 5, "locationName": "Brooklyn", "pr": True,
    "hrTimeInZone_1": 60.0, "hrTimeInZone_2": 600.0, "hrTimeInZone_3": 1500.0,
    "hrTimeInZone_4": 480.0, "hrTimeInZone_5": 60.0,
    "fastestSplit_1000": 318.2, "fastestSplit_1609": 520.4, "fastestSplit_5000": 1650.9,
}


def _assert_list_details(activity):
    assert [activity[f"hr_zone_{n}_seconds"] for n in range(1, 6)] == [60.0, 600.0, 1500.0, 480.0, 60.0]
    assert (activity["fastest_km_seconds"], activity["fastest_mile_seconds"],
            activity["fastest_5k_seconds"]) == (318.2, 520.4, 1650.9)
    assert (activity["aerobic_training_effect"], activity["anaerobic_training_effect"]) == (3.4, 0.8)
    assert activity["training_effect_label"] == "AEROBIC_BASE"
    assert activity["training_load"] == 112.5
    assert activity["body_battery_impact"] == -14
    assert activity["vo2_max"] == 51.0
    assert activity["avg_respiration_brpm"] == 32.5
    assert activity["location_name"] == "Brooklyn"
    assert activity["personal_record"] is True


@pytest.mark.asyncio
async def test_activity_list_gives_zones_fastest_splits_and_training_effect(app, mock_garmin_client):
    mock_garmin_client.get_activities.return_value = [LISTED]

    data = await _call(app, "get_activities", {"start": 0, "limit": 1})

    _assert_list_details(data["activities"][0])


@pytest.mark.asyncio
async def test_activities_by_date_give_the_same_details(app, mock_garmin_client):
    mock_garmin_client.connectapi.return_value = [LISTED]

    data = await _call(app, "get_activities_by_date", {"start_date": D, "end_date": D})

    _assert_list_details(data["activities"][0])


@pytest.mark.asyncio
async def test_activities_for_a_date_give_the_same_details(app, mock_garmin_client):
    mock_garmin_client.get_activities_fordate.return_value = {"ActivitiesForDay": {"payload": [LISTED]}}

    activity = (await _call(app, "get_activities_fordate", {"date": D}))["activities"][0]

    _assert_list_details(activity)


@pytest.mark.asyncio
async def test_laps_give_running_dynamics_power_breathing_and_temperature(app, mock_garmin_client):
    mock_garmin_client.get_activity_splits.return_value = {"activityId": 1, "lapDTOs": [{
        "lapIndex": 1, "startTimeGMT": "2026-10-29T11:00:00.0", "distance": 1609.34,
        "duration": 540.0, "averageSpeed": 2.98,
        "maxRunCadence": 182, "maxPower": 390, "minPower": 140, "normalizedPower": 262,
        "groundContactTime": 248.0, "groundContactBalanceLeft": 49.7, "strideLength": 107.0,
        "verticalOscillation": 8.6, "verticalRatio": 8.0, "stepSpeedLossPercent": 4.1,
        "avgRespirationRate": 31.0, "maxRespirationRate": 40.0,
        "averageTemperature": 12.0, "minTemperature": 11.0, "maxTemperature": 13.0,
        "maxElevation": 30.0, "minElevation": 12.0,
    }]}

    lap = (await _call(app, "get_activity_splits", {"activity_id": 1}))["laps"][0]

    assert (lap["max_cadence"], lap["max_power_watts"], lap["min_power_watts"],
            lap["normalized_power_watts"]) == (182, 390, 140, 262)
    assert (lap["avg_ground_contact_time_ms"], lap["avg_ground_contact_balance_left_percent"]) == (248.0, 49.7)
    assert (lap["avg_stride_length_cm"], lap["avg_stride_length_in"]) == (107.0, 42.1)
    assert (lap["avg_vertical_oscillation_cm"], lap["avg_vertical_ratio_percent"]) == (8.6, 8.0)
    assert lap["step_speed_loss_percent"] == 4.1
    assert (lap["avg_respiration_brpm"], lap["max_respiration_brpm"]) == (31.0, 40.0)
    assert (lap["min_temperature_c"], lap["max_temperature_f"]) == (11.0, 55.4)
    assert (lap["max_elevation_feet"], lap["min_elevation_feet"]) == (98, 39)
