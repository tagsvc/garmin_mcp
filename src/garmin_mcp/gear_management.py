"""
Gear management functions for Garmin Connect MCP Server
"""

import json
import logging
from typing import Any, Dict, List, Optional, Union

from mcp.server.fastmcp import Context
from garmin_mcp.client_resolver import get_client
from garmin_mcp.times import put_times
from garmin_mcp.units import (
    METERS_PER_MILE,
    account_unit_system,
    add_account_units,
    is_imperial,
)

# The garmin_client will be set by the main file
garmin_client = None
logger = logging.getLogger(__name__)

# Activity type mappings for gear defaults: Garmin activity typeId -> typeKey,
# as returned by get_activity_types, for the types gear is usually a default
# for. Any other typeId is reported as activity_<typeId>.
ACTIVITY_TYPE_MAPPING = {
    1: "running",
    2: "cycling",
    3: "hiking",
    4: "other",
    5: "mountain_biking",
    6: "trail_running",
    7: "street_running",
    8: "track_running",
    9: "walking",
    10: "road_biking",
    13: "strength_training",
    15: "casual_walking",
    16: "speed_walking",
    18: "treadmill_running",
    19: "cyclocross",
    20: "downhill_biking",
    21: "track_cycling",
    22: "recumbent_cycling",
    25: "indoor_cycling",
    26: "swimming",
    27: "lap_swimming",
    28: "open_water_swimming",
    143: "gravel_cycling",
    152: "virtual_ride",
    153: "virtual_run",
    154: "obstacle_run",
    156: "indoor_running",
    175: "e_bike_mountain",
    176: "e_bike_fitness",
    181: "ultra_run",
}

GEAR_V2_LIST_ENDPOINT = "/gear-service/gear/v2/list"


def _gear_v2_record(uuid: Optional[str]) -> Dict[str, Any]:
    """One gear item's v2 record, by UUID; {} when Garmin has none."""
    hyphenated = _hyphenated_gear_uuid(uuid)
    if not hyphenated:
        return {}
    try:
        record = garmin_client.connectapi(f"/gear-service/gear/v2/{hyphenated}")
    except Exception as exc:
        logger.debug("Garmin v2 gear record unavailable: %s", exc, exc_info=True)
        return {}
    return record if isinstance(record, dict) else {}


def _catalog_name(*names: Any) -> Optional[str]:
    """The first real brand or model name, skipping Garmin's placeholders
    ("Other", "Unknown Shoes") that stand for no name at all."""
    for name in names:
        if isinstance(name, str) and name.strip():
            lowered = name.strip().lower()
            if lowered != "other" and not lowered.startswith("unknown"):
                return name.strip()
    return None


def _parse_iso_date(iso_string: Optional[str]) -> Optional[str]:
    """Extract a date from an optional ISO datetime string."""
    if not iso_string:
        return None
    return iso_string.split("T")[0] if "T" in iso_string else iso_string


def _hyphenated_gear_uuid(uuid: Optional[str]) -> Optional[str]:
    """A legacy 32-hex gear UUID in the 8-4-4-4-12 form the v2 endpoints take."""
    plain = _normalize_gear_uuid(uuid)
    if not plain or len(plain) != 32:
        return None
    return f"{plain[:8]}-{plain[8:12]}-{plain[12:16]}-{plain[16:20]}-{plain[20:]}"


def _normalize_gear_uuid(uuid: Optional[str]) -> Optional[str]:
    """Normalize UUIDs returned in hyphenated and unhyphenated forms."""
    if not isinstance(uuid, str):
        return None
    return uuid.replace("-", "").lower()


def _is_missing_gear(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "gear" in message
        and ("doesn't exist" in message or "not found" in message or "404" in message)
    )


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def register_tools(app):
    """Register all gear management tools with the MCP server app"""

    @app.tool()
    async def get_gear(ctx: Context, include_stats: bool = True) -> str:
        """Get all gear registered with the user account

        Returns complete gear inventory including free-text notes, usage statistics,
        and default activity associations. The notes field is null when unavailable.
        defaults maps each activity type (Garmin typeKey, e.g. "running") to the
        names of its default gear.
        A gear item's stats_error reports a failed usage-statistics request;
        a null total_distance_km means Garmin did not provide a distance.
        A statute (US or UK) account also gets total_distance_miles and
        max_distance_miles.
        No parameters required - user profile is fetched automatically.

        Args:
            include_stats: Include usage statistics for each gear item (default True).
                           Set to False for faster response with large gear collections.
        """
        try:
            # 1. Get user_profile_id automatically from last used device
            device_info = get_client(ctx).get_device_last_used()
            if not device_info:
                return "Could not retrieve user profile. Please ensure you have a synced device."
            user_profile_id = device_info.get("userProfileNumber")

            # 2. Get all gear
            gear_list = get_client(ctx).get_gear(user_profile_id)
            if not gear_list:
                return "No gear found."

            # Notes, brand and model are only exposed by Garmin's v2 gear
            # endpoint. Its UUIDs are hyphenated, unlike the legacy endpoint
            # used for the existing fields.
            notes_by_uuid: Dict[str, Optional[str]] = {}
            v2_by_uuid: Dict[str, Dict[str, Any]] = {}
            try:
                gear_v2_list = garmin_client.connectapi(GEAR_V2_LIST_ENDPOINT) or []
                if not isinstance(gear_v2_list, list):
                    logger.debug(
                        "Unexpected Garmin v2 gear response type: %s",
                        type(gear_v2_list).__name__,
                    )
                    gear_v2_list = []
                for gear_v2 in gear_v2_list:
                    if not isinstance(gear_v2, dict):
                        continue
                    normalized_v2_uuid = _normalize_gear_uuid(gear_v2.get("uuid"))
                    if normalized_v2_uuid:
                        notes_by_uuid[normalized_v2_uuid] = gear_v2.get("notes")
                        v2_by_uuid[normalized_v2_uuid] = gear_v2
            except Exception as exc:
                logger.debug(
                    "Garmin v2 gear list unavailable: %s", exc, exc_info=True
                )

            # 3. Get defaults to map gear -> activity types
            defaults_list = get_client(ctx).get_gear_defaults(user_profile_id) or []
            defaults_by_uuid = {}
            for d in defaults_list:
                uuid = d.get("uuid")
                activity_pk = d.get("activityTypePk")
                activity_type = ACTIVITY_TYPE_MAPPING.get(
                    activity_pk, f"activity_{activity_pk}"
                )
                if uuid not in defaults_by_uuid:
                    defaults_by_uuid[uuid] = []
                defaults_by_uuid[uuid].append(activity_type)

            # 4. Build curated gear list
            # Miles come from Garmin's meters, not from the rounded km.
            imperial = is_imperial(account_unit_system(get_client(ctx)))
            curated_gear = []
            active_count = 0
            retired_count = 0

            for g in gear_list:
                uuid = g.get("uuid")
                status = g.get("gearStatusName", "").lower()
                v2 = v2_by_uuid.get(_normalize_gear_uuid(uuid))
                if v2 is None:
                    # The v2 list leaves retired gear out (its status filter is
                    # ignored); the v2 record by UUID has it (verified live
                    # 2026-10-30: ASICS / Gel-Cumulus 28, retired).
                    v2 = _gear_v2_record(uuid)

                if status == "active":
                    active_count += 1
                elif status == "retired":
                    retired_count += 1

                gear_item = {
                    "uuid": uuid,
                    "name": g.get("displayName"),
                    "full_name": g.get("customMakeModel"),
                    # FORK: the brand and model picked from Garmin's catalog
                    # (verified live 2026-10-30: v2 "adidas" / "Adizero Adios
                    # Pro 5", and the typed text for a model chosen as "Other").
                    # The legacy list's gearMakeName/gearModelName read
                    # "Other" / "Unknown Shoes" even for catalog gear.
                    "make": _catalog_name(v2.get("brand"), g.get("gearMakeName")),
                    "model": _catalog_name(v2.get("model"), g.get("gearModelName")),
                    "type": g.get("gearTypeName"),
                    "status": status,
                    "date_begin": _parse_iso_date(g.get("dateBegin")),
                    "date_end": _parse_iso_date(g.get("dateEnd")),
                    "notes": notes_by_uuid.get(
                        _normalize_gear_uuid(uuid), v2.get("notes", g.get("notes"))
                    ),
                }

                # Add max distance in km if set
                max_meters = g.get("maximumMeters")
                if max_meters and max_meters > 0:
                    gear_item["max_distance_km"] = round(max_meters / 1000, 1)
                    if imperial:
                        gear_item["max_distance_miles"] = round(max_meters / METERS_PER_MILE, 1)

                # Add default activity associations
                if uuid in defaults_by_uuid:
                    gear_item["is_default_for"] = defaults_by_uuid[uuid]

                # 5. Get stats if requested
                if include_stats:
                    try:
                        stats = garmin_client.get_gear_stats(uuid)
                    except Exception as exc:
                        gear_item["stats_error"] = str(exc)
                    else:
                        if stats:
                            distance = stats.get("totalDistance")
                            gear_item["stats"] = {
                                "total_activities": stats.get("totalActivities"),
                                "total_distance_km": round(
                                    distance / 1000, 1
                                ) if distance is not None else None,
                            }
                            if imperial:
                                gear_item["stats"]["total_distance_miles"] = round(
                                    distance / METERS_PER_MILE, 1
                                ) if distance is not None else None

                curated_gear.append(gear_item)

            # Sort: active first, then by date_begin descending
            curated_gear.sort(
                key=lambda x: (x["status"] != "active", x.get("date_begin") or ""),
                reverse=True,
            )
            # Re-sort to ensure active comes first
            curated_gear.sort(key=lambda x: x["status"] != "active")

            # Build defaults summary (activity -> gear names; an activity type
            # can have several default gear items, and displayName is null for
            # gear without a nickname)
            defaults_summary: Dict[str, List[str]] = {}
            for uuid, activities in defaults_by_uuid.items():
                gear = next((g for g in curated_gear if g["uuid"] == uuid), {})
                gear_name = gear.get("name") or gear.get("full_name") or uuid
                for activity in activities:
                    defaults_summary.setdefault(activity, []).append(gear_name)

            return json.dumps(
                {
                    "gear_count": len(curated_gear),
                    "active_count": active_count,
                    "retired_count": retired_count,
                    "defaults": defaults_summary,
                    "gear": curated_gear,
                },
                indent=2,
            )
        except Exception as e:
            return f"Error retrieving gear: {str(e)}"

    @app.tool()
    async def add_gear_to_activity(ctx: Context, activity_id: int, gear_uuid: str) -> str:
        """Associate gear with an activity

        Links a specific piece of gear (like shoes, bike, etc.) to an activity.

        Args:
            activity_id: ID of the activity
            gear_uuid: UUID of the gear to add (get from get_gear)
        """
        try:
            get_client(ctx).add_gear_to_activity(gear_uuid, activity_id)

            return json.dumps(
                {
                    "success": True,
                    "activity_id": activity_id,
                    "gear_uuid": gear_uuid,
                    "message": "Gear successfully added to activity",
                },
                indent=2,
            )
        except Exception as e:
            if _is_missing_gear(e):
                return (
                    f"No gear association changed; gear {gear_uuid} was not found "
                    f"for activity {activity_id}."
                )
            return f"Error adding gear to activity: {str(e)}"

    @app.tool()
    async def remove_gear_from_activity(ctx: Context, activity_id: int, gear_uuid: str) -> str:
        """Remove gear association from an activity

        Unlinks a specific piece of gear from an activity.

        Args:
            activity_id: ID of the activity
            gear_uuid: UUID of the gear to remove
        """
        try:
            get_client(ctx).remove_gear_from_activity(gear_uuid, activity_id)

            return json.dumps(
                {
                    "success": True,
                    "activity_id": activity_id,
                    "gear_uuid": gear_uuid,
                    "message": "Gear successfully removed from activity",
                },
                indent=2,
            )
        except Exception as e:
            if _is_missing_gear(e):
                return (
                    f"No gear association changed; gear {gear_uuid} was not found "
                    f"for activity {activity_id}."
                )
            return f"Error removing gear from activity: {str(e)}"

    @app.tool()
    async def get_gear_activities(
        ctx: Context, gear_uuid: str, limit: int = 1000
    ) -> str:
        """List every activity a given piece of gear was used on.

        The reverse of get_activity_gear (which answers "what gear did I use on
        this activity"). Use this to audit a shoe's or bike's accumulated
        mileage: get_gear reports the total, this itemises what makes it up, so
        a mis-recorded activity -- a GPS dropout logging 0.5 mi for a 2 mi walk
        -- can actually be found.

        Distances are meters (distance_m, total_distance_m), each with a
        companion in the account's units: distance_miles for a US account,
        distance_km for a metric one.

        Start times: start_time_gmt, and start_time_local with the activity's own
        UTC offset.

        Args:
            gear_uuid: UUID of the gear (get UUIDs from get_gear).
            limit: Maximum activities to return (default 1000).
        """
        try:
            data = get_client(ctx).get_gear_activities(gear_uuid, limit=limit)
            if not isinstance(data, list):
                return json.dumps(data, indent=2)

            curated = []
            for a in data:
                item = {
                    "activity_id": a.get("activityId"),
                    "name": a.get("activityName"),
                }
                # FORK: was "start_time", local time with no zone.
                put_times(item, "start_time", a.get("startTimeGMT"), a.get("startTimeLocal"))
                item.update({
                    "distance_m": a.get("distance"),
                    "duration_s": a.get("duration"),
                    "activity_type": (a.get("activityType") or {}).get("typeKey"),
                })
                curated.append(item)
            total_m = sum(c["distance_m"] or 0 for c in curated)
            return json.dumps(
                add_account_units(
                    {
                        "gear_uuid": gear_uuid,
                        "activity_count": len(curated),
                        # Summed here so the total can be reconciled against the
                        # figure get_gear reports for the same gear.
                        "total_distance_m": round(total_m, 2),
                        "activities": curated,
                    },
                    account_unit_system(get_client(ctx)),
                ),
                indent=2,
            )
        except Exception as e:
            return f"Error listing activities for gear: {str(e)}"

    return app
