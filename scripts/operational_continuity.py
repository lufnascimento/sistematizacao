"""Operational-continuity diagnostics for candidate sugarcane rows.

This module does not route a planter or authorize a maneuver.  It enforces the
geometric invariant that an analytical zone boundary cannot create a work stop:
every generated row must remain continuous until it reaches a physical work
edge.  Fleet envelope, headland suitability and obstacle access remain explicit
pending inputs at E0.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

import numpy as np
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from shapely.strtree import STRtree


GEOMETRY_BLOCKERS = {
    "INTERNAL_UNSUPPORTED_ENDPOINT",
    "SELF_INTERSECTION",
    "ROW_CROSSING",
    "ROW_OVERLAP",
    "ROW_TOUCH",
    "ROW_INTERSECTION",
    "CLOSED_LOOP_WITHOUT_APPROVED_ENTRY",
    "MINIMUM_RADIUS_VIOLATION",
}


@dataclass(frozen=True)
class ContinuityPolicy:
    terminal_surface_tolerance_m: float
    row_spacing_m: float
    spacing_tolerance_fraction: float = 0.20
    spacing_sample_interval_m: float = 12.0
    spacing_endpoint_trim_m: float = 3.0
    maximum_spacing_pair_distance_factor: float = 4.0
    required_minimum_radius_m: float | None = None

    def __post_init__(self) -> None:
        if self.terminal_surface_tolerance_m <= 0:
            raise ValueError("terminal_surface_tolerance_m must be positive")
        if self.row_spacing_m <= 0:
            raise ValueError("row_spacing_m must be positive")
        if not 0 <= self.spacing_tolerance_fraction < 1:
            raise ValueError("spacing_tolerance_fraction must be in [0, 1)")
        if self.spacing_sample_interval_m <= 0:
            raise ValueError("spacing_sample_interval_m must be positive")
        if self.spacing_endpoint_trim_m < 0:
            raise ValueError("spacing_endpoint_trim_m must be non-negative")
        if self.maximum_spacing_pair_distance_factor <= 0:
            raise ValueError("maximum_spacing_pair_distance_factor must be positive")
        if self.required_minimum_radius_m is not None and self.required_minimum_radius_m <= 0:
            raise ValueError("required_minimum_radius_m must be positive when provided")


def _surface_distance(point: Point, surface) -> float:
    if surface is None or surface.is_empty:
        return math.inf
    return float(point.distance(surface))


def classify_endpoint(point: Point, field: dict, tolerance_m: float) -> tuple[str, float]:
    """Classify a terminal against physical work edges, never analytic zones."""

    candidates = [
        ("HEADLAND_WORK_EDGE", _surface_distance(point, field.get("headland_work_edge"))),
        (
            "UNCLASSIFIED_OBSTACLE_EDGE",
            _surface_distance(point, field.get("unclassified_obstacle_edge")),
        ),
    ]
    if all(not math.isfinite(distance) for _, distance in candidates):
        candidates.append(("WORK_AREA_BOUNDARY_UNCLASSIFIED", point.distance(field["usable"].boundary)))
    priority = {
        "UNCLASSIFIED_OBSTACLE_EDGE": 0,
        "WORK_AREA_BOUNDARY_UNCLASSIFIED": 1,
        "HEADLAND_WORK_EDGE": 2,
    }
    minimum_distance = min(distance for _, distance in candidates)
    coincident = [
        (surface_type, distance)
        for surface_type, distance in candidates
        if distance <= minimum_distance + 1e-6
    ]
    surface_type, distance = min(
        coincident,
        key=lambda item: (priority.get(item[0], 99), item[1]),
    )
    if distance > tolerance_m:
        return "INTERNAL_UNSUPPORTED", float(distance)
    return surface_type, float(distance)


def _terminal_states(surface_type: str) -> tuple[str, str, list[str]]:
    if surface_type == "HEADLAND_WORK_EDGE":
        return (
            "WORK_TERMINATION_GEOMETRICALLY_SUPPORTED",
            "NOT_EVALUATED_MISSING_FLEET_ENVELOPE",
            ["HEADLAND_MANEUVER_NOT_VERIFIED"],
        )
    if surface_type == "UNCLASSIFIED_OBSTACLE_EDGE":
        return (
            "WORK_TERMINATION_REQUIRED_BY_OBSTACLE",
            "NOT_AUTHORIZED_UNCLASSIFIED_OBSTACLE",
            ["OBSTACLE_ACCESS_AND_MANEUVER_NOT_VERIFIED"],
        )
    if surface_type == "WORK_AREA_BOUNDARY_UNCLASSIFIED":
        return (
            "WORK_TERMINATION_GEOMETRICALLY_SUPPORTED",
            "NOT_EVALUATED_UNCLASSIFIED_BOUNDARY",
            ["TERMINAL_SURFACE_NOT_CLASSIFIED"],
        )
    return (
        "UNSUPPORTED_INTERNAL_TERMINATION",
        "PROHIBITED_NO_MANEUVER_SURFACE",
        ["INTERNAL_UNSUPPORTED_ENDPOINT"],
    )


def _discrete_radius_values(line: LineString) -> np.ndarray:
    coordinates = np.asarray(line.coords, dtype=float)[:, :2]
    radii: list[float] = []
    for first, middle, last in zip(coordinates[:-2], coordinates[1:-1], coordinates[2:]):
        side_a = float(np.linalg.norm(middle - first))
        side_b = float(np.linalg.norm(last - middle))
        side_c = float(np.linalg.norm(last - first))
        cross_twice_area = abs(float(np.cross(middle - first, last - first)))
        if min(side_a, side_b, side_c) <= 1e-9 or cross_twice_area <= 1e-9:
            continue
        radii.append(side_a * side_b * side_c / (2.0 * cross_twice_area))
    return np.asarray(radii, dtype=float)


def _radius_diagnostic(line: LineString, required_m: float | None) -> dict:
    radii = _discrete_radius_values(line)
    observed_min = float(radii.min()) if radii.size else None
    observed_p05 = float(np.percentile(radii, 5)) if radii.size else None
    if required_m is None:
        status = "NOT_EVALUATED_MISSING_STATIC_PATH_RADIUS_REQUIREMENT"
        blockers: list[str] = []
    elif observed_min is None or observed_min + 1e-9 >= required_m:
        status = "PASS_STATIC_PATH_RADIUS_ONLY"
        blockers = []
    else:
        status = "FAIL_STATIC_PATH_RADIUS"
        blockers = ["MINIMUM_RADIUS_VIOLATION"]
    return {
        "radius_min_observed_m": observed_min,
        "radius_p05_observed_m": observed_p05,
        "radius_required_m": required_m,
        "radius_status": status,
        "blockers": blockers,
    }


def _sample_line_interior(line: LineString, policy: ContinuityPolicy) -> Iterable[Point]:
    trim = min(policy.spacing_endpoint_trim_m, max(0.0, line.length * 0.25))
    start = trim
    stop = line.length - trim
    if stop <= start:
        yield line.interpolate(0.5, normalized=True)
        return
    count = max(1, int(math.ceil((stop - start) / policy.spacing_sample_interval_m)))
    for distance in np.linspace(start, stop, count + 1):
        yield line.interpolate(float(distance))


def geometric_spacing_metrics(records: list[dict], policy: ContinuityPolicy) -> dict:
    """Measure adjacent extracted rows directly; still an E0 sampling diagnostic."""

    by_level: dict[int, list[LineString]] = defaultdict(list)
    for record in records:
        by_level[int(record["level_id"])].append(record["geometry"])
    levels = sorted(by_level)
    values: list[float] = []
    unpaired = 0
    maximum_distance = policy.row_spacing_m * policy.maximum_spacing_pair_distance_factor
    for first_level, second_level in zip(levels[:-1], levels[1:]):
        first = unary_union(by_level[first_level])
        second = unary_union(by_level[second_level])
        for source_records, target in ((by_level[first_level], second), (by_level[second_level], first)):
            for line in source_records:
                for point in _sample_line_interior(line, policy):
                    distance = float(point.distance(target))
                    if distance <= maximum_distance:
                        values.append(distance)
                    else:
                        unpaired += 1
    array = np.asarray(values, dtype=float)
    if array.size:
        lower = policy.row_spacing_m * (1.0 - policy.spacing_tolerance_fraction)
        upper = policy.row_spacing_m * (1.0 + policy.spacing_tolerance_fraction)
        paired_outside_count = int(np.count_nonzero((array < lower) | (array > upper)))
        total_sample_count = int(array.size) + unpaired
        outside_paired = 100.0 * paired_outside_count / int(array.size)
        outside_total = 100.0 * (paired_outside_count + unpaired) / total_sample_count
        p05, p50, p95 = (float(np.percentile(array, level)) for level in (5, 50, 95))
        status = "E0_SAMPLED_DIAGNOSTIC"
    elif unpaired:
        p05 = p50 = p95 = outside_paired = None
        outside_total = 100.0
        status = "E0_SAMPLED_DIAGNOSTIC_ALL_SAMPLES_UNPAIRED"
    else:
        p05 = p50 = p95 = outside_paired = outside_total = None
        status = "NOT_EVALUATED_NO_ADJACENT_SAMPLES"
    return {
        "geometric_spacing_status": status,
        "geometric_spacing_sample_count": int(array.size),
        "geometric_spacing_unpaired_sample_count": int(unpaired),
        "geometric_spacing_p05_m": p05,
        "geometric_spacing_p50_m": p50,
        "geometric_spacing_p95_m": p95,
        "geometric_spacing_outside_tolerance_percent": outside_total,
        "geometric_spacing_paired_outside_tolerance_percent": outside_paired,
    }


def _pair_relation(first: LineString, second: LineString) -> str | None:
    if first.crosses(second):
        return "ROW_CROSSING"
    if first.overlaps(second):
        return "ROW_OVERLAP"
    if first.touches(second):
        return "ROW_TOUCH"
    if first.intersects(second):
        return "ROW_INTERSECTION"
    return None


def analyze_operational_continuity(
    records: list[dict],
    fields: list[dict],
    policy: ContinuityPolicy,
) -> dict:
    """Return deterministic per-line, node and scenario/field diagnostics."""

    field_by_code = {str(field["code"]): field for field in fields}
    diagnostics: dict[str, dict] = {}
    nodes: list[dict] = []
    group_records: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for record in records:
        line_id = record["line_id"]
        field_code = str(record["field"]["code"])
        scenario_id = record["scenario"]["id"]
        field = field_by_code[field_code]
        line = record["geometry"]
        blockers: set[str] = set()
        warnings: set[str] = set()
        if not line.is_simple:
            blockers.add("SELF_INTERSECTION")
        if line.is_ring:
            blockers.add("CLOSED_LOOP_WITHOUT_APPROVED_ENTRY")

        endpoint_payload = {}
        for endpoint_name, coordinate in (("START", line.coords[0]), ("END", line.coords[-1])):
            point = Point(coordinate[:2])
            surface_type, surface_distance = classify_endpoint(
                point, field, policy.terminal_surface_tolerance_m
            )
            termination_status, maneuver_status, endpoint_codes = _terminal_states(surface_type)
            for code in endpoint_codes:
                (blockers if code in GEOMETRY_BLOCKERS else warnings).add(code)
            node_id = f"{line_id}:{endpoint_name}"
            nodes.append(
                {
                    "node_id": node_id,
                    "line_id": line_id,
                    "scenario_id": scenario_id,
                    "field_code": field_code,
                    "endpoint": endpoint_name,
                    "geometry": point,
                    "surface_type": surface_type,
                    "surface_distance_m": surface_distance,
                    "termination_status": termination_status,
                    "maneuver_status": maneuver_status,
                    "qa_status": "FAIL" if "INTERNAL_UNSUPPORTED_ENDPOINT" in endpoint_codes else "E0_PENDING",
                    "blocker_codes": sorted(code for code in endpoint_codes if code in GEOMETRY_BLOCKERS),
                    "warning_codes": sorted(code for code in endpoint_codes if code not in GEOMETRY_BLOCKERS),
                }
            )
            endpoint_payload[endpoint_name.lower()] = {
                "node_id": node_id,
                "surface_type": surface_type,
                "surface_distance_m": surface_distance,
                "termination_status": termination_status,
                "maneuver_status": maneuver_status,
            }

        radius = _radius_diagnostic(line, policy.required_minimum_radius_m)
        blockers.update(radius.pop("blockers"))
        diagnostics[line_id] = {
            **endpoint_payload,
            **radius,
            "blocker_codes": blockers,
            "warning_codes": warnings,
        }
        group_records[(scenario_id, field_code)].append(record)

    group_relation_counts: dict[tuple[str, str], dict[str, int]] = {}
    for group_key, grouped in group_records.items():
        lines = [record["geometry"] for record in grouped]
        tree = STRtree(lines)
        counts: dict[str, int] = defaultdict(int)
        for first_index, first in enumerate(lines):
            for second_index in tree.query(first, predicate="intersects"):
                second_index = int(second_index)
                if second_index <= first_index:
                    continue
                relation = _pair_relation(first, lines[second_index])
                if relation is None:
                    continue
                counts[relation] += 1
                diagnostics[grouped[first_index]["line_id"]]["blocker_codes"].add(relation)
                diagnostics[grouped[second_index]["line_id"]]["blocker_codes"].add(relation)
        group_relation_counts[group_key] = dict(counts)

    for line_id, diagnostic in diagnostics.items():
        blockers = sorted(diagnostic["blocker_codes"])
        warnings = sorted(diagnostic["warning_codes"])
        diagnostic["blocker_codes"] = blockers
        diagnostic["warning_codes"] = warnings
        diagnostic["topology_status"] = "FAIL" if blockers else "PASS"
        diagnostic["operational_run_state"] = "ISOLATED_SEGMENT_E0"
        diagnostic["operational_continuity_status"] = (
            "FAIL_GEOMETRY" if blockers else "E0_GEOMETRY_ONLY_PENDING_FLEET_AND_MANEUVER_REVIEW"
        )

    group_summaries: dict[tuple[str, str], dict] = {}
    nodes_by_group: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for node in nodes:
        nodes_by_group[(node["scenario_id"], node["field_code"])].append(node)
    for group_key, grouped in group_records.items():
        grouped_diagnostics = [diagnostics[record["line_id"]] for record in grouped]
        grouped_nodes = nodes_by_group[group_key]
        relation_counts = group_relation_counts[group_key]
        blocker_codes = sorted(
            {code for diagnostic in grouped_diagnostics for code in diagnostic["blocker_codes"]}
        )
        warning_codes = sorted(
            {code for diagnostic in grouped_diagnostics for code in diagnostic["warning_codes"]}
        )
        spacing = geometric_spacing_metrics(grouped, policy)
        observed_radii = [
            diagnostic["radius_min_observed_m"]
            for diagnostic in grouped_diagnostics
            if diagnostic["radius_min_observed_m"] is not None
        ]
        group_summaries[group_key] = {
            "internal_endpoint_count": sum(
                node["surface_type"] == "INTERNAL_UNSUPPORTED" for node in grouped_nodes
            ),
            "headland_endpoint_count": sum(
                node["surface_type"] == "HEADLAND_WORK_EDGE" for node in grouped_nodes
            ),
            "unclassified_obstacle_endpoint_count": sum(
                node["surface_type"] == "UNCLASSIFIED_OBSTACLE_EDGE" for node in grouped_nodes
            ),
            "loop_count": sum(record["geometry"].is_ring for record in grouped),
            "self_intersection_count": sum(not record["geometry"].is_simple for record in grouped),
            "row_crossing_count": relation_counts.get("ROW_CROSSING", 0),
            "row_overlap_count": relation_counts.get("ROW_OVERLAP", 0),
            "row_touch_count": relation_counts.get("ROW_TOUCH", 0),
            "row_other_intersection_count": relation_counts.get("ROW_INTERSECTION", 0),
            "continuity_component_count": len(grouped),
            "radius_violation_line_count": sum(
                diagnostic["radius_status"] == "FAIL_STATIC_PATH_RADIUS"
                for diagnostic in grouped_diagnostics
            ),
            "radius_evaluation_status": (
                "STATIC_PATH_RADIUS_EVALUATED_NOT_MANEUVERABILITY"
                if policy.required_minimum_radius_m is not None
                else "NOT_EVALUATED_MISSING_STATIC_PATH_RADIUS_REQUIREMENT"
            ),
            "static_path_radius_min_observed_m": (
                float(min(observed_radii)) if observed_radii else None
            ),
            "static_path_radius_p05_observed_m": (
                float(np.percentile(observed_radii, 5)) if observed_radii else None
            ),
            "operational_continuity_status": (
                "FAIL_GEOMETRY" if blocker_codes else "E0_GEOMETRY_ONLY_PENDING_FLEET_AND_MANEUVER_REVIEW"
            ),
            "continuity_blocker_codes": ",".join(blocker_codes),
            "continuity_warning_codes": ",".join(warning_codes),
            **spacing,
        }

    return {
        "line_diagnostics": diagnostics,
        "nodes": nodes,
        "group_summaries": group_summaries,
        "policy": {
            "terminal_surface_tolerance_m": policy.terminal_surface_tolerance_m,
            "row_spacing_m": policy.row_spacing_m,
            "spacing_tolerance_fraction": policy.spacing_tolerance_fraction,
            "spacing_sample_interval_m": policy.spacing_sample_interval_m,
            "spacing_endpoint_trim_m": policy.spacing_endpoint_trim_m,
            "maximum_spacing_pair_distance_factor": policy.maximum_spacing_pair_distance_factor,
            "required_minimum_radius_m": policy.required_minimum_radius_m,
            "radius_interpretation": (
                "static row-centerline curvature diagnostic; does not validate headland turns, "
                "curvature transitions or swept envelopes"
            ),
        },
    }
