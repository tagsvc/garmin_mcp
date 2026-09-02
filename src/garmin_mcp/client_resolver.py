"""
Client resolver for Garmin MCP server.

Resolves the Garmin client based on context:
- stdio mode: returns the global client (single-user)
- remote mode: extracts user_id from OAuth access token, resolves via SessionManager
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from garminconnect import Garmin

if TYPE_CHECKING:
    from mcp.server.fastmcp import Context

# Global client for stdio mode
_global_client: Optional[Garmin] = None

# Session manager for remote mode (set by remote.py)
_session_manager = None


def set_global_client(client: Garmin) -> None:
    """Set the global Garmin client for stdio mode."""
    global _global_client
    _global_client = client


def set_session_manager(manager) -> None:
    """Set the session manager for remote mode."""
    global _session_manager
    _session_manager = manager


def is_remote_mode() -> bool:
    """True when the server is running in remote (multi-user, HTTP) mode.

    Remote mode is defined by a SessionManager having been installed by
    ``remote.py``. Tools use this to refuse capabilities that are safe on a
    local single-user stdio server but not on a network-exposed one (e.g.
    reading arbitrary paths off the server's filesystem).
    """
    return _session_manager is not None


def get_user_id(ctx: Optional[Context] = None) -> Optional[str]:
    """Resolve the calling user's id in remote mode, else None.

    Lets per-user state (saved reports, uploads) be scoped so one authenticated
    user cannot read or overwrite another's.
    """
    if ctx is None or _session_manager is None:
        return None
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token

        access_token = get_access_token()
        if access_token is None:
            return None
        return _session_manager.get_user_id_for_token(access_token.token)
    except ImportError:
        return None


def get_client(ctx: Optional[Context] = None) -> Garmin:
    """Resolve the Garmin client based on context.

    In stdio mode (no ctx or no auth token): returns the global client.
    In remote mode: extracts user_id from the OAuth access token and
    returns the per-user Garmin client from SessionManager.
    """
    # Try remote mode: extract user_id from OAuth token.
    #
    # NOTE: ctx is deliberately NOT consulted here. The caller's identity comes
    # from get_access_token(), a contextvar set per request -- ctx was only ever
    # a truthiness gate, which made every tool carry a `ctx` parameter it did
    # not actually use. Dropping the gate lets a tool resolve the right per-user
    # client without threading ctx through, which is what makes the module-level
    # _ResolvingGarminProxy below safe.
    if _session_manager is not None:
        try:
            from mcp.server.auth.middleware.auth_context import get_access_token

            access_token = get_access_token()
            if access_token is not None:
                user_id = _session_manager.get_user_id_for_token(access_token.token)
                if user_id:
                    client = _session_manager.get_client(user_id)
                    if client is not None:
                        return client
                    raise RuntimeError(
                        "Garmin session expired or not available. Please re-authenticate."
                    )
        except ImportError:
            pass

    # Fallback to global client (stdio mode)
    if _global_client is not None:
        return _global_client

    raise RuntimeError("Garmin client not available. Please authenticate first.")


class _ResolvingGarminProxy:
    """A module-global stand-in that resolves the *calling user's* client per call.

    Modules hold a `garmin_client` global set by `configure()`. In stdio mode
    that is one real client. In remote mode there is no single client -- there
    is one per authenticated user -- so historically remote.py left the global
    as None and every tool had to be rewritten to call `get_client(ctx)`. That
    made each upstream merge a manual migration, and a missed call site crashed
    only at runtime.

    This proxy removes that split. It holds no client of its own: each attribute
    access resolves the caller's client through `get_client()`, which reads the
    per-request contextvar. Two concurrent users therefore never share a client,
    and a tool written against the plain `garmin_client` global works unchanged
    in both modes.

    With no access token in context (startup, background work), `get_client()`
    falls through to the stdio global -- which remote.py never sets -- and
    raises. The failure mode stays fail-closed, never "someone else's client".
    """

    def __getattr__(self, name):
        from garmin_mcp import _GarminProxy

        return getattr(_GarminProxy(get_client()), name)
