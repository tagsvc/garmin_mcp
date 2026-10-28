"""
Course management functions for Garmin Connect MCP Server.

Adds support for uploading GPX files as Garmin Connect Courses, listing courses,
getting detailed course waypoints/metadata, downloading course GPX files, and deleting courses.
"""

import datetime
from decimal import Decimal
import io
import json
import math
import os
import pathlib
import re
import sys
from typing import Any, Dict, Optional
from xml.sax.saxutils import escape

from garmin_mcp.activity_analysis import _resolve_download_dir

from mcp.server.fastmcp import Context

from garmin_mcp.client_resolver import get_client, is_remote_mode
from garmin_mcp.units import account_unit_system, add_account_units

# The garmin_client will be set by the main file
garmin_client = None


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


def _gpx_decimal(value: Any) -> str:
    """GPX uses xsd:decimal; expand exponent notation without rounding."""
    return format(Decimal(str(value)), "f")


_EARTH_RADIUS_M = 6371000.0


def _haversine(p1: Dict[str, float], p2: Dict[str, float]) -> float:
    lat1, lon1 = math.radians(p1["latitude"]), math.radians(p1["longitude"])
    lat2, lon2 = math.radians(p2["latitude"]), math.radians(p2["longitude"])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _initial_bearing(p1: Dict[str, float], p2: Dict[str, float]) -> float:
    lat1, lat2 = math.radians(p1["latitude"]), math.radians(p2["latitude"])
    dlon = math.radians(p2["longitude"] - p1["longitude"])
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    initial_bearing = math.atan2(x, y)
    initial_bearing = math.degrees(initial_bearing)
    return (initial_bearing + 360) % 360


# Garmin Connect activity typeIds (see get_activity_types).
_ACTIVITY_TYPE_IDS = {
    "running": 1,
    "cycling": 2,
    "hiking": 3,
    "walking": 9,
    "trail_running": 6,
    "mountain_biking": 5,
    "road_biking": 10,
    "gravel_cycling": 143,
}


def _safe_upload_filename(name: str, fallback: str = "course.gpx") -> str:
    """Derive a safe multipart filename from caller-supplied text.

    ``course_name`` reaches us from the tool caller and is sent to Garmin as the
    filename of a multipart part. Left raw it could carry path separators, or
    quotes and CR/LF that land in a Content-Disposition header. Keep a
    conservative character set, strip any directory component, and bound the
    length.
    """
    base = os.path.basename((name or "").strip())
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")
    cleaned = cleaned[:80]
    return f"{cleaned}.gpx" if cleaned else fallback


def _build_course_payload(
    parsed_skeleton: Dict[str, Any],
    course_name: str,
    activity_type_id: int,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    geo_points = parsed_skeleton.get("geoPoints", [])
    if not geo_points:
        raise ValueError("GPX parsed skeleton contains no geoPoints")

    total_distance = 0.0
    for i in range(1, len(geo_points)):
        total_distance += _haversine(geo_points[i - 1], geo_points[i])

    for p in geo_points:
        if p.get("elevation") is None:
            p["elevation"] = 0.0

    lats = [p["latitude"] for p in geo_points]
    lons = [p["longitude"] for p in geo_points]

    bbox = {
        "center": {
            "latitude": (min(lats) + max(lats)) / 2,
            "longitude": (min(lons) + max(lons)) / 2,
        },
        "lowerLeft": {"latitude": min(lats), "longitude": min(lons)},
        "upperRight": {"latitude": max(lats), "longitude": max(lons)},
        "lowerLeftLatIsSet": True,
        "lowerLeftLongIsSet": True,
        "upperRightLatIsSet": True,
        "upperRightLongIsSet": True,
    }

    start_point = {
        "latitude": geo_points[0]["latitude"],
        "longitude": geo_points[0]["longitude"],
        "elevation": geo_points[0].get("elevation") or 0.0,
        "distance": None,
        "timestamp": None,
    }

    bearing = _initial_bearing(geo_points[0], geo_points[-1])

    return {
        "courseName": course_name,
        "description": description,
        "openStreetMap": False,
        "matchedToSegments": False,
        "userProfilePk": None,
        "userGroupPk": None,
        "rulePK": 2,  # private
        "geoRoutePk": None,
        "sourceTypeId": 3,  # GPX
        "sourcePk": None,
        "distanceMeter": total_distance,
        "elevationGainMeter": 0.0,
        "elevationLossMeter": 0.0,
        "startPoint": start_point,
        "coursePoints": [],
        "boundingBox": bbox,
        "hasShareableEvent": False,
        "hasTurnDetectionDisabled": False,
        "activityTypePk": activity_type_id,
        "virtualPartnerId": None,
        "includeLaps": False,
        "elapsedSeconds": None,
        "startBearing": bearing,
        "endBearing": None,
        "geoPoints": geo_points,
    }


def _resolve_gpx_output_path(course_id: int, output_path: Optional[str] = None) -> str:
    """Resolve the destination file path for downloading a course GPX.

    A directory output_path gets {course_id}.gpx; a file output_path must end
    in .gpx so the tool cannot overwrite arbitrary files. Without output_path,
    use the same directory as download_activity_file (GARMIN_FIT_DOWNLOAD_DIR,
    then the one saved by set_fit_download_dir), falling back to ./courses.
    """
    if output_path:
        p = os.path.abspath(os.path.expanduser(output_path))
        if os.path.isdir(p) or output_path.endswith("/") or output_path.endswith("\\"):
            return os.path.join(p, f"{course_id}.gpx")
        if not p.lower().endswith(".gpx"):
            raise ValueError(
                f"output_path must be a directory or a .gpx file, got: {output_path}"
            )
        return p

    base_dir = _resolve_download_dir(None) or os.path.abspath("./courses")
    return os.path.join(base_dir, f"{course_id}.gpx")


def _course_preview(client: Any, course_id: int) -> Dict[str, Any]:
    """Build a compact preview of a course before deleting it."""
    preview: Dict[str, Any] = {"course_id": course_id}

    course = None
    try:
        course = client.client.connectapi(f"/course-service/course/{course_id}")
    except Exception:
        course = None

    if not (isinstance(course, dict) and course):
        # Fall back to the course list if the details call fails.
        try:
            items = client.client.connectapi("/course-service/course")
        except Exception:
            items = None
        if not isinstance(items, list):
            return preview
        course = next(
            (
                item
                for item in items
                if isinstance(item, dict) and str(item.get("courseId")) == str(course_id)
            ),
            None,
        )
        if course is None:
            return preview

    activity_type = course.get("activityType")
    activity_type = activity_type if isinstance(activity_type, dict) else {}
    preview.update(
        {
            "name": course.get("courseName"),
            "activity": activity_type.get("typeKey"),
            "distance_m": course.get("distanceInMeters") or course.get("distanceMeter"),
            "created": course.get("createdDateFormatted"),
        }
    )
    return {key: value for key, value in preview.items() if value is not None}


def _course_activity(data: Dict[str, Any]) -> Dict[str, Any]:
    """The course's activity type, from whichever shape the payload uses.

    FORK: the course LIST carries an ``activityType`` object with a typeKey
    (verified live). The course DETAIL endpoint returned none on a live
    course 2026-10-14, so get_course_details reported ``activity: null`` for a
    course the list calls running. The save response carries a numeric
    ``activityTypePk`` instead, so read that too and name it where the id is
    one upload_course knows. Neither present -> both null, not guessed.
    """
    activity_type = data.get("activityType")
    type_key = activity_type.get("typeKey") if isinstance(activity_type, dict) else None
    type_id = data.get("activityTypePk")
    if type_key is None and isinstance(type_id, int):
        type_key = {pk: key for key, pk in _ACTIVITY_TYPE_IDS.items()}.get(type_id)
    return {"activity": type_key, "activity_type_id": type_id}


def register_tools(app):
    """Register course management tools"""

    @app.tool()
    async def get_courses(ctx: Context) -> str:
        """List all courses saved on Garmin Connect.

        Returns a curated list of courses with id, name, distance, activity type
        and creation date.

        Distances and elevations are meters (distance_m, elevation_gain_m), each
        with a companion in the account's units: distance_miles and
        elevation_gain_feet for a US account, distance_km for a metric one.
        """
        try:
            data = get_client(ctx).connectapi("/course-service/course")

            if not isinstance(data, list):
                return json.dumps(data, indent=2)

            curated = [
                {
                    "course_id": c.get("courseId"),
                    "name": c.get("courseName"),
                    "distance_m": c.get("distanceInMeters"),
                    "elevation_gain_m": c.get("elevationGainInMeters"),
                    "elevation_loss_m": c.get("elevationLossInMeters"),
                    "activity": (c.get("activityType") or {}).get("typeKey"),
                    "has_pace_band": c.get("hasPaceBand"),
                    "created": c.get("createdDateFormatted"),
                }
                for c in data
            ]
            return json.dumps(
                add_account_units(
                    {"count": len(curated), "courses": curated},
                    account_unit_system(get_client(ctx)),
                ),
                indent=2,
            )
        except Exception as e:
            return f"Error listing courses: {str(e)}"

    @app.tool()
    async def get_course_details(course_id: int) -> str:
        """Get full details of a Garmin Connect course by ID.

        Returns course metadata, elevation gain/loss, total distance,
        and all custom course waypoints (shops, water, food, campgrounds, hazards).

        Distances and elevations are meters (distance_m, elevation_gain_m), each
        with a companion in the account's units: distance_miles and
        elevation_gain_feet for a US account, distance_km for a metric one.

        Args:
            course_id: ID of the course (from get_courses).
        """
        try:
            data = garmin_client.client.connectapi(f"/course-service/course/{course_id}")
            if not isinstance(data, dict):
                return json.dumps(data, indent=2)

            course_points = [
                {
                    "name": cp.get("name"),
                    "type": cp.get("pointType"),
                    "lat": cp.get("lat"),
                    "lon": cp.get("lon"),
                    "distance_m": cp.get("distance"),
                }
                for cp in (data.get("coursePoints") or [])
            ]
            geo_points = data.get("geoPoints") or []

            result = {
                "course_id": data.get("courseId"),
                "name": data.get("courseName"),
                "distance_m": data.get("distanceInMeters") or data.get("distanceMeter"),
                "elevation_gain_m": data.get("elevationGainInMeters") or data.get("elevationGainMeter"),
                "elevation_loss_m": data.get("elevationLossInMeters") or data.get("elevationLossMeter"),
                **_course_activity(data),
                "waypoints_count": len(course_points),
                "waypoints": course_points,
                "geo_points_count": len(geo_points),
                "url": f"https://connect.{garmin_client.client.domain}/modern/course/{course_id}",
            }
            return json.dumps(
                add_account_units(result, account_unit_system(garmin_client)), indent=2
            )
        except Exception as e:
            return f"Error fetching course details: {str(e)}"

    @app.tool()
    async def download_course_gpx(
        course_id: int,
        output_path: Optional[str] = None,
    ) -> str:
        """Download a Garmin Connect course as a GPX file.

        The GPX is rebuilt from the course's track points (lat/lon/elevation)
        and course points (as waypoints); it is not the originally uploaded file.

        In remote mode the GPX is returned inline in the response and
        output_path is refused: a path there would name the server's disk.

        Args:
            course_id: ID of the course to download.
            output_path: Optional destination directory (saved as {course_id}.gpx)
                or file path ending in .gpx; any other file path is rejected.
                Defaults to the download_activity_file directory
                (GARMIN_FIT_DOWNLOAD_DIR, then set_fit_download_dir), else
                ./courses/{course_id}.gpx. Stdio mode only.
        """
        try:
            # FORK: refuse before resolving or creating anything. A path names
            # the SERVER's disk in remote mode, so it is both useless to the
            # caller and an arbitrary-file-write primitive -- and upstream's
            # ordering would os.makedirs() the default directory on the server
            # even with no output_path given.
            remote = is_remote_mode()
            if remote and output_path:
                return (
                    "Error: output_path is disabled in remote mode because it "
                    "would write to the server's filesystem, not yours. Omit it "
                    "and the GPX is returned inline."
                )
            target_path = None if remote else _resolve_gpx_output_path(course_id, output_path)
            data = garmin_client.client.connectapi(f"/course-service/course/{course_id}")
            if not isinstance(data, dict) or not data.get("geoPoints"):
                return f"Error: course {course_id} not found or missing geoPoints."
            name = data.get("courseName") or f"Garmin Course {course_id}"
            geo_points = data.get("geoPoints") or []
            course_points = data.get("coursePoints") or []

            lines = [
                "<?xml version='1.0' encoding='UTF-8'?>",
                "<gpx version='1.1' creator='GarminConnectMCP' xmlns='http://www.topografix.com/GPX/1/1'>",
                "  <metadata>",
                f"    <name>{escape(name)}</name>",
                "  </metadata>",
            ]
            for cp in course_points:
                lat, lon = cp.get("lat"), cp.get("lon")
                if lat is not None and lon is not None:
                    cp_name = cp.get("name") or cp.get("pointType") or "Waypoint"
                    cp_type = cp.get("pointType") or "GENERIC"
                    lines.append(f"  <wpt lat='{_gpx_decimal(lat)}' lon='{_gpx_decimal(lon)}'>")
                    lines.append(f"    <name>{escape(cp_name)}</name>")
                    lines.append(f"    <type>{escape(cp_type)}</type>")
                    lines.append("  </wpt>")

            lines.append("  <trk>")
            lines.append(f"    <name>{escape(name)}</name>")
            lines.append("    <trkseg>")
            for p in geo_points:
                lat = p.get("latitude")
                lon = p.get("longitude")
                ele = p.get("elevation")
                if lat is not None and lon is not None:
                    ele_tag = f"<ele>{_gpx_decimal(ele)}</ele>" if ele is not None else ""
                    lines.append(f"      <trkpt lat='{_gpx_decimal(lat)}' lon='{_gpx_decimal(lon)}'>{ele_tag}</trkpt>")
            lines.append("    </trkseg>")
            lines.append("  </trk>")
            lines.append("</gpx>")
            gpx = "\n".join(lines)

            summary = {
                "status": "success",
                "course_id": course_id,
                "name": name,
                "waypoints_count": len(course_points),
                "track_points_count": len(geo_points),
            }
            if remote:
                summary["gpx"] = gpx
                return json.dumps(summary, indent=2)

            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(gpx)
            summary["gpx_path"] = target_path
            return json.dumps(summary, indent=2)
        except Exception as e:
            return f"Error downloading course GPX: {str(e)}"

    @app.tool()
    async def upload_course(
        ctx: Context,
        gpx_path: Optional[str] = None,
        gpx_base64: Optional[str] = None,
        course_name: Optional[str] = None,
        activity_type: str = "running",
        description: Optional[str] = None,
    ) -> str:
        """Upload a GPX file as a Garmin Connect Course.

        The course can then be loaded onto the watch (sync or "Send to Device")
        and used as a navigation course or to build a PacePro strategy.

        Supply the GPX either as `gpx_base64` (the file's bytes, base64-encoded)
        or, when running locally over stdio, as `gpx_path`. Reading a path off
        the server's filesystem is refused in remote mode: on a network server
        the path would name the SERVER's disk, not yours, so it is both useless
        and an arbitrary-file-read primitive. Use `gpx_base64` there.

        Args:
            gpx_path: Path to a .gpx file on the machine running the server.
                stdio (local) mode only.
            gpx_base64: Base64-encoded contents of the .gpx file. Works in both
                modes and is required in remote mode.
            course_name: Override the course name. Defaults to the name parsed
                from the GPX file. Max 128 characters; Garmin rejects longer.
            activity_type: One of running, cycling, hiking, walking, trail_running,
                mountain_biking, road_biking, gravel_cycling. Defaults to running.
            description: Optional description shown on the course detail page.
                Max 512 characters; Garmin rejects longer.
        """
        try:
            if not gpx_path and not gpx_base64:
                return "Error: supply either gpx_base64 (preferred) or gpx_path."
            if gpx_path and gpx_base64:
                return "Error: supply only one of gpx_base64 or gpx_path, not both."

            upload_filename = "course.gpx"

            if gpx_base64:
                import base64 as _b64

                try:
                    gpx_bytes = _b64.b64decode(gpx_base64, validate=True)
                except Exception:
                    return "Error: gpx_base64 is not valid base64."
                if not gpx_bytes:
                    return "Error: gpx_base64 decoded to empty content."
                if course_name:
                    upload_filename = _safe_upload_filename(course_name)
            else:
                # Path reads name the SERVER's filesystem. Harmless for a local
                # stdio server; on the remote server it lets any authenticated
                # user read arbitrary .gpx-suffixed files off the host.
                if is_remote_mode():
                    return (
                        "Error: gpx_path is disabled in remote mode because it would "
                        "read from the server's filesystem, not yours. Send the file "
                        "contents as gpx_base64 instead."
                    )
                _p = pathlib.Path(gpx_path)
                if _p.suffix.lower() != ".gpx":
                    return f"Error: only .gpx files are allowed, got: {_p.suffix or '(no extension)'}"
                gpx_path = str(_p.resolve())
                if not os.path.isfile(gpx_path):
                    return f"Error: GPX file not found: {gpx_path}"
                with open(gpx_path, "rb") as f:
                    gpx_bytes = f.read()
                upload_filename = _safe_upload_filename(os.path.basename(gpx_path))

            activity_type_id = _ACTIVITY_TYPE_IDS.get(activity_type.lower())
            if activity_type_id is None:
                return (
                    f"Error: unknown activity_type '{activity_type}'. "
                    f"Supported: {', '.join(sorted(_ACTIVITY_TYPE_IDS))}."
                )

            client = get_client(ctx)
            # Step 1: parse the GPX server-side
            parsed = client.client.post(
                "connectapi",
                "/course-service/course/import",
                files={
                    "file": (
                        # FORK: upload_filename is sanitised (_safe_upload_filename);
                        # upstream's raw os.path.basename(gpx_path) would put
                        # caller-controlled quotes/CRLF into Content-Disposition,
                        # and gpx_path is None on the base64 path we require in
                        # remote mode. BytesIO wrapper kept from upstream.
                        upload_filename,
                        io.BytesIO(gpx_bytes),
                        "application/gpx+xml",
                    )
                },
                api=True,
            )

            effective_name = (
                course_name
                or parsed.get("courseName")
                or os.path.splitext(upload_filename)[0]
            )

            # Step 2: build the create payload and save
            payload = _build_course_payload(
                parsed,
                course_name=effective_name,
                activity_type_id=activity_type_id,
                description=description,
            )

            saved = client.client.post(
                "connectapi", "/course-service/course", json=payload, api=True,
            )
            return json.dumps(
                {
                    "status": "success",
                    "course_id": saved.get("courseId"),
                    "name": saved.get("courseName"),
                    "distance_m": saved.get("distanceMeter"),
                    "elevation_gain_m": saved.get("elevationGainMeter"),
                    "elevation_loss_m": saved.get("elevationLossMeter"),
                    "activity_type_id": saved.get("activityTypePk"),
                    "url": f"https://connect.{client.client.domain}/modern/course/{saved.get('courseId')}",
                },
                indent=2,
            )

        except Exception as e:
            return f"Error uploading course: {str(e)}"

    @app.tool()
    async def delete_course(ctx: Context, course_id: int, confirm: bool = False) -> str:
        """Delete a course from Garmin Connect.

        This cannot be undone. Call once with confirm=false (the default) to
        preview the course, then call again with confirm=true to delete it.

        Args:
            course_id: ID of the course to delete (get IDs from get_courses).
            confirm: Must be true to actually delete. Default false returns a preview.
        """
        try:
            preview = _course_preview(garmin_client, course_id)
            if not confirm:
                return json.dumps(
                    {
                        "status": "needs_confirmation",
                        "course": preview,
                        "message": (
                            "This permanently deletes the course from Garmin Connect "
                            "and cannot be undone. Call again with confirm=true to proceed."
                        ),
                    },
                    indent=2,
                )

            garmin_client.client.delete(
                "connectapi", f"/course-service/course/{course_id}"
            )
            print(
                "delete_course: deleted course "
                f"{course_id} name={preview.get('name')!r} "
                f"at {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
                file=sys.stderr,
            )
            return json.dumps(
                {
                    "status": "success",
                    "course_id": course_id,
                    "course": preview,
                    "message": f"Course {course_id} deleted",
                },
                indent=2,
            )
        except Exception as e:
            return f"Error deleting course: {str(e)}"

    return app
