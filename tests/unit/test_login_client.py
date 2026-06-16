"""Unit tests for the fail-fast Garmin login client (429 handling)."""

from garmin_mcp.oauth_provider import (
    _LOGIN_RETRY_STATUS_FORCELIST,
    _new_login_client,
)


def test_login_client_does_not_retry_on_429():
    """A rate-limited (429) login must fail fast instead of being retried."""
    client = _new_login_client()
    assert 429 not in client.status_forcelist


def test_login_client_still_retries_transient_errors():
    """Genuinely transient errors should still be retried."""
    client = _new_login_client()
    for status in (500, 502, 503, 504):
        assert status in client.status_forcelist


def test_login_client_adapter_retry_excludes_429():
    """The mounted HTTPS adapter's Retry policy must not include 429."""
    client = _new_login_client()
    adapter = client.sess.get_adapter("https://connectapi.garmin.com")
    forcelist = adapter.max_retries.status_forcelist or ()
    assert 429 not in forcelist


def test_login_retry_forcelist_constant_excludes_429():
    assert 429 not in _LOGIN_RETRY_STATUS_FORCELIST
