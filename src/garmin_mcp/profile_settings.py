"""The Garmin Connect profile's settings, read once per Garmin client.

FORK: tools follow settings the athlete chose in Garmin Connect -- the time
zone (times.py) and the hydration unit (units.py), alongside the measurement
system garminconnect already caches at login. They come from one settings
call per client: a client lives as long as its session (remote:
SessionManager.CACHE_TTL, one hour), so a change in Garmin Connect is picked
up when the session is next restored.
"""

import weakref
from typing import Any, Dict, Optional

_settings: "weakref.WeakKeyDictionary[Any, Dict[str, Any]]" = weakref.WeakKeyDictionary()


def profile_settings(client: Any) -> Dict[str, Any]:
    """The profile's user settings (``timeZone``, ``hydrationMeasurementUnit``...).

    Empty when they cannot be read. The module-level ``garmin_client`` in
    remote mode is one proxy shared by every user, so it is never a cache
    key: it is resolved to the caller's own client first.
    """
    from garmin_mcp.client_resolver import _ResolvingGarminProxy, get_client
    from garmin_mcp.payload_recording import unwrap

    if isinstance(client, _ResolvingGarminProxy):
        client = get_client()
    # While inspect_garmin records, the client is wrapped per call; the cache
    # is keyed by the client underneath.
    client = unwrap(client)
    try:
        if client in _settings:
            return _settings[client]
    except TypeError:
        return _read(client) or {}
    settings = _read(client)
    if settings is None:
        # A failed read (a 429, say) is not cached: the next call tries again,
        # rather than the client going without its zone for its whole life.
        return {}
    try:
        _settings[client] = settings
    except TypeError:
        pass
    return settings


def _read(client: Any) -> Optional[Dict[str, Any]]:
    """The settings, {} when Garmin sends something else, None when the read fails."""
    try:
        settings = client.get_userprofile_settings()
    except Exception:
        return None
    return settings if isinstance(settings, dict) else {}


def forget(client: Any) -> None:
    """Drop the settings cached for a client, as when it signs in to another account."""
    try:
        _settings.pop(client, None)
    except TypeError:
        pass
