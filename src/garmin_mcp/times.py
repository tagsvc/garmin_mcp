"""Explicit time zones for Garmin's timestamps.

FORK: Garmin mixes GMT, the watch's local time and epoch milliseconds, often
under keys that do not say which; a 3:17 AM GMT bedtime read as local is
four hours off, and an evening run lands on the next day. The rule, agreed
2026-10-28:

- GMT stays, under a key ending ``_gmt``, as ISO 8601 ending in ``Z``.
- Local time goes under a key ending ``_local``, as ISO 8601 with its UTC
  offset (``2026-10-27T23:17:13-04:00``). It comes from the record's own
  offset whenever Garmin gives one -- a ``timezoneOffset``, or a ``*Local``
  value beside the ``*GMT`` one -- because that is where the watch was: a
  run in London reads London time, as in the Garmin app.
- Only when a record carries no offset does it come from the profile's time
  zone (Garmin Connect's ``timeZone`` setting), and the tool says so with a
  ``local_time_zone`` field.

Garmin's ``*Local`` epoch values are not real epochs: they encode the local
wall clock as if it were UTC (1793143033000 is "2026-10-27T23:17:13", not
that instant in New York). ``wall_clock`` reads them that way.
"""

import datetime
import re
import weakref
from typing import Any, Optional, Tuple
from zoneinfo import ZoneInfo

UTC = datetime.timezone.utc

# Garmin epochs are milliseconds; anything this large cannot be seconds.
_EPOCH_MS_FLOOR = 10**11
_ISO_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}(?::\d{2})?)(?:\.\d+)?"
    r"\s*(Z|GMT|UTC|[+-]\d{2}:?\d{2})?$",
    re.IGNORECASE,
)

# Profile time zone per Garmin client. A client lives as long as its
# session (remote: SessionManager.CACHE_TTL, one hour), so a change in
# Garmin Connect is picked up when the session is next restored.
_profile_zones: "weakref.WeakKeyDictionary[Any, Optional[str]]" = weakref.WeakKeyDictionary()


def _parse(value: Any) -> Tuple[Optional[datetime.datetime], bool]:
    """(naive wall-clock datetime, whether the value named UTC or an offset)."""
    if isinstance(value, datetime.datetime):
        if value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None), True
        return value, False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < _EPOCH_MS_FLOOR:
            return None, False
        moment = datetime.datetime.fromtimestamp(value / 1000, tz=UTC)
        return moment.replace(tzinfo=None), True
    if not isinstance(value, str):
        return None, False
    match = _ISO_PATTERN.match(value.strip())
    if not match:
        return None, False
    day, clock, zone = match.groups()
    if clock.count(":") == 1:
        clock += ":00"
    parsed = datetime.datetime.fromisoformat(f"{day}T{clock}")
    if zone and zone.upper() not in ("Z", "GMT", "UTC"):
        offset = zone.replace(":", "")
        delta = datetime.timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5]))
        parsed = parsed - delta if offset[0] == "+" else parsed + delta
    return parsed, zone is not None


def gmt(value: Any) -> Optional[datetime.datetime]:
    """An aware UTC datetime from a Garmin GMT value (ISO text or epoch ms).

    Text without a zone is taken as GMT: call this only on values Garmin
    documents as GMT (a ``*GMT``/``*Gmt`` key, or one verified to be).
    """
    parsed, _ = _parse(value)
    return parsed.replace(tzinfo=UTC) if parsed else None


def wall_clock(value: Any) -> Optional[datetime.datetime]:
    """A naive local wall-clock datetime from a Garmin ``*Local`` value.

    ISO text is read as written; an epoch is one of Garmin's local
    pseudo-epochs, whose UTC rendering is the local wall clock.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < _EPOCH_MS_FLOOR:
            return None
        return datetime.datetime.fromtimestamp(value / 1000, tz=UTC).replace(tzinfo=None)
    parsed, _ = _parse(value)
    return parsed


def gmt_iso(value: Any) -> Optional[str]:
    """Garmin's GMT value as ``YYYY-MM-DDTHH:MM:SSZ``, or None."""
    moment = gmt(value)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ") if moment else None


def _iso_with_offset(moment: datetime.datetime) -> str:
    return moment.isoformat(timespec="seconds")


def local_from_offset(gmt_value: Any, offset: Optional[datetime.timedelta]) -> Optional[str]:
    """Local ISO time for a GMT value shifted by the record's own offset."""
    moment = gmt(gmt_value)
    if moment is None or offset is None:
        return None
    return _iso_with_offset(moment.astimezone(datetime.timezone(offset)))


def offset_from_ms(offset_ms: Any) -> Optional[datetime.timedelta]:
    """A ``timezoneOffset`` in milliseconds (-14400000) as a timedelta."""
    if isinstance(offset_ms, (int, float)) and not isinstance(offset_ms, bool):
        return datetime.timedelta(milliseconds=offset_ms)
    return None


def offset_from_pair(gmt_value: Any, local_value: Any) -> Optional[datetime.timedelta]:
    """The offset implied by a GMT value and the local one beside it.

    Rounded to the quarter hour, the finest real-world offset step, so a
    second's difference between the two stamps does not leak in.
    """
    moment = gmt(gmt_value)
    local = wall_clock(local_value)
    if moment is None or local is None:
        return None
    minutes = (local - moment.replace(tzinfo=None)).total_seconds() / 60
    minutes = round(minutes / 15) * 15
    if abs(minutes) > 14 * 60:
        return None
    return datetime.timedelta(minutes=minutes)


def local_from_zone(gmt_value: Any, zone: Optional[str]) -> Optional[str]:
    """Local ISO time for a GMT value in an IANA zone (daylight saving applied)."""
    moment = gmt(gmt_value)
    if moment is None or not zone:
        return None
    try:
        return _iso_with_offset(moment.astimezone(ZoneInfo(zone)))
    except Exception:
        return None


def put_times(
    target: dict,
    stem: str,
    gmt_value: Any = None,
    local_value: Any = None,
    *,
    offset: Optional[datetime.timedelta] = None,
    zone: Optional[str] = None,
) -> bool:
    """Set ``{stem}_gmt`` and ``{stem}_local`` on target, per the module rule.

    Local time comes from, in order: ``offset`` (the record's own), the
    offset implied by ``local_value`` beside ``gmt_value``, ``local_value``
    alone (written as its wall clock, with no offset to add), and only then
    ``zone`` (the profile's). Returns True when the profile zone was used,
    so the caller can say so.
    """
    gmt_text = gmt_iso(gmt_value)
    if gmt_text:
        target[f"{stem}_gmt"] = gmt_text
    if offset is None and gmt_text and local_value is not None:
        offset = offset_from_pair(gmt_value, local_value)
    if offset is not None and gmt_text:
        target[f"{stem}_local"] = local_from_offset(gmt_value, offset)
        return False
    clock = wall_clock(local_value) if local_value is not None else None
    if clock is not None:
        target[f"{stem}_local"] = clock.isoformat(timespec="seconds")
        return False
    if zone and gmt_text:
        local = local_from_zone(gmt_value, zone)
        if local:
            target[f"{stem}_local"] = local
            return True
    return False


def profile_time_zone(client: Any) -> Optional[str]:
    """The Garmin Connect profile's time zone (``America/New_York``), or None.

    Read from the user settings once per client and cached with it. The
    module-level ``garmin_client`` in remote mode is one proxy shared by every
    user, so it is never a cache key: it is resolved to the caller's own
    client first.
    """
    from garmin_mcp.client_resolver import _ResolvingGarminProxy, get_client

    if isinstance(client, _ResolvingGarminProxy):
        client = get_client()
    try:
        if client in _profile_zones:
            return _profile_zones[client]
    except TypeError:
        return _read_profile_time_zone(client)
    zone = _read_profile_time_zone(client)
    try:
        _profile_zones[client] = zone
    except TypeError:
        pass
    return zone


def _read_profile_time_zone(client: Any) -> Optional[str]:
    try:
        settings = client.get_userprofile_settings()
    except Exception:
        return None
    zone = settings.get("timeZone") if isinstance(settings, dict) else None
    if not isinstance(zone, str) or not zone:
        return None
    try:
        ZoneInfo(zone)
    except Exception:
        return None
    return zone


def local_today(client: Any) -> datetime.date:
    """Today in the profile's time zone; the server's date without one.

    The server's own date is UTC on Railway: from 8 PM in New York it is
    already tomorrow there.
    """
    zone = profile_time_zone(client)
    if zone:
        return datetime.datetime.now(ZoneInfo(zone)).date()
    return datetime.date.today()
