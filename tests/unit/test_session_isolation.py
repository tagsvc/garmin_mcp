"""Guards proving the 'anyone-with-the-URL inherits the session' vector is closed.

In remote mode there is no shared/ambient Garmin client, and a Garmin session is
reachable only via an OAuth access token bound to that user's id. These tests fail
if a future change reintroduces a shared session or breaks per-user binding.
"""
import pytest

import garmin_mcp.client_resolver as cr
from garmin_mcp.session_manager import SessionManager


@pytest.fixture
def restore_resolver_globals():
    saved_client = cr._global_client
    saved_sm = cr._session_manager
    yield
    cr._global_client = saved_client
    cr._session_manager = saved_sm


def test_unknown_token_maps_to_no_user(tmp_path):
    """A token that was never issued resolves to no user (no identity to borrow)."""
    sm = SessionManager(str(tmp_path))
    assert sm.get_user_id_for_token("never-issued-token") is None


def test_access_tokens_are_isolated_per_user(tmp_path):
    """One user's token never resolves to another user's id."""
    sm = SessionManager(str(tmp_path))
    sm.set_token_user_mapping("tok-A", "user-A")
    sm.set_token_user_mapping("tok-B", "user-B")
    assert sm.get_user_id_for_token("tok-A") == "user-A"
    assert sm.get_user_id_for_token("tok-B") == "user-B"
    assert sm.get_user_id_for_token("tok-C") is None  # unmapped → nothing


def test_remote_mode_has_no_ambient_client(monkeypatch, tmp_path, restore_resolver_globals):
    """With no global client (remote mode), a request whose token maps to no user
    must raise — never fall back to some already-authenticated session."""
    sm = SessionManager(str(tmp_path))
    cr.set_session_manager(sm)
    cr.set_global_client(None)  # remote mode: there is no shared client

    class _Tok:
        token = "unmapped-token"

    monkeypatch.setattr(
        "mcp.server.auth.middleware.auth_context.get_access_token",
        lambda: _Tok(),
    )

    with pytest.raises(RuntimeError):
        cr.get_client(ctx=object())


def test_no_token_no_client(monkeypatch, tmp_path, restore_resolver_globals):
    """No access token at all and no global client → no access (raises)."""
    sm = SessionManager(str(tmp_path))
    cr.set_session_manager(sm)
    cr.set_global_client(None)

    monkeypatch.setattr(
        "mcp.server.auth.middleware.auth_context.get_access_token",
        lambda: None,
    )

    with pytest.raises(RuntimeError):
        cr.get_client(ctx=object())
