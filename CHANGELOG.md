# Changelog

All notable changes **this fork** makes relative to its upstream base,
[Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp). See `FORK.md` for the
invariants behind these and the upstream-sync procedure. The authoritative diff is
`git diff upstream/main...main` once the upstream remote is wired.

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
