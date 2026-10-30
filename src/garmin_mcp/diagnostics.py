"""inspect_garmin: Garmin's raw replies, to check what a tool leaves out.

FORK: a curated tool's output cannot show what it omits -- get_body_battery
kept Garmin's band ("HIGH") and dropped the level for months. Checking meant
the athlete running a script on their own computer or a PR adding a raw
option. This tool does it from a chat: it runs one read-only tool with its
Garmin replies recorded (payload_recording.py), and lists the fields the
tool's output leaves out (field_coverage.py); or it fetches one Garmin
Connect API path no tool calls yet.

Read-only by construction: it runs only tools annotated read-only, and the
path mode sends GET requests to Garmin Connect API paths only. Replies are the
caller's own (each call resolves the caller's Garmin session), capped in
size, and narrowed with ``find`` or ``fields``.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from mcp.server.fastmcp import Context

from garmin_mcp import field_coverage, payload_recording
from garmin_mcp.client_resolver import get_client

_app = None

# The most JSON one call returns; a night's full sleep reply is ~150 KB.
MAX_CHARS = 20000
# Matches listed per call for find/fields, and missing fields listed.
MAX_MATCHES = 300
# A Garmin Connect API path: rooted, no host, no "..", no "//", no query
# (pass that as params).
_ENDPOINT = re.compile(r"^(?!.*\.\.)(?!.*//)/[A-Za-z0-9._~\-/]+$")


def configure(client):
    """Nothing to hold: each call resolves the caller's own client."""


def _result_parts(result: Any) -> Tuple[Any, str]:
    """A FastMCP call_tool result as (parsed JSON or None, its text)."""
    try:
        first = result[0]
        block = first[0] if isinstance(first, (list, tuple)) else first
        text = str(block.text)
    except (IndexError, KeyError, TypeError, AttributeError):
        return None, ""
    try:
        return json.loads(text), text
    except (TypeError, ValueError):
        return None, text


def _matches(reply: Any, find: Optional[str], fields: Optional[List[str]]) -> List[Dict[str, Any]]:
    """Each key in a reply that find (a regex) or fields (exact names) picks,
    with its path and value; a picked key's whole value is kept."""
    pattern = re.compile(find, re.IGNORECASE) if find else None
    wanted = set(fields or [])
    found: List[Dict[str, Any]] = []

    def walk(node: Any, path: str) -> None:
        if len(found) >= MAX_MATCHES:
            return
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{path}.{key}" if path else str(key)
                if (pattern and pattern.search(str(key))) or key in wanted:
                    found.append({"path": here, "value": value})
                else:
                    walk(value, here)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")

    walk(reply, "")
    return found


def _capped(payload: Dict[str, Any]) -> str:
    """The payload as JSON, with any reply too big to fit summarised."""
    text = json.dumps(payload, indent=2, default=str)
    if len(text) <= MAX_CHARS:
        return text
    for entry in payload.get("garmin_replies", []):
        reply = entry.get("reply")
        size = len(json.dumps(reply, default=str))
        if size > MAX_CHARS // 4:
            keys = list(reply)[:50] if isinstance(reply, dict) else None
            entry["reply"] = {"truncated": True, "size_chars": size, "top_level_keys": keys}
    payload["note"] = (
        f"Replies over {MAX_CHARS // 4} characters are summarised; narrow them "
        "with find (a regex on key names) or fields (exact key names)."
    )
    text = json.dumps(payload, indent=2, default=str)
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + "\n... (truncated)"


def register_tools(app):
    global _app
    _app = app

    @app.tool()
    async def inspect_garmin(
        ctx: Context,
        tool: Optional[str] = None,
        arguments: Optional[Dict[str, Any]] = None,
        endpoint: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
        find: Optional[str] = None,
        fields: Optional[List[str]] = None,
        missing: bool = False,
        include_noise: bool = False,
    ) -> str:
        """Diagnostic: Garmin's raw reply, to check what a tool leaves out.

        For checking the MCP itself, not for answering questions -- use the
        curated tools for those. Read-only: it runs only read-only tools and
        sends only GET requests.

        Two modes:
        - tool + arguments: runs that read-only tool and returns the replies
          Garmin sent it (garmin_replies, each with the call it answered).
          With missing=true, also missing_fields: each reply field the tool's
          output leaves out, with how many of its values are shown (a series
          the tool summarises shows some of all). Ids, owners, images,
          coordinates and device capability flags are left out of that list
          unless include_noise=true.
        - endpoint (+ params): GET one Garmin Connect API path, such as
          "/gear-service/gear/v2/list" or "/device-service/deviceregistration/devices".

        find (a regex on key names, e.g. "batt|bedtime") or fields (exact key
        names) returns only the matching keys, with their paths and values.
        Replies are capped at about 20 KB; a bigger one is summarised by its
        top-level keys, so narrow it with find or fields.

        Args:
            tool: A read-only tool's name, e.g. "get_body_battery"
            arguments: That tool's arguments, e.g. {"start_date": "2026-10-30", "end_date": "2026-10-30"}
            endpoint: A Garmin Connect API path (instead of tool)
            params: Query parameters for endpoint
            find: Regex on key names; returns only matching keys
            fields: Exact key names to return
            missing: With tool, list the reply fields its output leaves out
            include_noise: With missing, also list ids, owners and the like
        """
        if bool(tool) == bool(endpoint):
            return "Pass either tool (with its arguments) or endpoint, not both or neither."
        if find:
            try:
                re.compile(find)
            except re.error as error:
                return f"find is not a valid regex: {error}"

        if endpoint:
            if not _ENDPOINT.match(endpoint):
                return (
                    "endpoint must be a Garmin Connect API path such as "
                    "'/gear-service/gear/v2/list': rooted, no host, no '..' or '//', "
                    "with any query in params."
                )
            try:
                reply = get_client(ctx).connectapi(endpoint, params=params or None)
            except Exception as error:
                return f"Garmin refused {endpoint}: {type(error).__name__}: {str(error)[:300]}"
            replies = [(endpoint, reply)]
            payload: Dict[str, Any] = {"endpoint": endpoint, "params": params}
        else:
            tools = {t.name: t for t in await _app.list_tools()}
            target = tools.get(tool)
            if target is None:
                return f"No tool named {tool!r}."
            if tool == "inspect_garmin" or not (
                target.annotations and target.annotations.readOnlyHint
            ):
                return f"{tool} is not a read-only tool; inspect_garmin runs only read-only tools."
            with payload_recording.recording() as calls:
                result = await _app.call_tool(tool, arguments or {})
            output, text = _result_parts(result)
            replies = [
                (name, reply) for name, reply in calls
                if name.split(".")[-1] not in field_coverage.AUXILIARY_METHODS or name == tool
            ]
            payload = {"tool": tool, "arguments": arguments or {}}
            if output is None:
                payload["tool_output_text"] = text[:500]
            if missing:
                if output is None:
                    payload["missing_fields"] = "the tool returned text, not JSON, so nothing to compare"
                else:
                    gaps = field_coverage.missing_fields(
                        [(n, r) for n, r in replies if isinstance(r, (dict, list, int, float))], output
                    )
                    shown = [g for g in gaps if include_noise or not field_coverage.is_noise(g[0])]
                    payload["missing_fields"] = [
                        {"field": field, "shown": seen, "of": total}
                        for field, seen, total in shown[:MAX_MATCHES]
                    ]
                    payload["noise_fields_hidden"] = len(gaps) - len(shown)

        if find or fields:
            payload["garmin_replies"] = [
                {"call": name, "matches": _matches(reply, find, fields)} for name, reply in replies
            ]
        else:
            payload["garmin_replies"] = [{"call": name, "reply": reply} for name, reply in replies]
        return _capped(payload)

    return app
