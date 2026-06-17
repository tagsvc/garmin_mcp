# Fork notes — what this fork adds and what must be preserved

This repository is a fork of [Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp).
It carries custom work on top of upstream. **When merging upstream changes, the
features and invariants below must not regress.** After any upstream merge, run
the full test suite (`uv run pytest -m "not e2e"`) — every item here has test
coverage.

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

- **`garminconnect==0.3.2` stays pinned** (`pyproject.toml`). The stored-token format
  (di_token / di_refresh_token / di_client_id) depends on this. Do **not** downgrade
  to 0.2.x. If upstream bumps it, re-verify `session_manager` + token import before taking it.
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

## Updating from upstream (sync procedure)

You are never obligated to sync. Pull upstream only when you want a specific fix
or feature. Each sync is a deliberate, tested operation — never a blind merge.

**Fastest path:** open a Claude Code session in this repo and say *"sync upstream
and preserve our changes."* `CLAUDE.md` routes it here; it will do the steps below,
resolve conflicts in favor of these invariants, run the tests, and only land it if green.

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
# review the result, then merge to main and push; Railway redeploys on push to main
```

**Conflict-prone files** (this fork customized them, so upstream edits here often
collide): `src/garmin_mcp/__init__.py`, `remote.py`, `oauth_provider.py`,
`config.py`, `token_utils.py`, `session_manager.py`, `pyproject.toml`,
`Dockerfile.remote`.

**Watch items during conflict resolution:**

- `pyproject.toml` — keep `garminconnect==0.3.2` unless you re-verify token import.
- `__init__.py` / `remote.py` — ensure `analytics` stays registered in both,
  `auth_tools` stays registered in `__init__.py` **only**, and the `/import-token`
  route survives.
- `Dockerfile.remote` — ensure no `VOLUME` instruction was reintroduced.
- `config.py` — keep the `$PORT` fallback, the allowlist, and the import secret.

**Definition of done:** suite green, invariants intact, tool counts stdio 134 / remote 132.

## Expected state after a clean build

- Full suite: `uv run pytest -m "not e2e"` → all pass (354+ at time of writing).
- Tool counts: **stdio 134**, **remote 132** (auth tools are stdio-only).

## History

Integrated via PRs #1–#6 on this fork. See `CHANGELOG.md` for a categorized list
of every change relative to upstream, and `README.md` (Remote Mode, Security,
Token import / refresh) for operational detail.
