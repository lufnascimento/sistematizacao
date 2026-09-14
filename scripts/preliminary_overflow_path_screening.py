"""Spatial screening of declared overflow paths and receiving points."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


OVERFLOW_PATH_RELEASE = "PRELIMINARY_DECLARED_OVERFLOW_PATH_SCREENING_ONLY"
OVERFLOW_PATH_LIMITATIONS = (
    "DECLARED_GEOMETRY_ONLY",
    "NO_TERRAIN_DERIVED_FLOW_PATH",
    "NO_OVERFLOW_HYDROGRAPH_ROUTING",
    "NO_RECEIVER_CAPACITY_CHECK",
    "NO_CULVERT_OR_STRUCTURE_MODEL",
    "BARRIERS_REQUIRE_COMPLETE_PROJECT_INVENTORY",
    "NOT_PROJECT_EXECUTIVE",
    "NOT_GUIDANCE_AUTHORIZED",
)


def _distance_2d(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))


def _orientation(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> float:
    return (float(b[0]) - float(a[0])) * (float(c[1]) - float(a[1])) - (float(b[1]) - float(a[1])) * (float(c[0]) - float(a[0]))


def _point_segment_distance(point: Sequence[float], a: Sequence[float], b: Sequence[float]) -> float:
    dx, dy = float(b[0]) - float(a[0]), float(b[1]) - float(a[1])
    if dx == 0 and dy == 0:
        return _distance_2d(point, a)
    t = max(0.0, min(1.0, ((float(point[0]) - float(a[0])) * dx + (float(point[1]) - float(a[1])) * dy) / (dx * dx + dy * dy)))
    return math.hypot(float(point[0]) - (float(a[0]) + t * dx), float(point[1]) - (float(a[1]) + t * dy))


def _segments_intersect(a: Sequence[float], b: Sequence[float], c: Sequence[float], d: Sequence[float]) -> bool:
    o1, o2 = _orientation(a, b, c), _orientation(a, b, d)
    o3, o4 = _orientation(c, d, a), _orientation(c, d, b)
    eps = 1e-9
    if ((o1 > eps and o2 < -eps) or (o1 < -eps and o2 > eps)) and ((o3 > eps and o4 < -eps) or (o3 < -eps and o4 > eps)):
        return True
    return any(
        abs(o) <= eps and _point_segment_distance(p, x, y) <= eps
        for o, p, x, y in ((o1, c, a, b), (o2, d, a, b), (o3, a, c, d), (o4, b, c, d))
    )


def _segment_distance(a: Sequence[float], b: Sequence[float], c: Sequence[float], d: Sequence[float]) -> float:
    if _segments_intersect(a, b, c, d):
        return 0.0
    return min(
        _point_segment_distance(a, c, d), _point_segment_distance(b, c, d),
        _point_segment_distance(c, a, b), _point_segment_distance(d, a, b),
    )


def screen_overflow_paths(
    paths: Sequence[Mapping[str, Any]], receivers: Sequence[Mapping[str, Any]],
    barriers: Sequence[Mapping[str, Any]] = (), *, endpoint_tolerance_m: float = 2.0,
    elevation_tolerance_m: float = 0.05,
) -> dict[str, Any]:
    if not paths:
        raise ValueError("paths cannot be empty")
    if not math.isfinite(endpoint_tolerance_m) or endpoint_tolerance_m <= 0 or not math.isfinite(elevation_tolerance_m) or elevation_tolerance_m < 0:
        raise ValueError("screening tolerances are invalid")
    receiver_by_id = {str(item.get("id") or "").strip(): item for item in receivers}
    if "" in receiver_by_id or len(receiver_by_id) != len(receivers):
        raise ValueError("receiver ids must be non-empty and unique")
    results, seen = [], set()
    for index, path in enumerate(paths):
        path_id = str(path.get("id") or "").strip()
        reach_id = str(path.get("reach_id") or "").strip()
        receiver_id = str(path.get("receiver_id") or "").strip()
        if not path_id or path_id in seen or not reach_id or receiver_id not in receiver_by_id:
            raise ValueError(f"paths[{index}] has invalid identity or receiver")
        seen.add(path_id)
        coordinates = path.get("coordinates") or []
        if len(coordinates) < 2 or any(len(point) != 3 or not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) for value in point) for point in coordinates):
            raise ValueError(f"paths[{index}].coordinates must contain at least two finite XYZ points")
        receiver = receiver_by_id[receiver_id]
        receiver_point = receiver.get("coordinate") or []
        if len(receiver_point) != 3 or not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) for value in receiver_point):
            raise ValueError(f"receiver {receiver_id} requires a finite XYZ coordinate")
        horizontal_lengths = [_distance_2d(a, b) for a, b in zip(coordinates, coordinates[1:])]
        if any(length <= 0 for length in horizontal_lengths):
            raise ValueError(f"paths[{index}] contains a zero-length segment")
        rises = [float(b[2]) - float(a[2]) for a, b in zip(coordinates, coordinates[1:])]
        adverse = [position for position, rise in enumerate(rises) if rise > elevation_tolerance_m]
        # Compare against every earlier low point, not just the previous vertex.
        lowest_elevation = float(coordinates[0][2])
        maximum_accumulated_rise = 0.0
        for point in coordinates[1:]:
            elevation = float(point[2])
            maximum_accumulated_rise = max(maximum_accumulated_rise, elevation - lowest_elevation)
            lowest_elevation = min(lowest_elevation, elevation)
        endpoint_distance = _distance_2d(coordinates[-1], receiver_point)
        endpoint_vertical_difference = abs(float(coordinates[-1][2]) - float(receiver_point[2]))
        crossed = []
        for barrier in barriers:
            barrier_id = str(barrier.get("id") or "").strip()
            line = barrier.get("coordinates") or []
            buffer_m = float(barrier.get("buffer_m", 0.0))
            if not barrier_id or len(line) < 2 or not math.isfinite(buffer_m) or buffer_m < 0 or any(
                len(point) != 2 or not all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) for value in point)
                for point in line
            ):
                raise ValueError("barriers require id, line coordinates and non-negative buffer")
            if any(
                _segment_distance(a, b, c, d) <= buffer_m
                for a, b in zip(coordinates, coordinates[1:]) for c, d in zip(line, line[1:])
            ):
                crossed.append({"id": barrier_id, "type": str(barrier.get("type") or "OTHER"), "buffer_m": buffer_m})
        connected = endpoint_distance <= endpoint_tolerance_m and endpoint_vertical_difference <= elevation_tolerance_m
        status = "SCREENED_CLEAR" if connected and maximum_accumulated_rise <= elevation_tolerance_m and not crossed else "REQUIRES_REVIEW"
        results.append({
            "id": path_id, "reach_id": reach_id, "receiver_id": receiver_id,
            "coordinates": [[float(value) for value in point] for point in coordinates],
            "length_m": sum(horizontal_lengths), "elevation_drop_m": float(coordinates[0][2]) - float(coordinates[-1][2]),
            "minimum_segment_slope_m_m": min(-rise / length for rise, length in zip(rises, horizontal_lengths)),
            "maximum_adverse_rise_m": max([0.0, *rises]), "adverse_segment_indexes": adverse,
            "maximum_accumulated_adverse_rise_m": maximum_accumulated_rise,
            "endpoint_distance_to_receiver_m": endpoint_distance,
            "endpoint_vertical_difference_m": endpoint_vertical_difference,
            "receiver_connection_status": "CONNECTED_WITHIN_TOLERANCE" if connected else "NOT_CONNECTED",
            "crossed_barriers": crossed, "screening_status": status,
            "receiver_approved": False, "overflow_path_approved": False,
        })
    return {
        "release": OVERFLOW_PATH_RELEASE,
        "method": "DECLARED_3D_POLYLINE_GRAVITY_AND_BARRIER_SCREENING",
        "path_count": len(results), "screened_clear_count": sum(item["screening_status"] == "SCREENED_CLEAR" for item in results),
        "requires_review_count": sum(item["screening_status"] == "REQUIRES_REVIEW" for item in results),
        "connected_receiver_count": sum(item["receiver_connection_status"] == "CONNECTED_WITHIN_TOLERANCE" for item in results),
        "barrier_conflict_count": sum(bool(item["crossed_barriers"]) for item in results),
        "paths": results, "terrain_derived": False, "overflow_routed": False,
        "receivers_approved": False, "project_executive_authorized": False, "guidance_authorized": False,
        "limitations": list(OVERFLOW_PATH_LIMITATIONS),
    }


def validate_overflow_path_release(result: Mapping[str, Any]) -> None:
    if result.get("release") != OVERFLOW_PATH_RELEASE:
        raise ValueError("overflow path release changed")
    for key in ("terrain_derived", "overflow_routed", "receivers_approved", "project_executive_authorized", "guidance_authorized"):
        if result.get(key) is not False:
            raise ValueError(f"{key} must remain false")
    if any(item.get("receiver_approved") is not False or item.get("overflow_path_approved") is not False for item in result.get("paths", [])):
        raise ValueError("paths and receivers must remain unapproved")
    if not set(OVERFLOW_PATH_LIMITATIONS).issubset(set(result.get("limitations", []))):
        raise ValueError("overflow path limitations are incomplete")
