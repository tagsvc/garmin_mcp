"""The stdio/remote contract.

Two rules keep upstream merges from silently breaking or endangering the remote
server, and both are enforced here rather than by review:

1. Every tool module must be `configure()`d in remote mode, so the
   `garmin_client` global resolves the calling user's client.
2. Any tool taking a filesystem path must refuse it in remote mode, because a
   path there names the SERVER's disk, not the caller's.

Rule 2 used to be enforced by accident: an unconfigured global made such tools
crash. Rule 1 removes that crash, so rule 2 needs a real test.
"""
import inspect
import re

import pytest
from mcp.server.fastmcp import FastMCP

import garmin_mcp.client_resolver as cr
from garmin_mcp import remote
from garmin_mcp.client_resolver import _ResolvingGarminProxy
from garmin_mcp.session_manager import SessionManager


# ─── Rule 1: every registered module is configured ────────────────────────

def _registered_modules_in_source():
    """Modules remote.py calls register_tools() on, read from its source."""
    src = inspect.getsource(remote)
    return set(re.findall(r"app = (\w+)\.register_tools\(app\)", src))


def test_every_registered_module_is_also_configured():
    """A module registered but not configured has a None garmin_client.

    Upstream tools use that global directly, so the mismatch is invisible until
    a user calls the tool and gets AttributeError on NoneType.
    """
    configured = {m.__name__.rsplit(".", 1)[-1] for m in remote._CONFIGURED_MODULES}
    registered = _registered_modules_in_source()

    assert registered - configured == set(), (
        "these modules register tools but are never configure()d in remote.py: "
        f"{sorted(registered - configured)}"
    )
    assert configured - registered == set(), (
        "these modules are configured but register no tools: "
        f"{sorted(configured - registered)}"
    )


def test_every_configured_module_exposes_configure():
    for module in remote._CONFIGURED_MODULES:
        assert hasattr(module, "configure"), f"{module.__name__} has no configure()"


# ─── The proxy resolves per caller, and fails closed ──────────────────────

@pytest.fixture
def restore_resolver():
    saved = cr._session_manager, cr._global_client
    yield
    cr._session_manager, cr._global_client = saved


def test_proxy_resolves_the_calling_users_client(monkeypatch, restore_resolver):
    """Two concurrent users must never share a client.

    The proxy is a single module-level object, so the only thing keeping their
    calls apart is that resolution happens on every attribute access rather
    than once at configure() time. Flip the resolved client between calls and
    the proxy must follow it.
    """
    from unittest.mock import Mock

    alice, bob = Mock(name="alice"), Mock(name="bob")
    alice.get_activities.return_value = ["alice-run"]
    bob.get_activities.return_value = ["bob-ride"]

    current = {"client": alice}
    monkeypatch.setattr(cr, "get_client", lambda ctx=None: current["client"])

    proxy = _ResolvingGarminProxy()

    assert proxy.get_activities() == ["alice-run"]
    current["client"] = bob
    assert proxy.get_activities() == ["bob-ride"]

    alice.get_activities.assert_called_once()
    bob.get_activities.assert_called_once()


def test_proxy_holds_no_client_of_its_own():
    """State on the proxy would be shared across users -- there must be none."""
    proxy = _ResolvingGarminProxy()
    assert not getattr(proxy, "__dict__", {}), (
        "the proxy must stay stateless; an attribute here would be shared "
        "between concurrent users"
    )


def test_resolution_fails_closed_without_a_token(tmp_path, restore_resolver):
    """No access token in context must raise, never fall back to someone else."""
    cr.set_session_manager(SessionManager(str(tmp_path / "s")))
    cr._global_client = None  # remote.py never sets this

    with pytest.raises(RuntimeError, match="not available"):
        cr.get_client()


def test_get_client_no_longer_needs_ctx(tmp_path, restore_resolver):
    """ctx was only a truthiness gate; dropping it is what makes the proxy work."""
    sentinel = object()
    cr.set_session_manager(None)
    cr.set_global_client(sentinel)
    assert cr.get_client() is sentinel
    assert cr.get_client(None) is sentinel


# ─── Rule 2: no tool may take a server path in remote mode ────────────────

_PATH_PARAM = re.compile(r"(^|_)(path|dir|directory|filename|output_dir)$")

# Tools that legitimately accept a path AND refuse it in remote mode. Adding a
# name here is a deliberate decision that the tool is remote-safe -- it is not
# a way to silence the test.
_GUARDED = {
    ("upload_course", "gpx_path"),
    ("download_activity_file", "output_dir"),
    ("set_fit_download_dir", "path"),
}


def _all_tools_with_path_params():
    found = set()
    for module in remote._CONFIGURED_MODULES:
        app = module.register_tools(FastMCP("contract-scan"))
        for tool in app._tool_manager.list_tools():
            props = (tool.parameters or {}).get("properties", {})
            for param in props:
                if _PATH_PARAM.search(param):
                    found.add((tool.name, param))
    return found


def test_no_unguarded_filesystem_path_parameters():
    """A path parameter on a remote tool is an arbitrary-file-read/write primitive.

    If this fails after an upstream merge, the new tool needs an
    `is_remote_mode()` refusal (see upload_course) before it can be added to
    _GUARDED.
    """
    found = _all_tools_with_path_params()
    unguarded = found - _GUARDED
    assert not unguarded, (
        "tool(s) expose a filesystem path with no remote-mode guard: "
        f"{sorted(unguarded)}. A path names the SERVER's disk in remote mode. "
        "Add an is_remote_mode() refusal, then list it in _GUARDED."
    )
    assert not _GUARDED - found, (
        f"_GUARDED lists tools that no longer exist: {sorted(_GUARDED - found)}"
    )


# ─── The activity_analysis guards actually refuse ─────────────────────────

async def _call(module, name, args):
    app = module.register_tools(FastMCP("guard-test"))
    result = await app.call_tool(name, args)
    return result[0][0].text


@pytest.mark.asyncio
async def test_download_activity_file_refuses_output_dir_in_remote_mode(
    tmp_path, restore_resolver
):
    from garmin_mcp import activity_analysis

    cr.set_session_manager(SessionManager(str(tmp_path / "s")))
    text = await _call(
        activity_analysis,
        "download_activity_file",
        {"activity_id": 1, "output_dir": str(tmp_path / "anywhere")},
    )
    assert "disabled in remote mode" in text
    assert not (tmp_path / "anywhere").exists(), "must not create server directories"


@pytest.mark.asyncio
async def test_download_activity_file_returns_bytes_instead_in_remote_mode(
    monkeypatch, tmp_path, restore_resolver
):
    """Refusing the path must not remove the capability -- return the file."""
    import base64
    from unittest.mock import Mock

    from garmin_mcp import activity_analysis

    cr.set_session_manager(SessionManager(str(tmp_path / "s")))
    client = Mock()
    client.download_activity.return_value = b"<gpx/>"
    monkeypatch.setattr(activity_analysis, "get_client", lambda ctx=None: client)

    text = await _call(
        activity_analysis, "download_activity_file", {"activity_id": 7, "format": "gpx"}
    )
    assert base64.b64encode(b"<gpx/>").decode() in text
    assert "does not write to the server" in text


@pytest.mark.asyncio
async def test_set_fit_download_dir_is_refused_in_remote_mode(tmp_path, restore_resolver):
    """It writes a process-global setting: one caller would move everyone's files."""
    from garmin_mcp import activity_analysis

    cr.set_session_manager(SessionManager(str(tmp_path / "s")))
    text = await _call(
        activity_analysis, "set_fit_download_dir", {"path": str(tmp_path / "nope")}
    )
    assert "disabled in remote mode" in text
    assert not (tmp_path / "nope").exists()


@pytest.mark.asyncio
async def test_stdio_mode_still_writes_to_disk(monkeypatch, tmp_path, restore_resolver):
    """Locally the path is the user's own disk, so it stays supported."""
    from unittest.mock import Mock

    from garmin_mcp import activity_analysis

    cr.set_session_manager(None)
    client = Mock()
    client.download_activity.return_value = b"<gpx/>"
    monkeypatch.setattr(activity_analysis, "get_client", lambda ctx=None: client)

    out = tmp_path / "dl"
    text = await _call(
        activity_analysis,
        "download_activity_file",
        {"activity_id": 7, "format": "gpx", "output_dir": str(out)},
    )
    assert "disabled in remote mode" not in text
    assert (out / "7.gpx").read_bytes() == b"<gpx/>"


# ─── The point of all this: upstream's own idiom works unchanged ──────────

@pytest.mark.asyncio
async def test_an_upstream_style_tool_using_the_bare_global_works_in_remote_mode(
    monkeypatch, tmp_path, restore_resolver
):
    """This is the whole reason the proxy exists.

    Upstream is stdio-only, so every tool it writes calls the module-level
    `garmin_client` global directly. Before the proxy that global was None in
    remote mode and each merge meant hand-migrating every new tool to
    `get_client(ctx)`; a missed one crashed at runtime for real users.

    The module below is written exactly the way upstream writes them -- no ctx
    parameter, bare global -- and must work.
    """
    from unittest.mock import Mock

    garmin_client = None

    def configure(client):
        nonlocal garmin_client
        garmin_client = client

    def register_tools(app):
        @app.tool()
        async def upstream_style_tool() -> str:
            return str(garmin_client.get_activities(0, 1))

        return app

    cr.set_session_manager(SessionManager(str(tmp_path / "s")))

    alice, bob = Mock(), Mock()
    alice.get_activities.return_value = ["alice-run"]
    bob.get_activities.return_value = ["bob-ride"]
    current = {"client": alice}
    monkeypatch.setattr(cr, "get_client", lambda ctx=None: current["client"])

    # Exactly what remote.py now does for every module.
    configure(_ResolvingGarminProxy())
    app = register_tools(FastMCP("upstream-sim"))

    assert "alice-run" in (await app.call_tool("upstream_style_tool", {}))[0][0].text
    current["client"] = bob
    assert "bob-ride" in (await app.call_tool("upstream_style_tool", {}))[0][0].text
