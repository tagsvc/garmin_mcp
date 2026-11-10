"""get_activity_fit_messages device_summary: sensor times name their zone.

FORK: device_info timestamps are UTC. Each sensor's first and last sighting
goes out as *_gmt and, at the watch's own offset from the activity message,
*_local, per the fork's time contract. The FIT messages are synthetic,
shaped like a strap run in New York after the clocks went back (UTC-5).
"""
import datetime
import json

import pytest

from tests.integration.test_activity_analysis_tools import (  # noqa: F401 (fixture)
    _call_fit_messages,
    _make_creator_device_info,
    _make_generic_fit_message,
    _make_sensor_device_info,
    app_with_activity_analysis,
)
from tests.units_times_contract import output_violations

pytestmark = pytest.mark.integration


def _activity(ts, local_ts):
    return _make_generic_fit_message("activity", [
        (253, "timestamp", datetime.datetime.fromisoformat(ts), None),
        (5, "local_timestamp", datetime.datetime.fromisoformat(local_ts), None),
    ])


def _strap_run(*extra):
    return [
        _make_creator_device_info(ts="2026-11-07T18:21:01"),
        _make_sensor_device_info("2026-11-07T18:21:01", 3, "bluetooth_low_energy", 3611680303, battery=92),
        _make_sensor_device_info("2026-11-07T18:40:12", 3, "bluetooth_low_energy", 3611680303, battery=91),
        _make_creator_device_info(ts="2026-11-07T19:18:08"),
        _make_sensor_device_info("2026-11-07T19:18:08", 3, "bluetooth_low_energy", 3611680303, battery=90),
        *extra,
    ]


async def _summary(app, messages):
    text = await _call_fit_messages(app, messages, {"output_format": "device_summary"})
    return json.loads(text)


@pytest.mark.asyncio
async def test_sensor_times_are_gmt_and_local_at_the_watchs_offset(
    app_with_activity_analysis, mock_garmin_client
):
    mock_garmin_client.download_activity.return_value = b"\x00" * 20
    messages = _strap_run(_activity("2026-11-07T19:18:10", "2026-11-07T14:18:10"))

    data = await _summary(app_with_activity_analysis, messages)

    (strap,) = data["sensors"]
    assert strap["first_seen_gmt"] == "2026-11-07T18:21:01Z"
    assert strap["first_seen_local"] == "2026-11-07T13:21:01-05:00"
    assert strap["last_seen_gmt"] == "2026-11-07T19:18:08Z"
    assert strap["last_seen_local"] == "2026-11-07T14:18:08-05:00"
    assert "first_seen" not in strap and "last_seen" not in strap
    assert output_violations(data) == []


@pytest.mark.asyncio
async def test_without_the_activity_message_only_gmt_is_given(
    app_with_activity_analysis, mock_garmin_client
):
    """No offset to read: the local time is left out rather than guessed."""
    mock_garmin_client.download_activity.return_value = b"\x00" * 20

    data = await _summary(app_with_activity_analysis, _strap_run())

    (strap,) = data["sensors"]
    assert strap["first_seen_gmt"] == "2026-11-07T18:21:01Z"
    assert "first_seen_local" not in strap
    assert output_violations(data) == []
