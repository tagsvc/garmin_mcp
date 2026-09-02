"""No test that performs a real Garmin login may run in the default CI selection.

This repository is **public**, so its build logs are public. A test that
authenticates against Garmin prints whatever it prints straight into them.

Two CodeQL alerts were dismissed on the strength of that exclusion, and a
dismissal is sticky: if the marker were removed the test would start running,
data would reach public logs, and the alert would stay closed. Nothing would say
so. This test is what makes the exclusion real rather than a note in a document.

It also caught `test_mcp_debug.py`, which was unmarked and therefore *did* run in
CI — performing a real login and printing the login email, while catching every
exception so it passed regardless.
"""
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"

# A real client instantiation or a real login call. Comments are stripped first:
# test_session_manager.py mentions `Garmin().login(token_dir)` in prose while
# using a local garth Client with no network.
_LIVE_CALL = re.compile(r"(?:^|[^_\w])Garmin\(|\.login\(\)")
_E2E_MARK = re.compile(r"^pytestmark\s*=\s*pytest\.mark\.e2e\s*$", re.M)


def _without_comments(text: str) -> str:
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def _modules_doing_live_auth():
    found = []
    for path in sorted(TESTS.rglob("test_*.py")):
        if _LIVE_CALL.search(_without_comments(path.read_text())):
            found.append(path)
    return found


def test_the_detector_still_finds_something():
    """A detector that matches nothing would pass this file vacuously."""
    assert _modules_doing_live_auth(), (
        "no live-auth test modules detected -- the signature probably needs "
        "updating, not deleting: this file would otherwise pass without checking"
    )


@pytest.mark.parametrize(
    "path", _modules_doing_live_auth(), ids=lambda p: str(p.relative_to(ROOT))
)
def test_live_auth_module_is_marked_e2e(path):
    assert _E2E_MARK.search(path.read_text()), (
        f"{path.relative_to(ROOT)} performs a real Garmin login but is not "
        "marked `pytestmark = pytest.mark.e2e`, so CI collects it. This repo is "
        "public: anything it prints lands in public build logs."
    )


def test_no_live_auth_module_is_collected_by_the_ci_selection():
    """The property that actually matters, checked the way CI checks it.

    The marker assertion above could pass while a pytest.ini change quietly
    re-included these; this runs the real collection command instead.
    """
    modules = [str(p.relative_to(ROOT)) for p in _modules_doing_live_auth()]
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "not e2e", "--collect-only", "-q", *modules],
        cwd=ROOT, capture_output=True, text=True, timeout=120,
    )
    assert " 0 selected" in result.stdout or "no tests collected" in result.stdout, (
        "the default CI selection collects a module that performs a real Garmin "
        f"login:\n{result.stdout[-1500:]}"
    )
