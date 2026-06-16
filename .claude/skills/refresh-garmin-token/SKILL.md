---
name: refresh-garmin-token
description: >-
  Walk the user through refreshing the Garmin Connect token used by the remote
  Garmin MCP server (the connector on claude.ai / iOS, hosted on Railway). Use
  when the Garmin tools on that connector start failing with "session expired" /
  "re-authenticate", or when the user asks to refresh / re-mint / renew their
  Garmin token. This is a GUIDE: the user runs the local mint command themselves
  and pastes the result into the server's login page. Token minting must happen
  on the user's own machine (residential IP) — Garmin rate-limits (HTTP 429) the
  token-mint endpoint from datacenter/cloud IPs.
---

# Refresh Garmin token (guided)

Garmin rate-limits its OAuth token-mint endpoint from datacenter IPs, so the
Railway server can't log in to Garmin itself. The user mints a fresh token on
their own machine and imports it through the server's login page, which is gated
by the email allowlist **and** a shared import secret.

Walk the user through the steps below. Do **not** ask them to share their Garmin
password, MFA code, the minted token, or the import secret with you — those are
entered locally or on the server's login page only.

## One-time setup (skip if already done)

1. **Generate a long import secret** on their machine:
   ```bash
   openssl rand -hex 32
   ```
   They save it in a password manager. It must never be committed or pasted into chat.
2. **Set it on the server**: in Railway → the service → Variables, add
   `GARMIN_IMPORT_SECRET` = that value. Railway redeploys automatically.

## Refresh steps

1. **Mint a fresh token locally.** Have the user run, in their own terminal
   (residential IP, so no 429):
   ```bash
   python3 -c "from garminconnect import Garmin; g=Garmin(); g.login('~/.garminconnect'); print(g.client.dumps())"
   ```
   - If `garminconnect` isn't installed in their default Python, use `uvx`:
     ```bash
     uvx --python 3.12 --with garminconnect python3 -c "from garminconnect import Garmin; g=Garmin(); g.login('~/.garminconnect'); print(g.client.dumps())"
     ```
   - If their local tokens have also expired, they re-authenticate first:
     ```bash
     uvx --python 3.12 --with garminconnect python3 -c "from garminconnect import Garmin; import getpass; g=Garmin(input('email: '), getpass.getpass('password: '), prompt_mfa=lambda: input('MFA code: ')); g.login(); print(g.client.dumps())"
     ```
   The command prints a one-line JSON blob:
   `{"di_token": "...", "di_refresh_token": "...", "di_client_id": "..."}`.

2. **Import it on the server.** Open the connector's login page (reconnect /
   re-authenticate the Garmin connector in claude.ai, which lands on the Garmin
   login page) and:
   - Expand **"Advanced: import an existing Garmin token instead."**
   - Paste the JSON blob into the token box.
   - Enter the **allowlisted email** in the email field.
   - Enter the **import secret** in the "Import secret" field.
   - Leave **password blank**.
   - Click **Sign In with Garmin**.

3. **Verify.** Ask the remote connector for something real (e.g. today's health
   status). If it returns data, the refresh worked.

## Troubleshooting (messages shown on the login page)

- **"Invalid import secret."** → the secret entered doesn't match
  `GARMIN_IMPORT_SECRET` on the server.
- **"Token import is disabled on this server."** → `GARMIN_IMPORT_SECRET` isn't
  set on Railway (do the one-time setup).
- **"This account is not authorized..."** → the email isn't in
  `GARMIN_ALLOWED_EMAILS`.
- **"Could not import token..."** → the pasted blob isn't valid token JSON;
  re-run the mint command and copy the whole single line.

## Notes

- This is only needed when the server's stored refresh token has actually
  expired or been invalidated (Garmin password change, sign-out-all-devices,
  long inactivity) — typically weeks to months apart.
- The imported token is persisted on the server's `/data` volume, so it survives
  redeploys.
