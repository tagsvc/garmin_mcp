---
name: refresh-garmin-token
description: >-
  Refresh the Garmin Connect token used by the remote Garmin MCP server (the
  one connected to claude.ai / iOS, hosted on Railway). Use when the Garmin
  tools on that connector start failing with "session expired" or
  "re-authenticate", or when the user asks to refresh / re-mint / renew their
  Garmin token. MUST run in local Claude Code on the user's own machine
  (residential IP): Garmin rate-limits (HTTP 429) token minting from
  datacenter/cloud IPs, so this cannot be done from a cloud/remote agent.
---

# Refresh Garmin token

Re-mints a Garmin Connect token from the user's own machine and pushes it to the
remote MCP server's `/import-token` endpoint. The server never logs in to Garmin
itself (its datacenter IP is rate-limited), so this local refresh is the
supported way to renew access.

## Preconditions — STOP if these aren't met

1. **You must be running locally on the user's machine** (their Mac), not in a
   cloud/remote environment. If you are a remote agent, do not run the mint — the
   login will be 429'd from the datacenter IP. Tell the user to run this skill in
   local Claude Code instead.
2. **`uvx` must be available** (`which uvx`). It ships with `uv`. If missing,
   install `uv` first (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

## Configuration

The mint script needs three values. Prefer reading them from the environment;
ask the user only for whatever is missing:

- `GARMIN_MCP_SERVER_URL` — public URL of the deployed server
  (e.g. `https://garminmcp-production-d119.up.railway.app`).
- `GARMIN_IMPORT_SECRET` — the shared secret set as an env var on the server.
- `GARMIN_EMAIL` — the user's allowlisted Garmin Connect email.

The user's **password and MFA code are entered interactively** during the mint.
Do NOT ask the user to paste their password or MFA code to you, and do not put
them in commands you run — they go straight into the interactive prompt.

## Steps

1. Confirm the preconditions above.
2. Make sure `GARMIN_MCP_SERVER_URL`, `GARMIN_IMPORT_SECRET`, and `GARMIN_EMAIL`
   are set (in the env or provided by the user). If any are missing, the script
   will prompt for them too.
3. **Have the user run the mint command in their own terminal** so they can type
   their password and MFA code privately (the interactive prompts won't work
   reliably if you run it for them, and credentials should not pass through you):

   ```bash
   uvx --python 3.12 --with garminconnect python3 \
     "$CLAUDE_SKILL_DIR/scripts/refresh_token.py"
   ```

   (Substitute the real path to `scripts/refresh_token.py` inside this skill
   directory if `$CLAUDE_SKILL_DIR` is not set.)

   The script logs in to Garmin locally, mints a fresh token, and POSTs it to
   `<server>/import-token` with the secret. On success it prints
   `Garmin token refreshed on the server.`

4. If the server returns an error:
   - `401 Unauthorized` → the `GARMIN_IMPORT_SECRET` doesn't match the server's.
   - `403` → the email isn't on the server's `GARMIN_ALLOWED_EMAILS` allowlist.
   - `404` → the server has no `GARMIN_IMPORT_SECRET` set (endpoint disabled).
   - `400 Invalid token` → the mint produced an unexpected blob; re-run.

5. **Verify** by exercising a Garmin tool on the remote connector (e.g. ask for
   today's health status). If it returns data, the refresh worked.

## Notes

- This only needs to be done when the server's stored refresh token has actually
  expired or been invalidated (Garmin password change, sign-out-all-devices,
  long inactivity) — typically weeks to months apart, not routine.
- The refreshed token is persisted on the server's `/data` volume, so it
  survives redeploys.
