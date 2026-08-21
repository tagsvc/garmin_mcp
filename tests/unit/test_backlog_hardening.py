"""Regression tests for the August 2026 review backlog (M3, L1, L3, L4).

Each test pins a behaviour that a future upstream merge could silently undo.
"""
import json
from types import SimpleNamespace

import pytest
from mcp.server.fastmcp import FastMCP

import garmin_mcp.client_resolver as cr
from garmin_mcp import courses
from garmin_mcp.analytics import _report_store_path
from garmin_mcp.oauth_provider import (
    _GENERIC_LOGIN_ERROR,
    _safe_log,
    GarminOAuthProvider,
)
from garmin_mcp.session_manager import SessionManager


@pytest.fixture
def restore_resolver():
    saved_sm, saved_client = cr._session_manager, cr._global_client
    yield
    cr._session_manager, cr._global_client = saved_sm, saved_client


# ─── M3: remote tools must not read the server's filesystem ───────────────

def _courses_app(mock_client):
    courses.configure(mock_client)
    cr.set_global_client(mock_client)
    return courses.register_tools(FastMCP("t"))


@pytest.mark.asyncio
async def test_upload_course_refuses_server_path_in_remote_mode(
    mock_garmin_client, tmp_path, restore_resolver
):
    """gpx_path names the SERVER's disk — an arbitrary-file-read primitive."""
    app = _courses_app(mock_garmin_client)
    cr.set_session_manager(SessionManager(str(tmp_path / "s")))  # remote mode

    gpx = tmp_path / "secret.gpx"
    gpx.write_text("<gpx/>")

    result = await app.call_tool("upload_course", {"gpx_path": str(gpx)})
    text = result[0][0].text
    assert "disabled in remote mode" in text
    assert "gpx_base64" in text
    # It must not have reached Garmin at all.
    mock_garmin_client.client.post.assert_not_called()


@pytest.mark.asyncio
async def test_upload_course_still_allows_path_in_stdio_mode(
    mock_garmin_client, tmp_path, restore_resolver
):
    """Locally the path is the user's own disk, so it stays supported."""
    app = _courses_app(mock_garmin_client)
    cr.set_session_manager(None)  # stdio mode

    gpx = tmp_path / "route.gpx"
    gpx.write_text("<gpx/>")

    result = await app.call_tool("upload_course", {"gpx_path": str(gpx)})
    assert "disabled in remote mode" not in result[0][0].text


@pytest.mark.asyncio
async def test_upload_course_rejects_bad_base64(mock_garmin_client, restore_resolver):
    app = _courses_app(mock_garmin_client)
    result = await app.call_tool("upload_course", {"gpx_base64": "!!!not base64!!!"})
    assert "not valid base64" in result[0][0].text


@pytest.mark.asyncio
async def test_upload_course_requires_exactly_one_source(
    mock_garmin_client, restore_resolver
):
    app = _courses_app(mock_garmin_client)
    neither = await app.call_tool("upload_course", {})
    assert "either gpx_base64" in neither[0][0].text
    both = await app.call_tool(
        "upload_course", {"gpx_path": "/x/a.gpx", "gpx_base64": "eA=="}
    )
    assert "only one of" in both[0][0].text


# ─── L1: saved-report store is per-user in remote mode ────────────────────

def test_report_store_is_shared_in_stdio_mode(monkeypatch, tmp_path, restore_resolver):
    monkeypatch.setenv("GARMIN_REPORTS_PATH", str(tmp_path / "reports.json"))
    cr.set_session_manager(None)
    assert _report_store_path(None) == tmp_path / "reports.json"


def test_report_store_is_scoped_per_user_in_remote_mode(
    monkeypatch, tmp_path, restore_resolver
):
    """Two users must not share one saved-report file."""
    monkeypatch.setenv("GARMIN_REPORTS_PATH", str(tmp_path / "reports.json"))
    cr.set_session_manager(SessionManager(str(tmp_path / "s")))

    def _as_user(uid):
        monkeypatch.setattr(cr, "get_user_id", lambda ctx=None: uid)
        import garmin_mcp.analytics as an
        monkeypatch.setattr(an, "get_user_id", lambda ctx=None: uid)
        return _report_store_path(object())

    a, b = _as_user("user-aaa"), _as_user("user-bbb")
    assert a != b
    assert "user-aaa" in a.name and "user-bbb" in b.name
    assert a.suffix == ".json" and b.suffix == ".json"


# ─── L3: limiter lockout + allowlist membership oracle ────────────────────

class _Req:
    def __init__(self, data, peer="203.0.113.7"):
        self._data = data
        self.headers = {}
        self.client = SimpleNamespace(host=peer)

    async def form(self):
        return self._data


def _provider(tmp_path, **kw):
    return GarminOAuthProvider(
        db_path=str(tmp_path / "t.db"), server_url="https://x", **kw
    )


@pytest.mark.asyncio
async def test_attacker_cannot_lock_out_an_allowlisted_account(tmp_path, monkeypatch):
    """Keying the limiter on email alone let anyone lock the real user out."""
    import garth

    # Never touch Garmin from a test: an allowlisted email would otherwise reach
    # the real SSO endpoint (slow, flaky, and it feeds Garmin's IP rate limiter).
    monkeypatch.setattr(
        garth.sso, "login", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad creds"))
    )
    p = _provider(tmp_path, allowed_emails={"ok@x.com"})
    form = {"state": "s", "email": "ok@x.com", "password": "wrong"}

    # Attacker burns their own bucket from one IP.
    for _ in range(12):
        await p.handle_login_callback(_Req(form, peer="198.51.100.66"))
    attacker = await p.handle_login_callback(_Req(form, peer="198.51.100.66"))
    assert b"Too many attempts" in attacker.body

    # The legitimate user, from a different IP, is unaffected.
    victim = await p.handle_login_callback(_Req(form, peer="203.0.113.7"))
    assert b"Too many attempts" not in victim.body


@pytest.mark.asyncio
async def test_allowlist_membership_is_not_revealed_by_the_response(tmp_path, monkeypatch):
    """Allowlisted-but-wrong-password and not-allowlisted must look identical."""
    import garth

    monkeypatch.setattr(
        garth.sso, "login", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad creds"))
    )
    p = _provider(tmp_path, allowed_emails={"ok@x.com"})

    on_list = await p.handle_login_callback(
        _Req({"state": "s", "email": "ok@x.com", "password": "wrong"})
    )
    off_list = await p.handle_login_callback(
        _Req({"state": "s", "email": "nope@x.com", "password": "wrong"}, peer="1.2.3.4")
    )

    assert _GENERIC_LOGIN_ERROR.encode() in on_list.body
    assert _GENERIC_LOGIN_ERROR.encode() in off_list.body
    assert on_list.body == off_list.body  # byte-identical: nothing to difference


# ─── L4: log injection ────────────────────────────────────────────────────

def test_safe_log_escapes_line_breaks():
    forged = "victim@x.com\n2026-01-01 [INFO] forged: admin logged in"
    out = _safe_log(forged)
    assert "\n" not in out and "\r" not in out
    assert "\\n" in out
    assert "victim@x.com" in out  # still diagnosable


def test_safe_log_bounds_length():
    assert len(_safe_log("a" * 5000)) <= 200


# ─── Round 2: log injection via headers, and upload filename ──────────────

class _IPReq:
    """Minimal request stand-in for _client_ip()."""

    def __init__(self, xff=None, peer=None):
        self.headers = {"x-forwarded-for": xff} if xff is not None else {}
        self.client = SimpleNamespace(host=peer) if peer else None


def test_client_ip_is_sanitised_at_source():
    """Every caller of _client_ip gets a safe value — it is an untrusted header.

    Sanitising inside _client_ip rather than at each log site means a future
    caller cannot reintroduce the log-injection hole by forgetting to wrap it.
    """
    from garmin_mcp.oauth_provider import _client_ip

    forged = "1.1.1.1, 203.0.113.9\n2026-01-01 [INFO] forged: admin logged in"
    out = _client_ip(_IPReq(xff=forged))
    assert "\n" not in out and "\r" not in out


def test_client_ip_bounds_length_so_a_huge_header_cannot_bloat_the_limiter():
    from garmin_mcp.oauth_provider import _client_ip

    assert len(_client_ip(_IPReq(xff="9.9.9.9, " + "A" * 10_000))) <= 64


def test_peer_fallback_is_also_sanitised():
    from garmin_mcp.oauth_provider import _client_ip

    assert "\n" not in _client_ip(_IPReq(peer="10.0.0.1\nforged"))


def test_upload_filename_strips_paths_quotes_and_newlines():
    """course_name is caller-supplied and becomes a multipart filename."""
    from garmin_mcp.courses import _safe_upload_filename as f

    assert f("../../etc/passwd") == "passwd.gpx"          # no directory escape
    assert '"' not in f('a"; filename="evil.gpx')          # no header injection
    assert "\n" not in f("route\nX-Injected: 1")
    assert f("") == "course.gpx"                           # empty -> fallback
    assert f("/////") == "course.gpx"                      # nothing usable -> fallback
    assert len(f("z" * 500)) <= 84                         # bounded
    assert f("My Route") == "My Route.gpx"                 # ordinary names survive
