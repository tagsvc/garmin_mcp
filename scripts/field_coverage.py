"""Which of Garmin's fields each read-only tool leaves out of its output.

FORK: the curated get_body_battery kept Garmin's word for the level ("HIGH")
and dropped the number (PR #61); a reader concluded the number did not
exist. Upstream's curation (f060072, "Improve all MCP tools with curated
responses") rebuilt about 40 tools the same way, and a curated output cannot
show what it omits. This script can.

It logs in with your saved tokens (run ``garmin-mcp-auth`` first if needed),
calls every read-only tool once, records each reply Garmin sent the tool,
and lists the reply's fields that appear nowhere in the tool's output, by
key or by value (times in any zone, and common unit conversions, count).

The report holds field names and counts only: no values, and any key that
looks like data (a date, an id, a serial) is printed as ``{key}``. It never
calls a tool that writes.

    uv run python scripts/field_coverage.py                 # yesterday, 7-day ranges
    uv run python scripts/field_coverage.py --date 2026-10-29 --days 7
    uv run python scripts/field_coverage.py --activity-id 123 --output report.txt
"""

import argparse
import asyncio
import copy
import datetime
import json
import re
import sys
import time
from collections import defaultdict
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

# Replies that only help a tool label units and times, not its data.
AUXILIARY_METHODS = {"get_userprofile_settings", "get_unit_system"}

# Modules whose tools compute a report from many replies rather than present
# one; nearly every raw field is "left out" by design. --include-derived runs them.
DERIVED_MODULES = {"garmin_mcp.analytics"}

# Values for the required arguments that need no lookup.
FIXED_ARGUMENTS = {"metric": "distance", "query": "banana"}

# Required id arguments: the tool that lists them, its arguments, and the
# keys that may hold the id in its output.
ID_SOURCES = {
    "activity_id": ("get_activities", {"limit": 1}, ("activityId", "activity_id", "id")),
    "workout_id": ("get_workouts", {}, ("workoutId", "workout_id", "id")),
    "course_id": ("get_courses", {}, ("courseId", "course_id", "id")),
    "device_id": ("get_devices", {}, ("deviceId", "device_id")),
    "gear_uuid": ("get_gear", {"include_stats": False}, ("uuid", "gear_uuid", "gearUuid")),
    "plan_id": ("get_coach_plans", {}, ("trainingPlanId", "plan_id", "planId", "id")),
}

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


# --- recording client --------------------------------------------------------

class RecordingClient:
    """The Garmin client, keeping a copy of every reply a tool receives."""

    def __init__(self, client: Any, calls: Optional[list] = None, prefix: str = ""):
        self._client = client
        self.calls = calls if calls is not None else []
        self._prefix = prefix

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._client, name)
        if name == "client" and not self._prefix:
            # garminconnect's HTTP client: courses call its connectapi directly.
            return RecordingClient(attribute, self.calls, "client.")
        if not callable(attribute):
            return attribute

        def call(*args: Any, **kwargs: Any) -> Any:
            reply = attribute(*args, **kwargs)
            self.calls.append((self._prefix + name, copy.deepcopy(reply)))
            return reply

        return call


# --- running the tools -------------------------------------------------------

def _build_app(client: Any) -> Any:
    from mcp.server.fastmcp import FastMCP

    from garmin_mcp import remote
    from garmin_mcp.client_resolver import set_global_client

    set_global_client(client)
    for module in remote._CONFIGURED_MODULES:
        module.configure(client)
    app = FastMCP("field-coverage")
    remote.register_remote_tools(app)
    return app


def _json(result: Any) -> Any:
    try:
        first = result[0]
        block = first[0] if isinstance(first, (list, tuple)) else first
        return json.loads(block.text)
    except (IndexError, KeyError, TypeError, AttributeError, ValueError):
        return None


def _text(result: Any) -> str:
    try:
        first = result[0]
        block = first[0] if isinstance(first, (list, tuple)) else first
        return str(block.text)
    except (IndexError, KeyError, TypeError, AttributeError):
        return ""


def _first(node: Any, keys: Sequence[str]) -> Any:
    if isinstance(node, dict):
        for key in keys:
            if node.get(key) not in (None, ""):
                return node[key]
        for value in node.values():
            found = _first(value, keys)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _first(item, keys)
            if found is not None:
                return found
    return None


def _why_not_json(text: str) -> str:
    if text.startswith("No ") or "not found" in text.lower()[:80]:
        return "no data for these dates"
    status = re.search(r"\b([45]\d\d)\b", text)
    return f"error (HTTP {status.group(1)})" if status else "returned text, not JSON"


async def run(
    client: Any,
    day: datetime.date,
    days: int,
    ids: Optional[Dict[str, Any]] = None,
    include_derived: bool = False,
    pause: float = 0.0,
    only: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    """Run every read-only tool once and collect what each leaves out."""
    recorder = RecordingClient(client)
    app = _build_app(recorder)
    tools = {t.name: t for t in await app.list_tools() if t.annotations and t.annotations.readOnlyHint}
    modules = {name: app._tool_manager.get_tool(name).fn.__module__ for name in tools}

    values: Dict[str, Any] = {
        "date": day.isoformat(),
        "calendar_date": day.isoformat(),
        "start_date": (day - datetime.timedelta(days=days - 1)).isoformat(),
        "end_date": day.isoformat(),
        **FIXED_ARGUMENTS,
        **{k: v for k, v in (ids or {}).items() if v is not None},
    }
    report: Dict[str, Any] = {"date": day.isoformat(), "days": days, "tools": {}, "skipped": {}}

    async def resolve(name: str) -> Any:
        if name not in values and name in ID_SOURCES:
            tool, arguments, keys = ID_SOURCES[name]
            try:
                values[name] = _first(_json(await app.call_tool(tool, arguments)), keys)
            except Exception:  # noqa: BLE001 - a missing id only skips its tools
                values[name] = None
        return values.get(name)

    for name in sorted(tools):
        if only and name not in only:
            continue
        if modules[name] in DERIVED_MODULES and not include_derived:
            report["skipped"][name] = "computes a report from many replies (--include-derived)"
            continue
        arguments = {}
        for parameter in tools[name].inputSchema.get("required", []):
            arguments[parameter] = await resolve(parameter)
        unresolved = [p for p, v in arguments.items() if v is None]
        if unresolved:
            report["skipped"][name] = f"needs {', '.join(unresolved)} (none found; pass it as a flag)"
            continue

        recorder.calls.clear()
        try:
            result = await app.call_tool(name, arguments)
        except Exception as error:  # noqa: BLE001
            report["skipped"][name] = f"raised {type(error).__name__}"
            continue
        output = _json(result)
        if output is None:
            report["skipped"][name] = _why_not_json(_text(result))
            continue
        replies = [
            (method, reply) for method, reply in recorder.calls
            if method.split(".")[-1] not in AUXILIARY_METHODS or method == name
        ]
        binary = [m for m, r in replies if isinstance(r, (bytes, bytearray, str))]
        replies = [(m, r) for m, r in replies if isinstance(r, (dict, list, int, float))]
        report["tools"][name] = {
            "methods": sorted({m for m, _ in replies} | set(binary)),
            "binary": sorted(set(binary)),
            "missing": missing_fields(replies, output),
        }
        if pause:
            await asyncio.sleep(pause)
    return report


def render(report: Dict[str, Any], show_all: bool = False) -> str:
    """The report as text: field names and counts only."""
    tools = report["tools"]
    shown = {n: [f for f in t["missing"] if show_all or not is_noise(f[0])] for n, t in tools.items()}
    hidden = sum(len(t["missing"]) - len(shown[n]) for n, t in tools.items())
    lacking = {n: dict(tools[n], missing=shown[n]) for n in tools if shown[n]}
    complete = sorted(n for n, t in tools.items() if not shown[n] and t["methods"])
    no_calls = sorted(n for n, t in tools.items() if not t["methods"])
    lines = [
        f"Field coverage, {report['date']} ({report['days']}-day ranges): "
        f"{len(tools)} tools compared, {len(lacking)} leave fields out, "
        f"{len(report['skipped'])} skipped.",
        "A field marked 'some' is in the output for only part of its values,",
        "as when a tool summarises a series.",
    ]
    if hidden:
        lines.append(f"{hidden} fields every tool omits on purpose (ids, owners, images, coordinates, "
                     "device capability flags) are hidden; --all shows them.")
    lines.append("")
    for name in sorted(lacking, key=lambda n: (-len(lacking[n]["missing"]), n)):
        entry = lacking[name]
        lines.append(f"## {name}  ({len(entry['missing'])} fields; from {', '.join(entry['methods'])})")
        for path, seen, total in entry["missing"]:
            note = "" if seen == 0 else f"  [some: {seen} of {total}]"
            lines.append(f"  {path}{note}")
        if entry["binary"]:
            lines.append(f"  (not compared: {', '.join(entry['binary'])} sent a file)")
        lines.append("")
    if complete:
        lines += ["## Nothing left out", "  " + ", ".join(complete), ""]
    if no_calls:
        lines += ["## No Garmin reply to compare", "  " + ", ".join(no_calls), ""]
    if report["skipped"]:
        lines.append("## Skipped")
        lines += [f"  {name}: {why}" for name, why in sorted(report["skipped"].items())]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--date", type=datetime.date.fromisoformat,
                        default=datetime.date.today() - datetime.timedelta(days=1),
                        help="the day to read (default: yesterday)")
    parser.add_argument("--days", type=int, default=7, help="length of date ranges (default 7)")
    for name in ID_SOURCES:
        parser.add_argument(f"--{name.replace('_', '-')}", dest=name,
                            help=f"{name} to use (default: the first one found)")
    parser.add_argument("--tool", action="append", help="run only this tool (repeatable)")
    parser.add_argument("--include-derived", action="store_true",
                        help="also run the analytics tools")
    parser.add_argument("--pause", type=float, default=0.5,
                        help="seconds between tools, to stay under Garmin's rate limit")
    parser.add_argument("--output", help="also write the report to this file")
    parser.add_argument("--all", action="store_true",
                        help="also list ids, owners, images, coordinates and device capability flags")
    args = parser.parse_args(argv)

    from garmin_mcp import init_api

    client = init_api(None, None)
    if client is None:
        return 1
    ids = {name: getattr(args, name) for name in ID_SOURCES}
    started = time.monotonic()
    report = asyncio.run(run(client, args.date, args.days, ids, args.include_derived,
                             args.pause, set(args.tool) if args.tool else None))
    text = render(report, args.all)
    print(text)
    if args.output:
        with open(args.output, "w") as handle:
            handle.write(text)
    print(f"({time.monotonic() - started:.0f}s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
