"""Driver for test_remote_end_to_end.py -- run in a subprocess, not collected.

Boots the real remote main() and drives authenticated MCP requests through the
real stack: OAuth bearer middleware, Streamable HTTP transport, _ToolFilter's
worker threads, and per-request client resolution. Stubbed: only the access
token lookup and the per-user Garmin client. Prints one JSON line of results.

A subprocess because main() changes process-wide state (the resolver's session
manager, the SDK patches in oauth_claude) that must not leak into other tests.
"""
import asyncio
import json

import httpx
import uvicorn
from mcp.server.auth.provider import AccessToken

from garmin_mcp.oauth_provider import GarminOAuthProvider
from garmin_mcp.session_manager import SessionManager

USERS = {"tok-alice": "alice", "tok-bob": "bob"}


class FakeGarmin:
    def __init__(self, name, ftp):
        self.name, self.ftp = name, ftp

    def get_full_name(self):  # used via get_client(ctx)
        return self.name

    def get_cycling_ftp(self):  # used via the module-level garmin_client proxy
        return {"functionalThresholdPower": self.ftp, "calendarDate": "2026-10-27"}


CLIENTS = {"alice": FakeGarmin("Alice", 111), "bob": FakeGarmin("Bob", 222)}


async def _load_access_token(self, token):
    return AccessToken(token=token, client_id="c", scopes=["garmin"]) if token in USERS else None


GarminOAuthProvider.load_access_token = _load_access_token
SessionManager.get_user_id_for_token = lambda self, token: USERS.get(token)
SessionManager.get_client = lambda self, user_id: CLIENTS.get(user_id)

captured = {}
uvicorn.run = lambda app, **kw: captured.update(app=app)
from garmin_mcp import remote  # noqa: E402

remote.main()
asgi = captured["app"]
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _body(resp):
    if resp.headers.get("content-type", "").startswith("text/event-stream"):
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:])
    return resp.json()


async def _session(client, token):
    headers = {**HEADERS, "Authorization": f"Bearer {token}"}
    init = await client.post("/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "e2e", "version": "1"}},
    })
    session_id = init.headers.get("mcp-session-id")
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    await client.post("/mcp", headers=headers,
                      json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    texts = {}
    for i, tool in enumerate(["get_full_name", "get_cycling_ftp"], start=2):
        resp = await client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0", "id": i, "method": "tools/call",
            "params": {"name": tool, "arguments": {}},
        })
        result = _body(resp)["result"]
        texts[tool] = result["content"][0]["text"]
        texts[tool + "_wrapped"] = "structuredContent" in result
    return {"token": token, "init_status": init.status_code, "session_id": session_id, **texts}


async def main():
    unauth_app = asgi.app if hasattr(asgi, "app") else asgi
    async with unauth_app.router.lifespan_context(unauth_app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=asgi),
                                     base_url="https://e2e.example.invalid") as client:
            no_token = await client.post("/mcp", headers=HEADERS, json={})
            as_meta = await client.get("/.well-known/oauth-authorization-server")
            results = await asyncio.gather(*(
                _session(client, t) for t in ["tok-alice", "tok-bob", "tok-alice", "tok-bob"]
            ))
    print(json.dumps({
        "no_token_status": no_token.status_code,
        "no_token_challenge": no_token.headers.get("www-authenticate"),
        "token_auth_methods": as_meta.json().get("token_endpoint_auth_methods_supported"),
        "sessions": results,
    }))


asyncio.run(main())
