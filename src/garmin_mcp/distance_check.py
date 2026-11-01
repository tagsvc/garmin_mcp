"""Where an activity's distance came from, checked against its GPS track.

FORK: a fenix 8 took speed and distance from a paired HRM 600 on every run
from 2026-09-19 to 2026-10-27 (the strap's Pace and Distance set to Always,
auto calibrate off). Easy runs came out 24-40% longer than their GPS tracks,
and nothing in Garmin's summary showed it: the distance, pace and stride all
looked plausible.

Two separate readings, both from the activity's original FIT file:

- The watch's own record of which sensor supplied speed and distance. That
  is FIT message 22, which Garmin does not document; each entry names a
  device_info entry by its device index. Field 1 is read as the distance
  source (field 0 the speed source) from live files: field 4 follows heart
  rate from the wrist to the strap as it connects, field 3 is the barometer,
  and fields 0 and 1 named the same sensor in every run checked, apart from
  the first seconds.
- The GPS track's own length, from the per-second positions, against the
  distance recorded. Where GPS supplied distance the two agreed within 1%
  (live runs, 2026-10-29 to 2026-11-01); where the strap did, its figure was
  24-40% longer.
"""

import datetime
import io
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import fitparse
    FITPARSE_AVAILABLE = True
except ImportError:  # pragma: no cover - fitparse is a dependency
    FITPARSE_AVAILABLE = False

# The undocumented FIT message naming each data channel's source device.
SOURCE_MESSAGE = 22
_DISTANCE_FIELD = "unknown_1"
_TIME_FIELD = "unknown_253"

# Sports a strap's or foot pod's pace and distance can stand in for GPS.
FOOT_SPORTS = ("run", "walk", "hik")
# Beyond this the recorded distance and the GPS track disagree. A GPS-sourced
# run's distance and its track agree within about 1%.
DISAGREE_PERCENT = 5.0
# Compare only a track with positions in most records, and long enough to mean
# something.
MIN_FIX_SHARE = 0.8
MIN_TRACK_METERS = 200.0
# Consecutive positions further apart in time than this are a pause or a
# dropout, not a step of the track.
MAX_STEP_SECONDS = 10
# A source that supplied distance for less than this is not listed as a change
# (the watch's own sensors stand in for the first seconds while a strap connects).
MIN_SEGMENT_SECONDS = 60

_FIT_EPOCH = datetime.datetime(1989, 12, 31)
_SEMICIRCLE_DEGREES = 180.0 / 2 ** 31
_EARTH_RADIUS_M = 6371008.8

# device_info device_type, by how the sensor connects (FIT profile values).
_LOCAL_GPS = {0, 1, 2}  # gps, glonass, gps_glonass
_LOCAL_ACCELEROMETER = 3
_PAIRED_SOURCES = {"ant", "antplus", "bluetooth", "bluetooth_low_energy"}
_SENSOR_KINDS = {
    # ANT+ (fitparse names them), then Bluetooth LE (fitparse leaves the number)
    "heart_rate": "heart_rate_strap", 120: "heart_rate_strap",
    "stride_speed_distance": "foot_pod", 124: "foot_pod",
    ("bluetooth_low_energy", 1): "heart_rate_strap",
    ("bluetooth_low_energy", 6): "foot_pod",
}


def applies(type_key: Optional[str], distance_meters: Any) -> bool:
    """Whether to check this activity: a run, walk or hike with a distance."""
    return (
        isinstance(type_key, str)
        and any(sport in type_key for sport in FOOT_SPORTS)
        and isinstance(distance_meters, (int, float))
        and distance_meters > 0
    )


def _fit_seconds(value: Any) -> Optional[float]:
    """A FIT time as seconds since FIT's epoch: decoded or raw."""
    if isinstance(value, datetime.datetime):
        return (value.replace(tzinfo=None) - _FIT_EPOCH).total_seconds()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _haversine(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(h))


def _describe(device: Optional[Dict[str, Any]], sensor_name: Callable[[Any], Optional[str]]) -> Dict[str, Any]:
    """A device_info entry as a distance source."""
    if not device:
        return {"source": "watch"}
    source_type = device.get("source_type")
    device_type = next(
        (device[key] for key in ("antplus_device_type", "ant_device_type", "device_type")
         if device.get(key) is not None),
        None,
    )
    if source_type == "local":
        if device_type in _LOCAL_GPS:
            return {"source": "gps"}
        if device_type == _LOCAL_ACCELEROMETER:
            return {"source": "watch_accelerometer"}
        return {"source": "watch"}
    if source_type in _PAIRED_SOURCES:
        kind = _SENSOR_KINDS.get(device_type) or _SENSOR_KINDS.get((source_type, device_type)) or "sensor"
        product = device.get("garmin_product", device.get("product"))
        name = sensor_name(product) if product is not None else None
        if not name and isinstance(product, str):
            name = product  # a product fitparse knows by name
        described = {"source": "paired_sensor", "sensor_kind": kind}
        if name:
            described["sensor"] = name
        return described
    return {"source": "watch"}


def _label(described: Dict[str, Any]) -> str:
    return described.get("sensor") or described.get("sensor_kind") or described["source"]


def _segments(
    sources: List[Tuple[Optional[float], Any]],
    devices: Dict[int, Dict[str, Any]],
    start: Optional[float],
    end: Optional[float],
    sensor_name: Callable[[Any], Optional[str]],
) -> List[Dict[str, Any]]:
    """Consecutive stretches of one distance source, with their seconds."""
    segments: List[Dict[str, Any]] = []
    timed = [(t, index) for t, index in sources if t is not None]
    if not timed or start is None or end is None:
        return segments
    for position, (t, index) in enumerate(timed):
        begin = max(t, start)
        finish = timed[position + 1][0] if position + 1 < len(timed) else end
        seconds = max(0.0, min(finish, end) - begin)
        described = _describe(devices.get(index) if isinstance(index, int) else None, sensor_name)
        if segments and segments[-1]["described"] == described:
            segments[-1]["seconds"] += seconds
        else:
            segments.append({"described": described, "from": begin - start, "seconds": seconds})
    return segments


def check(
    fit_bytes: bytes,
    recorded_meters: Optional[float],
    sensor_name: Callable[[Any], Optional[str]] = lambda _product: None,
) -> Optional[Dict[str, Any]]:
    """Where distance came from, and how it compares with the GPS track.

    Args:
        fit_bytes: The activity's FIT file (already out of Garmin's ZIP).
        recorded_meters: The distance Garmin reports for the activity.
        sensor_name: Product id -> the paired sensor's name, if known.

    Returns None when the file says nothing about either.
    """
    if not FITPARSE_AVAILABLE:
        return None
    devices: Dict[int, Dict[str, Any]] = {}
    sources: List[Tuple[Optional[float], Any]] = []
    records = fixes = 0
    track = 0.0
    previous: Optional[Tuple[float, Tuple[float, float]]] = None
    first_time = last_time = None

    for message in fitparse.FitFile(io.BytesIO(fit_bytes)).get_messages():
        name = message.name
        if name == "record":
            values = message.get_values()
            records += 1
            t = _fit_seconds(values.get("timestamp"))
            if t is not None:
                first_time = t if first_time is None else first_time
                last_time = t
            lat, lon = values.get("position_lat"), values.get("position_long")
            if isinstance(lat, int) and isinstance(lon, int):
                fixes += 1
                here = (lat * _SEMICIRCLE_DEGREES, lon * _SEMICIRCLE_DEGREES)
                if previous is not None and t is not None and t - previous[0] <= MAX_STEP_SECONDS:
                    track += _haversine(previous[1], here)
                if t is not None:
                    previous = (t, here)
        elif name == "device_info":
            values = message.get_values()
            index = values.get("device_index")
            if isinstance(index, int):
                devices[index] = values
        elif name == f"unknown_{SOURCE_MESSAGE}" or getattr(message, "mesg_num", None) == SOURCE_MESSAGE:
            values = message.get_values()
            when = values.get(_TIME_FIELD, values.get("timestamp"))
            sources.append((_fit_seconds(when), values.get(_DISTANCE_FIELD)))

    result: Dict[str, Any] = {}
    segments = _segments(sources, devices, first_time, last_time, sensor_name)
    if segments:
        main = max(segments, key=lambda s: s["seconds"])["described"]
        result["distance_source"] = main["source"]
        if main["source"] == "paired_sensor":
            result["distance_source_sensor"] = _label(main)
            result["distance_source_sensor_kind"] = main["sensor_kind"]
        lasting = [s for s in segments if s["seconds"] >= MIN_SEGMENT_SECONDS]
        merged: List[Dict[str, Any]] = []
        for segment in lasting:
            if not merged or merged[-1]["described"] != segment["described"]:
                merged.append(segment)
        if len(merged) > 1:
            result["distance_source_changes"] = [
                {"from_minute": round(s["from"] / 60, 1), "source": _label(s["described"])}
                for s in merged
            ]

    compared = (
        records and fixes / records >= MIN_FIX_SHARE and track >= MIN_TRACK_METERS
        and isinstance(recorded_meters, (int, float)) and recorded_meters > 0
    )
    if compared:
        percent = (recorded_meters / track - 1) * 100
        result["gps_track_distance_meters"] = round(track, 1)
        result["recorded_vs_gps_track_percent"] = round(percent, 1)
        result["agrees_with_gps_track"] = abs(percent) <= DISAGREE_PERCENT
        if abs(percent) > DISAGREE_PERCENT:
            direction = "longer" if percent > 0 else "shorter"
            warning = f"Recorded distance is {abs(percent):.0f}% {direction} than the GPS track."
            if segments and result.get("distance_source") != "gps":
                warning += (
                    f" The watch took distance from {_label(main)}, not GPS, so this "
                    "activity's distance, pace and speed follow that sensor; "
                    "gps_track_distance is the better measure of ground covered. A "
                    "paired sensor set to supply pace and distance Always overrides "
                    "GPS outdoors."
                )
            result["warning"] = warning
    elif fixes and records:
        result["gps_track_note"] = (
            f"GPS positions in {100 * fixes / records:.0f}% of the recording: "
            "too few to compare the distance with the track."
        )
    return result or None
