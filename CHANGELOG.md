# Changelog

All notable changes **this fork** makes relative to its upstream base,
[Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp). See `FORK.md` for the
invariants behind these and the upstream-sync procedure. The authoritative diff is
`git diff upstream/main...main` once the upstream remote is wired.

## CI actually scans for vulnerabilities now — 2026-08-21

`security.yml`'s "Check for dependency vulnerabilities" step was a placeholder:
it ran `uv pip list`, echoed *"Checking for known security vulnerabilities…"*,
and checked nothing. Between the job name (`dependency-check`), the step name,
and the reassuring output, it read as a working scanner — the most misleading
possible state, and worse once `OPERATIONS.md` described the surrounding posture.

- **Runs `pip-audit` for real**, against `uv export --frozen --no-dev` — the same
  export `Dockerfile.remote` installs from, so it audits exactly what is deployed
  rather than a fresh resolve. Verified it detects genuine advisories and exits
  non-zero, rather than trusting a clean result from an unproven scanner.
- **Deliberately overlaps Dependabot.** They fail differently: Dependabot is
  asynchronous and graph-derived, pip-audit is synchronous and reports on the PR
  that introduces the problem. Recorded in `OPERATIONS.md`.
- **Left out of the required checks, deliberately** — a newly published advisory
  with no available fix would otherwise block every unrelated PR.
- **Dead lock-file guard fixed.** GitHub runs `run:` blocks under `bash -e`, so
  `uv lock --check` failing aborted the step before its `if [ $? -ne 0 ]` branch
  could run — the "run `uv lock` locally" message had never printed. The step
  failed correctly; the guidance was unreachable. Also moved ahead of the audit,
  since auditing a stale lock file scans the wrong dependency set.
- **`OPERATIONS.md` correction** — it claimed a lock-check failure blocks the
  merge. It does not: `security.yml`'s jobs are not required checks.

## Platform configuration documented — 2026-08-21

No code change. The repository and deployment settings that protect this fork
lived only in one person's memory; `OPERATIONS.md` now records them as the
counterpart to `FORK.md` — code invariants there, platform configuration here.

- **Branch ruleset on `main`** — active, empty bypass list, no direct pushes, no
  force pushes, no deletion, and the four CI checks required with GitHub Actions
  pinned as their reporting source. This changes `FORK.md`'s invariants from
  *visible when broken* to *unmergeable when broken*.
- **GitHub Advanced Security** — secret scanning with push protection, dependency
  graph (off by default on forks), Dependabot alerts / malware alerts / security
  updates / grouped updates, private vulnerability reporting.
- **Dependabot version updates deliberately left off**, with the reasoning
  recorded: the deliberate pins (`garminconnect==0.3.5`, `mcp<2`, exact `requests`
  and `python-dotenv`) would generate PRs that must be closed every time.
- **Railway** — builder pin, `/data` volume, environment-variable contract (names
  and which are fail-closed), and the backup policy: daily schedule plus a locked
  snapshot as a permanent known-good baseline.
- **`FORK.md`** — sync procedure corrected: it told you to merge to `main` and
  push, which the ruleset now refuses. It routes through a PR instead.

## Hardening round 2 — 2026-08-21

Two issues introduced by the same day's fixes, found in follow-up validation.
Filed as "cosmetic"; the first is not.

- **Log injection through `X-Forwarded-For`** — the same bug class as L4, reached
  through a different input. `_client_ip()` reads an attacker-controlled header and
  its value was logged raw at the `/import-token` rate-limit warning, so CR/LF in
  the header could forge log lines. Fixed **at the source**: `_client_ip()` now
  escapes and length-bounds what it returns, so every caller — present and future —
  is safe rather than each log site needing to remember. A sweep found and fixed a
  third site as well (raw request `state` in the auth-state warnings).
- **Unsanitised `course_name` as a multipart filename** — introduced with the M3
  fix. Now passed through `_safe_upload_filename()`: strips directory components
  (`../../etc/passwd` -> `passwd.gpx`), quotes that could inject into
  `Content-Disposition`, and CR/LF; falls back to `course.gpx` when nothing usable
  remains, and bounds the length.

Tests: +4. Result: full suite 592 passed.

## Dependency refresh — 2026-08-21

Full `uv lock --upgrade` of everything the deliberate pins allow (~30 packages),
plus removal of four transitive packages nothing needs any more (`rich`,
`markdown-it-py`, `mdurl`, `sniffio`).

**The invariant pins held automatically**, which was the point of recording them:
`garminconnect` stayed `0.3.5`, `garth` stayed `<0.6.0`, `requests` and
`python-dotenv` unchanged, and **`mcp` stopped at 1.29.0 rather than 2.x** — the
`<2` cap prevented an upgrade that would have swapped out `mcp.server.fastmcp`
and broken the server outright.

Notable moves, all in the serving path and therefore validated explicitly:
`starlette` 1.3.1 -> 1.6.0, `sse-starlette` 2.2.1 -> **3.4.8** (major),
`uvicorn` 0.34.0 -> 0.52.4, `pydantic` 2.12.5 -> 2.13.4 (+ `pydantic-settings`
2.8.1 -> 2.15.0), `curl-cffi` 0.15 -> 0.16 (garth's HTTP layer), `click` 8.1.8 ->
8.4.2 (clears the advisory; `click.edit` was never called here).

Verification beyond the suite: the OAuth layer imports and its RFC 9728 patches
apply; the remote app builds and wraps in the security-headers middleware; and the
server was started locally on the new stack, where `/mcp` returned 401 both
anonymously and with a forged bearer, discovery served 200, and all five security
headers were present.

Known benign warning: `pydantic-settings` 2.15 emits
`IncompleteFieldDefinitionWarning` for MCP's own `FastMCP.lifespan` field (an
unresolved forward reference in the SDK's settings model, not our code, and a
field this server never sets).

Result: full suite 588 passed.

## Security review backlog — M3, L1, L2, L3, L4

Closes the findings deliberately deferred from the August 2026 review.

- **M3 — remote tools accepted server filesystem paths.** `upload_course` read
  `gpx_path` off the server's disk in remote mode: an arbitrary-file-read
  primitive (any `.gpx`-suffixed file) for any authenticated user. It now takes
  `gpx_base64` and refuses `gpx_path` in remote mode, where a server path is both
  useless to the caller and dangerous. Local stdio use is unchanged.
- **L1 — analytics saved-report store was global.** Scoped per `user_id` in
  remote mode, so one user can no longer read, overwrite, or delete another's
  report definitions.
- **L3 — login-limiter lockout and allowlist oracle.** The limiter is keyed on
  email + client IP (email alone let anyone lock an allowlisted account out with
  one request every ~37s), and the browser login path now returns a single
  generic error for both "not allowlisted" and "bad credentials" so membership
  cannot be inferred by differencing. The specific reason is still logged.
- **L2 — container ran as root.** The image now starts as root only long enough
  to take ownership of the volume, then drops to an unprivileged `appuser` via
  `gosu` before exec'ing the server, so an RCE in the Python process does not get
  root in the container. A bare `USER` directive cannot work here: Railway mounts
  volumes root-owned at container start, and its documented workaround
  (`RAILWAY_RUN_UID=0`) would force root again.
- **L4 — log injection.** Untrusted values are escaped before logging
  (`_safe_log`), so CR/LF in an email cannot forge log lines.

Tests: +10 covering each behaviour, including that the two login rejections are
byte-identical and that an attacker cannot lock out the real user from another IP.

## Security review fixes — 2026-08-20

From the August 2026 security review (findings H1, M1, M2):

- **H1 — CVE-2026-54447** (`garminconnect` <=0.3.4 writes `garmin_tokens.json`
  world-readable): pin raised to `garminconnect==0.3.5`, and
  `session_manager` now calls `secure_token_dir()` after **every** token dump so
  remote mode gets owner-only permissions regardless of library version.
  Verified: 0.3.5 writes mode `0o600`, and the di-token format
  (`di_token`/`di_refresh_token`/`di_client_id`) round-trips unchanged, so the
  pin invariant's re-verification requirement is satisfied.
- **H1 residual closed** — `SessionManager` now sweeps existing session
  directories at startup and applies owner-only permissions, so token files
  written *before* the hardening (mode 0644 on the volume) are healed in place.
  No manual chmod or re-import needed; idempotent and logged.
- **M1 — rate-limit bypass via spoofable `X-Forwarded-For`**: `_client_ip()` now
  keys on the LAST XFF hop (appended by the edge proxy) instead of the first
  (client-supplied). Previously an attacker could mint a fresh bucket per
  request and bypass the `/import-token` limiter. Recorded as an invariant, with
  a regression test proving the old logic yields 30 buckets over 30 requests
  where the fix yields 1.
- **M2 — h11 CVE-2025-43859** (request smuggling): `h11` 0.14.0 -> 0.16.0
  (httpcore 1.0.9) in the regenerated lockfile.
- Python floor raised to >=3.12 (required by garminconnect 0.3.5); CI matrix
  trimmed to 3.12/3.13 and the DXT manifest/bundle updated to match.

Deferred (documented in the review report, not scheduled): M3 remote
`upload_course` server-path read, L1-L4 (shared analytics report store, non-root
container, login-limiter lockout, log injection).

Result: full suite 576 passed; tool counts stdio 150 / remote 148.

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

## Upstream sync — 2026-06-20

Merged `Taxuspt/garmin_mcp` (12 commits, PRs #219–#243).

**Taken from upstream:**
- **`mcp` capped `<2`** (#227) — mcp 2.x renames `mcp.server.fastmcp` →
  `mcp.server.mcpserver` (`FastMCP` → `MCPServer`). Without the cap a fresh
  dependency resolve could pull 2.x and break the server. Recorded as an invariant.
- New tools: `search_foods` (catalog search + source routing for `log_custom_food`),
  `get_garmin_coach_workouts` (with `get_training_plan_workouts` as a compat alias).
- Fixes: weather temperature conversion + wind label (#223); nested workout target
  bounds moved to step level (#221); invalid category on strength steps (#222);
  custom bpm HR range in `create_run_workout` (#224); virtual challenge field
  mapping (#212); DXT `${HOME}` token path (#204); VO2 max profile fallback (#240).
- Dependency: `cryptography` 48.0.1 → 50.0.0 (validated). Hatch wheel packaging.

**Reconciled (remote multi-user):**
- Migrated every new/rewritten path to `get_client(ctx)`: `search_foods`, the
  Garmin Coach helper (now takes the resolved client), and upstream's rewritten
  VO2 trend loop (`_get_max_metrics_range` + profile fallback). None leak `ctx`.
- `token_utils`: kept our path helpers **and** upstream's `resolve_token_path`.
- `training.py`: kept our client-passing `_get_activity_type_mapping` alongside
  upstream's new VO2 helpers.

Result: full suite 573 passed; tool counts stdio 150 / remote 148.

## Upstream sync — 2026-06-19

Merged `Taxuspt/garmin_mcp` (PRs #201 batch, #205/#206, #163/#165).

**Taken from upstream:**
- New tools: `set_nutrition_daily_settings`, `get_training_load_balance`.
- Fixes: weather °C/°F label for imperial accounts; suppress stdout during login
  to prevent MCP stdio corruption; `token_utils.secure_token_dir()` (owner-only perms).
- **Dependency bump: `mcp` 1.26.0 → 1.28.1** (validated against the OAuth provider,
  RFC 9728 patches, and security-headers middleware). `garminconnect` stays `0.3.2`.

**Reconciled (remote multi-user):**
- Migrated the new tools + the weather fix's `get_unit_system()` call to
  `get_client(ctx)`. None leak `ctx`.
- Adapted upstream's new courses tests to our pattern (`set_global_client` fixture;
  mock the top-level `Garmin.connectapi` error-handling wrapper).

Result: full suite 484 passed; tool counts stdio 148 / remote 146.

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
