"""End to end through the real remote server, in-process.

The first test of an authenticated request through the whole remote stack
rather than pieces of it. See remote_e2e_driver.py for what is real and what
is stubbed. Concurrent callers, two users, each calling one tool that resolves
its client through get_client(ctx) and one through the module-level proxy.
"""
import json
import pathlib
import subprocess
import sys

import pytest

DRIVER = pathlib.Path(__file__).with_name("remote_e2e_driver.py")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("remote-e2e")
    env = {
        "PATH": "/usr/bin:/bin",
        "GARMIN_ALLOWED_EMAILS": "e2e@example.invalid",
        "GARMIN_MCP_SERVER_URL": "https://e2e.example.invalid",
        "DB_PATH": str(tmp / "db.sqlite"),
        "SESSION_STORAGE_PATH": str(tmp / "sessions"),
        "HOME": str(tmp),
    }
    proc = subprocess.run([sys.executable, str(DRIVER)], env=env, capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_unauthenticated_request_is_refused(run):
    assert run["no_token_status"] == 401


def test_claude_ai_oauth_patches_still_take_effect(run):
    """oauth_claude monkeypatches SDK internals; an SDK bump can bind cleanly
    and silently stop working. claude.ai needs both behaviours."""
    assert "none" in run["token_auth_methods"]
    assert 'resource_metadata="https://e2e.example.invalid/.well-known/oauth-protected-resource"' \
        in run["no_token_challenge"]


def test_each_caller_gets_only_their_own_garmin_data(run):
    expected = {"tok-alice": ("Alice", "111", "Bob", "222"),
                "tok-bob": ("Bob", "222", "Alice", "111")}
    assert len(run["sessions"]) == 4
    for s in run["sessions"]:
        mine_name, mine_ftp, other_name, other_ftp = expected[s["token"]]
        assert s["init_status"] == 200
        assert mine_name in s["get_full_name"] and other_name not in s["get_full_name"]
        assert mine_ftp in s["get_cycling_ftp"] and other_ftp not in s["get_cycling_ftp"]


def test_remote_is_stateless(run):
    """No Mcp-Session-Id: nothing to accumulate (CVE-2026-59951) or expire."""
    assert all(s["session_id"] is None for s in run["sessions"])


def test_results_are_not_double_wrapped(run):
    assert not any(s["get_full_name_wrapped"] for s in run["sessions"])
