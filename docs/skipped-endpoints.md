# Intentionally skipped endpoints

Some python-garminconnect methods have no tool here, for performance or
complexity reasons. Back to the [README](../README.md).

## High data volume

- `get_activity_details()` returns Garmin's downsampled chart feed (50KB-500KB,
  capped at ~2000 points). Use `get_activity()` for summaries, or
  `get_activity_fit_data()` for the real FIT file at full per-second
  resolution, which is strictly higher fidelity than this endpoint.

## Specialized workout formats

- `upload_running_workout()`, `upload_cycling_workout()`,
  `upload_swimming_workout()` are sport-specific workout uploads. Use
  `upload_workout()` for general workouts (see [workouts.md](workouts.md)).

## Maintenance and destructive operations

- `delete_blood_pressure()`: destructive operations require careful
  consideration.
- `delete_activity()` is implemented, with a confirmation gate: call once to
  preview, then again with `confirm=true` to delete. Deletions are logged to
  stderr. There is no undo. It is stdio-only: the remote server does not offer
  it (see the README's Security section).
- Internal and auth methods (`login()`, `resume_login()`, `connectapi()`,
  `download()`) are handled automatically by the library. `inspect_garmin`
  can GET one Garmin Connect API path for diagnosis.

If you need one of these endpoints, please
[open an issue](https://github.com/tagsvc/garmin_mcp/issues).
