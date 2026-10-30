"""
Weight management functions for Garmin Connect MCP Server
"""
import json
import datetime
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Union

from mcp.server.fastmcp import Context
from garmin_mcp.client_resolver import get_client
from garmin_mcp.times import profile_time_zone, put_times
from garmin_mcp.units import account_unit_system, add_account_units

# The garmin_client will be set by the main file
garmin_client = None


def _local_epoch(weigh_in: dict):
    """A weigh-in's local pseudo-epoch ("date"), when Garmin sent one."""
    value = weigh_in.get("date")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return None


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def register_tools(app):
    """Register all weight management tools with the MCP server app"""

    @app.tool()
    async def get_weigh_ins(ctx: Context, start_date: str, end_date: str) -> str:
        """Get weight measurements between specified dates

        Weights are grams and kilograms as Garmin stores them. A US account
        also gets pounds beside each: weight_lb, bone_mass_lb, muscle_mass_lb
        and average_weight_lb.

        Each weigh-in's time: timestamp_gmt, and timestamp_local with its own UTC
        offset.

        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
        """
        try:
            data = get_client(ctx).get_weigh_ins(start_date, end_date)
            if not data:
                return f"No weight measurements found between {start_date} and {end_date}."

            # API returns nested structure: {dailyWeightSummaries: [{allWeightMetrics: [...]}]}
            daily_summaries = data.get("dailyWeightSummaries") or []
            if not daily_summaries:
                return f"No weight measurements found between {start_date} and {end_date}."

            # Extract all measurements from daily summaries
            all_measurements = []
            for day in daily_summaries:
                metrics = day.get("allWeightMetrics") or []
                all_measurements.extend(metrics)

            # Curate the response
            curated = {
                "date_range": {"start": start_date, "end": end_date},
                "measurement_count": len(all_measurements),
                "days_with_data": len(daily_summaries),
                "measurements": []
            }

            for w in all_measurements:
                measurement = {
                    "date": w.get("calendarDate"),
                    "weight_grams": w.get("weight"),
                    "weight_kg": round(w.get("weight", 0) / 1000, 2) if w.get("weight") else None,
                    "bmi": w.get("bmi"),
                    "body_fat_percent": w.get("bodyFat"),
                    "body_water_percent": w.get("bodyWater"),
                    "bone_mass_grams": w.get("boneMass"),
                    "muscle_mass_grams": w.get("muscleMass"),
                    "source_type": w.get("sourceType"),
                    # Change from the previous weigh-in, in grams like weight
                    # (field-coverage run, 2026-10-30).
                    "weight_change_grams": w.get("weightDelta"),
                }
                # FORK: timestamp_gmt was Garmin's bare epoch ms. Its "date"
                # is a local pseudo-epoch (the wall clock written as UTC),
                # which gives the weigh-in's own offset.
                put_times(measurement, "timestamp", w.get("timestampGMT"), _local_epoch(w))
                # Remove None values
                measurement = {k: v for k, v in measurement.items() if v is not None}
                curated["measurements"].append(measurement)

            # Sort by date descending (most recent first)
            curated["measurements"].sort(
                key=lambda x: x.get("date") or "", reverse=True
            )

            # Include average if available
            total_avg = data.get("totalAverage") or {}
            if total_avg.get("weight"):
                curated["average_weight_grams"] = total_avg["weight"]
                curated["average_weight_kg"] = round(total_avg["weight"] / 1000, 2)

            return json.dumps(
                add_account_units(curated, account_unit_system(get_client(ctx))), indent=2
            )
        except Exception as e:
            return f"Error retrieving weight measurements: {str(e)}"

    @app.tool()
    async def get_daily_weigh_ins(ctx: Context, date: str) -> str:
        """Get weight measurements for a specific date

        Weights are grams and kilograms as Garmin stores them. A US account
        also gets pounds beside each: weight_lb, bone_mass_lb, muscle_mass_lb
        and average_weight_lb.

        Each weigh-in's time: timestamp_gmt, and timestamp_local with its own UTC
        offset.

        Args:
            date: Date in YYYY-MM-DD format
        """
        try:
            data = get_client(ctx).get_daily_weigh_ins(date)
            if not data:
                return f"No weight measurements found for {date}."

            # API returns nested structure: {dateWeightList: [...]}
            weight_list = data.get("dateWeightList") or []
            if not weight_list:
                return f"No weight measurements found for {date}."

            # Curate the response
            curated = {
                "date": date,
                "measurement_count": len(weight_list),
                "measurements": []
            }

            for w in weight_list:
                measurement = {
                    "weight_grams": w.get("weight"),
                    "weight_kg": round(w.get("weight", 0) / 1000, 2) if w.get("weight") else None,
                    "bmi": w.get("bmi"),
                    "body_fat_percent": w.get("bodyFat"),
                    "body_water_percent": w.get("bodyWater"),
                    "bone_mass_grams": w.get("boneMass"),
                    "muscle_mass_grams": w.get("muscleMass"),
                    "source_type": w.get("sourceType"),
                    # Change from the previous weigh-in, in grams like weight
                    # (field-coverage run, 2026-10-30).
                    "weight_change_grams": w.get("weightDelta"),
                }
                # FORK: timestamp_gmt was Garmin's bare epoch ms. Its "date"
                # is a local pseudo-epoch (the wall clock written as UTC),
                # which gives the weigh-in's own offset.
                put_times(measurement, "timestamp", w.get("timestampGMT"), _local_epoch(w))
                # Remove None values
                measurement = {k: v for k, v in measurement.items() if v is not None}
                curated["measurements"].append(measurement)

            # Include average if available
            total_avg = data.get("totalAverage") or {}
            if total_avg.get("weight"):
                curated["average_weight_grams"] = total_avg["weight"]
                curated["average_weight_kg"] = round(total_avg["weight"] / 1000, 2)

            return json.dumps(
                add_account_units(curated, account_unit_system(get_client(ctx))), indent=2
            )
        except Exception as e:
            return f"Error retrieving daily weight measurements: {str(e)}"

    @app.tool()
    async def delete_weigh_ins(ctx: Context, date: str, delete_all: bool = True, confirm: bool = False) -> str:
        """Delete weight measurements for a specific date

        Permanently removes the selected day's weigh-ins. This cannot be undone.
        Call once with confirm=false (the default) to preview the measurements,
        then call again with confirm=true to delete them.

        Args:
            date: Date in YYYY-MM-DD format
            delete_all: Whether to delete all measurements for the day
            confirm: Set true after reviewing the preview to perform the deletion
        """
        try:
            if not confirm:
                data = garmin_client.get_daily_weigh_ins(date)
                measurements = (data or {}).get("dateWeightList") or []
                if not measurements or (len(measurements) > 1 and not delete_all):
                    message = (
                        f"No weight measurements found for {date}; nothing was deleted"
                        if not measurements else
                        f"Nothing was deleted for {date}: deleting multiple measurements requires delete_all=true"
                    )
                    return json.dumps({
                        "status": "nothing_deleted", "date": date,
                        "deleted_count": 0, "message": message,
                    }, indent=2)
                preview = []
                for measurement in measurements:
                    weight = measurement.get("weight")
                    item = {
                        "id": measurement.get("samplePk"),
                        "weight_kg": round(weight / 1000, 2) if weight is not None else None,
                        "source_type": measurement.get("sourceType"),
                    }
                    put_times(item, "timestamp", measurement.get("timestampGMT"), _local_epoch(measurement))
                    preview.append({key: value for key, value in item.items() if value is not None})
                return json.dumps({
                    "status": "needs_confirmation", "date": date,
                    "delete_all": delete_all, "measurement_count": len(preview),
                    "measurements": preview,
                    "message": (
                        "This permanently deletes the listed weight measurements and cannot be undone. "
                        "Call again with confirm=true to proceed."
                    ),
                }, indent=2)

            # API returns count of deleted entries
            deleted_count = garmin_client.delete_weigh_ins(date, delete_all=delete_all)
            # ...or None, having deleted nothing, when the day has no weigh-ins
            # or (with delete_all=False) has more than one
            if deleted_count is None:
                if delete_all:
                    message = f"No weight measurements found for {date}; nothing was deleted"
                else:
                    message = (
                        f"Nothing was deleted for {date}: the day has either no weight "
                        f"measurements or more than one, and deleting more than one "
                        f"requires delete_all=true"
                    )
                return json.dumps({
                    "status": "nothing_deleted",
                    "date": date,
                    "deleted_count": 0,
                    "message": message
                }, indent=2)
            return json.dumps({
                "status": "success",
                "date": date,
                "deleted_count": deleted_count if isinstance(deleted_count, int) else 0,
                "message": f"Weight measurements deleted for {date}"
            }, indent=2)
        except Exception as e:
            return f"Error deleting weight measurements: {str(e)}"

    @app.tool()
    async def add_weigh_in(ctx: Context, weight: float, unit_key: str = "kg") -> str:
        """Add a new weight measurement

        Args:
            weight: Weight value
            unit_key: Unit of weight ('kg' or 'lbs'; 'lb' is accepted as an alias)
        """
        try:
            unit_key = unit_key.strip().lower()
            if unit_key == "lb":
                unit_key = "lbs"
            result = garmin_client.add_weigh_in(weight=weight, unitKey=unit_key)
            # Return structured response
            return json.dumps({
                "status": "success",
                "weight": weight,
                "unit": unit_key,
                "message": "Weight measurement added successfully"
            }, indent=2)
        except Exception as e:
            return f"Error adding weight measurement: {str(e)}"

    @app.tool()
    async def add_weigh_in_with_timestamps(
        ctx: Context,
        weight: float,
        unit_key: str = "kg",
        date_timestamp: Optional[str] = None,
        gmt_timestamp: Optional[str] = None
    ) -> str:
        """Add a new weight measurement with specific timestamps

        If both timestamps are omitted, the current time is used. If only
        date_timestamp is given, the GMT timestamp is derived from it.

        Args:
            weight: Weight value
            unit_key: Unit of weight ('kg' or 'lbs'; 'lb' is accepted as an alias)
            date_timestamp: Local timestamp in format YYYY-MM-DDThh:mm:ss
                (required if gmt_timestamp is given -- it sets the day the
                weigh-in is recorded on)
            gmt_timestamp: GMT timestamp in format YYYY-MM-DDThh:mm:ss
        """
        try:
            unit_key = unit_key.strip().lower()
            if unit_key == "lb":
                unit_key = "lbs"
            if not date_timestamp and not gmt_timestamp:
                # Generate timestamps if not provided. FORK: "local" was the
                # server's clock -- UTC on Railway -- so an evening weigh-in in
                # New York was recorded on the next day. Local now comes from
                # the profile's time zone.
                zone = profile_time_zone(get_client(ctx))
                now = (
                    datetime.datetime.now(ZoneInfo(zone)) if zone
                    else datetime.datetime.now().astimezone()
                )
                date_timestamp = now.strftime('%Y-%m-%dT%H:%M:%S')
                gmt_timestamp = now.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
            elif not date_timestamp:
                # garminconnect would fill the local timestamp with "now" rather
                # than derive it from gmt_timestamp, recording the wrong day
                return (
                    "date_timestamp is required when gmt_timestamp is given: the "
                    "local timestamp sets the day the weigh-in is recorded on."
                )

            result = get_client(ctx).add_weigh_in_with_timestamps(
                weight=weight,
                unitKey=unit_key,
                dateTimestamp=date_timestamp,
                # garminconnect derives the GMT timestamp from dateTimestamp when empty
                gmtTimestamp=gmt_timestamp or ""
            )
            # Return structured response
            return json.dumps({
                "status": "success",
                "weight": weight,
                "unit": unit_key,
                "timestamp_local": date_timestamp,
                "timestamp_gmt": gmt_timestamp,
                "message": "Weight measurement added successfully"
            }, indent=2)
        except Exception as e:
            return f"Error adding weight measurement with timestamps: {str(e)}"

    return app
