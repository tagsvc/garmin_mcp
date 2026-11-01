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


def test_garminconnect_is_pinned_exactly_at_or_above_its_security_floor() -> None:
    """One exact garminconnect version, never below 0.3.5 (CVE-2026-54447).

    A garminconnect release can change how requests are built, how login and
    MFA behave and how tokens are stored, so each version is its own reviewed
    change: an exact pin, and the lock file on the same version.
    """
    import re

    repo_root = Path(__file__).resolve().parents[2]
    pyproject_text = (repo_root / "pyproject.toml").read_text()
    pins = re.findall(r'"garminconnect([^"]*)"', pyproject_text)
    assert len(pins) == 1, f"one garminconnect requirement expected, got {pins}"
    match = re.fullmatch(r"==(\d+)\.(\d+)\.(\d+)", pins[0])
    assert match, f"garminconnect must be pinned exactly, got {pins[0]!r}"
    assert tuple(map(int, match.groups())) >= (0, 3, 5)

    lock_text = (repo_root / "uv.lock").read_text()
    locked = re.search(r'\[\[package\]\]\nname = "garminconnect"\nversion = "([^"]+)"', lock_text)
    assert locked, "uv.lock has no garminconnect package"
    assert locked.group(1) == pins[0][2:], "uv.lock and pyproject.toml pin different versions"
