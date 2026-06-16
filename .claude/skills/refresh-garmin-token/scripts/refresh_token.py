#!/usr/bin/env python3
"""Mint a fresh Garmin Connect token locally and push it to the remote MCP server.

WHY THIS EXISTS
    Garmin rate-limits (HTTP 429) its OAuth token-mint endpoint from
    datacenter/cloud IPs, so the Railway-hosted server cannot log in to Garmin
    itself. This script performs the login from wherever it is run — meant to be
    YOUR machine, on a residential IP — and pushes the resulting token to the
    server's /import-token endpoint. The server never contacts Garmin's SSO.

USAGE (run in your own terminal so you can type your password + MFA code):

    uvx --python 3.12 --with garminconnect python3 refresh_token.py

CONFIGURATION (read from env vars; prompted interactively if missing):
    GARMIN_MCP_SERVER_URL   e.g. https://garminmcp-production-xxxx.up.railway.app
    GARMIN_IMPORT_SECRET    the shared secret configured on the server
    GARMIN_EMAIL            your allowlisted Garmin Connect email

Your Garmin password and MFA code are entered locally and used only to log in.
They are never written to disk or sent anywhere except Garmin's own login.
"""
import getpass
import json
import os
import sys
import urllib.error
import urllib.request


def _value(label: str, env: str, secret: bool = False) -> str:
    existing = os.environ.get(env, "").strip()
    if existing:
        return existing
    prompt = f"{label}: "
    return (getpass.getpass(prompt) if secret else input(prompt)).strip()


def main() -> None:
    server = _value("Server URL", "GARMIN_MCP_SERVER_URL").rstrip("/")
    secret = _value("Import secret", "GARMIN_IMPORT_SECRET", secret=True)
    email = _value("Garmin email", "GARMIN_EMAIL")
    password = getpass.getpass("Garmin password: ").strip()

    if not (server and secret and email and password):
        sys.exit("Server URL, import secret, email, and password are all required.")

    try:
        from garminconnect import Garmin
    except ImportError:
        sys.exit(
            "garminconnect is not installed. Run this script via:\n"
            "  uvx --python 3.12 --with garminconnect python3 refresh_token.py"
        )

    print("Logging in to Garmin from this machine...", file=sys.stderr)
    garmin = Garmin(email, password, prompt_mfa=lambda: input("MFA code: ").strip())
    garmin.login()  # prompts for the MFA code above if Garmin requires it
    blob = garmin.client.dumps()
    print("Token minted. Pushing to the server...", file=sys.stderr)

    payload = json.dumps({"email": email, "token": blob}).encode()
    request = urllib.request.Request(
        f"{server}/import-token",
        data=payload,
        headers={"Content-Type": "application/json", "X-Import-Secret": secret},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            print(f"Server responded {resp.status}: {resp.read().decode()}")
    except urllib.error.HTTPError as e:
        sys.exit(f"Import failed: HTTP {e.code} — {e.read().decode()}")
    except Exception as e:  # noqa: BLE001 - surface any transport error
        sys.exit(f"Import failed: {e}")

    print("Garmin token refreshed on the server.")


if __name__ == "__main__":
    main()
