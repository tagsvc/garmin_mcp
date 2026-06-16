"""Unit tests for importing pre-minted Garmin tokens (429 IP-throttle workaround)."""
import base64
import json
import os

import pytest

from garmin_mcp.session_manager import SessionManager
from garmin_mcp.oauth_provider import GarminOAuthProvider


VALID_BLOB = json.dumps(
    {"di_token": "AAA", "di_refresh_token": "BBB", "di_client_id": "CCC"}
)


# ─── SessionManager.create_session_from_token_blob ────────────────────────


def test_import_raw_json_writes_token_file(tmp_path):
    sm = SessionManager(str(tmp_path))
    sm.create_session_from_token_blob("user-1", VALID_BLOB)
    token_file = tmp_path / "user-1" / "garmin_tokens.json"
    assert token_file.exists()
    assert json.loads(token_file.read_text())["di_token"] == "AAA"
    assert sm.has_session("user-1")


def test_import_base64_blob(tmp_path):
    sm = SessionManager(str(tmp_path))
    encoded = base64.b64encode(VALID_BLOB.encode()).decode()
    sm.create_session_from_token_blob("user-2", encoded)
    assert (tmp_path / "user-2" / "garmin_tokens.json").exists()


def test_import_empty_blob_raises(tmp_path):
    sm = SessionManager(str(tmp_path))
    with pytest.raises(ValueError):
        sm.create_session_from_token_blob("user-3", "   ")


def test_import_garbage_raises(tmp_path):
    sm = SessionManager(str(tmp_path))
    with pytest.raises(ValueError):
        sm.create_session_from_token_blob("user-4", "not json at all !!!")


def test_import_json_missing_fields_raises(tmp_path):
    sm = SessionManager(str(tmp_path))
    with pytest.raises(ValueError):
        sm.create_session_from_token_blob("user-5", json.dumps({"foo": "bar"}))


# ─── OAuth provider login callback: token-import branch ───────────────────


class _FakeRequest:
    def __init__(self, data):
        self._data = data

    async def form(self):
        return self._data


@pytest.fixture
def provider(tmp_path):
    sm = SessionManager(str(tmp_path / "sessions"))
    return GarminOAuthProvider(
        db_path=str(tmp_path / "test.db"),
        server_url="https://example.com",
        session_manager=sm,
        allowed_emails={"allowed@example.com"},
    )


@pytest.mark.asyncio
async def test_login_callback_token_import_skips_sso(provider, monkeypatch):
    """Pasting a token must persist a session WITHOUT any Garmin SSO call."""
    import garth

    def _boom(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("garth SSO must not be called on token import")

    monkeypatch.setattr(garth.sso, "login", _boom)
    monkeypatch.setattr(provider, "_complete_auth_flow", lambda state, uid: "OK")

    request = _FakeRequest(
        {
            "state": "abc",
            "email": "allowed@example.com",
            "password": "",
            "garmin_token": VALID_BLOB,
        }
    )
    result = await provider.handle_login_callback(request)

    assert result == "OK"
    # A session was persisted for the user mapped to the email.
    user_id = provider._get_or_create_user("allowed@example.com")
    assert provider.session_manager.has_session(user_id)


@pytest.mark.asyncio
async def test_login_callback_token_import_rejects_non_allowlisted(provider):
    request = _FakeRequest(
        {
            "state": "abc",
            "email": "blocked@example.com",
            "password": "",
            "garmin_token": VALID_BLOB,
        }
    )
    response = await provider.handle_login_callback(request)
    assert response.status_code == 200
    assert b"not authorized" in response.body


@pytest.mark.asyncio
async def test_login_callback_requires_password_or_token(provider):
    request = _FakeRequest(
        {"state": "abc", "email": "allowed@example.com", "password": "", "garmin_token": ""}
    )
    response = await provider.handle_login_callback(request)
    assert response.status_code == 200
    assert b"password" in response.body.lower()


# ─── /import-token endpoint ───────────────────────────────────────────────


class _FakeJsonRequest:
    def __init__(self, body, headers=None):
        self._body = body
        self.headers = headers or {}

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


@pytest.fixture
def secret_provider(tmp_path):
    sm = SessionManager(str(tmp_path / "sessions"))
    return GarminOAuthProvider(
        db_path=str(tmp_path / "test.db"),
        server_url="https://example.com",
        session_manager=sm,
        allowed_emails={"allowed@example.com"},
        import_secret="s3cr3t",
    )


@pytest.mark.asyncio
async def test_import_endpoint_disabled_without_secret(tmp_path):
    sm = SessionManager(str(tmp_path / "sessions"))
    provider = GarminOAuthProvider(
        db_path=str(tmp_path / "test.db"),
        server_url="https://example.com",
        session_manager=sm,
        allowed_emails={"allowed@example.com"},
        import_secret="",
    )
    req = _FakeJsonRequest(
        {"email": "allowed@example.com", "token": VALID_BLOB},
        {"X-Import-Secret": "anything"},
    )
    resp = await provider.handle_import_token(req)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_import_endpoint_rejects_bad_secret(secret_provider):
    req = _FakeJsonRequest(
        {"email": "allowed@example.com", "token": VALID_BLOB},
        {"X-Import-Secret": "wrong"},
    )
    resp = await secret_provider.handle_import_token(req)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_import_endpoint_rejects_non_allowlisted(secret_provider):
    req = _FakeJsonRequest(
        {"email": "blocked@example.com", "token": VALID_BLOB},
        {"X-Import-Secret": "s3cr3t"},
    )
    resp = await secret_provider.handle_import_token(req)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_import_endpoint_requires_fields(secret_provider):
    req = _FakeJsonRequest(
        {"email": "allowed@example.com"}, {"X-Import-Secret": "s3cr3t"}
    )
    resp = await secret_provider.handle_import_token(req)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_import_endpoint_success(secret_provider):
    req = _FakeJsonRequest(
        {"email": "allowed@example.com", "token": VALID_BLOB},
        {"X-Import-Secret": "s3cr3t"},
    )
    resp = await secret_provider.handle_import_token(req)
    assert resp.status_code == 200
    user_id = secret_provider._get_or_create_user("allowed@example.com")
    assert secret_provider.session_manager.has_session(user_id)


@pytest.mark.asyncio
async def test_import_endpoint_rejects_invalid_token(secret_provider):
    req = _FakeJsonRequest(
        {"email": "allowed@example.com", "token": "garbage"},
        {"X-Import-Secret": "s3cr3t"},
    )
    resp = await secret_provider.handle_import_token(req)
    assert resp.status_code == 400
