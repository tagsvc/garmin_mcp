"""Health Snapshot and ECG tools for Garmin Connect MCP Server.

FORK: both need garminconnect 0.3.20 or later, which has the methods.

- A Health Snapshot is a two-minute reading the watch takes on request:
  heart rate, respiration, stress, SpO2 and HRV, summarised and sampled
  every second (wellnessactivity-service).
- An ECG is a 30-second heart rhythm recording from the watch's ECG app,
  served from a separate host (ghs.garmin.com): a summary (rhythm, average
  heart rate, HRV, the wrist) and the waveform, sampled at 128 Hz.
"""
import datetime
import json
import re
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import Context

from garmin_mcp.client_resolver import get_client
from garmin_mcp.times import gmt, profile_time_zone, put_times

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


def _ecg_offset(summary: Dict[str, Any]) -> Optional[datetime.timedelta]:
    seconds = summary.get("timeZoneOffsetInSeconds")
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool):
        return datetime.timedelta(seconds=seconds)
    return None


def _curate_ecg_summary(summary: Dict[str, Any], zone: Optional[str]) -> Dict[str, Any]:
    """An ECG summary: its result, when it was taken, and on what."""
    ecg: Dict[str, Any] = {"ecg_id": summary.get("detailId")}
    if put_times(ecg, "start_time", summary.get("startTime"), summary.get("startTimeLocal"),
                 offset=_ecg_offset(summary), zone=zone):
        ecg["local_time_zone"] = zone
    ecg.update({
        "rhythm": summary.get("rhythmClassification"),
        "heart_rate_avg_bpm": summary.get("heartRateAverage"),
        "hrv_rmssd_ms": summary.get("rmssdHrv"),
        "wrist": summary.get("mountingSide"),
        "symptoms": summary.get("symptoms"),
        "ecg_app_version": summary.get("ecgAppVersion"),
        "udi": summary.get("udi"),
    })
    device = summary.get("deviceInfo")
    if isinstance(device, dict):
        ecg["device"] = {
            k: v for k, v in {"name": device.get("productName"),
                              "firmware": device.get("firmwareVersion")}.items()
            if v is not None
        }
    return {k: v for k, v in ecg.items() if v is not None}


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

        Each recording gives its rhythm (Garmin's classification, e.g.
        SINUS_NORMAL), average heart rate (heart_rate_avg_bpm), HRV as RMSSD
        (hrv_rmssd_ms), the wrist the watch was on, any symptoms logged, the
        ECG app version and the device and its firmware. start_time_gmt and
        start_time_local give when it was taken, local from the recording's own
        offset; local_time_zone appears only when the profile's zone had to be
        used. get_ecg returns one recording with its waveform. An account
        without the ECG feature gets a "no ECG data" answer.

        Args:
            start_date: First day, YYYY-MM-DD.
            end_date: Last day, YYYY-MM-DD (default: start_date).
        """
        try:
            client = get_client(ctx)
            end = end_date or start_date
            data = client.get_ecg_list(start=start_date, end=end)
            content = data.get("content") if isinstance(data, dict) else None
            entries = [e for e in content or [] if isinstance(e, dict)]
            if not entries:
                return f"No ECG recordings between {start_date} and {end}."
            zone = profile_time_zone(client)
            result: Dict[str, Any] = {
                "count": len(entries),
                "recordings": [_curate_ecg_summary(e, zone) for e in entries],
            }
            errors = data.get("transformErrors")
            if errors:
                result["transform_errors"] = errors
            return json.dumps(result, indent=2)
        except Exception as e:
            if _not_found(e):
                return "No ECG data: Garmin has no ECG recordings for this account (or no ECG feature)."
            return f"Error retrieving ECG recordings: {str(e)}"

    @app.tool()
    async def get_ecg(ctx: Context, ecg_id: str, include_waveform: bool = False) -> str:
        """One ECG recording: its summary, the reading's sampling and, if asked, the waveform.

        The summary is as in get_ecg_recordings. The reading gives the lead
        (e.g. "Lead I", the single lead a watch records), the sample rate
        (sample_rate_hz, and the watch's raw_sample_rate_hz), its length
        (duration_seconds) and sample_count. With include_waveform the samples
        themselves are returned as Garmin sends them (a few thousand numbers).
        uploaded_gmt/_local and processed_gmt/_local say when Garmin received
        and analysed it. start_time_gmt/_local as in the list; local_time_zone
        appears only when the profile's zone had to be used.

        Args:
            ecg_id: The recording's ecg_id, from get_ecg_recordings.
            include_waveform: Also return the waveform samples (default False).
        """
        try:
            client = get_client(ctx)
            data = client.get_ecg(ecg_id)
            if not isinstance(data, dict) or not data:
                return f"No ECG recording {ecg_id}."
            summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
            zone = profile_time_zone(client)
            ecg = _curate_ecg_summary(summary, zone)
            reading = data.get("reading") if isinstance(data.get("reading"), dict) else {}
            samples = reading.get("samples") if isinstance(reading.get("samples"), list) else None
            ecg.update({k: v for k, v in {
                "lead": reading.get("leadType"),
                "sample_rate_hz": reading.get("sampleRate"),
                "raw_sample_rate_hz": reading.get("rawSampleRate"),
                "duration_seconds": reading.get("durationInSeconds"),
                "sample_count": len(samples) if samples is not None else None,
            }.items() if v is not None})
            processing = data.get("processingInfo") if isinstance(data.get("processingInfo"), dict) else {}
            offset = _ecg_offset(summary)
            for stem, key in (("uploaded", "uploadTime"), ("processed", "processingTime")):
                put_times(ecg, stem, processing.get(key), offset=offset, zone=zone)
            if include_waveform and samples is not None:
                ecg["samples"] = samples
            return json.dumps(ecg, indent=2)
        except Exception as e:
            if _not_found(e):
                return f"No ECG recording {ecg_id}."
            return f"Error retrieving ECG {ecg_id}: {str(e)}"

    return app
