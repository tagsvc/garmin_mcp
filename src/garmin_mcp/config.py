"""
Configuration for Garmin MCP remote server via environment variables.
"""

import os


class RemoteConfig:
    """Configuration for the remote MCP server."""

    def __init__(self):
        self.host = os.getenv("GARMIN_MCP_HOST", "0.0.0.0")
        self.port = int(os.getenv("GARMIN_MCP_PORT", "8000"))
        self.path = os.getenv("GARMIN_MCP_PATH", "/mcp")
        self.server_url = os.getenv("GARMIN_MCP_SERVER_URL", "")
        self.scope = os.getenv("MCP_SCOPE", "garmin")
        self.db_path = os.getenv("DB_PATH", "/data/garmin_mcp.db")
        self.session_storage_path = os.getenv(
            "SESSION_STORAGE_PATH", "/data/garmin_sessions"
        )
        # Email allowlist. Only Garmin Connect accounts whose login email is on
        # this list may authenticate. Fail-closed: if unset/empty, NO email is
        # allowed and every login is rejected.
        self.allowed_emails = self._parse_allowed_emails(
            os.getenv("GARMIN_ALLOWED_EMAILS")
        )

    @staticmethod
    def _parse_allowed_emails(value: str | None) -> frozenset[str]:
        """Parse a comma-separated allowlist into a normalized (lowercase) set."""
        if not value:
            return frozenset()
        return frozenset(
            entry.strip().lower() for entry in value.split(",") if entry.strip()
        )

    def is_email_allowed(self, email: str) -> bool:
        """Return True only if ``email`` is on the configured allowlist.

        Fail-closed: an empty allowlist rejects everyone.
        """
        if not self.allowed_emails:
            return False
        return (email or "").strip().lower() in self.allowed_emails

    def validate(self):
        """Validate required configuration."""
        if not self.server_url:
            raise ValueError(
                "GARMIN_MCP_SERVER_URL is required. "
                "Set it to the public URL of your server (e.g., https://garmin-mcp.example.com)"
            )


def get_config() -> RemoteConfig:
    """Get and validate the remote server configuration."""
    config = RemoteConfig()
    config.validate()
    return config
