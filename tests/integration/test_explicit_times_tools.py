"""Tools name every time's zone: *_gmt, and *_local with its UTC offset.

Driven through the real MCP tool calls with a mocked Garmin client. Values
are the athlete's own from 2026-10-27/28 in New York (UTC-4): the run started
18:37:26 GMT = 14:37:26 local; the night began 03:17:13 GMT = 23:17:13 the
evening before.
"""
import datetime
import json
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import (
    activity_management,
    analytics,
    devices,
    gear_management,
    health_wellness,
    training,
    weight_management,
)
from garmin_mcp.client_resolver import set_global_client

MODULES = (
    activity_management, analytics, devices, gear_management, health_wellness,
    training, weight_management,
)


@pytest.fixture
def app(mock_garmin_client):
    set_global_client(mock_garmin_client)
    app = FastMCP("Test explicit times")
    for module in MODULES:
        module.configure(mock_garmin_client)
        app = module.register_tools(app)
    return app


def _zone(client, zone="America/New_York"):
    client.get_userprofile_settings.return_value = {"timeZone": zone}


async def _call(app, tool, arguments=None):
    result = await app.call_tool(tool, arguments or {})
    text = result[0][0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pytest.fail(f"{tool} returned text, not JSON: {text}")


# --- body battery -----------------------------------------------------------

@pytest.mark.asyncio
async def test_body_battery_event_times_are_gmt_and_the_events_own_local(app, mock_garmin_client):
    """The event's GMT start was shown as a bare "start_time": 18:37 for a
    run at 2:37 PM."""
    mock_garmin_client.get_body_battery.return_value = [{
        "date": "2026-10-27",
        "bodyBatteryActivityEvent": [
            {"eventType": "ACTIVITY", "eventStartTimeGmt": "2026-10-27T18:37:26.0",
             "timezoneOffset": -14400000, "durationInMilliseconds": 1401000},
        ],
    }]

    data = await _call(app, "get_body_battery", {"start_date": "2026-10-27", "end_date": "2026-10-27"})

    event = data[0]["events"][0]
    assert event["start_time_gmt"] == "2026-10-27T18:37:26Z"
    assert event["start_time_local"] == "2026-10-27T14:37:26-04:00"
    assert "start_time" not in event
    assert "local_time_zone" not in event
    mock_garmin_client.get_userprofile_settings.assert_not_called()


@pytest.mark.asyncio
async def test_body_battery_event_without_an_offset_uses_the_profile_zone(app, mock_garmin_client):
    _zone(mock_garmin_client)
    mock_garmin_client.get_body_battery.return_value = [{
        "date": "2026-10-27",
        "bodyBatteryActivityEvent": [
            {"eventType": "ACTIVITY", "eventStartTimeGmt": "2026-10-27T18:37:26.0"},
        ],
    }]

    data = await _call(app, "get_body_battery", {"start_date": "2026-10-27", "end_date": "2026-10-27"})

    event = data[0]["events"][0]
    assert event["start_time_local"] == "2026-10-27T14:37:26-04:00"
    assert event["local_time_zone"] == "America/New_York"


@pytest.mark.asyncio
async def test_body_battery_events_payload_gets_local_start_and_offset(app, mock_garmin_client):
    mock_garmin_client.get_body_battery_events.return_value = [{
        "event": {"eventType": "SLEEP", "eventStartTimeGmt": "2026-10-28T03:17:13.0",
                  "timezoneOffset": -14400000},
        "stressValuesArray": [[1793157300000, 6]],
    }]

    data = await _call(app, "get_body_battery_events", {"date": "2026-10-28"})

    event = data[0]["event"]
    # Garmin's key, its text given its zone in place.
    assert event["eventStartTimeGmt"] == "2026-10-28T03:17:13Z"
    assert event["eventStartTimeLocal"] == "2026-10-27T23:17:13-04:00"
    assert event["utcOffsetHours"] == -4.0
    assert data[0]["stressValuesArray"] == [[1793157300000, 6]]


# --- sleep, HRV, SpO2 ---------------------------------------------------------

SLEEP = {"dailySleepDTO": {
    "calendarDate": "2026-10-28",
    "sleepTimeSeconds": 29100,
    "sleepStartTimestampGMT": 1793157433000,
    "sleepEndTimestampGMT": 1793186533000,
    "sleepStartTimestampLocal": 1793143033000,
    "sleepEndTimestampLocal": 1793172133000,
}}


@pytest.mark.asyncio
async def test_sleep_summary_window_is_gmt_and_local(app, mock_garmin_client):
    """sleep_start was a bare GMT epoch; the HRV tool used the same name for
    local text."""
    mock_garmin_client.get_sleep_data.return_value = SLEEP

    data = await _call(app, "get_sleep_summary", {"date": "2026-10-28"})

    assert data["sleep_start_gmt"] == "2026-10-28T03:17:13Z"
    assert data["sleep_start_local"] == "2026-10-27T23:17:13-04:00"
    assert data["sleep_end_local"] == "2026-10-28T07:22:13-04:00"
    assert "sleep_start" not in data and "sleep_end" not in data


@pytest.mark.asyncio
async def test_sleep_data_payload_gets_iso_times_and_offset(app, mock_garmin_client):
    """Garmin's *Local epochs are the wall clock written as UTC: decoded as a
    real epoch, 23:17 became 19:17."""
    mock_garmin_client.get_sleep_data.return_value = json.loads(json.dumps(SLEEP))

    data = await _call(app, "get_sleep_data", {"date": "2026-10-28"})

    daily = data["dailySleepDTO"]
    assert daily["sleepStartTimestampLocal"] == 1793143033000  # untouched
    assert daily["sleepStartGmt"] == "2026-10-28T03:17:13Z"
    assert daily["sleepStartLocal"] == "2026-10-27T23:17:13-04:00"
    assert daily["sleepEndLocal"] == "2026-10-28T07:22:13-04:00"
    assert data["utcOffsetHours"] == -4.0


@pytest.mark.asyncio
async def test_hrv_sleep_window_and_readings_are_gmt_and_local(app, mock_garmin_client):
    mock_garmin_client.get_hrv_data.return_value = {
        "hrvSummary": {"calendarDate": "2026-10-28", "lastNightAvg": 59},
        "sleepStartTimestampGMT": "2026-10-28T03:17:13.0",
        "sleepEndTimestampGMT": "2026-10-28T11:22:13.0",
        "sleepStartTimestampLocal": "2026-10-27T23:17:13.0",
        "sleepEndTimestampLocal": "2026-10-28T07:22:13.0",
        "hrvReadings": [{"hrvValue": 61, "readingTimeGMT": "2026-10-28T03:20:51.0",
                         "readingTimeLocal": "2026-10-27T23:20:51.0"}],
    }

    data = await _call(app, "get_hrv_data", {"date": "2026-10-28", "return_timeseries": True})

    assert data["sleep_start_gmt"] == "2026-10-28T03:17:13Z"
    assert data["sleep_start_local"] == "2026-10-27T23:17:13-04:00"
    assert "sleep_start" not in data
    reading = data["hrv_readings"][0]
    assert reading == {
        "time_gmt": "2026-10-28T03:20:51Z",
        "time_local": "2026-10-27T23:20:51-04:00",
        "hrv_ms": 61,
    }


@pytest.mark.asyncio
async def test_spo2_latest_reading_and_hourly_rows_are_labelled(app, mock_garmin_client):
    mock_garmin_client.get_spo2_data.return_value = {
        "calendarDate": "2026-10-28",
        "startTimestampGMT": "2026-10-28T04:00:00.0",
        "startTimestampLocal": "2026-10-28T00:00:00.0",
        "latestSpO2": 97,
        "latestSpO2TimestampGMT": "2026-10-28T09:59:00.0",
        "latestSpO2TimestampLocal": "2026-10-28T05:59:00.0",
        "spO2HourlyAverages": [[1793160000000, 96]],
    }

    data = await _call(app, "get_spo2_data", {"date": "2026-10-28"})

    assert data["latest_reading_time_gmt"] == "2026-10-28T09:59:00Z"
    assert data["latest_reading_time_local"] == "2026-10-28T05:59:00-04:00"
    assert "latest_reading_time" not in data
    assert data["hourly_averages"] == [{
        "hour_start_gmt": "2026-10-28T04:00:00Z",
        "hour_start_local": "2026-10-28T00:00:00-04:00",
        "avg_spo2_percent": 96,
    }]


@pytest.mark.asyncio
async def test_spo2_hourly_rows_fall_back_to_the_profile_zone(app, mock_garmin_client):
    _zone(mock_garmin_client)
    mock_garmin_client.get_spo2_data.return_value = {
        "calendarDate": "2026-10-28", "spO2HourlyAverages": [[1793160000000, 96]],
    }

    data = await _call(app, "get_spo2_data", {"date": "2026-10-28"})

    assert data["hourly_averages"][0]["hour_start_local"] == "2026-10-28T00:00:00-04:00"
    assert data["local_time_zone"] == "America/New_York"


@pytest.mark.asyncio
async def test_training_readiness_timestamp_is_gmt_and_local(app, mock_garmin_client):
    mock_garmin_client.get_training_readiness.return_value = [{
        "calendarDate": "2026-10-27", "timestamp": "2026-10-27T19:01:16.0",
        "timestampLocal": "2026-10-27T15:01:16.0", "score": 20,
    }]

    data = await _call(app, "get_training_readiness", {"date": "2026-10-27"})

    assert data[0]["timestamp_gmt"] == "2026-10-27T19:01:16Z"
    assert data[0]["timestamp_local"] == "2026-10-27T15:01:16-04:00"
    assert "timestamp" not in data[0]


# --- day series and steps -------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool, method",
    [("get_stress_data", "get_stress_data"), ("get_all_day_stress", "get_all_day_stress"),
     ("get_heart_rates", "get_heart_rates"), ("get_respiration_data", "get_respiration_data"),
     ("get_floors", "get_floors")],
)
async def test_day_series_get_the_days_own_offset(app, mock_garmin_client, tool, method):
    getattr(mock_garmin_client, method).return_value = {
        "calendarDate": "2026-10-27",
        "startTimestampGMT": "2026-10-27T04:00:00.0",
        "startTimestampLocal": "2026-10-27T00:00:00.0",
        "values": [[1793073600000, 14]],
    }

    data = await _call(app, tool, {"date": "2026-10-27"})

    assert data["utcOffsetHours"] == -4.0
    assert data["values"] == [[1793073600000, 14]]


@pytest.mark.asyncio
async def test_step_buckets_get_local_times_from_the_profile_zone(app, mock_garmin_client):
    _zone(mock_garmin_client)
    mock_garmin_client.get_steps_data.return_value = [
        {"startGMT": "2026-10-27T18:45:00.0", "endGMT": "2026-10-27T19:00:00.0", "steps": 1200},
    ]

    data = await _call(app, "get_steps_data", {"date": "2026-10-27"})

    assert data[0]["startLocal"] == "2026-10-27T14:45:00-04:00"
    assert data[0]["endLocal"] == "2026-10-27T15:00:00-04:00"
    assert data[0]["localTimeZone"] == "America/New_York"
    assert data[0]["startGMT"] == "2026-10-27T18:45:00Z"


@pytest.mark.asyncio
async def test_body_composition_weigh_ins_get_iso_times(app, mock_garmin_client):
    """`date` is a local pseudo-epoch: decoded as a real one it read 04:20
    for an 8:20 AM weigh-in."""
    mock_garmin_client.get_body_composition.return_value = {"dateWeightList": [
        {"calendarDate": "2026-10-27", "date": 1793089231000, "timestampGMT": 1793103631000,
         "weight": 79900.0},
    ]}

    data = await _call(app, "get_body_composition", {"start_date": "2026-10-27", "end_date": "2026-10-27"})

    entry = data["dateWeightList"][0]
    assert entry["measuredAtGmt"] == "2026-10-27T12:20:31Z"
    assert entry["measuredAtLocal"] == "2026-10-27T08:20:31-04:00"
    assert entry["date"] == 1793089231000


# --- weigh-ins ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_weigh_in_times_are_iso_gmt_and_local(app, mock_garmin_client):
    weigh_in = {"calendarDate": "2026-10-27", "weight": 79900.0, "date": 1793089231000,
                "timestampGMT": 1793103631000}
    mock_garmin_client.get_weigh_ins.return_value = {
        "dailyWeightSummaries": [{"allWeightMetrics": [weigh_in]}]
    }
    mock_garmin_client.get_daily_weigh_ins.return_value = {"dateWeightList": [weigh_in]}

    ranged = await _call(app, "get_weigh_ins", {"start_date": "2026-10-27", "end_date": "2026-10-27"})
    daily = await _call(app, "get_daily_weigh_ins", {"date": "2026-10-27"})

    for data in (ranged, daily):
        measurement = data["measurements"][0]
        assert measurement["timestamp_gmt"] == "2026-10-27T12:20:31Z"
        assert measurement["timestamp_local"] == "2026-10-27T08:20:31-04:00"


@pytest.mark.asyncio
async def test_add_weigh_in_now_uses_the_profile_zone_not_the_server_clock(app, mock_garmin_client):
    """With no timestamps the "local" one was the server's clock -- UTC on
    Railway -- so an evening weigh-in was recorded on the next day."""
    _zone(mock_garmin_client, "Asia/Kolkata")  # +5:30, no daylight saving
    mock_garmin_client.add_weigh_in_with_timestamps.return_value = {}

    await _call(app, "add_weigh_in_with_timestamps", {"weight": 80.0, "unit_key": "kg"})

    kwargs = mock_garmin_client.add_weigh_in_with_timestamps.call_args.kwargs
    local = datetime.datetime.fromisoformat(kwargs["dateTimestamp"])
    utc = datetime.datetime.fromisoformat(kwargs["gmtTimestamp"])
    assert local - utc == datetime.timedelta(hours=5, minutes=30)


# --- activities -----------------------------------------------------------------

LISTED_RUN = {
    "activityId": 24873123388, "activityName": "Hills", "activityType": {"typeKey": "running"},
    "startTimeLocal": "2026-10-27 14:37:26", "startTimeGMT": "2026-10-27 18:37:26",
    "distance": 3494.78, "duration": 1401.156,
}


@pytest.mark.asyncio
async def test_activity_lists_name_the_start_times_zone(app, mock_garmin_client):
    """Lists used start_time for local time and the FIT tool for UTC."""
    mock_garmin_client.connectapi.return_value = [LISTED_RUN]
    mock_garmin_client.get_activities.return_value = [LISTED_RUN]
    mock_garmin_client.get_gear_activities.return_value = [LISTED_RUN]

    by_date = await _call(app, "get_activities_by_date",
                          {"start_date": "2026-10-27", "end_date": "2026-10-27"})
    paged = await _call(app, "get_activities", {"limit": 1})
    gear = await _call(app, "get_gear_activities", {"gear_uuid": "abc"})

    for activity in (by_date["activities"][0], paged["activities"][0], gear["activities"][0]):
        assert activity["start_time_gmt"] == "2026-10-27T18:37:26Z"
        assert activity["start_time_local"] == "2026-10-27T14:37:26-04:00"
        assert "start_time" not in activity


@pytest.mark.asyncio
async def test_get_activity_times_carry_their_zone(app, mock_garmin_client):
    mock_garmin_client.get_activity.return_value = {
        "activityId": 1, "activityTypeDTO": {"typeKey": "running"},
        "summaryDTO": {"startTimeLocal": "2026-10-27T14:37:26.0",
                       "startTimeGMT": "2026-10-27T18:37:26.0"},
    }

    data = await _call(app, "get_activity", {"activity_id": 1})

    assert data["start_time_gmt"] == "2026-10-27T18:37:26Z"
    assert data["start_time_local"] == "2026-10-27T14:37:26-04:00"


@pytest.mark.asyncio
async def test_lap_times_get_the_profile_zone_and_say_so(app, mock_garmin_client):
    _zone(mock_garmin_client)
    mock_garmin_client.get_activity_splits.return_value = {
        "activityId": 1,
        "lapDTOs": [{"lapIndex": 1, "startTimeGMT": "2026-10-27T18:37:26.0", "distance": 1609.34}],
    }

    data = await _call(app, "get_activity_splits", {"activity_id": 1})

    assert data["laps"][0]["start_time_gmt"] == "2026-10-27T18:37:26Z"
    assert data["laps"][0]["start_time_local"] == "2026-10-27T14:37:26-04:00"
    assert data["local_time_zone"] == "America/New_York"


@pytest.mark.asyncio
async def test_lap_times_without_a_profile_zone_stay_gmt_only(app, mock_garmin_client):
    mock_garmin_client.get_activity_splits.return_value = {
        "activityId": 1,
        "lapDTOs": [{"lapIndex": 1, "startTimeGMT": "2026-10-27T18:37:26.0"}],
    }

    data = await _call(app, "get_activity_splits", {"activity_id": 1})

    assert "start_time_local" not in data["laps"][0]
    assert "local_time_zone" not in data


@pytest.mark.asyncio
async def test_weather_issue_time_is_gmt_and_profile_local(app, mock_garmin_client):
    _zone(mock_garmin_client)
    mock_garmin_client.get_activity_weather.return_value = {
        "temp": 63, "issueDate": "2026-10-27T17:51:00.000+00:00",
    }

    data = await _call(app, "get_activity_weather", {"activity_id": 1})

    assert data["issue_time_gmt"] == "2026-10-27T17:51:00Z"
    assert data["issue_time_local"] == "2026-10-27T13:51:00-04:00"
    assert data["local_time_zone"] == "America/New_York"
    assert "issue_time" not in data


# --- device and analytics -------------------------------------------------------

@pytest.mark.asyncio
async def test_device_upload_time_is_not_the_server_clock(app, mock_garmin_client):
    """It was formatted in the server's own zone, UTC on Railway, with no
    label."""
    _zone(mock_garmin_client)
    mock_garmin_client.get_device_last_used.return_value = {
        "userDeviceId": 1, "lastUsedDeviceUploadTime": 1793222087000,
    }

    data = await _call(app, "get_device_last_used")

    assert data["last_upload_time_gmt"] == "2026-10-28T21:14:47Z"
    assert data["last_upload_time_local"] == "2026-10-28T17:14:47-04:00"
    assert data["local_time_zone"] == "America/New_York"
    assert "last_upload_time" not in data


def test_analytics_default_end_date_is_the_athletes_today():
    """It was the server's date: UTC on Railway, tomorrow from 8 PM in New
    York. A zone 14 hours ahead of UTC shows the difference at any hour."""
    client = Mock()
    client.get_userprofile_settings.return_value = {"timeZone": "Pacific/Kiritimati"}
    client.get_activities_by_date.return_value = []
    client.get_stats.return_value = {}
    client.get_sleep_data.return_value = {}
    client.get_training_readiness.return_value = []

    analytics._daily_rows(client, end_date=None, days=1)

    today = datetime.datetime.now(ZoneInfo("Pacific/Kiritimati")).date().isoformat()
    client.get_stats.assert_called_once_with(today)


# --- the athlete's "today" ------------------------------------------------------
# Zones 14 h ahead of and 11 h behind UTC are 25 hours apart, so their dates
# always differ: the server's own date can match at most one of them.

def _today_in(zone):
    return datetime.datetime.now(ZoneInfo(zone)).date()


@pytest.mark.asyncio
@pytest.mark.parametrize("zone", ["Pacific/Kiritimati", "Pacific/Pago_Pago"])
async def test_stats_range_marks_the_athletes_today_partial(app, mock_garmin_client, zone):
    _zone(mock_garmin_client, zone)
    today = _today_in(zone)
    days = [today - datetime.timedelta(days=1), today]
    values = {"values": [{"calendarDate": d.isoformat(), "values": {"totalCalories": 2500,
                                                                     "totalSteps": 9000}}
                         for d in days]}
    mock_garmin_client.connectapi.side_effect = [values, values]

    data = await _call(app, "get_stats_range",
                       {"start_date": days[0].isoformat(), "end_date": days[1].isoformat()})

    assert [d["is_partial"] for d in data["days"]] == [False, True]


@pytest.mark.asyncio
@pytest.mark.parametrize("zone", ["Pacific/Kiritimati", "Pacific/Pago_Pago"])
async def test_energy_balance_leaves_out_the_athletes_today(app, mock_garmin_client, zone):
    _zone(mock_garmin_client, zone)
    today = _today_in(zone)
    yesterday = today - datetime.timedelta(days=1)
    intake = {"dailyNutritionSummaries": [
        {"mealDate": d.isoformat(), "mealDetails": [{"loggedFoods": [{}] * 4}],
         "dailyNutritionContent": {"calories": 2000}}
        for d in (yesterday, today)
    ]}
    calories = {"values": [
        {"calendarDate": yesterday.isoformat(), "values": {"totalCalories": 2400}},
        {"calendarDate": today.isoformat(), "values": {"totalCalories": 2600}},
    ]}
    mock_garmin_client.connectapi.side_effect = [intake, calories]
    mock_garmin_client.get_body_composition.return_value = {"dateWeightList": []}

    data = await _call(app, "get_energy_balance",
                       {"start_date": yesterday.isoformat(), "end_date": today.isoformat()})

    # Today is still running, so only yesterday's 2400 counts.
    assert data["expenditure_garmin"]["mean_total_calories_per_day"] == 2400.0


def test_recovery_fallback_uses_the_athletes_today():
    """At 8:20 PM in New York (00:20 UTC on the 28th) the 27th is still
    today: the activity-based estimate must be offered for it."""
    from garmin_mcp.health_wellness import _collect_recovery_time

    now = datetime.datetime(2026, 10, 28, 0, 20, tzinfo=datetime.timezone.utc)
    client = Mock()
    client.get_userprofile_settings.return_value = {"timeZone": "America/New_York"}
    client.get_training_readiness.return_value = []
    client.get_activities.return_value = [{
        "activityId": 1, "recoveryTime": 4320, "duration": 1401.0,
        "beginTimestamp": datetime.datetime(
            2026, 10, 27, 18, 37, 26, tzinfo=datetime.timezone.utc).timestamp() * 1000,
    }]

    data = _collect_recovery_time(client, "2026-10-27", now=now)

    assert data["source"] == "recent_activity"
    assert data["value_type"] == "estimate"


# --- the daily summary payload ---------------------------------------------------

DAILY = {
    "calendarDate": "2026-10-27",
    "wellnessStartTimeGmt": "2026-10-27T04:00:00.0", "wellnessStartTimeLocal": "2026-10-27T00:00:00.0",
    "lastSyncTimestampGMT": "2026-10-28T21:18:19.470",
    "bodyBatteryDynamicFeedbackEvent": {"eventTimestampGmt": "2026-10-28T00:20:46"},
    "bodyBatteryActivityEventList": [{
        "eventType": "SLEEP", "eventStartTimeGmt": "2026-10-28T03:17:13", "timezoneOffset": -14400000}],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("tool, method", [("get_user_summary", "get_user_summary"),
                                          ("get_stats_and_body", "get_stats_and_body")])
async def test_daily_summary_gmt_times_get_local_times(app, mock_garmin_client, tool, method):
    """The summary's body battery list printed sleep at 3:17 (GMT) with no
    local time beside it: 11:17 PM the evening before."""
    getattr(mock_garmin_client, method).return_value = json.loads(json.dumps(DAILY))

    data = await _call(app, tool, {"date": "2026-10-27"})

    event = data["bodyBatteryActivityEventList"][0]
    assert event["eventStartTimeGmt"] == "2026-10-28T03:17:13Z"  # Garmin's key, zoned
    assert event["eventStartTimeLocal"] == "2026-10-27T23:17:13-04:00"
    assert data["lastSyncTimestampLocal"] == "2026-10-28T17:18:19-04:00"
    assert data["bodyBatteryDynamicFeedbackEvent"]["eventTimestampLocal"] == "2026-10-27T20:20:46-04:00"
    assert data["utcOffsetHours"] == -4.0
    # Garmin's own local field gains the offset from its GMT partner.
    assert data["wellnessStartTimeLocal"] == "2026-10-27T00:00:00-04:00"
    assert data["wellnessStartTimeGmt"] == "2026-10-27T04:00:00Z"


@pytest.mark.asyncio
async def test_split_end_times_get_the_splits_own_local_time(app, mock_garmin_client):
    mock_garmin_client.get_activity_typed_splits.return_value = {"splits": [{
        "startTimeGMT": "2026-10-27T18:37:26.0", "startTimeLocal": "2026-10-27T14:37:26.0",
        "endTimeGMT": "2026-10-27T18:46:02.0", "type": "RWD_RUN"}]}

    data = await _call(app, "get_activity_typed_splits", {"activity_id": 1})

    split = data["splits"][0]
    assert split["endTimeLocal"] == "2026-10-27T14:46:02-04:00"
    assert split["startTimeLocal"] == "2026-10-27T14:37:26-04:00"  # Garmin's, zoned
    assert split["endTimeGMT"] == "2026-10-27T18:46:02Z"



@pytest.mark.asyncio
async def test_body_battery_raw_returns_garmins_report_unchanged(app, mock_garmin_client):
    report = [{"date": "2026-10-30", "charged": 51, "drained": 1,
               "bodyBatteryValuesArray": [[1793160000000, 40], [1793195000000, 90]],
               "bodyBatteryActivityEvent": [{"eventStartTimeGmt": "2026-10-30T04:10:55.0"}]}]
    mock_garmin_client.get_body_battery.return_value = json.loads(json.dumps(report))

    data = await _call(app, "get_body_battery",
                       {"start_date": "2026-10-30", "end_date": "2026-10-30", "raw": True})

    assert data == report


# The shape of Garmin's daily report, seen live with raw=True on 2026-10-30;
# the values here are synthetic.
BATTERY_DESCRIPTORS = [
    {"bodyBatteryValueDescriptorIndex": 0, "bodyBatteryValueDescriptorKey": "timestamp"},
    {"bodyBatteryValueDescriptorIndex": 1, "bodyBatteryValueDescriptorKey": "bodyBatteryLevel"},
]


def _battery_day(**overrides):
    day = {
        "date": "2026-10-30", "charged": 44, "drained": 2,
        "startTimestampGMT": "2026-10-30T04:00:00.0", "endTimestampGMT": "2026-10-30T13:10:00.0",
        "startTimestampLocal": "2026-10-30T00:00:00.0", "endTimestampLocal": "2026-10-30T09:10:00.0",
        "bodyBatteryValuesArray": [[1793332800000, 35], [1793343600000, 58],
                                   [1793354400000, 84], [1793365200000, 77]],
        "bodyBatteryValueDescriptorDTOList": BATTERY_DESCRIPTORS,
        "bodyBatteryDynamicFeedbackEvent": {
            "eventTimestampGmt": "2026-10-30T12:40:00.0", "bodyBatteryLevel": "HIGH",
            "feedbackShortType": None, "feedbackLongType": "EARLY_MORNING_HIGH_GOOD_SLEEP"},
        "bodyBatteryActivityEvent": [],
    }
    day.update(overrides)
    return day


@pytest.mark.asyncio
async def test_body_battery_gives_the_level_as_a_number_not_only_garmins_band(app, mock_garmin_client):
    """The summary kept only the feedback band ("HIGH", as body_battery_level),
    so a reader could not say the level; the number is in Garmin's series."""
    mock_garmin_client.get_body_battery.return_value = [_battery_day()]

    day = (await _call(app, "get_body_battery", {"start_date": "2026-10-30", "end_date": "2026-10-30"}))[0]

    assert day["latest_level"] == 77
    assert day["latest_level_time_gmt"] == "2026-10-30T13:00:00Z"
    assert day["latest_level_time_local"] == "2026-10-30T09:00:00-04:00"
    assert (day["highest_level"], day["lowest_level"]) == (84, 35)
    assert day["level_band"] == "HIGH"
    assert "body_battery_level" not in day
    assert day["current_feedback"] is None
    assert day["current_feedback_long"] == "EARLY_MORNING_HIGH_GOOD_SLEEP"
    assert day["feedback_time_local"] == "2026-10-30T08:40:00-04:00"
    assert day["start_time_local"] == "2026-10-30T00:00:00-04:00"
    assert day["end_time_gmt"] == "2026-10-30T13:10:00Z"
    assert day["end_time_local"] == "2026-10-30T09:10:00-04:00"
    assert "local_time_zone" not in day
    mock_garmin_client.get_userprofile_settings.assert_not_called()


@pytest.mark.asyncio
async def test_body_battery_latest_level_is_the_latest_reading_not_the_last_row(app, mock_garmin_client):
    mock_garmin_client.get_body_battery.return_value = [_battery_day(
        bodyBatteryValuesArray=[[1793365200000, 77], [1793332800000, 35],
                                [1793354400000, None], "junk", [1793343600000]],
    )]

    day = (await _call(app, "get_body_battery", {"start_date": "2026-10-30", "end_date": "2026-10-30"}))[0]

    assert day["latest_level"] == 77
    assert day["latest_level_time_gmt"] == "2026-10-30T13:00:00Z"
    assert (day["highest_level"], day["lowest_level"]) == (77, 35)


@pytest.mark.asyncio
async def test_body_battery_reads_the_columns_its_descriptor_names(app, mock_garmin_client):
    mock_garmin_client.get_body_battery.return_value = [_battery_day(
        bodyBatteryValuesArray=[[35, 1793332800000], [77, 1793365200000]],
        bodyBatteryValueDescriptorDTOList=[
            {"bodyBatteryValueDescriptorIndex": 1, "bodyBatteryValueDescriptorKey": "timestamp"},
            {"bodyBatteryValueDescriptorIndex": 0, "bodyBatteryValueDescriptorKey": "bodyBatteryLevel"},
        ],
    )]

    day = (await _call(app, "get_body_battery", {"start_date": "2026-10-30", "end_date": "2026-10-30"}))[0]

    assert day["latest_level"] == 77
    assert day["latest_level_time_gmt"] == "2026-10-30T13:00:00Z"


@pytest.mark.asyncio
async def test_body_battery_on_a_clock_change_day_places_times_by_the_profile_zone(app, mock_garmin_client):
    """New York leaves daylight saving at 2 AM on 2026-11-01: the day opens at
    -04:00 and closes at -05:00, so neither offset fits every reading."""
    _zone(mock_garmin_client)
    mock_garmin_client.get_body_battery.return_value = [_battery_day(
        date="2026-11-01",
        startTimestampGMT="2026-11-01T04:00:00.0", startTimestampLocal="2026-11-01T00:00:00.0",
        endTimestampGMT="2026-11-01T14:00:00.0", endTimestampLocal="2026-11-01T09:00:00.0",
        bodyBatteryValuesArray=[[1793505600000, 30], [1793539800000, 80]],
        bodyBatteryDynamicFeedbackEvent=None,
    )]

    day = (await _call(app, "get_body_battery", {"start_date": "2026-11-01", "end_date": "2026-11-01"}))[0]

    assert day["start_time_local"] == "2026-11-01T00:00:00-04:00"
    assert day["end_time_local"] == "2026-11-01T09:00:00-05:00"
    assert day["latest_level_time_local"] == "2026-11-01T08:30:00-05:00"
    assert day["local_time_zone"] == "America/New_York"
    assert "level_band" not in day


@pytest.mark.asyncio
async def test_body_battery_day_with_no_series_adds_no_levels(app, mock_garmin_client):
    mock_garmin_client.get_body_battery.return_value = [_battery_day(
        bodyBatteryValuesArray=None, bodyBatteryDynamicFeedbackEvent={},
    )]

    day = (await _call(app, "get_body_battery", {"start_date": "2026-10-30", "end_date": "2026-10-30"}))[0]

    assert not {"latest_level", "highest_level", "lowest_level", "level_band"} & set(day)
    assert day["charged"] == 44
