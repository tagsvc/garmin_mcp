"""Regression tests for the remote server's OAuth and sign-in hardening.

Each test pins one guard: sign-ins return only to approved hosts, the sign-in
page names who is asking, the allowlist holds at every token use, the login
handler refuses dead sign-ins before calling Garmin, token-import refusals say
nothing of the allowlist, codes and refresh tokens are single use, stored rows
cannot grow without bound, and saved reports fail closed.
"""
import time
from types import SimpleNamespace

import pytest
from mcp.server.auth.provider import AuthorizationParams, RegistrationError, TokenError
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

import garmin_mcp.client_resolver as cr
from garmin_mcp import oauth_provider
from garmin_mcp.config import RemoteConfig
from garmin_mcp.oauth_provider import (
    _GENERIC_LOGIN_ERROR,
    DEFAULT_REDIRECT_HOSTS,
    GarminOAuthProvider,
    redirect_uri_allowed,
)
from garmin_mcp.session_manager import SessionManager
from tests.unit.oauth_helpers import CLAUDE_CALLBACK, issued_code, pending_sign_in

pytestmark = pytest.mark.unit

EVIL = "https://attacker.example/cb"


def _provider(tmp_path, **kw):
    kw.setdefault("allowed_emails", {"ok@x.com"})
    return GarminOAuthProvider(db_path=str(tmp_path / "t.db"), server_url="https://example.com", **kw)


def _client(*uris, client_id="dyn-1", name="Some App"):
    return OAuthClientInformationFull(
        client_id=client_id, redirect_uris=[AnyUrl(u) for u in uris],
        token_endpoint_auth_method="none", grant_types=["authorization_code", "refresh_token"],
        response_types=["code"], scope="garmin", client_name=name,
    )


class _Req:
    def __init__(self, form, peer="203.0.113.7"):
        self._form, self.headers, self.client = form, {}, SimpleNamespace(host=peer)

    async def form(self):
        return self._form


def _no_garmin(monkeypatch):
    import garth

    def _boom(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("Garmin must not be contacted")

    monkeypatch.setattr(garth.sso, "login", _boom)


# ─── Sign-ins return only to approved hosts ────────────────────────────────

@pytest.mark.parametrize("uri, allowed", [
    (CLAUDE_CALLBACK, True),
    ("https://claude.com/api/mcp/auth_callback", True),
    ("http://localhost:33418/callback", True),
    ("http://127.0.0.1:5000/cb", True),
    ("http://[::1]:5000/cb", True),
    ("http://claude.ai/api/mcp/auth_callback", False),  # not https
    (EVIL, False),
    ("https://claude.ai.attacker.example/cb", False),
    ("https://claude.ai@attacker.example/cb", False),  # userinfo, host is the attacker
    ("https://claude.ai/cb#frag", False),
    ("javascript:alert(1)", False),
])
def test_redirect_hosts(uri, allowed):
    assert redirect_uri_allowed(uri, DEFAULT_REDIRECT_HOSTS) is allowed


@pytest.mark.asyncio
async def test_registration_refuses_an_unapproved_redirect(tmp_path):
    p = _provider(tmp_path)
    with pytest.raises(RegistrationError) as err:
        await p.register_client(_client(EVIL))
    assert err.value.error == "invalid_redirect_uri"
    assert await p.get_client("dyn-1") is None


@pytest.mark.asyncio
async def test_registration_accepts_claude_and_this_computer(tmp_path):
    p = _provider(tmp_path)
    await p.register_client(_client(CLAUDE_CALLBACK, "http://localhost:4000/cb"))
    assert await p.get_client("dyn-1") is not None


@pytest.mark.asyncio
async def test_a_stored_client_is_held_to_the_rule_too(tmp_path):
    """A client stored before the rule cannot be sent a code at another host."""
    p = _provider(tmp_path)
    p.seed_clients([_client(EVIL, client_id="old-evil"),
                    _client(EVIL, CLAUDE_CALLBACK, client_id="old-mixed")])
    assert await p.get_client("old-evil") is None
    mixed = await p.get_client("old-mixed")
    assert [str(u) for u in mixed.redirect_uris] == [CLAUDE_CALLBACK]


def test_the_redirect_hosts_come_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("GARMIN_MCP_SERVER_URL", "https://example.com")
    monkeypatch.delenv("GARMIN_ALLOWED_REDIRECT_HOSTS", raising=False)
    assert _provider(tmp_path, allowed_redirect_hosts=RemoteConfig().allowed_redirect_hosts) \
        .allowed_redirect_hosts == DEFAULT_REDIRECT_HOSTS
    monkeypatch.setenv("GARMIN_ALLOWED_REDIRECT_HOSTS", " Claude.ai , chatgpt.com ,")
    hosts = _provider(tmp_path, allowed_redirect_hosts=RemoteConfig().allowed_redirect_hosts) \
        .allowed_redirect_hosts
    assert hosts == frozenset({"claude.ai", "chatgpt.com"})
    monkeypatch.setenv("GARMIN_ALLOWED_REDIRECT_HOSTS", "")
    assert _provider(tmp_path, allowed_redirect_hosts=RemoteConfig().allowed_redirect_hosts) \
        .allowed_redirect_hosts == frozenset()


# ─── The sign-in page names who is asking ──────────────────────────────────

@pytest.mark.asyncio
async def test_the_sign_in_page_names_the_app_and_its_return_host(tmp_path):
    p = _provider(tmp_path)
    p.seed_clients([_client(CLAUDE_CALLBACK, client_id="c1", name="<b>Claude</b>")])
    pending_sign_in(p, "s1", client_id="c1")
    body = (await p.get_login_page("s1")).body
    assert b"&lt;b&gt;Claude&lt;/b&gt;" in body and b"<b>Claude</b>" not in body
    assert b"claude.ai" in body


@pytest.mark.asyncio
async def test_a_dead_sign_in_gets_no_form(tmp_path):
    resp = await _provider(tmp_path).get_login_page("never-started")
    assert resp.status_code == 400 and b"password" not in resp.body.lower()


# ─── The allowlist holds at every token use ───────────────────────────────

@pytest.mark.asyncio
async def test_taking_an_email_off_the_allowlist_cuts_its_tokens_off(tmp_path):
    p = _provider(tmp_path)
    issued_code(p, email="ok@x.com")
    client = SimpleNamespace(client_id="c1")
    tok = await p.exchange_authorization_code(client, SimpleNamespace(code="authcode1", scopes=["garmin"]))
    assert await p.load_access_token(tok.access_token) is not None

    p.allowed_emails = frozenset()
    assert await p.load_access_token(tok.access_token) is None
    assert await p.load_refresh_token(client, tok.refresh_token) is None

    p.allowed_emails = frozenset({"ok@x.com"})  # restored: the tokens were kept
    assert await p.load_access_token(tok.access_token) is not None


# ─── The login handler refuses dead sign-ins before Garmin ─────────────────

@pytest.mark.asyncio
async def test_a_login_with_no_live_sign_in_never_reaches_garmin(tmp_path, monkeypatch):
    _no_garmin(monkeypatch)
    resp = await _provider(tmp_path).handle_login_callback(
        _Req({"state": "made-up", "email": "ok@x.com", "password": "guess"})
    )
    assert resp.status_code == 400 and b"expired" in resp.body.lower()


# ─── Token-import refusals say nothing of the allowlist ────────────────────

@pytest.mark.asyncio
async def test_a_wrong_import_secret_reads_the_same_for_every_email(tmp_path, monkeypatch):
    _no_garmin(monkeypatch)
    p = _provider(tmp_path, session_manager=SessionManager(str(tmp_path / "s")), import_secret="sek")
    pending_sign_in(p, "s")
    form = {"state": "s", "password": "", "garmin_token": "{}", "import_secret": "wrong"}
    on = await p.handle_login_callback(_Req({**form, "email": "ok@x.com"}))
    off = await p.handle_login_callback(_Req({**form, "email": "nope@x.com"}, peer="1.2.3.4"))
    assert on.body == off.body and b"Invalid import secret" in on.body


@pytest.mark.asyncio
async def test_a_non_ascii_import_secret_on_the_form_is_refused_not_an_error(tmp_path):
    p = _provider(tmp_path, session_manager=SessionManager(str(tmp_path / "s")), import_secret="sek")
    pending_sign_in(p, "s")
    resp = await p.handle_login_callback(_Req(
        {"state": "s", "email": "ok@x.com", "password": "", "garmin_token": "{}", "import_secret": "é"}
    ))
    assert resp.status_code == 200 and b"Invalid import secret" in resp.body


@pytest.mark.asyncio
async def test_a_correct_secret_for_an_unlisted_email_gets_the_generic_error(tmp_path):
    p = _provider(tmp_path, session_manager=SessionManager(str(tmp_path / "s")), import_secret="sek")
    pending_sign_in(p, "s")
    resp = await p.handle_login_callback(_Req(
        {"state": "s", "email": "nope@x.com", "password": "", "garmin_token": "{}", "import_secret": "sek"}
    ))
    assert _GENERIC_LOGIN_ERROR.encode() in resp.body


# ─── Codes and refresh tokens are single use ───────────────────────────────

@pytest.mark.asyncio
async def test_an_authorization_code_redeems_once(tmp_path):
    p = _provider(tmp_path)
    issued_code(p)
    client, code = SimpleNamespace(client_id="c1"), SimpleNamespace(code="authcode1", scopes=["garmin"])
    await p.exchange_authorization_code(client, code)
    with pytest.raises(TokenError) as err:
        await p.exchange_authorization_code(client, code)
    assert err.value.error == "invalid_grant"


@pytest.mark.asyncio
async def test_a_refresh_token_redeems_once(tmp_path):
    p = _provider(tmp_path)
    issued_code(p)
    client = SimpleNamespace(client_id="c1")
    tok = await p.exchange_authorization_code(client, SimpleNamespace(code="authcode1", scopes=["garmin"]))
    rt = await p.load_refresh_token(client, tok.refresh_token)
    await p.exchange_refresh_token(client, rt, ["garmin"])
    with pytest.raises(TokenError):
        await p.exchange_refresh_token(client, rt, ["garmin"])


# ─── Stored rows cannot grow without bound ─────────────────────────────────

def _count(p, table):
    conn = p._get_conn()
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_starting_a_sign_in_purges_expired_ones(tmp_path):
    p = _provider(tmp_path)
    pending_sign_in(p, "stale")
    conn = p._get_conn()
    conn.execute("UPDATE auth_codes SET expires_at = ? WHERE code = 'stale'", (time.time() - 1,))
    conn.commit()
    conn.close()
    params = AuthorizationParams(state="x", scopes=["garmin"], code_challenge="c",
                                 redirect_uri=AnyUrl(CLAUDE_CALLBACK),
                                 redirect_uri_provided_explicitly=True)
    await p.authorize(SimpleNamespace(client_id="c1"), params)
    assert _count(p, "auth_codes") == 1


@pytest.mark.asyncio
async def test_registration_at_the_cap_drops_the_oldest_unused_client(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth_provider, "MAX_REGISTERED_CLIENTS", 3)
    p = _provider(tmp_path)
    for n in range(3):
        await p.register_client(_client(CLAUDE_CALLBACK, client_id=f"c{n}"))
    pending_sign_in(p, "busy", client_id="c0")  # c0 is in use, so c1 goes
    await p.register_client(_client(CLAUDE_CALLBACK, client_id="c3"))
    assert _count(p, "oauth_clients") == 3
    assert await p.get_client("c0") is not None and await p.get_client("c1") is None


@pytest.mark.asyncio
async def test_registration_is_refused_when_every_client_is_in_use(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth_provider, "MAX_REGISTERED_CLIENTS", 2)
    p = _provider(tmp_path)
    for n in range(2):
        await p.register_client(_client(CLAUDE_CALLBACK, client_id=f"c{n}"))
        pending_sign_in(p, f"busy{n}", client_id=f"c{n}")
    with pytest.raises(RegistrationError):
        await p.register_client(_client(CLAUDE_CALLBACK, client_id="c2"))


@pytest.mark.asyncio
async def test_register_and_authorize_are_rate_limited_per_ip():
    from garmin_mcp.remote import _OAuthEndpointRateLimit

    seen = []

    async def app(scope, receive, send):
        seen.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    limited = _OAuthEndpointRateLimit(app, {"/register": (2, 3600)})
    statuses = []

    async def send(message):
        if message["type"] == "http.response.start":
            statuses.append(message["status"])

    async def receive():  # pragma: no cover - the handlers read no body
        return {"type": "http.request", "body": b""}

    def scope(path, ip):
        return {"type": "http", "path": path, "method": "POST", "headers": [],
                "client": (ip, 1), "query_string": b""}

    for _ in range(3):
        await limited(scope("/register", "9.9.9.9"), receive, send)
    await limited(scope("/register", "8.8.8.8"), receive, send)
    await limited(scope("/token", "9.9.9.9"), receive, send)
    assert statuses == [200, 200, 429, 200, 200]
    assert seen.count("/register") == 3


# ─── Saved reports fail closed ─────────────────────────────────────────────

def test_an_unidentified_remote_caller_gets_no_report_store(tmp_path, monkeypatch):
    import garmin_mcp.analytics as an

    saved = cr._session_manager, cr._global_client
    try:
        cr.set_session_manager(SessionManager(str(tmp_path / "s")))
        monkeypatch.setattr(an, "get_user_id", lambda ctx=None: None)
        with pytest.raises(RuntimeError):
            an._report_store_path(object())
    finally:
        cr._session_manager, cr._global_client = saved
