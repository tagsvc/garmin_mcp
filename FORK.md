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
| Railway deploy | `railway.json`, `Dockerfile.remote`, `config.py` | `railway.json` pins the Dockerfile builder; `config.port` honors `$PORT`. |

## Invariants that must NOT regress

- **`mcp` stays capped `<2`.** mcp 2.x renames `mcp.server.fastmcp` -> `mcp.server.mcpserver`
  (`FastMCP` -> `MCPServer`); the server is not ported. Don't remove the upper bound
  until that port is done.
- **`garminconnect==0.3.5` stays pinned** (`pyproject.toml`). The stored-token format
  (di_token / di_refresh_token / di_client_id) depends on the 0.3.x line. Do **not**
  downgrade to 0.2.x, and do not go below 0.3.5 (CVE-2026-54447: ≤0.3.4 writes the
  token store world-readable). If upstream bumps it, re-verify `session_manager` +
  token import before taking it.
- **Allowlist is fail-closed.** An empty/unset `GARMIN_ALLOWED_EMAILS` rejects every login.
- **Token import is fail-closed and secret-gated.** `GARMIN_IMPORT_SECRET` (constant-time
  compare) is required on both the login-page import and `POST /import-token`; unset disables import.
- **No live-Garmin validation of imported tokens.** Validation is structural only — a live
  check would make the server a good/bad token oracle and re-introduce the 429 surface.
- **`auth_tools` is stdio-only** — registered in `__init__.py`, never in `remote.py`.
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
`Dockerfile.remote`.

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

**Definition of done:** suite green, invariants intact, tool counts stdio 150 / remote 148.

## Expected state after a clean build

- Full suite: `uv run pytest -m "not e2e"` → all pass (604 at time of writing).
- Tool counts: **stdio 150**, **remote 148** (auth tools are stdio-only).

## History

Integrated via PRs #1–#6 on this fork, then synced with upstream on 2026-06-17
(upstream PRs #140/#141/#142, Issues #137/#138/#139). See `CHANGELOG.md` for a
categorized list of every change relative to upstream, and `README.md` (Remote
Mode, Security, Token import / refresh) for operational detail.
