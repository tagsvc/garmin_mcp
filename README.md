# Garmin MCP Server

This Model Context Protocol (MCP) server connects to Garmin Connect and exposes your fitness and health data to Claude and other MCP-compatible clients.

Garmin's API is accessed via the awesome [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) library.

## Features

- List recent activities with pagination support
- Get detailed activity information
- Edit activities: name, type, description/notes, event type, perceived effort (RPE), feel, and strength exercise sets
- Access health metrics (steps, heart rate, sleep, stress, respiration)
- View body composition data
- Track training status and readiness
- Access cycling FTP and lactate threshold metrics
- Manage gear and equipment, including free-text notes returned by `get_gear`
- Access workouts and training plans
- Inspect detailed workout step structures, including repeat groups and swim pace targets
- Weekly health aggregates (steps, stress, intensity minutes)
- Advanced cycling analytics: power zones, FIT file analysis, DI2 electronic shift intelligence
- Training load trend (CTL/ATL/TSB), HRV trend, VO2 max trend, respiration rate trend
- Power Duration Curve, climb detection with VAM, cardiac drift (aerobic decoupling), W/kg calculations
- Historical health analytics: personal rolling baselines, wellness anomaly detection, lagged metric correlations, weekly reviews, and custom multi-metric reports

### Tool Coverage

This MCP server implements **~174 tools** covering ~90% of the [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) library (v0.3.5):

- ✅ Activity Management (23 tools) - includes write tools for type, description, event type, perceived effort, feel, and exercise sets, plus a confirm-gated `delete_activity` (stdio only)
- ✅ Health & Wellness (35 tools) - includes custom lightweight summary tools, multi-day calorie/step totals, a composition-derived TDEE estimate, and recovery time remaining
- ✅ Training & Performance (18 tools) - includes CTL/ATL/TSB, HRV, VO2 max and respiration trends, heat/altitude acclimation, and running tolerance
- ✅ Workouts (18 tools) - Garmin Coach plan listing, detail (race goal, confidence, training week) and per-workout grades; moving a scheduled workout to another date
- ✅ Devices (8 tools) - includes paired-sensor battery levels and sending a workout to a device
- ✅ Gear Management (4 tools) - includes per-gear activity lists for mileage auditing
- ✅ Weight Tracking (5 tools)
- ✅ Challenges & Badges (9 tools)
- ✅ Nutrition (15 tools) - food logs, meals, custom foods, food logging, and multi-day intake summaries
- ✅ Data Management (3 tools) - bulk export helpers
- ✅ Women's Health (3 tools)
- ✅ Calendar (1 tool) - races and events from the Garmin calendar
- ✅ User Profile (6 tools) - includes per-sport heart rate zone reads and writes
- ✅ High-Level Workout Builders (6 tools) - create and schedule workouts without writing JSON, including repeat-based interval runs
- ✅ Courses (5 tools) - list / get details / upload GPX as course (base64 or local path) / download GPX / delete course
- ✅ Activity Analysis (3 tools) - FIT file parsing, paginated FIT messages, Power Duration Curve; requires power meter and/or Di2
- ✅ Historical Analytics (8 tools) - rolling baselines, wellness anomalies, lagged correlations, weekly review, and saved/custom multi-metric health reports
- ✅ Interactive Auth (2 tools, stdio mode) - `check_garmin_auth` / `login_to_garmin` to authenticate without restarting the client
- ✅ Activity File Downloads (2 tools) - download activity files in FIT, GPX, TCX, or CSV format

> **Note:** Activity Analysis tools require a compatible power meter (e.g., Garmin Rally, Favero Assioma, PowerTap P1) and/or Shimano Di2 / SRAM eTap electronic shifting. The `fitparse` dependency is installed automatically.

### Gear Notes

Each item in the `gear` array returned by `get_gear` includes a `notes` field
containing the free-text Notes value shown in Garmin Connect. Gear without a
Notes value returns `null`; all existing gear fields remain unchanged.

### Activity File Downloads

Two tools let you download a raw activity file to disk:

- **`download_activity_file(activity_id, format="fit", output_dir=None)`** — downloads the activity and saves it to the configured directory. `format` accepts `fit` (default), `gpx`, `tcx`, or `csv`.
- **`set_fit_download_dir(path)`** — sets and persists the default download directory (written to the config file).

**Where files are saved (precedence):**

1. `output_dir` argument — one-off override, not persisted.
2. `GARMIN_FIT_DOWNLOAD_DIR` environment variable.
3. Persisted config set via `set_fit_download_dir`.

**Remote mode:** writing to a path is disabled. On a hosted server a path names
the *server's* filesystem, not yours, so `download_activity_file` returns the
file inline as base64 (omit `output_dir`), and `set_fit_download_dir` is
refused. The same applies to `download_course_gpx`, which returns the GPX inline.

**First-run behavior (stdio only):** if no directory is configured, `download_activity_file` returns `status: "needs_setup"`. The assistant will ask where you want to save files (suggesting the current directory as default), call `set_fit_download_dir` to persist your choice, and then retry the download automatically.

### Strength Exercise Sets

`set_activity_exercise_sets(activity_id, sets)` writes structured sets onto a recorded
strength activity, the same per-set grid the Garmin Connect "Edit sets" screen saves, so
they appear in Garmin's exercise history and volume charts. It **replaces every existing
set**, including watch-detected ones; read them first with `get_activity_exercise_sets`
if you want to keep any.

Each set takes a `category` and optional `exercise_name` using Garmin's keys from
[`Exercises.json`](https://connect.garmin.com/web-data/exercises/Exercises.json), plus
optional `reps`, `weight_kg` or `weight_lb` (omit for bodyweight), `duration_seconds` and
`rest_seconds`. Garmin rejects unknown keys and leaves the activity unchanged.

If the watch recorded the right sets but misidentified the exercises, pass
`keep_timing: true`: the recorded start times, durations and rest periods are kept and only
the exercise, reps and weight are replaced, in order. The number of sets must match the
activity's recorded active sets.

```json
{
  "activity_id": 12345678901,
  "sets": [
    {"category": "DEADLIFT", "exercise_name": "BARBELL_DEADLIFT", "reps": 5, "weight_kg": 150, "rest_seconds": 180},
    {"category": "PULL_UP", "reps": 8}
  ]
}
```

### Intentionally Skipped Endpoints

Some endpoints are not implemented due to performance or complexity considerations:

**High Data Volume:**
- `get_activity_details()` - Returns Garmin's downsampled chart feed (50KB-500KB, capped at ~2000 points). Use `get_activity()` for summaries, or `get_activity_fit_data()` for the real FIT file at full per-second resolution — strictly higher fidelity than this endpoint.

**Specialized Workout Formats:**
- `upload_running_workout()`, `upload_cycling_workout()`, `upload_swimming_workout()` - Sport-specific workout uploads. Use `upload_workout()` for general workouts.

**Maintenance & Destructive Operations:**
- `delete_blood_pressure()` - Destructive operations require careful consideration.
- `delete_activity()` is implemented with a confirmation gate: call once to
  preview, then again with `confirm=true` to delete. Deletions are logged to
  stderr. There is no undo.
- Internal/Auth methods: `login()`, `resume_login()`, `connectapi()`, `download()` - Handled automatically by the library.

If you need any of these endpoints, please [open an issue](https://github.com/tagsvc/garmin_mcp/issues).

## Deployment Modes

This server runs in one of two modes:

- **stdio (local, single-user)** — runs on your machine for Claude Desktop / CLI clients. You authenticate once with `garmin-mcp-auth`. See [Setup](#setup).
- **Remote (multi-user, HTTP + OAuth2)** — a hosted server that clients like **claude.ai / Claude iOS** connect to over HTTPS, with per-user Garmin sessions, an email allowlist, and 2FA. See [Remote Mode](#remote-mode-multi-user-http--oauth2).

### Quickstart: deploy remote on Railway

The repo ships a `railway.json` pinned to `Dockerfile.remote`, so Railway deploys the HTTP server straight from git.

1. Create a Railway service from this repo (branch `main`); `railway.json` selects the Dockerfile automatically.
2. **Settings → Networking → Generate Domain** (the server binds to Railway's injected `PORT`).
3. Add a **volume mounted at `/data`** (persists the SQLite DB + Garmin session tokens across redeploys).
4. Set the required env vars — at minimum `GARMIN_MCP_SERVER_URL` (your generated domain) and `GARMIN_ALLOWED_EMAILS`; set `GARMIN_IMPORT_SECRET` to enable token import. Full table in [Configuration](#configuration-environment-variables).
5. Point your client at `https://<your-domain>/mcp`.

> Garmin rate-limits its OAuth token-mint endpoint from datacenter IPs, so the first browser login may be 429'd. See [Token import / refresh](#token-import--refresh-datacenter-ip-429-workaround) for the residential-IP workaround.

## Tool Filtering

This server registers ~174 tools by default, which can be a lot of context for
an LLM to carry in every session. You can expose only the tools you need with
three optional environment variables:

| Env var | Effect |
|---|---|
| `GARMIN_TOOL_PROFILE` | A built-in subset by area: `minimal`, `health`, `activities`, `workouts`, `training`, `nutrition`, or `all` (the default). Combine several with commas, e.g. `health,training`. |
| `GARMIN_ENABLED_TOOLS` | Comma-separated **allowlist** — if set, *only* these tools are registered. With a profile, these are **added** to it instead. |
| `GARMIN_DISABLED_TOOLS` | Comma-separated **denylist** — listed tools are skipped. Ignored if an allowlist is set, unless a profile is also set; then it removes tools from the profile. |

Tool names are case-insensitive. With none of them set, all tools register
(unchanged default behaviour). Names that match no tool are ignored with a
warning on stderr, which makes typos easy to spot. An unknown profile name
stops the server at startup with the list of known profiles.

### Profiles

| Profile | Tools | What it covers |
|---|---:|---|
| `minimal` | 20 | Recent activities with splits, daily stats, sleep, HRV, stress, body battery, training readiness and status |
| `health` | 53 | Every health_wellness tool plus weight, body composition, blood pressure, hydration, women's health, fitness age, HRV and respiration trends |
| `activities` | 41 | Activity list, detail, splits and edits; FIT analysis and power curve; gear; courses |
| `workouts` | 26 | Workout library, upload, schedule and delete; the high-level builders; the calendar |
| `training` | 38 | Training status, load, effect, VO2max, FTP, lactate threshold, endurance and hill scores, readiness, recovery time, personal records, race predictions, goals, badges |
| `nutrition` | 23 | Food log, custom foods, daily nutrition settings, hydration, energy balance |

Every profile also includes the identity and unit tools (`get_full_name`, `get_heart_rate_zones`, `get_unit_system`, `get_user_profile`, `get_userprofile_settings`), so the
client can label the numbers it gets back.

Example — a coaching session that needs training metrics and the workout tools:

```json
"env": {
  "GARMIN_TOOL_PROFILE": "training,workouts"
}
```

Example — the health profile plus recent activities, without the write tools:

```json
"env": {
  "GARMIN_TOOL_PROFILE": "health",
  "GARMIN_ENABLED_TOOLS": "get_activities,get_activity",
  "GARMIN_DISABLED_TOOLS": "add_weigh_in,add_weigh_in_with_timestamps,delete_weigh_ins,add_body_composition,add_hydration_data,set_blood_pressure"
}
```

Example — hand-pick tools without a profile:

```json
"env": {
  "GARMIN_ENABLED_TOOLS": "get_sleep_data,get_stress_summary,get_activities"
}
```

## High-level workout tools

These builder tools let an LLM create and schedule workouts without writing raw Garmin JSON.

### `create_walk_run_workout`

Creates a walk/run interval workout with optional heart-rate zone target.

```json
{
  "name": "W3 Mié 2:2",
  "run_seconds": 120,
  "walk_seconds": 120,
  "repeats": 9,
  "warmup_min": 10,
  "cooldown_min": 8,
  "hr_zone": "Z3"
}
```

Returns: `{"status": "success", "workout_id": 1234567890, ...}`

### `create_z2_walk_workout`

Creates a steady Z2 walking workout.

```json
{
  "name": "Z2 Walk 45m",
  "duration_min": 45,
  "hr_min": 110,
  "hr_max": 130
}
```

Returns: `{"status": "success", "workout_id": 1234567890, ...}`

### `create_strength_workout`

Creates a strength workout from a list of exercises. Each becomes a reps-based step, with the
name kept in the step description. The name is also sent as `exerciseName`, but Garmin only
retains that when it matches one of its own exercise keys (e.g. `FARMERS_CARRY`) — any other
value is accepted and then stored empty.

`category` is optional and passed straight through. Omit it and the key is left out of the
payload entirely, which Garmin accepts. Supply it and it must be one of Garmin's exercise
categories — anything else, including `OTHER` and `UNASSIGNED`, is rejected with
`400 - Invalid category`. The full list is published at
[`Exercises.json`](https://connect.garmin.com/web-data/exercises/Exercises.json).

```json
{
  "name": "Full Body A",
  "exercises": [
    {"name": "Sentadillas", "sets": 3, "reps": 12, "rest_seconds": 90},
    {"name": "Flexiones",   "sets": 3, "reps": 15, "rest_seconds": 60},
    {"name": "Peso muerto", "sets": 3, "reps": 10, "rest_seconds": 90},
    {"name": "Farmers Carry 40m", "sets": 3, "reps": 1, "rest_seconds": 90, "category": "CARRY"}
  ]
}
```

Returns: `{"status": "success", "workout_id": 1234567890, ...}`

### `schedule_week`

Schedules multiple workouts in one call.

```json
{
  "week": [
    {"date": "2026-05-12", "workout_id": 1234567890},
    {"date": "2026-05-14", "workout_id": 1234567891}
  ]
}
```

Returns: `{"status": "complete", "scheduled": [...]}`

### Full flow example

```text
create_walk_run_workout(name="W3 Mié 2:2", run_seconds=120, walk_seconds=120,
                        repeats=9, warmup_min=10, cooldown_min=8)
  → workout_id = 1560092011

schedule_workout(workout_id=1560092011, date="2026-05-06")
  → OK
```

After syncing your watch, the workout appears on the Forerunner 965 calendar.

### Raw `upload_workout` end conditions

When building custom workout JSON for `upload_workout` or `upload_workouts`, the
`endCondition.conditionTypeId` and `endCondition.conditionTypeKey` must match
Garmin's canonical mapping. Garmin treats the numeric `conditionTypeId` as the
source of truth; if the key and ID conflict, Garmin stores the condition that
matches the ID.

For example, this is invalid for a heart-rate end condition because ID `4` is
`calories`, not `heart.rate`:

```json
{
  "endCondition": {
    "conditionTypeId": 4,
    "conditionTypeKey": "heart.rate"
  },
  "endConditionValue": 145
}
```

Use ID `6` for heart rate:

```json
{
  "endCondition": {
    "conditionTypeId": 6,
    "conditionTypeKey": "heart.rate"
  },
  "endConditionValue": 145
}
```

For a zone-based heart-rate end condition, use `endConditionZone` (1-5) on the
same `endCondition` object and omit `endConditionValue`. If both are sent,
Garmin keeps the zone and drops the value (verified against the production API
on 2026-09-01):

```json
{
  "endCondition": {
    "conditionTypeId": 6,
    "conditionTypeKey": "heart.rate",
    "endConditionZone": 2
  }
}
```


Common end-condition IDs:

| ID | Key |
|---:|---|
| 1 | `lap.button` |
| 2 | `time` |
| 3 | `distance` |
| 4 | `calories` |
| 5 | `power` |
| 6 | `heart.rate` |
| 7 | `iterations` |
| 8 | `fixed.rest` |
| 9 | `fixed.repetition` |
| 10 | `reps` |
| 11 | `training.peaks.tss` |

### Raw `upload_workout` target types

When building raw Garmin workout JSON, `targetType.workoutTargetTypeId` and
`targetType.workoutTargetTypeKey` must use Garmin's canonical mapping. Garmin
treats the numeric ID as authoritative: a mismatched payload such as
`{"workoutTargetTypeId": 6, "workoutTargetTypeKey": "heart.rate"}` is stored as
`pace.zone`, because ID `6` means `pace.zone`.

For a custom heart-rate range, use target type ID `4` with `heart.rate.zone` and
put the bpm range in `targetValueOne` / `targetValueTwo`. These value fields
belong on the workout step, alongside `targetType`; do not nest them inside the
`targetType` object:

```json
{
  "targetType": {
    "workoutTargetTypeId": 4,
    "workoutTargetTypeKey": "heart.rate.zone"
  },
  "targetValueOne": 143,
  "targetValueTwo": 157
}
```

The same shape applies to a custom running pace range. Pace bounds use meters
per second:

```json
{
  "targetType": {
    "workoutTargetTypeId": 6,
    "workoutTargetTypeKey": "pace.zone"
  },
  "targetValueOne": 1.9607843,
  "targetValueTwo": 2.0833333
}
```

That example represents `8:00–8:30 min/km`. The lower numeric bound is listed
first for consistency with the heart-rate example; Garmin normalizes either
bound order. Garmin silently discards values nested inside `targetType`, leaving
a pace target with no active range. The upload tools repair that unambiguous
nesting mistake, but reject the request if nested and step-level values conflict.

For a named Garmin HR zone, use the same target type with `zoneNumber` instead:

```json
{
  "targetType": {
    "workoutTargetTypeId": 4,
    "workoutTargetTypeKey": "heart.rate.zone"
  },
  "zoneNumber": 3
}
```

Use either `zoneNumber` or `targetValueOne` / `targetValueTwo` on a target, not
both. Garmin treats the named zone as authoritative and silently discards a
coexisting custom range, so the upload tools reject that ambiguous shape.

## One-click Install (Claude Desktop)

The easiest way to add this server to Claude Desktop is via the `.dxt` Desktop Extension file — no JSON editing required.

### Download and install

1. Download the latest `garmin-mcp.dxt` from the upstream [Releases page](https://github.com/Taxuspt/garmin_mcp/releases) (this fork does not publish a `.dxt`).
2. Drag the `.dxt` file into the Claude Desktop window, **or** double-click it, **or** go to **Settings → Extensions → Install Extension** and select the file.
3. Claude Desktop will prompt you for optional configuration (token path, email, password).

### First-time authentication

The extension installs and runs the server automatically, but you must authenticate with Garmin once before data can be fetched:

```bash
uvx --python 3.12 --from git+https://github.com/tagsvc/garmin_mcp garmin-mcp-auth
```

This saves OAuth tokens to `~/.garminconnect`. After that the server works without any credentials in the config.

> **Note:** Tokens are valid for approximately 6 months. Re-run `garmin-mcp-auth` when they expire.

### Build the `.dxt` yourself

```bash
bash scripts/build_dxt.sh   # produces garmin-mcp.dxt in the repo root
```

---

## Setup

### Quick Start for MCP Clients

The easiest way to use this MCP server with Claude Desktop, [Codex](https://openai.com/codex/), or another MCP client is to authenticate once before adding the server to your configuration.

#### Prerequisites

- Python 3.12+
- Garmin Connect account
- MFA may be required if enabled on your account

#### Step 1: Pre-authenticate (One-time)

Before adding the server to your MCP client, authenticate once in your terminal:

```bash

# Install and run authentication tool
uvx --python 3.12 --from git+https://github.com/tagsvc/garmin_mcp garmin-mcp-auth

# You'll be prompted for:
# - Email (or set GARMIN_EMAIL env var)
# - Password (or set GARMIN_PASSWORD env var)
# - MFA code (if enabled on your account)

# OAuth tokens will be saved to ~/.garminconnect
```

You can verify your credentials at any time with
```bash
uv run garmin-mcp-auth --verify
```

**Note:** You can also set credentials via environment variables:
```bash
GARMIN_EMAIL=your@email.com GARMIN_PASSWORD=secret garmin-mcp-auth
```

If you don't have MFA enabled you can also skip `garmin-mcp-auth` and pass `GARMIN_EMAIL` and `GARMIN_PASSWORD` as env variables directly to your MCP client, if supported. For better security, prefer the pre-authentication flow above and keep credentials out of MCP client configuration.

#### Step 2: Configure Claude Desktop

Add to your Claude Desktop MCP settings **WITHOUT** credentials:

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uvx",
      "args": [
        "--python",
        "3.12",
        "--from",
        "git+https://github.com/tagsvc/garmin_mcp",
        "garmin-mcp"
      ]
    }
  }
}
```

**Important:** No `GARMIN_EMAIL` or `GARMIN_PASSWORD` needed in config! The server uses your saved tokens.

#### Step 3: Restart your MCP client

Your Garmin data is now available to your MCP client.

For Codex and other clients, see the examples below.

---

### Using more than one Garmin account

A single server process is bound to one Garmin account. To use several accounts
at once, run **one server instance per account**, each with its own token
directory, selected with the `GARMINTOKENS` environment variable.

`GARMINTOKENS` defaults to `~/.garminconnect`. Point it somewhere else and the
server reads and writes tokens there instead, leaving the default store
untouched.

#### Step 1: Authenticate each account into its own directory

```bash
garmin-mcp-auth --token-path ~/.garminconnect-alice
garmin-mcp-auth --token-path ~/.garminconnect-bob
```

`--token-path` also accepts `$GARMINTOKENS`, so `GARMINTOKENS=~/.garminconnect-alice garmin-mcp-auth`
is equivalent.

#### Step 2: Declare one server per account

```json
{
  "mcpServers": {
    "garmin-alice": {
      "command": "uvx",
      "args": ["--python", "3.12", "--from", "git+https://github.com/Taxuspt/garmin_mcp", "garmin-mcp"],
      "env": {
        "GARMINTOKENS": "${HOME}/.garminconnect-alice"
      }
    },
    "garmin-bob": {
      "command": "uvx",
      "args": ["--python", "3.12", "--from", "git+https://github.com/Taxuspt/garmin_mcp", "garmin-mcp"],
      "env": {
        "GARMINTOKENS": "${HOME}/.garminconnect-bob"
      }
    }
  }
}
```

Each server logs the directory it authenticates from on startup, so you can
confirm the wiring:

```
Trying to login to Garmin Connect using token data from directory '/home/you/.garminconnect-alice'...
```

#### Notes

- **No silent fallback.** If `GARMINTOKENS` points at a directory with no valid
  tokens and login fails, the server logs the failure and tool calls fail;
  HTTP `/readyz` remains unavailable (503). The server does not switch to the
  default token store, so a misconfigured second server cannot silently reuse
  the first account's session.
- **`${HOME}` is expanded** even when an MCP client passes it through
  unresolved, and `~` works on Windows via `USERPROFILE`.
- **Restrict write tools on secondary accounts.** Tools such as
  `upload_workout` and `schedule_workout` write to whichever account the server
  is bound to. Pair `GARMINTOKENS` with `GARMIN_ENABLED_TOOLS` (see
  [Tool Filtering](#tool-filtering)) to make an account read-only:

  ```json
  "env": {
    "GARMINTOKENS": "${HOME}/.garminconnect-bob",
    "GARMIN_ENABLED_TOOLS": "get_activities,get_activities_by_date,get_activity,get_activity_splits"
  }
  ```
- **Token directories hold long-lived credentials.** They are created with
  owner-only permissions; keep them out of shared or synced folders.

---

### Development Setup

1. Install the required packages on a new environment:

```bash
uv sync
```

## Running the Server

### Configuration

Your Garmin Connect credentials are read from environment variables:

- `GARMIN_EMAIL`: Your Garmin Connect email address
- `GARMIN_EMAIL_FILE`: Path to a file containing your Garmin Connect email address
- `GARMIN_PASSWORD`: Your Garmin Connect password
- `GARMIN_PASSWORD_FILE`: Path to a file containing your Garmin Connect password
- `GARMIN_IS_CN`: Set to `true` to use Garmin Connect China (garmin.cn) instead of the international version (default: `false`)
- `GARMIN_FIT_DOWNLOAD_DIR`: Default directory for downloaded activity files. When set, skips the first-run setup prompt in `download_activity_file`.
- `GARMIN_FIT_CONFIG`: Path to the persisted download-directory config file (default: `~/.garminconnect_fit_config.json`).

File-based secrets are useful in certain environments, such as inside a Docker container. Note that you cannot set both `GARMIN_EMAIL` and `GARMIN_EMAIL_FILE`, similarly you cannot set both `GARMIN_PASSWORD` and `GARMIN_PASSWORD_FILE`.

### Transport

By default the server communicates over **stdio**, which is what Claude Desktop, the MCP Inspector, and most local clients expect. To serve over **HTTP** instead (e.g. when running in a container or Kubernetes), set the transport via environment variables:

- `GARMIN_MCP_TRANSPORT`: `stdio` (default), `streamable-http`, or `sse`
- `GARMIN_MCP_HOST`: bind address for HTTP transports (default `127.0.0.1`; set to `0.0.0.0` only together with `GARMIN_MCP_HTTP_TOKEN` or behind an authenticating reverse proxy)
- `GARMIN_MCP_PORT`: bind port for HTTP transports (default `8000`)
- `GARMIN_MCP_HTTP_TOKEN`: shared secret for the HTTP transports. When set, every request to `/mcp`, `/sse` and `/messages/` must carry `Authorization: Bearer <token>`; anything else gets `401`. `/healthz` and `/readyz` stay open for probes. See [Protecting the HTTP endpoint](#protecting-the-http-endpoint).
- `GARMIN_MCP_STATELESS_HTTP`: set to `true` to run `streamable-http` without sessions (default `false`). The MCP SDK drops a stateful session after 30 minutes idle, so the first call after an idle gap gets `404 Session not found`; some clients (including claude.ai custom connectors) report that as a failed tool call rather than re-initializing and retrying. Stateless mode has no sessions to expire. No tool in this server depends on session state, so nothing is lost. Recommended for long-lived remote deployments.
- `GARMIN_MCP_CALL_TIMEOUT`: per-request timeout in seconds for calls to Garmin (default `90`). Garmin's API occasionally stalls a single request indefinitely; without this bound the call hangs until the MCP client's own timeout fires and reports the whole server as unresponsive. On timeout the tool returns a clear, retry-able error instead. Set to `0` to disable the bound.

```bash
GARMIN_MCP_TRANSPORT=streamable-http garmin-mcp
```

When an HTTP transport is selected:

- MCP clients connect to the **`/mcp`** path (e.g. `http://localhost:8000/mcp`).
- **`GET /healthz`** returns `ok` as soon as the server is up — use it as a **liveness** probe.
- **`GET /readyz`** reports whether Garmin login has finished — use it as a **readiness** probe. It returns `200 {"status": "ready"}` once login succeeded, and `503` with `{"status": "pending"}` while login is still running or `{"status": "failed"}` if it failed (re-run `garmin-mcp-auth` and restart).

#### Protecting the HTTP endpoint

The HTTP transports expose full read/write access to the Garmin account, so
the server binds to loopback by default. For anything reachable from other
machines, set a shared secret and have the client send it as a bearer token:

```bash
export GARMIN_MCP_HTTP_TOKEN="$(openssl rand -hex 32)"
GARMIN_MCP_TRANSPORT=streamable-http GARMIN_MCP_HOST=0.0.0.0 garmin-mcp
```

```bash
# rejected: 401 {"error": "unauthorized", ...}
curl -i http://localhost:8000/mcp
# accepted (the MCP handshake then proceeds as usual)
curl -i -H "Authorization: Bearer $GARMIN_MCP_HTTP_TOKEN" http://localhost:8000/mcp
```

Most MCP clients accept custom headers for remote servers (Claude Desktop and
claude.ai connectors, the MCP Inspector, and the SDK clients all do); put the
same `Authorization: Bearer …` header there. The comparison is constant-time.
`/healthz` and `/readyz` never require the token, since they only report that
the process is up and whether the Garmin login finished.

Without the token the server still starts, but if `GARMIN_MCP_HOST` is not a
loopback address it prints a warning on stderr that the account is exposed. A
reverse proxy with its own authentication (nginx, Traefik, Authelia, etc.)
remains a fine alternative; the token is the small, built-in option for the
common self-hosted case.

### Garmin Connect China (garmin.cn)

If you use Garmin Connect China (garmin.cn) instead of the international version, set the `GARMIN_IS_CN` environment variable to `true`:

```bash
# Pre-authenticate with Garmin Connect China
GARMIN_IS_CN=true garmin-mcp-auth

# Or use the CLI flag
garmin-mcp-auth --is-cn
```

For Claude Desktop, add `GARMIN_IS_CN` to the `env` section:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uvx",
      "args": [
        "--python",
        "3.12",
        "--from",
        "git+https://github.com/tagsvc/garmin_mcp",
        "garmin-mcp"
      ],
      "env": {
        "GARMIN_IS_CN": "true"
      }
    }
  }
}
```

For Docker, add `GARMIN_IS_CN=true` to your `.env` file or uncomment it in `docker-compose.yml`.

### Testing the server locally with MCP Inspector

The Inspector runs directly through npx without requiring installation. Run from the project root:

```bash
npx @modelcontextprotocol/inspector uv run garmin-mcp
```

You'll be able to inspect and test the tools.

### With Claude Desktop

1. Create a configuration in Claude Desktop:

Edit your Claude Desktop configuration file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

You have two options to run the MCP locally with Claude.

#### Directly from github without cloning the repo:

1. Add this server configuration:

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uvx",
      "args": [
        "--python",
        "3.12",
        "--from",
        "git+https://github.com/tagsvc/garmin_mcp",
        "garmin-mcp"
      ],
      "env": {
        "GARMIN_EMAIL": "YOUR_GARMIN_EMAIL",
        "GARMIN_PASSWORD": "YOUR_GARMIN_PASSWORD"
      }
    }
  }
}
```

You might have to add the full path to `uvx` you can check the full path with `which uvx`

2. Restart Claude Desktop

#### Directly from your local copy of the repository:

1. Add this server configuration:

```
{
  "mcpServers": {
    "garmin-local": {
      "command": "uv",
      "args": [
        "--directory",
        "<full path to your local repository>/garmin_mcp",
        "run",
        "garmin-mcp"
      ]
    }
  }
}
```

2. Restart Claude Desktop

### With Codex

Codex uses TOML for MCP server configuration. Add one of the following entries to `~/.codex/config.toml` after authenticating with `garmin-mcp-auth`.

You can also ask your MCP-capable client to set this up for you. For example:

```text
Install the Garmin MCP server from https://github.com/tagsvc/garmin_mcp, authenticate with garmin-mcp-auth, and add it to my MCP configuration without storing my Garmin email or password.
```

#### Directly from GitHub without cloning the repo

```toml
[mcp_servers.garmin]
command = "uvx"
args = [
  "--python",
  "3.12",
  "--from",
  "git+https://github.com/tagsvc/garmin_mcp",
  "garmin-mcp"
]
```

#### Directly from your local copy of the repository

```toml
[mcp_servers.garmin-local]
command = "uv"
args = [
  "--directory",
  "/full/path/to/garmin_mcp",
  "run",
  "garmin-mcp"
]
```

Restart your MCP client after saving the file.

### With opencode

[opencode](https://opencode.ai) auto-loads a project-level `opencode.json` when launched from a repository root, so contributors who clone this repo get the Garmin MCP wired up against the local source with no extra config.

#### From a clone of this repository (recommended for development)

This repo ships an [`opencode.json`](./opencode.json) that runs the MCP via `uv run garmin-mcp`, so it always tracks the working tree.

```bash
git clone https://github.com/Taxuspt/garmin_mcp.git
cd garmin_mcp
uv sync                # install dependencies
garmin-mcp-auth        # one-time Garmin login (skip if ~/.garminconnect already exists)
opencode               # launches with the garmin MCP attached
```

Verify the server is connected:

```bash
opencode mcp list
# ●  ✓ garmin   connected
#       uv run garmin-mcp
```

#### From any other directory (GitHub install)

Add the server to your global opencode config at `~/.config/opencode/opencode.json` after running `garmin-mcp-auth`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "garmin": {
      "type": "local",
      "command": [
        "uvx",
        "--python",
        "3.12",
        "--from",
        "git+https://github.com/Taxuspt/garmin_mcp",
        "garmin-mcp"
      ],
      "enabled": true,
      "timeout": 30000
    }
  }
}
```

Restart opencode after saving the file. The first `uvx` invocation downloads and caches the package, so the initial startup may take a few seconds.

### With Docker

Docker provides an isolated and consistent environment for running the MCP server.

#### Quick Start with Docker Compose (Recommended)

1. Create a `.env` file with your credentials:

```bash
echo "GARMIN_EMAIL=your_email@example.com" > .env
echo "GARMIN_PASSWORD=your_password" >> .env
```

2. Start the container:

```bash
docker compose up -d
```

3. View logs to monitor the server:

```bash
docker compose logs -f garmin-mcp
```

#### Using Docker Directly

```bash
# Build the image
docker build -t garmin-mcp .

# Run the container
docker run -it \
  -e GARMIN_EMAIL="your_email@example.com" \
  -e GARMIN_PASSWORD="your_password" \
  -v garmin-tokens:/root/.garminconnect \
  garmin-mcp
```

#### Using File-Based Secrets (More Secure)

For enhanced security, especially in production environments, use file-based secrets instead of environment variables:

1. Create a secrets directory and add your credentials:

```bash
mkdir -p secrets
echo "your_email@example.com" > secrets/garmin_email.txt
echo "your_password" > secrets/garmin_password.txt
chmod 600 secrets/*.txt
```

2. Edit [docker-compose.yml](docker-compose.yml) and uncomment the secrets section:

```yaml
services:
  garmin-mcp:
    environment:
      - GARMIN_EMAIL_FILE=/run/secrets/garmin_email
      - GARMIN_PASSWORD_FILE=/run/secrets/garmin_password
    secrets:
      - garmin_email
      - garmin_password

secrets:
  garmin_email:
    file: ./secrets/garmin_email.txt
  garmin_password:
    file: ./secrets/garmin_password.txt
```

3. Start the container:

```bash
docker compose up -d
```

#### Handling MFA with Docker

If you have multi-factor authentication (MFA) enabled on your Garmin account:

1. Run the authentication CLI with the service's token volume:

```bash
docker compose run --rm --entrypoint garmin-mcp-auth garmin-mcp
```

2. When prompted, enter your MFA code:

```
Garmin Connect MFA required. Please check your email/phone for the code.
Enter MFA code: 123456
```

3. The OAuth tokens will be saved to the Docker volume (`garmin-tokens`), so you won't need to re-authenticate on subsequent runs.

4. After MFA setup, you can run the container normally:

```bash
docker compose up -d
```

#### Docker Volume Management

The OAuth tokens are stored in a persistent Docker volume to avoid re-authentication:

```bash
# List volumes
docker volume ls

# Inspect the tokens volume
docker volume inspect garmin_mcp_garmin-tokens

# Remove the volume (will require re-authentication)
docker volume rm garmin_mcp_garmin-tokens
```

#### Using with Claude Desktop via Docker

To use the Dockerized MCP server with Claude Desktop, you can configure it to communicate with the container. However, note that MCP servers typically communicate via stdio, which works best with direct process execution. For Docker-based deployments, consider using the standard `uvx` method shown in the [With Claude Desktop](#with-claude-desktop) section instead.


## Usage Examples

Once connected in Claude, you can ask questions like:

- "Show me my recent activities"
- "What was my sleep like last night?"
- "How many steps did I take yesterday?"
- "Show me the details of my latest run"
- "Analyze my last ride's power zones and compare to my training zones"
- "Show me my CTL, ATL, and TSB trend for the last 6 weeks"
- "What was my power duration curve from yesterday's ride? Estimate my FTP."
- "Analyze the FIT data from my last cycling activity — how was my shifting quality on the climbs?"
- "Show me my HRV trend for the last 2 weeks and flag any recovery concerns"
- "What's my season best 20-minute power and when did I set it?"

## Troubleshooting

### `get_training_effect` returns HTTP 403 for a valid activity

Garmin's activity-details endpoint (`/activity-service/activity/{id}`) can
return **403 Forbidden** even when the same activity is visible in list tools.
`get_training_effect` now falls back to activity list search, which still
includes aerobic/anaerobic training effect for recent activities.

If the activity is older than the recent-search window, list the activity with
`get_activities` / `get_activities_by_date` and retry, or use those list fields
directly.

### `get_goals` returns no goals that exist in Garmin Connect

Garmin's goal-service only returns goals created in Connect's current Goals UI
(named distance/time targets such as a monthly cycling goal) when the request
sends `Sec-Fetch-Site: same-origin` and uses a 1-based `start`. With
`start=0`, it returns an empty list. python-garminconnect's `get_goals()` does not do both
(through 0.3.16), so `get_goals` here calls `/goal-service/goal/goals`
directly the way Connect's Goals page does, and uses the library call only as
a fallback.

### "Failed to spawn process: No such file or directory"

If Claude Desktop can't find `uvx`, it's because `uvx` is not in the PATH that Claude Desktop uses. To fix this:

1. Find where `uvx` is installed:
```bash
which uvx
```

2. Use the full path in your configuration. For example, if `uvx` is at `/Users/username/.cargo/bin/uvx`:
```json
{
  "mcpServers": {
    "garmin": {
      "command": "/Users/username/.cargo/bin/uvx",
      "args": [
        "--python",
        "3.12",
        "--from",
        "git+https://github.com/tagsvc/garmin_mcp",
        "garmin-mcp"
      ]
    }
  }
}
```

### Windows: Smart App Control blocks `uv` / `uvx`

On Windows 11 with **Smart App Control** enabled, `uv.exe` / `uvx.exe` may be blocked when launching Garmin_MCP — including when `uv` tries to load an unsigned `garmin-mcp.exe` from a local `.venv`.

This is **not** the same as Microsoft Defender Antivirus quarantine. Smart App Control is under:

**Windows Security → App & browser control → Smart App Control**

#### Symptoms
- Smart App Control notification when running `uv`, `uvx`, or Claude Desktop with `command: "uvx"`
- Claude Desktop fails to spawn the server even though `uvx` appears installed
- Event Viewer → *Applications and Services Logs* → *Microsoft* → *Windows* → *CodeIntegrity* → *Operational* shows **CodeIntegrity Error 3077** mentioning `uv.exe` and `garmin-mcp.exe` (policy / Enterprise signing level)

#### Confirm
1. Smart App Control is **On** (Evaluation or Enforcement).
2. Check the CodeIntegrity Operational log for event **3077** around the failed launch.
3. Defender “Virus & threat protection” history may be empty — that does not rule SAC out.

#### Recoveries (prefer keeping Smart App Control on)
1. Install or reinstall `uv` via a packaged channel, then verify in PowerShell:
   ```powershell
   winget install --id=astral-sh.uv -e
   # or: scoop install main/uv
   uvx --version
   ```
2. Point Claude Desktop at the full path to `uvx.exe` (same idea as the PATH troubleshooting above), for example:
   ```json
   "command": "C:\\Users\\<you>\\.local\\bin\\uvx.exe"
   ```
3. If Enforcement still blocks loading `garmin-mcp.exe`, you may need an admin/policy exception for that binary path. Turning Smart App Control off globally is a last resort, not the default advice.
4. If local uvx remains blocked, use the Docker Compose install path instead.

### Login Issues

If you encounter login issues:

1. Verify your credentials are correct
2. Check if Garmin Connect requires additional verification
3. Ensure the garminconnect package is up to date

### Logs

For other issues, check the Claude Desktop logs at:

- macOS: `~/Library/Logs/Claude/mcp-server-garmin.log`
- Windows: `%APPDATA%\Claude\logs\mcp-server-garmin.log`

### Garmin Connect Multi-Factor Authentication (MFA)

#### Understanding MFA with MCP Servers

MCP servers run as background processes without direct terminal access. If your Garmin account has MFA enabled, you must authenticate once using the pre-authentication tool before the server can run.

#### Recommended: Pre-Authentication Tool

The easiest way to handle MFA is using the dedicated authentication tool:

```bash
garmin-mcp-auth
```

This saves OAuth tokens to `~/.garminconnect` for future use. The server will automatically use these tokens when running in Claude Desktop or other MCP clients.

**Additional Options:**

```bash
# Use environment variables for credentials
GARMIN_EMAIL=you@example.com GARMIN_PASSWORD=secret garmin-mcp-auth

# Verify existing tokens
garmin-mcp-auth --verify

# Show where tokens are looked up, what is on disk, and whether they work
garmin-mcp-auth --diagnose

# Force re-authentication (e.g., when tokens expire)
garmin-mcp-auth --force-reauth

# Use custom token location
garmin-mcp-auth --token-path ~/.garmin_tokens
```

#### Alternative: Manual First Run

You can provide credentials from files to the authentication CLI:

```bash
# Store credentials in files for security
echo "your_email@example.com" > ~/.garmin_email
echo "your_password" > ~/.garmin_password
chmod 600 ~/.garmin_email ~/.garmin_password

# Run the authentication CLI before starting the server
GARMIN_EMAIL_FILE=~/.garmin_email GARMIN_PASSWORD_FILE=~/.garmin_password \
  uvx --python 3.12 --from git+https://github.com/tagsvc/garmin_mcp garmin-mcp-auth

# Enter MFA code when prompted
# Tokens will be saved automatically
# Now add to Claude Desktop config without credentials
```

After initial authentication, configure Claude Desktop **without** credentials (tokens are already saved):

```json
{
  "mcpServers": {
    "garmin": {
      "command": "uvx",
      "args": [
        "--python",
        "3.12",
        "--from",
        "git+https://github.com/tagsvc/garmin_mcp",
        "garmin-mcp"
      ]
    }
  }
}
```

#### Using Docker with MFA

If using Docker, follow the [Handling MFA with Docker](#handling-mfa-with-docker) section above for a streamlined experience with persistent token storage.

#### Troubleshooting MFA

**Error: "MFA required; run 'garmin-mcp-auth' before starting the server."**

Solution:
1. Open terminal
2. Run: `garmin-mcp-auth`
3. Enter credentials and MFA code
4. Restart Claude Desktop

**Token Expired**

OAuth tokens expire periodically (approximately every 6 months). Re-authenticate:
```bash
garmin-mcp-auth --force-reauth
```

**Verify Tokens Work**
```bash
garmin-mcp-auth --verify
```

**`garmin-mcp-auth` succeeded, but the server still cannot log in**

The server loads tokens only from `garmin_tokens.json` inside the token
directory (`$GARMINTOKENS`, default `~/.garminconnect`). Its startup error says
what it found there: no directory, no token file, a file it cannot read, or
tokens Garmin rejected. The usual cause is the MCP client starting the server
with a different `GARMINTOKENS` or `HOME` than the shell that ran
`garmin-mcp-auth` (common with Claude Desktop on Windows), so the two resolve
to different directories. Compare them with:
```bash
garmin-mcp-auth --diagnose
```
The `~/.garminconnect_base64` file that `garmin-mcp-auth` also writes is a copy
for other garminconnect-based tools; the server does not read it.
## Remote Mode (Multi-User, HTTP + OAuth2)

Remote mode runs the MCP server over HTTP with OAuth2 authentication, enabling multi-user access. Each user authenticates directly with their **Garmin Connect credentials** during the OAuth2 flow — no pre-created accounts or manual account linking required.

### How It Works

When a client (e.g., Claude) connects to the remote server:

1. The client discovers the OAuth2 endpoints and initiates authorization
2. The user is redirected to a login page asking for their **Garmin Connect email and password**
3. If 2FA is enabled on the Garmin account, a second page asks for the verification code
4. On success, the Garmin session tokens are stored server-side and an OAuth2 access token is returned to the client
5. The client uses this token to access all Garmin tools — no extra setup needed

```
Client → 401 → OAuth2 discovery
  → /authorize → redirect to /login?state=...
  → User enters Garmin email + password
  → POST /login/callback
      ├─ No 2FA → create user + session → redirect with auth code
      └─ 2FA required → redirect to /login/mfa?state=...
           → User enters verification code
           → POST /login/mfa/callback → create user + session → redirect with auth code
  → Client exchanges code for tokens → access granted
```

### Security

**Access control**

- **Email allowlist** (`GARMIN_ALLOWED_EMAILS`): only Garmin accounts on this list may authenticate. Fail-closed — if unset, every login is rejected.
- **Token import gating** (`GARMIN_IMPORT_SECRET`): importing a pre-minted token (login page or `POST /import-token`) requires this shared secret in addition to the allowlist, so knowing an allowlisted email alone cannot overwrite a user's session. Fail-closed — unset disables import.
- OAuth2 + PKCE on `/mcp`; auth codes are single-use and refresh tokens rotate.
- The login page returns a **single generic error** whether an email is off the allowlist or the password is wrong, so allowlist membership cannot be inferred by comparing responses. The specific reason is logged server-side.

**Credentials and data at rest**

- Garmin credentials are **never stored** — only the resulting session tokens are persisted on disk (per user, in garminconnect's native format).
- Session token directories are **owner-only** (`0700`/`0600`), applied after every write and swept at startup so pre-existing files are healed too.
- OAuth access/refresh tokens are stored **SHA-256 hashed**; a read of the database yields no usable bearer tokens.
- The 2FA client state is held in memory only, with a 5-minute TTL and single-use (`pop`).
- Users are identified by their Garmin email (upserted on login), and per-user state (Garmin sessions, saved analytics reports) is **scoped by user id** — one authenticated user cannot read or overwrite another's.

**Hardening**

- **Rate limiting** on `/login`, the MFA callback, and `/import-token`. The login limiter is keyed on email **plus client IP**, so one bad actor cannot lock a legitimate account out; the client IP is taken from the last `X-Forwarded-For` hop, which the client cannot spoof.
- **Response security headers** on every reply: HSTS, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, a strict CSP (`default-src 'none'`), and `Referrer-Policy: no-referrer`.
- Reflected values on the login/MFA pages are **HTML-escaped**, and untrusted values are escaped before logging (no log injection).
- **Remote tools never read the server's filesystem.** `upload_course` takes `gpx_base64` in remote mode and refuses `gpx_path` — see [Courses](#courses-and-gpx-uploads).
- **Three tools are stdio-only.** `check_garmin_auth` and `login_to_garmin` (on a network server, `login_to_garmin` would let any authenticated user store tokens for *any* Garmin account, bypassing the email allowlist), and `delete_activity` (irreversible, and an activity is the training history that Garmin Coach grades and every load and analytics figure is computed from). `delete_workout` and `delete_course` remain available — a workout or course can be recreated.
- **Stateless transport.** The server runs Streamable HTTP in stateless mode: no `Mcp-Session-Id`, nothing held per session, so there is no session state to accumulate or to expire under an idle client.
- **Every remote tool carries effect annotations** (`readOnlyHint` / `destructiveHint` / `idempotentHint` / `openWorldHint`), so a client can tell a delete from a read and ask before running one. Results are returned as plain text, not additionally wrapped as `{"result": ...}` structured content.
- The container **does not run the server as root**: the entrypoint takes ownership of `/data`, then drops to an unprivileged user.

### Courses and GPX uploads

`upload_course` accepts the GPX in one of two ways:

- **`gpx_base64`** — the file's bytes, base64-encoded. Works in both modes and is **required in remote mode**.
- **`gpx_path`** — a path on the machine running the server. **stdio (local) mode only.**

A path is refused in remote mode by design: there it would name the *server's* disk rather than yours, which is both useless to the caller and an arbitrary-file-read primitive. Locally the path is your own disk, so it stays supported.

### Running the Remote Server

#### With Docker Compose (Recommended)

```bash
# Set the public URL of your server
export GARMIN_MCP_SERVER_URL=https://garmin-mcp.example.com

# Start
docker compose -f docker-compose.remote.yml up -d
```

#### Locally

```bash
export GARMIN_MCP_SERVER_URL=http://localhost:8000

uv run garmin-mcp-remote
```

#### On Railway (deploy from git)

The repo ships a `railway.json` that pins the build to `Dockerfile.remote`
(the HTTP/OAuth server), so Railway deploys the right image straight from git —
the bare `Dockerfile` runs the stdio server and must **not** be used for the
remote deployment.

1. Create a Railway service from this repo. The `railway.json` selects the
   Dockerfile builder automatically; no build config needed in the dashboard.
2. Add a **volume mounted at `/data`** (persists the SQLite DB and Garmin
   session tokens across redeploys — without it, every redeploy logs users out).
3. Set the required environment variables (see table below). At minimum:
   `GARMIN_MCP_SERVER_URL` (your public Railway URL) and `GARMIN_ALLOWED_EMAILS`.

The server binds to Railway's injected `PORT` automatically, so you do not need
to set `GARMIN_MCP_PORT`.

4. Back up the volume. It is the only stateful thing in the deployment: lose it
   and every user has to authenticate again. See `OPERATIONS.md` for the backup
   policy and the second recovery path.

### Configuration (Environment Variables)

| Variable | Default | Description |
|---|---|---|
| `GARMIN_MCP_SERVER_URL` | *(required)* | Public URL of the server |
| `GARMIN_MCP_HOST` | `0.0.0.0` | Listen address |
| `GARMIN_MCP_PORT` | `$PORT` or `8000` | Listen port. Falls back to the platform-injected `PORT` (e.g. Railway), then `8000`. |
| `GARMIN_MCP_PATH` | `/mcp` | MCP endpoint path |
| `DB_PATH` | `/data/garmin_mcp.db` | SQLite database path |
| `SESSION_STORAGE_PATH` | `/data/garmin_sessions` | Garth token storage |
| `GARMIN_ALLOWED_EMAILS` | *(required)* | Comma-separated allowlist of Garmin Connect emails permitted to log in. **Fail-closed:** if unset or empty, every login is rejected. |
| `GARMIN_IMPORT_SECRET` | *(optional)* | Shared secret enabling the `POST /import-token` endpoint for programmatic token refresh. If unset, the endpoint is disabled (404). See "Refreshing tokens" below. |

### Token import / refresh (datacenter-IP 429 workaround)

Garmin rate-limits (HTTP 429) its OAuth token-mint endpoint from datacenter/cloud
IPs, so the remote server often cannot complete a Garmin login itself even though
the same flow works from a residential IP. Mint a token on a trusted machine and
import it. **Both import paths require `GARMIN_IMPORT_SECRET`** (a long random
value you set) in addition to the email allowlist — writing a session is a
state-changing action and an email is not a secret, so the secret prevents anyone
who merely knows an allowlisted email from overwriting a user's session.

Mint a token on a trusted (residential-IP) machine:

```bash
python -c "from garminconnect import Garmin; g=Garmin(); g.login('~/.garminconnect'); print(g.client.dumps())"
```

Then import it via either:

- **Login page:** expand "Advanced: import an existing Garmin token" on the
  Garmin login page, paste the blob, enter your allowlisted email and the import
  secret, leave password blank. The server stores it without performing SSO.
- **Endpoint (`POST /import-token`):** header `X-Import-Secret`, JSON body
  `{"email": "...", "token": "..."}`.

> **Security note:** the remote server gates authentication on `GARMIN_ALLOWED_EMAILS`.
> Only the Garmin Connect accounts whose login email is on this list can authenticate.
> Matching is case-insensitive. Because the default is fail-closed, you must set this
> variable (e.g. `GARMIN_ALLOWED_EMAILS=you@example.com,partner@example.com`) before any
> user can connect.

### Testing with MCP Inspector

```bash
npx @modelcontextprotocol/inspector http://localhost:8000/mcp
```

## Testing

This project includes comprehensive tests for all MCP tools. **All tests are currently passing (100%)**.

### Running Tests

```bash
# Run unit and integration tests (default - uses mocked Garmin API, e2e tests are deselected)
uv run pytest

# Run only the integration tests
uv run pytest tests/integration/

# Run tests with verbose output
uv run pytest tests/integration/ -v

# Run a specific test module
uv run pytest tests/integration/test_health_wellness_tools.py -v

# Run end-to-end tests (requires real Garmin credentials).
# Some of these create and delete foods, food logs and workouts in your account.
uv run pytest tests/e2e/ -m e2e -v
```

### Test Structure

- **Integration tests** (200+ tests): Test all MCP tools using FastMCP integration with mocked Garmin API responses
- **End-to-end tests** (`tests/e2e/`): Test with real MCP server and Garmin API (requires valid credentials). Only run with `-m e2e`.

## Reinstalling from local path

If you are working from a local checkout or fork:

```bash
uv tool install --python 3.12 --force C:\Users\aresd\Desktop\programacion\garmin_mcp
```

## Credits

This fork stands on work from several projects:

- **[Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp)** — the original
  Garmin MCP server and our upstream (we sync new tools and fixes from here).
- **[Tomas2D/garmin_mcp](https://github.com/Tomas2D/garmin_mcp)** — the OAuth2
  remote server (PR #124, RFC 9728 / claude.ai compatible) that this fork's
  remote mode is built on.
- **[coloboxp/garmin_mcp](https://github.com/coloboxp/garmin_mcp)** — the
  historical analytics and interactive auth tools (PR #121).
- **[cyberjunky/python-garminconnect](https://github.com/cyberjunky/python-garminconnect)**
  — the underlying library used to access the Garmin Connect API.

This fork adds the email allowlist, secret-gated token import, 429 fail-fast
login, Railway deployment, and the security hardening (rate limiting, token-at-rest
hashing, response security headers). See `CHANGELOG.md` for the full divergence.
