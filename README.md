# Garmin Connect MCP server

An MCP server that gives Claude your Garmin Connect data: activities, health and
sleep, training metrics, workouts, gear and nutrition. It runs mainly as a
**remote server for claude.ai, hosted on Railway**, and as a **local stdio
server** (Claude Desktop, Claude Code and other clients) when the remote one
cannot be used.

**About this fork.** This is a fork of
[Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp). It adds, among
other things:

- Health Snapshot and ECG tools, power zones and fastest splits, on
  garminconnect 0.3.20;
- explicit time zones on every time, and values in the account's own units
  beside Garmin's metric ones;
- historical health analytics, Garmin Coach plan tools, a check of where a
  run's distance came from, and `inspect_garmin` diagnostics;
- the remote server's sign-in: an email allowlist and a secret-gated token
  import.

[FORK.md](FORK.md) lists what the fork adds and the rules that must not break,
[OPERATIONS.md](OPERATIONS.md) covers the deployment and repository settings,
[CHANGELOG.md](CHANGELOG.md) is the history, and [SECURITY.md](SECURITY.md)
says how to report a vulnerability. Garmin's API is reached through
[python-garminconnect](https://github.com/cyberjunky/python-garminconnect).

**Contents:** [What it can do](#what-it-can-do) ·
[Remote Mode](#remote-mode) · [Local fallback (stdio)](#local-fallback-stdio) ·
[Conventions](#conventions) · [Tool notes](#tool-notes) ·
[Tool profiles and filtering](#tool-profiles-and-filtering) ·
[Development](#development) · [Credits](#credits)

## What it can do

This MCP server implements **~181 tools** on python-garminconnect 0.3.20, plus
Garmin Connect endpoints the library does not cover. The remote server offers
all of them except the three stdio-only tools (see [Security](#security)).
Items marked *fork* exist only in this fork.

**Activities**

- ✅ Activity Management (24 tools) - list, search and count activities; details, splits, laps, time in HR and power zones, weather; manual activities; edit name, type, description, event type, perceived effort, feel and strength exercise sets; your fastest 1K, mile and 5K in a date range (`get_fastest_splits`, *fork*); a confirm-gated `delete_activity` (stdio only). `get_activity` says where a run's, walk's or hike's distance came from (GPS, the watch, or a paired strap or foot pod) and flags a distance that disagrees with the GPS track by more than 5% (*fork*)
- ✅ Activity Analysis (3 tools) - the original FIT file: cycling analysis (power, Di2/eTap shifting, climbs with VAM, cardiac drift, W/kg), every FIT message with pagination, and a season-best power duration curve
- ✅ Activity File Downloads (2 tools) - an activity as FIT, GPX, TCX or CSV
- ✅ Courses (5 tools) - list, details, upload a GPX (base64, or a local path over stdio), download as GPX, delete
- ✅ Gear Management (4 tools) - gear with its Garmin Connect notes; add or remove gear on an activity; `get_gear_activities` lists a shoe's or bike's activities for mileage audits (*fork*)

**Health**

- ✅ Health & Wellness (35 tools) - daily stats, steps, floors, heart rate, resting HR, sleep, stress, body battery, respiration, SpO2, hydration, blood pressure, body composition, training readiness, recovery time, health status; weekly aggregates, multi-day calorie and step totals, and a composition-derived TDEE estimate
- ✅ Health Snapshot & ECG (4 tools, *fork*) - two-minute Health Snapshots (heart rate, respiration, stress, SpO2, HRV) with their per-second samples, and ECG recordings from the watch's ECG app
- ✅ Historical Analytics (8 tools, *fork*) - rolling baselines, wellness anomalies, lagged correlations, a weekly review, and custom or saved multi-metric health reports
- ✅ Weight Tracking (5 tools) - weigh-ins: read, add (optionally with timestamps), delete
- ✅ Data Management (3 tools) - log body composition, blood pressure and hydration
- ✅ Women's Health (3 tools) - menstrual calendar and day data, pregnancy summary
- ✅ Nutrition (15 tools) - food logs, meals, custom foods, food search and logging, daily nutrition settings, multi-day intake summaries

**Training and workouts**

- ✅ Training & Performance (18 tools) - training status, load and load balance, CTL/ATL/TSB, HRV, VO2 max and respiration trends, training effect, FTP, lactate threshold, endurance and hill scores, fitness age, heat and altitude acclimation, running tolerance, progress summaries
- ✅ Challenges & Badges (9 tools) - badges, challenges, goals, personal records, race predictions
- ✅ User Profile (7 tools) - name, profile, settings, unit system, per-sport heart rate zones (read and write), and power zones (`get_power_zones`, *fork*)
- ✅ Workouts (18 tools) - the workout library: upload, download, schedule, unschedule, move to another date, delete. Garmin Coach: plan workouts, and (*fork*) plans with their status, plan detail (race goal, confidence, training week) and per-workout grades
- ✅ High-Level Workout Builders (6 tools) - runs, run intervals, walk/run, Z2 walks and strength sessions without writing JSON, and scheduling a week ([docs/workouts.md](docs/workouts.md))
- ✅ Calendar (1 tool) - races and events from the Garmin calendar

**Devices, sign-in and diagnostics**

- ✅ Devices (8 tools) - devices, settings, alarms, solar data, last used and primary training device; paired-sensor battery levels (`get_sensors`, *fork*); sending a workout to a device
- ✅ Interactive Auth (2 tools, stdio only, *fork*) - `check_garmin_auth` / `login_to_garmin` to sign in to Garmin without restarting the client
- ✅ Diagnostics (1 tool, *fork*) - `inspect_garmin`: Garmin's raw reply for a read-only tool or a GET of one API path, and the fields a tool's output leaves out

The cycling analyses need power and/or electronic-shifting data in the FIT file
(e.g. a Garmin Rally, Favero Assioma or PowerTap P1 power meter, Shimano Di2 or
SRAM eTap). `get_activity_fit_messages` reads any activity's FIT file. Some
Garmin methods are deliberately left out; see
[docs/skipped-endpoints.md](docs/skipped-endpoints.md).

Things to ask once it is connected: "What was my sleep like last night, and how
is my HRV trending?", "Show my CTL, ATL and TSB for the last six weeks", "How is
my Garmin Coach plan going this week?"

## Remote Mode

The remote server (`garmin-mcp-remote`) serves the tools over Streamable HTTP
with OAuth2, so claude.ai, and the Claude apps that share its connectors, can
use them from anywhere. Each allowlisted Garmin account gets its own session.

### How it fits together

```
claude.ai ──HTTPS──▶ https://<your-domain>/mcp            (Railway, Dockerfile.remote)
   │  401 → OAuth discovery (/.well-known/oauth-protected-resource)
   │  /authorize → /login: Garmin email + password, or an imported token
   │            → /login/mfa when the account has 2FA
   ▼
per-user Garmin session  /data/garmin_sessions/<user id>/garmin_tokens.json
OAuth clients, users and hashed tokens  /data/garmin_mcp.db (SQLite)
   │
   ▼
each tool call: bearer token → that user's Garmin session → Garmin Connect
```

- The sign-in page is the server's own. It passes the Garmin credentials to
  Garmin once and keeps only the resulting session tokens.
- claude.ai connects with a pre-registered client (`client_id`
  `https://claude.ai`); other MCP clients register dynamically.
- The server is stateless: every request is identified by its OAuth bearer
  token, so there is no MCP session to expire between calls.
- The `/data` volume holds the database and the Garmin sessions. Lose it and
  every user signs in again.

### Deploying on Railway

The repo ships a `railway.json` pinned to `Dockerfile.remote`, so Railway
builds the HTTP server straight from git. The bare `Dockerfile` runs the stdio
server and cannot serve traffic.

1. Create a Railway service from this repo (branch `main`); `railway.json`
   selects the Dockerfile automatically.
2. **Settings → Networking → Generate Domain.** The server binds to Railway's
   injected `PORT` itself; leave `GARMIN_MCP_PORT` unset.
3. Add a **volume mounted at `/data`**. It keeps the SQLite database and the
   Garmin sessions across redeploys; without it, every redeploy signs everyone
   out.
4. Set at least `GARMIN_MCP_SERVER_URL` (`https://<your-domain>`) and
   `GARMIN_ALLOWED_EMAILS`. Set `GARMIN_IMPORT_SECRET` too if you will import
   tokens (you probably will; see [Token import / refresh](#token-import--refresh)).
5. In claude.ai, add a custom connector with the URL
   `https://<your-domain>/mcp` and sign in (below).
6. Back up the volume. [OPERATIONS.md](OPERATIONS.md) has the backup policy,
   the full Railway configuration and the repository settings; this README does
   not repeat them.

### Configuration

Read by `src/garmin_mcp/config.py` and `remote.py`:

| Variable | Default | Notes |
|---|---|---|
| `GARMIN_MCP_SERVER_URL` | *(required)* | Public base URL. OAuth metadata and redirects use it; the server will not start without it. |
| `GARMIN_ALLOWED_EMAILS` | *(none: every login rejected)* | Comma-separated Garmin Connect emails allowed to sign in, matched case-insensitively. **Fail-closed.** |
| `GARMIN_IMPORT_SECRET` | *(none: import disabled)* | Shared secret for both token-import paths. **Fail-closed:** unset, `/import-token` answers 404 and the login-page import is refused. |
| `DB_PATH` | `/data/garmin_mcp.db` | SQLite: OAuth clients, users, hashed tokens. |
| `SESSION_STORAGE_PATH` | `/data/garmin_sessions` | One directory per user, holding garminconnect's `garmin_tokens.json`. |
| `GARMIN_MCP_HOST` | `0.0.0.0` | Listen address. |
| `GARMIN_MCP_PORT` | `$PORT`, else `8000` | An explicit value wins over the platform's `PORT`. |
| `GARMIN_MCP_PATH` | `/mcp` | MCP endpoint path. |
| `MCP_SCOPE` | `garmin` | OAuth scope the server requires. |
| `GARMIN_MCP_CALL_TIMEOUT` | `90` | Seconds before a stalled Garmin request is abandoned with a retry-able error; `0` disables the bound. |
| `GARMIN_REPORTS_PATH` | `garmin_mcp_reports.json` beside the sessions directory (`/data`) | Saved analytics reports, one file per user beside this path, so they survive redeploys with the sessions. |
| `LOG_LEVEL` | `INFO` | `DEBUG` shows why garminconnect could not load or refresh a session. |

### Signing in

When you add the connector, claude.ai opens the server's sign-in page:

1. Enter your Garmin Connect email and password. The email must be on
   `GARMIN_ALLOWED_EMAILS`.
2. If the account has two-factor authentication, a second page asks for the
   code Garmin sent. It stays valid for 5 minutes; a wrong code can be retried.
3. The page redirects back to claude.ai, and the tools are ready. The whole
   sign-in must finish within 15 minutes of opening the page.

claude.ai's access token lasts a day and is renewed with a refresh token,
which expires after 30 days unused; then you sign in again. Your Garmin session
on the server is separate and lasts until Garmin rejects it.

An email off the allowlist and a wrong password both show "Invalid email or
password."; the server log says which. If Garmin rate-limits the sign-in, use
token import.

### Token import / refresh

Garmin rate-limits (HTTP 429) its sign-in from datacenter IPs such as
Railway's, so the server often cannot complete a Garmin login even with the
right password and code; the same login works from a home connection. The
workaround is to sign in on a trusted machine and import the resulting token.
The server stores it without contacting Garmin.

Both import paths require `GARMIN_IMPORT_SECRET` in addition to the allowlist:
writing a session is a state-changing action, and an email is not a secret.

1. On a trusted machine on a residential connection, run `garmin-mcp-auth`
   (see [Authenticate once](#authenticate-once-garmin-mcp-auth)). It writes
   `~/.garminconnect/garmin_tokens.json` and a base64 copy,
   `~/.garminconnect_base64`. Either form can be imported.
2. Import it, either way:
   - **Login page:** on the sign-in page, expand "Advanced: import an existing
     Garmin token instead", paste the token, enter your allowlisted email and
     the import secret, and leave the password blank.
   - **Endpoint:** `POST /import-token` with header `X-Import-Secret` and a JSON
     body `{"email": "...", "token": "..."}`:

     ```bash
     curl -X POST "https://<your-domain>/import-token" \
       -H "X-Import-Secret: $GARMIN_IMPORT_SECRET" \
       -H "Content-Type: application/json" \
       -d "{\"email\": \"user@example.com\", \"token\": \"$(cat ~/.garminconnect_base64)\"}"
     ```

     It answers `{"status": "ok", ...}`, or 401 (wrong secret), 403 (email
     not allowlisted), 400 (malformed token) or 429 (too many attempts).

The email's case does not matter: the server keys each user on the email
ignoring case, as the allowlist does.

**Refreshing.** When the Garmin session on the server stops working, tool calls
answer "Garmin session expired or not available. Please re-authenticate."
Import a fresh token the same way. It replaces that user's stored session, and
the claude.ai connection keeps working without reconnecting. If the volume is
lost, the OAuth tokens go with it: reconnect the connector and import on the
sign-in page (OPERATIONS.md covers backups and recovery).

### Security

**Access control**

- **Email allowlist, fail-closed:** with `GARMIN_ALLOWED_EMAILS` unset or empty,
  every login is rejected. It is checked before the server contacts Garmin.
- **Token import is secret-gated, fail-closed,** on both paths, with a
  constant-time comparison. Imported tokens are checked for structure only,
  never against Garmin, so the endpoint cannot be used to test whether a token
  works and makes the server send no Garmin login traffic.
- **One generic sign-in error** for "not allowlisted" and "wrong password", so
  allowlist membership cannot be inferred from the response.
- **OAuth 2.0 with PKCE;** authorization codes are single-use and refresh tokens
  rotate on every use.

**Data at rest**

- Garmin passwords are **never stored**, only the session tokens Garmin issues.
  Per-user token directories are **owner-only** (`0700` / `0600`), applied after
  every write and swept again at startup.
- OAuth tokens are stored as **SHA-256 hashes**, so a copy of the database
  yields no usable bearer token. Pending 2FA state lives in memory, for 5
  minutes.
- Per-user state (Garmin sessions, saved analytics reports) is scoped by user
  id; one user cannot read or overwrite another's.

**What the tools can do**

- **Three tools are stdio-only.** `check_garmin_auth` and `login_to_garmin`,
  because on a network server `login_to_garmin` would store tokens for any
  Garmin account, around the allowlist. `delete_activity`, because it is
  irreversible and an activity is the training history Garmin Coach grades and
  every load and analytics figure is computed from; `delete_workout` and
  `delete_course` stay, since a workout or course can be recreated. The server
  refuses to start if an excluded name stops matching a registered tool.
- **Remote tools never read or write the server's filesystem** (see
  [Files and paths](#files-and-paths)).
- **Every tool carries effect annotations** (`readOnlyHint`, `destructiveHint`,
  `idempotentHint`, `openWorldHint`), so a client can tell a delete from a read
  and ask before running one.

**Hardening**

- **Rate limits:** sign-in 8 attempts per 5 minutes per email and client IP;
  2FA 8 per 5 minutes per pending sign-in; token import 10 per 5 minutes per
  IP. The client IP is the last `X-Forwarded-For` hop, which the client cannot
  choose.
- **Security headers** on every response: HSTS, `nosniff`,
  `X-Frame-Options: DENY`, a strict CSP (`default-src 'none'`),
  `Referrer-Policy: no-referrer`. Values reflected on the sign-in pages are
  HTML-escaped, and untrusted values are escaped before they are logged.
- **Stateless transport** (no `Mcp-Session-Id`, nothing kept per session) and a
  **non-root process**: the container takes ownership of `/data`, then drops to
  an unprivileged user (`scripts/docker-entrypoint.sh`).

### Running the remote server elsewhere

With Docker Compose, `docker-compose.remote.yml` builds `Dockerfile.remote` and
keeps `/data` in a named volume. It passes `GARMIN_ALLOWED_EMAILS` and
`GARMIN_IMPORT_SECRET` through from the shell or a `.env` file; without the
allowlist every login is rejected.

```bash
export GARMIN_MCP_SERVER_URL=https://garmin-mcp.example.com
export GARMIN_ALLOWED_EMAILS=user@example.com
docker compose -f docker-compose.remote.yml up -d
```

Locally, for development (the default `/data` paths usually do not exist on a
workstation):

```bash
GARMIN_MCP_SERVER_URL=http://localhost:8000 \
GARMIN_ALLOWED_EMAILS=user@example.com \
DB_PATH=./data/garmin_mcp.db SESSION_STORAGE_PATH=./data/garmin_sessions \
uv run garmin-mcp-remote

npx @modelcontextprotocol/inspector http://localhost:8000/mcp
```

## Local fallback (stdio)

The stdio server (`garmin-mcp`) runs on your own machine as one Garmin account,
with tokens you create once with `garmin-mcp-auth`. Use it when:

- the Railway service is down or being redeployed;
- Garmin refuses sign-ins from Railway's datacenter IP and you need data now;
- you are developing or testing the server;
- you need what the remote server does not offer: `delete_activity`, saving
  files to your own disk, or a Garmin Connect China account
  ([docs/garmin-cn.md](docs/garmin-cn.md)).

It needs Python 3.12+ and [uv](https://docs.astral.sh/uv/) (`uvx` comes with it).

### Authenticate once: garmin-mcp-auth

The server never prompts for credentials, because stdin carries the MCP
protocol. Sign in once in a terminal instead:

```bash
uvx --python 3.12 --from git+https://github.com/tagsvc/garmin_mcp garmin-mcp-auth
```

It asks for your email, password and, if the account has 2FA, the code, then
saves tokens to `~/.garminconnect/garmin_tokens.json` (owner-only) and a base64
copy to `~/.garminconnect_base64` for other garminconnect-based tools (and for
[token import](#token-import--refresh)); the server reads only the first.
Tokens last roughly six months.

| Option | Effect |
|---|---|
| `--verify` | Check the saved tokens without signing in again. |
| `--diagnose` | Show where tokens are looked up, what is on disk, and whether they work. |
| `--force-reauth` | Sign in again even if valid tokens exist (e.g. after they expire). |
| `--token-path DIR` | Use another token directory; without it, `$GARMINTOKENS`, else `~/.garminconnect`. |
| `--is-cn` | Garmin Connect China ([docs/garmin-cn.md](docs/garmin-cn.md)). |

Credentials can come from the environment instead of prompts:
`GARMIN_EMAIL` / `GARMIN_PASSWORD`, or `GARMIN_EMAIL_FILE` /
`GARMIN_PASSWORD_FILE` naming files that hold them (one of each pair, never
both).

From a clone, `uv run garmin-mcp-auth` does the same. To put the three commands
on your `PATH` from a local checkout:
`uv tool install --python 3.12 --force /path/to/garmin_mcp`.

### Claude Desktop: the `.dxt` extension

1. Download [`garmin-mcp.dxt`](https://github.com/tagsvc/garmin_mcp/raw/main/garmin-mcp.dxt)
   from this repository's root. It runs this fork's server through `uvx`;
   upstream's `.dxt` runs upstream's.
2. Drag it into Claude Desktop, double-click it, or use **Settings → Extensions
   → Install Extension**.
3. Its settings (token directory, email, password) are optional. Leave email
   and password blank once you have run `garmin-mcp-auth`.

To rebuild it from a clone: `uv run bash scripts/build_dxt.sh` (writes
`garmin-mcp.dxt` in the repo root; `uv run` provides the project's Python,
which the build imports).

### Claude Desktop: JSON config

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
or `%APPDATA%\Claude\claude_desktop_config.json` (Windows), with no
credentials in it:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uvx",
      "args": ["--python", "3.12", "--from", "git+https://github.com/tagsvc/garmin_mcp", "garmin-mcp"]
    }
  }
}
```

From a local clone, use `"command": "uv"` with
`"args": ["--directory", "/full/path/to/garmin_mcp", "run", "garmin-mcp"]`.
Restart Claude Desktop after saving.

Without saved tokens, the server can sign in with `GARMIN_EMAIL` and
`GARMIN_PASSWORD` in the config's `env`, but only for an account without 2FA;
`garmin-mcp-auth` keeps credentials out of the config. The stdio-only tools
`check_garmin_auth` and `login_to_garmin` can also sign in from the chat,
without a restart: call `login_to_garmin` with email and password, then again
with `otp_code` if it answers `mfa_required`. On macOS, when the email or
password is neither passed nor in the environment, it asks in a local dialog
rather than through the chat (`GARMIN_DISABLE_LOCAL_PROMPTS=1` turns that off).

### Other clients

- **Claude Code, Codex and other stdio clients** run the same command:
  `uvx --python 3.12 --from git+https://github.com/tagsvc/garmin_mcp garmin-mcp`.
  For Codex that is `command = "uvx"` and the same `args` under
  `[mcp_servers.garmin]` in `~/.codex/config.toml`.
- **opencode** picks up the repo's `opencode.json` when launched from a clone,
  and runs `uv run garmin-mcp` against the working tree.
- **MCP Inspector:** `npx @modelcontextprotocol/inspector uv run garmin-mcp`
  from a clone.

### Docker

`docker-compose.yml` runs the stdio server with tokens in a named volume. Put
`GARMIN_EMAIL` and `GARMIN_PASSWORD` in a `.env` file, then:

```bash
docker compose run --rm --entrypoint garmin-mcp-auth garmin-mcp   # once, if the account has 2FA
docker compose up -d
```

The compose file also shows, commented out, file-based secrets
(`GARMIN_EMAIL_FILE` / `GARMIN_PASSWORD_FILE` with Docker secrets) and the HTTP
transport. For Claude Desktop the `uvx` setup is simpler, since stdio servers
expect to be started as a local process.

### Multiple Garmin accounts

One server process serves one account. For several, run one server per account,
each with its own token directory set by `GARMINTOKENS` (default
`~/.garminconnect`):

```bash
garmin-mcp-auth --token-path ~/.garminconnect-alice
garmin-mcp-auth --token-path ~/.garminconnect-bob
```

```json
"garmin-alice": {
  "command": "uvx",
  "args": ["--python", "3.12", "--from", "git+https://github.com/tagsvc/garmin_mcp", "garmin-mcp"],
  "env": { "GARMINTOKENS": "${HOME}/.garminconnect-alice" }
}
```

and the same for `garmin-bob`, under `mcpServers`.

- Each server logs the token directory it uses at startup. If that directory
  has no working tokens, tool calls fail: the server never falls back to the
  default store and another account's session.
- `${HOME}` is expanded even when a client passes it through unresolved, and
  `~` works on Windows (via `USERPROFILE`).
- Write tools write to whichever account the server is bound to; pair
  `GARMINTOKENS` with `GARMIN_ENABLED_TOOLS` to keep a secondary account
  read-only.

### Serving the local server over HTTP

`garmin-mcp` can serve one account over HTTP instead of stdio, e.g. in a
container. This is not the multi-user remote server.

| Variable | Default | Notes |
|---|---|---|
| `GARMIN_MCP_TRANSPORT` | `stdio` | `stdio`, `streamable-http` or `sse`. Clients connect to `/mcp`. |
| `GARMIN_MCP_HOST` | `127.0.0.1` | Bind beyond loopback only with `GARMIN_MCP_HTTP_TOKEN` set or behind an authenticating proxy; otherwise the server warns that the account is exposed. |
| `GARMIN_MCP_PORT` | `8000` | |
| `GARMIN_MCP_HTTP_TOKEN` | *(none)* | When set, every request except the probes needs `Authorization: Bearer <token>` (constant-time compare); anything else gets 401. |
| `GARMIN_MCP_STATELESS_HTTP` | `false` | `true` serves `streamable-http` without sessions, so the first call after 30 idle minutes does not fail with "Session not found". No tool depends on session state. |
| `GARMIN_MCP_CALL_TIMEOUT` | `90` | As for the remote server. |

```bash
export GARMIN_MCP_HTTP_TOKEN="$(openssl rand -hex 32)"
GARMIN_MCP_TRANSPORT=streamable-http GARMIN_MCP_HOST=0.0.0.0 garmin-mcp
```

Probes: `GET /healthz` returns `ok` once the server is up; `GET /readyz`
returns 200 `{"status": "ready"}` after the Garmin login succeeds, else 503
with `pending` or `failed`.

### Local troubleshooting

- **"MFA required; run 'garmin-mcp-auth' before starting the server."** Run
  `garmin-mcp-auth`, then restart the client.
- **Tokens expired or rejected:** `garmin-mcp-auth --force-reauth`.
- **`garmin-mcp-auth` worked but the server still cannot log in.** The server
  loads only `garmin_tokens.json` from `$GARMINTOKENS` (default
  `~/.garminconnect`), and its startup error says what it found there. The
  usual cause is the client starting the server with a different `GARMINTOKENS`
  or `HOME` than your shell (common with Claude Desktop on Windows). Compare with
  `garmin-mcp-auth --diagnose`.
- **"Failed to spawn process: No such file or directory":** Claude Desktop
  cannot find `uvx` on its `PATH`. Put the full path from `which uvx` in
  `"command"`.
- **Windows Smart App Control** can block `uv.exe` / `uvx.exe` or an unsigned
  `garmin-mcp.exe` (CodeIntegrity event 3077 in Event Viewer; Defender history
  may show nothing). Reinstall uv with `winget install --id=astral-sh.uv -e` or
  `scoop install main/uv`, use the full path to `uvx.exe`, ask for a policy
  exception, or use Docker; turning Smart App Control off is a last resort.
- **Claude Desktop logs:** `~/Library/Logs/Claude/mcp-server-garmin.log`
  (macOS), `%APPDATA%\Claude\logs\mcp-server-garmin.log` (Windows).

## Conventions

### Units

Garmin's API returns meters, meters per second and grams or kilograms whatever
the account's unit setting. Every metric field stays as Garmin sent it, and the
same value in the account's units is added right after it:

| Account (`get_unit_system`) | Added beside the metric field |
|---|---|
| `statute_us` | miles, mph, min/mile pace (m:ss), feet, pounds, inches, °F: `distance_miles`, `avg_speed_mph`, `avg_pace_per_mile`, `elevation_gain_feet`, `weight_lb`, `avg_stride_length_in`, `avg_temp_f` |
| `statute_uk` | miles, mph, pace per mile, feet and inches; weight stays in kg and temperature in °C |
| `metric` | `distance_km`, `avg_speed_kmh`, `avg_pace_per_km`; elevation, weight and temperature are already metric |

- Garmin's own payloads get camelCase companions (`distanceMiles`,
  `averagePacePerMile`, `elevationGainFeet`, `averageTemperatureFahrenheit`,
  `strideLengthInches`, `weightLb` beside Garmin's bare grams).
- A temperature difference (a range, a deviation) converts by ×1.8 without +32;
  skin temperature is a deviation from your baseline and is named so
  (`skin_temperature_deviation_celsius` / `_fahrenheit`).
- Decimal hours also come as h:mm text (`sleep_hm: 6:22` beside
  `sleep_hours: 6.37`). Swims keep meters, because Garmin shows pool units and
  pace per 100.
- Hydration follows the hydration unit in your Garmin profile, a separate
  setting: a US account logging water in milliliters gets no fluid ounces.
  Activity weather is the one endpoint Garmin localises itself;
  `get_activity_weather` labels its units.
- Workout pace targets accept text such as `10:30/mi` or `5:00/km`
  ([docs/workouts.md](docs/workouts.md)).

The unit system is the measurement setting in your Garmin Connect profile
(`measurementSystem`), read when the server signs in to Garmin. After you change
it, the remote server picks it up within the hour (when it next restores your
session) or on a redeploy; a stdio server on restart.

### Times

Every time a curated tool returns names its zone: `*_gmt` is GMT, as ISO 8601
ending in `Z`, and `*_local` is the same moment in local time with its UTC
offset (`start_time_gmt: 2026-10-27T18:37:26Z`,
`start_time_local: 2026-10-27T14:37:26-04:00`).

Local time comes from the record's own offset whenever Garmin sends one, which
is where the watch was: a run while travelling shows that place's time, as in
the Garmin app. Records without an offset (laps, weather reports, device sync
times, 15-minute step buckets) use the time zone in your Garmin Connect profile,
and the tool names it in `local_time_zone`.

Tools that return Garmin's own payload keep its keys, give its time text its
zone in place (`latestSpo2ReadingTimeGmt: 2026-10-28T09:59:00Z`,
`latestSpo2ReadingTimeLocal: 2026-10-28T05:59:00-04:00`), add local siblings
where Garmin sends a GMT time alone (`eventStartTimeLocal`, `sleepStartLocal`),
and add `utcOffsetHours` for reading their `[UTC epoch ms, value]` series. On a
day the clocks change, day-level payloads add `utcOffsetHoursAfterChange` and
`clockChangeGmt`.

Date defaults ("today") use the profile's time zone, not the server's clock,
which is UTC on Railway.

## Tool notes

### Files and paths

`download_activity_file(activity_id, format="fit", output_dir=None)` saves an
activity as `fit` (default), `gpx`, `tcx` or `csv`, named `{activity_id}.{ext}`.
Over stdio the directory is the first of: the `output_dir` argument (one call
only); `GARMIN_FIT_DOWNLOAD_DIR`; the directory saved by
`set_fit_download_dir(path)` in `~/.garminconnect_fit_config.json` (or the file
named by `GARMIN_FIT_CONFIG`). With none set, it returns
`status: "needs_setup"`; the assistant asks where to save, calls
`set_fit_download_dir`, and retries.

On the remote server a path would name the server's disk, so
`download_activity_file` and `download_course_gpx` return the file inline (as
base64 and GPX text), `set_fit_download_dir` is refused, and `upload_course`
takes the GPX as `gpx_base64`, not `gpx_path`.

### Strength exercise sets

`set_activity_exercise_sets(activity_id, sets)` writes sets onto a recorded
strength activity, the grid Garmin Connect's "Edit sets" screen saves, so they
appear in Garmin's exercise history and volume charts. It **replaces every
existing set**, including watch-detected ones; read them first with
`get_activity_exercise_sets` to keep any.

Each set takes a `category` and optional `exercise_name` from Garmin's
[`Exercises.json`](https://connect.garmin.com/web-data/exercises/Exercises.json),
plus optional `reps`, `weight_kg` or `weight_lb` (omit for bodyweight),
`duration_seconds` and `rest_seconds`; Garmin rejects unknown keys and leaves
the activity unchanged. If the watch recorded the right sets but the wrong
exercises, pass `keep_timing: true`: start times, durations and rests are kept
and only exercise, reps and weight are replaced, in order, so the number of sets
must match the activity's recorded active sets.

```json
{"activity_id": 12345678901, "sets": [
  {"category": "DEADLIFT", "exercise_name": "BARBELL_DEADLIFT", "reps": 5, "weight_kg": 150, "rest_seconds": 180},
  {"category": "PULL_UP", "reps": 8}
]}
```

### Other tool behaviour

- **`get_gear`** returns each item's free-text Notes from Garmin Connect as
  `notes` (`null` when empty).
- **`get_training_effect`** falls back to the activity list search when Garmin's
  activity-details endpoint answers 403 for a visible activity. For an activity
  older than that search window, find it with `get_activities` or
  `get_activities_by_date` and use the training-effect fields in the list.
- **`get_goals`** reads the goal service the way Garmin Connect's Goals page
  does (a 1-based `start` and `Sec-Fetch-Site: same-origin`), which is what
  returns goals made in Connect's current Goals UI; garminconnect's own
  `get_goals()` is the fallback.

## Tool profiles and filtering

By default `garmin-mcp` registers ~181 tools, a lot of context for a model to
carry in every session. Three optional variables narrow that. They apply to
`garmin-mcp` (stdio and its HTTP transports) only; the remote server always
serves its full set.

| Variable | Effect |
|---|---|
| `GARMIN_TOOL_PROFILE` | A built-in subset: `minimal`, `health`, `activities`, `workouts`, `training`, `nutrition`, `devices`, or `all` (the default). Combine with commas, e.g. `health,training`. |
| `GARMIN_ENABLED_TOOLS` | Comma-separated allowlist: only these tools register. With a profile, they are **added** to it. |
| `GARMIN_DISABLED_TOOLS` | Comma-separated denylist. Ignored when an allowlist is set, unless a profile is also set; then it removes tools from the result. |

Names are case-insensitive. A name that matches no tool is ignored with a
warning on stderr; an unknown profile name stops the server at startup with the
list of known profiles.

| Profile | Tools | What it covers |
|---|---:|---|
| `minimal` | 22 | Recent activities with splits, daily stats, sleep, HRV, stress, body battery, training readiness and status |
| `health` | 69 | Every health_wellness tool, Health Status included; Health Snapshot and ECG; weight, body composition, blood pressure, hydration; women's health; fitness age, HRV and respiration trends; the historical analytics and saved health reports |
| `activities` | 45 | Activity list, detail, splits and edits; FIT analysis, power curve and file downloads; gear and its activities; courses |
| `workouts` | 33 | Workout library, upload, schedule, reschedule and delete; sending a workout to the watch; the Garmin Coach plan and its workouts; the high-level builders; the calendar |
| `training` | 42 | Training status, load, effect, VO2 max, FTP, lactate threshold, endurance and hill scores, readiness, recovery time, heart rate zones (read and write), power zones, personal records, race predictions, goals, badges |
| `nutrition` | 25 | Food log, custom foods, daily nutrition settings, hydration, energy balance |
| `devices` | 15 | Devices, their settings, alarms, solar data and last sync; paired sensors; the primary training device; sending a workout to the watch |

Every profile also includes `get_full_name`, `get_heart_rate_zones`,
`get_unit_system`, `get_user_profile` and `get_userprofile_settings`, so the
client can label the numbers it gets back, and the interactive auth tools
(`check_garmin_auth`, `login_to_garmin`), so a profile never stops the server
signing in again. Every tool is in at least one profile except
`inspect_garmin`, the raw diagnostic; add it with `GARMIN_ENABLED_TOOLS`. A test
fails when a new tool is in no profile.

For example, the health profile plus recent activities, without the health
write tools (in the client config's `env`; `"GARMIN_TOOL_PROFILE":
"training,workouts"` alone would suit a coaching session):

```json
"env": {
  "GARMIN_TOOL_PROFILE": "health",
  "GARMIN_ENABLED_TOOLS": "get_activities,get_activity",
  "GARMIN_DISABLED_TOOLS": "add_weigh_in,add_weigh_in_with_timestamps,delete_weigh_ins,add_body_composition,add_hydration_data,set_blood_pressure"
}
```

## Development

```bash
uv sync                                  # install, including dev dependencies
uv run pytest -m "not e2e"               # unit + integration tests, mocked Garmin
uv run pytest tests/e2e -m e2e           # end-to-end, needs real Garmin tokens
```

`pytest.ini` deselects e2e tests by default, so a plain `uv run pytest` is safe
on a machine with saved tokens; some e2e tests create and delete foods, food
logs and workouts in the real account.

- **CI:** every pull request runs `test (3.12)`, `test (3.13)`, `validate` and
  `test-installation`, and all four must pass before it can merge. `main` takes
  no direct pushes. `security.yml` adds a lock-file check and a dependency
  audit that report without blocking. Details in OPERATIONS.md.
- **Rules:** [CLAUDE.md](CLAUDE.md) holds the working rules (including the
  steps after any upstream merge), and [FORK.md](FORK.md) the invariants and the
  upstream-sync procedure. Documented tool counts are test-enforced
  (`tests/unit/test_documented_counts.py`); a new read-only tool goes in
  `tests/integration/test_units_times_sweep.py`'s table.
- **Field coverage:** after changing a tool module, run
  `uv run python scripts/field_coverage.py` on a machine with real Garmin
  tokens. It lists the fields Garmin sent that each read-only tool leaves out,
  by name only; keep the report out of the repo.
- **Comments and docs** say why, in the present tense; history goes in
  CHANGELOG.md.

## Credits

- **[Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp)**: the original
  Garmin MCP server and this fork's upstream, from which new tools and fixes are
  synced.
- **[Tomas2D/garmin_mcp](https://github.com/Tomas2D/garmin_mcp)**: the OAuth2
  remote server (RFC 9728, claude.ai compatible) this fork's remote mode is
  built on.
- **[coloboxp/garmin_mcp](https://github.com/coloboxp/garmin_mcp)**: the
  historical analytics and interactive auth tools.
- **[cyberjunky/python-garminconnect](https://github.com/cyberjunky/python-garminconnect)**:
  the library used to reach the Garmin Connect API.

This fork adds the email allowlist, secret-gated token import, a login client
that fails fast on Garmin's rate limit, the Railway deployment and the security
hardening; [CHANGELOG.md](CHANGELOG.md) lists the full divergence.
