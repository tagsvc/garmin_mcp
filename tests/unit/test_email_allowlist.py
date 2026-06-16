"""Unit tests for the email allowlist (config + OAuth provider)."""
import pytest

from garmin_mcp.config import RemoteConfig
from garmin_mcp.oauth_provider import GarminOAuthProvider


# ─── RemoteConfig parsing / fail-closed ──────────────────────────────────


def test_config_parse_normalizes_and_dedupes():
    parsed = RemoteConfig._parse_allowed_emails(" A@B.com , c@d.com ,, a@b.com ")
    assert parsed == frozenset({"a@b.com", "c@d.com"})


def test_config_empty_is_fail_closed(monkeypatch):
    monkeypatch.delenv("GARMIN_ALLOWED_EMAILS", raising=False)
    monkeypatch.setenv("GARMIN_MCP_SERVER_URL", "https://example.com")
    config = RemoteConfig()
    assert config.allowed_emails == frozenset()
    assert config.is_email_allowed("anyone@example.com") is False


def test_config_allows_only_listed(monkeypatch):
    monkeypatch.setenv("GARMIN_ALLOWED_EMAILS", "ok@x.com")
    monkeypatch.setenv("GARMIN_MCP_SERVER_URL", "https://example.com")
    config = RemoteConfig()
    assert config.is_email_allowed("OK@X.com") is True  # case-insensitive
    assert config.is_email_allowed("nope@x.com") is False


# ─── GarminOAuthProvider enforcement ──────────────────────────────────────


@pytest.fixture
def provider(tmp_path):
    return GarminOAuthProvider(
        db_path=str(tmp_path / "test.db"),
        server_url="https://example.com",
        allowed_emails={"Allowed@Example.com"},
    )


def test_provider_allowlist_is_normalized(provider):
    assert provider.allowed_emails == frozenset({"allowed@example.com"})


def test_provider_is_email_allowed(provider):
    assert provider._is_email_allowed("allowed@example.com") is True
    assert provider._is_email_allowed("ALLOWED@EXAMPLE.COM") is True
    assert provider._is_email_allowed("other@example.com") is False


def test_provider_empty_allowlist_fail_closed(tmp_path):
    provider = GarminOAuthProvider(
        db_path=str(tmp_path / "test.db"),
        server_url="https://example.com",
        allowed_emails=None,
    )
    assert provider._is_email_allowed("anyone@example.com") is False


class _FakeRequest:
    def __init__(self, data):
        self._data = data

    async def form(self):
        return self._data


@pytest.mark.asyncio
async def test_login_callback_rejects_non_allowlisted_without_contacting_garmin(
    provider, monkeypatch
):
    """A disallowed email must be rejected before any Garmin login attempt."""
    import garth

    def _boom(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("garth login should not be attempted")

    monkeypatch.setattr(garth.sso, "login", _boom)

    request = _FakeRequest(
        {"state": "abc", "email": "blocked@example.com", "password": "pw"}
    )
    response = await provider.handle_login_callback(request)

    assert response.status_code == 200
    assert b"not authorized" in response.body


@pytest.mark.asyncio
async def test_login_callback_allows_listed_email_to_proceed(provider, monkeypatch):
    """An allowlisted email passes the gate and reaches the Garmin login call."""
    import garth

    called = {}

    def _fake_login(email, password, client=None, return_on_mfa=True):
        called["email"] = email
        return ("oauth1", "oauth2")

    monkeypatch.setattr(garth.sso, "login", _fake_login)
    monkeypatch.setattr(provider, "_get_or_create_user", lambda email: "user-1")
    monkeypatch.setattr(provider, "_complete_auth_flow", lambda state, uid: "OK")

    request = _FakeRequest(
        {"state": "abc", "email": "allowed@example.com", "password": "pw"}
    )
    result = await provider.handle_login_callback(request)

    assert called["email"] == "allowed@example.com"
    assert result == "OK"
