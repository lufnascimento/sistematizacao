"""Pure geometry helpers for the C1 embedded-terrace E0 screening stage.

This module deliberately does not implement PCE, PCX, an embedded cross
section, earthwork, or guidance.  Elevation intervals are sensitivity inputs
only.  The generated geometries are therefore geometric precursors and cannot
be promoted to a TI or TD project.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np
from shapely.geometry import GeometryCollection, LineString, MultiLineString, Polygon
from shapely.ops import unary_union

try:
    from continuous_family import phase_contours
except ModuleNotFoundError:  # Allows ``python -m scripts...`` from the repo root.
    from scripts.continuous_family import phase_contours


SCREENING_BLOCKERS = (
    "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED",
    "VERTICAL_ACCURACY_NOT_VALIDATED",
    "PCE_SOLVER_NOT_RUN",
    "PCX_SOLVER_NOT_RUN",
    "TI_HYDRAULIC_FUNCTION_NOT_CONFIRMED",
    "EMBEDDED_SECTION_NOT_APPLIED",
    "PROPOSED_DTM_NOT_GENERATED",
    "ROW_CANDIDATES_DERIVED_FROM_CF0C_NOT_RESOLVED_PER_STRIP",
    "FLEET_NOT_VALIDATED",
    "GUIDANCE_NOT_AUTHORIZED",
)

TD_BLOCKERS = (
    "TD_RECEIVER_MISSING",
    "TD_GRADE_RULE_MISSING",
    "VERTICAL_ACCURACY_NOT_VALIDATED",
    "PCE_SOLVER_NOT_RUN",
    "PCX_SOLVER_NOT_RUN",
    "EMBEDDED_SECTION_NOT_APPLIED",
    "PROPOSED_DTM_NOT_GENERATED",
    "GUIDANCE_NOT_AUTHORIZED",
)


@dataclass(frozen=True)
class ScreeningPolicy:
    vertical_interval_candidates_m: tuple[float, ...] = (2.0, 4.0, 6.0)
    offset_fractions: tuple[float, ...] = (0.0, 0.25, 0.5, 0.75)
    topology_gap_half_width_m: float = 0.25
    minimum_axis_length_m: float = 15.0
    minimum_strip_area_m2: float = 50.0
    minimum_row_segment_length_m: float = 8.0

    def validate(self) -> "ScreeningPolicy":
        intervals = tuple(float(value) for value in self.vertical_interval_candidates_m)
        offsets = tuple(float(value) for value in self.offset_fractions)
        if not intervals or any(not math.isfinite(value) or value <= 0 for value in intervals):
            raise ValueError("vertical intervals must be finite and positive")
        if intervals != tuple(sorted(set(intervals))):
            raise ValueError("vertical intervals must be unique and increasing")
        if not offsets or any(not math.isfinite(value) or value < 0 or value >= 1 for value in offsets):
            raise ValueError("offset fractions must be finite and in [0, 1)")
        if offsets != tuple(sorted(set(offsets))):
            raise ValueError("offset fractions must be unique and increasing")
        for label, value in (
            ("topology gap half width", self.topology_gap_half_width_m),
            ("minimum axis length", self.minimum_axis_length_m),
            ("minimum strip area", self.minimum_strip_area_m2),
            ("minimum row segment length", self.minimum_row_segment_length_m),
        ):
            if not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"{label} must be finite and positive")
        return self


def iter_lines(geometry: Any) -> Iterable[LineString]:
    if geometry is None or geometry.is_empty:
        return
    if isinstance(geometry, LineString):
        yield geometry
        return
    if isinstance(geometry, (MultiLineString, GeometryCollection)):
        for part in geometry.geoms:
            yield from iter_lines(part)


def iter_polygons(geometry: Any) -> Iterable[Polygon]:
    if geometry is None or geometry.is_empty:
        return
    if isinstance(geometry, Polygon):
        yield geometry
        return
    if hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from iter_polygons(part)


def deterministic_geometry_key(geometry: Any) -> tuple[float, ...]:
    bounds = geometry.bounds
    return (
        round(float(bounds[0]), 8),
        round(float(bounds[1]), 8),
        round(float(bounds[2]), 8),
        round(float(bounds[3]), 8),
        round(float(geometry.area if hasattr(geometry, "area") else 0.0), 8),
        round(float(geometry.length), 8),
    )


def scenario_id(field_id: str, vertical_interval_m: float, offset_fraction: float) -> str:
    interval = f"{float(vertical_interval_m):.3f}".rstrip("0").rstrip(".").replace(".", "P")
    offset = f"{float(offset_fraction):.2f}".replace(".", "P")
    return f"C1E0_TI_{field_id}_VI{interval}_O{offset}"


def extract_ti_sensitivity_axes(
    elevation: np.ndarray,
    mask: np.ndarray,
    x_coordinates: np.ndarray | Sequence[float],
    y_coordinates: np.ndarray | Sequence[float],
    usable_geometry: Any,
    *,
    vertical_interval_m: float,
    offset_fraction: float,
    minimum_axis_length_m: float,
) -> list[dict[str, Any]]:
    """Extract terrain isolines for a non-agronomic TI sensitivity case."""

    interval = float(vertical_interval_m)
    offset = float(offset_fraction)
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("vertical_interval_m must be finite and positive")
    if not math.isfinite(offset) or offset < 0 or offset >= 1:
        raise ValueError("offset_fraction must be in [0, 1)")
    if not math.isfinite(float(minimum_axis_length_m)) or minimum_axis_length_m <= 0:
        raise ValueError("minimum_axis_length_m must be finite and positive")

    array = np.asarray(elevation, dtype=float)
    valid = np.asarray(mask, dtype=bool) & np.isfinite(array)
    if array.shape != valid.shape:
        raise ValueError("elevation and mask must have the same shape")
    if not np.any(valid):
        return []

    records = phase_contours(
        array,
        valid,
        np.asarray(x_coordinates, dtype=float),
        np.asarray(y_coordinates, dtype=float),
        interval,
        0.0,
        level_range_mask=valid,
        phase_offset_m=interval * offset,
    )
    axes: list[dict[str, Any]] = []
    for record in records:
        clipped = record["geometry"].intersection(usable_geometry)
        for part in iter_lines(clipped):
            if part.length + 1e-9 < minimum_axis_length_m:
                continue
            axes.append(
                {
                    "level_index": int(record["level_id"]),
                    "target_elevation_m": float(record["phase_level_m"]),
                    "geometry": part,
                    "length_m": float(part.length),
                }
            )
    axes.sort(key=lambda item: (item["target_elevation_m"], deterministic_geometry_key(item["geometry"])))
    return axes


def partition_interterrace_strips(
    usable_geometry: Any,
    axes: Sequence[LineString | dict[str, Any]],
    *,
    topology_gap_half_width_m: float,
    minimum_strip_area_m2: float,
) -> tuple[list[Polygon], dict[str, Any]]:
    """Partition a work polygon with a topology-only buffered axis gap."""

    half_width = float(topology_gap_half_width_m)
    minimum_area = float(minimum_strip_area_m2)
    if not math.isfinite(half_width) or half_width <= 0:
        raise ValueError("topology_gap_half_width_m must be finite and positive")
    if not math.isfinite(minimum_area) or minimum_area <= 0:
        raise ValueError("minimum_strip_area_m2 must be finite and positive")

    line_geometries = [item["geometry"] if isinstance(item, dict) else item for item in axes]
    line_geometries = [line for line in line_geometries if line is not None and not line.is_empty]
    if line_geometries:
        gap = unary_union(line_geometries).buffer(
            half_width,
            cap_style=2,
            join_style=2,
        )
        remaining = usable_geometry.difference(gap)
        gap_area = float(usable_geometry.intersection(gap).area)
    else:
        remaining = usable_geometry
        gap_area = 0.0

    all_parts = sorted(iter_polygons(remaining), key=deterministic_geometry_key)
    strips = [part for part in all_parts if part.area + 1e-9 >= minimum_area]
    excluded_area = float(sum(part.area for part in all_parts if part.area + 1e-9 < minimum_area))
    return strips, {
        "method": "AXIS_BUFFER_TOPOLOGY_GAP_NOT_CROSS_SECTION",
        "topology_gap_half_width_m": half_width,
        "topology_gap_area_m2": gap_area,
        "retained_strip_count": len(strips),
        "excluded_small_component_count": len(all_parts) - len(strips),
        "excluded_small_component_area_m2": excluded_area,
        "partitioned_area_m2": float(sum(strip.area for strip in strips)),
        "scope_note": "The gap is numerical partition support, not embedded-section width or lost plantable area.",
    }


def clip_rows_to_strips(
    source_rows: Sequence[dict[str, Any]],
    strips: Sequence[Polygon],
    *,
    minimum_segment_length_m: float,
) -> list[dict[str, Any]]:
    """Clip CF0C rows into diagnostic segments separated by screening axes."""

    minimum = float(minimum_segment_length_m)
    if not math.isfinite(minimum) or minimum <= 0:
        raise ValueError("minimum_segment_length_m must be finite and positive")
    results: list[dict[str, Any]] = []
    for strip_index, strip in enumerate(strips, start=1):
        for source in source_rows:
            intersection = source["geometry"].intersection(strip)
            parts = sorted(iter_lines(intersection), key=deterministic_geometry_key)
            for part_index, part in enumerate(parts, start=1):
                if part.length + 1e-9 < minimum:
                    continue
                results.append(
                    {
                        "source_row_id": str(source["source_row_id"]),
                        "source_family_id": str(source.get("source_family_id", "")),
                        "strip_index": strip_index,
                        "part_index": part_index,
                        "geometry": part,
                        "length_m": float(part.length),
                    }
                )
    results.sort(
        key=lambda item: (
            item["source_row_id"],
            item["strip_index"],
            item["part_index"],
            deterministic_geometry_key(item["geometry"]),
        )
    )
    return results


def segment_length_metrics(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    values = np.asarray([float(record["length_m"]) for record in records], dtype=float)
    if values.size == 0:
        return {
            "segment_count": 0,
            "total_length_m": 0.0,
            "length_p05_m": None,
            "length_p50_m": None,
            "length_p95_m": None,
            "length_max_m": None,
        }
    return {
        "segment_count": int(values.size),
        "total_length_m": float(values.sum()),
        "length_p05_m": float(np.percentile(values, 5)),
        "length_p50_m": float(np.percentile(values, 50)),
        "length_p95_m": float(np.percentile(values, 95)),
        "length_max_m": float(values.max()),
    }


__all__ = [
    "SCREENING_BLOCKERS",
    "TD_BLOCKERS",
    "ScreeningPolicy",
    "clip_rows_to_strips",
    "extract_ti_sensitivity_axes",
    "iter_lines",
    "iter_polygons",
    "partition_interterrace_strips",
    "scenario_id",
    "segment_length_metrics",
]
