"""Phase-1 security hardening regression tests.

Covers: (1) reflected-XSS escaping on the login/MFA pages, (2) rate limiting on
the auth endpoints, (3) token-at-rest hashing of access/refresh tokens.
"""
import hashlib
import sqlite3
from types import SimpleNamespace

import pytest

from garmin_mcp.oauth_provider import (
    GarminOAuthProvider,
    _PendingMfa,
    _RateLimiter,
)
from garmin_mcp.session_manager import SessionManager


def _provider(tmp_path, **kw):
    return GarminOAuthProvider(
        db_path=str(tmp_path / "t.db"),
        server_url="https://example.com",
        **kw,
    )


class _FakeReq:
    def __init__(self, form=None, headers=None, body=None):
        self._form = dict(form or {})
        self.headers = headers or {}
        self._body = body if body is not None else {}

    async def form(self):
        return self._form

    async def json(self):
        return self._body


# ─── Fix 1: reflected-XSS escaping ────────────────────────────────────────

XSS = '"><script>alert(1)</script>'


@pytest.mark.asyncio
async def test_login_page_escapes_state(tmp_path):
    resp = await _provider(tmp_path).get_login_page(XSS)
    assert b"<script>alert(1)" not in resp.body
    assert b"&lt;script&gt;" in resp.body


@pytest.mark.asyncio
async def test_mfa_page_escapes_state(tmp_path):
    p = _provider(tmp_path)
    p._pending_mfa[XSS] = _PendingMfa(client_state={}, garmin_email="x@y.com")
    resp = await p.get_mfa_page(XSS)
    assert b"<script>alert(1)" not in resp.body
    assert b"&lt;script&gt;" in resp.body


@pytest.mark.asyncio
async def test_error_message_escaped(tmp_path):
    resp = await _provider(tmp_path).get_login_page("s", error="<img src=x onerror=alert(1)>")
    assert b"<img src=x" not in resp.body


# ─── Fix 2: rate limiting ─────────────────────────────────────────────────

def test_ratelimiter_blocks_after_max_and_isolates_keys():
    rl = _RateLimiter(max_attempts=3, window_seconds=300)
    assert [rl.allow("k") for _ in range(3)] == [True, True, True]
    assert rl.allow("k") is False
    assert rl.allow("other") is True  # independent key


@pytest.mark.asyncio
async def test_login_handler_rate_limited(tmp_path):
    p = _provider(tmp_path, allowed_emails={"ok@x.com"})
    form = {"state": "s", "email": "nope@x.com", "password": "p"}
    for _ in range(8):  # allowed (each rejected by allowlist, not rate limit)
        r = await p.handle_login_callback(_FakeReq(form=form))
        assert b"Too many attempts" not in r.body
    r = await p.handle_login_callback(_FakeReq(form=form))
    assert b"Too many attempts" in r.body


@pytest.mark.asyncio
async def test_import_endpoint_rate_limited(tmp_path):
    p = _provider(
        tmp_path,
        session_manager=SessionManager(str(tmp_path / "s")),
        import_secret="sek",
    )
    headers = {"x-forwarded-for": "9.9.9.9", "X-Import-Secret": "wrong"}
    for _ in range(10):  # allowed (each 401 bad secret)
        assert (await p.handle_import_token(_FakeReq(headers=headers))).status_code == 401
    assert (await p.handle_import_token(_FakeReq(headers=headers))).status_code == 429


@pytest.mark.asyncio
async def test_mfa_handler_rate_limited(tmp_path, monkeypatch):
    import garth

    def _boom(*a, **k):
        raise RuntimeError("bad code")

    monkeypatch.setattr(garth.sso, "resume_login", _boom)
    p = _provider(tmp_path)
    state = "mfastate"
    p._pending_mfa[state] = _PendingMfa(
        client_state={"client": None, "signin_params": {}}, garmin_email="x@y.com"
    )
    form = {"state": state, "mfa_code": "123456"}
    for _ in range(8):
        r = await p.handle_mfa_callback(_FakeReq(form=form))
        assert b"Too many attempts" not in r.body
    r = await p.handle_mfa_callback(_FakeReq(form=form))
    assert b"Too many attempts" in r.body


# ─── Fix 3: token-at-rest hashing ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_tokens_hashed_at_rest_and_roundtrip(tmp_path):
    p = _provider(tmp_path)
    client = SimpleNamespace(client_id="c1")
    ac = SimpleNamespace(code="authcode1", scopes=["garmin"])
    tok = await p.exchange_authorization_code(client, ac)

    conn = sqlite3.connect(str(tmp_path / "t.db"))
    db_access = [r[0] for r in conn.execute("SELECT token FROM access_tokens")]
    db_refresh = [r[0] for r in conn.execute("SELECT token FROM refresh_tokens")]
    conn.close()

    # Plaintext is never stored; the SHA-256 is.
    assert tok.access_token not in db_access
    assert tok.refresh_token not in db_refresh
    assert hashlib.sha256(tok.access_token.encode()).hexdigest() in db_access

    # Lookups by plaintext still work; wrong tokens don't.
    at = await p.load_access_token(tok.access_token)
    assert at is not None and at.token == tok.access_token
    assert await p.load_access_token("not-a-real-token") is None

    # Refresh round-trip: old refresh is consumed, new access differs.
    rt = await p.load_refresh_token(client, tok.refresh_token)
    assert rt is not None and rt.token == tok.refresh_token
    new = await p.exchange_refresh_token(client, rt, ["garmin"])
    assert new.access_token != tok.access_token
    assert await p.load_refresh_token(client, tok.refresh_token) is None

    # Revoke by plaintext.
    at2 = await p.load_access_token(new.access_token)
    await p.revoke_token(at2)
    assert await p.load_access_token(new.access_token) is None


def test_token_hash_migration_is_safe_and_idempotent(tmp_path):
    db = str(tmp_path / "m.db")
    GarminOAuthProvider(db_path=db, server_url="https://x")  # creates tables

    # Simulate a pre-hashing-era plaintext token row.
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO access_tokens (token, client_id, user_id, scopes, expires_at) "
        "VALUES (?,?,?,?,?)",
        ("PLAINTEXT_token_value", "c1", "u1", "garmin", 9_999_999_999),
    )
    conn.commit()
    conn.close()

    # Re-init triggers the migration.
    GarminOAuthProvider(db_path=db, server_url="https://x")
    conn = sqlite3.connect(db)
    toks = [r[0] for r in conn.execute("SELECT token FROM access_tokens")]
    conn.close()
    assert "PLAINTEXT_token_value" not in toks
    assert hashlib.sha256(b"PLAINTEXT_token_value").hexdigest() in toks

    # Idempotent: a further re-init must not double-hash.
    GarminOAuthProvider(db_path=db, server_url="https://x")
    conn = sqlite3.connect(db)
    toks2 = [r[0] for r in conn.execute("SELECT token FROM access_tokens")]
    conn.close()
    assert toks2 == toks
