"""Which of Garmin's fields a tool's output leaves out, and the replies' noise.

FORK: shared by scripts/field_coverage.py (every read-only tool, run locally)
and inspect_garmin (one tool, from a chat). A field counts as shown when its
value is in the output, by key or by value: times match in any zone or form,
common unit conversions count when the keys are related, and ids, owners,
images, coordinates and device capability flags are noise every tool omits on
purpose (``is_noise``).
"""

import datetime
import re
from collections import defaultdict
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

# Replies that only help a tool label units and times, not its data.
AUXILIARY_METHODS = {"get_userprofile_settings", "get_unit_system"}

# Unit conversions a tool may apply to a raw number: ms, s -> min, h; m -> km,
# mi, ft; g, kg -> lb; fraction -> percent; cm -> m.
FACTORS = (
    1 / 60, 1 / 3600, 1 / 60000, 1 / 3600000, 1 / 1000, 1000, 1 / 100, 100, 10, 1 / 10,
    1 / 1609.344, 1000 / 1609.344, 3.28084, 2.2046226, 0.0022046226,
)
# The same word, spelt two ways in Garmin's keys and ours.
SYNONYMS = {"maximum": "max", "minimum": "min", "average": "avg", "averages": "avg"}
# Words that say how a value is measured, not what it is.
UNIT_WORDS = {
    "in", "of", "value", "values", "gmt", "local", "ms", "milliseconds", "millis",
    "seconds", "secs", "sec", "minutes", "mins", "hours", "meters", "m", "km", "mi",
    "miles", "kg", "grams", "g", "lb", "lbs", "percent", "pct", "dto", "list", "array",
    "timestamp", "time", "date", "s", "mps", "kph", "mph", "celsius", "c", "f",
}
_EPOCH_MS = (10**12, 2.5 * 10**12)
_TIME_TEXT = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(\.\d+)?(Z|[+-]\d{2}:?\d{2}|\s?GMT|\s?UTC)?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATA_KEY = re.compile(
    r"^(\d{4}-\d{2}-\d{2}.*|-?\d+|[0-9a-f]{8}-[0-9a-f]{4}-.*|.*\d{6,}.*)$", re.IGNORECASE
)


# --- comparison --------------------------------------------------------------

def _words(key: str) -> Set[str]:
    parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", key)
    return {SYNONYMS.get(p.lower(), p.lower()) for p in parts} - UNIT_WORDS


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _instant_and_clock(value: Any) -> Tuple[Optional[str], Optional[str]]:
    """A time's UTC instant (when its zone is known or it is GMT) and wall clock."""
    if _number(value) and _EPOCH_MS[0] <= value <= _EPOCH_MS[1]:
        moment = datetime.datetime.fromtimestamp(value / 1000, datetime.timezone.utc)
        text = moment.strftime("%Y-%m-%dT%H:%M:%S")
        return text, text  # GMT epoch, or a local pseudo-epoch read as written
    if not isinstance(value, str):
        return None, None
    match = _TIME_TEXT.match(value.strip())
    if not match:
        return None, None
    day, clock, _, zone = match.groups()
    clock = clock if len(clock) == 8 else clock + ":00"
    wall = f"{day}T{clock}"
    if not zone or zone.strip() in ("Z", "GMT", "UTC"):
        return wall, wall
    sign = 1 if zone[0] == "+" else -1
    hours, minutes = int(zone[1:3]), int(zone[-2:])
    moment = datetime.datetime.fromisoformat(wall) - sign * datetime.timedelta(hours=hours, minutes=minutes)
    return moment.strftime("%Y-%m-%dT%H:%M:%S"), wall


def _decimals(value: float) -> int:
    text = repr(float(value))
    if "e" in text:
        return 6
    return min(len(text.split(".")[1].rstrip("0")), 6)


def _distinctive(value: Any) -> bool:
    """A value unlikely to match an unrelated field by chance."""
    if isinstance(value, bool):
        return False
    if _number(value):
        return not (float(value).is_integer() and abs(value) < 10)
    if isinstance(value, str):
        return len(value) > 3
    return False


def _leaves(node: Any, path: Tuple[str, ...] = ()) -> Iterator[Tuple[Tuple[str, ...], Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _leaves(value, path + (str(key),))
    elif isinstance(node, list):
        rows = bool(node) and all(isinstance(row, list) for row in node)
        for item in node:
            if rows:
                for column, cell in enumerate(item):
                    yield from _leaves(cell, path + ("[]", f"[{column}]"))
            else:
                yield from _leaves(item, path + ("[]",))
    elif node is not None and node != "":
        yield path, node


def _last_key(path: Sequence[str]) -> str:
    return next((part for part in reversed(path) if not part.startswith("[")), "")


class _Output:
    """A tool's output, indexed for matching raw values against it."""

    def __init__(self, data: Any):
        self.texts: Dict[str, Set[str]] = defaultdict(set)
        self.times: Dict[str, Set[str]] = defaultdict(set)
        self.numbers: Dict[int, Dict[float, Set[str]]] = defaultdict(lambda: defaultdict(set))
        self.bools: Dict[bool, Set[str]] = defaultdict(set)
        self.dates: Dict[str, Set[str]] = defaultdict(set)
        self.keys: Set[str] = set()
        for path, value in _leaves(data):
            key = _last_key(path)
            instant, clock = _instant_and_clock(value)
            if instant:
                self.times[instant].add(key)
                self.times[clock].add(key)
            if isinstance(value, bool):
                self.bools[value].add(key)
            elif _number(value):
                self.numbers[_decimals(value)][round(float(value), _decimals(value))].add(key)
            elif isinstance(value, str):
                self.texts[value].add(key)
                if _DATE.match(value):
                    self.dates[value].add(key)
        self.keys = {_norm(path[-1]) for path in _paths(data) if path}

    def holders(self, value: Any) -> List[Tuple[Set[str], bool]]:
        """Output keys holding the value: (keys, converted?) per way it matched."""
        found = []
        instant, clock = _instant_and_clock(value)
        if instant:
            for text in {instant, clock}:
                if text in self.times:
                    found.append((self.times[text], False))
                # A time the tool shows as its date alone, under a related key.
                if text[:10] in self.dates:
                    found.append((self.dates[text[:10]], True))
            return found
        if isinstance(value, bool):
            return [(self.bools[value], False)] if value in self.bools else []
        if isinstance(value, str):
            return [(self.texts[value], False)] if value in self.texts else []
        for factor in (1,) + FACTORS:
            target = float(value) * factor
            for places, values in self.numbers.items():
                keys = values.get(round(target, places))
                if keys and (places > 0 or abs(target) >= 0.5 or target == 0):
                    found.append((keys, factor != 1))
        return found


def _norm(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _paths(node: Any, path: Tuple[str, ...] = ()) -> Iterator[Tuple[str, ...]]:
    """Every key path in a tree, whatever its value."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield path + (str(key),)
            yield from _paths(value, path + (str(key),))
    elif isinstance(node, list):
        for item in node:
            yield from _paths(item, path)


def _surfaced(path: Tuple[str, ...], value: Any, output: _Output) -> bool:
    key = _last_key(path)
    if isinstance(value, str) and len(_norm(value)) > 1 and _norm(value) in output.keys:
        return True  # the tool turned the value into a key, as health status does
    words = _words(key)
    for keys, converted in output.holders(value):
        related = any(words & _words(held) for held in keys) or _norm(key) in {_norm(k) for k in keys}
        if related or (not converted and _distinctive(value)):
            return True
    return False


def _shown(path: Sequence[str]) -> str:
    """A path for the report: data-like keys hidden, list steps as []."""
    text = ""
    for part in path:
        if part.startswith("["):
            text += part
        else:
            text += ("." if text else "") + ("{key}" if _DATA_KEY.match(part) else part)
    return text or "(reply)"


# Fields every tool leaves out on purpose: ids, who owns a record, pictures,
# coordinates, Garmin's internal flags and versions, and the ~300 device
# capability flags. The report hides them unless --all.
_NOISE_KEY = re.compile(
    r"Capable$|Id$|ID$|Pk$|PK$|Uuid$|UUID$|^(id|uuid|pk)$|[Ii]mage|[Tt]humbnail|[Uu]rl$|URL$"
    r"|[Ll]atitude$|[Ll]ongitude$|^owner|^userProfile|^user(Id|Pk|Pro|Roles)$"
    r"|^(display|full|first|last)[Nn]ame$|^profileImg|^vivokidUser$"
    r"|^(sortOrder|displayOrder|isHidden|restricted|trimmable|statusCode|successful|messageIndex)$"
    r"|^min\w+Version$|^(respiration|sleep|bodyBattery|respirationAlgorithm)Version$"
)
_NOISE_BLOCKS = {"author", "userInfoDto", "capabilities"}


def is_noise(field: str) -> bool:
    """A reported field ("method: a.b[].c") of a kind every tool omits on purpose."""
    parts = [p for p in re.split(r"[.\[\]]+", field.split(": ", 1)[-1]) if p and not p.isdigit()]
    return bool(parts) and (bool(_NOISE_KEY.search(parts[-1])) or bool(_NOISE_BLOCKS & set(parts)))


def missing_fields(replies: Sequence[Tuple[str, Any]], output: Any) -> List[Tuple[str, int, int]]:
    """(path, values surfaced, values in all) for each raw field the output lacks.

    A field is listed when some of its values appear nowhere in the output;
    a series the tool only summarises shows as surfaced < all.
    """
    index = _Output(output)
    counts: Dict[str, List[int]] = {}
    for method, reply in replies:
        for path, value in _leaves(reply):
            if any("Descriptor" in part for part in path):
                continue  # a series' column names, not data
            shown = f"{method}: {_shown(path)}"
            tally = counts.setdefault(shown, [0, 0])
            tally[1] += 1
            tally[0] += _surfaced(path, value, index)
    return sorted((path, seen, total) for path, (seen, total) in counts.items() if seen < total)
