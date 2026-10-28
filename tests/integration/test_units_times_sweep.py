"""Every read-only tool, once, for a US account, against the contract.

FORK: the 2026-10-28 audit found metric fields without account-unit
companions and times without zones across dozens of tools. This sweep calls
each read-only remote tool with a Garmin stand-in returning payloads shaped
like the live ones (synthetic values, the audited keys), and the autouse
contract check in tests/conftest.py inspects every result. A tool that
starts emitting a bare metric field or an unlabelled time fails here even if
no other test calls it. The values are synthetic; no athlete data is stored.
"""
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import remote
from garmin_mcp.client_resolver import set_global_client
from tests.fixtures.garmin_responses import (
    MOCK_CYCLING_FTP,
    MOCK_DEVICE_SETTINGS,
    MOCK_DEVICE_SOLAR_DATA,
    MOCK_DEVICES,
    MOCK_RHR_DAY,
    MOCK_TRAINING_STATUS,
    MOCK_WEEKLY_INTENSITY_MINUTES,
    MOCK_WEEKLY_STRESS,
)
from tests.units_times_contract import result_json

D, D2 = "2026-10-27", "2026-10-28"
GMT, LOCAL = "2026-10-27T18:37:26.0", "2026-10-27T14:37:26.0"
EPOCH_GMT, EPOCH_LOCAL = 1793126246000, 1793111846000  # same moment; local pseudo-epoch
RUN = {
    "activityId": 1, "activityName": "Run", "activityType": {"typeKey": "running"},
    "startTimeLocal": "2026-10-27 14:37:26", "startTimeGMT": "2026-10-27 18:37:26",
    "distance": 5000.0, "duration": 1800.0, "averageSpeed": 2.78, "elevationGain": 40.0,
    "elevationLoss": 35.0, "beginTimestamp": EPOCH_GMT, "recoveryTime": 1440,
}
SPLIT = {"startTimeGMT": GMT, "startTimeLocal": LOCAL, "distance": 1609.34, "duration": 600.0,
         "averageSpeed": 2.68, "maxSpeed": 3.1, "elevationGain": 5.0, "averageTemperature": 22.0,
         "strideLength": 75.0, "verticalOscillation": 8.0, "type": "RWD_RUN"}
READINESS = {"calendarDate": D, "timestamp": "2026-10-27T19:01:16.0",
             "timestampLocal": "2026-10-27T15:01:16.0", "score": 40, "recoveryTime": 600,
             "inputContext": "AFTER_POST_EXERCISE_RESET", "level": "LOW"}
DAY_SERIES = {"calendarDate": D, "startTimestampGMT": "2026-10-27T04:00:00.0",
              "startTimestampLocal": "2026-10-27T00:00:00.0", "values": [[EPOCH_GMT, 20]]}
SLEEP = {"dailySleepDTO": {"calendarDate": D2, "sleepTimeSeconds": 28800,
                           "sleepStartTimestampGMT": 1793157433000, "sleepEndTimestampGMT": 1793186233000,
                           "sleepStartTimestampLocal": 1793143033000, "sleepEndTimestampLocal": 1793171833000,
                           "sleepScores": {"overall": {"value": 80}}}}
WEIGH_IN = {"calendarDate": D, "weight": 72000.0, "boneMass": 3000.0, "muscleMass": 33000.0,
            "date": EPOCH_LOCAL, "timestampGMT": EPOCH_GMT}


def _connectapi(url, params=None, headers=None, **_):
    url = str(url)
    if "healthstatus" in url:
        day = {"calendarDate": D, "metrics": [{"type": "SKIN_TEMP_C", "value": 0.1},
                                              {"type": "SKIN_TEMP_F", "value": 0.2}]}
        dates = url.split("summary/", 1)[1].strip("/").split("/")
        return [day] if len(dates) == 2 else day
    if "usersummary-service/stats/daily" in url:
        return {"values": [{"calendarDate": D, "values": {"totalCalories": 2500, "totalSteps": 9000}}]}
    if "device-service/sensors" in url:
        return [{"deviceName": "HRM", "serialNumber": 1, "batteryLevel": 80}]
    if url.startswith("/course-service/course/"):
        return {"courseId": 7, "courseName": "Loop", "distanceInMeters": 5000.0,
                "elevationGainInMeters": 30.0, "coursePoints": [{"name": "Water", "distance": 1609.0}]}
    if url == "/course-service/course":
        return [{"courseId": 7, "courseName": "Loop", "distanceInMeters": 5000.0,
                 "elevationGainInMeters": 30.0, "activityType": {"typeKey": "running"}}]
    if "atp-api" in url:
        return []
    if "nutrition-service/settings" in url:
        return {"startingWeight": 72000, "targetWeightGoal": 70000, "weightChangeRate": 227}
    if "nutrition-service" in url:
        return {}
    if "activitylist-service" in url or url == "ACTIVITIES":
        return [RUN]
    if "goal" in url:
        return []
    return {}


def _garmin_double():
    client = Mock()
    client.get_unit_system.return_value = "statute_us"
    client.get_userprofile_settings.return_value = {
        "timeZone": "America/New_York", "measurementSystem": "statute_us",
        "hydrationMeasurementUnit": "ounce",
    }
    client.connectapi.side_effect = _connectapi
    client.garmin_connect_activities = "ACTIVITIES"
    client.garmin_connect_goals_url = "/goal-service/goal/goals"
    client.client.connectapi.side_effect = _connectapi
    client.query_garmin_graphql.return_value = {"data": {"workoutScheduleSummariesScalar": [
        {"scheduleDate": D, "workoutId": 1, "workoutName": "Easy", "workoutType": "running",
         "estimatedDistanceInMeters": 3600.0},
    ]}}
    client.count_activities.return_value = 10
    client.get_activities.return_value = [RUN]
    client.get_activities_fordate.return_value = {"ActivitiesForDay": {"payload": [RUN]}}
    client.get_activity.return_value = {
        "activityId": 1, "activityTypeDTO": {"typeKey": "running"},
        "summaryDTO": {"startTimeLocal": LOCAL, "startTimeGMT": GMT, "distance": 5000.0,
                       "averageSpeed": 2.78, "averageMovingSpeed": 2.8, "maxSpeed": 3.5,
                       "elevationGain": 40.0, "strideLength": 80.0, "verticalOscillation": 8.0},
    }
    client.get_activity_splits.return_value = {"activityId": 1, "lapDTOs": [SPLIT]}
    client.get_activity_typed_splits.return_value = {"activityId": 1, "splits": [SPLIT]}
    client.get_activity_split_summaries.return_value = {"activityId": 1, "splitSummaries": [SPLIT]}
    client.get_activity_weather.return_value = {"temp": 63, "windSpeed": 8,
                                                "issueDate": "2026-10-27T17:51:00.000+00:00"}
    client.get_activity_gear.return_value = [{"uuid": "g", "maximumMeters": 800000.0}]
    client.get_activity_exercise_sets.return_value = {"exerciseSets": [{"weight": 20000.0}]}
    client.get_activity_hr_in_timezones.return_value = [{"zoneNumber": 1, "secsInZone": 60}]
    client.get_activity_power_in_timezones.return_value = [{"zoneNumber": 1, "secsInZone": 60}]
    client.get_activity_types.return_value = [{"typeKey": "running", "typeId": 1}]
    client.get_gear_activities.return_value = [RUN]
    client.get_device_last_used.return_value = {"userDeviceId": 1, "userProfileNumber": 1,
                                                "lastUsedDeviceUploadTime": EPOCH_GMT}
    client.get_gear.return_value = [{"uuid": "g", "displayName": "Shoe", "gearStatusName": "active",
                                     "maximumMeters": 800000.0}]
    client.get_gear_defaults.return_value = []
    client.get_gear_stats.return_value = {"totalActivities": 3, "totalDistance": 15000.0}
    client.get_body_battery.return_value = [{"date": D, "bodyBatteryActivityEvent": [
        {"eventType": "ACTIVITY", "eventStartTimeGmt": GMT, "timezoneOffset": -14400000,
         "durationInMilliseconds": 1800000}]}]
    client.get_body_battery_events.return_value = [{"event": {
        "eventType": "SLEEP", "eventStartTimeGmt": GMT, "timezoneOffset": -14400000}}]
    client.get_sleep_data.return_value = SLEEP
    client.get_stress_data.return_value = dict(DAY_SERIES)
    client.get_all_day_stress.return_value = dict(DAY_SERIES)
    client.get_heart_rates.return_value = {**DAY_SERIES, "heartRateValues": [[EPOCH_GMT, 60]],
                                           "restingHeartRate": 55}
    client.get_respiration_data.return_value = dict(DAY_SERIES)
    client.get_floors.return_value = dict(DAY_SERIES)
    client.get_steps_data.return_value = [{"startGMT": GMT, "endGMT": "2026-10-27T18:52:26.0",
                                           "steps": 300}]
    client.get_spo2_data.return_value = {"calendarDate": D, "latestSpO2": 97,
                                         "latestSpO2TimestampGMT": GMT,
                                         "latestSpO2TimestampLocal": LOCAL,
                                         "startTimestampGMT": "2026-10-27T04:00:00.0",
                                         "startTimestampLocal": "2026-10-27T00:00:00.0",
                                         "spO2HourlyAverages": [[EPOCH_GMT, 96]]}
    client.get_training_readiness.return_value = [READINESS]
    client.get_morning_training_readiness.return_value = READINESS
    client.get_stats.return_value = {"totalDistanceMeters": 8000.0, "totalSteps": 9000}
    client.get_user_summary.return_value = {"totalDistanceMeters": 8000.0,
                                            "floorsAscendedInMeters": 30.0}
    client.get_stats_and_body.return_value = {"totalDistanceMeters": 8000.0, "weight": 72000.0}
    client.get_daily_steps.return_value = [{"calendarDate": D, "totalSteps": 9000,
                                            "totalDistance": 8000.0}]
    client.get_weekly_steps.return_value = [{"calendarDate": D, "values": {
        "totalSteps": 60000, "totalDistance": 50000.0, "averageDistance": 7000.0}}]
    client.get_body_composition.return_value = {"dateWeightList": [WEIGH_IN],
                                                "totalAverage": {"weight": 72000.0}}
    client.get_weigh_ins.return_value = {"dailyWeightSummaries": [{"allWeightMetrics": [WEIGH_IN]}],
                                         "totalAverage": {"weight": 72000.0}}
    client.get_daily_weigh_ins.return_value = {"dateWeightList": [WEIGH_IN]}
    client.get_hydration_data.return_value = {"valueInML": 2000.0, "goalInML": 2500.0}
    client.get_hrv_data.return_value = {
        "hrvSummary": {"calendarDate": D2, "lastNightAvg": 50},
        "sleepStartTimestampGMT": "2026-10-28T03:17:13.0",
        "sleepStartTimestampLocal": "2026-10-27T23:17:13.0",
        "hrvReadings": [{"hrvValue": 50, "readingTimeGMT": "2026-10-28T03:20:00.0",
                         "readingTimeLocal": "2026-10-27T23:20:00.0"}],
    }
    client.get_lactate_threshold.return_value = {
        "speed_and_heart_rate": {"speed": 0.3, "heartRate": 170}, "power": {"weight": 72.0}}
    client.get_running_tolerance.return_value = [{
        "calendarDate": D, "acuteTolerance": 20000.0, "acuteImpactLoad": 9000.0,
        "acuteDistance": 8000.0, "tolerance": 20000.0, "totalImpactLoad": 9000.0,
        "totalDistance": 8000.0}]
    client.get_progress_summary_between_dates.return_value = [{"stats": {"running": {
        "distance": {"count": 1, "sum": 500000.0, "avg": 500000.0, "min": 500000.0, "max": 500000.0}}}}]
    client.get_max_metrics.return_value = [{"heatAltitudeAcclimation": {
        "calendarDate": D, "altitudeAcclimation": 1000, "currentAltitude": 20}}]
    client.get_user_profile.return_value = {"userData": {"weight": 72000.0, "height": 175.0,
                                                         "lactateThresholdSpeed": 0.3}}
    client.get_personal_record.return_value = [
        {"typeId": 7, "value": 21097.5, "prStartTimeLocalFormatted": "2026-10-24T10:00:00.0"}]
    client.get_workouts.return_value = [{"workoutId": 1, "workoutName": "Easy",
                                         "sportType": {"sportTypeKey": "running"},
                                         "estimatedDistanceInMeters": 3600.0}]
    client.get_workout_by_id.return_value = {
        "workoutId": 1, "workoutName": "Tempo", "sportType": {"sportTypeKey": "running"},
        "workoutSegments": [{"segmentOrder": 1, "sportType": {"sportTypeKey": "running"},
                             "workoutSteps": [{"type": "ExecutableStepDTO", "stepOrder": 1,
                                               "endCondition": {"conditionTypeKey": "distance"},
                                               "endConditionValue": 1609.344,
                                               "targetType": {"workoutTargetTypeKey": "pace.zone"},
                                               "targetValueOne": 2.8, "targetValueTwo": 2.6}]}]}
    for method in ("get_available_badge_challenges", "get_badge_challenges",
                   "get_non_completed_badge_challenges", "get_inprogress_virtual_challenges"):
        getattr(client, method).return_value = [{
            "uuid": "b", "badgeChallengeName": "30 mi", "badgeUnitId": 1,
            "badgeProgressValue": 16093.44, "badgeTargetValue": 48280.32}]
    client.get_adhoc_challenges.return_value = []
    client.get_earned_badges.return_value = []
    client.get_goals.return_value = []
    # Payload shapes from tests/fixtures (synthetic values).
    client.get_devices.return_value = MOCK_DEVICES
    client.get_device_settings.return_value = MOCK_DEVICE_SETTINGS
    client.get_device_solar_data.return_value = MOCK_DEVICE_SOLAR_DATA
    client.get_primary_training_device.return_value = MOCK_DEVICES[0]
    client.get_device_alarms.return_value = [{"alarmId": 1, "time": "07:00", "enabled": True}]
    client.get_rhr_day.return_value = MOCK_RHR_DAY
    client.get_weekly_stress.return_value = MOCK_WEEKLY_STRESS
    client.get_weekly_intensity_minutes.return_value = MOCK_WEEKLY_INTENSITY_MINUTES
    client.get_cycling_ftp.return_value = MOCK_CYCLING_FTP
    client.get_training_status.return_value = MOCK_TRAINING_STATUS
    client.get_race_predictions.return_value = {
        "time5K": 1860, "time10K": 3900, "timeHalfMarathon": 8700, "timeMarathon": 18600}
    client.get_fitnessage_data.return_value = {
        "fitnessAge": 40, "chronologicalAge": 45, "lastUpdated": "2026-10-27T00:00:00.0"}
    client.get_all_day_events.return_value = [{
        "calendarDate": D, "startTimestampGMT": GMT, "startTimestampLocal": LOCAL,
        "duration": 23}]
    client.get_blood_pressure.return_value = {"measurementSummaries": []}
    client.get_lifestyle_logging_data.return_value = {"calendarDate": D, "dailyLogsReport": []}
    client.get_full_name.return_value = "Runner"
    return client


# Tool -> arguments. Every read-only remote tool is listed (checked below).
ARGS = {
    "count_activities": {}, "get_activities": {"limit": 1},
    "get_activities_by_date": {"start_date": D, "end_date": D},
    "get_activities_fordate": {"date": D}, "get_activity": {"activity_id": 1},
    "get_activity_splits": {"activity_id": 1}, "get_activity_typed_splits": {"activity_id": 1},
    "get_activity_split_summaries": {"activity_id": 1}, "get_activity_weather": {"activity_id": 1},
    "get_activity_gear": {"activity_id": 1}, "get_activity_exercise_sets": {"activity_id": 1},
    "get_activity_hr_in_timezones": {"activity_id": 1},
    "get_activity_power_in_timezones": {"activity_id": 1}, "get_activity_types": {},
    "get_activity_fit_data": {"activity_id": 1}, "get_activity_fit_messages": {"activity_id": 1},
    "get_power_duration_curve": {},
    "get_gear": {}, "get_gear_activities": {"gear_uuid": "g"},
    "get_courses": {}, "get_course_details": {"course_id": 7},
    "get_calendar_events": {"start_date": D, "end_date": D},
    "get_body_battery": {"start_date": D, "end_date": D}, "get_body_battery_events": {"date": D},
    "get_sleep_data": {"date": D2}, "get_sleep_summary": {"date": D2},
    "get_sleep_summary_range": {"start_date": D2, "end_date": D2},
    "get_stress_data": {"date": D}, "get_stress_summary": {"date": D},
    "get_all_day_stress": {"date": D}, "get_weekly_stress": {"end_date": D},
    "get_heart_rates": {"date": D}, "get_heart_rates_summary": {"date": D}, "get_rhr_day": {"date": D},
    "get_respiration_data": {"date": D}, "get_respiration_summary": {"date": D},
    "get_spo2_data": {"date": D}, "get_floors": {"date": D}, "get_steps_data": {"date": D},
    "get_daily_steps": {"start_date": D, "end_date": D}, "get_weekly_steps": {"end_date": D},
    "get_weekly_intensity_minutes": {"end_date": D},
    "get_stats": {"date": D}, "get_user_summary": {"date": D}, "get_stats_and_body": {"date": D},
    "get_stats_range": {"start_date": D, "end_date": D},
    "get_body_composition": {"start_date": D, "end_date": D},
    "get_blood_pressure": {"start_date": D, "end_date": D},
    "get_hydration_data": {"date": D}, "get_energy_balance": {"start_date": D, "end_date": D},
    "get_lifestyle_logging_data": {"date": D}, "get_all_day_events": {"date": D},
    "get_health_status": {"date": D}, "get_health_status_range": {"start_date": D, "end_date": D},
    "get_training_readiness": {"date": D}, "get_morning_training_readiness": {"date": D},
    "get_recovery_time_remaining": {"date": D},
    "get_weigh_ins": {"start_date": D, "end_date": D}, "get_daily_weigh_ins": {"date": D},
    "get_hrv_data": {"date": D2, "return_timeseries": True}, "get_hrv_trend": {"start_date": D, "end_date": D},
    "get_respiration_trend": {"start_date": D, "end_date": D},
    "get_lactate_threshold": {}, "get_cycling_ftp": {},
    "get_running_tolerance": {"date": D}, "get_running_tolerance_trend": {"start_date": D, "end_date": D},
    "get_progress_summary_between_dates": {"start_date": D, "end_date": D, "metric": "distance"},
    "get_acclimation": {"date": D}, "get_endurance_score": {"start_date": D, "end_date": D},
    "get_hill_score": {"start_date": D, "end_date": D}, "get_fitnessage_data": {"date": D},
    "get_training_effect": {"activity_id": 1}, "get_training_status": {"date": D},
    "get_training_load_balance": {"date": D}, "get_training_load_trend": {"start_date": D, "end_date": D},
    "get_vo2max_trend": {"start_date": D, "end_date": D},
    "get_user_profile": {}, "get_userprofile_settings": {}, "get_unit_system": {},
    "get_full_name": {}, "get_heart_rate_zones": {},
    "get_devices": {}, "get_device_last_used": {}, "get_device_settings": {},
    "get_device_alarms": {}, "get_device_solar_data": {"device_id": 1, "date": D},
    "get_primary_training_device": {}, "get_sensors": {},
    "get_personal_record": {}, "get_race_predictions": {}, "get_goals": {},
    "get_earned_badges": {}, "get_adhoc_challenges": {}, "get_available_badge_challenges": {},
    "get_badge_challenges": {}, "get_non_completed_badge_challenges": {},
    "get_inprogress_virtual_challenges": {},
    "get_workouts": {}, "get_workout_by_id": {"workout_id": 1},
    "get_scheduled_workouts": {"start_date": D, "end_date": D},
    "get_garmin_coach_workouts": {"calendar_date": D}, "get_training_plan_workouts": {"calendar_date": D},
    "get_coach_plans": {}, "get_coach_plan_details": {"plan_id": 1},
    "get_coach_plan_progress": {"plan_id": 1, "start_date": D, "end_date": D},
    "get_nutrition_daily_food_log": {"date": D}, "get_nutrition_daily_meals": {"date": D},
    "get_nutrition_daily_settings": {"date": D},
    "get_nutrition_summary_between_dates": {"start_date": D, "end_date": D},
    "get_custom_foods": {}, "get_custom_food_serving_units": {}, "search_foods": {"query": "apple"},
    "get_menstrual_calendar_data": {"start_date": D, "end_date": D},
    "get_menstrual_data_for_date": {"date": D}, "get_pregnancy_summary": {},
    "get_health_baselines": {"end_date": D, "days": 14},
    "get_wellness_anomalies": {"end_date": D, "days": 14},
    "get_lagged_health_correlations": {"end_date": D, "days": 14},
    "get_weekly_health_review": {"end_date": D}, "run_custom_health_report": {"end_date": D, "days": 7},
    "list_health_report_metrics": {}, "list_saved_health_reports": {},
}

# Tools whose sweep payload carries the audited unit and time fields: these
# must come back as JSON, so the contract check cannot pass by default.
MUST_RETURN_JSON = {
    "get_activities", "get_activities_by_date", "get_activities_fordate", "get_activity",
    "get_activity_splits", "get_activity_typed_splits", "get_activity_split_summaries",
    "get_activity_weather", "get_activity_gear", "get_activity_exercise_sets", "get_gear",
    "get_gear_activities", "get_courses", "get_course_details", "get_body_battery",
    "get_body_battery_events", "get_sleep_data", "get_sleep_summary", "get_stress_data",
    "get_all_day_stress", "get_heart_rates", "get_respiration_data", "get_spo2_data",
    "get_floors", "get_steps_data", "get_daily_steps", "get_weekly_steps", "get_stats",
    "get_user_summary", "get_stats_and_body", "get_stats_range", "get_body_composition",
    "get_hydration_data", "get_health_status", "get_health_status_range",
    "get_training_readiness", "get_morning_training_readiness", "get_recovery_time_remaining",
    "get_weigh_ins", "get_daily_weigh_ins", "get_hrv_data", "get_lactate_threshold",
    "get_running_tolerance", "get_running_tolerance_trend", "get_progress_summary_between_dates",
    "get_acclimation", "get_user_profile", "get_device_last_used", "get_sensors",
    "get_personal_record", "get_available_badge_challenges", "get_badge_challenges",
    "get_non_completed_badge_challenges", "get_inprogress_virtual_challenges",
    "get_workouts", "get_workout_by_id", "get_scheduled_workouts", "get_nutrition_daily_settings",
    "get_devices", "get_device_settings", "get_primary_training_device", "get_device_alarms",
    "get_rhr_day", "get_weekly_stress", "get_weekly_intensity_minutes", "get_cycling_ftp",
    "get_race_predictions", "get_fitnessage_data", "get_all_day_events", "get_full_name",
    "get_sleep_summary_range",
}


@pytest.fixture
def mock_garmin_client():
    """The Garmin stand-in; named so the autouse contract check sees it."""
    return _garmin_double()


@pytest.fixture
def app(mock_garmin_client):
    set_global_client(mock_garmin_client)
    for module in remote._CONFIGURED_MODULES:
        module.configure(mock_garmin_client)
    app = FastMCP("Sweep")
    remote.register_remote_tools(app)
    return app


async def _read_only_tools(app):
    tools = await app.list_tools()
    return sorted(t.name for t in tools if t.annotations and t.annotations.readOnlyHint)


@pytest.mark.asyncio
async def test_every_read_only_tool_is_in_the_sweep(app):
    """A new read-only tool must be added to ARGS, so the sweep covers it."""
    assert sorted(ARGS) == await _read_only_tools(app)


@pytest.mark.asyncio
async def test_every_read_only_tool_meets_the_units_and_times_contract(app):
    """The autouse check fails the call on any breach; this collects which
    tools returned JSON so a payload gap cannot hide a tool."""
    returned_json = set()
    for name in await _read_only_tools(app):
        result = await app.call_tool(name, ARGS[name])
        if result_json(result) is not None:
            returned_json.add(name)
    assert MUST_RETURN_JSON <= returned_json, sorted(MUST_RETURN_JSON - returned_json)
