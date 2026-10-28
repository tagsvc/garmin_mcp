"""Project metadata regression tests."""

from pathlib import Path


def test_project_caps_mcp_to_v1_series() -> None:
    """The project should explicitly stay on the MCP v1 series for compatibility."""
    repo_root = Path(__file__).resolve().parents[2]
    pyproject_text = (repo_root / "pyproject.toml").read_text()

    # FORK: the cap is what this guards; the floor is this fork's security floor
    # (1.30.0, CVE-2026-59951), so check both rather than one literal string.
    import re

    match = re.search(r'"mcp>=(\d+)\.(\d+)\.(\d+),<2"', pyproject_text)
    assert match, "mcp must be capped below 2 with an explicit floor"
    assert tuple(map(int, match.groups())) >= (1, 30, 0)
