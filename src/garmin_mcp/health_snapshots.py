"""Health Snapshot and ECG tools for Garmin Connect MCP Server.

FORK: both need garminconnect 0.3.20 or later, which has the methods.

- A Health Snapshot is a two-minute reading the watch takes on request:
  heart rate, respiration, stress, SpO2 and HRV, summarised and sampled
  every second (wellnessactivity-service).
- An ECG is a 30-second heart rhythm recording from the watch's ECG app.
  Garmin serves it from a separate host (ghs.garmin.com), so its reply is
  returned close to as sent, with Garmin's own time text given its zone.
"""
import datetime
import json
import re
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context

from garmin_mcp.client_resolver import get_client
from garmin_mcp.times import gmt, profile_time_zone, put_times, zone_garmin_times

# The garmin_client will be set by the main file
garmin_client = None

# A Health Snapshot's summaryType -> the output key (units in the name).
_SUMMARY_KEYS = {
    "HEART_RATE": "heart_rate_bpm",
    "RESPIRATION": "respiration_brpm",
    "STRESS": "stress",
    "SPO2": "spo2_percent",
    "RMSSD_HRV": "hrv_rmssd_ms",
    "SDRR_HRV": "hrv_sdrr_ms",
}
# The per-second sample columns, by Garmin's descriptor key.
_SAMPLE_KEYS = {
    "heartRate": "heart_rate_bpm",
    "stress": "stress",
    "spo2": "spo2_percent",
    "respiration": "respiration_brpm",
}
# An ECG waveform runs to thousands of numbers; a list this long is left out
# unless asked for.
_WAVEFORM_MIN_LENGTH = 200


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _summary_value(item: Dict[str, Any]) -> Any:
    """{avg, min, max} for a summary entry, or the bare value when it has one only."""
    values = {
        stat: item.get(f"{stat}Value")
        for stat in ("avg", "min", "max")
        if item.get(f"{stat}Value") is not None
    }
    if set(values) == {"avg"}:
        return values["avg"]
    return values or None


def _curate_snapshot(entry: Dict[str, Any], zone: Optional[str]) -> Dict[str, Any]:
    uuid = entry.get("activityUuid")
    snapshot: Dict[str, Any] = {
        "snapshot_id": uuid.get("uuid") if isinstance(uuid, dict) else uuid,
        "name": entry.get("activityName"),
        "date": entry.get("calendarDate"),
        "type": entry.get("wellnessActivityType"),
        "notes": entry.get("notes"),
    }
    used_zone = False
    for stem, key in (("start_time", "startTimestamp"), ("end_time", "endTimestamp")):
        used_zone |= put_times(snapshot, stem, entry.get(f"{key}GMT"), entry.get(f"{key}Local"), zone=zone)
    if used_zone:
        snapshot["local_time_zone"] = zone
    start, end = gmt(entry.get("startTimestampGMT")), gmt(entry.get("endTimestampGMT"))
    if start and end:
        snapshot["duration_seconds"] = round((end - start).total_seconds(), 1)
    for item in entry.get("summaryTypeDataList") or []:
        if not isinstance(item, dict) or not item.get("summaryType"):
            continue
        kind = item["summaryType"]
        value = _summary_value(item)
        if value is not None:
            snapshot[_SUMMARY_KEYS.get(kind, kind.lower())] = value
    zones = [
        {
            "zone": z.get("zoneNumber"),
            "seconds": round(z["millisInZone"] / 1000, 1) if isinstance(z.get("millisInZone"), (int, float)) else None,
            "from_bpm": z.get("zoneLowBoundary"),
        }
        for z in entry.get("timeInZoneList") or []
        if isinstance(z, dict)
    ]
    if zones:
        snapshot["hr_zones"] = zones
    device = entry.get("deviceMetaData")
    if isinstance(device, dict):
        snapshot["device"] = {
            k: v for k, v in {"name": device.get("deviceName"), "firmware": device.get("deviceVersion")}.items()
            if v is not None
        }
    return {k: v for k, v in snapshot.items() if v is not None}


def _curate_samples(epochs: Any, start: Optional[datetime.datetime]) -> List[Dict[str, Any]]:
    """Garmin's per-second rows as records, timed in seconds from the snapshot's start."""
    if not isinstance(epochs, dict):
        return []
    columns = {
        d.get("index"): d.get("key")
        for d in epochs.get("epochDescriptorDTOList") or []
        if isinstance(d, dict)
    }
    time_index = next((i for i, k in columns.items() if k == "timestamp"), None)
    rows = [r for r in epochs.get("epochArray") or [] if isinstance(r, list)]
    first = None
    if time_index is not None and rows and isinstance(rows[0][time_index], (int, float)):
        first = rows[0][time_index]
    base = start.timestamp() * 1000 if start else first
    samples = []
    for row in rows:
        sample: Dict[str, Any] = {}
        if time_index is not None and base is not None and isinstance(row[time_index], (int, float)):
            sample["seconds"] = round((row[time_index] - base) / 1000, 1)
        for index, key in columns.items():
            if index == time_index or not isinstance(index, int) or index >= len(row):
                continue
            if row[index] is not None:
                sample[_SAMPLE_KEYS.get(key, _snake(key or f"column_{index}"))] = row[index]
        samples.append(sample)
    return samples


def _drop_waveforms(node: Any) -> Any:
    """Replace long lists of numbers (waveform samples) with their length."""
    if isinstance(node, dict):
        return {k: _drop_waveforms(v) for k, v in node.items()}
    if isinstance(node, list):
        if len(node) >= _WAVEFORM_MIN_LENGTH and all(
            isinstance(x, (int, float, list)) and not isinstance(x, bool) for x in node
        ):
            return {"omitted_samples": len(node)}
        return [_drop_waveforms(v) for v in node]
    return node


def _not_found(exc: Exception) -> bool:
    return "404" in str(exc) or type(exc).__name__ == "GarminConnectNotFoundError"


def register_tools(app):
    """Register the Health Snapshot and ECG tools with the MCP server app"""

    @app.tool()
    async def get_health_snapshots(ctx: Context, start_date: str, end_date: Optional[str] = None) -> str:
        """List your Health Snapshots: two-minute readings the watch takes on request.

        Each snapshot gives heart rate (heart_rate_bpm), respiration
        (respiration_brpm), stress and SpO2 (spo2_percent), each as avg, min
        and max, plus HRV as RMSSD and SDRR in milliseconds (hrv_rmssd_ms,
        hrv_sdrr_ms), seconds in each heart rate zone (hr_zones), and the device and its
        firmware. Times are start_time_gmt/_local and end_time_gmt/_local,
        local from the snapshot's own offset; local_time_zone appears only
        when the profile's zone had to be used instead.

        Use snapshot_id with get_health_snapshot for the per-second samples.

        Args:
            start_date: First day, YYYY-MM-DD.
            end_date: Last day, YYYY-MM-DD (default: start_date).
        """
        try:
            client = get_client(ctx)
            data = client.get_health_snapshots_range(start_date, end_date or start_date)
            entries = [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []
            if not entries:
                return f"No Health Snapshots between {start_date} and {end_date or start_date}."
            zone = profile_time_zone(client)
            snapshots = [_curate_snapshot(e, zone) for e in entries]
            return json.dumps({"count": len(snapshots), "snapshots": snapshots}, indent=2)
        except Exception as e:
            return f"Error retrieving Health Snapshots: {str(e)}"

    @app.tool()
    async def get_health_snapshot(
        ctx: Context, snapshot_id: str, date: str, include_samples: bool = False
    ) -> str:
        """One Health Snapshot, optionally with its per-second samples.

        The summary is as in get_health_snapshots. With include_samples, the
        samples are one row per second: seconds from the snapshot's start,
        heart_rate_bpm, stress, spo2_percent and respiration_brpm (a value
        Garmin had not measured yet is left out of its row). Times are
        start_time_gmt/_local and end_time_gmt/_local; local_time_zone appears
        only when the profile's zone had to be used.

        Args:
            snapshot_id: The snapshot_id from get_health_snapshots.
            date: The snapshot's date, YYYY-MM-DD (its `date`).
            include_samples: Also return the per-second samples (default False).
        """
        try:
            client = get_client(ctx)
            entry = client.get_health_snapshot(date, snapshot_id)
            if not isinstance(entry, dict) or not entry:
                return f"No Health Snapshot {snapshot_id} on {date}."
            snapshot = _curate_snapshot(entry, profile_time_zone(client))
            if include_samples:
                samples = _curate_samples(
                    client.get_health_snapshot_epochs(snapshot_id),
                    gmt(entry.get("startTimestampGMT")),
                )
                snapshot["sample_count"] = len(samples)
                snapshot["samples"] = samples
            return json.dumps(snapshot, indent=2)
        except Exception as e:
            return f"Error retrieving Health Snapshot {snapshot_id}: {str(e)}"

    @app.tool()
    async def get_ecg_recordings(ctx: Context, start_date: str, end_date: Optional[str] = None) -> str:
        """List your ECG recordings (the watch's ECG app) in a date range.

        Garmin's reply is returned in its own layout, with its time text given
        a zone (GMT values end in Z; local values carry their offset). Long
        sample lists are replaced by {"omitted_samples": n}; get_ecg returns
        one recording with its waveform. An account without the ECG feature
        gets a "no ECG data" answer.

        Args:
            start_date: First day, YYYY-MM-DD.
            end_date: Last day, YYYY-MM-DD (default: start_date).
        """
        try:
            client = get_client(ctx)
            end = end_date or start_date
            data = client.get_ecg_list(start=start_date, end=end)
            if not data:
                return f"No ECG recordings between {start_date} and {end}."
            reply = _drop_waveforms(zone_garmin_times(data, zone=profile_time_zone(client)))
            return json.dumps({"start_date": start_date, "end_date": end, "ecg": reply}, indent=2)
        except Exception as e:
            if _not_found(e):
                return "No ECG data: Garmin has no ECG recordings for this account (or no ECG feature)."
            return f"Error retrieving ECG recordings: {str(e)}"

    @app.tool()
    async def get_ecg(ctx: Context, ecg_id: str, include_waveform: bool = False) -> str:
        """One ECG recording: Garmin's result for it and, if asked, the waveform.

        Returned in Garmin's own layout, with its time text given a zone (GMT
        values end in Z; local values carry their offset). The waveform runs
        to thousands of samples, so it is replaced by {"omitted_samples": n}
        unless include_waveform is set.

        Args:
            ecg_id: The recording's id, from get_ecg_recordings.
            include_waveform: Also return the waveform samples (default False).
        """
        try:
            client = get_client(ctx)
            data = client.get_ecg(ecg_id)
            if not data:
                return f"No ECG recording {ecg_id}."
            reply = zone_garmin_times(data, zone=profile_time_zone(client))
            if not include_waveform:
                reply = _drop_waveforms(reply)
            return json.dumps(reply, indent=2)
        except Exception as e:
            if _not_found(e):
                return f"No ECG recording {ecg_id}."
            return f"Error retrieving ECG {ecg_id}: {str(e)}"

    return app
