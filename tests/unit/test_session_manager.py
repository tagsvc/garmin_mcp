"""
Unit tests for SessionManager token persistence.

Regression coverage for the garth -> garminconnect token-format bridge: the
login flow yields garth OAuth2 tokens, but garminconnect (used to restore
sessions and make API calls) persists/loads a single ``garmin_tokens.json`` with
a DI bearer token. Writing garth's two-file format here would make every
restore fail with a generic "session expired" error.

These tests verify the on-disk format only (no network / no live Garmin login).
"""

import os
from types import SimpleNamespace

import pytest

from garminconnect.client import Client
from garmin_mcp.session_manager import SessionManager

pytestmark = pytest.mark.unit


def _fake_oauth2(access="fake-di-token", refresh="fake-di-refresh"):
    # Mimics the attributes SessionManager reads off a garth OAuth2Token.
    return SimpleNamespace(access_token=access, refresh_token=refresh)


def test_persists_in_garminconnect_native_format(tmp_path):
    sm = SessionManager(str(tmp_path))
    sm.create_session_from_garth_tokens("user-1", oauth1_token=None, oauth2_token=_fake_oauth2())

    token_dir = os.path.join(str(tmp_path), "user-1")
    files = os.listdir(token_dir)

    # garminconnect restores from garmin_tokens.json, NOT the
    # garth oauth1_token.json / oauth2_token.json pair.
    assert files == ["garmin_tokens.json"]
    assert "oauth1_token.json" not in files
    assert "oauth2_token.json" not in files


def test_persisted_tokens_load_into_garminconnect_client(tmp_path):
    sm = SessionManager(str(tmp_path))
    sm.create_session_from_garth_tokens(
        "user-1", oauth1_token=None, oauth2_token=_fake_oauth2(access="di-bearer-xyz")
    )
    token_dir = os.path.join(str(tmp_path), "user-1")

    # A fresh garminconnect client must be able to load what we wrote and end up
    # authenticated (di_token present). This is exactly the load step that runs
    # inside get_client() -> Garmin().login(token_dir) before the live API call.
    client = Client()
    client.load(token_dir)
    assert client.di_token == "di-bearer-xyz"
    assert client.is_authenticated is True


# ─── one user's session work never blocks another's ──────────────────────

import json
import threading

from garmin_mcp import session_manager as session_module
from garmin_mcp.session_manager import _CachedClient

_BLOB = json.dumps({"di_token": "di-new", "di_refresh_token": "r-new", "di_client_id": "c"})


class _BlockingGarmin:
    """Stands in for garminconnect.Garmin: a login from a directory named
    "slow" waits until released, any other returns at once."""

    release = threading.Event()

    def __init__(self):
        self.client = SimpleNamespace(_tokenstore_path=None)

    def login(self, token_dir):
        if os.path.basename(token_dir) == "slow":
            assert self.release.wait(5), "the slow restore was never released"
        self.client._tokenstore_path = token_dir


def test_a_slow_restore_does_not_block_another_users_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "Garmin", _BlockingGarmin)
    _BlockingGarmin.release.clear()
    sm = SessionManager(str(tmp_path))
    os.makedirs(tmp_path / "slow")
    os.makedirs(tmp_path / "fast")

    slow = threading.Thread(target=sm.get_client, args=("slow",))
    slow.start()
    try:
        fast_result = []
        fast = threading.Thread(target=lambda: fast_result.append(sm.get_client("fast")))
        fast.start()
        fast.join(timeout=2)
        assert not fast.is_alive(), "user 'fast' waited on user 'slow''s restore"
        assert fast_result and fast_result[0] is not None
    finally:
        _BlockingGarmin.release.set()
        slow.join(timeout=5)


def test_a_replaced_session_stops_the_old_clients_token_writes(tmp_path):
    """garminconnect saves a token refresh to the directory the client loaded
    from; a client still in use after an import must not write the replaced
    tokens back over the imported ones."""
    sm = SessionManager(str(tmp_path))
    old = SimpleNamespace(client=SimpleNamespace(_tokenstore_path=str(tmp_path / "u")))
    sm._cache["u"] = _CachedClient(old, sm.CACHE_TTL)

    sm.create_session_from_token_blob("u", _BLOB)

    assert old.client._tokenstore_path is None
    assert "u" not in sm._cache
    with open(tmp_path / "u" / "garmin_tokens.json") as f:
        assert json.load(f)["di_token"] == "di-new"


def test_removing_a_session_stops_the_old_clients_token_writes(tmp_path):
    sm = SessionManager(str(tmp_path))
    sm.create_session_from_token_blob("u", _BLOB)
    old = SimpleNamespace(client=SimpleNamespace(_tokenstore_path=str(tmp_path / "u")))
    sm._cache["u"] = _CachedClient(old, sm.CACHE_TTL)

    assert sm.remove_session("u") is True

    assert old.client._tokenstore_path is None
    assert not os.path.exists(tmp_path / "u")


def test_garminconnect_saves_refreshes_to_the_loaded_directory(tmp_path):
    """_stop_token_writes relies on this attribute; a garminconnect version
    without it would let a retired client write tokens back."""
    sm = SessionManager(str(tmp_path))
    sm.create_session_from_token_blob("u", _BLOB)

    client = Client()
    client.load(str(tmp_path / "u"))

    assert client._tokenstore_path
