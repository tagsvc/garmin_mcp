"""The units/times contract guard itself (tests/units_times_contract.py).

The source scan below is the guard for code no test reaches; the output
checker runs on every tool call (tests/conftest.py) and in the sweep.
"""
import textwrap

from tests.units_times_contract import (
    UNLABELLED_TIME_KEYS,
    output_violations,
    result_json,
    source_time_key_violations,
)


def test_no_tool_module_emits_a_time_key_without_a_zone():
    """A bare start_time (GMT in one tool, local in another) must not come
    back; name it *_gmt or *_local, or add it to UNLABELLED_TIME_KEYS with
    the reason its zone is unknown."""
    assert source_time_key_violations() == []


def test_the_scan_catches_a_planted_bare_time_key(tmp_path):
    (tmp_path / "planted.py").write_text(textwrap.dedent('''
        def curate(event):
            out = {"start_time": event.get("eventStartTimeGmt"), "start_time_gmt": 1}
            out["last_sync_timestamp"] = 2
            out["duration_seconds"] = 3
            return out
    '''))
    found = source_time_key_violations(tmp_path)
    assert {entry.split(": ", 1)[1] for entry in found} == {"'start_time'", "'last_sync_timestamp'"}


def test_output_checker_flags_a_metric_field_without_its_companion():
    assert output_violations({"distance_meters": 1609.344}) == [
        "$.distance_meters: no distance_miles companion"
    ]
    assert output_violations({"distance_meters": 1609.344, "distance_miles": 1.0}) == []


def test_output_checker_flags_unlabelled_times_and_epochs():
    found = output_violations({"start_time": "2026-10-27T18:37:26.0", "sync": 1793126246000,
                               "activity_id": 1793126246000})
    assert found == [
        "$.start_time: time '2026-10-27T18:37:26.0' names no zone",
        "$.sync: epoch 1793126246000 names no zone",
    ]


def test_output_checker_accepts_labelled_and_self_describing_times():
    assert output_violations({
        "start_time_gmt": "2026-10-27T18:37:26Z",
        "start_time_local": "2026-10-27T14:37:26-04:00",
        "issued": "2026-10-27T17:51:00.000+00:00",
        "created": "2026-10-26 23:08:09.0 GMT",
        "date": "2026-10-27",
        "created_date": "2026-10-26T07:58:13.0",  # allowlisted: zone not established
    }) == []
    assert "created_date" in UNLABELLED_TIME_KEYS


def test_output_checker_respects_swims_and_raw_restatements():
    assert output_violations({"laps": [{"distance_meters": 45.72, "avg_swolf": 40}]}) == []
    assert output_violations({"type": "lap_swimming", "distance_meters": 1000.0}) == []
    assert output_violations({"date": 1793089231000, "measuredAtLocal": "2026-10-27T08:20:31-04:00"}) == []
    assert output_violations({"date": 1793089231000}) == ["$.date: epoch 1793089231000 names no zone"]


def test_output_checker_walks_nested_raw_payloads():
    assert output_violations({"splits": [{"averageTemperature": 25.0}]}) == [
        "$.splits[0].averageTemperature: no averageTemperatureFahrenheit companion"
    ]


def test_result_json_reads_both_call_tool_shapes():
    class Block:
        text = '{"a": 1}'

    assert result_json(([Block()], {})) == {"a": 1}   # structured output on
    assert result_json([Block()]) == {"a": 1}          # _ToolFilter: content list
    Block.text = "No data"
    assert result_json([Block()]) is None


def test_raw_gmt_times_need_a_local_time_or_an_offset():
    assert output_violations({"lastSyncTimestampGMT": "2026-10-28T21:18:19.470"}) == [
        "$.lastSyncTimestampGMT: GMT time with no local time beside it"
    ]
    assert output_violations({"lastSyncTimestampGMT": "2026-10-28T21:18:19.470",
                              "lastSyncTimestampLocal": "2026-10-28T17:18:19-04:00"}) == []
    # A payload's utcOffsetHours converts every GMT time beneath it.
    assert output_violations({"utcOffsetHours": -4.0,
                              "sleepLevels": [{"startGMT": "2026-10-28T03:17:13.0"}]}) == []
    # Body composition restates timestampGMT as measuredAtLocal.
    assert output_violations({"timestampGMT": 1793103631000,
                              "measuredAtLocal": "2026-10-27T08:20:31-04:00"}) == []
    # Curated *_gmt keys are already named; this rule is for Garmin's keys.
    assert output_violations({"start_time_gmt": "2026-10-27T18:37:26Z"}) == []
