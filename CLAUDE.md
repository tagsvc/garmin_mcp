# CLAUDE.md

This repository is a **fork** of [Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp)
with custom features (historical analytics, interactive auth tools, an email
allowlist, secret-gated token import, and a Railway remote deployment).

## Read this before changing anything

- **`FORK.md`** documents what this fork adds and the **invariants that must not
  regress**. Read it before merging upstream changes or touching auth/registration/config.
- **`OPERATIONS.md`** documents the platform configuration nothing in the repo
  enforces — the branch ruleset, GitHub security settings, and the Railway
  deployment (volume, env vars, backups). Read it before changing deployment
  config or repository settings.

`main` takes **no direct pushes**: every change goes through a PR, and the four
required checks must pass before it can merge.

## Working in this repo

- Setup: `uv sync`
- Tests: `uv run pytest -m "not e2e"` (integration + unit; e2e needs real Garmin creds)
- Entry points: `garmin-mcp` (stdio), `garmin-mcp-remote` (HTTP + OAuth2), `garmin-mcp-auth` (stdio auth CLI)

## After ANY upstream merge or significant change

1. Run the full suite: `uv run pytest -m "not e2e"` — it must pass.
2. Confirm the invariants in `FORK.md` still hold (allowlist fail-closed,
   import-secret gating, `garminconnect==0.3.5` pin (security floor, CVE-2026-54447), `mcp>=1.30.0`
   floor (CVE-2026-59951), remote server stateless, `auth_tools` stdio-only, `delete_activity` withheld
   from remote, `inspect_garmin` read-only (read-only tools and GET only), recovery hours from `recoveryTime` (never the change phrase), account-unit
   companions added beside metric fields (never replacing them), times as `*_gmt`/`*_local`
   with local from the record's own offset before the profile zone, no `VOLUME` in
   `Dockerfile.remote`, `$PORT` handling).
3. Re-enumerate tool counts if registration changed (expect stdio 175 / remote 172).
4. A new read-only tool goes in `tests/integration/test_units_times_sweep.py`'s
   table; a units/times contract failure is fixed, not allowlisted without a reason.
5. If a tool module changed, ask the user to run
   `uv run python scripts/field_coverage.py` locally (it needs real Garmin
   tokens) and paste the report; review any field a tool newly leaves out.
