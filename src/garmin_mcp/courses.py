"""
Course management functions for Garmin Connect MCP Server.

Adds support for uploading GPX files as Garmin Connect Courses. The underlying
Garmin Connect endpoint is undocumented; this module reverse-engineers the
two-step flow used by the web UI:

    1) POST /course-service/course/import   (multipart upload of the GPX)
       -> returns a parsed course skeleton with geoPoints but no distance
          / bounding box / start point

    2) POST /course-service/course          (JSON, the actual save)
       -> server enriches with elevation gain/loss from terrain DB and
          returns the saved course with a courseId.

Both calls require the same OAuth2 bearer the rest of the MCP already uses.
"""

import io
import json
import math
import os
import pathlib
import re
from typing import Any, Dict, Optional

from mcp.server.fastmcp import Context

from garmin_mcp.client_resolver import get_client, is_remote_mode

# The garmin_client will be set by the main file
garmin_client = None


def configure(client):
    """Configure the module with the Garmin client instance"""
    global garmin_client
    garmin_client = client


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
    return (math.degrees(math.atan2(x, y)) + 360) % 360


# Map common activity keys to the Garmin activity type id Garmin's course
# service understands. The id list is small and stable.
_ACTIVITY_TYPE_IDS = {
    "running": 1,
    "cycling": 2,
    "hiking": 3,
    "walking": 9,
    "trail_running": 6,
    "mountain_biking": 5,
    "road_biking": 10,
    "gravel_cycling": 4,
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
    parsed: Dict[str, Any],
    course_name: str,
    activity_type_id: int,
    description: Optional[str],
) -> Dict[str, Any]:
    """Construct the create-course JSON body from the /import response."""

    geo_points = list(parsed.get("geoPoints") or [])
    if len(geo_points) < 2:
        raise ValueError("Parsed course has fewer than 2 geo points; GPX is empty or invalid")

    # Compute cumulative distance per point + total
    total_distance = 0.0
    for i, p in enumerate(geo_points):
        if i == 0:
            p["distance"] = 0.0
        else:
            total_distance += _haversine(geo_points[i - 1], p)
            p["distance"] = total_distance
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
        "speedMeterPerSecond": None,
        "courseLines": [
            {
                "courseId": None,
                "sortOrder": 1,
                "numberOfPoints": len(geo_points),
                "distanceInMeters": total_distance,
                "bearing": bearing,
                "points": geo_points,
                "coordinateSystem": "WGS84",
                "originalCoordinateSystem": "WGS84",
            }
        ],
        "coordinateSystem": "WGS84",
        "targetCoordinateSystem": "WGS84",
        "originalCoordinateSystem": "WGS84",
        "consumer": None,
        "elevationSource": 3,
        "hasPaceBand": False,
        "hasPowerGuide": False,
        "favorite": False,
        "startNote": None,
        "finishNote": None,
        "cutoffDuration": None,
        "geoPoints": geo_points,
    }


def register_tools(app):
    """Register course management tools"""

    @app.tool()
    async def get_courses(ctx: Context) -> str:
        """List all courses saved on Garmin Connect.

        Returns a curated list of courses with id, name, distance, activity type
        and creation date.
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
            return json.dumps({"count": len(curated), "courses": curated}, indent=2)
        except Exception as e:
            return f"Error listing courses: {str(e)}"

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
                from the GPX file.
            activity_type: One of running, cycling, hiking, walking, trail_running,
                mountain_biking, road_biking, gravel_cycling. Defaults to running.
            description: Optional description shown on the course detail page.
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
                        upload_filename,
                        gpx_bytes,
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
    async def delete_course(ctx: Context, course_id: int) -> str:
        """Delete a course from Garmin Connect.

        Args:
            course_id: ID of the course to delete (get IDs from get_courses).
        """
        try:
            get_client(ctx).client.delete(
                "connectapi", f"/course-service/course/{course_id}"
            )
            return json.dumps(
                {
                    "status": "success",
                    "course_id": course_id,
                    "message": f"Course {course_id} deleted",
                },
                indent=2,
            )
        except Exception as e:
            return f"Error deleting course: {str(e)}"

    return app
