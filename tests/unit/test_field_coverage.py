"""scripts/field_coverage.py: which of Garmin's fields a tool leaves out.

The body battery cases are PR #61's: the curated summary kept the band
("HIGH") and dropped the level series. Values are synthetic.
"""
import asyncio
import datetime
import importlib.util
import pathlib
from unittest.mock import Mock

import pytest

from garmin_mcp.client_resolver import set_global_client

_PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "field_coverage.py"
_spec = importlib.util.spec_from_file_location("field_coverage", _PATH)
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)

BATTERY_REPLY = [{
    "date": "2026-10-30", "charged": 44, "drained": 2,
    "bodyBatteryValuesArray": [[1793332800000, 35], [1793343600000, 58],
                               [1793354400000, 84], [1793365200000, 77]],
    "bodyBatteryValueDescriptorDTOList": [
        {"bodyBatteryValueDescriptorIndex": 0, "bodyBatteryValueDescriptorKey": "timestamp"}],
    "bodyBatteryDynamicFeedbackEvent": {"bodyBatteryLevel": "HIGH",
                                        "feedbackLongType": "EARLY_MORNING_HIGH_GOOD_SLEEP"},
}]
BEFORE_PR_61 = [{"date": "2026-10-30", "charged": 44, "drained": 2, "events": [],
                 "current_feedback": None, "body_battery_level": "HIGH"}]
AFTER_PR_61 = [{"date": "2026-10-30", "charged": 44, "drained": 2,
                "latest_level": 77, "latest_level_time_gmt": "2026-10-30T13:00:00Z",
                "latest_level_time_local": "2026-10-30T09:00:00-04:00",
                "highest_level": 84, "lowest_level": 35, "level_band": "HIGH",
                "current_feedback_long": "EARLY_MORNING_HIGH_GOOD_SLEEP", "events": []}]


def _missing(reply, output, method="get_body_battery"):
    return {path.split(": ", 1)[1]: (seen, total)
            for path, seen, total in fc.missing_fields([(method, reply)], output)}


def test_it_finds_the_body_battery_level_the_curated_summary_dropped():
    missing = _missing(BATTERY_REPLY, BEFORE_PR_61)

    assert missing["[].bodyBatteryValuesArray[][1]"] == (0, 4)
    assert missing["[].bodyBatteryValuesArray[][0]"] == (0, 4)
    assert "[].bodyBatteryDynamicFeedbackEvent.feedbackLongType" in missing
    assert "[].bodyBatteryDynamicFeedbackEvent.bodyBatteryLevel" not in missing
    assert "[].charged" not in missing
    # A series' column names are not data.
    assert not [path for path in missing if "Descriptor" in path]


def test_after_the_fix_the_levels_show_as_summarised():
    missing = _missing(BATTERY_REPLY, AFTER_PR_61)

    assert missing["[].bodyBatteryValuesArray[][1]"] == (3, 4)  # latest, highest, lowest
    assert "[].bodyBatteryDynamicFeedbackEvent.feedbackLongType" not in missing


@pytest.mark.parametrize("raw, shown", [
    ("2026-10-30T04:10:55.0", "2026-10-30T04:10:55Z"),
    ("2026-10-30 04:10:55", "2026-10-30T00:10:55-04:00"),  # GMT text, shown local
    ("2026-10-30T00:10:55.0", "2026-10-30T00:10:55-04:00"),  # local text, shown local
    (1793333455000, "2026-10-30T04:10:55Z"),  # GMT epoch
    (1793333455000, "2026-10-30T00:10:55-04:00"),
])
def test_a_time_counts_in_any_zone_or_form(raw, shown):
    assert _missing({"eventStartTimeGmt": raw}, {"start_time": shown}) == {}


def test_a_unit_conversion_counts_when_the_keys_are_related():
    reply = {"durationInMilliseconds": 30420000, "totalDistanceMeters": 5000.0}
    output = {"duration_minutes": 507.0, "distance_km": 5.0}
    assert _missing(reply, output) == {}


def test_a_converted_number_under_an_unrelated_key_does_not_count():
    assert _missing({"sleepScoreSeconds": 1200}, {"floors_climbed": 20}) == {"sleepScoreSeconds": (0, 1)}


def test_small_numbers_and_booleans_need_a_related_key():
    reply = {"isPrimary": True, "lapCount": 3}
    assert set(_missing(reply, {"solar": True, "sets": 3})) == {"isPrimary", "lapCount"}
    assert _missing(reply, {"is_primary": True, "lap_count": 3}) == {}


def test_empty_values_are_not_reported():
    assert _missing({"notes": None, "name": "", "tags": []}, {}) == {}


def test_the_report_holds_names_and_counts_never_values():
    reply = {"2026-10-30": {"restingHeartRate": 48},
             "3412345678": {"serialNumber": "SN-ABC-123456"},
             "deviceName": "Forerunner 970"}
    missing = fc.missing_fields([("connectapi", reply)], {})
    report = fc.render({"date": "2026-10-30", "days": 7, "skipped": {},
                        "tools": {"some_tool": {"methods": ["connectapi"], "binary": [],
                                                "missing": missing}}})

    assert "{key}.restingHeartRate" in report
    assert "{key}.serialNumber" in report
    for value in ("48", "SN-ABC", "3412345678", "Forerunner", "2026-10-30T"):
        assert value not in report.split("\n", 1)[1]


def test_the_recorder_keeps_the_reply_as_garmin_sent_it():
    garmin = Mock()
    garmin.get_body_battery.return_value = [{"date": "2026-10-30", "charged": 44}]
    garmin.client.connectapi.return_value = {"courseId": 7}
    garmin.display_name = "someone"
    recorder = fc.RecordingClient(garmin)

    reply = recorder.get_body_battery("2026-10-30", "2026-10-30")
    reply[0]["charged"] = "labelled in place by the tool"
    recorder.client.connectapi("/course-service/course/7")

    assert recorder.calls == [
        ("get_body_battery", [{"date": "2026-10-30", "charged": 44}]),
        ("client.connectapi", {"courseId": 7}),
    ]
    assert recorder.display_name == "someone"


WRITE_PREFIXES = ("add_", "create_", "delete_", "remove_", "set_", "update_", "upload_",
                  "schedule_", "unschedule_", "reschedule_", "log_", "push_", "upsert_")


def test_a_run_reads_every_tool_and_never_writes():
    from tests.integration.test_units_times_sweep import _garmin_double

    garmin = _garmin_double()
    try:
        report = asyncio.run(fc.run(garmin, datetime.date(2026, 10, 27), 2))
    finally:
        set_global_client(None)

    called = {name.split(".")[0] for name, _, _ in garmin.mock_calls}
    assert not [name for name in called if name.startswith(WRITE_PREFIXES)]
    assert {"get_body_battery", "get_sleep_summary", "get_activity"} <= set(report["tools"])
    assert len(report["tools"]) + len(report["skipped"]) >= 100
    assert report["skipped"]["run_custom_health_report"].startswith("computes a report")
    text = fc.render(report)
    assert text.startswith("Field coverage, 2026-10-27 (2-day ranges):")


def test_an_id_argument_with_nothing_to_use_skips_its_tools():
    from tests.integration.test_units_times_sweep import _garmin_double

    garmin = _garmin_double()
    try:
        report = asyncio.run(fc.run(garmin, datetime.date(2026, 10, 27), 2,
                                    only={"get_coach_plan_details"}))
    finally:
        set_global_client(None)

    assert report["skipped"] == {
        "get_coach_plan_details": "needs plan_id (none found; pass it as a flag)"}
