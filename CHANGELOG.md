# Changelog

All notable changes **this fork** makes relative to its upstream base,
[Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp). See `FORK.md` for the
invariants behind these and the upstream-sync procedure. The authoritative diff is
`git diff upstream/main...main` once the upstream remote is wired.

## Security hardening — 2026-06-18

Phase-1 self-review of the auth surface (`oauth_provider.py`) and its fixes:
- **Reflected XSS fixed** — HTML-escape `state` and `error` on the login and MFA
  pages, which reflected the raw `?state=` query param into the credential form.
- **Rate limiting added** — `/login` (per email), MFA callback (per pending state),
  and `/import-token` (per IP). Slows credential-stuffing / MFA brute-force and
  avoids re-hammering Garmin SSO.
- **Tokens hashed at rest** — access/refresh tokens stored as SHA-256; lookups hash
  the incoming token. A one-time, idempotent migration hashes any pre-existing
  plaintext rows, so live sessions are **not** disrupted on deploy.
- **Security response headers** (Phase-2 finding) — a pure-ASGI middleware adds
  HSTS, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, a strict CSP
  (`default-src 'none'` — blocks scripts), and `Referrer-Policy: no-referrer` to
  every response. Verified live: Phase-2 probe was 10/10 pass with only these
  headers flagged; now added.

Review confirmed clean: parameterized SQL (no injection), constant-time secret
compare, single-use auth codes, per-user isolation, no eval/pickle/path-traversal.
Full suite: 464 passed.

## Upstream sync — 2026-06-18

Merged `Taxuspt/garmin_mcp` (PRs #147–#162, Issues #128/#155).

**Taken from upstream:**
- New tools: `set_activity_type`, `set_activity_description`, `set_activity_event_type`,
  `set_perceived_effort`, `set_activity_feel`, `delete_custom_food`, `create_run_workout`;
  `delete_food_log` reworked (UUID + `meal_date`); custom-food brand/micros fields;
  cycling VO2 max in training status; `get_activity` surfaces description/event type.
- Fixes: `power.between` cycling target-type (#155); nutrition write-tool crashes;
  Windows stdio newline handling (#128).

**Reconciled (remote multi-user):**
- Migrated every new tool off the stdio-only `garmin_client` global to
  `get_client(ctx)`, and threaded the client through the `_put_activity_update` /
  `_update_activity_summary` helpers. None leak `ctx`.
- Kept upstream's nutrition fixes (UUID-aware delete, dict-shaped customFood
  responses) with our `get_client(ctx)` calls.
- Migrated `training.py`'s `_get_activity_type_mapping` helper off the module
  global too — the codebase now has **zero** module-global client usages, so every
  tool is remote-safe (activity-type names no longer degrade to "unknown" in
  remote mode).

Result: full suite 451 passed; tool counts stdio 146 / remote 144.

## Upstream sync — 2026-06-17

Merged `Taxuspt/garmin_mcp` (upstream PRs #140/#141/#142, Issues #137/#138/#139).

**Taken from upstream:**
- Security: workout target-type/end-condition validation; date + GPX-path
  injection validation.
- New tools (now `get_client(ctx)`-migrated so they work in remote mode):
  `create_manual_activity`, `download_activity_file`, `set_fit_download_dir`,
  `unschedule_workout`, `unschedule_workouts`.
- `_GarminProxy` (friendly runtime error messages); `GARMIN_MCP_TRANSPORT`
  plumbing (stdio default) + `/healthz`; dependency bumps (starlette 0.52→1.3.1,
  pyjwt, cryptography, python-multipart). `garminconnect` stays `0.3.2`.

**Reconciled / not taken:**
- Kept our per-user `get_client(ctx)` pattern, no-token startup, and stdio-only
  `auth_tools`. Migrated upstream's new tools off the module global.
- Did **not** adopt PR #141's unauthenticated HTTP transport as the public
  server — our OAuth2 remote (allowlist + import-secret + per-user sessions) stands.

Result: full suite 421 passed; tool counts stdio 139 / remote 137.

## Fork divergence

### Added
- **Historical analytics — 8 tools** (`src/garmin_mcp/analytics.py`): rolling
  baselines, wellness anomalies, lagged correlations, weekly review, and
  saved/custom multi-metric health reports. Registered in `remote.py` and
  `__init__.py`. _Adapted from coloboxp/garmin_mcp PR #121._ (PR #1)
- **Interactive auth — 2 tools** (`src/garmin_mcp/auth_tools.py`):
  `check_garmin_auth`, `login_to_garmin`; stdio-only (`__init__.py`).
  _Adapted from PR #121; token dump migrated to `garmin.client.*` for garminconnect 0.3.2._ (PR #1)
- **`token_utils` helpers**: `resolve_path`, `ensure_token_directory`,
  `without_token_env`, `_clean_config_value` (additive). (PR #1)
- **Token import** for the remote server: paste a pre-minted token on the login
  page, or `POST /import-token`. Lets a token minted on a residential IP be used
  by the server, bypassing Garmin's datacenter-IP throttling. (PR #3, #4)
- **Railway deploy config**: `railway.json` pinned to `Dockerfile.remote`. (PR #1)

### Security
- **Email allowlist** (`GARMIN_ALLOWED_EMAILS`) enforced in
  `oauth_provider.handle_login_callback`; fail-closed (unset rejects all). (PR #1)
- **Import-secret gating** (`GARMIN_IMPORT_SECRET`): required, with constant-time
  compare, on both token-import paths in addition to the allowlist; fail-closed
  (unset disables import). Closes a session-overwrite (DoS) vector on the
  browser import path. (PR #4, #5)
- **Reflected-XSS fix**: HTML-escape `state`/`error` on the login & MFA pages. (PR #9)
- **Rate limiting** on `/login`, MFA callback, and `/import-token` (`_RateLimiter`). (PR #9)
- **Token-at-rest hashing**: access/refresh tokens stored as SHA-256, with an
  idempotent migration for existing rows. (PR #9)
- **Response security headers** (HSTS, nosniff, X-Frame-Options, CSP,
  Referrer-Policy) via `_SecurityHeadersMiddleware`. (PR #10)

### Fixed
- **429 fail-fast login client** (`oauth_provider._new_login_client`): excludes
  429 from garth's retry list so a rate-limited Garmin login isn't amplified into
  a retry storm. (PR #2)

### Deployment / portability
- **`$PORT` support**: `config.port` honors the platform-injected `PORT`
  (then `GARMIN_MCP_PORT`, then `8000`) for Railway. (post-merge to `main`)
- **Removed the `VOLUME` instruction** from `Dockerfile.remote` — Railway's
  builder rejects it; persistence uses a Railway volume at `/data`. (post-merge to `main`)

### Docs
- README: analytics/auth coverage, Deployment Modes + Railway quickstart,
  Security (allowlist + import secret), token import/refresh, retargeted fork links.
- Added `FORK.md` (fork invariants + upstream-sync procedure) and `CLAUDE.md`
  (auto-loaded pointer for future sessions).

### Removed
- The `refresh-garmin-token` skill (`.claude/skills/`) was added then removed;
  the refresh procedure is kept out of the repo as a personal chat script. (PR #6)
