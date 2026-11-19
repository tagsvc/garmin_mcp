"""Shared setup for OAuth provider tests: sign-ins and codes as the server stores them.

The login handler refuses a state that names no pending sign-in, and tokens
are honoured only for a user whose email is on the allowlist, so a test that
exercises either path starts from rows the server itself would have written.
"""
import time

CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"


def pending_sign_in(provider, state="s", client_id="c1", redirect_uri=CLAUDE_CALLBACK):
    """A sign-in /authorize started and no user has finished yet."""
    conn = provider._get_conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO auth_codes
               (code, client_id, user_id, scopes, code_challenge, redirect_uri,
                redirect_uri_provided_explicitly, client_state, expires_at)
               VALUES (?, ?, NULL, 'garmin', 'challenge', ?, 1, 'client-state', ?)""",
            (state, client_id, redirect_uri, time.time() + 900),
        )
        conn.commit()
    finally:
        conn.close()
    return state


def issued_code(provider, code="authcode1", client_id="c1", email="ok@x.com"):
    """An authorization code for an allowlisted user, ready to exchange."""
    provider.allowed_emails = provider.allowed_emails | {email}
    user_id = provider._get_or_create_user(email)
    conn = provider._get_conn()
    try:
        conn.execute(
            """INSERT INTO auth_codes
               (code, client_id, user_id, scopes, code_challenge, redirect_uri,
                redirect_uri_provided_explicitly, client_state, expires_at)
               VALUES (?, ?, ?, 'garmin', 'challenge', ?, 1, NULL, ?)""",
            (code, client_id, user_id, CLAUDE_CALLBACK, time.time() + 300),
        )
        conn.commit()
    finally:
        conn.close()
    return user_id
