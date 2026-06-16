"""Unit tests for auth_tools and the token_utils functions it depends on."""
import json
import os

import pytest
from unittest.mock import Mock, patch
from mcp.server.fastmcp import FastMCP

from garmin_mcp import auth_tools, token_utils


# ─── token_utils additions ────────────────────────────────────────────────


def test_resolve_path_is_absolute_and_expands_user():
    resolved = token_utils.resolve_path("~/.garminconnect")
    assert os.path.isabs(resolved)
    assert resolved == os.path.abspath(os.path.expanduser("~/.garminconnect"))


def test_resolve_path_ignores_dxt_placeholder():
    resolved = token_utils.resolve_path("${user_config.tokens}", default="~/fallback")
    assert resolved.endswith("fallback")


def test_without_token_env_restores_environment(monkeypatch):
    monkeypatch.setenv("GARMINTOKENS", "/some/path")
    monkeypatch.setenv("GARMINTOKENS_BASE64", "/some/b64")
    with token_utils.without_token_env():
        assert "GARMINTOKENS" not in os.environ
        assert "GARMINTOKENS_BASE64" not in os.environ
    assert os.environ["GARMINTOKENS"] == "/some/path"
    assert os.environ["GARMINTOKENS_BASE64"] == "/some/b64"


def test_ensure_token_directory_creates_dir(tmp_path):
    target = tmp_path / "tokens"
    created = token_utils.ensure_token_directory(str(target))
    assert os.path.isdir(created)


def test_ensure_token_directory_replaces_empty_placeholder_file(tmp_path):
    placeholder = tmp_path / "tokens"
    placeholder.write_text("")  # empty placeholder file
    created = token_utils.ensure_token_directory(str(placeholder))
    assert os.path.isdir(created)


# ─── auth_tools ───────────────────────────────────────────────────────────


@pytest.fixture
def app_with_auth_tools():
    auth_tools.configure(lambda client: None)
    app = FastMCP("Test Auth")
    return auth_tools.register_tools(app)


@pytest.mark.asyncio
async def test_auth_tools_registered(app_with_auth_tools):
    names = {t.name for t in await app_with_auth_tools.list_tools()}
    assert {"check_garmin_auth", "login_to_garmin"}.issubset(names)


@pytest.mark.asyncio
async def test_check_garmin_auth_reports_valid(app_with_auth_tools):
    info = {
        "path": "~/.garminconnect",
        "expanded_path": "/home/u/.garminconnect",
        "exists": True,
        "valid": True,
        "error": "",
    }
    with patch("garmin_mcp.auth_tools.get_token_info", return_value=info):
        result = await app_with_auth_tools.call_tool("check_garmin_auth", {})
    payload = json.loads(result[0][0].text)
    assert payload["authenticated"] is True


@pytest.mark.asyncio
async def test_check_garmin_auth_reports_missing(app_with_auth_tools):
    info = {
        "path": "~/.garminconnect",
        "expanded_path": "/home/u/.garminconnect",
        "exists": False,
        "valid": False,
        "error": "",
    }
    with patch("garmin_mcp.auth_tools.get_token_info", return_value=info):
        result = await app_with_auth_tools.call_tool("check_garmin_auth", {})
    payload = json.loads(result[0][0].text)
    assert payload["authenticated"] is False
    assert "login_to_garmin" in payload["next_step"]
