"""
Session manager for per-user Garmin sessions in remote mode.

Persists Garmin Connect sessions in garminconnect's native token format
(``garmin_tokens.json`` with a DI bearer token) per-user on disk, and caches
active clients in memory. The login flow obtains tokens via garth SSO and they
are bridged into garminconnect's format on save (see
:meth:`SessionManager.create_session_from_garth_tokens`).
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import threading
from typing import Dict, Optional

from garminconnect import Garmin

from garmin_mcp.token_utils import secure_token_dir

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages per-user Garmin Connect sessions."""

    # Cache TTL in seconds (1 hour)
    CACHE_TTL = 3600

    def __init__(self, storage_path: str):
        self.storage_path = storage_path
        self._cache: Dict[str, _CachedClient] = {}
        self._token_user_map: Dict[str, str] = {}
        # Guards the dicts only, never a call to Garmin.
        self._lock = threading.Lock()
        # One per user: restoring, writing or removing one user's session waits
        # for that user's other session work, never for another user's.
        self._user_locks: Dict[str, threading.Lock] = {}
        os.makedirs(storage_path, exist_ok=True)
        self._secure_existing_sessions()

    def _secure_existing_sessions(self) -> None:
        """Apply owner-only permissions to token dirs already on disk.

        The per-dump ``secure_token_dir()`` calls only cover the tokens they
        write, so a session already on the volume (written without them, or by
        an affected garminconnect <=0.3.4, CVE-2026-54447) would stay
        world-readable until the next re-import. Sweeping at startup heals them in place
        with no manual chmod. Idempotent and cheap: a handful of directories,
        and re-running on already-secured dirs is a no-op.
        """
        secured = 0
        try:
            entries = list(os.scandir(self.storage_path))
        except OSError:
            logger.debug("Could not scan %s for session hardening", self.storage_path)
            return

        for entry in entries:
            if not entry.is_dir():
                continue
            try:
                secure_token_dir(entry.path)
                secured += 1
            except OSError:
                # Never let a permissions hiccup stop the server from booting.
                logger.warning("Could not secure token dir %s", entry.path, exc_info=True)

        if secured:
            logger.info(
                "Applied owner-only permissions to %d existing session dir(s) in %s",
                secured,
                self.storage_path,
            )

    def get_user_id_for_token(self, token: str) -> Optional[str]:
        """Get the user_id associated with an access token."""
        return self._token_user_map.get(token)

    def set_token_user_mapping(self, token: str, user_id: str) -> None:
        """Associate an access token with a user_id."""
        self._token_user_map[token] = user_id

    def _user_token_dir(self, user_id: str) -> str:
        """Get the token storage directory for a user."""
        return os.path.join(self.storage_path, user_id)

    def _user_lock(self, user_id: str) -> threading.Lock:
        with self._lock:
            return self._user_locks.setdefault(user_id, threading.Lock())

    def _retire_cached(self, user_id: str) -> None:
        """Drop a user's cached client before the session on disk is replaced.

        A call already holding the client keeps using it, but the client stops
        saving token refreshes: garminconnect writes a refresh to the directory
        the client loaded from, which would put the replaced tokens back over
        the new ones.
        """
        with self._lock:
            cached = self._cache.pop(user_id, None)
        if cached is not None:
            _stop_token_writes(cached.client)

    def get_client(self, user_id: str) -> Optional[Garmin]:
        """Get or restore a Garmin client for a user.

        Returns None if no session exists for this user. A restore talks to
        Garmin, so it holds only this user's lock: another user's calls go on
        while it runs.
        """
        cached = self._cached_client(user_id)
        if cached is not None:
            return cached

        with self._user_lock(user_id):
            # Another call may have restored the session while this one waited.
            cached = self._cached_client(user_id)
            if cached is not None:
                return cached

            token_dir = self._user_token_dir(user_id)
            if not os.path.isdir(token_dir):
                logger.warning(
                    "No Garmin session on disk for user %s (looked in %s)",
                    user_id,
                    token_dir,
                )
                return None

            try:
                garmin = Garmin()
                # NOTE: this performs live Garmin Connect API calls (social
                # profile + user settings) and refreshes tokens if needed, so it
                # can fail for reasons unrelated to the stored tokens being
                # missing (expired refresh token, Garmin rate-limiting/blocking
                # the server IP, transient network errors, etc.).
                garmin.login(token_dir)
            except Exception:
                # Don't swallow silently: the cause is otherwise invisible and
                # surfaces only as a generic "session expired" to the user.
                logger.exception(
                    "Failed to restore Garmin session for user %s from %s",
                    user_id,
                    token_dir,
                )
                return None

            with self._lock:
                self._cache[user_id] = _CachedClient(garmin, self.CACHE_TTL)
            return garmin

    def _cached_client(self, user_id: str) -> Optional[Garmin]:
        with self._lock:
            cached = self._cache.get(user_id)
            if cached and not cached.is_expired():
                return cached.client
        return None

    def create_session_from_garth_tokens(
        self, user_id: str, oauth1_token, oauth2_token
    ) -> None:
        """Persist an SSO login as a garminconnect-native session.

        The login flow obtains tokens via garth SSO (OAuth1/OAuth2), but
        garminconnect — which makes the actual API calls when we restore
        a session in :meth:`get_client` — does not use garth. It authenticates
        with a DI bearer token persisted as a single ``garmin_tokens.json``
        (keys: ``di_token`` / ``di_refresh_token`` / ``di_client_id``).

        The garth OAuth2 access/refresh tokens ARE that DI bearer/refresh pair,
        so we bridge them into garminconnect's client and let it persist in its
        own format. Dumping with garth's two-file format here would make every
        restore fail with "session expired" (garminconnect looks for
        garmin_tokens.json, finds oauth{1,2}_token.json, and falls back to
        username/password it doesn't have).

        Args:
            user_id: The user identifier.
            oauth1_token: garth OAuth1Token from SSO login (unused; kept for the
                call signature — garminconnect does not use OAuth1).
            oauth2_token: garth OAuth2Token from SSO login; its ``access_token``
                is the DI bearer and ``refresh_token`` the DI refresh token.
        """
        garmin = Garmin()
        client = garmin.client
        client.di_token = oauth2_token.access_token
        client.di_refresh_token = oauth2_token.refresh_token
        extract = getattr(client, "_extract_client_id_from_jwt", None)
        if extract is not None:
            try:
                client.di_client_id = extract(oauth2_token.access_token)
            except Exception:
                logger.debug("Could not extract di_client_id from JWT", exc_info=True)

        self._write_session(user_id, client)

    def _write_session(self, user_id: str, client) -> None:
        """Save a garminconnect client's tokens as the user's session.

        Under the user's lock, so no restore loads the old tokens meanwhile, and
        after the cached client is retired, so it cannot write them back. The
        next get_client() loads the new tokens from disk.
        """
        with self._user_lock(user_id):
            self._retire_cached(user_id)
            token_dir = self._user_token_dir(user_id)
            os.makedirs(token_dir, exist_ok=True)
            client.dump(token_dir)  # writes garmin_tokens.json
            # Owner-only permissions on the token dir and files. Defence in depth
            # over the garminconnect >= 0.3.5 fix for CVE-2026-54447 (token store
            # written world-readable under the default umask), so the guarantee
            # does not depend on the library version.
            secure_token_dir(token_dir)

    def create_session_from_token_blob(self, user_id: str, blob: str) -> None:
        """Persist a Garmin session from a pre-minted token blob.

        Lets a user authenticate Garmin from a trusted (e.g. residential) IP and
        import the resulting tokens here, so the server never performs the SSO /
        OAuth token-mint handshake itself. This sidesteps Garmin rate-limiting
        (HTTP 429) of those endpoints from datacenter/cloud IPs.

        Accepts either the raw contents of garminconnect's ``garmin_tokens.json``
        (a JSON object with ``di_token`` / ``di_refresh_token`` / ``di_client_id``)
        or a base64 encoding of it.

        Args:
            user_id: The user identifier.
            blob: The token JSON (or base64 thereof).

        Raises:
            ValueError: If the blob is empty or not valid Garmin token JSON.
        """
        import base64
        import json

        text = (blob or "").strip()
        if not text:
            raise ValueError("Token is empty.")

        # Normalize to the raw JSON string garminconnect expects.
        try:
            json.loads(text)
            token_json = text
        except Exception:
            try:
                token_json = base64.b64decode(text, validate=False).decode("utf-8")
                json.loads(token_json)
            except Exception as e:
                raise ValueError(f"Token is not valid JSON or base64 JSON: {e}") from e

        # Validate the tokens are structurally complete before persisting.
        garmin = Garmin()
        try:
            garmin.client.loads(token_json)
        except Exception as e:
            raise ValueError(f"Token is missing required fields: {e}") from e

        self._write_session(user_id, garmin.client)

    def remove_session(self, user_id: str) -> bool:
        """Remove a user's Garmin session and tokens.

        Returns True if session existed and was removed.
        """
        with self._user_lock(user_id):
            with self._lock:
                removed = user_id in self._cache
            self._retire_cached(user_id)

            token_dir = self._user_token_dir(user_id)
            if os.path.isdir(token_dir):
                shutil.rmtree(token_dir)
                removed = True

        return removed

    def merge_sessions(self, keep_id: str, merged_id: str) -> None:
        """Fold one user's Garmin session into another's, for users being merged.

        The newer session on disk (by its token file's time) ends up under
        keep_id, and merged_id's directory is removed, so the account keeps the
        tokens it last saved whichever of the two users held them.
        """
        first, second = sorted((keep_id, merged_id))
        with self._user_lock(first), self._user_lock(second):
            self._retire_cached(keep_id)
            self._retire_cached(merged_id)
            source = self._user_token_dir(merged_id)
            target = self._user_token_dir(keep_id)
            if not os.path.isdir(source):
                return
            if os.path.isdir(target) and _token_time(target) >= _token_time(source):
                shutil.rmtree(source)
                return
            if os.path.isdir(target):
                shutil.rmtree(target)
            shutil.move(source, target)
            secure_token_dir(target)

    def has_session(self, user_id: str) -> bool:
        """Check if a user has a stored Garmin session."""
        token_dir = self._user_token_dir(user_id)
        return os.path.isdir(token_dir)


def _stop_token_writes(garmin) -> None:
    """Stop a garminconnect client saving token refreshes to disk.

    garminconnect saves a refresh to ``client._tokenstore_path``, the directory
    the tokens were loaded from; with it unset, a refresh stays in memory.
    ``test_session_manager.py`` checks the library still has the attribute.
    """
    inner = getattr(garmin, "client", None)
    if inner is not None and hasattr(inner, "_tokenstore_path"):
        inner._tokenstore_path = None


def _token_time(token_dir: str) -> float:
    """When the session in a token directory was last saved; 0 if it has none."""
    try:
        return os.path.getmtime(os.path.join(token_dir, "garmin_tokens.json"))
    except OSError:
        return 0.0


class _CachedClient:
    """A cached Garmin client with TTL."""

    def __init__(self, client: Garmin, ttl: int):
        self.client = client
        self._expires_at = time.time() + ttl

    def is_expired(self) -> bool:
        return time.time() > self._expires_at
