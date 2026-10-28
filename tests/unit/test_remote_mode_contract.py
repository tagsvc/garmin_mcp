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


def test_stdio_and_remote_register_the_same_modules():
    """A new upstream module lands in __init__.py only -- remote silently lacks it.

    Upstream is stdio-only, so it wires new modules into __init__.py alone.
    Comparing remote.py against itself cannot catch that (both of its lists are
    consistently missing the module); this compares the two servers. Found by
    the 2026-09-02 sync, where calendar_events reached stdio only.
    """
    from garmin_mcp import __init__ as _pkg  # noqa: F401
    import garmin_mcp

    stdio = set(re.findall(r"app = (\w+)\.register_tools\(app\)", inspect.getsource(garmin_mcp)))
    remote_mods = _registered_modules_in_source()

    # auth_tools is deliberately stdio-only (FORK.md invariant): remote users
    # authenticate through the OAuth login page instead.
    assert stdio - remote_mods == {"auth_tools"}, (
        "module(s) registered for stdio but missing from remote.py: "
        f"{sorted(stdio - remote_mods - {'auth_tools'})}"
    )
    assert remote_mods - stdio == set(), (
        f"module(s) in remote.py but not stdio: {sorted(remote_mods - stdio)}"
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
    ("download_course_gpx", "output_path"),
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


@pytest.mark.asyncio
async def test_download_course_gpx_refuses_output_path_in_remote_mode(
    monkeypatch, tmp_path, restore_resolver
):
    """Caught by the path-guard tripwire when it arrived from upstream."""
    from unittest.mock import Mock

    from garmin_mcp import courses

    cr.set_session_manager(SessionManager(str(tmp_path / "s")))
    client = Mock()
    client.client.connectapi.return_value = {
        "courseName": "Loop", "geoPoints": [{"latitude": 1.0, "longitude": 2.0}],
    }
    courses.configure(client)

    refused = await _call(
        courses,
        "download_course_gpx",
        {"course_id": 1, "output_path": str(tmp_path / "nope.gpx")},
    )
    assert "disabled in remote mode" in refused
    assert not (tmp_path / "nope.gpx").exists()

    inline = await _call(courses, "download_course_gpx", {"course_id": 1})
    assert "<gpx" in inline and "trkpt" in inline


@pytest.mark.asyncio
async def test_course_gpx_escapes_xml_special_characters(
    tmp_path, restore_resolver
):
    """A course named 'Ben & Jerry <loop>' must not emit corrupt XML."""
    import json as _json
    import xml.etree.ElementTree as ET
    from unittest.mock import Mock

    from garmin_mcp import courses

    cr.set_session_manager(SessionManager(str(tmp_path / "s")))
    client = Mock()
    client.client.connectapi.return_value = {
        "courseName": "Ben & Jerry <loop>",
        "geoPoints": [{"latitude": 1.0, "longitude": 2.0, "elevation": 3.0}],
        "coursePoints": [{"lat": 1.0, "lon": 2.0, "name": "A & B", "pointType": "GENERIC"}],
    }
    courses.configure(client)

    gpx = _json.loads(await _call(courses, "download_course_gpx", {"course_id": 1}))["gpx"]
    ET.fromstring(gpx)  # raises on malformed XML


# --- The remote tool set ---------------------------------------------------------


def _remote_tools():
    return remote.register_remote_tools(FastMCP("remote-set"))._tool_manager.list_tools()


def _stdio_tool_names():
    import pathlib
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))
    from generate_dxt_tools import MODULES

    app = FastMCP("stdio-set")
    for module in MODULES:
        app = module.register_tools(app)
    return {t.name for t in app._tool_manager.list_tools()}


def test_remote_withholds_exactly_the_stdio_only_tools():
    """Remote = stdio minus auth_tools (module-level) minus REMOTE_EXCLUDED_TOOLS.

    Pins both directions: a new tool cannot go missing from remote by accident,
    and a withheld one cannot reach it.
    """
    withheld = _stdio_tool_names() - {t.name for t in _remote_tools()}
    # A literal, not REMOTE_EXCLUDED_TOOLS: comparing against the constant would
    # pass whatever the constant held. Withholding a tool from the network server
    # is a decision, so it should take an edit here as well as in remote.py.
    assert withheld == {"check_garmin_auth", "login_to_garmin", "delete_activity"}


def test_delete_activity_is_stdio_only():
    assert "delete_activity" in _stdio_tool_names()
    assert "delete_activity" not in {t.name for t in _remote_tools()}


def test_remote_tools_get_the_stdio_filter_treatment():
    """Remote registers through _ToolFilter, like stdio.

    Registered on the bare FastMCP app, a `-> str` tool gets an auto output
    schema, so every result is re-wrapped as {"result": "<escaped json>"} and
    the payload doubles; and it carries no effect annotations, so a client
    cannot tell a delete from a read.
    """
    tools = _remote_tools()
    assert tools
    assert [t.name for t in tools if t.output_schema is not None] == []
    assert [t.name for t in tools if t.annotations is None] == []
    destructive = {t.name for t in tools if t.annotations.destructiveHint}
    assert {"delete_workout", "delete_course"} <= destructive


def test_an_unmatched_remote_exclusion_fails_closed(monkeypatch):
    """A renamed tool must not slip past the exclusion and onto the network."""
    monkeypatch.setattr(remote, "REMOTE_EXCLUDED_TOOLS", frozenset({"no_such_tool"}))
    with pytest.raises(RuntimeError, match="no_such_tool"):
        remote.register_remote_tools(FastMCP("rename"))


# --- Per-caller identity survives the off-event-loop tool runner ----------------


@pytest.mark.asyncio
async def test_tool_bodies_on_worker_threads_still_see_their_own_callers_token():
    """Remote resolves the calling user from the MCP auth contextvar.

    Upstream #449 made _ToolFilter run every tool body as
    anyio.to_thread.run_sync(lambda: asyncio.run(...)), and remote registers
    through _ToolFilter. So the caller's context must cross two hops -- onto
    an anyio worker thread, then into a fresh event loop there -- for
    get_client() to find the right user. Verified with anyio 4.14.2; a plain
    threading.Thread loses it. This fails if a dependency change ever stops
    carrying context across, which would make every remote call fail closed.
    """
    import asyncio
    import threading

    from mcp.server.auth.middleware.auth_context import auth_context_var, get_access_token
    from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
    from mcp.server.auth.provider import AccessToken

    import garmin_mcp

    app = FastMCP("identity")
    tool_app = garmin_mcp._ToolFilter(app, set(), set())
    loop_thread = threading.current_thread().name

    @tool_app.tool()
    async def whoami() -> str:
        token = get_access_token()
        return f"{threading.current_thread().name}|{token.token if token else None}"

    def _text(result):
        content = result[0] if isinstance(result, tuple) else result
        return content[0].text

    async def one_request(token):
        auth_context_var.set(
            AuthenticatedUser(AccessToken(token=token, client_id="c", scopes=[]))
        )
        return token, _text(await app.call_tool("whoami", {}))

    tokens = ["user-a", "user-b", "user-a-again", "user-b-again"]
    results = await asyncio.gather(*(asyncio.create_task(one_request(t)) for t in tokens))
    for sent, text in results:
        thread, seen = text.split("|", 1)
        assert seen == sent, f"{sent!r}'s tool call saw {seen!r}"
        assert thread != loop_thread, "tool body is expected off the event-loop thread"
