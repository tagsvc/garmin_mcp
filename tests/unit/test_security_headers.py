"""Unit tests for the response security-headers ASGI middleware (remote.py)."""
import pytest

from garmin_mcp.remote import _SECURITY_HEADERS, _SecurityHeadersMiddleware


async def _run(mw, scope):
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}

    await mw(scope, receive, send)
    return sent


@pytest.mark.asyncio
async def test_security_headers_added_to_http_response():
    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/html; charset=utf-8")],
            }
        )
        await send({"type": "http.response.body", "body": b"<html></html>"})

    sent = await _run(_SecurityHeadersMiddleware(app), {"type": "http"})
    start = next(m for m in sent if m["type"] == "http.response.start")
    hdrs = {k.lower(): v for k, v in start["headers"]}

    for key, val in _SECURITY_HEADERS:
        assert hdrs.get(key) == val
    # original headers are preserved
    assert hdrs[b"content-type"] == b"text/html; charset=utf-8"


@pytest.mark.asyncio
async def test_csp_blocks_scripts_and_framing():
    csp = dict(_SECURITY_HEADERS)[b"content-security-policy"].decode()
    assert "default-src 'none'" in csp        # no scripts by default
    assert "frame-ancestors 'none'" in csp    # anti-clickjacking
    assert "form-action 'self'" in csp        # login form can still POST


@pytest.mark.asyncio
async def test_lifespan_passes_through_untouched():
    called = {"n": 0}

    async def app(scope, receive, send):
        called["n"] += 1

    await _run(_SecurityHeadersMiddleware(app), {"type": "lifespan"})
    assert called["n"] == 1


@pytest.mark.asyncio
async def test_existing_header_not_duplicated_or_overwritten():
    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"x-frame-options", b"SAMEORIGIN")],
            }
        )

    sent = await _run(_SecurityHeadersMiddleware(app), {"type": "http"})
    xfo = [v for k, v in sent[0]["headers"] if k.lower() == b"x-frame-options"]
    assert xfo == [b"SAMEORIGIN"]  # left as-is, not duplicated
