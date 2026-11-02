"""A real garminconnect client with no network: the offline_garmin fixture.

Every socket connection and every curl_cffi request raises NetworkBlocked, and
requests.Session.request is answered by GarminAPI, a router serving Garmin
Connect API paths from memory. A test can then use the library's own Garmin
object, log in from a token file and call tools through it, and still nothing
leaves the machine. tests/unit/test_live_auth_is_excluded_from_ci.py checks
the blocking and exempts modules that use the fixture for every test.

Every value here is synthetic.
"""
import base64
import json
import socket
import time
import urllib.parse

import requests

# Garmin Connect's API, and the host its ECG data is served from.
API_HOSTS = ("connectapi.garmin.com", "ghs.garmin.com")
PROFILE = {"displayName": "test-runner", "fullName": "Test Runner"}
SETTINGS = {"userData": {"measurementSystem": "statute_us"}}


class NetworkBlocked(RuntimeError):
    """Raised for any attempt to reach the network."""


def jwt(seconds: int = 86400) -> str:
    """An unsigned token whose expiry the client reads, far enough off not to refresh."""
    def part(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    return f"{part({'alg': 'none'})}.{part({'exp': int(time.time()) + seconds})}.signature"


TOKENS = {
    "di_token": jwt(),
    "di_refresh_token": "synthetic-refresh-token",
    "di_client_id": "synthetic-client-id",
}


class GarminAPI:
    """Answers each request by its path, and records it.

    A route maps a path to a JSON payload, or to a bare HTTP status.
    """

    def __init__(self):
        self.routes = {
            "/userprofile-service/socialProfile": PROFILE,
            "/userprofile-service/userprofile/user-settings": SETTINGS,
        }
        self.calls = []

    def __call__(self, method, url, **kwargs):
        parts = urllib.parse.urlsplit(url)
        if parts.hostname not in API_HOSTS:
            raise NetworkBlocked(f"offline: {method} {url}")
        self.calls.append({"method": method, "url": url, "host": parts.hostname, "path": parts.path,
                           "params": kwargs.get("params"), "json": kwargs.get("json"),
                           "headers": kwargs.get("headers") or {}})
        response = requests.Response()
        response.url = url
        response.headers["Content-Type"] = "application/json"
        payload = self.routes.get(parts.path)
        if payload is None:
            response.status_code = 404
            response._content = b'{"message": "no such path"}'
        elif isinstance(payload, int):  # a bare status, such as 429
            response.status_code = payload
            response._content = b"{}"
        else:
            response.status_code = 200
            response._content = json.dumps(payload).encode()
        return response

    def to(self, path):
        return [call for call in self.calls if call["path"] == path]


def _blocked(*args, **kwargs):
    raise NetworkBlocked("offline: no network in this test")


def install(monkeypatch) -> GarminAPI:
    """Block the network for the rest of the test and return the router."""
    api = GarminAPI()
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:  # pragma: no cover - garminconnect depends on curl_cffi
        pass
    else:
        monkeypatch.setattr(cffi_requests.Session, "request", _blocked)
        monkeypatch.setattr(cffi_requests.AsyncSession, "request", _blocked)
    monkeypatch.setattr(requests.Session, "request",
                        lambda _session, method, url, **kwargs: api(method, url, **kwargs))
    for name in ("GARMINTOKENS", "GARMINTOKENS_BASE64", "GARMIN_EMAIL", "GARMIN_PASSWORD",
                 "GARMIN_MFA_CODE", "GARMIN_OTP"):
        monkeypatch.delenv(name, raising=False)
    return api
