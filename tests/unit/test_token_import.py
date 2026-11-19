"""Unit tests for importing pre-minted Garmin tokens (429 IP-throttle workaround)."""
import base64
import json
import os

import pytest
from types import SimpleNamespace

from garmin_mcp.session_manager import SessionManager
from garmin_mcp.oauth_provider import GarminOAuthProvider
from tests.unit.oauth_helpers import pending_sign_in


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
    def __init__(self, data, headers=None, peer="203.0.113.7"):
        self._data = data
        self.headers = headers or {}
        self.client = SimpleNamespace(host=peer)

    async def form(self):
        return self._data


BROWSER_SECRET = "browser-import-s3cr3t"


@pytest.fixture
def provider(tmp_path):
    sm = SessionManager(str(tmp_path / "sessions"))
    p = GarminOAuthProvider(
        db_path=str(tmp_path / "test.db"),
        server_url="https://example.com",
        session_manager=sm,
        allowed_emails={"allowed@example.com"},
        import_secret=BROWSER_SECRET,
    )
    pending_sign_in(p, "abc")
    return p


@pytest.mark.asyncio
async def test_login_callback_token_import_skips_sso(provider, monkeypatch):
    """A correct email + secret + token must persist a session, no Garmin SSO."""
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
            "import_secret": BROWSER_SECRET,
        }
    )
    result = await provider.handle_login_callback(request)

    assert result == "OK"
    # A session was persisted for the user mapped to the email.
    user_id = provider._get_or_create_user("allowed@example.com")
    assert provider.session_manager.has_session(user_id)


@pytest.mark.asyncio
async def test_login_callback_token_import_rejects_wrong_secret(provider):
    """An allowlisted email but wrong secret must NOT overwrite the session."""
    request = _FakeRequest(
        {
            "state": "abc",
            "email": "allowed@example.com",
            "password": "",
            "garmin_token": VALID_BLOB,
            "import_secret": "wrong",
        }
    )
    response = await provider.handle_login_callback(request)
    assert response.status_code == 200
    assert b"Invalid import secret" in response.body
    user_id = provider._get_or_create_user("allowed@example.com")
    assert not provider.session_manager.has_session(user_id)


@pytest.mark.asyncio
async def test_login_callback_token_import_requires_secret(provider):
    """Omitting the secret entirely must be rejected (no session written)."""
    request = _FakeRequest(
        {
            "state": "abc",
            "email": "allowed@example.com",
            "password": "",
            "garmin_token": VALID_BLOB,
        }
    )
    response = await provider.handle_login_callback(request)
    assert response.status_code == 200
    assert b"Invalid import secret" in response.body
    user_id = provider._get_or_create_user("allowed@example.com")
    assert not provider.session_manager.has_session(user_id)


@pytest.mark.asyncio
async def test_login_callback_token_import_disabled_without_server_secret(tmp_path):
    """If the server has no import secret, browser token import is disabled."""
    sm = SessionManager(str(tmp_path / "sessions"))
    prov = GarminOAuthProvider(
        db_path=str(tmp_path / "test.db"),
        server_url="https://example.com",
        session_manager=sm,
        allowed_emails={"allowed@example.com"},
        import_secret="",
    )
    pending_sign_in(prov, "abc")
    request = _FakeRequest(
        {
            "state": "abc",
            "email": "allowed@example.com",
            "password": "",
            "garmin_token": VALID_BLOB,
            "import_secret": "anything",
        }
    )
    response = await prov.handle_login_callback(request)
    assert response.status_code == 200
    assert b"disabled" in response.body.lower()


@pytest.mark.asyncio
async def test_login_callback_token_import_rejects_non_allowlisted(provider):
    request = _FakeRequest(
        {
            "state": "abc",
            "email": "blocked@example.com",
            "password": "",
            "garmin_token": VALID_BLOB,
            "import_secret": BROWSER_SECRET,
        }
    )
    response = await provider.handle_login_callback(request)
    assert response.status_code == 200
    assert b"Invalid email or password." in response.body  # unified: no allowlist oracle


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


# ─── one user per account, whatever the email's case ──────────────────────

def test_the_same_email_in_another_case_is_the_same_user(provider):
    first = provider._get_or_create_user("Allowed@Example.com")

    assert provider._get_or_create_user("allowed@example.com") == first
    assert provider._get_or_create_user("  ALLOWED@example.COM ") == first


def _restarted(provider):
    """A provider on the same database and sessions, as after a redeploy."""
    return GarminOAuthProvider(
        db_path=provider.db_path,
        server_url="https://example.com",
        session_manager=SessionManager(provider.session_manager.storage_path),
        allowed_emails={"allowed@example.com"},
        import_secret=BROWSER_SECRET,
    )


def _insert_user(provider, user_id, email, created_at):
    conn = provider._get_conn()
    conn.execute(
        "INSERT INTO users (id, garmin_email, created_at) VALUES (?, ?, ?)",
        (user_id, email, created_at),
    )
    conn.commit()
    conn.close()


def _write_session(provider, user_id, di_token, mtime):
    token_dir = os.path.join(provider.session_manager.storage_path, user_id)
    os.makedirs(token_dir, exist_ok=True)
    path = os.path.join(token_dir, "garmin_tokens.json")
    with open(path, "w") as f:
        json.dump({"di_token": di_token, "di_refresh_token": "r", "di_client_id": "c"}, f)
    os.utime(path, (mtime, mtime))


def test_a_user_stored_in_mixed_case_is_found_after_a_restart(provider):
    """Rows stored in another case are normalized when the server starts."""
    _insert_user(provider, "old", "Allowed@Example.com", 1)

    assert _restarted(provider)._get_or_create_user("allowed@example.com") == "old"


def test_case_variants_become_the_oldest_user_at_startup(provider):
    _insert_user(provider, "newer", "allowed@example.com", 20)
    _insert_user(provider, "older", "Allowed@Example.com ", 10)

    restarted = _restarted(provider)

    assert restarted._get_or_create_user("ALLOWED@EXAMPLE.COM") == "older"
    conn = restarted._get_conn()
    rows = conn.execute("SELECT id, garmin_email FROM users").fetchall()
    conn.close()
    assert [(r["id"], r["garmin_email"]) for r in rows] == [("older", "allowed@example.com")]


def test_merged_users_tokens_follow_the_kept_user(provider):
    """A claude.ai connection signed in under the merged user keeps working:
    its tokens now resolve to the kept user."""
    _insert_user(provider, "newer", "allowed@example.com", 20)
    _insert_user(provider, "older", "Allowed@Example.com", 10)
    conn = provider._get_conn()
    conn.execute(
        "INSERT INTO access_tokens (token, client_id, user_id, scopes, expires_at) "
        "VALUES ('a', 'claude', 'newer', 'garmin', 9e9)"
    )
    conn.execute(
        "INSERT INTO refresh_tokens (token, client_id, user_id, scopes, expires_at) "
        "VALUES ('r', 'claude', 'newer', 'garmin', 9e9)"
    )
    conn.commit()
    conn.close()

    restarted = _restarted(provider)

    conn = restarted._get_conn()
    owners = {
        table: conn.execute(f"SELECT user_id FROM {table}").fetchone()["user_id"]
        for table in ("access_tokens", "refresh_tokens")
    }
    conn.close()
    assert owners == {"access_tokens": "older", "refresh_tokens": "older"}


def test_merged_users_keep_the_newer_garmin_session(provider):
    _insert_user(provider, "newer", "allowed@example.com", 20)
    _insert_user(provider, "older", "Allowed@Example.com", 10)
    _write_session(provider, "older", "stale", mtime=1000)
    _write_session(provider, "newer", "fresh", mtime=2000)

    restarted = _restarted(provider)

    storage = restarted.session_manager.storage_path
    with open(os.path.join(storage, "older", "garmin_tokens.json")) as f:
        assert json.load(f)["di_token"] == "fresh"
    assert not os.path.exists(os.path.join(storage, "newer"))


def test_merged_users_keep_the_kept_users_newer_session(provider):
    _insert_user(provider, "newer", "allowed@example.com", 20)
    _insert_user(provider, "older", "Allowed@Example.com", 10)
    _write_session(provider, "older", "fresh", mtime=2000)
    _write_session(provider, "newer", "stale", mtime=1000)

    restarted = _restarted(provider)

    storage = restarted.session_manager.storage_path
    with open(os.path.join(storage, "older", "garmin_tokens.json")) as f:
        assert json.load(f)["di_token"] == "fresh"
    assert not os.path.exists(os.path.join(storage, "newer"))


def test_merged_users_saved_reports_are_combined(provider, tmp_path, monkeypatch):
    monkeypatch.setenv("GARMIN_REPORTS_PATH", str(tmp_path / "reports.json"))
    _insert_user(provider, "newer", "allowed@example.com", 20)
    _insert_user(provider, "older", "Allowed@Example.com", 10)
    (tmp_path / "reports.newer.json").write_text(json.dumps({"a": {"v": "newer"}, "b": {"v": "newer"}}))
    (tmp_path / "reports.older.json").write_text(json.dumps({"b": {"v": "older"}}))

    _restarted(provider)

    assert json.loads((tmp_path / "reports.older.json").read_text()) == {
        "a": {"v": "newer"},
        "b": {"v": "older"},
    }
    assert not (tmp_path / "reports.newer.json").exists()


def test_emails_fold_case_the_way_the_allowlist_does(provider):
    """Non-ASCII capitals fold too, as Python's str.lower does for the allowlist."""
    _insert_user(provider, "old", "\u00c9lodie@example.com", 1)

    restarted = _restarted(provider)

    assert restarted._get_or_create_user("\u00e9lodie@example.com") == "old"
    assert restarted._get_or_create_user("\u00c9LODIE@EXAMPLE.COM") == "old"


def test_the_startup_merge_is_idempotent(provider):
    _insert_user(provider, "newer", "allowed@example.com", 20)
    _insert_user(provider, "older", "Allowed@Example.com", 10)

    again = _restarted(_restarted(provider))

    assert again._get_or_create_user("allowed@example.com") == "older"


@pytest.mark.asyncio
async def test_a_non_ascii_import_secret_is_refused_not_an_error(secret_provider):
    req = _FakeJsonRequest(
        {"email": "allowed@example.com", "token": VALID_BLOB},
        {"X-Import-Secret": "s3cr3t\u00e9"},
    )

    resp = await secret_provider.handle_import_token(req)

    assert resp.status_code == 401
