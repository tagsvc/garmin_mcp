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
import datetime
import json
import re
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Set

# The comparison and the recorder live in the package, shared with the
# inspect_garmin tool.
from garmin_mcp.field_coverage import AUXILIARY_METHODS, is_noise, missing_fields  # noqa: F401
from garmin_mcp.payload_recording import RecordingClient  # noqa: F401

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
        # A tool that leaves detail out unless asked is checked with it asked
        # for (get_fitnessage_data's component breakdown).
        if "details" in tools[name].inputSchema.get("properties", {}):
            arguments["details"] = True
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
