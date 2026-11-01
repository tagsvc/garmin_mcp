"""get_activity's distance_check: where distance came from, against the GPS track.

The case behind it: a watch took run distance from a paired heart-rate strap
for weeks, and recorded easy runs 24-40% longer than their GPS tracks. The FIT
files here are built byte for byte (tests/fit_builder.py) so fitparse reads
them as it reads a watch's; every value is synthetic.
"""
import io
import json
import zipfile

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import activity_management, distance_check
from garmin_mcp.client_resolver import set_global_client
from tests.fit_builder import (
    ENUM, SINT32, UINT8, UINT16, UINT32, build_fit, fit_time, semicircles,
)

START = 1793297549  # Unix seconds
LAT, LON = 45.0, -100.0
METERS_PER_DEGREE_LAT = 6371008.8 * 3.141592653589793 / 180

# Device indexes as a watch numbers them: its GPS, its accelerometer, a strap.
GPS, ACCEL, STRAP, POD = 9, 4, 3, 5
DEVICES = {
    GPS: {"type": 0, "source": 5, "product": 4533},
    ACCEL: {"type": 3, "source": 5, "product": 4533},
    STRAP: {"type": 1, "source": 3, "product": 4607},   # Bluetooth LE heart rate
    POD: {"type": 124, "source": 1, "product": 2222},   # ANT+ stride/speed/distance
}
SENSORS = [{"productId": 4607, "deviceName": "HRM 600"}]


def _fit(seconds=1200, speed=2.0, factor=1.0, sources=((0, STRAP),), positions=True,
         fix_every=1, gap=None):
    """A run heading north at speed (m/s), recording factor x the true distance.

    sources: (seconds from start, device index supplying distance[, speed]).
    gap: (at second, seconds) with no records, the runner moving on meanwhile.
    """
    messages = [(0, [(0, ENUM, 4), (1, UINT16, 1), (2, UINT16, 4533), (4, UINT32, fit_time(START))])]
    for index, device in DEVICES.items():
        messages.append((23, [
            (253, UINT32, fit_time(START)), (0, UINT8, index), (1, UINT8, device["type"]),
            (2, UINT16, 1), (4, UINT16, device["product"]), (25, ENUM, device["source"]),
        ]))
    for at, distance_index, *speed_source in sources:
        speed_index = speed_source[0] if speed_source else distance_index
        messages.append((22, [(253, UINT32, fit_time(START + at)), (0, UINT8, speed_index),
                              (1, UINT8, distance_index)]))
    skip = range(gap[0], gap[0] + gap[1]) if gap else range(0)
    for second in range(seconds):
        if second in skip:
            continue
        fields = [(253, UINT32, fit_time(START + second)),
                  (5, UINT32, int(round(second * speed * factor * 100)))]
        if positions and second % fix_every == 0:
            lat = LAT + second * speed / METERS_PER_DEGREE_LAT
            fields += [(0, SINT32, semicircles(lat)), (1, SINT32, semicircles(LON))]
        messages.append((20, fields))
    return build_fit(messages)


def _check(fit, recorded, names=None):
    lookup = {4607: "HRM 600"} if names is None else names
    return distance_check.check(fit, recorded, lambda product: lookup.get(product))


def test_a_strap_supplying_distance_is_named_and_flagged():
    """The strap took over from the watch's accelerometer in the first seconds.

    Speed is given as GPS here so the check is seen to read the distance entry.
    """
    fit = _fit(sources=((0, ACCEL), (3, STRAP, GPS)), factor=1.3)

    result = _check(fit, recorded=1199 * 2.0 * 1.3)

    assert result["distance_source"] == "paired_sensor"
    assert result["distance_source_sensor"] == "HRM 600"
    assert result["distance_source_sensor_kind"] == "heart_rate_strap"
    assert result["gps_track_distance_meters"] == pytest.approx(2398, abs=1)
    assert result["recorded_vs_gps_track_percent"] == pytest.approx(30.0, abs=0.2)
    assert result["agrees_with_gps_track"] is False
    assert "30% longer than the GPS track" in result["warning"]
    assert "from HRM 600, not GPS" in result["warning"]
    # Three seconds on the accelerometer is start-up, not a change of source.
    assert "distance_source_changes" not in result


def test_gps_distance_matching_its_track_is_quiet():
    result = _check(_fit(sources=((0, STRAP), (6, GPS))), recorded=1199 * 2.0)

    assert result["distance_source"] == "gps"
    assert result["agrees_with_gps_track"] is True
    assert result["recorded_vs_gps_track_percent"] == pytest.approx(0.0, abs=0.2)
    assert "warning" not in result
    assert "distance_source_sensor" not in result


def test_a_switch_partway_through_is_listed_and_the_longer_source_leads():
    fit = _fit(seconds=1500, sources=((0, STRAP), (600, GPS)))

    result = _check(fit, recorded=1499 * 2.0)

    assert result["distance_source"] == "gps"
    assert result["distance_source_changes"] == [
        {"from_minute": 0.0, "source": "HRM 600"},
        {"from_minute": 10.0, "source": "gps"},
    ]


def test_an_ant_foot_pod_with_no_known_name_is_named_by_kind():
    result = _check(_fit(sources=((0, POD),), factor=1.1), recorded=1199 * 2.0 * 1.1, names={})

    assert result["distance_source"] == "paired_sensor"
    assert result["distance_source_sensor_kind"] == "foot_pod"
    assert result["distance_source_sensor"] == "foot_pod"
    assert result["agrees_with_gps_track"] is False


def test_an_indoor_run_reports_its_source_and_no_track():
    result = _check(_fit(sources=((0, ACCEL),), positions=False), recorded=2400)

    assert result == {"distance_source": "watch_accelerometer"}


def test_too_few_gps_fixes_are_not_compared():
    result = _check(_fit(sources=((0, GPS),), fix_every=2), recorded=2400)

    assert "gps_track_distance_meters" not in result
    assert "50%" in result["gps_track_note"]


def test_without_the_source_message_the_track_is_still_compared():
    result = _check(_fit(sources=(), factor=1.25), recorded=1199 * 2.0 * 1.25)

    assert "distance_source" not in result
    assert result["recorded_vs_gps_track_percent"] == pytest.approx(25.0, abs=0.2)
    assert result["warning"] == "Recorded distance is 25% longer than the GPS track."


def test_a_pause_is_not_counted_as_track():
    """Sixty seconds with no records while the runner moves on 120 m."""
    fit = _fit(sources=((0, GPS),), gap=(600, 60))

    result = _check(fit, recorded=1199 * 2.0 - 120)

    assert result["gps_track_distance_meters"] == pytest.approx(1199 * 2.0 - 122, abs=1)
    assert result["agrees_with_gps_track"] is True


@pytest.mark.parametrize("type_key, distance, expected", [
    ("running", 3000.0, True), ("trail_running", 1.0, True), ("treadmill_running", 3000, True),
    ("walking", 1500.0, True), ("hiking", 8000.0, True),
    ("cycling", 30000.0, False), ("strength_training", 0, False), ("running", 0.0, False),
    ("running", None, False), (None, 3000.0, False),
])
def test_it_applies_to_runs_walks_and_hikes_with_a_distance(type_key, distance, expected):
    assert distance_check.applies(type_key, distance) is expected


# --- through get_activity -------------------------------------------------------

def _zip(fit: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("activity.fit", fit)
    return buffer.getvalue()


def _activity(type_key="running", distance=1199 * 2.0 * 1.3):
    return {
        "activityId": 77,
        "activityName": "Easy Run",
        "activityTypeDTO": {"typeKey": type_key},
        "summaryDTO": {"distance": distance, "duration": 1200.0,
                       "startTimeGMT": "2026-10-25T18:28:31.0", "startTimeLocal": "2026-10-25T14:28:31.0"},
        "timeZoneUnitDTO": {"timeZone": "America/New_York"},
    }


@pytest.fixture
def app(mock_garmin_client):
    mock_garmin_client.get_unit_system.return_value = "statute_us"
    mock_garmin_client.get_userprofile_settings.return_value = {"timeZone": "America/New_York"}
    mock_garmin_client.connectapi.side_effect = (
        lambda path, **_: SENSORS if path == "/device-service/sensors" else None)
    activity_management.configure(mock_garmin_client)
    set_global_client(mock_garmin_client)
    app = FastMCP("distance check")
    activity_management.register_tools(app)
    yield app
    set_global_client(None)


async def _get_activity(app):
    return json.loads((await app.call_tool("get_activity", {"activity_id": 77}))[0][0].text)


@pytest.mark.asyncio
async def test_get_activity_says_the_strap_supplied_distance(app, mock_garmin_client):
    from garminconnect import Garmin

    mock_garmin_client.get_activity.return_value = _activity()
    mock_garmin_client.download_activity.return_value = _zip(_fit(sources=((0, STRAP),), factor=1.3))

    data = await _get_activity(app)

    mock_garmin_client.download_activity.assert_called_once_with(
        77, dl_fmt=Garmin.ActivityDownloadFormat.ORIGINAL)
    checked = data["distance_check"]
    assert checked["distance_source_sensor"] == "HRM 600"
    assert checked["agrees_with_gps_track"] is False
    # The track's length in the account's units, beside the metric value.
    assert checked["gps_track_distance_miles"] == pytest.approx(2398 / 1609.344, abs=0.01)
    assert data["distance_meters"] == pytest.approx(3117.4, abs=0.1)


@pytest.mark.asyncio
async def test_get_activity_skips_the_check_for_a_ride(app, mock_garmin_client):
    mock_garmin_client.get_activity.return_value = _activity(type_key="cycling", distance=30000.0)

    data = await _get_activity(app)

    mock_garmin_client.download_activity.assert_not_called()
    assert "distance_check" not in data


@pytest.mark.asyncio
async def test_get_activity_still_answers_when_the_fit_file_is_unavailable(app, mock_garmin_client):
    mock_garmin_client.get_activity.return_value = _activity()
    mock_garmin_client.download_activity.side_effect = RuntimeError("Garmin said no")

    data = await _get_activity(app)

    assert data["name"] == "Easy Run"
    assert "distance_check" not in data
