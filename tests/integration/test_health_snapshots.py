"""Health Snapshot and ECG tools.

The payloads follow the shape of live replies (Health Snapshot list, summary
and per-second epochs; ECG list and recording); every value and id here is
synthetic.
"""
import json
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import health_snapshots
from garmin_mcp.client_resolver import set_global_client

UUID = "00000000-0000-4000-8000-000000000001"
SNAPSHOT = {
    "userProfilePk": 1,
    "activityUuid": {"uuid": UUID},
    "calendarDate": "2026-11-01",
    "activityName": "Health Snapshot - Evening",
    "notes": None,
    "wellnessActivityType": "HEALTH_MONITORING",
    "startTimestampGMT": "2026-11-01T22:40:00.0",
    "endTimestampGMT": "2026-11-01T22:42:00.25",
    "startTimestampLocal": "2026-11-01T17:40:00.0",
    "endTimestampLocal": "2026-11-01T17:42:00.25",
    "summaryTypeDataList": [
        {"summaryType": "HEART_RATE", "minValue": 60.0, "maxValue": 70.0, "avgValue": 65.0},
        {"summaryType": "RESPIRATION", "minValue": 12.0, "maxValue": 18.0, "avgValue": 15.0},
        {"summaryType": "STRESS", "minValue": 20.0, "maxValue": 30.0, "avgValue": 25.0},
        {"summaryType": "SPO2", "minValue": 95.0, "maxValue": 99.0, "avgValue": 97.0},
        {"summaryType": "RMSSD_HRV", "avgValue": 48.0},
        {"summaryType": "SDRR_HRV", "avgValue": 60.0},
    ],
    "timeInZoneList": [
        {"zoneNumber": 0, "millisInZone": 120000, "zoneLowBoundary": 0},
        {"zoneNumber": 1, "millisInZone": 0, "zoneLowBoundary": 100},
    ],
    "deviceMetaData": {"deviceId": 2, "deviceTypeId": 3, "deviceVersion": "1.00",
                       "deviceName": "Watch"},
}
START_MS = 1793572800000  # 2026-11-01T22:40:00Z
EPOCHS = {
    "activityUuid": UUID,
    "epochDescriptorDTOList": [
        {"index": 0, "key": "timestamp"}, {"index": 1, "key": "heartRate"},
        {"index": 2, "key": "stress"}, {"index": 3, "key": "spo2"},
        {"index": 4, "key": "respiration"},
    ],
    "epochArray": [
        [START_MS, 70.0, 30.0, None, 15.0],
        [START_MS + 1000, 69.0, 29.0, 98.0, 15.0],
    ],
}


@pytest.fixture
def client():
    client = Mock()
    client.get_userprofile_settings.return_value = {"timeZone": "America/New_York"}
    client.get_health_snapshots_range.return_value = [SNAPSHOT]
    client.get_health_snapshot.return_value = SNAPSHOT
    client.get_health_snapshot_epochs.return_value = EPOCHS
    return client


@pytest.fixture
def app(client):
    health_snapshots.configure(client)
    set_global_client(client)
    yield health_snapshots.register_tools(FastMCP("snapshots"))
    set_global_client(None)


async def _call(app, name, args):
    return (await app.call_tool(name, args))[0][0].text


@pytest.mark.asyncio
async def test_snapshots_are_listed_with_their_readings(app, client):
    payload = json.loads(await _call(app, "get_health_snapshots", {"start_date": "2026-10-01",
                                                                  "end_date": "2026-11-01"}))

    client.get_health_snapshots_range.assert_called_once_with("2026-10-01", "2026-11-01")
    assert payload["count"] == 1
    snapshot = payload["snapshots"][0]
    assert snapshot["snapshot_id"] == UUID
    assert snapshot["date"] == "2026-11-01"
    assert snapshot["heart_rate_bpm"] == {"avg": 65.0, "min": 60.0, "max": 70.0}
    assert snapshot["respiration_brpm"]["avg"] == 15.0
    assert snapshot["spo2_percent"] == {"avg": 97.0, "min": 95.0, "max": 99.0}
    assert snapshot["hrv_rmssd_ms"] == 48.0 and snapshot["hrv_sdrr_ms"] == 60.0
    assert snapshot["duration_seconds"] == 120.0
    assert snapshot["hr_zones"][0] == {"zone": 0, "seconds": 120.0, "from_bpm": 0}
    assert snapshot["device"] == {"name": "Watch", "firmware": "1.00"}
    assert "notes" not in snapshot


@pytest.mark.asyncio
async def test_snapshot_times_take_the_records_own_offset(app):
    snapshot = json.loads(await _call(app, "get_health_snapshots", {"start_date": "2026-11-01"}))["snapshots"][0]

    assert snapshot["start_time_gmt"] == "2026-11-01T22:40:00Z"
    assert snapshot["start_time_local"] == "2026-11-01T17:40:00-05:00"
    assert snapshot["end_time_local"] == "2026-11-01T17:42:00-05:00"
    assert "local_time_zone" not in snapshot


@pytest.mark.asyncio
async def test_without_a_local_time_the_profile_zone_places_it_and_says_so(app, client):
    client.get_health_snapshots_range.return_value = [
        {k: v for k, v in SNAPSHOT.items() if not k.endswith("Local")}]

    snapshot = json.loads(await _call(app, "get_health_snapshots", {"start_date": "2026-11-01"}))["snapshots"][0]

    assert snapshot["start_time_local"] == "2026-11-01T17:40:00-05:00"
    assert snapshot["local_time_zone"] == "America/New_York"


@pytest.mark.asyncio
async def test_no_snapshots_is_said_plainly(app, client):
    client.get_health_snapshots_range.return_value = []

    text = await _call(app, "get_health_snapshots", {"start_date": "2026-11-01"})

    assert text == "No Health Snapshots between 2026-11-01 and 2026-11-01."


@pytest.mark.asyncio
async def test_one_snapshot_with_its_per_second_samples(app, client):
    payload = json.loads(await _call(app, "get_health_snapshot", {
        "snapshot_id": UUID, "date": "2026-11-01", "include_samples": True}))

    client.get_health_snapshot.assert_called_once_with("2026-11-01", UUID)
    client.get_health_snapshot_epochs.assert_called_once_with(UUID)
    assert payload["sample_count"] == 2
    assert payload["samples"][0] == {"seconds": 0.0, "heart_rate_bpm": 70.0, "stress": 30.0,
                                     "respiration_brpm": 15.0}
    assert payload["samples"][1]["seconds"] == 1.0
    assert payload["samples"][1]["spo2_percent"] == 98.0


@pytest.mark.asyncio
async def test_samples_are_fetched_only_when_asked(app, client):
    payload = json.loads(await _call(app, "get_health_snapshot", {"snapshot_id": UUID, "date": "2026-11-01"}))

    client.get_health_snapshot_epochs.assert_not_called()
    assert "samples" not in payload


# --- ECG -----------------------------------------------------------------------

ECG_ID = "00000000-0000-4000-8000-0000000000e1"
ECG_SUMMARY = {
    "detailId": ECG_ID,
    "startTime": 1793573000000,  # 2026-11-01T22:43:20Z
    "startTimeLocal": "2026-11-01T17:43:20.0",
    "timeZoneOffsetInSeconds": -18000,
    "rhythmClassification": "SINUS_NORMAL",
    "mountingSide": "LEFT",
    "rmssdHrv": 45,
    "heartRateAverage": 70.0,
    "ecgAppVersion": "1.0.0",
    "udi": "(01)00000000000000(10)1.0.0",
    "symptoms": [],
    "deviceInfo": {"productName": "WATCH", "productId": 1, "firmwareVersion": "1.00",
                   "partNumber": "000-00000-00"},
    "healthDeviceId": "1",
}
ECG_RECORDING = {
    "summary": ECG_SUMMARY,
    "reading": {"sampleRate": 128.0, "rawSampleRate": 512.0, "durationInSeconds": 30,
                "samples": [0.01] * 3840},
    "processingInfo": {"uploadTime": 1793573045000, "processingTime": 1793573045010,
                       "apiVersion": "1.0.0"},
}


@pytest.mark.asyncio
async def test_ecg_recordings_are_listed_with_their_result(app, client):
    client.get_ecg_list.return_value = {"content": [ECG_SUMMARY], "transformErrors": []}

    payload = json.loads(await _call(app, "get_ecg_recordings", {"start_date": "2026-11-01"}))

    client.get_ecg_list.assert_called_once_with(start="2026-11-01", end="2026-11-01")
    assert payload["count"] == 1
    assert "transform_errors" not in payload
    ecg = payload["recordings"][0]
    assert ecg["ecg_id"] == ECG_ID
    assert (ecg["rhythm"], ecg["heart_rate_avg_bpm"], ecg["hrv_rmssd_ms"]) == ("SINUS_NORMAL", 70.0, 45)
    assert ecg["wrist"] == "LEFT" and ecg["symptoms"] == []
    assert ecg["device"] == {"name": "WATCH", "firmware": "1.00"}
    assert "healthDeviceId" not in ecg and "detailId" not in ecg


@pytest.mark.asyncio
async def test_ecg_times_take_the_recordings_own_offset(app, client):
    """startTime is a GMT epoch; timeZoneOffsetInSeconds is the recording's own."""
    client.get_ecg_list.return_value = {"content": [dict(ECG_SUMMARY, timeZoneOffsetInSeconds=-14400,
                                                         startTimeLocal="2026-11-01T18:43:20.0")]}

    ecg = json.loads(await _call(app, "get_ecg_recordings", {"start_date": "2026-11-01"}))["recordings"][0]

    assert ecg["start_time_gmt"] == "2026-11-01T22:43:20Z"
    # The recording's own offset wins over the profile's zone (-05:00 that day).
    assert ecg["start_time_local"] == "2026-11-01T18:43:20-04:00"
    assert "local_time_zone" not in ecg


@pytest.mark.asyncio
async def test_one_ecg_with_its_reading_and_no_waveform_by_default(app, client):
    client.get_ecg.return_value = ECG_RECORDING

    ecg = json.loads(await _call(app, "get_ecg", {"ecg_id": ECG_ID}))

    client.get_ecg.assert_called_once_with(ECG_ID)
    assert ecg["rhythm"] == "SINUS_NORMAL"
    assert (ecg["sample_rate_hz"], ecg["raw_sample_rate_hz"], ecg["duration_seconds"]) == (128.0, 512.0, 30)
    assert ecg["sample_count"] == 3840
    assert ecg["uploaded_gmt"] == "2026-11-01T22:44:05Z"
    assert ecg["uploaded_local"] == "2026-11-01T17:44:05-05:00"
    assert "samples" not in ecg


@pytest.mark.asyncio
async def test_the_waveform_comes_back_when_asked(app, client):
    client.get_ecg.return_value = ECG_RECORDING

    ecg = json.loads(await _call(app, "get_ecg", {"ecg_id": ECG_ID, "include_waveform": True}))

    assert len(ecg["samples"]) == 3840


@pytest.mark.asyncio
async def test_no_ecg_recordings_is_said_plainly(app, client):
    client.get_ecg_list.return_value = {"content": [], "transformErrors": []}

    text = await _call(app, "get_ecg_recordings", {"start_date": "2026-11-01"})

    assert text == "No ECG recordings between 2026-11-01 and 2026-11-01."


@pytest.mark.asyncio
async def test_an_account_without_ecg_gets_a_plain_answer(app, client):
    from garminconnect import GarminConnectConnectionError

    client.get_ecg_list.side_effect = GarminConnectConnectionError("API Error 404")

    text = await _call(app, "get_ecg_recordings", {"start_date": "2026-11-01"})

    assert text.startswith("No ECG data")
