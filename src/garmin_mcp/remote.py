"""
Remote MCP server entry point with OAuth2 authentication.

Runs the Garmin MCP server in remote mode with:
- Streamable HTTP transport (network accessible)
- OAuth2 authentication (integrated authorization server)
- Multi-user support (each user links their own Garmin account)
"""

from __future__ import annotations

import logging
import os
import sys

from pydantic import AnyHttpUrl
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from starlette.requests import Request
from starlette.responses import Response

# Defense-in-depth response headers, applied to every response by an ASGI
# middleware. Most relevant to the browser-rendered login/MFA pages (which
# collect Garmin credentials): the CSP blocks script execution (belt-and-braces
# over the output escaping), X-Frame-Options/frame-ancestors stop clickjacking,
# and Referrer-Policy keeps the `state` token out of the Referer header.
_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (
        b"content-security-policy",
        b"default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
        b"form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    ),
    (b"strict-transport-security", b"max-age=31536000"),
]


class _SecurityHeadersMiddleware:
    """Pure-ASGI middleware that adds security headers to HTTP responses.

    Pure ASGI (not Starlette BaseHTTPMiddleware) so it is robust across Starlette
    versions and transparently passes through lifespan and streaming messages.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {k.lower() for k, _ in headers}
                for key, value in _SECURITY_HEADERS:
                    if key not in present:
                        headers.append((key, value))
            await send(message)

        await self.app(scope, receive, send_wrapper)


from garmin_mcp.config import get_config
from garmin_mcp import oauth_claude
from garmin_mcp.oauth_provider import GarminOAuthProvider
from garmin_mcp.session_manager import SessionManager
from garmin_mcp.client_resolver import set_session_manager

# Import all tool modules
from garmin_mcp import activity_management
from garmin_mcp import health_wellness
from garmin_mcp import user_profile
from garmin_mcp import devices
from garmin_mcp import gear_management
from garmin_mcp import weight_management
from garmin_mcp import challenges
from garmin_mcp import training
from garmin_mcp import workouts
from garmin_mcp import workout_templates
from garmin_mcp import data_management
from garmin_mcp import womens_health
from garmin_mcp import nutrition
from garmin_mcp import courses
from garmin_mcp import workout_builders
from garmin_mcp import activity_analysis
from garmin_mcp import analytics


def main():
    """Start the remote MCP server with OAuth2 authentication."""
    # Configure logging so our own logs (session restore failures, OAuth
    # discovery patches) and the underlying garth/garminconnect detail are
    # visible. Set LOG_LEVEL=DEBUG to surface garminconnect's token
    # load/refresh failure reasons when diagnosing session issues.
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Load configuration
    try:
        config = get_config()
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Starting Garmin MCP remote server...", file=sys.stderr)
    print(f"  Server URL: {config.server_url}", file=sys.stderr)
    print(f"  Listen: {config.host}:{config.port}", file=sys.stderr)
    print(f"  MCP path: {config.path}", file=sys.stderr)
    print(f"  DB: {config.db_path}", file=sys.stderr)
    print(f"  Sessions: {config.session_storage_path}", file=sys.stderr)

    # Initialize session manager
    session_manager = SessionManager(config.session_storage_path)
    set_session_manager(session_manager)

    # Email allowlist (fail-closed). An empty allowlist rejects every login.
    if config.allowed_emails:
        print(
            f"  Allowlist: {len(config.allowed_emails)} email(s) permitted",
            file=sys.stderr,
        )
    else:
        print(
            "  WARNING: GARMIN_ALLOWED_EMAILS is not set — ALL logins will be "
            "rejected (fail-closed). Set it to a comma-separated email list.",
            file=sys.stderr,
        )

    if config.import_secret:
        print("  Token import endpoint: enabled (/import-token)", file=sys.stderr)
    else:
        print(
            "  Token import endpoint: disabled (set GARMIN_IMPORT_SECRET to enable)",
            file=sys.stderr,
        )

    # Initialize OAuth provider
    oauth_provider = GarminOAuthProvider(
        db_path=config.db_path,
        server_url=config.server_url,
        session_manager=session_manager,
        allowed_emails=config.allowed_emails,
        import_secret=config.import_secret,
    )

    # Pre-register clients that skip dynamic registration (e.g. Claude.ai,
    # which uses the static client_id "https://claude.ai").
    oauth_provider.seed_clients(oauth_claude.build_static_clients(config.scope))

    # Make OAuth discovery work for RFC 9728 clients like Claude.ai. These
    # patch the MCP SDK before the Starlette app is built (in app.run()), so
    # they must run before FastMCP.streamable_http_app() is invoked:
    #   - advertise the "none" token-endpoint auth method (PKCE public clients)
    #   - emit `resource_metadata` in WWW-Authenticate on 401s, distinguishing
    #     "no token" (bare challenge) from "invalid token" (error=invalid_token)
    oauth_claude.patch_token_endpoint_auth_methods()
    oauth_claude.patch_require_auth_middleware(
        oauth_claude.resource_metadata_url(config.server_url)
    )

    # Create the MCP app with OAuth2 authentication
    app = FastMCP(
        name="Garmin Connect v1.0",
        auth_server_provider=oauth_provider,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(config.server_url),
            client_registration_options=ClientRegistrationOptions(
                enabled=True,
                valid_scopes=[config.scope],
                default_scopes=[config.scope],
            ),
            revocation_options=RevocationOptions(enabled=True),
            required_scopes=[config.scope],
            resource_server_url=None,  # Combined AS+RS mode
        ),
        host=config.host,
        port=config.port,
        streamable_http_path=config.path,
    )

    # RFC 9728 protected resource metadata. Claude.ai requires this endpoint
    # before it will begin OAuth discovery; in combined AS+RS mode the SDK does
    # not register it, so we serve it ourselves.
    @app.custom_route(
        oauth_claude.WELL_KNOWN_PROTECTED_RESOURCE, methods=["GET", "OPTIONS"]
    )
    async def protected_resource_metadata(request: Request) -> Response:
        return oauth_claude.protected_resource_response(
            request, config.server_url, [config.scope]
        )

    # Register custom routes for login pages
    @app.custom_route("/login", methods=["GET"])
    async def login_page(request: Request) -> Response:
        state = request.query_params.get("state", "")
        return await oauth_provider.get_login_page(state)

    @app.custom_route("/login/callback", methods=["POST"])
    async def login_callback(request: Request) -> Response:
        return await oauth_provider.handle_login_callback(request)

    @app.custom_route("/login/mfa", methods=["GET"])
    async def mfa_page(request: Request) -> Response:
        state = request.query_params.get("state", "")
        return await oauth_provider.get_mfa_page(state)

    @app.custom_route("/login/mfa/callback", methods=["POST"])
    async def mfa_callback(request: Request) -> Response:
        return await oauth_provider.handle_mfa_callback(request)

    @app.custom_route("/import-token", methods=["POST"])
    async def import_token(request: Request) -> Response:
        return await oauth_provider.handle_import_token(request)

    # Register tools from all modules
    app = activity_management.register_tools(app)
    app = health_wellness.register_tools(app)
    app = user_profile.register_tools(app)
    app = devices.register_tools(app)
    app = gear_management.register_tools(app)
    app = weight_management.register_tools(app)
    app = challenges.register_tools(app)
    app = training.register_tools(app)
    app = workouts.register_tools(app)
    app = data_management.register_tools(app)
    app = womens_health.register_tools(app)
    app = nutrition.register_tools(app)
    app = courses.register_tools(app)
    app = workout_builders.register_tools(app)
    app = activity_analysis.register_tools(app)
    app = analytics.register_tools(app)

    # Register resources (workout templates)
    app = workout_templates.register_resources(app)

    print("Server ready.", file=sys.stderr)

    # Build the streamable-HTTP app, wrap it with the security-headers middleware,
    # and serve it ourselves (FastMCP.run() offers no middleware hook). This
    # mirrors FastMCP.run_streamable_http_async() but adds the wrapper.
    import uvicorn

    starlette_app = app.streamable_http_app()
    wrapped = _SecurityHeadersMiddleware(starlette_app)
    uvicorn.run(
        wrapped,
        host=config.host,
        port=config.port,
        log_level=log_level.lower(),
    )


if __name__ == "__main__":
    main()
