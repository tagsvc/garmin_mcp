# Operations — platform configuration that must not regress

`FORK.md` records the invariants that live **in the code**, and CI enforces them.
This file records the configuration that lives **around** the code — GitHub
repository settings and the Railway deployment — which nothing enforces
automatically. A wrong toggle here is as damaging as a bad merge and far quieter:
there is no test that fails when branch protection is switched off.

Read this when: taking over the repo, recovering the deployment, or auditing
whether the platform still looks the way it is supposed to.

> **This file is public.** It records *which* settings exist and why, never their
> values. No secrets, no token material, no deployment URL. Anything sensitive
> belongs in the Railway variable store, not here.

## GitHub repository settings

### Branch protection

A repository ruleset named **`main protection`** targets `~DEFAULT_BRANCH`
(resolves to `main`, no exclusions) with enforcement **active** and an **empty
bypass list** — the rules apply to the repository owner too.

| Rule | Effect |
|---|---|
| `pull_request` | No direct pushes to `main`. Required approvals: **0** (solo repo). |
| `required_status_checks` | `test (3.12)`, `test (3.13)`, `validate`, `test-installation` must pass. |
| `non_fast_forward` | No force pushes. |
| `deletion` | `main` cannot be deleted. |

The four required checks are bound to GitHub Actions as their reporting source
(`integration_id: 15368`), so an outside app cannot satisfy a check by posting a
status under the same name. When adding a required check, pick the entry whose
source is *GitHub Actions*, not the "any source" variant.

This is what upgrades `FORK.md`'s invariants from "CI tells you" to "CI stops
you": a merge that regresses an invariant now cannot land at all.

Two defaults GitHub sets on its own, both deliberate to leave as-is:

- `require_extra_approval_for_unattributed_changes: true` — a PR containing
  commits not attributed to a known account needs an approval regardless of the
  zero-approval setting. Does not fire on ordinary work.
- `strict_required_status_checks_policy: false` — a PR can merge on green checks
  even if `main` moved on since. Enabling it would force a rebase-and-rerun on
  every merge, which buys little on a single-author repo.

### Advanced Security

Settings → Advanced Security. All of these are **on**:

- **Secret Protection** and **Push protection** — push protection is the one that
  matters most here: it blocks a commit containing a recognised secret before it
  reaches the remote.
- **Dependency graph** — off by default on forks, so it must be enabled
  explicitly. Everything below depends on it.
- **Dependabot alerts**, **malware alerts**, **security updates**, **grouped
  security updates**.
- **Private vulnerability reporting** — the repo is public; this gives a reporter
  a private channel instead of a public issue against a server that holds Garmin
  tokens.

Deliberately **off**:

- **Dependabot version updates.** `pyproject.toml` carries intentional
  constraints — `garminconnect==0.3.5` (CVE-2026-54447 floor), `mcp>=1.28.1,<2`
  (2.x renames `mcp.server.fastmcp`), exact pins on `requests` and
  `python-dotenv`. Version updates ignore that intent and would reopen the same
  `mcp` 2.x bump indefinitely. Security updates fire only on real advisories,
  which is the signal worth having. If this is ever turned on, it needs a
  `.github/dependabot.yml` with `ignore` rules for those four.
- **CodeQL** and **AI findings** — optional, not a gap. Note that the **Copilot
  Autofix** toggle is on but inert until CodeQL default setup exists.
- **Automatic dependency submission** — for build-time ecosystems like Gradle;
  irrelevant to uv.

`security.yml` runs `uv lock --check`, so a dependency PR that edits
`pyproject.toml` without regenerating `uv.lock` fails and the ruleset blocks the
merge. The fix is `uv lock` locally, then push to that PR branch.

### Actions

GitHub Actions must be enabled at the **fork** level (forks ship with workflows
disabled). This is a separate switch from workflow *permissions* — having the
permissions page populated does not mean Actions is running. If no workflow run
has ever appeared, that switch is the reason.

## Railway deployment

- **Builder**: `railway.json` pins `Dockerfile.remote` (the HTTP/OAuth server).
  Without the pin Railway auto-detects the bare `Dockerfile`, which runs the
  *stdio* server and cannot serve traffic.
- **Volume**: mounted at `/data`, holding the SQLite database and per-user
  session directories. Railway mounts it root-owned at container start, which is
  why `scripts/docker-entrypoint.sh` chowns it as root and then drops to
  `appuser` via `gosu` (see `FORK.md`).
- **Port**: Railway injects `PORT`; the app binds it automatically.
- **Deploys**: push to `main` triggers a redeploy.

### Environment variables

Names only — values live in Railway. Defaults are in `src/garmin_mcp/config.py`.

| Variable | Required | Notes |
|---|---|---|
| `GARMIN_ALLOWED_EMAILS` | **yes** | Comma-separated allowlist. **Fail-closed**: unset means every login is rejected. |
| `GARMIN_MCP_SERVER_URL` | **yes** | Public URL; used in OAuth metadata and redirects. |
| `GARMIN_IMPORT_SECRET` | for token import | **Fail-closed**: unset disables `/import-token` and the login-page import. |
| `DB_PATH` | no | Defaults to `/data/garmin_mcp.db`. |
| `SESSION_STORAGE_PATH` | no | Defaults to `/data/garmin_sessions`. |
| `GARMIN_MCP_PORT` | no | Overrides the injected `PORT`. Leave unset on Railway. |

The two fail-closed variables are the ones to check first if logins start being
rejected after a config change.

### Backups

The `/data` volume is the only stateful thing in the deployment. Policy:

- A **daily scheduled backup**, plus
- A **locked** manual snapshot as a permanent known-good baseline. Locking
  exempts it from retention pruning, which matters because token-store damage may
  not surface until the next authentication — by then a rolling window can have
  discarded the last good copy.

A healthy snapshot is tens of MB (database + session store). A few KB means it
captured the wrong mount.

**Do not test-restore in place** — Railway's restore replaces the volume, taking
down the live session store to prove a point. The independent second recovery
path is re-importing a token blob via `/import-token`, which rebuilds a session
without the volume at all (see README, "Token import / refresh").

## Verifying the configuration

Branch protection is the one worth checking programmatically, because a disabled
ruleset is indistinguishable from an active one until something tries to violate
it. This endpoint reports the rules **actually in effect** on `main`, regardless
of how they were configured:

```bash
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  https://api.github.com/repos/tagsvc/garmin_mcp/rules/branches/main
```

Expect four entries: `deletion`, `non_fast_forward`, `pull_request`,
`required_status_checks`. An empty array means `main` is unprotected.

```bash
# The ruleset itself — confirm "enforcement": "active" and "bypass_actors": null
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  https://api.github.com/repos/tagsvc/garmin_mcp/rulesets
```

The Dependabot and secret-scanning endpoints need an admin-scoped token; a
read-only token returns `403` rather than reporting them as disabled. Do not read
a `403` there as "the feature is off" — check the settings page instead.
