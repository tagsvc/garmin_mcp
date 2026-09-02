"""Tool counts in prose must match the tools actually registered.

Counts in documentation rot silently: nothing fails when they are wrong, so the
error is only found by someone recounting by hand. This fork drifted to 148 in
the README while registering 164, and CLAUDE.md -- which loads at the start of
every session -- carried a figure two syncs stale, so the very check it
prescribes ("re-enumerate tool counts") would have been measured against a wrong
target.

These tests derive the counts from the registered tools and fail when a document
disagrees, which turns a silent rot into a red check on the PR that caused it.
"""
import pathlib
import re

import pytest
from mcp.server.fastmcp import FastMCP

from garmin_mcp import auth_tools, remote

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def counts():
    """(stdio, remote) tool counts, measured rather than asserted."""
    app = FastMCP("count")
    for module in remote._CONFIGURED_MODULES:
        app = module.register_tools(app)
    remote_n = len(app._tool_manager.list_tools())
    app = auth_tools.register_tools(app)  # stdio-only, per FORK.md
    return len(app._tool_manager.list_tools()), remote_n


def _read(name):
    return (ROOT / name).read_text()


def test_claude_md_matches(counts):
    """CLAUDE.md loads automatically, so a stale figure misleads first."""
    stdio, remote_n = counts
    expected = f"stdio {stdio} / remote {remote_n}"
    assert expected in _read("CLAUDE.md"), (
        f"CLAUDE.md does not say '{expected}'. It is auto-loaded at session "
        "start, so a stale count here is the first thing a future session reads."
    )


def test_fork_md_matches(counts):
    stdio, remote_n = counts
    text = _read("FORK.md")
    assert f"stdio {stdio} / remote {remote_n}" in text, "FORK.md 'definition of done' count is stale"
    assert f"**stdio {stdio}**, **remote {remote_n}**" in text, "FORK.md 'expected state' count is stale"


def test_readme_headline_totals_match(counts):
    stdio, _ = counts
    text = _read("README.md")
    totals = [int(n) for n in re.findall(r"(?:implements \*\*~|registers ~)(\d+) tools", text)]
    assert totals, "README no longer states a headline tool count"
    assert all(t == stdio for t in totals), (
        f"README headline counts {totals} disagree with the {stdio} tools registered"
    )


def test_readme_section_counts_sum_to_the_real_total(counts):
    """The section list is where the drift actually happened.

    It summed to 148 against 164 registered: Nutrition six under, Workouts six
    under, Data Management absent entirely, Gear Management two over.
    """
    stdio, _ = counts
    sections = re.findall(r"^- ✅ ([^(]+?) \((\d+) tools?", _read("README.md"), re.M)
    assert sections, "README no longer lists per-section tool counts"

    total = sum(int(n) for _, n in sections)
    assert total == stdio, (
        f"README sections sum to {total} but {stdio} tools are registered "
        f"(off by {stdio - total}). Sections: "
        + ", ".join(f"{name.strip()}={n}" for name, n in sections)
    )
