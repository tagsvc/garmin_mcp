"""Account-unit companions for the metric values Garmin returns.

FORK: Garmin's API reports distance in meters, speed in m/s, elevation in
meters and weight in grams or kilograms whatever the account's display
setting; only the activity weather endpoint follows the setting. Tools keep
those metric fields exactly as they were and add a converted companion beside
each one, in the account's own units, so a reader can quote miles or a
min/mile pace instead of converting (and converting wrongly).

A ``statute_us`` account gets miles, feet, mph, min/mile pace, pounds,
inches and degrees Fahrenheit. A ``metric`` account gets km, km/h and min/km
pace; its elevation, weight and temperature are already in its units.
``statute_uk`` gets miles, feet, mph, min/mile pace and inches but keeps
kilograms and Celsius, since this module does not guess between pounds and
stone. Temperature differences (a range, a deviation) convert by x1.8 with
no +32. An unknown unit system adds nothing, so the output is what it was
before this module existed.

Swims keep meters: Garmin shows pool distances in the pool's own units and
swim pace per 100, neither of which a per-mile or per-km companion matches.

This module also parses the other direction, pace text such as ``10:30/mi``
into the m/s Garmin's workout targets take (``parse_pace``).
"""

import math
import re
from typing import Any, Iterator, Optional, Tuple

METERS_PER_MILE = 1609.344
METERS_PER_KM = 1000.0
METERS_PER_FOOT = 0.3048
KG_PER_POUND = 0.45359237

STATUTE_US = "statute_us"
STATUTE_UK = "statute_uk"
METRIC = "metric"
_KNOWN_UNIT_SYSTEMS = frozenset({STATUTE_US, STATUTE_UK, METRIC})

_HEIGHT_WORDS = ("elevation", "altitude", "ascent", "descent", "ascended", "descended")
_MASS_WORDS = ("weight", "mass")
# Keys whose string value names the sport of the dict they sit in.
_SPORT_KEYS = ("type", "activity_type", "activity", "sport", "splitType")

# Garmin's own camelCase keys, for the splits tools that return its payload
# as-is. Exact names only: a raw key is converted when it is known to be
# meters or m/s, never because its name looks like it might be.
_RAW_DISTANCE_KEYS = frozenset({
    "distance", "totalDistance", "maxDistanceWithPrecision", "totalDistanceMeters",
    "maximumMeters",
})
_RAW_HEIGHT_KEYS = frozenset({
    "elevationGain", "elevationLoss", "maxElevationGain", "averageElevationGain",
    "startElevation", "maxElevation", "minElevation", "floorsAscendedInMeters",
    "floorsDescendedInMeters",
})
_RAW_SPEED_KEYS = frozenset({
    "averageSpeed", "averageMovingSpeed", "maxSpeed", "avgSpeed", "avgGradeAdjustedSpeed",
})
# The watch's own temperature sensor, in degrees Celsius with no unit in the
# key (typed splits: 25 against the wrist on a 63 F day).
_RAW_TEMPERATURE_KEYS = frozenset({"averageTemperature", "maxTemperature", "minTemperature"})
# Centimeters, unlabelled (beside avgStepLength, which is in meters).
_RAW_CENTIMETER_KEYS = frozenset({"strideLength", "verticalOscillation"})
# A temperature key whose stem names a difference is a delta: x1.8, no +32.
_DELTA_WORDS = ("range", "change", "delta", "deviation", "drift")


def account_unit_system(client: Any) -> Optional[str]:
    """The account's unit system, or None when it is not one this module knows.

    garminconnect reads ``measurementSystem`` from the user settings at login
    and caches it, so this makes no API call.
    """
    try:
        unit_system = client.get_unit_system()
    except Exception:
        return None
    return unit_system if unit_system in _KNOWN_UNIT_SYSTEMS else None


def format_duration(seconds: float) -> str:
    """Format seconds as m:ss, or h:mm:ss past an hour."""
    # Half up, not Python's half-to-even: 10:29.5 reads as 10:30.
    total = math.floor(seconds + 0.5)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_hours(hours: float) -> str:
    """Decimal hours as h:mm (6.37 -> "6:22"), rounded half up to the minute."""
    minutes = math.floor(abs(hours) * 60 + 0.5)
    sign = "-" if hours < 0 and minutes else ""
    return f"{sign}{minutes // 60}:{minutes % 60:02d}"


def format_pace(seconds: float) -> str:
    """Format seconds per unit distance as m:ss, or h:mm:ss past an hour."""
    return format_duration(seconds)


def pace(speed_mps: float, unit_meters: float) -> Optional[str]:
    """Pace for one unit distance at speed_mps, or None for no movement."""
    if speed_mps <= 0:
        return None
    return format_pace(unit_meters / speed_mps)


_PACE_PATTERN = re.compile(
    r"""^\s*(?P<minutes>\d{1,3}):(?P<seconds>[0-5]\d(?:\.\d+)?)\s*
        (?:min(?:ute)?s?\.?\s*)?(?:/|per\s+)\s*
        (?P<unit>mi|miles?|km|kms|kilomet(?:er|re)s?)\s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def parse_pace(text: str) -> float:
    """Convert pace text such as ``10:30/mi`` or ``5:00 min/km`` to m/s.

    The unit is required: a bare ``10:30`` is a per-mile pace to one runner
    and a per-km pace to another, and guessing wrong uploads a target that is
    a minute and a half a mile off.
    """
    match = _PACE_PATTERN.match(text)
    if not match:
        raise ValueError(
            f"{text!r} is not a pace; write it as minutes:seconds per mile or "
            f"km, e.g. '10:30/mi' or '5:00/km'"
        )
    seconds = int(match["minutes"]) * 60 + float(match["seconds"])
    if seconds <= 0:
        raise ValueError(f"{text!r} is not a pace; it must be slower than 0:00")
    unit = match["unit"].lower()
    unit_meters = METERS_PER_MILE if unit.startswith("mi") else METERS_PER_KM
    return round(unit_meters / seconds, 7)


def add_gram_masses(payload: Any, keys: Tuple[str, ...], unit_system: Optional[str]) -> Any:
    """Pounds beside Garmin raw mass keys that are bare grams (``weight``: 79900.0).

    Only for the payloads where those keys are known to be grams: the same
    name holds kilograms elsewhere (73.0 in the FTP payload), so this is
    never applied by key name alone.
    """
    if unit_system != STATUTE_US or not isinstance(payload, dict):
        return payload
    for key in keys:
        value = payload.get(key)
        if _is_number(value) and value > 0 and f"{key}Lb" not in payload:
            payload[f"{key}Lb"] = round(value / 1000 / KG_PER_POUND, 1)
    return payload


def add_account_units(data: Any, unit_system: Optional[str], *, swim: bool = False) -> Any:
    """Return data with account-unit companions beside its metric fields.

    Works on curated snake_case output (``distance_meters``, ``avg_speed_mps``,
    ``weight_kg``...) and on Garmin's raw camelCase split payloads. Returns a
    new structure; data is not modified. A companion never replaces a key the
    data already has.

    Args:
        data: A dict or list from a tool, before json.dumps.
        unit_system: From account_unit_system(); None adds nothing.
        swim: Treat everything as a swim (no distance or pace companions),
              for payloads such as laps that do not name their sport.
    """
    if unit_system not in _KNOWN_UNIT_SYSTEMS:
        return data
    return _walk(data, unit_system, swim)


def _walk(node: Any, unit_system: str, swim: bool) -> Any:
    if isinstance(node, list):
        return [_walk(item, unit_system, swim) for item in node]
    if not isinstance(node, dict):
        return node

    swim = swim or _names_a_swim(node)
    out = {}
    for key, value in node.items():
        out[key] = _walk(value, unit_system, swim)
        if not isinstance(key, str) or not _is_number(value):
            continue
        for companion, converted in _companions(key, value, unit_system, swim):
            if companion not in node and companion not in out and converted is not None:
                out[companion] = converted
    if not swim:
        _add_workout_step_companions(node, out, unit_system)
    return out


def _names_a_swim(node: dict) -> bool:
    return any(
        isinstance(node.get(key), str) and "swim" in node[key].lower()
        for key in _SPORT_KEYS
    )


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def is_imperial(unit_system: Optional[str]) -> bool:
    """True for the two statute systems, which show distance in miles."""
    return unit_system in (STATUTE_US, STATUTE_UK)


def _distance(stem: str, meters: float, unit_system: str, camel: bool) -> Tuple[str, float]:
    if is_imperial(unit_system):
        return (f"{stem}Miles" if camel else f"{stem}_miles"), round(meters / METERS_PER_MILE, 2)
    return (f"{stem}Km" if camel else f"{stem}_km"), round(meters / METERS_PER_KM, 2)


def _speed(stem: str, mps: float, unit_system: str, camel: bool) -> Iterator[Tuple[str, Any]]:
    """Speed and pace companions; stem is the key minus its speed suffix."""
    if is_imperial(unit_system):
        speed_unit, pace_unit, unit_meters = ("Mph", "PerMile", METERS_PER_MILE)
    else:
        speed_unit, pace_unit, unit_meters = ("Kmh", "PerKm", METERS_PER_KM)
    if camel:
        yield f"{stem}Speed{speed_unit}", round(mps * 3600 / unit_meters, 1)
        yield f"{stem}Pace{pace_unit}", pace(mps, unit_meters)
    else:
        prefix = f"{stem}_" if stem else ""
        yield f"{prefix}speed_{speed_unit.lower()}", round(mps * 3600 / unit_meters, 1)
        yield f"{prefix}pace_{_snake(pace_unit)}", pace(mps, unit_meters)


def _snake(camel: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", camel).lower()


def _raw_stem(key: str) -> str:
    """totalDistanceMeters -> totalDistance, floorsAscendedInMeters -> floorsAscended."""
    for suffix in ("InMeters", "Meters"):
        if key.endswith(suffix) and len(key) > len(suffix):
            return key[: -len(suffix)]
    return key


def _fahrenheit(celsius: float, delta: bool) -> float:
    return round(celsius * 9 / 5 + (0 if delta else 32), 1)


def _companions(key: str, value: float, unit_system: str, swim: bool) -> Iterator[Tuple[str, Any]]:
    """Yield (key, value) companions for one numeric field."""
    # Garmin's raw camelCase payloads.
    if key in _RAW_HEIGHT_KEYS:
        if is_imperial(unit_system):
            yield f"{_raw_stem(key)}Feet", round(value / METERS_PER_FOOT)
        return
    if key in _RAW_DISTANCE_KEYS:
        if not swim:
            yield _distance(_raw_stem(key), value, unit_system, camel=True)
        return
    if key in _RAW_TEMPERATURE_KEYS:
        # Fahrenheit is the US unit; statute_uk shows Celsius.
        if unit_system == STATUTE_US:
            yield f"{key}Fahrenheit", _fahrenheit(value, delta=False)
        return
    if key in _RAW_CENTIMETER_KEYS:
        if is_imperial(unit_system):
            yield f"{key}Inches", round(value / 2.54, 1)
        return
    if key in _RAW_SPEED_KEYS:
        if not swim:
            yield from _speed(key[: -len("Speed")], value, unit_system, camel=True)
        return

    # Curated snake_case output, recognised by its unit suffix.
    if key == "speed_mps" or key.endswith("_speed_mps"):
        if not swim:
            yield from _speed(key[: -len("speed_mps")].rstrip("_"), value, unit_system, camel=False)
        return
    for suffix in ("_meters", "_m"):
        if key.endswith(suffix):
            stem = key[: -len(suffix)]
            if any(word in stem for word in _HEIGHT_WORDS):
                if is_imperial(unit_system):
                    yield f"{stem}_feet", round(value / METERS_PER_FOOT)
            elif "distance" in stem and not swim:
                yield _distance(stem, value, unit_system, camel=False)
            return
    if key.endswith("_km"):
        # Already metric units; imperial accounts get miles.
        if is_imperial(unit_system) and not swim:
            yield f"{key[:-3]}_miles", round(value * METERS_PER_KM / METERS_PER_MILE, 2)
        return
    for suffix, unit in (("_celsius", "_fahrenheit"), ("_c", "_f")):
        if key.endswith(suffix):
            stem = key[: -len(suffix)]
            if "temp" in stem and unit_system == STATUTE_US:
                delta = any(word in stem for word in _DELTA_WORDS)
                yield f"{stem}{unit}", _fahrenheit(value, delta)
            return
    if key.endswith("_cm"):
        if is_imperial(unit_system):
            yield f"{key[:-3]}_in", round(value / 2.54, 1)
        return
    if key.endswith("_hours"):
        # Decimal hours: 6.37 is 6 h 22 min, not 6:37.
        yield f"{key[:-6]}_hm", format_hours(value)
        return
    for suffix, kg_per_unit in (("_kg", 1.0), ("_grams", 0.001)):
        if key.endswith(suffix):
            stem = key[: -len(suffix)]
            if (
                unit_system == STATUTE_US
                and "per" not in stem.split("_")
                and any(word in stem for word in _MASS_WORDS)
            ):
                yield f"{stem}_lb", round(value * kg_per_unit / KG_PER_POUND, 1)
            return


def _add_workout_step_companions(node: dict, out: dict, unit_system: str) -> None:
    """Pace for a curated workout step's pace target, distance for its end.

    A curated step carries ``target_type: pace.zone`` with bare
    ``target_value_low``/``target_value_high`` in m/s (and the same under a
    ``secondary_`` prefix), and a distance end condition as a bare
    ``end_condition_value`` in meters, so the suffix rules cannot see them.
    """
    for prefix in ("", "secondary_"):
        if node.get(f"{prefix}target_type") != "pace.zone":
            continue
        for bound in ("low", "high"):
            value = node.get(f"{prefix}target_value_{bound}")
            if not _is_number(value):
                continue
            unit_meters = METERS_PER_MILE if is_imperial(unit_system) else METERS_PER_KM
            per = "per_mile" if is_imperial(unit_system) else "per_km"
            companion = f"{prefix}target_pace_{bound}_{per}"
            converted = pace(value, unit_meters)
            if companion not in node and converted is not None:
                out[companion] = converted

    value = node.get("end_condition_value")
    if node.get("end_condition") == "distance" and _is_number(value):
        companion, converted = _distance("end_condition_value", value, unit_system, camel=False)
        if companion not in node:
            out[companion] = converted
