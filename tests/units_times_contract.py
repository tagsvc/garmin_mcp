"""The account-units and explicit-times contract, checked on tool output.

FORK: a CI guard behind the 2026-10-28 audit, which found metric fields with
no companion in the account's units and times with no zone across dozens of
tools. Two checks keep that from coming back:

- ``output_violations`` walks a tool's JSON output for a statute_us account
  and reports each metric field missing a companion the converter would add
  (the converter's own rules decide, so the two cannot drift apart), and
  each curated time -- ISO text or an epoch -- whose key names no zone.
  tests/conftest.py runs it on every tool call the suite makes for a US
  account.
- ``source_time_key_violations`` reads every tool module and reports curated
  keys that look like times but say neither ``_gmt`` nor ``_local``, so a
  bare ``start_time`` cannot come back even in code no test reaches.

Exceptions live in one place, each with its reason.
"""

import ast
import json
import pathlib
import re
from typing import Any, Iterator, List

from garmin_mcp import units

# Curated keys allowed to hold a time without naming its zone, and why.
UNLABELLED_TIME_KEYS = {
    # Zone not established from live data (audit, 2026-10-28); left as Garmin
    # sends them rather than labelled with a guess.
    "created_date": "workout-service createdDate: zone not established",
    "updated_date": "workout-service updatedDate: zone not established",
    "last_connected": "sensor lastConnected: zone not established",
    "speed_hr_date": "lactate threshold calendarDate stamp: zone not established",
    "power_date": "FTP calendarDate stamp: zone not established",
    "calendar_date": "get_cycling_ftp calendarDate stamp (date and time): zone not established",
    "last_updated": "fitness age lastUpdated: zone not established",
    "earned_date": "badge earned date: date only, from Garmin's stamp",
    # Documented in the tool: device-local, the same clock as start_time_local.
    "performed_at": "device-local by FORK.md invariant (performed_at)",
    "last_sync_time": "get_devices lastSyncTime: never seen in a live payload, zone unknown",
}

# Raw Garmin keys holding an unlabelled time that the tool restates, with its
# zone, under the sibling named here; the raw value stays for fidelity.
RAW_RESTATED_BY = {
    "date": "measuredAtLocal",  # body composition: local pseudo-epoch
}

# Garmin raw keys allowed to keep zoneless time text, and why.
RAW_UNZONED = {
    "calendarDate": "a calendar date written as local midnight, not an instant",
    "createDate": "gear service createDate: zone not established",
    "updateDate": "gear service updateDate: zone not established",
    "dateBegin": "gear service dateBegin: a date written as midnight",
    "dateEnd": "gear service dateEnd: a date written as midnight",
}

# Keys whose subtree is Garmin's reply exactly as sent, by design, and why.
RAW_PASSTHROUGH = {
    "garmin_replies": "inspect_garmin shows Garmin's replies unchanged, to check what tools leave out",
}

# Garmin GMT keys whose local time the tool adds under a different name.
RAW_LOCAL_SIBLINGS = {
    "timestampGMT": ("measuredAtLocal",),  # body composition weigh-ins
}

# Keys in the source that are not tool output, per module, and why.
SOURCE_EXEMPT = {
    ("activity_analysis.py", "start_time"): "FIT internals; _label_fit_times renames before output",
    ("activity_analysis.py", "end_time"): "FIT internals; _label_fit_times renames before output",
    ("activity_analysis.py", "timestamp"): "FIT internals; _label_fit_times renames before output",
    ("courses.py", "timestamp"): "geo point in the course upload sent to Garmin, not output",
}

# Curated keys that contain "time" but are not points in time.
NOT_A_TIME_KEYS = {
    "time_zone", "local_time_zone", "time_format", "time_seconds", "time_s",
    "goal_time", "goal_time_seconds", "total_elapsed_time_s", "total_timer_time_s",
    "recovery_time_hours", "time_minutes", "time_in_zone_seconds",
    "time",  # race prediction durations ("20:00") and workout end-condition ids
}

_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_EXPLICIT_ZONE = re.compile(r"(Z|[+-]\d{2}:?\d{2}|\sGMT|\sUTC)$")
_SNAKE = re.compile(r"^[a-z0-9_]+$")
_ZONE_SUFFIXES = ("_gmt", "_local", "_utc")
# Garmin epochs in milliseconds, 2001-2049.
_EPOCH_MS = (10**12, 2.5 * 10**12)
# Curated output marking swim laps, which keep meters by design.
_SWIM_MARKERS = ("lengths", "avg_swolf", "avg_swim_cadence", "active_length_count",
                 "total_strokes", "stroke")


def _labelled(key: str) -> bool:
    return key.endswith(_ZONE_SUFFIXES) or key in UNLABELLED_TIME_KEYS


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def output_violations(data: Any) -> List[str]:
    """Contract breaches in one tool's parsed JSON output (statute_us)."""
    return list(_walk(data, "$", swim=False, offset_in_scope=False))


def _raw_gmt_key(key: str) -> bool:
    return key.endswith(("Gmt", "GMT")) and not _SNAKE.match(key)


def _local_siblings(key: str) -> set:
    stem = key[:-3]
    return {f"{stem}Local", *RAW_LOCAL_SIBLINGS.get(key, ())}


def _is_time_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_ISO_DATETIME.match(value))
    return _is_number(value) and _EPOCH_MS[0] <= value <= _EPOCH_MS[1]


def _walk(node: Any, path: str, swim: bool, offset_in_scope: bool) -> Iterator[str]:
    if isinstance(node, list):
        for index, item in enumerate(node):
            yield from _walk(item, f"{path}[{index}]", swim, offset_in_scope)
        return
    if not isinstance(node, dict):
        return
    swim = (
        swim
        or units._names_a_swim(node)
        or any(marker in node for marker in _SWIM_MARKERS)
    )
    # A payload's utcOffsetHours converts every GMT time beneath it.
    offset_in_scope = offset_in_scope or "utcOffsetHours" in node
    for key, value in node.items():
        if key in RAW_PASSTHROUGH:
            continue
        if (
            isinstance(key, str) and not _SNAKE.match(key) and key not in RAW_UNZONED
            and isinstance(value, str) and _ISO_DATETIME.match(value)
            and not _EXPLICIT_ZONE.search(value)
        ):
            yield f"{path}.{key}: Garmin time {value!r} carries no Z or offset"
        if isinstance(value, list) and value and all(isinstance(row, list) for row in value):
            for row_index, row in enumerate(value):
                for item in row:
                    if isinstance(item, str) and _ISO_DATETIME.match(item) and not _EXPLICIT_ZONE.search(item):
                        yield f"{path}.{key}[{row_index}]: Garmin time {item!r} carries no Z or offset"
                        break
        if (
            isinstance(key, str) and _raw_gmt_key(key) and _is_time_value(value)
            and not offset_in_scope and not (_local_siblings(key) & set(node))
        ):
            yield f"{path}.{key}: GMT time with no local time beside it"
    for key, value in node.items():
        here = f"{path}.{key}"
        if key in RAW_PASSTHROUGH:
            continue
        if isinstance(key, str) and _is_number(value) and units._is_number(value):
            for companion, converted in units._companions(key, value, units.STATUTE_US, swim):
                if converted is not None and companion not in node:
                    yield f"{here}: no {companion} companion"
        restated = RAW_RESTATED_BY.get(key) in node or node.get("profile_type") == "local_date_time"
        if isinstance(key, str) and _SNAKE.match(key) and not _labelled(key) and not restated:
            if isinstance(value, str) and _ISO_DATETIME.match(value) and not _EXPLICIT_ZONE.search(value):
                yield f"{here}: time {value!r} names no zone"
            elif _is_number(value) and _EPOCH_MS[0] <= value <= _EPOCH_MS[1] and "id" not in key.split("_"):
                yield f"{here}: epoch {value!r} names no zone"
        yield from _walk(value, here, swim, offset_in_scope)


def result_json(result: Any) -> Any:
    """The JSON a FastMCP call_tool result carries, or None for plain text.

    A tool registered with structured output returns (content, structured);
    one registered without it, as _ToolFilter does, returns the content list
    itself.
    """
    try:
        first = result[0]
        block = first[0] if isinstance(first, (list, tuple)) else first
        text = block.text
    except (IndexError, KeyError, TypeError, AttributeError):
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


# --- source scan -----------------------------------------------------------

_TIME_TOKENS = {"time", "timestamp"}
SOURCE_DIR = pathlib.Path(__file__).resolve().parents[1] / "src" / "garmin_mcp"
# Modules whose dict keys are tool output, not request payloads or config.
_NOT_OUTPUT_MODULES = {
    "__init__.py", "__main__.py", "auth_cli.py", "config.py", "http_auth.py",
    "oauth_claude.py", "oauth_provider.py", "remote.py", "session_manager.py",
    "token_utils.py", "tool_annotations.py", "tool_profiles.py", "write_errors.py",
    "client_resolver.py", "units.py", "times.py", "profile_settings.py",
    "field_coverage.py", "payload_recording.py",
    "workout_builders.py", "workout_templates.py",
}


def _looks_like_a_time(key: str) -> bool:
    if not _SNAKE.match(key) or key in NOT_A_TIME_KEYS:
        return False
    tokens = key.split("_")
    if tokens[-1] in ("gmt", "local", "utc", "s", "ms", "seconds", "minutes", "hours", "hm", "hms"):
        return False
    return bool(_TIME_TOKENS & set(tokens)) or key.endswith("_at")


def _output_keys(tree: ast.AST) -> Iterator[ast.Constant]:
    """String constants used as dict keys or assigned into a dict by key."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    yield key
        elif isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant) \
                        and isinstance(target.slice.value, str):
                    yield target.slice


def source_time_key_violations(source_dir: pathlib.Path = SOURCE_DIR) -> List[str]:
    """Curated time-like keys in tool modules that name no zone."""
    found = []
    for path in sorted(pathlib.Path(source_dir).glob("*.py")):
        if path.name in _NOT_OUTPUT_MODULES:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for constant in _output_keys(tree):
            key = constant.value
            if (
                _looks_like_a_time(key)
                and not _labelled(key)
                and (path.name, key) not in SOURCE_EXEMPT
            ):
                found.append(f"{path.name}:{constant.lineno}: {key!r}")
    return found
