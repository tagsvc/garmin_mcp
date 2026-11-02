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
- Comments and docstrings say why the code is as it is, in the present tense. History (what it used to do, when or how something was found, dates, PR numbers) goes in CHANGELOG.md and commit messages, never in code. `tests/unit/test_no_history_in_code.py` enforces it; upstream's own lines that trip it are listed, word for word, in its `UPSTREAM_TEXT`.

## After ANY upstream merge or significant change

1. Run the full suite: `uv run pytest -m "not e2e"` — it must pass.
2. Confirm the invariants in `FORK.md` still hold (allowlist fail-closed,
   import-secret gating, `garminconnect==0.3.20` exact pin (never below 0.3.5, CVE-2026-54447), `mcp>=1.30.0`
   floor (CVE-2026-59951), remote server stateless, `auth_tools` stdio-only, `delete_activity` withheld
   from remote, `inspect_garmin` read-only (read-only tools and GET only), recovery hours from `recoveryTime` (never the change phrase), account-unit
   companions added beside metric fields (never replacing them), times as `*_gmt`/`*_local`
   with local from the record's own offset before the profile zone, timed writes
   passed as plain local times (garminconnect places them in the profile zone),
   upstream's direct calls left as upstream wrote them, the remote login on garth,
   no `VOLUME` in `Dockerfile.remote`, `$PORT` handling).
3. Re-enumerate tool counts if registration changed (expect stdio 181 / remote 178).
   Before changing the garminconnect version, run
   `tests/integration/test_garminconnect_client.py` against the new version
   (FORK.md, "What a version change has to keep working").
4. A new read-only tool goes in `tests/integration/test_units_times_sweep.py`'s
   table; a units/times contract failure is fixed, not allowlisted without a reason.
   Every new tool also goes in the profile for its area in `tool_profiles.py`
   (or `NO_PROFILE_TOOLS`, with the reason); `test_tool_profiles.py` fails otherwise.
5. If a tool module changed, ask the user to run
   `uv run python scripts/field_coverage.py` locally (it needs real Garmin
   tokens) and paste the report; review any field a tool newly leaves out.
