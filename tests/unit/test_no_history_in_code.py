"""Comments and docstrings carry no history (CLAUDE.md).

They say why the code is as it is, in the present tense. What the code used to
do, when or how something was found, and PR or issue numbers belong in
CHANGELOG.md and commit messages. This scans every comment and docstring under
src/, scripts/ and tests/ for the plain markers of history: a PR or issue
number, a month and year, a date beside a verb of change, and phrases that only
describe the code's past.

A date on its own is not flagged: tests name the day their data comes from, and
docstrings show date formats by example. Nor is a word like "change" or
"found" alone, which describes clock changes and lookups in the present tense.

Upstream's own comments are kept word for word so merges stay clean. The ones
that match a marker are listed in UPSTREAM_TEXT, by file. Only text upstream
wrote goes there; the fork's own text is reworded instead.
"""
import ast
import io
import pathlib
import re
import tokenize

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCANNED = ("src", "scripts", "tests")

_MONTH = (
    r"(?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec)"
)
MARKERS = {
    "PR or issue number": re.compile(
        r"(?<![\w&/])#\d{1,5}\b|\bPRs? ?#?\d+\b|\b(?:issue|pull request)s? #?\d+\b", re.I
    ),
    "month and year": re.compile(rf"\b{_MONTH}\.? (?:[0-3]?\d,? )?20\d\d\b"),
    "dated change": re.compile(
        r"\b(?:found|fixed|added|changed|noticed|discovered|reported|introduced|"
        r"removed|broke|since|until|as of)\b\W+(?:\w+\W+){0,5}?20\d\d-[01]\d-[0-3]\d\b",
        re.I,
    ),
    "the code's past": re.compile(
        r"\b(?:used to (?:be|return|send|have|call|read|write|drop|report|show|"
        r"crash|fail|work|give|keep|skip)|previously|formerly|originally|"
        r"no longer|until recently|turned out|we found|fixed in|introduced in|"
        r"regressed|this fix|the fix for|(?:before|after) the fix|"
        r"the old (?:code|version|behaviou?r|path|tool)|was renamed|renamed from)\b",
        re.I,
    ),
}

# Upstream's comments that match a marker, left as upstream wrote them.
UPSTREAM_TEXT = {
    "scripts/generate_dxt_tools.py": {
        "to 8 hand-picked entries while the server grew to ~150 tools (#340). This",
    },
    "src/garmin_mcp/__init__.py": {
        "# Optional shared secret (#339). Without it the endpoint is open, which",
        "# block the MCP handshake (issue #255). That thread may still emit",
        "# handshake (issue #255). Tool calls block on it individually instead,",
        "# message hid all of that (#335).",
        "# output written concurrently by the server's own thread (#255).",
        "# size for clients that forward both blocks (issue #331). Opt out",
        "# unresponsive (see issue #248). 90s sits comfortably above a normal slow call",
        "MCP ping, for the length of the Garmin call (#338). The coroutine is",
        "can be answered (issue #255).",
        "if it does not return within the configured timeout (issue #248 — an",
        "moved to a background thread (#255) that is before Garmin login has",
        "starts (issue #255): the login call may still emit stray progress",
    },
    "src/garmin_mcp/activity_analysis.py": {
        "Garmin fields are renamed from live comparison with Garmin Connect.",
    },
    "src/garmin_mcp/courses.py": {
        "and course points (as waypoints); it is not the originally uploaded file.",
    },
    "src/garmin_mcp/http_auth.py": {
        "Shared-secret bearer auth for the HTTP transports (#339).",
    },
    "src/garmin_mcp/workout_builders.py": {
        'Garmin shows the real set count — previously "sets" only ever reached the',
    },
    "src/garmin_mcp/workouts.py": {
        "# previously dropped entirely -- strokeType/equipmentType/drillType are real",
        "Adapted from PR #295. Parsing is optional: a successful POST with no JSON",
        "devices. As of July 31, 2026, no supported or known Garmin Connect",
    },
    "tests/e2e/test_nested_workout_target_values_live.py": {
        "MCP repairs issue #210's payload before Garmin silently drops its bounds.",
    },
    "tests/fixtures/garmin_responses.py": {
        "# from a live Instinct 2X Solar (upstream PR #247); values are made up.",
    },
    "tests/integration/test_activity_management_tools.py": {
        "Regression: the detail view previously never extracted 'description', so a",
    },
    "tests/integration/test_health_wellness_tools.py": {
        "the deficit is returned -- both were previously impossible with the",
    },
    "tests/integration/test_nutrition_tools.py": {
        "# Field names follow the reporter's settings payload in issue #294.",
    },
    "tests/integration/test_schedule_response_ids.py": {
        "Extracted from the scheduling support in PR #295. Transport is mocked; these",
    },
    "tests/integration/test_workouts_tools.py": {
        "# Cycling power target tests (Issue #155)",
        "Batch uploads use the same issue #210 repair as upload_workout.",
        "Fix for Issue #155: power.between must use ID 6, not ID 2.",
        "Repair the exact payload shape that caused issue #210.",
        "The issue #210 schedule_workouts path repairs bounds before upload.",
        "Using workoutTargetTypeId 2 with key 'power.between' is the root cause of Issue #155.",
    },
    "tests/unit/test_config_loading.py": {
        "Credentials and token settings are read at startup, not at import (#343).",
        "Importing garmin_mcp used to read GARMIN_EMAIL(_FILE) / GARMIN_PASSWORD(_FILE),",
    },
    "tests/unit/test_dxt_manifest.py": {
        "The manifest's tools list must track the server's actual tool set (#340).",
    },
    "tests/unit/test_garmin_proxy.py": {
        "A call that stalls past the timeout raises a clear, retry-able error (issue #248).",
    },
    "tests/unit/test_http_bearer_token.py": {
        "GARMIN_MCP_HTTP_TOKEN gates the HTTP transports with a bearer token (#339).",
    },
    "tests/unit/test_login_gate.py": {
        "Unit tests for the deferred-login helpers behind issue #255.",
    },
    "tests/unit/test_server_startup.py": {
        "app.run() must not wait on Garmin login to finish (issue #255).",
    },
    "tests/unit/test_token_failure_messages.py": {
        "init_api says why saved tokens failed to load (#335).",
    },
    "tests/unit/test_tool_filter.py": {
        "# --- profiles compose with the allow/deny lists (#336) -----------------------",
        "String tools must not double-emit escaped JSON in structuredContent (#331).",
    },
    "tests/unit/test_tool_profiles.py": {
        "GARMIN_TOOL_PROFILE selects a built-in subset of tools (#336).",
    },
    "tests/unit/test_tools_off_event_loop.py": {
        "# Before the fix slow_tool blocked the loop inside release.wait(), so",
        "Tool bodies run on worker threads, so one Garmin call cannot stall the server (#338).",
    },
    "tests/unit/test_workout_builders.py": {
        "# Previously [1, 1, 2, 2, 1, 2]: both groups' children restarted at 1.",
        '# previously hardcoded "UNASSIGNED" — fails every upload with `400 - Invalid',
        "# stepOrder numbering (#420)",
        "The snapshot is the POC payload Garmin accepted, updated for #420 so the",
    },
}


def _comments_and_docstrings(path):
    """(line, text) for every comment and docstring line in a Python file."""
    source = path.read_text()
    found = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type == tokenize.COMMENT:
                found.append((token.start[0], token.string.strip()))
    except tokenize.TokenError:
        pass
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            for offset, line in enumerate(first.value.value.splitlines()):
                found.append((first.lineno + offset, line.strip()))
    return found


def _python_files():
    for directory in SCANNED:
        yield from sorted((ROOT / directory).rglob("*.py"))


def history_notes():
    """(file, line, marker, text) for every comment or docstring line that reads
    as history, upstream's listed text excepted."""
    notes = []
    for path in _python_files():
        rel = path.relative_to(ROOT).as_posix()
        allowed = UPSTREAM_TEXT.get(rel, set())
        for line, text in _comments_and_docstrings(path):
            if text in allowed:
                continue
            for marker, pattern in MARKERS.items():
                if pattern.search(text):
                    notes.append((rel, line, marker, text))
                    break
    return notes


def test_comments_and_docstrings_carry_no_history():
    notes = history_notes()
    assert not notes, (
        "Comments and docstrings say why the code is as it is; history goes in "
        "CHANGELOG.md and commit messages (CLAUDE.md). Reword these, or, for a "
        "line upstream wrote word for word, list it in UPSTREAM_TEXT:\n"
        + "\n".join(f"  {rel}:{line} [{marker}] {text}" for rel, line, marker, text in notes)
    )


def test_listed_upstream_text_is_still_there():
    """An entry whose line has gone is dropped, so the list cannot grow stale
    and excuse a line written later."""
    stale = []
    for rel, texts in UPSTREAM_TEXT.items():
        path = ROOT / rel
        present = {text for _, text in _comments_and_docstrings(path)} if path.exists() else set()
        stale.extend(f"{rel}: {text}" for text in sorted(texts - present))
    assert not stale, "UPSTREAM_TEXT lists lines no longer in the code:\n  " + "\n  ".join(stale)


@pytest.mark.parametrize(
    "text",
    [
        "# Fixed in PR #88 after the deploy.",
        "# See issue 248.",
        "# Garmin started sending this in October 2026.",
        "# Found on 2026-11-01 while checking the run.",
        "# The tool used to send the query in the path.",
        "# Previously this read the change phrase.",
        "# Garmin no longer sends the field.",
    ],
)
def test_each_marker_is_caught(text):
    assert any(pattern.search(text) for pattern in MARKERS.values()), text


@pytest.mark.parametrize(
    "text",
    [
        "# New York leaves daylight saving at 2 AM on 2026-11-01.",
        "# A real run, 2026-10-24: 7260.53 m in 2899.16 s.",
        '# e.g. {"start_date": "2026-10-30"}',
        "# An end before the change: no change in that span.",
        "An empty reply says no plan was found, rather than an empty shell.",
        "# The value is used to compute the pace.",
        "# Zone 1 starts at 0 W.",
    ],
)
def test_present_tense_text_is_not_flagged(text):
    assert not any(pattern.search(text) for pattern in MARKERS.values()), text
