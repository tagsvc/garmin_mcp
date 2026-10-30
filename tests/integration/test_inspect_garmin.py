"""inspect_garmin: Garmin's raw replies, and what a tool's output leaves out.

The body battery case is the one that prompted it: the curated summary kept
the band ("HIGH") and dropped the level series. Values are synthetic.
"""
import asyncio
import contextvars
import json
import threading
from unittest.mock import Mock

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import _GarminProxy, payload_recording, profile_settings, remote
from garmin_mcp.client_resolver import _ResolvingGarminProxy, get_client, set_global_client

D = "2026-10-30"
BATTERY = [{
    "date": D, "charged": 44, "drained": 2,
    "startTimestampGMT": "2026-10-30T04:00:00.0", "startTimestampLocal": "2026-10-30T00:00:00.0",
    "bodyBatteryValuesArray": [[1793332800000, 35], [1793365200000, 77]],
    "bodyBatteryDynamicFeedbackEvent": {"bodyBatteryLevel": "HIGH", "deviceId": 123,
                                        "feedbackLongType": "EARLY_MORNING_HIGH"},
    "hiddenLevelField": 63.5,
    # No offset of its own: the tool reads the profile's time zone to place it.
    "bodyBatteryActivityEvent": [{"eventType": "SLEEP", "eventStartTimeGmt": "2026-10-30T04:10:55.0"}],
}]


SUMMARY = {"calendarDate": D, "wellnessStartTimeGmt": "2026-10-30T04:00:00.0",
           "wellnessStartTimeLocal": "2026-10-30T00:00:00.0", "totalSteps": 9000}


@pytest.fixture
def garmin():
    client = Mock()
    client.get_unit_system.return_value = "statute_us"
    client.get_userprofile_settings.return_value = {"timeZone": "America/New_York"}
    # A fresh copy per call: tools label replies in place.
    client.get_body_battery.side_effect = lambda *_: json.loads(json.dumps(BATTERY))
    client.get_user_summary.side_effect = lambda *_: json.loads(json.dumps(SUMMARY))
    return client


@pytest.fixture
def app(garmin):
    set_global_client(garmin)
    for module in remote._CONFIGURED_MODULES:
        module.configure(garmin)
    app = FastMCP("inspect")
    remote.register_remote_tools(app)
    yield app
    set_global_client(None)


async def _inspect(app, **arguments):
    result = await app.call_tool("inspect_garmin", arguments)
    text = result[0][0].text if isinstance(result[0], (list, tuple)) else result[0].text
    try:
        return json.loads(text)
    except ValueError:
        return text


@pytest.mark.asyncio
async def test_it_returns_the_reply_garmin_sent_before_the_tool_relabels_it(app):
    data = await _inspect(app, tool="get_body_battery", arguments={"start_date": D, "end_date": D})

    assert data["tool"] == "get_body_battery"
    assert data["garmin_replies"] == [{"call": "get_body_battery", "reply": BATTERY}]
    # The profile settings call only helps label times; it is not the tool's data.
    assert all(r["call"] != "get_userprofile_settings" for r in data["garmin_replies"])


@pytest.mark.asyncio
async def test_a_reply_the_tool_labels_in_place_is_kept_as_garmin_sent_it(app, garmin):
    """get_user_summary gives Garmin's own time text its zone in place."""
    data = await _inspect(app, tool="get_user_summary", arguments={"date": D})

    # Only the tool's own data: get_unit_system, asked to convert units, is left out.
    assert [r["call"] for r in data["garmin_replies"]] == ["get_user_summary"]
    assert data["garmin_replies"][0]["reply"]["wellnessStartTimeGmt"] == "2026-10-30T04:00:00.0"
    labelled = json.loads((await app.call_tool("get_user_summary", {"date": D}))[0].text)
    assert labelled["wellnessStartTimeGmt"] == "2026-10-30T04:00:00Z"


@pytest.mark.asyncio
async def test_missing_lists_what_the_output_leaves_out_without_the_noise(app):
    data = await _inspect(app, tool="get_body_battery",
                          arguments={"start_date": D, "end_date": D}, missing=True)

    fields = {m["field"]: (m["shown"], m["of"]) for m in data["missing_fields"]}
    assert fields["get_body_battery: [].hiddenLevelField"] == (0, 1)
    # The level series is summarised (latest, highest, lowest), not dropped.
    assert "get_body_battery: [].bodyBatteryValuesArray[][1]" not in fields
    assert not [f for f in fields if f.endswith("deviceId")]
    assert data["noise_fields_hidden"] >= 1

    noisy = await _inspect(app, tool="get_body_battery", arguments={"start_date": D, "end_date": D},
                           missing=True, include_noise=True)
    assert any(m["field"].endswith("deviceId") for m in noisy["missing_fields"])


@pytest.mark.asyncio
async def test_find_and_fields_return_only_the_keys_asked_for(app):
    found = await _inspect(app, tool="get_body_battery",
                           arguments={"start_date": D, "end_date": D}, find="level")
    matches = found["garmin_replies"][0]["matches"]
    assert {m["path"] for m in matches} == {
        "[0].bodyBatteryDynamicFeedbackEvent.bodyBatteryLevel", "[0].hiddenLevelField"}

    picked = await _inspect(app, tool="get_body_battery",
                            arguments={"start_date": D, "end_date": D}, fields=["charged"])
    assert picked["garmin_replies"][0]["matches"] == [{"path": "[0].charged", "value": 44}]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["add_weigh_in", "delete_workout", "inspect_garmin", "no_such_tool"])
async def test_it_runs_only_read_only_tools_and_never_itself(app, garmin, tool):
    text = await _inspect(app, tool=tool, arguments={})

    assert isinstance(text, str)
    assert "read-only" in text or "No tool named" in text
    garmin.add_weigh_in.assert_not_called()
    garmin.delete_workout.assert_not_called()


@pytest.mark.asyncio
async def test_endpoint_mode_gets_one_garmin_path_with_its_params(app, garmin):
    garmin.connectapi.return_value = [{"brand": "ASICS", "model": "Gel-Cumulus 28"}]

    data = await _inspect(app, endpoint="/gear-service/gear/v2/list", params={"status": "ALL"})

    garmin.connectapi.assert_called_once_with("/gear-service/gear/v2/list", params={"status": "ALL"})
    assert data["garmin_replies"] == [
        {"call": "/gear-service/gear/v2/list", "reply": [{"brand": "ASICS", "model": "Gel-Cumulus 28"}]}]


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", [
    "https://evil.example/x", "gear-service/gear", "/gear-service/../oauth", "//evil.example/x",
    "/gear-service/gear?status=ALL", "/gear service",
])
async def test_endpoint_mode_refuses_anything_but_a_rooted_garmin_path(app, garmin, endpoint):
    text = await _inspect(app, endpoint=endpoint)

    assert "must be a Garmin Connect API path" in text
    garmin.connectapi.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{}, {"tool": "get_stats", "endpoint": "/x"}])
async def test_it_takes_exactly_one_of_tool_or_endpoint(app, arguments):
    assert "either tool" in await _inspect(app, **arguments)


@pytest.mark.asyncio
async def test_a_reply_too_big_to_return_is_summarised(app, garmin):
    garmin.connectapi.return_value = {"sleepMovement": [{"activityLevel": n} for n in range(5000)],
                                      "dailySleepDTO": {"sleepTimeSeconds": 29520}}

    data = await _inspect(app, endpoint="/wellness-service/wellness/dailySleepData/x")

    reply = data["garmin_replies"][0]["reply"]
    assert reply["truncated"] is True
    assert reply["top_level_keys"] == ["sleepMovement", "dailySleepDTO"]
    assert "narrow them with find" in data["note"]


# --- recording stays inside its own request -------------------------------------

def test_recording_covers_only_its_own_context():
    garmin = Mock()
    garmin.get_stats.return_value = {"totalSteps": 1}
    outside = []

    def elsewhere():
        # A concurrent request: its own context, not the one recording.
        outside.append(payload_recording.wrap_if_recording(garmin).get_stats("d"))

    with payload_recording.recording() as calls:
        payload_recording.wrap_if_recording(garmin).get_stats("d")
        thread = threading.Thread(target=elsewhere)
        thread.start()
        thread.join()
        copied = contextvars.copy_context()
        copied.run(lambda: payload_recording.wrap_if_recording(garmin).get_stats("d"))

    assert [name for name, _ in calls] == ["get_stats", "get_stats"]
    assert payload_recording.active_calls() is None
    payload_recording.wrap_if_recording(garmin).get_stats("d")  # recording is over
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_two_concurrent_recordings_do_not_mix():
    first, second = Mock(), Mock()
    first.get_stats.return_value = {"who": "first"}
    second.get_stats.return_value = {"who": "second"}

    async def inspect(client):
        with payload_recording.recording() as calls:
            await asyncio.sleep(0)
            payload_recording.wrap_if_recording(client).get_stats("d")
            await asyncio.sleep(0)
        return calls

    a, b = await asyncio.gather(inspect(first), inspect(second))

    assert a == [("get_stats", {"who": "first"})]
    assert b == [("get_stats", {"who": "second"})]


def test_each_call_is_recorded_once_whichever_way_a_tool_reaches_garmin():
    garmin = Mock()
    garmin.get_stats.return_value = {"totalSteps": 1}
    garmin.client.connectapi.return_value = {"courseId": 7}
    set_global_client(garmin)
    try:
        with payload_recording.recording() as calls:
            get_client().get_stats("d")                        # a tool's get_client(ctx)
            _ResolvingGarminProxy().get_stats("d")             # remote module global
            _GarminProxy(garmin, timeout=0).get_stats("d")     # stdio module global
            _GarminProxy(garmin, timeout=0).client.connectapi("/course-service/course/7")
    finally:
        set_global_client(None)

    assert [name for name, _ in calls] == [
        "get_stats", "get_stats", "get_stats", "client.connectapi"]


def test_the_recording_wrapper_is_never_a_settings_cache_key():
    garmin = Mock()
    garmin.get_userprofile_settings.return_value = {"timeZone": "America/New_York"}

    with payload_recording.recording():
        profile_settings.profile_settings(payload_recording.wrap_if_recording(garmin))
        profile_settings.profile_settings(payload_recording.wrap_if_recording(garmin))
    profile_settings.profile_settings(garmin)

    garmin.get_userprofile_settings.assert_called_once_with()
