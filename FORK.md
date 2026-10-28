# Fork notes — what this fork adds and what must be preserved

This repository is a fork of [Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp).
It carries custom work on top of upstream.

**Provenance (see README "Credits" for links):** the OAuth2 remote server is from
**Tomas2D/garmin_mcp PR #124**; the historical-analytics and interactive-auth tools
are from **coloboxp/garmin_mcp PR #121**; the original project / sync upstream is
**Taxuspt/garmin_mcp**. This fork's own additions are the allowlist, secret-gated
token import, 429 fail-fast, Railway deploy, and the security hardening.

> **Upstream identity (important):** GitHub lists this fork's parent as
> `Tomas2D/garmin_mcp`, but our real upstream is **`Taxuspt/garmin_mcp`** (the
> original project; Tomas2D is itself a fork). Always sync from Taxuspt using the
> procedure below — do **not** click GitHub's "Sync fork" button or trust its
> "N commits behind" banner, which both compare against `Tomas2D` and fight our
> divergence.

**When merging upstream changes, the
features and invariants below must not regress.** After any upstream merge, run
the full test suite (`uv run pytest -m "not e2e"`) — every item here has test
coverage.

> **These invariants are CI-gated *and* merge-gated.** GitHub Actions runs the
> suite on every push and pull request to `main` (`.github/workflows/ci.yml`,
> Python 3.12/3.13), and a branch ruleset makes those checks **required**: a merge
> that regresses one of them cannot land on `main` at all, rather than landing red.
> `main` also takes no direct pushes, so every change arrives through a PR. See
> `OPERATIONS.md` for the ruleset detail and how to verify it is still enforcing.
> Still run the suite locally while resolving a merge — the gate is the backstop,
> not the first line of defence.

## What this fork adds

| Feature | Lives in | Notes |
|---|---|---|
| Historical analytics (8 tools) | `src/garmin_mcp/analytics.py` | Registered in **both** `remote.py` and `__init__.py`. Adapted from coloboxp/garmin_mcp PR #121. |
| Interactive auth (2 tools) | `src/garmin_mcp/auth_tools.py` | `check_garmin_auth`, `login_to_garmin`. Registered in `__init__.py` **only** (stdio). Adapted from PR #121. |
| Token utils additions | `src/garmin_mcp/token_utils.py` | `resolve_path`, `ensure_token_directory`, `without_token_env`, `_clean_config_value` (added, not replacing the existing functions). |
| Email allowlist | `config.py`, `oauth_provider.py` | `GARMIN_ALLOWED_EMAILS`; enforced in `handle_login_callback` before any Garmin contact. |
| Token import | `session_manager.py`, `oauth_provider.py`, `remote.py` | `create_session_from_token_blob`; login-page import + `POST /import-token`. Gated by `GARMIN_IMPORT_SECRET` + allowlist. |
| 429 fail-fast login client | `oauth_provider.py` (`_new_login_client`) | Excludes 429 from garth's retry `status_forcelist` so a rate-limited login isn't amplified. |
| Coach plan curation fix | `workouts.py` (`_get_garmin_coach_workouts`) | Surfaces the `trainingPlanDetailsDTO` fields that exist — race goal, workouts per week, registration date. Upstream reads only `trainingType`, which adaptive plans do not have. |
| Garmin Coach tools (3) | `workouts.py` | `get_coach_plans` (active + completed), `get_coach_plan_details`, `get_coach_plan_progress` (per-workout grades). All on `atp-api`, found by capturing Garmin Connect's own traffic. |
| Paired sensor battery (1 tool) | `devices.py` (`get_sensors`) | `/device-service/sensors` — battery level and last-connected, which `get_devices` omits. |
| Coverage additions (1 tool) | `gear_management.py` | `get_gear_activities` — the reverse of `get_activity_gear`, for auditing a shoe's or bike's accumulated mileage. Three training-plan tools added alongside it were removed on 2026-09-19; see the invariant below. |
| Railway deploy | `railway.json`, `Dockerfile.remote`, `config.py` | `railway.json` pins the Dockerfile builder; `config.port` honors `$PORT`. |
| Explicit time zones | `times.py`; applied across `health_wellness`, `activity_management`, `activity_analysis`, `gear_management`, `training`, `weight_management`, `devices`, `analytics` | Every curated time is `*_gmt` (ISO, `Z`) plus `*_local` (ISO with its UTC offset, from the record's own offset, else the profile's `timeZone`). Raw payloads keep their shape and gain local siblings and `utcOffsetHours`. |
| Account-unit companions | `units.py`; applied in `activity_management`, `weight_management`, `gear_management`, `training`, `courses`, `workouts` | Beside each metric field, the same value in the account's units (`distance_miles`, `avg_pace_per_mile`, `elevation_gain_feet`, `weight_lb` for `statute_us`). Workout uploads accept pace text such as `10:30/mi`. |

## Invariants that must NOT regress

- **`mcp` stays capped `<2`.** mcp 2.x renames `mcp.server.fastmcp` -> `mcp.server.mcpserver`
  (`FastMCP` -> `MCPServer`); the server is not ported. Don't remove the upper bound
  until that port is done.
- **`garminconnect==0.3.5` stays pinned** (`pyproject.toml`). Do **not** downgrade
  to 0.2.x, and do not go below 0.3.5 (CVE-2026-54447: ≤0.3.4 writes the token
  store world-readable). If upstream bumps it, re-verify `session_manager` +
  token import before taking it.
  - **The real dependency is `garth`, not the 0.3.x line.** An earlier version of
    this note said the stored-token format depends on "the 0.3.x line", which
    implied any 0.3.x is safe. It is not. Checked on 2026-09-10: **0.3.13 drops
    garth entirely**, replacing it with `curl_cffi` + `ua-generator`. Our auth
    substrate is garth throughout — `session_manager.garth.dump(token_dir)` and
    its `OAuth1Token`/`OAuth2Token` handling, the `di_token` / `di_refresh_token`
    / `di_client_id` blob the token-import page accepts, `GarthHTTPError` in
    `auth_tools`, and the 429 fail-fast client, which patches garth's retry
    `status_forcelist`. A bump past the last garth-based release is not a version
    change but a replacement of the authentication layer, and would likely
    invalidate every session already stored on the `/data` volume.
  - Between 0.3.5 and 0.3.13 upstream added 16 methods and removed none. The
    appealing ones (`push_workout_to_device`, `update_workout`,
    `get_hrv_data_range`) are conveniences over capabilities this fork already
    has, so they do not justify the migration. Re-evaluate only if something
    genuinely unavailable appears, and scope it as an auth-layer rewrite.
- **Allowlist is fail-closed.** An empty/unset `GARMIN_ALLOWED_EMAILS` rejects every login.
- **Token import is fail-closed and secret-gated.** `GARMIN_IMPORT_SECRET` (constant-time
  compare) is required on both the login-page import and `POST /import-token`; unset disables import.
- **No live-Garmin validation of imported tokens.** Validation is structural only — a live
  check would make the server a good/bad token oracle and re-introduce the 429 surface.
- **`auth_tools` is stdio-only** — registered in `__init__.py`, never in `remote.py`.
- **The remote server is stateless Streamable HTTP** (`stateless_http=True` in
  `remote.py`). Stateful servers on `mcp` <= 1.29.1 never reclaimed sessions
  (CVE-2026-59951), so any token holder could grow server memory without bound;
  1.30.0 caps and expires them, but its 30-minute idle expiry makes claude.ai
  report the first call after a quiet spell as failed. Stateless keeps no
  sessions at all. Nothing needs them: the caller is identified per request
  from the OAuth bearer token. `mcp` is floored at 1.30.0 in `pyproject.toml`.
  Pinned by `tests/integration/test_remote_end_to_end.py`, which drives
  authenticated requests from two users through the real remote stack.
- **Each tool call keeps its caller's identity on the worker thread.** Since the
  2026-10-27 sync, `_ToolFilter` runs every tool body as
  `anyio.to_thread.run_sync(lambda: asyncio.run(...))` (upstream #449), so the
  remote server's per-request auth contextvar must cross onto a worker thread
  and into a fresh event loop for `get_client()` to resolve the right user.
  anyio carries it; a plain `threading.Thread` does not. If it is ever lost,
  remote calls fail closed ("client not available") rather than leak a client,
  but every call breaks. Pinned by `test_remote_mode_contract.py` with concurrent
  callers holding different tokens; it was confirmed to fail on a plain thread.
- **Remote registers through `remote.register_remote_tools()`, and nowhere else.**
  It routes every module through `garmin_mcp._ToolFilter` — the wrapper stdio
  uses — so remote tools get upstream's effect annotations and
  `structured_output=False` (bare FastMCP registration re-wraps every JSON string
  as `{"result": "<escaped json>"}`, doubling the payload, and attaches no
  annotations). It also withholds `REMOTE_EXCLUDED_TOOLS` — currently only
  `delete_activity` — and **fails closed**: an exclusion naming no registered
  tool (an upstream rename) raises at startup instead of serving the tool. The
  count and contract tests call this same function, so documented remote counts
  are what is actually served. Pinned by `test_remote_mode_contract.py`, which
  asserts the stdio-minus-remote set as a literal: withholding a tool takes an
  edit to the test as well as to `remote.py`.
- **`Dockerfile.remote` must not use the `VOLUME` instruction** — Railway rejects it;
  persistence is a Railway volume mounted at `/data`.
- **`config.port` honors `$PORT`** (then `GARMIN_MCP_PORT`, then 8000) for Railway.
- **`/data` persistence**: SQLite DB (`DB_PATH`) and per-user sessions (`SESSION_STORAGE_PATH`).
- **Login/MFA pages must HTML-escape reflected `state`/`error`** (anti reflected-XSS;
  the pages collect Garmin credentials). Use `_html_escape`, not raw f-string interpolation.
- **Access/refresh tokens are stored hashed at rest** (SHA-256 in SQLite); lookups
  hash the incoming token. Don't revert to storing/looking-up plaintext.
- **Per-user token dirs are owner-only.** `secure_token_dir()` runs after every
  token dump *and* `SessionManager` sweeps existing dirs at startup, so tokens on
  the volume are never left world-readable (CVE-2026-54447 residual).
- **The server process does not run as root.** `Dockerfile.remote` starts as root
  (Railway mounts volumes root-owned, at container start only), chowns `/data`, then
  drops to `appuser` via `gosu` in `scripts/docker-entrypoint.sh`. Do **not** replace
  this with a bare `USER` directive: the volume would be unwritable, and Railway's
  documented workaround (`RAILWAY_RUN_UID=0`) would just put it back to root.
- **Remote tools never read *or write* server filesystem paths.** A path names
  the SERVER's disk in remote mode, making it an arbitrary-file-read (or -write)
  primitive for any authenticated user. `upload_course` takes `gpx_base64` and
  refuses `gpx_path`; `download_activity_file` returns the bytes inline and
  refuses `output_dir`; `set_fit_download_dir` is refused outright (it is also
  process-global, so one caller would redirect every other user's downloads).
  **This is now CI-enforced:** `tests/unit/test_remote_mode_contract.py` fails
  when any registered tool exposes a path-shaped parameter that is not on its
  reviewed `_GUARDED` list. That test replaces protection this used to get by
  accident — see the module-configuration invariant below.
- **Every tool module is `configure()`d in remote mode**, from
  `remote._CONFIGURED_MODULES`, with a `_ResolvingGarminProxy`. The proxy holds
  no client: each attribute access resolves the *calling user's* client via the
  per-request contextvar, so a tool written against the plain `garmin_client`
  global — which is how upstream writes every tool — works in both modes and
  never shares a client between users. Do not "simplify" this back to a single
  client captured at configure() time: that would hand one user's Garmin session
  to another. `test_remote_mode_contract.py` fails if a registered module is
  left unconfigured.
- **The workout feed under-counts a plan week; the ATP calendar does not.**
  An adaptive plan reserves days before generating workouts for them. The
  GraphQL `trainingPlanScalar` feed behind `get_garmin_coach_workouts` returns
  only generated workouts; `/atp-api/atp/athlete/calendar` behind
  `get_coach_plan_progress` also returns the reserved days, which arrive with a
  date and `completed: false` and **no workout or scheduled-workout id** (the
  "Stay Tuned for Details" entries in Garmin's app). Measured on plan
  1789330190 over 2026-09-13..09-26: 7 calendar entries against 5 generated
  workouts. Anything answering "how many sessions this week" from the feed
  alone is wrong. Pinned by `test_coach_progress_and_sensors.py`.
- **Recovery hours come from `recoveryTime`; `recoveryTimeChangePhrase` is not a
  reading.** Upstream zeroed recovery whenever the phrase was `REACHED_ZERO`. A
  live post-run snapshot (2026-10-27, `AFTER_POST_EXERCISE_RESET`) carried
  `REACHED_ZERO` with `recoveryTime` 4320 — the 72h Garmin's app showed — and a
  recovery factor of 10 (POOR); the tools reported 0h and "recovered". The phrase
  describes what changed at the snapshot's event; when recovery has drained,
  Garmin sends the low minute value itself (that morning: 1). Garmin Connect's
  web client has no display text for the phrase and shows hours from
  `recoveryTime`. Snapshots come at wake-up, after an activity, and in occasional
  mid-day `UPDATE_REALTIME_VARIABLES` updates that record the countdown (seen
  2026-10-25: 10.2h after a run, 5.8h four hours later); each holds until the
  next, so values are reported with `as_of` and `input_context`, never
  as a live countdown. With no date given, `get_recovery_time_remaining`
  reports the most recent snapshot on any day (`resolved_as: latest_snapshot`),
  not the server's "today": on Railway's UTC clock it is already tomorrow every
  evening in New York, and the tool said "unavailable" while a 72h snapshot was
  current. Snapshots are filed under the athlete's local date, so it checks UTC
  tomorrow, today and yesterday, newest first, with no timezone lookup. _On
  merge:_ do not take an upstream change that lets the phrase alter the hours,
  or that defaults the date to the server's clock. Pinned by `test_recovery_live_snapshots.py`, built from
  the real payload and confirmed to fail on the upstream rule.
- **Garmin's "completed" means ended, not finished.** A cancelled (quit) plan is
  listed under `/atp-api/atp/athlete/completed` and carries `planCompleted: true`
  as well as a non-null `quit` — observed on plan 1789330190 after it was quit,
  2026-10-14. `get_coach_plans` therefore reports `status` as `active`,
  `completed`, `quit`, or `ended` (over, but quit could not be read), and never
  infers "completed" from the list alone. Pinned by
  `test_coach_progress_and_sensors.py`.
- **Metric fields are never replaced; account units are added beside them.**
  Garmin's API returns meters, m/s, and grams or kilograms whatever the
  account's setting (only activity weather follows it). `units.add_account_units`
  adds a companion after each metric field — miles, feet, mph, min/mile pace and
  pounds for `statute_us`; km, km/h and min/km pace for `metric`; miles and pace
  but kilograms for `statute_uk` — and never overwrites a key. An unknown unit
  system adds nothing, so output matches what it was before (the shared test
  mock's `get_unit_system` returns a Mock, which reads as unknown). Swims keep
  meters: Garmin shows pool units and pace per 100. The unit system is the profile's
  `measurementSystem`, which garminconnect caches at login; reading it makes no
  API call. A change in Garmin Connect therefore reaches the remote server when
  `SessionManager` next restores the session (`CACHE_TTL`, one hour) and a stdio
  server on restart. Pace text in a
  workout's `pace.zone` bounds (`10:30/mi`, `5:00/km`) becomes m/s in
  `_normalize_workout_steps`, checked for the whole workout before any step
  changes; a bare `10:30` is rejected, since it could be per mile or per km.
  Temperatures get Fahrenheit on a US account only, and a difference (a
  range, a deviation) converts by x1.8 with no +32; centimeters get inches on
  statute accounts. Garmin's raw payloads hold bare grams under `weight`,
  `boneMass` and `muscleMass` in some places and kilograms under the same
  name in others (the FTP payload's `weight` is 73.0), so pounds for raw
  keys are added per payload (`units.add_gram_masses`), never by key name.
  Hydration follows the profile's own `hydrationMeasurementUnit` -- a US
  account can log water in milliliters -- not the measurement system.
  Decimal hours get h:mm text (`_hm`): 6.37 h is 6:22, not 6:37.
  _On merge:_ an upstream rewrite of one of these tools' `json.dumps` returns
  must keep the `add_account_units(...)` wrap. Pinned by
  `test_account_units_tools.py`, `test_more_account_units_tools.py` and
  `test_units.py`.
- **Times name their zone; local comes from the record before the profile.**
  Garmin mixes GMT, the watch's local time and epoch milliseconds, often
  under keys that do not say which: body battery printed its GMT start as a
  bare `start_time` (6:37 PM for a 2:37 PM run), the activity lists and the
  FIT tool used `start_time` for local and UTC respectively, and several
  tools used the server's own clock (UTC on Railway). The rule (`times.py`):
  GMT under `*_gmt` as ISO ending in `Z`; local under `*_local` as ISO with
  its UTC offset, taken from the record's own offset when Garmin gives one
  (`timezoneOffset`, a `*Local` beside the `*GMT`, the FIT activity message),
  else from the profile's `timeZone` with a `local_time_zone` field saying
  so. Garmin's `*Local` epochs are the wall clock written as UTC, not real
  epochs (`times.wall_clock`). "Today" for defaults is the profile zone's
  (`local_today`). The profile zone is read once per Garmin client and cached
  on it -- never on the module proxy, which every remote user shares
  (`test_the_shared_proxy_is_never_the_cache_key`). Raw payloads keep their
  shape and gain camelCase siblings (`eventStartTimeLocal`, `sleepStartLocal`,
  `utcOffsetHours`). `tzdata` is a dependency: Windows has no system zone
  database. _On merge:_ keep the `_gmt`/`_local` names and do not reintroduce
  a bare time key or a server-clock "today". Pinned by `test_times.py` and
  `test_explicit_times_tools.py`.
- **The fitnessstats endpoint is not in its documented units.** Behind
  `get_progress_summary_between_dates`, Garmin sends distance and elevation
  gain in centimeters, durations in milliseconds and calories pre-scaled by
  ~4.19 (verified 2026-10-28 against a run of known distance, time and gain).
  `training._PROGRESS_METRIC_UNITS` converts them; an unverified metric is
  passed through with `unit: "unknown"`, not guessed. _On merge:_ keep the
  table. Pinned by `test_training_tools.py`.
- **`protected` and `benchmark` are different fields, not aliases.**
  `protected` comes from the GraphQL workout feed, `benchmark` from the ATP
  calendar; neither service sends the other's key. They coincided on the single
  benchmark observed, which does not establish that they mean the same thing.
  Do not collapse them.
- **`performed_at` is device-local and carries no zone designator**, identical
  to the activity's `start_time_local` and four hours behind its
  `start_time_gmt` for this account. Appending `Z` when parsing shifts evening
  sessions onto the next day.
- **`performanceRating` semantics are unobserved.** Every sample so far is
  `GOOD`. Describe it as a per-workout rating from Garmin Coach; do not
  document a scale or what moves it until a different value appears.
- **No test performing a real Garmin login runs in the default CI selection.**
  This repository is public, so build logs are public: such a test prints live
  account data into them. Any module doing live auth must carry
  `pytestmark = pytest.mark.e2e`, and
  `tests/unit/test_live_auth_is_excluded_from_ci.py` fails when one does not —
  including new ones, since it detects the calls rather than listing files. Two
  dismissed CodeQL alerts rest on this exclusion, and a dismissal stays closed
  even after the exclusion breaks.
- **Training-plan data comes from GraphQL, not `trainingplan-service` REST.**
  Verified against a live enrolled adaptive (Garmin Coach) plan on 2026-09-19:
  `/trainingplan-service/trainingplan/plans` returns an **empty list** while a
  plan is active, and `.../fbt-adaptive/{plan_id}` returns **404** for a plan id
  taken from the working feed. Three tools built on those endpoints
  (`get_training_plans`, `get_training_plan_details`,
  `get_adaptive_training_plan_details`) were removed for that reason — an empty
  list reads as "you have no plans", which is worse than the tool not existing.
  Two sources work, and neither is `trainingplan-service`: the GraphQL
  `trainingPlanScalar` query used by `_get_garmin_coach_workouts`, and
  **`/atp-api/atp/athlete/plan?lang=en&athletePlanId=<id>`**, which is what
  Garmin Connect's own web client calls and what `get_coach_plan_details` uses.
  Confirmed on 2026-09-19 by capturing the browser's own traffic:
  `trainingplan-service/trainingplan/plans` returned `{"trainingPlanList": []}`
  even from an authenticated browser session with the plan active. Do not
  rebuild plan tools on `trainingplan-service`.
- **`_get_garmin_coach_workouts` must surface the `trainingPlanDetailsDTO`
  fields that exist.** Upstream reads only `trainingType`, absent on adaptive
  plans, so the key resolved to `None`, was dropped by the strip-`None` filter,
  and the whole DTO — the athlete's race goal included — was discarded.
  `tests/integration/test_coach_plan_curation.py` pins this against a real ATP
  payload; an upstream merge that reverts the curation fails it.
- **Per-user state is scoped by `user_id` in remote mode.** The analytics
  saved-report store is per-user; a shared file lets one authenticated user read
  or overwrite another's definitions.
- **The browser login path returns ONE generic error** (`_GENERIC_LOGIN_ERROR`)
  for both "not allowlisted" and "bad credentials" — distinct messages let an
  attacker enumerate allowlist membership by differencing. Log the specific
  reason server-side instead.
- **Untrusted values are sanitised before logging** (`_safe_log`): embedded
  CR/LF in an email would otherwise forge log lines. Request-derived values are
  sanitised **at their source**, not at each log site — `_client_ip()` escapes and
  bounds the value it returns, so a caller cannot reintroduce the hole by
  forgetting to wrap it.
- **Caller-supplied text never becomes a filename unfiltered.**
  `upload_course` passes `course_name` through `_safe_upload_filename()` before
  using it as a multipart filename (strips directory components, quotes and
  CR/LF that would otherwise land in a `Content-Disposition` header).
- **Rate-limit keying uses the LAST `X-Forwarded-For` hop**, never the first.
  The first entry is client-supplied and spoofable; keying on it lets an attacker
  mint a fresh bucket per request and bypass the limiter entirely (`_client_ip`).
- **Auth endpoints are rate-limited** (`/login`, MFA callback, `/import-token`) via `_RateLimiter`.
  The login limiter is keyed on **email + client IP**, never email alone: email-only
  keying let anyone lock an allowlisted account out indefinitely.
- **Remote responses carry security headers** via `_SecurityHeadersMiddleware`
  in `remote.py` (HSTS, nosniff, `X-Frame-Options: DENY`, strict CSP,
  `Referrer-Policy: no-referrer`). The remote server is served through this
  wrapper, not bare `FastMCP.run()` — keep the wrapper on the run path.

## Why these changes (decision log)

Read this before reconciling an upstream merge — it explains the *intent* behind
each divergence so a merge doesn't silently undo a deliberate decision.

- **Pin `garminconnect==0.3.5`.** Why: the stored-token format and session restore
  (`session_manager`, token import) depend on the 0.3.x client API
  (`garmin.client.dumps/loads/dump`, di-token fields). PR #121 used 0.2.38, which
  is incompatible. 0.3.5 is the security floor: ≤0.3.4 writes `garmin_tokens.json`
  world-readable (CVE-2026-54447); `session_manager` also chmods the token dir
  itself as defence in depth. _On merge:_ if upstream bumps it, re-verify token
  import + session restore before accepting; don't take the bump blindly.

- **`auth_tools` is stdio-only.** Why: in remote mode, authentication happens
  through the OAuth login page; `login_to_garmin` exists so the *stdio* server can
  start without tokens and authenticate at runtime. Registering it in `remote.py`
  would expose a redundant, confusing second auth path. _On merge:_ keep it out of
  `remote.py`.

- **`delete_activity` is stdio-only** (added upstream 2026-10). Why: it is
  irreversible, and an activity is a record, not a plan — Garmin Coach grades it,
  and training load, acute/chronic balance and every historical analytic here
  are computed from it. Its `confirm=true` gate stops an accidental call but not
  a deliberate one, and on the remote server the caller is a model acting on
  whatever content it was handed. `delete_workout` and `delete_course` stay
  exposed: a workout or course is recreatable. _On merge:_ keep it in
  `REMOTE_EXCLUDED_TOOLS`; if upstream renames it, startup fails until the
  exclusion is updated.

- **Email allowlist, fail-closed.** Why: the remote server is publicly reachable
  (claude.ai); without a gate, anyone who completes a Garmin login could attach an
  account. Fail-closed means a misconfiguration (unset var) denies access rather
  than opening it. _On merge:_ don't change the default to fail-open.

- **429 fail-fast login client.** Why: garth retries on HTTP 429, turning one login
  into ~4 hits on Garmin's rate-limited OAuth endpoint and deepening the throttle
  ("too many 429 error responses"). Failing fast hits Garmin once. The alternative
  (more retries/backoff) was rejected because it amplifies the limit. _On merge:_
  keep 429 out of the client's retry `status_forcelist`.

- **Token import (login-page paste + `POST /import-token`).** Why: Garmin
  rate-limits its OAuth token-mint endpoint from datacenter IPs, so the server
  often can't complete a login even with a correct MFA code. Minting on a
  residential IP and importing sidesteps this entirely. _On merge:_ preserve both
  import paths.

- **No live-Garmin validation of imported tokens.** Why: validating a pasted token
  against Garmin would (a) make the endpoint a good/bad token *oracle* and (b)
  re-introduce the datacenter-IP 429 surface (server calling Garmin). We gate with
  a secret instead. _On merge:_ keep validation structural-only.

- **Import-secret gating, fail-closed, on both paths.** Why: the browser import was
  originally email-only, but an email is not a secret — anyone who knew an
  allowlisted email could overwrite that user's session with a bogus token (a DoS).
  The shared secret is the proof-of-authorization. _On merge:_ keep both paths
  gated with a constant-time compare; unset secret disables import.

- **Remove `VOLUME` from `Dockerfile.remote`.** Why: Railway's builder rejects the
  Docker `VOLUME` instruction; persistence is a Railway volume mounted at `/data`.
  _On merge:_ don't reintroduce `VOLUME`.

- **`$PORT` fallback in `config.py`.** Why: Railway injects a random `PORT` and
  routes external traffic to it; the app must bind there. Precedence is
  `GARMIN_MCP_PORT` > `PORT` > `8000` so an explicit override still wins. _On
  merge:_ keep the fallback.

- **`railway.json` pins `Dockerfile.remote`.** Why: the bare `Dockerfile` runs the
  *stdio* server (no HTTP port); without the pin, Railway auto-detects it and
  deploys a container that can't serve traffic. _On merge:_ keep the pin.

- **Refresh skill kept out of the repo.** Why: by preference, the token-refresh
  procedure lives as a personal chat script, not a committed skill; the
  `/import-token` endpoint remains for automation. _On merge:_ don't re-add a
  committed skill unless intended.

## Updating from upstream (sync procedure)

You are never obligated to sync. Pull upstream only when you want a specific fix
or feature. Each sync is a deliberate, tested operation — never a blind merge.

**Fastest path:** open a Claude Code session in this repo and say something like
*"the upstream we forked from has updated — show me what's new and how we'd bring
it in."* `CLAUDE.md` routes here; the session follows the steps below.

**What an "update from upstream" request should produce — BEFORE merging anything:**

1. Fetch upstream and list what's new since the last sync (`git log main..upstream/main`).
2. **Triage each upstream change into three buckets and report them to the user:**
   - *Safe to take* — doesn't touch this fork's customized files/areas.
   - *Overlaps our work* — touches a conflict-prone file (below); reconcile using
     the decision log above so our intent is preserved.
   - *Conflicts with an invariant* — would regress something in "Invariants" /
     "Why these changes"; flag it explicitly and ask the user before proceeding.
3. Present the integration plan (what we take as-is, what we reconcile and how,
   what we drop or flag) and get the user's go-ahead.
4. Only then merge on a branch, resolve conflicts per that plan, run the full
   suite, and land it if green.

Do not silently merge — the value is the review, not the merge.

**Manual procedure:**

One-time — wire the upstream remote:

```bash
git remote add upstream https://github.com/Taxuspt/garmin_mcp.git
```

Each sync:

```bash
git fetch upstream
git log --oneline main..upstream/main      # what's new upstream since last sync
git diff main...upstream/main              # full upstream diff

git switch -c sync-upstream-$(date +%Y%m%d)
git merge upstream/main                     # or: git rebase upstream/main
# resolve conflicts, preserving the invariants above

uv run pytest -m "not e2e"                  # MUST pass before going further

git push -u origin sync-upstream-$(date +%Y%m%d)
# then open a PR: `main` takes no direct pushes, and the four required checks
# must go green before it can merge. Railway redeploys once the PR lands on main.
```

**Conflict-prone files** (this fork customized them, so upstream edits here often
collide): `src/garmin_mcp/__init__.py`, `remote.py`, `oauth_provider.py`,
`config.py`, `token_utils.py`, `session_manager.py`, `pyproject.toml`,
`Dockerfile.remote`. Add to those the **tool modules**, which conflicted in both
the 2026-09-02 and 2026-09-10 syncs for the reason below: `health_wellness.py`,
`nutrition.py`, `courses.py`, `training.py`, `workouts.py`.

**The recurring conflict, and how to resolve it.** Two syncs running, most
conflicts were the same shape and are not real disagreements. Upstream inserts a
new tool immediately *before* an existing one, and the existing one's signature
differs here because this fork threads `ctx` into it. Git sees the new tool and
the changed signature line as one overlapping hunk:

```
<<<<<<< HEAD
    async def get_user_summary(ctx: Context, date: str) -> str:     <- ours
=======
    ...upstream's entire new tool...
    async def get_user_summary(date: str) -> str:                   <- theirs
>>>>>>> upstream/main
```

The resolution is mechanical: **take upstream's block, drop its last line, and
restore our signature line.** Nothing else in the hunk is in dispute. Judge each
hunk before applying it though — in the 2026-09-10 sync one conflict in
`nutrition.py` looked identical but was a genuine upstream bug fix that had to be
taken whole.

**Watch items during conflict resolution:**

- `pyproject.toml` — keep `garminconnect==0.3.5` (0.3.5 minimum; CVE-2026-54447)
  unless you re-verify token import.
- `__init__.py` / `remote.py` — ensure `analytics` stays registered in both,
  `auth_tools` stays registered in `__init__.py` **only**, and the `/import-token`
  route survives.
- `Dockerfile.remote` — ensure no `VOLUME` instruction was reintroduced, and it
  still installs from `uv.lock` (reproducible deploys), not a fresh resolve.
- `config.py` — keep the `$PORT` fallback, the allowlist, and the import secret.
- **New upstream tools no longer need migrating.** Upstream is stdio-only, so
  its tools call the module-global `garmin_client` directly. That global is now
  a `_ResolvingGarminProxy` in remote mode, so those tools work unchanged — the
  old "grep for `garmin_client.` and rewrite every call to `get_client(ctx)`"
  step is gone. Two things replace it, both automatic:
  - a **new module** must be added to `remote._CONFIGURED_MODULES` *and* have
    `register_tools()` called on it; the contract test fails if they diverge.
  - a **new tool taking a path** fails the path-guard test until it refuses that
    path in remote mode. Do not add it to `_GUARDED` to make the test pass —
    `_GUARDED` records tools that already refuse.
  - a **new tool** fails `test_tool_annotations.py` until it is classified in
    `tool_annotations.py`. Fork tools go in the `_FORK_*` sets at the bottom,
    which union into upstream's, so upstream's own lists keep merging cleanly.
- **Watch for helper-signature drift — it does not show up as a conflict.**
  Upstream has been dropping the `client` argument from module helpers (the
  proxy makes it unnecessary). Taking upstream's side of a hunk that changes a
  helper's signature, or drops a local our code still uses, leaves code
  *outside* the hunk inconsistent: it merges cleanly and raises `TypeError` or
  `NameError` on first call. The 2026-10-14 sync hit this with
  `_is_already_scheduled`, `_put_activity_update` and `schedule_week`'s
  `client`; the broken callers of `_put_activity_update` were in no conflict
  hunk at all. When a hunk changes a helper's signature, grep every call site
  across the package and adopt upstream's caller text along with the helper.

**Definition of done:** suite green, invariants intact, tool counts stdio 174 / remote 171.

## Expected state after a clean build

- Full suite: `uv run pytest -m "not e2e"` → all pass (1660 at time of writing).
- Tool counts: **stdio 174**, **remote 171** (`auth_tools` and `delete_activity` are stdio-only).
- Documented counts are test-enforced: `tests/unit/test_documented_counts.py`
  fails when `CLAUDE.md`, `FORK.md` or `README.md` disagrees with the tools
  actually registered, so these figures cannot silently rot again.

## History

Integrated via PRs #1–#6 on this fork, then synced with upstream on 2026-06-17
(upstream PRs #140/#141/#142, Issues #137/#138/#139), and again on 2026-09-02
(17 upstream commits: 10 new tools, 7 fixes — the first sync after the
stdio/remote split was collapsed, and the first needing no tool migration), on
2026-09-10 (9 upstream commits: 2 new tools, 7 fixes), on 2026-09-19
(3 upstream commits: `get_energy_balance` plus docs), and on 2026-10-14 (54
upstream commits: 4 new tools, tool effect annotations, ~30 data-correctness
fixes; the first sync to route remote registration through `_ToolFilter`), and
on 2026-10-27 (33 upstream commits: 2 new tools, tool bodies moved off the event
loop, tool profiles, bearer auth for upstream's own HTTP mode, ~20 fixes). See `CHANGELOG.md` for a
categorized list of every change relative to upstream, and `README.md` (Remote
Mode, Security, Token import / refresh) for operational detail.
