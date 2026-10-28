# Changelog

All notable changes **this fork** makes relative to its upstream base,
[Taxuspt/garmin_mcp](https://github.com/Taxuspt/garmin_mcp). See `FORK.md` for the
invariants behind these and the upstream-sync procedure. The authoritative diff is
`git diff upstream/main...main` once the upstream remote is wired.

## Current recovery no longer "unavailable" in the evening — 2026-10-28

Right after the recovery fix went live, `get_recovery_time_remaining` called
without a date returned `state: "unavailable"` at 8:20 PM in New York, while a 72h
snapshot from that afternoon was current. With no date, it looked up the
**server's** date (upstream #469: "Today uses the server's local date"). Railway
runs on UTC, so from 8 PM Eastern (7 PM in winter) until midnight it is already
tomorrow there; and overnight, until the wake-up snapshot exists, "today" has
none either.

**Fix:** with no date, the tool now reports the most recent snapshot, on whatever
day it was taken, marked `resolved_as: latest_snapshot` — consistent with how
Garmin works: a snapshot holds until the next wake-up, activity or nap. Snapshots
are filed under the athlete's local calendar date, which only moves forward and
is always within a day of UTC, so it checks UTC tomorrow, today and yesterday,
newest first, and takes the first with snapshots. No timezone lookup, and no
assumption about where the server or the athlete is; a date in the athlete's
future simply comes back empty (verified live). A failed lookup on a newer date
is reported in `source_errors`, since the result may then not be the latest.
Only if nothing is found within a day does it fall back to upstream's
activity-based estimate. An explicit `date` behaves as before.

Tests: the live evening case (00:20 UTC = 20:20 EDT), daytime, newest date
winning, an athlete east of UTC found on UTC tomorrow, a failed newer lookup,
nothing in range, and the tool's no-date call — which fails on the old default.
Suite: 1459 passed.

## mcp 1.30.0 and certifi 2026.10.23; remote runs stateless — 2026-10-28

Five advisories published after the 2026-10-14 audit, all already present on
`main` (none introduced by the 2026-10-27 sync):

| package | advisory | reaches this server? |
|---|---|---|
| mcp 1.29.0 | CVE-2026-59951 — stateful Streamable HTTP never reclaimed sessions | **yes**: remote ran stateful Streamable HTTP. OAuth limits who can open sessions, not how many a token holder can |
| certifi 2026.7.22 | PYSEC-2026-4320 — compromised emSign root CA | **yes**: every outbound Garmin call trusts the bundle (exploitation needs an on-path attacker) |
| mcp 1.29.0 | CVE-2026-116684 — bearer token audience unchecked | no: combined authorization + resource server, issuing tokens only for itself |
| mcp 1.29.0 | CVE-2026-116685 — client follows cross-origin redirects | no: client-side; the server is never an MCP client |
| mcp 1.29.0 | CVE-2026-116686 — client fetches output-schema `$ref`s | no: client-side |

**Fix:** `mcp` 1.30.0 (floored in `pyproject.toml`) and `certifi` 2026.10.23.
`remote.py` now passes `stateless_http=True`: no sessions are kept, so there is
nothing to accumulate, and 1.30.0's 30-minute idle expiry — which upstream saw
make claude.ai report the first call after a quiet spell as failed — cannot
occur. No tool uses session state; callers are identified per request by bearer
token.

**Verified before committing,** on 1.30.0:

- `oauth_claude` monkeypatches SDK internals, the main risk in an SDK bump. Over
  HTTP against the real app: the authorization-server metadata still advertises
  `none`, an unauthenticated `/mcp` request still gets 401 with the
  `resource_metadata` challenge, a bad token gets `invalid_token`, and the
  security headers are present.
- A new end-to-end test, `tests/integration/test_remote_end_to_end.py`, boots
  the real remote `main()` in a subprocess and drives authenticated MCP requests
  — two users, four concurrent sessions, one tool through `get_client(ctx)` and
  one through the module-level proxy — through the OAuth middleware, transport,
  worker threads and client resolution. Each caller got only their own data;
  handing every caller the same client fails it, and so does turning stateless
  off. It also pins both claude.ai OAuth behaviours above.

`test_project_dependencies` now checks the `<2` cap and the 1.30.0 floor rather
than one literal constraint string. Suite: 1452 passed; `pip-audit` clean.

## Recovery hours no longer zeroed by Garmin's change phrase — 2026-10-27

**72 hours of recovery was reported as 0.** After a run, Garmin Connect showed
"Recovery Time 72h, High Need" while `get_training_readiness` reported
`recovery_time_hours: 0.0` and `get_recovery_time_remaining` reported
`remaining_hours: 0.0`, `state: "recovered"`. Anything reading those tools would
have cleared a hard session the day after hill repeats.

The cause came in with upstream's recovery-time feature (`f4fc10b`) and is still
present upstream after #469: when a snapshot's `recoveryTimeChangePhrase` was
`REACHED_ZERO`, the hours were set to 0 whatever `recoveryTime` said, on the
premise that Garmin then keeps a stale minute value.

The real payload, captured from Garmin Connect's web client, shows otherwise:

| snapshot | `inputContext` | `recoveryTime` | phrase | recovery factor |
|---|---|---|---|---|
| 08:03 | `AFTER_WAKEUP_RESET` | 1 min | `NO_CHANGE_SLEEP` | 99 (GOOD) |
| 15:01 | `AFTER_POST_EXERCISE_RESET` | **4320 min = 72h** | `REACHED_ZERO` | 10 (POOR) |

The phrase describes what changed at the event that produced the snapshot — the
morning's last minute running out before the run — not the value now. When
recovery has drained, Garmin sends the low value itself. Garmin Connect has no
display text for the phrase and shows hours from `recoveryTime`. The athlete
confirmed waking with zero recovery needed, so the morning value was right both
before and after this change.

**Fix:** recovery hours always come from `recoveryTime`, and the coarse `state`
from the hours alone. The phrase is still reported as `change_phrase`.

**Also:** `recovery_score` is renamed `readiness_score` — it was always the
Training Readiness score (20 on a day with 72h to go). `get_recovery_time_remaining`
now reports `input_context`, the event behind the snapshot: Garmin recomputes
recovery at wake-up, after activities and after naps, and the value holds until
the next one. It is deliberately not decayed into a live countdown between
events, since Garmin revises it at each.

Tests: two built from the real snapshots (IDs replaced), confirmed to fail on the
old rule with `0.0 == 72.0`; upstream's tests that encoded the rule now assert the
phrase is ignored. Suite: 1447 passed.

## Upstream sync 2026-10-27 — 33 commits

Syncs Taxuspt/garmin_mcp through `a2ca5fc` (2026-10-14 .. 10-20).

**Taken from upstream:** `push_workout_to_device` and `reschedule_workout`;
confirmation previews on `delete_weigh_ins`, `delete_custom_food` and
`delete_food_log`; ~20 correctness fixes, including zero values no longer
dropped from training metrics, `get_endurance_score` no longer crashing on a day
without a score, VO2 measurement dates kept, Garmin HTTP failures classified
before hints are added, cycling data kept in analysis, and GPX coordinates
written as plain decimals; auth robustness (saved tokens survive a failed
optional export, `garmin-mcp-auth --diagnose`); recovery snapshots labelled with
`as_of` and `value_type` (#469); tool profiles (`GARMIN_TOOL_PROFILE`) and bearer
auth for upstream's own single-user HTTP mode.

**Not fixed upstream, and not by this sync:** `get_training_readiness` and
`get_recovery_time_remaining` report 0 recovery hours whenever Garmin's
`recoveryTimeChangePhrase` is `REACHED_ZERO`, discarding the real value. Seen
live on 2026-10-27: 72h reported as 0h after a run. Fixed separately.

**Worker threads and per-caller identity.** Upstream now runs every tool body on
an anyio worker thread inside a fresh event loop (#449), and the remote server
registers through the same `_ToolFilter`, so every remote call now does too. The
remote server identifies the caller from a per-request context variable, which
has to cross both hops. Tested before merging: four concurrent calls from two
callers each saw their own token, while a plain `threading.Thread` lost it. A new
contract test pins this and was confirmed to fail on the plain thread.

**Merge breakage git did not report**, fixed by adopting upstream's text:
`get_power_duration_curve` kept a call to a local `client` that upstream's side of
a hunk had removed (`NameError` on every power-curve request). Upstream also
dropped the `client` argument from `_get_rider_weight_kg` and
`_get_activity_type_mapping`; all callers were aligned and checked with a
call-arity scan.

**Two upstream tests adjusted for this fork:**

- `test_garmin_proxy` expected garminconnect 0.3.2's exception type for a 401 or
  429. Under the 0.3.5 pin the library already narrows those to
  `GarminConnectAuthenticationError` and `GarminConnectTooManyRequestsError`, so
  the expected type is now a parameter. The proxy's hints were already correct.
- `test_live_auth_is_excluded_from_ci` flagged two upstream modules as doing a
  real Garmin login. Both build a credential-free `Garmin()` and mock its
  transport on the next statement, so they never contact Garmin. The detector
  now exempts exactly that pattern and nothing else (parametrized cases pin it),
  rather than those tests being pushed out of CI.

Kept from this fork: the `garminconnect==0.3.5` pin, remote-mode GPX handling
(upstream's decimal fix ported into it), stdio-only auth tools and
`delete_activity`. Tools: stdio 172 → 174, remote 169 → 171. Tests: 1077 → 1445.

## A cancelled Coach plan no longer reads as completed; course activity type — 2026-10-14

**`get_coach_plans` reported a quit plan as `"status": "completed"`.** The first
cancelled plan these tools have seen (1789330190, quit before its 2026-11-26
race day) showed Garmin files quit plans under its `/completed` list and sets
`planCompleted: true` on them; only a non-null `quit` tells them apart. The tool
took its status from which list a plan came from, so a cancelled plan read as
finished. It now reports `active`, `completed`, `quit`, or `ended` — the last
when a plan is over but `quit` cannot be read, rather than guessing "completed".
`quit` is read from the list entry when present; the list's shape is unverified
(its fixture is a trimmed sample), so otherwise it comes from the plan-detail
payload, where it was observed. `get_coach_plan_details` now documents that its
`plan_completed` is also true for a quit plan.

**`get_course_details` returned `activity: null`** for a course `get_courses`
lists as running. The list carries an `activityType` object; the detail
evidently does not, and the course save response carries a numeric
`activityTypePk` instead. The tool now reads either shape, adds the raw
`activity_type_id`, and names the type only when the id is one `upload_course`
knows. Which shape the detail payload uses is confirmed once this deploys.

The pace-unit note now records that the plan was quit, so the unit can no longer
be settled from this account.

## oauthlib 3.3.1 → 4.0.0 (CVE-2026-49265) — 2026-10-14

The weekly audit went red on `oauthlib 3.3.1` (PYSEC-2026-4114 /
CVE-2026-49265): a timing side-channel in oauthlib's OAuth2 authorization-server
PKCE check. Already present on `main` before the 2026-10-14 sync — a newly
published advisory, not a regression. This server never reached the flawed code
(oauthlib arrives only through garth → requests-oauthlib, as an OAuth1 *client*),
but the fix is now taken rather than excepted.

A major version on the Garmin login path looked risky, and an exception was
drafted on that basis. Diffing the two releases showed otherwise, so it was
withdrawn before review:

- every module on garth's OAuth1 client path — `oauth1/rfc5849/__init__.py`
  (`Client`), `signature.py`, `parameters.py`, `utils.py` — is **byte-identical**
  between 3.3.1 and 4.0.0;
- the only other change garth's path touches is one added `"resource": None`
  default in `common.Request`, an OAuth2 parameter OAuth1 signing never reads;
- both of 4.0.0's breaking changes are in OAuth2 provider code nothing here
  loads.

Confirmed end to end: the same OAuth1 request, signed through `requests_oauthlib`
with a fixed nonce and timestamp, produces an identical `Authorization` header
under both versions. Suite green; the audit is clean with no exception.

The audit policy in `security.yml` and `OPERATIONS.md` now also says to prefer
the fix and to diff a release over the paths this repo runs before calling a
bump risky — and that any exception must state what ends it.

## Upstream sync 2026-10-14 — 54 commits; courses fixed; remote gets annotations

**Course viewing was broken on every course.** `get_course_details` and
`download_course_gpx` read `data.get("coursePoints", [])`. The default only
applies when the key is *absent*; Garmin sends `"coursePoints": null` for a
course with no custom waypoints, so both tools crashed with `'NoneType' object
is not iterable`. Reproduced live on all four courses on the account, including
one from 2013. Fixed upstream (`c9cccf5`, their #377) and taken here.
`upload_course` also saved 5 of its 8 activity types under the wrong Garmin
sport (walking as "other", road biking as street running); fixed in `3c24deb`.

**Taken from upstream:** 4 new tools (`delete_activity`, `set_activity_exercise_sets`,
`create_run_interval_workout`, `get_recovery_time_remaining`); tool effect
annotations; ~30 correctness fixes, several of which change figures already
being read (lactate-threshold speed units, calendar `distance_meters`, morning
readiness fields, intensity-minute totals, bike power counted in running power
curves, wrapped-around negative lap distances, record-timestamp analysis); a
"may have committed" report for a 5xx after a write; stricter date validation;
full workout-library paging; urllib3 2.8.0 and PyJWT 2.15.0.

**Kept from this fork:** the `garminconnect==0.3.5` pin (upstream is on 0.3.2);
the remote-mode GPX path handling (see below); the stdio-only auth tools. Upstream
replaced the `.dockerignore` → `.gitignore` symlink with a standalone file; taken,
with this fork's exclusion of captured live-Garmin fixtures (personal data) and
`.mcp.json` restored, since the stdio `Dockerfile` copies `tests/`. Railway's
`Dockerfile.remote` copies only `src/` and the lock, so the deploy was unaffected.
Upstream's `0273d07` independently made the same Coach-curation fix as this
fork's 2026-09-19 change, so upstream's text was taken and the region no longer
diverges.

**Remote now registers through `_ToolFilter`, the wrapper stdio uses.** Before,
`remote.py` registered on the bare FastMCP app, which meant two upstream fixes in
this range would never have reached the Railway server:

- no effect annotations, so claude.ai could not tell `delete_workout` from
  `get_activities`;
- FastMCP's automatic structured output, which re-wraps every JSON string a tool
  returns as `{"result": "<escaped json>"}` and doubles the payload (upstream
  `8163e3a`, their #331).

The new `remote.register_remote_tools()` is the single place the remote tool set
is defined; `main()` and the tests both call it, so documented counts are what is
served.

**`delete_activity` is stdio-only.** Irreversible, and an activity is training
history that Coach grades and every load and analytics figure depends on. It is
withheld through `REMOTE_EXCLUDED_TOOLS`, which fails closed: a name matching no
tool raises at startup rather than letting a renamed tool through.

**GPX download ordering.** Upstream's `download_course_gpx` resolves the output
path and runs `os.makedirs()` before anything else. Merged naively, that would
create directories on the server's disk in remote mode even with no
`output_path`. The remote check now runs first, and remote returns the GPX
inline as before. The JSON `name` is now the plain course name; it was
previously XML-escaped (`Rock &amp; Roll`).

**Merge hazards caught that git did not report.** Taking upstream's side of a
hunk that changed a helper's signature (`_is_already_scheduled`,
`_put_activity_update`) or dropped a local our code still used
(`schedule_week`'s `client`) left code *outside* the hunk inconsistent with it —
a clean merge that raises `NameError` or `TypeError` on first call. Before
repair that broke `schedule_week`, `schedule_workouts`, and the activity edits
that go through `_put_activity_update` (description, event type, feel and
perceived effort). Found by comparing every merged function against both
parents and by a call-arity check over the package, and fixed by adopting
upstream's callers together with each helper.

**Tests that could not fail, made able to.** The DXT manifest test compared the
manifest against the generator, which both read one hand-kept module list, so it
passed while omitting this fork's `analytics` and `auth_tools`; a new test pins
that list to `main()`. A new remote contract test first compared the withheld
set against `REMOTE_EXCLUDED_TOOLS` itself, and so passed with the exclusion
emptied; it now asserts a literal.

Tools: stdio 168 → 172, remote 166 → 169. Tests: 783 → 1072.

## Documented what the new Coach tools actually return — 2026-09-19

A review from a parallel session found a behaviour worth knowing and three
claims that outran the evidence. No tool behaviour changed; the documentation
now matches what the endpoints do.

**The finding: the workout feed under-counts a plan week.** An adaptive plan
reserves days before generating workouts for them. `get_garmin_coach_workouts`
returns only generated ones; `get_coach_plan_progress` also returns the reserved
days — a `date` and `completed: false` with no ids, the "Stay Tuned for Details"
entries in Garmin's app. Over 2026-09-13..09-26 that is 7 entries against 5.
This is the only way to see a week's true shape, and nothing said so. Now in the
docstrings of both tools, in `FORK.md`, and pinned by a test.

It falls straight out of the implementation — `completed` is a bool so `False`
survives the strip-`None` pass while the null ids do not — but it was not
anticipated when the tool was written.

**Three claims narrowed:**

- `performanceRating` — the docstring asserted it measures execution against the
  prescription. That was inferred from two identical `GOOD` samples. It now says
  only that it is a per-workout rating from Garmin Coach, with the scale
  explicitly unobserved.
- `performed_at` — confirmed device-local with no zone designator, byte-identical
  to the activity's `start_time_local` and four hours behind `start_time_gmt`.
  Documented, since appending `Z` moves an evening session to the next day.
- `pre_plan_training_pace_seconds` — Garmin sends a bare number, and it stays
  **formally unresolved**. Three earlier notes here have been withdrawn.

  **Only registration-day data counts.** A value captured 2026-09-13T20:09:50Z
  cannot have been computed from a session on the 14th or the 17th. Applying
  that cut removed an argument from *each* side: the per-kilometre reading
  rested on a 09-17 composite, and the per-mile reading rested on the 09-14
  benchmark interval. Measured across the four sessions that did exist —
  09-02, 09-07, 09-09, 09-11:

  | | per session | per interval |
  |---|---|---|
  | run segments | 336.8–397.7 s/km | 308.6–424.5 s/km |
  | walk segments | 710.3–733.1 s/km | |
  | whole activity | 551.1–588.0 s/km | |

  Read as **seconds-per-mile**, 617 is 383.4 s/km — inside both run bands.
  Read as **seconds-per-kilometre** it is 16:33/mile — above every run figure
  by 45% or more and above every composite by 4.9% or more. It matches nothing
  that existed when it was captured. The asymmetry is the argument: landing
  inside a 116-second band does not prove per-mile, but fitting nothing at all
  sinks per-kilometre.

  **A second check, independent of activity data.** The goal is 31:00 over 5K,
  or 372.0 s/km. Against a per-mile baseline that is 3.0% faster — an ordinary
  ten-week target. Against a per-kilometre baseline it is 39.7% faster. Garmin
  publishes `confidence` 78 for that pairing, and the number is Garmin's own,
  computed from baseline and goal together. No coaching system rates a 40%
  improvement in ten weeks at 78. Because this reads two fields of one payload,
  no stale or misdated snapshot can spoil it.

  **Provenance is open, and a note claiming otherwise was wrong.** It argued
  these fields must be self-reported because `pre_plan_weekly_mileage` is 0
  while the account had logged runs — "no derivation rounds that to zero". The
  athlete has since confirmed he entered 0 himself, so the zero is his input
  and says nothing about Garmin's method. He also reports entering nothing for
  pace, which leaves **defaulted or derived**. Derived favours per-mile, per
  the bands above; a default favours per-mile too, since 10:17/mi is a
  plausible beginner default and 16:33/mi is not a pace anyone would be
  defaulted to. Both surviving branches point the same way.

  It stays unresolved because no branch is directly observed — **not** because
  the readings are balanced. They are not, and nothing currently argues for
  per-kilometre. If the setup flow did ask for pace and he simply does not
  recall, "entered" returns and the derived argument weakens. Flipping display
  units will not settle it: a registration-captured value does not change with
  display preference.

**And one question answered:** `protected` (workout feed) and `benchmark` (ATP
calendar) are different fields from different services — neither sends the
other's key. They coincided on the one benchmark observed, which is not evidence
they are the same flag.

Tests: +2. Result: 783 passed.

## Three tools from captured traffic — 2026-09-19

Built from a second HAR of Garmin Connect's own web client, this time with the
plan detail page open. Every fixture in the tests is a real response body.

**`get_coach_plan_progress`** — the significant one.
`/atp-api/atp/athlete/calendar` returns a **`performanceRating`** for each
completed workout: Garmin Coach's own assessment of how the session was executed
against what it prescribed. Both of this account's completed sessions came back
`GOOD` / "Good Job".

That rating is not the watch's training effect, which measures physiological
load. It is adherence to the prescription, and it exists nowhere else in this
server — not in the GraphQL workout feed, not in the activity summary. The same
endpoint also flags `benchmark` workouts, the plan's own checkpoints, and
carries the `activityId` link, so one call covers prescribed, performed and
graded over any date range.

**`get_coach_plans`** — properly replaces the `get_training_plans` deleted this
morning. `/atp-api/atp/athlete/active` and `/completed` return full plan objects,
so a plan id can be discovered without first fetching a week of workouts.
Completed plans are labelled and can be skipped.

**`get_sensors`** — `/device-service/sensors` carries battery level, last
connection and firmware for paired straps and pods. `get_devices` returns names,
models and serials but no battery state, which is the thing worth checking before
a session. Garmin lists the same sensor once per paired device, so entries are
de-duplicated by serial.

**Not built**, having seen their payloads: `atp/athlete/feed` (a timeline whose
tips are Contentful ids needing a second lookup to become readable, and whose
graded entries duplicate the calendar), `atp/athlete/nextWorkout` (subsumed by
the calendar), `atp/types` and `atp/setup/plans/change` (browse-once data for
choosing a plan, not for carrying in every session).

Tests: +12, all fixtures captured rather than invented. Result: 781 passed.
Tool counts stdio 168 / remote 166.

## get_coach_plan_details — the plan endpoint that actually works — 2026-09-19

Captured Garmin Connect's own network traffic to find what the web client calls,
rather than guessing at endpoints again. Three things came out of it.

**The removal was right, now proven from the browser.**
`GET /gc-api/trainingplan-service/trainingplan/plans?limit=50` returns
`{"trainingPlanList": []}` from a fully authenticated browser session with the
plan active. That service lists structured/phased plans; it does not know about
adaptive ones. The tool deleted this morning was calling a working endpoint that
simply has nothing to say about Garmin Coach.

**The real endpoint is on a different service prefix.**
`GET /atp-api/atp/athlete/plan?lang=en&athletePlanId=<id>` — `atp-api`, not
`gc-api/trainingplan-service`. That is why the old tool 404'd: right concept,
wrong service. `connectapi()` reaches it unchanged, verified against the live
plan. New tool `get_coach_plan_details` returns what the workout feed does not:

```
goal_time (31:00) and goal_type      confidence (Garmin's own 0-100 estimate
workout_days, long_run_day            that the goal will be met, which moves
coach_key, race_event_id              as you train)
pre_plan_weekly_mileage / pace        paused / quit
```

**The plan name was never a field.** The web client assembles "5K Plan with
Coach Jeff" client-side from the race distance and a second GraphQL endpoint —
`POST /gateway/graph-atp/graphql`, distinct from the `trainingPlanScalar` one
this fork uses — which resolves `coachKey: "JeffGalloway"` to a first and last
name. Earlier sessions spent effort looking for a `planName` that does not exist
server-side.

`userPk` is deliberately not exposed, as with `ownerId` earlier today: account
identifiers with no analytical value. Tests assert both stay out.

The fixture is the captured payload verbatim, and a test asserts the tool calls
`atp-api` and *not* `trainingplan-service` — pointing it back at the dead service
would reproduce exactly the bug that got three tools deleted.

Tests: +6. Result: 769 passed. Tool counts stdio 165 / remote 163.

## Coach workout curation: plan linkage, and prescribed vs performed — 2026-09-19

A second audit, one level below the plan-level fix earlier today. It found much
less, which is the useful result: at the workout level the curation was already
reading the right key names.

**`workoutPhrase` is not a bug.** The workout's training intent
(`AEROBIC_BASE`, `ANAEROBIC_SPEED`) is read correctly — Garmin simply leaves it
null on an ATP plan, along with `estimatedDurationInSecs`,
`estimatedDistanceInMeters`, `tpPlanName`, `tpType` and `workoutUuid`. Nothing
to fix; the data is not sent.

**One real gap.** Garmin carries the plan id in a different field per plan
family — `atpPlanId`, `itpPlanId`, `fbtAdaptivePlanId`, `selfGuidedPlanId`. The
curation read two of them, and neither is the one an adaptive plan uses, so
`training_plan_id` was null on every workout of a Coach plan. Now falls back
across all four.

**Two fields added:**

- **`performed_at`** (`associatedActivityDateTime`) — when the workout was
  actually done, against `scheduleDate` for when the plan asked for it. The gap
  between the two is what adherence over a twelve-week plan is made of, and it
  was not previously visible.
- **`protected`** — Garmin will not reshuffle a protected workout when the plan
  adapts, so it marks a fixed point (a benchmark, or the race). Emitted only
  when true; most workouts are unprotected.

**Deliberately not exposed:** `ownerId`, an account identifier with no
analytical value, asserted absent by a test.

The fixture in `test_coach_plan_curation.py` is now the live payload verbatim —
two real workouts, one completed and protected, one upcoming — so the nulls that
make this fragile are pinned alongside the values.

Tests: +4. Result: 763 passed.

## Upstream sync 2026-09-19 — 3 commits, 1 new tool

**`get_energy_balance`** — derives a composition-based TDEE from logged intake,
Garmin's expenditure model, and weight change over a date range. It completes
work taken in the previous sync: `get_stats_range` and
`get_nutrition_summary_between_dates` supply exactly the two inputs it needs,
so the fork now has the whole chain rather than the raw materials. 426 lines
with 422 lines of tests.

Two docs commits: running multiple Garmin accounts via `GARMINTOKENS`, and a
Windows Smart App Control troubleshooting note.

**The conflict was the documented one**, resolved from `FORK.md`'s recipe rather
than re-derived: upstream inserts the new tool immediately before
`get_user_summary`, whose signature this fork changed to thread `ctx`, so the
boundary collides. Take upstream's block, drop its last line, restore our
signature. Third sync running with this shape; writing it down turned a careful
read into a rote edit.

The documented-count guard fired again, correctly — it caught `CLAUDE.md` and
`FORK.md` after the README had already been updated by hand. Worth noting its
own second-order effect: the test total it reports moves once the count tests
themselves go from failing to passing, so the figure written into `FORK.md` has
to be the post-fix one (759, not the 757 seen while they were red).

Result: 759 passed. Tool counts stdio 164 / remote 162.

## Coach plan tools: three removed, one fixed — 2026-09-19

An enrolled Garmin Coach plan finally existed to test against, and it showed the
three training-plan tools added on 2026-09-02 were broken — exactly the risk
flagged when they shipped, and for exactly the predicted reason.

**Removed.** Built on `trainingplan-service` REST, which does not serve an
enrolled adaptive plan:

- `get_training_plans` → `/plans` returned an **empty list** while plan
  `1789330190` was active. It reported "you have no plans", which is worse than
  no tool: an error is a signal, an empty list is a wrong answer.
- `get_adaptive_training_plan_details` → `.../fbt-adaptive/{id}` returned
  **404** for a plan id taken from the working feed.
- `get_training_plan_details` → same dead family, untested.

**Fixed, and this is where the data actually was.** Upstream's
`_get_garmin_coach_workouts` reads the plan's `trainingPlanDetailsDTO` but picks
only `trainingType` — a key adaptive plans do not have. It resolved to `None`,
the strip-`None` filter dropped it, and the entire DTO went with it. The tool
could return nothing but an id and a classification.

The DTO in fact carries the plan's goal and shape, now surfaced:

```
race_name, race_day, workouts_per_week,
remaining_workouts_this_week, registration_date, plan_completed
```

For this account that is a 5K on 2026-11-26, three sessions a week — the thing
an adaptive plan is *for*, previously discarded on every call.

**Upstream's own test proved the bug.** Its fixture contains
`workoutsPerWeek: 4`, and its assertion expected that field to be absent from
the output. Surfacing the real fields made that assertion fail — upstream was
asserting its own data loss. The expectation was corrected rather than silenced.

`tests/integration/test_coach_plan_curation.py` pins the curation against the
live ATP payload verbatim, including the two facts that make it fragile:
`planName` is `None` for adaptive plans, and there is no `trainingType` key.
Verified non-vacuous by restoring upstream's curation, which fails it on
`KeyError: 'race_name'`.

Tool counts stdio 166 → 163 / remote 164 → 161. Result: 746 passed.

## Availability audit: what is left to pull in — 2026-09-10

Surveyed every source this fork draws from, to answer whether anything remains
worth taking.

- **Both parent forks are fully absorbed.** `Tomas2D/garmin_mcp` (last commit
  2026-06-04) and `coloboxp/garmin_mcp` (2026-05-27) each have **zero** commits
  not in our `main` and **zero** tools we lack — 124 and 122 against our 166.
  Nothing to take, and both look dormant.
- **Upstream is current** — 0 commits behind after today's sync.
- **The library is eight releases behind, and staying there.** We pin
  `garminconnect==0.3.5`; latest is `0.3.13`, which adds 16 methods and removes
  none.

**The pin invariant was understated and is now corrected.** It said the stored
token format "depends on the 0.3.x line", which implies any 0.3.x is safe. It is
not: **0.3.13 drops `garth` entirely**, replacing it with `curl_cffi` +
`ua-generator`. Garth is this fork's whole auth substrate — `garth.dump()` in
`session_manager`, the `OAuth1Token`/`OAuth2Token` handling, the
`di_token`/`di_refresh_token`/`di_client_id` blob the token-import page accepts,
`GarthHTTPError` in `auth_tools`, and the 429 fail-fast client that patches
garth's retry `status_forcelist`. A bump past the last garth-based release is an
authentication-layer rewrite, not a version change, and would likely invalidate
every session already stored on the `/data` volume.

The 16 new methods do not justify that. The appealing ones —
`push_workout_to_device`, `update_workout`, `get_hrv_data_range` — are
conveniences over capabilities this fork already has.

**A note on how this was found.** The first comparison reported `get_spo2_data`
as removed in 0.3.13, which would have been a blocker. It was not removed; the
method list had been filtered through `^[a-z_]+$`, which excludes digits, so
`spo2` was silently dropped from one side and read as a removal. A second filter
in the same investigation (`grep -v "^ "`) ate the indented answer to a different
question. Both produced confident, wrong output rather than an error — the same
shape as the placeholder scanner, the workflow that never ran, and the test that
swallowed every exception.

## Upstream sync 2026-09-10 — 9 commits, 2 new tools

**New tools:** `get_stats_range` (multi-day calorie/step totals, 28-day cap) and
`get_nutrition_summary_between_dates` (multi-day intake totals, 61-day cap).
Both replace "call the single-day tool once per day" loops.

**Fixes taken:** nightly HRV and period averages now populate; a night with null
sleep-phase breakdowns no longer crashes; nutrition goals read `calorieGoal` and
nested `macroGoals` rather than the old flat fields, and verify the write;
progress-summary calorie units corrected with misleading fields dropped; strength
workouts compare exercises by index for the rest-step skip; Garmin exception
translation keeps the original message; `endConditionZone` documented.

**The documented-count guard fired on its first sync**, which is what it was
added for. Four tests failed the moment the merge landed — `CLAUDE.md`,
`FORK.md`, and both README count claims — because the tool total moved 164 → 166
and nothing in the merge updates prose. Previously that drift was silent and
accumulated for months; here it was a red suite before the branch was pushed.

**Conflicts (3), all the same shape as the last sync:** upstream inserts a new
tool immediately before an existing one whose signature this fork changed to
thread `ctx`, so the boundary collides. Took upstream's new tool and restored our
signature in each case. The one exception was the nutrition settings writer,
where upstream's version is a genuine fix and was taken whole; its two
`garmin_client.` calls were normalised to the `client` the function had already
resolved, since mixing both in one body reads as though they differ.

No new path-shaped parameters, so the remote-mode tripwire stayed quiet.

Having hit the same conflict shape twice running, `FORK.md` now records it with
the mechanical resolution, and adds the tool modules to its conflict-prone list —
the list previously named only infrastructure files, while both recent syncs
actually collided in `health_wellness.py`, `nutrition.py` and `courses.py`.

Result: 746 passed. Tool counts stdio 166 / remote 164.

## Live-auth tests are provably excluded from CI — 2026-09-02

Two CodeQL alerts were dismissed on the grounds that the tests involved never run
in CI. That was true, but rested on a marker no check enforced — and a dismissal
is sticky: remove the marker, the test runs, live data reaches this public
repository's build logs, and the alert stays closed with nothing to say so.

`tests/unit/test_live_auth_is_excluded_from_ci.py` closes that gap. It detects
any test module performing a real Garmin login (matching on the calls, with
comments stripped, so new files are covered and a prose mention in
`test_session_manager.py` is not a false positive), asserts each is marked
`e2e`, and separately runs the real collection command to confirm the CI
selection picks up none of them.

**Writing it immediately found a second module.** `tests/test_mcp_debug.py` was
unmarked and therefore *did* run in CI. It performs a real `client.login()` and
printed the login email — `print(f"Logging in with email: {email}")` — while
catching every exception, so it passed regardless of what happened. Today that
prints `None`, because CI holds no Garmin credentials; the moment `GARMIN_EMAIL`
were added to CI it would print a real address into public logs. Now marked
`e2e`, and the print reports presence rather than the value.

Verified the guard fires: commenting out the marker on `test_garmin.py` fails the
suite by name.

Tests: +7. Result: 718 passed.

## CodeQL first scan: workflow permissions hardened — 2026-09-02

I predicted CodeQL would find nothing. It found 8; six were real.

**Fixed: `GITHUB_TOKEN` was unconstrained in all three workflows.** None declared
`permissions:`, so the token inherited the repository default, which can be
read/write. All three now pin `contents: read` at the top level. These jobs only
read the repository — they never push, comment, or publish — so a broader token
was blast radius for no benefit: a compromised dependency pulled during
`uv sync` would have been running with push rights against a repository whose
`main` is otherwise protected.

**Two dismissed as false positives in context**, reasoning recorded in
`OPERATIONS.md` so they are not re-litigated:

- `tests/test_garmin.py` printing a resting heart rate. The module sets
  `pytestmark = pytest.mark.e2e` and CI runs `-m "not e2e"`, so it never
  executes in CI and no health data reaches this public repository's build logs.
  Worth checking rather than assuming, given the repo is public — and worth
  knowing that removing that marker would make it a genuine finding.
- `courses.py` writing a GPX to disk. The write is stdio-only; remote mode
  refuses `output_path` and returns the file inline, so it is the user's own
  route on their own machine.

## Documented counts are now test-enforced, plus a security policy — 2026-09-02

The documentation audit earlier today fixed four stale counts. This fixes the
*cause*: nothing failed when a count was wrong, so drift was only ever found by
someone recounting by hand.

- **`tests/unit/test_documented_counts.py`** derives the tool counts by
  registering every module, then fails when `CLAUDE.md`, `FORK.md`, or
  `README.md` disagrees — including the README's per-section list, which is
  where the drift actually accumulated (148 against 164 registered). Verified
  non-vacuous by reintroducing both real drifts and confirming each is caught:
  the section failure prints every section and its count, so the wrong one is
  immediately visible.
- **`SECURITY.md`** — private vulnerability reporting has been enabled for a
  while, but nothing on the repo said so, which made the channel undiscoverable.
  States the reporting route, sets honest expectations (a personal fork, best
  effort, no bounty), scopes what is worth reporting given the server holds
  Garmin tokens, and notes that upstream bugs should also go upstream since this
  fork cannot fix them for upstream's users.

Tests: +4. Result: 711 passed.

## Documentation audit — 2026-09-02

Checked every count and claim in the docs against the running code. Most were
right; these were not.

- **`CLAUDE.md` still said stdio 150 / remote 148.** This is the file loaded
  automatically at the start of every session, so its stale figure was the first
  thing any future session would read — and the check it prescribes ("re-enumerate
  tool counts") would have been measured against a wrong target. Now 164 / 162.
- **`README.md` per-section tool counts summed to 148, not 164.** Drift
  accumulated across several syncs: Nutrition was six under, Workouts six under,
  Data Management (3 tools) was never listed at all, and Gear Management was two
  *over* — an error introduced earlier the same day by assuming
  `get_activity_gear` lived in `gear_management` when it is in
  `activity_management`. Every section recounted from the registered tools; the
  sections now sum to exactly 164.
- **`OPERATIONS.md` listed CodeQL as deliberately off.** It was enabled today.
  Moved to the "on" list with what it actually scans (Python *and* GitHub
  Actions — relevant now that Dependabot opens PRs modifying workflow files) and
  its three non-gating check runs.
- **`README.md` skip rationale for `get_activity_details` sharpened** — the real
  reason is not payload size but that `get_activity_fit_data` parses the true FIT
  file at per-second resolution, where that endpoint returns ~2000 downsampled
  chart points.

## Training-plan discovery and per-gear activity lists — 2026-09-02

Four tools filling gaps found by auditing this fork's coverage of the
`garminconnect` library (134 public methods; 29 were unused).

- **`get_training_plans`**, **`get_training_plan_details`**,
  **`get_adaptive_training_plan_details`** — the fork could already read the
  workouts *inside* a plan (`get_garmin_coach_workouts`) but could not list or
  inspect the plans themselves. Adaptive (Garmin Coach) plans are served by a
  separate endpoint, so they get their own tool; `get_training_plans` reports
  which family each plan belongs to, and Garmin spells that flag two different
  ways across families.
- **`get_gear_activities`** — the reverse of `get_activity_gear`. `get_gear`
  reports a shoe's or bike's accumulated total and `get_activity_gear` answers
  "what did I wear on this run", but nothing itemised the activities behind a
  total. That is what makes a mis-recorded activity findable: a GPS dropout
  logging 0.49 mi for a 1.78 mi walk silently undercounts a shoe by 1.3 mi, and
  the only way to spot it was fetching every activity and checking its gear one
  at a time. The tool sums distance itself so the figure can be reconciled
  against what `get_gear` reports.

**Deliberately not built**, having compared them against existing coverage:

- `get_activity_details` — Garmin's downsampled chart feed (2000 points). Our
  `get_activity_fit_data` parses the real FIT file at full per-second
  resolution and derives what that endpoint never exposes. It would be a
  lower-fidelity duplicate; the README already listed it as skipped.
- Golf (3 methods) — not used.
- `set_gear_default`, badges, `delete_blood_pressure`,
  `set_activity_exercise_sets` — one-time settings or cosmetic. Every tool is
  description text the model carries in every session, so unused ones make the
  used ones harder to find.

Tests: +7. Result: 707 passed. Tool counts stdio 164 / remote 162.

## Dependabot manages GitHub Actions versions — 2026-09-02

GitHub is deprecating Node 20; `actions/checkout@v4` and `astral-sh/setup-uv@v5`
are being force-run on Node 24. Nothing is broken yet, but the fix by hand is ten
`uses:` pins across three workflows, and it recurs at every runtime deprecation.

`.github/dependabot.yml` now enables version updates for the **`github-actions`
ecosystem only** — monthly, grouped into one PR. This is an exception to the
"version updates off" decision, not a reversal of it: that decision is about
`pip`, where the deliberate pins (`garminconnect==0.3.5`, `mcp<2`, exact
`requests` / `python-dotenv`) would generate PRs to close forever. The actions
ecosystem holds no such pins, so there is nothing for Dependabot to fight, and
its PRs still have to clear all four required checks.

Also fixes a grouping error introduced in the same edit to `OPERATIONS.md`, which
briefly listed CodeQL and automatic dependency submission under "deliberately
on". Both are off.

## Upstream sync 2026-09-02 — 17 commits, 10 new tools

First sync since the stdio/remote split was collapsed, and **the first that
needed no tool migration at all**: upstream's 10 new tools use the bare
`garmin_client` global and worked unchanged through `_ResolvingGarminProxy`.
Verified with a real one — `get_calendar_events` — that two callers dispatch to
their own clients (one call each, no bleed), not just with the simulated tool in
the contract test.

**New tools:** `get_acclimation`, `get_running_tolerance(_trend)`,
`get_heart_rate_zones` / `set_heart_rate_zones`, `get_course_details`,
`download_course_gpx`, `get_calendar_events` (new `calendar_events` module),
`get_sleep_summary_range`, `get_activity_fit_messages`.

**Fixes taken:** bounded Garmin call duration so a stalled request can't hang the
server, non-blocking background login, null-section hardening across HRV / sleep
/ progress / body-battery / scheduled workouts, null device-map entries in
`get_training_status`, lactate-threshold unit conversion, VO2-max trend
continuity, strength-workout repeat groups, nutrition query params.

**The tripwire earned itself on its first run.** `download_course_gpx` arrived
with an `output_path` that wrote to the server's disk, and the path-guard test
failed the merge by name. It now returns the GPX inline in remote mode and
refuses the path. Without the guard added hours earlier this would have merged
green, because collapsing the split had just removed the crash that used to stop
it by accident.

**Two things found while merging:**

- **A blind spot in the contract test.** It compared `remote.py`'s configured and
  registered module lists *against each other*, so a module upstream added to
  `__init__.py` alone passed — both of remote's lists were consistently missing
  it. `calendar_events` reached stdio only, and remote would have been short one
  tool. Fixed, and the test now compares the two servers rather than one server
  against itself.
- **Malformed XML in the new GPX writer.** Course and waypoint names were
  interpolated into the document raw, so a name containing `&` or `<` produced a
  corrupt file. Escaped, with a test that parses the output.

Conflicts resolved in `__init__.py`, `courses.py`, `health_wellness.py`,
`nutrition.py`, `training.py`, `workouts.py`. The one that mattered: upstream's
background login replaced the block where we call `set_global_client`, which
stdio's `get_client()` falls back to — taking upstream's version wholesale would
have left every stdio tool unable to resolve a client.

Result: 700 passed. Tool counts stdio 160 / remote 158.

## Collapse the stdio/remote split — 2026-09-02

Every upstream merge used to carry a manual migration: upstream is stdio-only,
so its tools call the module-global `garmin_client`, which was `None` in our
remote mode. Each sync meant grepping for `garmin_client.` and rewriting every
new tool to `get_client(ctx)`; a missed one crashed at runtime for real users.
The pending sync has 10 such tools, the largest batch yet.

**Root cause: `ctx` was a fake dependency.** `get_client(ctx)` only used `ctx`
as a truthiness gate — the caller's identity actually comes from
`get_access_token()`, a per-request contextvar. Every tool carried a `ctx`
parameter solely to satisfy a check that never read it.

- **Dropped the gate**, so a client can be resolved without threading `ctx`.
- **Added `_ResolvingGarminProxy`**, and `remote.py` now `configure()`s all 16
  modules with it. It holds no client: each attribute access resolves the
  *calling user's* client, so two concurrent users never share one, and a tool
  written the way upstream writes them works unchanged in both modes.
- **Fail-closed preserved.** With no token in context, resolution falls through
  to the stdio global — which `remote.py` never sets — and raises.

**The catch, and the reason this shipped with guards.** A tool using the bare
global previously *crashed* in remote mode, and that crash was accidental
protection: it is the only reason upstream's `download_course_gpx(output_path=)`
could not write to our server's disk. Removing the crash would have silently
enabled it. So the same change closes the live hole and replaces the accident
with a test:

- `download_activity_file` now returns the file inline as base64 in remote mode
  and refuses `output_dir`; `set_fit_download_dir` is refused outright (it wrote
  a process-global setting — one caller could redirect every other user's
  downloads). Both were unguarded arbitrary-file-write primitives, the write-side
  mirror of the M3 read primitive fixed in `courses.py`.
- `tests/unit/test_remote_mode_contract.py` fails when any registered tool
  exposes a path-shaped parameter not on a reviewed allowlist, and when a
  registered module is left unconfigured. Verified the tripwire catches
  `output_path` — the parameter on upstream's incoming tool.

Tests: +12, including one that registers an upstream-style tool (bare global, no
`ctx`) and proves it resolves per user. Result: 604 passed. Tool counts
unchanged at stdio 150 / remote 148.

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
- **The workflow has never actually run.** Found while verifying the fix: GitHub
  disables workflows carrying a `schedule:` trigger in forked repositories, and
  disables the *entire* workflow rather than just the scheduled run.
  `security.yml` is the only one of the three with a `schedule:`, so it alone
  sits in state `disabled_fork` — zero runs across the repository's history,
  while `ci.yml` and `pr-validation.yml` ran normally. The failure is silent: no
  run, no error, and PR checks look complete because the other workflows
  reported. Needs a one-click enable in the Actions tab (documented, with a
  verification command, in `OPERATIONS.md`); no repo change can fix it.

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
