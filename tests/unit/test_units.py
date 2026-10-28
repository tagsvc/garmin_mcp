"""Account-unit companions and pace parsing (garmin_mcp.units).

The metric fields are what every existing caller reads, so each test also
checks they survive untouched beside the companions.
"""
from unittest.mock import Mock

import pytest

from garmin_mcp.units import (
    account_unit_system,
    add_account_units,
    format_pace,
    parse_pace,
)

# The 2026-10-27 hill session, as get_activity curates it.
RUN = {
    "id": 24873123388,
    "type": "running",
    "distance_meters": 3494.780029296875,
    "duration_seconds": 1401.156005859375,
    "avg_speed_mps": 2.4942,
    "max_speed_mps": 4.189000129699707,
    "elevation_gain_meters": 44.0,
    "elevation_loss_meters": 30.0,
    "avg_stride_length_cm": 79.75,
}


def test_statute_us_adds_miles_pace_mph_and_feet_beside_each_metric_field():
    out = add_account_units(RUN, "statute_us")

    assert out["distance_miles"] == 2.17
    assert out["avg_speed_mph"] == 5.6
    assert out["avg_pace_per_mile"] == "10:45"
    assert out["max_pace_per_mile"] == "6:24"
    assert out["elevation_gain_feet"] == 144
    assert out["elevation_loss_feet"] == 98
    # The metric originals are untouched and the companion sits right after.
    for key, value in RUN.items():
        assert out[key] == value
    keys = list(out)
    assert keys.index("distance_miles") == keys.index("distance_meters") + 1
    assert keys.index("avg_pace_per_mile") == keys.index("avg_speed_mps") + 2


def test_metric_adds_km_and_pace_per_km_but_no_feet():
    out = add_account_units(RUN, "metric")

    assert out["distance_km"] == 3.49
    assert out["avg_speed_kmh"] == 9.0
    assert out["avg_pace_per_km"] == "6:41"
    assert not any(key.endswith(("_miles", "_feet", "_mph", "_per_mile")) for key in out)


def test_statute_uk_gets_miles_but_keeps_kilograms():
    out = add_account_units({**RUN, "weight_kg": 80.0}, "statute_uk")

    assert out["distance_miles"] == 2.17
    assert out["avg_pace_per_mile"] == "10:45"
    assert "weight_lb" not in out


@pytest.mark.parametrize("unit_system", [None, "", "imperial", Mock()])
def test_unknown_unit_system_returns_the_data_unchanged(unit_system):
    assert add_account_units(RUN, unit_system) is RUN


def test_input_is_not_modified():
    data = {"laps": [{"distance_meters": 1609.344}]}
    add_account_units(data, "statute_us")
    assert data == {"laps": [{"distance_meters": 1609.344}]}


def test_nested_dicts_and_lists_are_converted():
    out = add_account_units(
        {"activities": [{"type": "running", "distance_meters": 1609.344}]},
        "statute_us",
    )
    assert out["activities"][0]["distance_miles"] == 1.0


def test_existing_key_is_never_overwritten():
    # The existing key comes first, so a companion written after it would win.
    out = add_account_units({"distance_miles": "kept", "distance_meters": 1609.344}, "statute_us")
    assert out["distance_miles"] == "kept"
    out = add_account_units({"distance_meters": 1609.344, "distance_miles": "kept"}, "statute_us")
    assert out["distance_miles"] == "kept"


def test_swims_keep_meters_but_elevation_still_converts():
    swim = {"type": "lap_swimming", "distance_meters": 1000.0, "avg_speed_mps": 0.7,
            "elevation_gain_meters": 3.0}
    out = add_account_units(swim, "statute_us")

    assert "distance_miles" not in out
    assert "avg_pace_per_mile" not in out
    assert out["elevation_gain_feet"] == 10


def test_swim_flag_covers_payloads_that_name_no_sport():
    out = add_account_units({"laps": [{"distance_meters": 25.0}]}, "statute_us", swim=True)
    assert out == {"laps": [{"distance_meters": 25.0}]}


def test_weights_get_pounds_for_statute_us():
    out = add_account_units(
        {
            "weight_grams": 81234,
            "weight_kg": 81.23,
            "bone_mass_grams": 3400,
            "muscle_mass_grams": 36000,
            "average_weight_grams": 81000.0,
            "weight_change_kg": -1.5,
        },
        "statute_us",
    )
    assert out["weight_lb"] == 179.1
    assert out["bone_mass_lb"] == 7.5
    assert out["muscle_mass_lb"] == 79.4
    assert out["average_weight_lb"] == 178.6
    assert out["weight_change_lb"] == -3.3


def test_ratios_and_food_grams_are_not_weights():
    out = add_account_units(
        {"avg_w_per_kg": 3.1, "lean_mass_kcal_per_kg": 1800, "protein_grams": 20,
         "carbs_grams": 50},
        "statute_us",
    )
    assert out == {"avg_w_per_kg": 3.1, "lean_mass_kcal_per_kg": 1800, "protein_grams": 20,
                   "carbs_grams": 50}


def test_km_fields_get_miles_for_imperial_only():
    data = {"tolerance_km": 25.0, "acute_load_km": 10.0, "load_ratio": 1.2}
    assert add_account_units(data, "statute_us") == {
        "tolerance_km": 25.0, "tolerance_miles": 15.53,
        "acute_load_km": 10.0, "acute_load_miles": 6.21,
        "load_ratio": 1.2,
    }
    assert add_account_units(data, "metric") == data


def test_short_meter_suffix_and_altitudes():
    out = add_account_units(
        {"distance_m": 5000.0, "elevation_gain_m": 30.48, "altitude_m": 304.8,
         "total_ascent_m": 100.0, "progress_meters": 10.0},
        "statute_us",
    )
    assert out["distance_miles"] == 3.11
    assert out["elevation_gain_feet"] == 100
    assert out["altitude_feet"] == 1000
    assert out["total_ascent_feet"] == 328
    # Neither a distance nor a height by name: left alone rather than guessed.
    assert "progress_miles" not in out and "progress_feet" not in out


def test_bare_speed_key_and_threshold_speed():
    out = add_account_units(
        {"speed_mps": 3.889, "lactate_threshold_speed_mps": 3.889}, "metric"
    )
    assert out["speed_kmh"] == 14.0
    assert out["pace_per_km"] == "4:17"  # Garmin Connect shows 4:17 min/km
    assert out["lactate_threshold_pace_per_km"] == "4:17"


def test_raw_camelcase_split_fields():
    split = {
        "distance": 1245.35, "averageSpeed": 2.409, "averageMovingSpeed": 2.432,
        "maxSpeed": 2.631, "avgGradeAdjustedSpeed": 2.359, "elevationGain": 4.0,
        "startElevation": 185.6, "maxDistanceWithPrecision": 1245.35,
        # m/s, but vertical: a pace for it would be nonsense.
        "avgVerticalSpeed": -0.005,
    }
    out = add_account_units(split, "statute_us")

    assert out["distanceMiles"] == 0.77
    assert out["averagePacePerMile"] == "11:08"
    assert out["averageSpeedMph"] == 5.4
    assert out["averageMovingPacePerMile"] == "11:02"
    assert out["maxPacePerMile"] == "10:12"
    assert out["avgGradeAdjustedPacePerMile"] == "11:22"
    assert out["elevationGainFeet"] == 13
    assert out["startElevationFeet"] == 609
    assert out["maxDistanceWithPrecisionMiles"] == 0.77
    assert not any(key.startswith("avgVertical") and key != "avgVerticalSpeed" for key in out)


def test_raw_split_named_as_a_swim_by_its_type_keeps_meters():
    out = add_account_units({"type": "SWIM_INTERVAL", "distance": 50.0}, "statute_us")
    assert "distanceMiles" not in out


def test_standing_still_has_a_speed_but_no_pace():
    out = add_account_units({"avg_speed_mps": 0.0, "maxSpeed": 0}, "statute_us")
    assert out["avg_speed_mph"] == 0.0
    assert "avg_pace_per_mile" not in out
    assert "maxPacePerMile" not in out


def test_booleans_and_non_finite_values_are_skipped():
    out = add_account_units(
        {"distance_meters": True, "elevation_gain_meters": float("nan")}, "statute_us"
    )
    assert set(out) == {"distance_meters", "elevation_gain_meters"}


def test_curated_workout_step_gets_target_paces_and_end_distance():
    step = {
        "type": "interval",
        "end_condition": "distance",
        "end_condition_value": 804.672,
        "target_type": "pace.zone",
        "target_value_low": 2.777,
        "target_value_high": 2.472,
        "secondary_target_type": "pace.zone",
        "secondary_target_value_low": 3.0,
    }
    out = add_account_units(step, "statute_us")

    assert out["target_pace_low_per_mile"] == "9:40"
    assert out["target_pace_high_per_mile"] == "10:51"
    assert out["secondary_target_pace_low_per_mile"] == "8:56"
    assert out["end_condition_value_miles"] == 0.5
    assert add_account_units(step, "metric")["target_pace_low_per_km"] == "6:00"


def test_non_pace_targets_and_time_end_conditions_get_nothing():
    step = {"end_condition": "time", "end_condition_value": 600.0,
            "target_type": "heart.rate.zone", "target_value_low": 140,
            "target_value_high": 150}
    assert add_account_units(step, "statute_us") == step


def test_swim_workout_segment_gets_no_pace():
    workout = {"sport": "swimming", "segments": [{"steps": [
        {"target_type": "pace.zone", "target_value_low": 0.8,
         "end_condition": "distance", "end_condition_value": 100.0}]}]}
    assert add_account_units(workout, "statute_us") == workout


@pytest.mark.parametrize(
    "seconds, expected",
    [(641.94, "10:42"), (628.5, "10:29"), (58.5, "0:59"), (59.4, "0:59"), (3599.6, "1:00:00"),
     (3725, "1:02:05")],
)
def test_format_pace_rounds_half_up(seconds, expected):
    assert format_pace(seconds) == expected


@pytest.mark.parametrize(
    "text, expected_mps",
    [
        ("10:30/mi", 2.5545143),
        ("10:30 /mi", 2.5545143),
        ("10:30 min/mi", 2.5545143),
        ("10:30 mins/mile", 2.5545143),
        ("10:30 per mile", 2.5545143),
        ("10:30/MI", 2.5545143),
        ("5:00/km", 3.3333333),
        ("5:00 min/km", 3.3333333),
        ("5:00 per kilometre", 3.3333333),
        ("4:59.5/km", 3.3388982),
    ],
)
def test_parse_pace(text, expected_mps):
    assert parse_pace(text) == expected_mps


@pytest.mark.parametrize(
    "text", ["10:30", "10:30/", "10:30 per", "10:75/mi", "fast", "0:00/mi", "10:30/yd", "10/mi"]
)
def test_parse_pace_rejects_text_without_a_clear_pace_and_unit(text):
    with pytest.raises(ValueError, match="is not a pace"):
        parse_pace(text)


def test_parse_pace_round_trips_through_the_companion():
    out = add_account_units(
        {"target_type": "pace.zone", "target_value_low": parse_pace("10:30/mi")},
        "statute_us",
    )
    assert out["target_pace_low_per_mile"] == "10:30"


def test_account_unit_system():
    client = Mock()
    client.get_unit_system.return_value = "statute_us"
    assert account_unit_system(client) == "statute_us"

    client.get_unit_system.return_value = "furlongs"
    assert account_unit_system(client) is None

    client.get_unit_system.side_effect = RuntimeError("not logged in")
    assert account_unit_system(client) is None

    # A bare Mock, as most tool tests pass, must read as unknown.
    assert account_unit_system(Mock()) is None
