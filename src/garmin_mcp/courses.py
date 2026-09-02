"""
Course management functions for Garmin Connect MCP Server.

Adds support for uploading GPX files as Garmin Connect Courses, listing courses,
getting detailed course waypoints/metadata, downloading course GPX files, and deleting courses.
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


from xml.sax.saxutils import escape as _xml_escape

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


_ACTIVITY_TYPE_IDS = {
    "running": 1,
    "cycling": 2,
    "hiking": 3,
    "walking": 4,
    "trail_running": 5,
    "mountain_biking": 6,
    "road_biking": 7,
    "gravel_cycling": 8,
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
    """Resolve the destination file path for downloading a course GPX."""
    if output_path:
        p = os.path.abspath(os.path.expanduser(output_path))
        if os.path.isdir(p) or output_path.endswith("/") or output_path.endswith("\\"):
            return os.path.join(p, f"{course_id}.gpx")
        return p

    env_dir = os.getenv("GARMIN_FIT_DOWNLOAD_DIR")
    if env_dir:
        base_dir = os.path.abspath(os.path.expanduser(env_dir))
    else:
        base_dir = os.path.abspath("./courses")

    return os.path.join(base_dir, f"{course_id}.gpx")


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
    async def get_course_details(course_id: int) -> str:
        """Get full details of a Garmin Connect course by ID.

        Returns course metadata, elevation gain/loss, total distance,
        and all custom course waypoints (shops, water, food, campgrounds, hazards).

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
                for cp in data.get("coursePoints", [])
            ]

            result = {
                "course_id": data.get("courseId"),
                "name": data.get("courseName"),
                "distance_m": data.get("distanceInMeters") or data.get("distanceMeter"),
                "elevation_gain_m": data.get("elevationGainInMeters") or data.get("elevationGainMeter"),
                "elevation_loss_m": data.get("elevationLossInMeters") or data.get("elevationLossMeter"),
                "activity": (data.get("activityType") or {}).get("typeKey"),
                "waypoints_count": len(course_points),
                "waypoints": course_points,
                "geo_points_count": len(data.get("geoPoints", [])),
                "url": f"https://connect.{garmin_client.client.domain}/modern/course/{course_id}",
            }
            return json.dumps(result, indent=2)
        except Exception as e:
            return f"Error fetching course details: {str(e)}"

    @app.tool()
    async def download_course_gpx(
        course_id: int,
        output_path: Optional[str] = None,
    ) -> str:
        """Download the exact official GPX file for a Garmin Connect course.

        Args:
            course_id: ID of the course to download.
            output_path: Optional local destination file or directory path.
                Defaults to GARMIN_FIT_DOWNLOAD_DIR or ./courses/{course_id}.gpx.
        """
        try:
            data = garmin_client.client.connectapi(f"/course-service/course/{course_id}")
            if not isinstance(data, dict) or "geoPoints" not in data:
                return f"Error: course {course_id} not found or missing geoPoints."

            name = _xml_escape(str(data.get("courseName", f"Garmin Course {course_id}")))
            geo_points = data.get("geoPoints", [])
            course_points = data.get("coursePoints", [])

            lines = [
                "<?xml version='1.0' encoding='UTF-8'?>",
                "<gpx version='1.1' creator='GarminConnectMCP' xmlns='http://www.topografix.com/GPX/1/1'>",
                "  <metadata>",
                f"    <name>{name}</name>",
                "  </metadata>",
            ]
            for cp in course_points:
                lat, lon = cp.get("lat"), cp.get("lon")
                if lat is not None and lon is not None:
                    cp_name = _xml_escape(str(cp.get("name") or cp.get("pointType") or "Waypoint"))
                    cp_type = _xml_escape(str(cp.get("pointType", "GENERIC")))
                    lines.append(f"  <wpt lat='{lat}' lon='{lon}'>")
                    lines.append(f"    <name>{cp_name}</name>")
                    lines.append(f"    <type>{cp_type}</type>")
                    lines.append("  </wpt>")

            lines.append("  <trk>")
            lines.append(f"    <name>{name}</name>")
            lines.append("    <trkseg>")
            for p in geo_points:
                lat = p.get("latitude")
                lon = p.get("longitude")
                ele = p.get("elevation")
                if lat is not None and lon is not None:
                    ele_tag = f"<ele>{ele}</ele>" if ele is not None else ""
                    lines.append(f"      <trkpt lat='{lat}' lon='{lon}'>{ele_tag}</trkpt>")
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

            # A path names the SERVER's disk in remote mode, so it is both
            # useless to the caller and an arbitrary-file-write primitive.
            # Hand the file back instead, as upload_course does in reverse.
            if is_remote_mode():
                if output_path:
                    return (
                        "Error: output_path is disabled in remote mode because it "
                        "would write to the server's filesystem, not yours. Omit it "
                        "and the GPX is returned inline."
                    )
                summary["gpx"] = gpx
                return json.dumps(summary, indent=2)

            target_path = _resolve_gpx_output_path(course_id, output_path)
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
