"""Access tokens issued to an MCP client last a day; refresh tokens 30 days.

The client renews its access token with the refresh token, and a renewal it
misses leaves the connector refused until the user signs in again. A one-day
access token keeps renewals to about one a day.
"""
import sqlite3
import time
from types import SimpleNamespace

import pytest

from garmin_mcp import oauth_provider
from garmin_mcp.oauth_provider import (
    ACCESS_TOKEN_TTL,
    REFRESH_TOKEN_TTL,
    GarminOAuthProvider,
)

pytestmark = pytest.mark.unit

DAY = 86400
CLIENT = SimpleNamespace(client_id="c1")


def _provider(tmp_path):
    return GarminOAuthProvider(
        db_path=str(tmp_path / "t.db"), server_url="https://example.com"
    )


def _latest_expiries(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    try:
        access = conn.execute("SELECT max(expires_at) FROM access_tokens").fetchone()[0]
        refresh = conn.execute("SELECT max(expires_at) FROM refresh_tokens").fetchone()[0]
    finally:
        conn.close()
    return access, refresh


async def _sign_in(p):
    return await p.exchange_authorization_code(
        CLIENT, SimpleNamespace(code="authcode1", scopes=["garmin"])
    )


def test_the_lifetimes_are_a_day_and_30_days():
    assert ACCESS_TOKEN_TTL == DAY
    assert REFRESH_TOKEN_TTL == 30 * DAY


@pytest.mark.asyncio
async def test_sign_in_issues_a_one_day_access_token(tmp_path):
    p = _provider(tmp_path)
    before = time.time()
    tok = await _sign_in(p)
    after = time.time()

    assert tok.expires_in == DAY
    access, refresh = _latest_expiries(tmp_path)
    assert before + DAY <= access <= after + DAY
    assert before + 30 * DAY <= refresh <= after + 30 * DAY


@pytest.mark.asyncio
async def test_renewal_issues_the_same_lifetimes(tmp_path):
    p = _provider(tmp_path)
    tok = await _sign_in(p)
    rt = await p.load_refresh_token(CLIENT, tok.refresh_token)

    before = time.time()
    new = await p.exchange_refresh_token(CLIENT, rt, ["garmin"])
    after = time.time()

    assert new.expires_in == DAY
    access, refresh = _latest_expiries(tmp_path)
    assert before + DAY <= access <= after + DAY
    assert before + 30 * DAY <= refresh <= after + 30 * DAY


@pytest.mark.asyncio
async def test_an_access_token_is_accepted_for_a_day_and_refused_after(tmp_path, monkeypatch):
    p = _provider(tmp_path)
    issued = time.time()
    tok = await _sign_in(p)

    def at(seconds_later):
        monkeypatch.setattr(
            oauth_provider, "time", SimpleNamespace(time=lambda: issued + seconds_later)
        )

    at(DAY - 60)
    assert await p.load_access_token(tok.access_token) is not None
    at(DAY + 60)
    assert await p.load_access_token(tok.access_token) is None
