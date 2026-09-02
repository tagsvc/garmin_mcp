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

- **Dependabot version updates for `pip`.** `pyproject.toml` carries intentional
  constraints — `garminconnect==0.3.5` (CVE-2026-54447 floor), `mcp>=1.28.1,<2`
  (2.x renames `mcp.server.fastmcp`), exact pins on `requests` and
  `python-dotenv`. Version updates ignore that intent and would reopen the same
  `mcp` 2.x bump indefinitely. Security updates fire only on real advisories,
  which is the signal worth having. If pip version updates are ever turned on,
  they need `ignore` rules for those four.

- **CodeQL** and **AI findings** — optional, not a gap. Note that the **Copilot
  Autofix** toggle is on but inert until CodeQL default setup exists.
- **Automatic dependency submission** — for build-time ecosystems like Gradle;
  irrelevant to uv.

Deliberately **on**, and the one exception to the version-updates line above:

- **Dependabot version updates for `github-actions`**, via
  `.github/dependabot.yml` (monthly, grouped into one PR). That ecosystem holds
  no deliberate pins, so there is nothing for Dependabot to fight. Without it,
  every GitHub runner-runtime deprecation means hand-bumping ten `uses:` lines
  across three workflows — which is what prompted the file. Its PRs still have
  to clear all four required checks.

### How dependency scanning actually works

Two layers, deliberately overlapping, because they fail differently:

| | Dependabot | `pip-audit` in `security.yml` |
|---|---|---|
| When | Asynchronous — after the fact | Synchronous — on the PR that introduces it |
| Source | GitHub dependency graph | PyPI advisory database, directly |
| Output | Dashboard alert + a fix PR | Red check on the PR |
| Scope | Full graph, including dev | Locked **runtime** set (what ships) |

`pip-audit` runs against `uv export --frozen --no-dev` — the same export
`Dockerfile.remote` installs from — so it audits precisely what is deployed
rather than a fresh resolve. `security.yml` also runs `uv lock --check` first,
since auditing a lock file that no longer matches `pyproject.toml` would scan the
wrong set.

**These are not required checks.** `security.yml`'s jobs (`dependency-check`,
`code-quality`) report on the PR but do **not** gate the merge — only
`test (3.12)`, `test (3.13)`, `validate`, and `test-installation` do. That is
deliberate: making an advisory-database lookup a merge requirement means a newly
published advisory with no fix available blocks every unrelated PR until someone
intervenes. A red `dependency-check` is a stop-and-look signal you have to
actually look at, not an automatic block.

If an advisory has no fixed version, add an explicit `--ignore-vuln <ID>` to the
audit step with a comment explaining why, rather than weakening the step. The
exception then lives in version control as a decision someone made.

A dependency PR that edits `pyproject.toml` without regenerating `uv.lock` fails
the lock check. The fix is `uv lock` locally, then push to that PR branch.

### Actions

GitHub Actions must be enabled at the **fork** level (forks ship with workflows
disabled). This is a separate switch from workflow *permissions* — having the
permissions page populated does not mean Actions is running. If no workflow run
has ever appeared, that switch is the reason.

**`security.yml` needs a second, separate enable.** GitHub disables workflows
containing a `schedule:` trigger in forked repositories, and it disables the
*whole workflow* — not just the scheduled run. `security.yml` is the only one of
the three with a `schedule:`, which is why it alone sits in state
`disabled_fork` while `ci.yml` and `pr-validation.yml` run normally. The effect
is silent: no run appears, no failure appears, and the PR checks look complete
because the other two workflows reported.

Enable it at **Actions → Security Checks → Enable workflow** (one click; it then
stays enabled). Verify with:

```bash
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  https://api.github.com/repos/tagsvc/garmin_mcp/actions/workflows \
  | grep -E '"(name|state)"'
```

All workflows should read `"state": "active"`. Anything reading
`"disabled_fork"` is not running, whatever its file says. Note that enabling
requires an admin-scoped token; a read-only one returns `403` on the enable
endpoint.

GitHub also auto-disables scheduled workflows after 60 days without repository
activity, so a long-dormant period can put this back to `disabled_fork` — worth
re-checking after any long gap.

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
