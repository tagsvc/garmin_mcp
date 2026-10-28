"""Explicit time zones for Garmin timestamps (garmin_mcp.times).

Values are the athlete's real ones from 2026-10-27/28 (New York, UTC-4):
the 18:37:26 GMT run started at 14:37:26 local; the night's sleep began at
03:17:13 GMT, 23:17:13 local the evening before.
"""
import datetime
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

from garmin_mcp import profile_settings as profile_settings_module
from garmin_mcp.client_resolver import _ResolvingGarminProxy, set_global_client
from garmin_mcp.times import (
    gmt,
    gmt_iso,
    local_from_offset,
    local_from_zone,
    local_today,
    offset_from_ms,
    offset_from_pair,
    profile_time_zone,
    put_times,
    wall_clock,
)

MINUS_4 = datetime.timedelta(hours=-4)


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-27T18:37:26.0",
        "2026-10-27 18:37:26",
        "2026-10-27T18:37:26Z",
        "2026-10-27T18:37:26.000+00:00",
        "2026-10-27T14:37:26-04:00",
        "2026-10-27 18:37:26.0 GMT",
        1793126246000,
        datetime.datetime(2026, 10, 27, 18, 37, 26),
        datetime.datetime(2026, 10, 27, 18, 37, 26, tzinfo=datetime.timezone.utc),
    ],
)
def test_gmt_reads_every_shape_garmin_sends(value):
    assert gmt_iso(value) == "2026-10-27T18:37:26Z"


@pytest.mark.parametrize("value", [None, "", "yesterday", 1793155, True, {}, "2026-10-27"])
def test_gmt_rejects_what_is_not_a_time(value):
    assert gmt(value) is None


def test_wall_clock_reads_garmins_local_pseudo_epoch_as_written():
    # sleepStartTimestampLocal: the local wall clock written as if it were UTC.
    assert wall_clock(1793143033000) == datetime.datetime(2026, 10, 27, 23, 17, 13)
    assert wall_clock("2026-10-27T23:17:13.0") == datetime.datetime(2026, 10, 27, 23, 17, 13)


def test_offset_from_pair_and_rounding():
    assert offset_from_pair(1793157433000, 1793143033000) == MINUS_4
    # A second apart between the two stamps does not leak into the offset.
    assert offset_from_pair("2026-10-27T19:01:16.0", "2026-10-27T15:01:17.0") == MINUS_4
    # India is +5:30; quarter-hour rounding keeps it.
    assert offset_from_pair("2026-10-27T12:00:00", "2026-10-27T17:30:00") == datetime.timedelta(
        hours=5, minutes=30
    )
    assert offset_from_pair("2026-10-27T12:00:00", "2026-10-29T12:00:00") is None
    assert offset_from_pair(None, "2026-10-27T12:00:00") is None


def test_offset_from_ms():
    assert offset_from_ms(-14400000) == MINUS_4
    assert offset_from_ms(None) is None
    assert offset_from_ms(True) is None


def test_local_from_offset_writes_the_offset():
    assert local_from_offset("2026-10-28T03:17:13.0", MINUS_4) == "2026-10-27T23:17:13-04:00"
    assert local_from_offset("2026-10-28T03:17:13.0", None) is None


def test_local_from_zone_applies_daylight_saving():
    # New York leaves daylight saving on Sunday 2026-11-01.
    assert local_from_zone("2026-10-31T16:00:00Z", "America/New_York") == "2026-10-31T12:00:00-04:00"
    assert local_from_zone("2026-11-02T17:00:00Z", "America/New_York") == "2026-11-02T12:00:00-05:00"
    assert local_from_zone("2026-10-31T16:00:00Z", "Not/AZone") is None
    assert local_from_zone("2026-10-31T16:00:00Z", None) is None


def test_put_times_prefers_the_records_own_offset_over_the_profile_zone():
    out = {}
    used = put_times(out, "start_time", "2026-10-27T18:37:26.0", offset=MINUS_4, zone="Asia/Tokyo")
    assert used is False
    assert out == {
        "start_time_gmt": "2026-10-27T18:37:26Z",
        "start_time_local": "2026-10-27T14:37:26-04:00",
    }


def test_put_times_uses_the_local_value_beside_the_gmt_one():
    out = {}
    assert put_times(out, "as_of", "2026-10-27T19:01:16.0", "2026-10-27T15:01:16.0") is False
    assert out["as_of_local"] == "2026-10-27T15:01:16-04:00"


def test_put_times_local_only_is_written_without_an_invented_offset():
    out = {}
    assert put_times(out, "as_of", None, "2026-10-27T15:01:16.0", zone="Asia/Tokyo") is False
    assert out == {"as_of_local": "2026-10-27T15:01:16"}


def test_put_times_falls_back_to_the_profile_zone_and_says_so():
    out = {}
    assert put_times(out, "start_time", "2026-10-27T18:37:26.0", zone="America/New_York") is True
    assert out["start_time_local"] == "2026-10-27T14:37:26-04:00"


def test_put_times_with_nothing_usable_sets_nothing():
    out = {}
    assert put_times(out, "start_time", None, None, zone="America/New_York") is False
    assert out == {}


def _client_with_zone(zone):
    client = Mock()
    client.get_userprofile_settings.return_value = {"timeZone": zone, "measurementSystem": "statute_us"}
    return client


def test_profile_time_zone_is_read_once_per_client():
    client = _client_with_zone("America/New_York")
    assert profile_time_zone(client) == "America/New_York"
    assert profile_time_zone(client) == "America/New_York"
    client.get_userprofile_settings.assert_called_once_with()


@pytest.mark.parametrize(
    "settings", [{"timeZone": "Mars/Olympus_Mons"}, {"timeZone": ""}, {}, None, Mock()]
)
def test_profile_time_zone_rejects_unusable_settings(settings):
    client = Mock()
    client.get_userprofile_settings.return_value = settings
    assert profile_time_zone(client) is None


def test_profile_time_zone_survives_a_failed_settings_call():
    client = Mock()
    client.get_userprofile_settings.side_effect = RuntimeError("rate limited")
    assert profile_time_zone(client) is None


def test_the_shared_proxy_is_never_the_cache_key():
    """In remote mode every user shares one module proxy; caching the zone on
    it would hand one user's zone to the next. It resolves per call."""
    proxy = _ResolvingGarminProxy()
    first, second = _client_with_zone("America/New_York"), _client_with_zone("Asia/Tokyo")
    try:
        set_global_client(first)
        assert profile_time_zone(proxy) == "America/New_York"
        set_global_client(second)
        assert profile_time_zone(proxy) == "Asia/Tokyo"
    finally:
        set_global_client(None)
    assert proxy not in profile_settings_module._settings


def test_local_today_follows_the_profile_zone():
    # +14h and -11h are 25 hours apart: their dates always differ.
    east = local_today(_client_with_zone("Pacific/Kiritimati"))
    west = local_today(_client_with_zone("Pacific/Pago_Pago"))
    assert east == datetime.datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
    assert west == datetime.datetime.now(ZoneInfo("Pacific/Pago_Pago")).date()
    assert east != west


def test_local_today_without_a_zone_is_the_servers_date():
    assert local_today(Mock()) == datetime.date.today()


def test_add_local_siblings_uses_each_records_own_offset():
    from garmin_mcp.times import add_local_siblings

    payload = {
        "lastSyncTimestampGMT": "2026-10-28T21:18:19.470",
        "startTimestampGMT": "2026-10-27T04:00:00.0", "startTimestampLocal": "kept",
        "events": [{"eventStartTimeGmt": "2026-10-28T03:17:13", "timezoneOffset": 3600000}],
        "count": 3,
    }
    add_local_siblings(payload, MINUS_4)

    assert payload["lastSyncTimestampLocal"] == "2026-10-28T17:18:19-04:00"
    assert payload["startTimestampLocal"] == "kept"
    # The event's own +1h, not the day's -4h.
    assert payload["events"][0]["eventStartTimeLocal"] == "2026-10-28T04:17:13+01:00"
    assert "countLocal" not in payload


def test_add_local_siblings_without_an_offset_adds_nothing():
    from garmin_mcp.times import add_local_siblings

    payload = {"lastSyncTimestampGMT": "2026-10-28T21:18:19.470"}
    assert add_local_siblings(payload, None) == {"lastSyncTimestampGMT": "2026-10-28T21:18:19.470"}


def test_zone_garmin_times_gives_garmins_text_its_zone_in_place():
    from garmin_mcp.times import zone_garmin_times

    payload = {
        "wellnessStartTimeGmt": "2026-10-28T04:00:00.0",
        "wellnessStartTimeLocal": "2026-10-28T00:00:00.0",
        "latestSpo2ReadingTimeGmt": "2026-10-28T09:59:00.0",
        "latestSpo2ReadingTimeLocal": "2026-10-28T05:59:00.0",
        "events": [{"eventStartTimeGmt": "2026-10-28T03:17:13", "timezoneOffset": 3600000,
                    "eventStartTimeLocal": "2026-10-28T04:17:13"}],
        "sleepStartTimestampGMT": 1793157433000,
        "floorsValueDescriptorDTOList": [{"index": 0, "key": "startTimeGMT"},
                                         {"index": 2, "key": "floorsAscended"}],
        "floorValuesArray": [["2026-10-27T04:00:00.0", "keep", 3]],
        "calendarDate": "2026-10-28",
    }
    zone_garmin_times(payload, MINUS_4)

    assert payload["wellnessStartTimeGmt"] == "2026-10-28T04:00:00Z"
    # Each local takes the offset from its own GMT partner.
    assert payload["wellnessStartTimeLocal"] == "2026-10-28T00:00:00-04:00"
    assert payload["latestSpo2ReadingTimeLocal"] == "2026-10-28T05:59:00-04:00"
    # A record's own timezoneOffset wins over the day's.
    assert payload["events"][0]["eventStartTimeLocal"] == "2026-10-28T04:17:13+01:00"
    assert payload["events"][0]["eventStartTimeGmt"] == "2026-10-28T03:17:13Z"
    # Epochs stay numbers; only descriptor-named GMT columns change.
    assert payload["sleepStartTimestampGMT"] == 1793157433000
    assert payload["floorValuesArray"] == [["2026-10-27T04:00:00Z", "keep", 3]]
    assert payload["calendarDate"] == "2026-10-28"


def test_zone_garmin_times_uses_the_profile_zone_for_a_lone_local_time():
    from garmin_mcp.times import zone_garmin_times

    summer = zone_garmin_times({"lastEntryTimestampLocal": "2026-10-27T22:58:29.7"}, zone="America/New_York")
    winter = zone_garmin_times({"lastEntryTimestampLocal": "2026-11-02T22:58:29.7"}, zone="America/New_York")
    unknown = zone_garmin_times({"lastEntryTimestampLocal": "2026-10-27T22:58:29.7"})

    assert summer["lastEntryTimestampLocal"] == "2026-10-27T22:58:29-04:00"
    assert winter["lastEntryTimestampLocal"] == "2026-11-02T22:58:29-05:00"
    assert unknown["lastEntryTimestampLocal"] == "2026-10-27T22:58:29.7"  # no offset to add
