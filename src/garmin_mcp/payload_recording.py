"""Keeping a copy of each reply Garmin sends, for one request only.

FORK: inspect_garmin (diagnostics.py) shows the replies a tool received, to
check what the tool's output leaves out. Recording is switched on with
``recording()`` and lives in a contextvar, so it covers exactly the calls made
for that one request: a concurrent request -- another user's, or the same
user's in another chat -- runs in its own context and is never recorded into
it. Worker threads see it because the tools' off-loop runner and asyncio both
copy the context.

The client is wrapped where tools get it (client_resolver.get_client) and
where the stdio module globals call it (garmin_mcp._GarminProxy), never both
for one call: a wrapper that records marks itself, and the other skips it.
"""

import contextlib
import contextvars
import copy
from typing import Any, Iterator, List, Optional, Tuple

_calls: contextvars.ContextVar[Optional[List[Tuple[str, Any]]]] = contextvars.ContextVar(
    "garmin_recorded_calls", default=None
)


@contextlib.contextmanager
def recording() -> Iterator[List[Tuple[str, Any]]]:
    """Record every Garmin reply in this context; yields the list it fills."""
    calls: List[Tuple[str, Any]] = []
    token = _calls.set(calls)
    try:
        yield calls
    finally:
        _calls.reset(token)


def active_calls() -> Optional[List[Tuple[str, Any]]]:
    """The list this context records into, or None when not recording."""
    return _calls.get()


def record(calls: List[Tuple[str, Any]], name: str, reply: Any) -> None:
    """Keep a copy of a reply: tools label payloads in place afterwards."""
    try:
        calls.append((name, copy.deepcopy(reply)))
    except Exception:  # an uncopyable reply (a stream) is noted, not kept
        calls.append((name, f"<{type(reply).__name__} not copied>"))


class RecordingClient:
    """The Garmin client, keeping a copy of every reply a tool receives."""

    # Tells the other recording points this client already records.
    _records_calls = True

    def __init__(self, client: Any, calls: Optional[list] = None, prefix: str = ""):
        self._client = client
        self.calls = calls if calls is not None else []
        self._prefix = prefix

    def __getattr__(self, name: str) -> Any:
        attribute = getattr(self._client, name)
        if name == "client" and not self._prefix:
            # garminconnect's HTTP client: courses call its connectapi directly.
            return RecordingClient(attribute, self.calls, "client.")
        if not callable(attribute):
            return attribute

        def call(*args: Any, **kwargs: Any) -> Any:
            reply = attribute(*args, **kwargs)
            record(self.calls, self._prefix + name, reply)
            return reply

        return call


def unwrap(client: Any) -> Any:
    """The client a RecordingClient wraps, for caches keyed by client."""
    while isinstance(client, RecordingClient):
        client = client._client
    return client


def records_itself(client: Any) -> bool:
    """Whether a wrapper already records its calls. Asked of the class: an
    instance (a test's Mock, say) can answer any attribute."""
    return getattr(type(client), "_records_calls", False) is True


def wrap_if_recording(client: Any) -> Any:
    """The client, recording when this context records and nothing else does."""
    calls = active_calls()
    if calls is None or records_itself(client):
        return client
    return RecordingClient(client, calls)
