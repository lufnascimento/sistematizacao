"""Apply mandatory power-line work barriers to 3D agricultural work lines.

This V1 stage is deliberately conservative. It removes physical work inside a
buffered power-line corridor and emits independent work segments on either
side. It never creates a connector or asserts that a machine can cross the
barrier.

Typical invocation (QGIS Python):

    & 'C:\\Program Files\\QGIS 3.32.1\\bin\\python-qgis.bat' `
      '.\\scripts\\apply_constraint_barriers.py' `
      --work '.\\dataset\\derived\\sulcation_scenarios.gpkg' `
      --work-layer sulcation_lines `
      --barriers '.\\inputs\\power_lines.gpkg' `
      --barrier-layer power_lines `
      --half-width-m 10 `
      --min-fragment-m 8 `
      --target-crs EPSG:31982 `
      --output '.\\dataset\\derived\\sulcation_power_safe.gpkg'

Exit codes:
    0: success
    2: command-line usage error (argparse)
    3: invalid or incomplete input / failed mandatory validation
    4: unexpected processing or output error
    5: synthetic self-test failure
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import fiona
import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from pyproj import CRS
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union


SCHEMA_VERSION = "power-barrier-v1.0.0"
POWER_STATE = "POWER_LINE_BARRIER"
WORK_STATUS = "WORKED_OUTSIDE_POWER_BARRIER"
ISOLATED_CONTINUITY = "ISOLATED_WORK_SEGMENT"
SOURCE_ENDPOINT = "SOURCE_ENDPOINT"
BUFFER_RESOLUTION = 16
GEOMETRY_TOLERANCE_M = 1e-7
BALANCE_TOLERANCE_M = 1e-5

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_INPUT = 3
EXIT_PROCESSING = 4
EXIT_SELF_TEST = 5

RESERVED_WORK_FIELDS = {
    "run_id",
    "segment_id",
    "operational_run_id",
    "source_id",
    "source_part",
    "segment_seq",
    "start_m",
    "end_m",
    "length_m",
    "start_state",
    "end_state",
    "status",
    "continuity_state",
    "connector_generated",
}


class BarrierInputError(RuntimeError):
    """Raised when the mandatory barrier cannot be applied safely."""


class BarrierValidationError(RuntimeError):
    """Raised when generated output violates the V1 contract."""


@dataclass(frozen=True)
class RunParameters:
    half_width_m: float
    min_fragment_m: float
    target_crs: str | None = None
    work_crs: str | None = None
    barrier_crs: str | None = None
    id_field: str | None = None
    barrier_id_field: str | None = None


@dataclass(frozen=True)
class LinePart:
    source_id: str
    source_row: int
    source_part: int
    attributes: dict[str, Any]
    geometry: LineString


class LineZInterpolator:
    """Interpolate Z along a simple LineString using 2D chainage."""

    def __init__(self, line: LineString):
        coordinates = np.asarray(line.coords, dtype=float)
        if coordinates.ndim != 2 or coordinates.shape[0] < 2 or coordinates.shape[1] < 3:
            raise BarrierInputError("Every work line must be a finite LineStringZ.")
        if not np.isfinite(coordinates[:, :3]).all():
            raise BarrierInputError("Every work line must have finite X, Y and Z coordinates.")
        if not line.is_valid or line.is_empty:
            raise BarrierInputError("Work input contains an empty or invalid line.")
        line_2d = shapely.force_2d(line)
        if not line_2d.is_simple:
            raise BarrierInputError(
                "Self-crossing work lines are not supported because Z chainage would be ambiguous."
            )

        deltas = np.diff(coordinates[:, :2], axis=0)
        segment_lengths = np.sqrt(np.sum(deltas * deltas, axis=1))
        if np.any(segment_lengths <= GEOMETRY_TOLERANCE_M):
            raise BarrierInputError("Work input contains repeated or zero-length vertices.")

        self.coordinates = coordinates[:, :3]
        self.segment_lengths = segment_lengths
        self.cumulative = np.concatenate(([0.0], np.cumsum(segment_lengths)))
        self.length = float(self.cumulative[-1])
        self.line_2d = line_2d

    def chainage(self, x: float, y: float) -> float:
        point = np.array([float(x), float(y)], dtype=float)
        starts = self.coordinates[:-1, :2]
        vectors = self.coordinates[1:, :2] - starts
        denominators = np.sum(vectors * vectors, axis=1)
        fractions = np.sum((point - starts) * vectors, axis=1) / denominators
        fractions = np.clip(fractions, 0.0, 1.0)
        projections = starts + fractions[:, None] * vectors
        distance_sq = np.sum((projections - point) ** 2, axis=1)
        segment_index = int(np.argmin(distance_sq))
        distance = math.sqrt(float(distance_sq[segment_index]))
        if distance > 1e-4:
            raise BarrierValidationError(
                f"Generated vertex is {distance:.6f} m away from its source line."
            )
        return float(
            self.cumulative[segment_index]
            + fractions[segment_index] * self.segment_lengths[segment_index]
        )

    def z_at(self, chainage: float) -> float:
        distance = min(max(float(chainage), 0.0), self.length)
        index = int(np.searchsorted(self.cumulative, distance, side="right") - 1)
        index = min(index, len(self.segment_lengths) - 1)
        local = distance - self.cumulative[index]
        fraction = local / self.segment_lengths[index]
        z0 = self.coordinates[index, 2]
        z1 = self.coordinates[index + 1, 2]
        return float(z0 + fraction * (z1 - z0))

    def restore(self, line_2d: LineString) -> tuple[LineString, float, float]:
        if line_2d.is_empty or line_2d.length <= GEOMETRY_TOLERANCE_M:
            raise BarrierValidationError("Cannot restore Z on an empty line fragment.")
        points = list(line_2d.coords)
        chainages = [self.chainage(x, y) for x, y, *_ in points]
        if chainages[-1] < chainages[0]:
            points.reverse()
            chainages.reverse()
        coordinates = [
            (float(point[0]), float(point[1]), self.z_at(chainage))
            for point, chainage in zip(points, chainages)
        ]
        restored = LineString(coordinates)
        if not restored.has_z or not restored.is_valid:
            raise BarrierValidationError("Failed to build a valid LineStringZ fragment.")
        return restored, float(chainages[0]), float(chainages[-1])


def require(condition: bool, message: str, error_type=BarrierInputError) -> None:
    if not condition:
        raise error_type(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def rounded(value: float, digits: int = 9) -> float:
    return round(float(value), digits)


def iter_line_strings(geometry) -> Iterator[LineString]:
    if geometry is None or geometry.is_empty:
        return
    if geometry.geom_type == "LineString":
        yield geometry
        return
    if geometry.geom_type in {"MultiLineString", "GeometryCollection"}:
        for part in geometry.geoms:
            yield from iter_line_strings(part)


def iter_polygons(geometry) -> Iterator[Polygon]:
    if geometry is None or geometry.is_empty:
        return
    if geometry.geom_type == "Polygon":
        yield geometry
        return
    if geometry.geom_type in {"MultiPolygon", "GeometryCollection"}:
        for part in geometry.geoms:
            yield from iter_polygons(part)


def infer_layer(path: Path, requested: str | None, role: str) -> str:
    try:
        layers = list(fiona.listlayers(path))
    except Exception as exc:
        raise BarrierInputError(f"Could not inspect {role} datasource {path}: {exc}") from exc
    require(bool(layers), f"The {role} datasource has no readable layers: {path}")
    if requested:
        require(requested in layers, f"Layer {requested!r} is not present in {path}; found {layers}.")
        return requested
    require(
        len(layers) == 1,
        f"The {role} datasource has multiple layers; pass --{role}-layer. Found {layers}.",
    )
    return layers[0]


def load_vector(
    path: Path,
    layer: str | None,
    role: str,
    crs_override: str | None,
) -> tuple[gpd.GeoDataFrame, str]:
    require(path.exists(), f"Missing {role} datasource: {path}")
    selected_layer = infer_layer(path, layer, role)
    try:
        frame = gpd.read_file(path, layer=selected_layer)
    except Exception as exc:
        raise BarrierInputError(f"Could not read {role} layer {selected_layer!r}: {exc}") from exc
    require(not frame.empty, f"The mandatory {role} layer is empty.")
    require("geometry" in frame, f"The {role} layer has no geometry column.")
    require(not frame.geometry.isna().any(), f"The {role} layer contains null geometries.")
    require(not frame.geometry.is_empty.any(), f"The {role} layer contains empty geometries.")

    if frame.crs is None:
        require(
            crs_override is not None,
            f"The {role} layer has no CRS; provide --{role}-crs.",
        )
        try:
            frame = frame.set_crs(crs_override, allow_override=True)
        except Exception as exc:
            raise BarrierInputError(f"Invalid {role} CRS override: {crs_override}") from exc
    elif crs_override is not None:
        declared = CRS.from_user_input(frame.crs)
        supplied = CRS.from_user_input(crs_override)
        require(
            declared == supplied,
            f"The {role} layer declares {declared.to_string()}, but --{role}-crs is {supplied.to_string()}.",
        )
    return frame, selected_layer


def is_metric_projected(crs_value: Any) -> bool:
    crs = CRS.from_user_input(crs_value)
    if not crs.is_projected or not crs.axis_info:
        return False
    return all(
        axis.unit_conversion_factor is not None
        and math.isclose(float(axis.unit_conversion_factor), 1.0, rel_tol=0.0, abs_tol=1e-12)
        for axis in crs.axis_info[:2]
    )


def choose_target_crs(work: gpd.GeoDataFrame, requested: str | None) -> CRS:
    if requested:
        try:
            target = CRS.from_user_input(requested)
        except Exception as exc:
            raise BarrierInputError(f"Invalid target CRS: {requested}") from exc
        require(
            is_metric_projected(target),
            "--target-crs must be a projected CRS whose horizontal unit is metre.",
        )
        return target

    require(work.crs is not None, "Work CRS is required to select a metric processing CRS.")
    if is_metric_projected(work.crs):
        return CRS.from_user_input(work.crs)
    try:
        estimated = work.estimate_utm_crs()
    except Exception as exc:
        raise BarrierInputError(
            "Could not infer a local metric CRS; provide --target-crs explicitly."
        ) from exc
    require(
        estimated is not None and is_metric_projected(estimated),
        "Could not infer a local metric CRS; provide --target-crs explicitly.",
    )
    return CRS.from_user_input(estimated)


def reproject(frame: gpd.GeoDataFrame, target: CRS, role: str) -> gpd.GeoDataFrame:
    try:
        result = frame.to_crs(target)
    except Exception as exc:
        raise BarrierInputError(f"Could not reproject {role} to {target.to_string()}: {exc}") from exc
    require(not result.geometry.is_empty.any(), f"Reprojection produced an empty {role} geometry.")
    return result


def scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_, bool)):
        return int(bool(value))
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (dict, list, tuple, set)):
        return canonical_json(value)
    if isinstance(value, (str, int, float)):
        return value
    return str(value)


def rename_source_fields(columns: Sequence[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    used = set(RESERVED_WORK_FIELDS)
    for column in columns:
        candidate = str(column)
        if candidate in used:
            candidate = f"src_{candidate}"
        suffix = 2
        base = candidate
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        mapping[str(column)] = candidate
        used.add(candidate)
    return mapping


def stable_identifier(value: Any, fallback: str) -> str:
    candidate = fallback if value is None else str(value).strip()
    require(bool(candidate), "Feature identifiers cannot be blank.")
    return candidate


def build_work_parts(
    work: gpd.GeoDataFrame,
    id_field: str | None,
    field_mapping: dict[str, str],
) -> list[LinePart]:
    if id_field is not None:
        require(id_field in work.columns, f"--id-field {id_field!r} is not present in the work layer.")
        raw_ids = [stable_identifier(value, "") for value in work[id_field].tolist()]
        require(len(raw_ids) == len(set(raw_ids)), "--id-field values must be unique.")
    else:
        raw_ids = [f"FID-{index + 1:06d}" for index in range(len(work))]

    parts: list[LinePart] = []
    attribute_columns = [column for column in work.columns if column != work.geometry.name]
    for source_row, (_, row) in enumerate(work.iterrows()):
        geometry = row.geometry
        require(
            geometry.geom_type in {"LineString", "MultiLineString"},
            f"Work feature {raw_ids[source_row]} is {geometry.geom_type}; only line geometry is accepted.",
        )
        attributes = {
            field_mapping[str(column)]: scalar(row[column]) for column in attribute_columns
        }
        source_parts = list(iter_line_strings(geometry))
        require(bool(source_parts), f"Work feature {raw_ids[source_row]} has no line parts.")
        for part_index, part in enumerate(source_parts, start=1):
            LineZInterpolator(part)
            parts.append(
                LinePart(
                    source_id=raw_ids[source_row],
                    source_row=source_row,
                    source_part=part_index,
                    attributes=attributes,
                    geometry=part,
                )
            )
    return parts


def build_barrier_buffer(
    barriers: gpd.GeoDataFrame,
    half_width_m: float,
    barrier_id_field: str | None,
) -> tuple[Any, list[dict[str, Any]], float]:
    if barrier_id_field is not None:
        require(
            barrier_id_field in barriers.columns,
            f"--barrier-id-field {barrier_id_field!r} is not present in the barrier layer.",
        )

    source_buffers = []
    for row_number, (_, row) in enumerate(barriers.iterrows(), start=1):
        geometry = row.geometry
        require(
            geometry.geom_type in {"LineString", "MultiLineString"},
            f"Barrier feature {row_number} is {geometry.geom_type}; only line centerlines are accepted.",
        )
        require(geometry.is_valid, f"Barrier feature {row_number} is invalid.")
        line_2d = shapely.force_2d(geometry)
        require(line_2d.length > GEOMETRY_TOLERANCE_M, f"Barrier feature {row_number} has zero length.")
        source_id = stable_identifier(
            row[barrier_id_field] if barrier_id_field else None,
            f"POWER-SOURCE-{row_number:06d}",
        )
        buffered = line_2d.buffer(
            half_width_m,
            cap_style="round",
            join_style="round",
            resolution=BUFFER_RESOLUTION,
        )
        require(not buffered.is_empty and buffered.area > 0.0, f"Could not buffer barrier {source_id}.")
        source_buffers.append({"source_id": source_id, "geometry": buffered})

    require(bool(source_buffers), "The mandatory barrier layer has no usable line centerlines.")
    dissolved = unary_union([item["geometry"] for item in source_buffers])
    require(not dissolved.is_empty and dissolved.area > 0.0, "The dissolved power barrier is empty.")
    require(dissolved.is_valid, "The dissolved power barrier is invalid.")
    sum_area = float(sum(item["geometry"].area for item in source_buffers))
    require(
        dissolved.area <= sum_area + BALANCE_TOLERANCE_M,
        "Dissolved barrier area exceeds the sum of source buffer areas.",
        BarrierValidationError,
    )

    polygons = sorted(
        iter_polygons(dissolved),
        key=lambda polygon: (
            rounded(polygon.bounds[0]),
            rounded(polygon.bounds[1]),
            rounded(polygon.bounds[2]),
            rounded(polygon.bounds[3]),
            rounded(polygon.area),
        ),
    )
    outputs = []
    for index, polygon in enumerate(polygons, start=1):
        source_ids = sorted(
            item["source_id"]
            for item in source_buffers
            if item["geometry"].intersects(polygon.representative_point())
            or item["geometry"].intersection(polygon).area > GEOMETRY_TOLERANCE_M
        )
        outputs.append(
            {
                "barrier_id": f"PB-{index:06d}",
                "state": POWER_STATE,
                "half_width_m": float(half_width_m),
                "area_m2": float(polygon.area),
                "source_count": len(source_ids),
                "source_ids": "|".join(source_ids),
                "geometry": polygon,
            }
        )
    return dissolved, outputs, sum_area


def barrier_ids_for_fragment(fragment: LineString, barriers: Sequence[dict[str, Any]]) -> str:
    identifiers = [
        item["barrier_id"]
        for item in barriers
        if fragment.intersection(item["geometry"]).length > GEOMETRY_TOLERANCE_M
        or fragment.distance(item["geometry"]) <= GEOMETRY_TOLERANCE_M
    ]
    return "|".join(sorted(identifiers))


def endpoint_state(chainage: float, source_length: float) -> str:
    if chainage <= GEOMETRY_TOLERANCE_M or source_length - chainage <= GEOMETRY_TOLERANCE_M:
        return SOURCE_ENDPOINT
    return POWER_STATE


def compute_run_id(
    parts: Sequence[LinePart],
    barrier_geometry,
    parameters: RunParameters,
    target: CRS,
) -> str:
    digest = hashlib.sha256()
    digest.update(SCHEMA_VERSION.encode("ascii"))
    digest.update(canonical_json({
        "half_width_m": rounded(parameters.half_width_m),
        "min_fragment_m": rounded(parameters.min_fragment_m),
        "target_crs": target.to_string(),
    }).encode("ascii"))
    for part in parts:
        digest.update(part.source_id.encode("utf-8"))
        digest.update(str(part.source_part).encode("ascii"))
        digest.update(shapely.normalize(part.geometry).wkb)
    digest.update(shapely.normalize(barrier_geometry).wkb)
    return f"PBR-{digest.hexdigest()[:20]}"


def apply_barriers(
    parts: Sequence[LinePart],
    barrier_geometry,
    barrier_records: Sequence[dict[str, Any]],
    parameters: RunParameters,
    run_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, float | int]]:
    worked: list[dict[str, Any]] = []
    breaks: list[dict[str, Any]] = []
    source_length_m = 0.0
    kept_length_m = 0.0
    power_break_length_m = 0.0
    discarded_fragment_length_m = 0.0
    maximum_part_balance_error_m = 0.0

    for part in parts:
        interpolator = LineZInterpolator(part.geometry)
        source_line_2d = interpolator.line_2d
        source_length_m += interpolator.length
        try:
            outside = source_line_2d.difference(barrier_geometry)
            removed = source_line_2d.intersection(barrier_geometry)
        except Exception as exc:
            raise BarrierValidationError(
                f"Geometry overlay failed for work feature {part.source_id}: {exc}"
            ) from exc

        kept_candidates = []
        for fragment in iter_line_strings(outside):
            if fragment.length <= GEOMETRY_TOLERANCE_M:
                continue
            geometry_z, start_m, end_m = interpolator.restore(fragment)
            kept_candidates.append((start_m, end_m, geometry_z))
        kept_candidates.sort(key=lambda item: (rounded(item[0]), rounded(item[1])))

        removed_candidates = []
        for fragment in iter_line_strings(removed):
            if fragment.length <= GEOMETRY_TOLERANCE_M:
                continue
            geometry_z, start_m, end_m = interpolator.restore(fragment)
            removed_candidates.append((start_m, end_m, geometry_z))
        removed_candidates.sort(key=lambda item: (rounded(item[0]), rounded(item[1])))

        part_kept = 0.0
        part_discarded = 0.0
        for _, _, geometry_z in kept_candidates:
            if geometry_z.length + GEOMETRY_TOLERANCE_M < parameters.min_fragment_m:
                part_discarded += geometry_z.length
            else:
                part_kept += geometry_z.length
        part_removed = float(sum(item[2].length for item in removed_candidates))
        part_error = abs(interpolator.length - part_kept - part_discarded - part_removed)
        maximum_part_balance_error_m = max(maximum_part_balance_error_m, part_error)
        require(
            part_error <= BALANCE_TOLERANCE_M,
            (
                f"Length balance failed for {part.source_id} part {part.source_part}: "
                f"error={part_error:.9f} m."
            ),
            BarrierValidationError,
        )

        segment_sequence = 0
        for start_m, end_m, geometry_z in kept_candidates:
            if geometry_z.length + GEOMETRY_TOLERANCE_M < parameters.min_fragment_m:
                discarded_fragment_length_m += geometry_z.length
                continue
            segment_sequence += 1
            segment_id = (
                f"{run_id}:WS:{part.source_row + 1:06d}:"
                f"{part.source_part:03d}:{segment_sequence:04d}"
            )
            properties = dict(part.attributes)
            properties.update(
                {
                    "run_id": run_id,
                    "segment_id": segment_id,
                    "operational_run_id": segment_id,
                    "source_id": part.source_id,
                    "source_part": part.source_part,
                    "segment_seq": segment_sequence,
                    "start_m": rounded(start_m, 6),
                    "end_m": rounded(end_m, 6),
                    "length_m": rounded(geometry_z.length, 6),
                    "start_state": endpoint_state(start_m, interpolator.length),
                    "end_state": endpoint_state(end_m, interpolator.length),
                    "start_termination_status": (
                        "WORK_TERMINATION_REQUIRED_BY_POWER_BARRIER"
                        if endpoint_state(start_m, interpolator.length) == POWER_STATE
                        else "SOURCE_TERMINATION_PRESERVED"
                    ),
                    "end_termination_status": (
                        "WORK_TERMINATION_REQUIRED_BY_POWER_BARRIER"
                        if endpoint_state(end_m, interpolator.length) == POWER_STATE
                        else "SOURCE_TERMINATION_PRESERVED"
                    ),
                    "start_maneuver_status": (
                        "PROHIBITED_POWER_BARRIER"
                        if endpoint_state(start_m, interpolator.length) == POWER_STATE
                        else "SOURCE_STATUS_PRESERVED"
                    ),
                    "end_maneuver_status": (
                        "PROHIBITED_POWER_BARRIER"
                        if endpoint_state(end_m, interpolator.length) == POWER_STATE
                        else "SOURCE_STATUS_PRESERVED"
                    ),
                    "status": WORK_STATUS,
                    "continuity_state": ISOLATED_CONTINUITY,
                    "connector_generated": 0,
                    "geometry": geometry_z,
                }
            )
            worked.append(properties)
            kept_length_m += geometry_z.length

        for break_sequence, (start_m, end_m, geometry_z) in enumerate(
            removed_candidates, start=1
        ):
            break_id = (
                f"{run_id}:WB:{part.source_row + 1:06d}:"
                f"{part.source_part:03d}:{break_sequence:04d}"
            )
            breaks.append(
                {
                    "run_id": run_id,
                    "break_id": break_id,
                    "source_id": part.source_id,
                    "source_part": part.source_part,
                    "break_seq": break_sequence,
                    "start_m": rounded(start_m, 6),
                    "end_m": rounded(end_m, 6),
                    "length_m": rounded(geometry_z.length, 6),
                    "state": POWER_STATE,
                    "connector_generated": 0,
                    "barrier_ids": barrier_ids_for_fragment(geometry_z, barrier_records),
                    "geometry": geometry_z,
                }
            )
            power_break_length_m += geometry_z.length

    global_error = abs(
        source_length_m
        - kept_length_m
        - power_break_length_m
        - discarded_fragment_length_m
    )
    require(
        global_error <= BALANCE_TOLERANCE_M,
        f"Global length balance error is {global_error:.9f} m.",
        BarrierValidationError,
    )

    worked.sort(key=lambda item: item["segment_id"])
    breaks.sort(key=lambda item: item["break_id"])
    metrics: dict[str, float | int] = {
        "source_feature_count": len({(part.source_row, part.source_id) for part in parts}),
        "source_part_count": len(parts),
        "source_length_m": rounded(source_length_m, 6),
        "worked_segment_count": len(worked),
        "worked_length_m": rounded(kept_length_m, 6),
        "work_break_count": len(breaks),
        "power_break_length_m": rounded(power_break_length_m, 6),
        "discarded_fragment_length_m": rounded(discarded_fragment_length_m, 6),
        "length_balance_error_m": rounded(global_error, 9),
        "maximum_part_balance_error_m": rounded(maximum_part_balance_error_m, 9),
        "connector_count": 0,
    }
    return worked, breaks, metrics


def records_to_frame(
    records: Sequence[dict[str, Any]],
    crs: CRS,
    columns_if_empty: Sequence[str],
) -> gpd.GeoDataFrame:
    if records:
        return gpd.GeoDataFrame(list(records), geometry="geometry", crs=crs)
    data = {column: pd.Series(dtype="object") for column in columns_if_empty if column != "geometry"}
    return gpd.GeoDataFrame(data, geometry=gpd.GeoSeries([], crs=crs), crs=crs)


def normalize_output_values(frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    result = frame.copy()
    for column in result.columns:
        if column == result.geometry.name:
            continue
        if result[column].dtype == "object":
            result[column] = result[column].map(scalar)
    return result


def write_output(
    output: Path,
    metadata_output: Path,
    worked: Sequence[dict[str, Any]],
    barrier_records: Sequence[dict[str, Any]],
    breaks: Sequence[dict[str, Any]],
    metadata: dict[str, Any],
    crs: CRS,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.stem}.{os.getpid()}.tmp.gpkg")
    ready = output.with_name(f".{output.stem}.{os.getpid()}.ready.gpkg")
    temporary_metadata = metadata_output.with_name(
        f".{metadata_output.stem}.{os.getpid()}.tmp.json"
    )
    for path in (temporary, ready, temporary_metadata):
        if path.exists():
            path.unlink()

    worked_columns = sorted(
        set(RESERVED_WORK_FIELDS)
        | {key for record in worked for key in record if key != "geometry"}
    ) + ["geometry"]
    worked_frame = records_to_frame(worked, crs, worked_columns)
    barrier_frame = records_to_frame(
        barrier_records,
        crs,
        [
            "run_id",
            "barrier_id",
            "state",
            "half_width_m",
            "area_m2",
            "source_count",
            "source_ids",
            "geometry",
        ],
    )
    break_frame = records_to_frame(
        breaks,
        crs,
        [
            "run_id",
            "break_id",
            "source_id",
            "source_part",
            "break_seq",
            "start_m",
            "end_m",
            "length_m",
            "state",
            "connector_generated",
            "barrier_ids",
            "geometry",
        ],
    )
    frames = {
        "worked_segments": normalize_output_values(worked_frame),
        "power_barriers": normalize_output_values(barrier_frame),
        "work_breaks": normalize_output_values(break_frame),
    }

    try:
        for layer_name, frame in frames.items():
            frame.to_file(
                temporary,
                layer=layer_name,
                driver="GPKG",
                index=False,
            )
        metadata_text = json.dumps(metadata, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
        database = sqlite3.connect(temporary)
        try:
            database.execute(
                "CREATE TABLE constraint_barrier_metadata "
                "(run_id TEXT PRIMARY KEY, schema_version TEXT NOT NULL, metadata_json TEXT NOT NULL)"
            )
            database.execute(
                "INSERT INTO constraint_barrier_metadata(run_id, schema_version, metadata_json) "
                "VALUES (?, ?, ?)",
                (metadata["run_id"], SCHEMA_VERSION, metadata_text),
            )
            database.commit()
        finally:
            database.close()
        temporary_metadata.write_text(metadata_text, encoding="utf-8")

        # Fiona/GDAL can retain a Windows sharing lock that prevents renaming
        # the datasource it just created. Copying to a closed staging file and
        # atomically replacing from that file avoids publishing a partial GPKG.
        shutil.copy2(temporary, ready)
        if output.exists():
            output.unlink()
        ready.replace(output)
        temporary.unlink()
        if metadata_output.exists():
            metadata_output.unlink()
        temporary_metadata.replace(metadata_output)
    except Exception:
        for path in (temporary, ready, temporary_metadata):
            if path.exists():
                path.unlink()
        raise


def validate_generated(
    output: Path,
    metadata_output: Path,
    barrier_geometry,
    expected_crs: CRS,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    require(output.exists() and output.stat().st_size > 0, "Output GeoPackage is missing.", BarrierValidationError)
    require(
        metadata_output.exists() and metadata_output.stat().st_size > 0,
        "Output metadata JSON is missing.",
        BarrierValidationError,
    )
    layers = list(fiona.listlayers(output))
    require(
        layers
        == [
            "worked_segments",
            "power_barriers",
            "work_breaks",
            "constraint_barrier_metadata",
        ],
        f"Unexpected GeoPackage layers or order: {layers}",
        BarrierValidationError,
    )

    worked = gpd.read_file(output, layer="worked_segments")
    barriers = gpd.read_file(output, layer="power_barriers")
    breaks = gpd.read_file(output, layer="work_breaks")
    for name, frame in (("worked_segments", worked), ("power_barriers", barriers), ("work_breaks", breaks)):
        require(frame.crs is not None, f"Layer {name} has no CRS.", BarrierValidationError)
        require(
            CRS.from_user_input(frame.crs) == expected_crs,
            f"Layer {name} has an unexpected CRS.",
            BarrierValidationError,
        )
        require(not (~frame.geometry.is_valid).any(), f"Layer {name} contains invalid geometry.", BarrierValidationError)

    outside_overlap_m = float(
        sum(shapely.force_2d(geometry).intersection(barrier_geometry).length for geometry in worked.geometry)
    )
    non_3d_work_count = int(sum(not geometry.has_z for geometry in worked.geometry))
    non_3d_break_count = int(sum(not geometry.has_z for geometry in breaks.geometry))
    duplicate_segment_ids = int(worked["segment_id"].duplicated().sum()) if not worked.empty else 0
    duplicate_break_ids = int(breaks["break_id"].duplicated().sum()) if not breaks.empty else 0
    wrong_states = int((breaks["state"] != POWER_STATE).sum()) if not breaks.empty else 0
    connector_count = int(worked["connector_generated"].sum()) if not worked.empty else 0
    connector_count += int(breaks["connector_generated"].sum()) if not breaks.empty else 0

    require(outside_overlap_m <= BALANCE_TOLERANCE_M, f"Worked output overlaps the power barrier by {outside_overlap_m:.9f} m.", BarrierValidationError)
    require(non_3d_work_count == 0, "A worked segment lost its Z coordinate.", BarrierValidationError)
    require(non_3d_break_count == 0, "A work break lost its Z coordinate.", BarrierValidationError)
    require(duplicate_segment_ids == 0, "Duplicate segment IDs were generated.", BarrierValidationError)
    require(duplicate_break_ids == 0, "Duplicate break IDs were generated.", BarrierValidationError)
    require(wrong_states == 0, "A work break has a state other than POWER_LINE_BARRIER.", BarrierValidationError)
    require(connector_count == 0, "The barrier stage generated a connector.", BarrierValidationError)

    loaded_metadata = json.loads(metadata_output.read_text(encoding="utf-8"))
    require(loaded_metadata == metadata, "Sidecar metadata differs from declared metadata.", BarrierValidationError)
    database = sqlite3.connect(output)
    try:
        stored = database.execute(
            "SELECT metadata_json FROM constraint_barrier_metadata WHERE run_id = ?",
            (metadata["run_id"],),
        ).fetchone()
    finally:
        database.close()
    require(stored is not None, "GeoPackage metadata record is missing.", BarrierValidationError)
    require(json.loads(stored[0]) == metadata, "Embedded metadata differs from sidecar JSON.", BarrierValidationError)

    return {
        "status": "verified",
        "layers": layers,
        "worked_segment_count": len(worked),
        "power_barrier_count": len(barriers),
        "work_break_count": len(breaks),
        "worked_overlap_with_barrier_m": rounded(outside_overlap_m, 9),
        "non_3d_work_count": non_3d_work_count,
        "non_3d_break_count": non_3d_break_count,
        "duplicate_segment_id_count": duplicate_segment_ids,
        "duplicate_break_id_count": duplicate_break_ids,
        "connector_count": connector_count,
    }


def run_pipeline(
    work_path: Path,
    work_layer: str | None,
    barrier_path: Path,
    barrier_layer: str | None,
    output: Path,
    metadata_output: Path,
    parameters: RunParameters,
) -> dict[str, Any]:
    require(parameters.half_width_m > 0.0 and math.isfinite(parameters.half_width_m), "--half-width-m must be finite and greater than zero.")
    require(parameters.min_fragment_m >= 0.0 and math.isfinite(parameters.min_fragment_m), "--min-fragment-m must be finite and zero or greater.")
    require(output.suffix.lower() == ".gpkg", "--output must use the .gpkg extension.")
    require(output.resolve() != work_path.resolve(), "--output must not overwrite the work input.")
    require(output.resolve() != barrier_path.resolve(), "--output must not overwrite the barrier input.")

    work, selected_work_layer = load_vector(
        work_path, work_layer, "work", parameters.work_crs
    )
    barriers, selected_barrier_layer = load_vector(
        barrier_path, barrier_layer, "barrier", parameters.barrier_crs
    )
    target = choose_target_crs(work, parameters.target_crs)
    work = reproject(work, target, "work")
    barriers = reproject(barriers, target, "barrier")

    field_mapping = rename_source_fields(
        [column for column in work.columns if column != work.geometry.name]
    )
    parts = build_work_parts(work, parameters.id_field, field_mapping)
    barrier_geometry, barrier_records, sum_buffer_area = build_barrier_buffer(
        barriers, parameters.half_width_m, parameters.barrier_id_field
    )
    run_id = compute_run_id(parts, barrier_geometry, parameters, target)
    for record in barrier_records:
        record["run_id"] = run_id

    worked, breaks, metrics = apply_barriers(
        parts, barrier_geometry, barrier_records, parameters, run_id
    )
    barrier_area = float(sum(record["geometry"].area for record in barrier_records))
    area_balance_error = abs(barrier_area - barrier_geometry.area)
    require(
        area_balance_error <= BALANCE_TOLERANCE_M,
        f"Barrier polygon area balance error is {area_balance_error:.9f} m2.",
        BarrierValidationError,
    )

    metadata: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "status": "POWER_LINE_BARRIER_APPLIED",
        "policy": {
            "barrier_state": POWER_STATE,
            "creates_connectors": False,
            "continuity_across_barrier": False,
            "meaning": "Physical work is removed; machine crossing is neither generated nor approved.",
        },
        "inputs": {
            "work": {
                "path": str(work_path.resolve()),
                "layer": selected_work_layer,
                "sha256": sha256_file(work_path),
                "declared_crs": CRS.from_user_input(work.crs).to_string(),
            },
            "barriers": {
                "path": str(barrier_path.resolve()),
                "layer": selected_barrier_layer,
                "sha256": sha256_file(barrier_path),
                "declared_crs": CRS.from_user_input(barriers.crs).to_string(),
            },
        },
        "parameters": {
            "half_width_m": parameters.half_width_m,
            "min_fragment_m": parameters.min_fragment_m,
            "target_crs": target.to_string(),
            "buffer_cap_style": "round",
            "buffer_join_style": "round",
            "buffer_resolution": BUFFER_RESOLUTION,
            "geometry_tolerance_m": GEOMETRY_TOLERANCE_M,
            "balance_tolerance_m": BALANCE_TOLERANCE_M,
            "id_field": parameters.id_field,
            "barrier_id_field": parameters.barrier_id_field,
        },
        "field_mapping": field_mapping,
        "metrics": {
            **metrics,
            "barrier_source_feature_count": len(barriers),
            "dissolved_barrier_count": len(barrier_records),
            "source_buffer_area_sum_m2": rounded(sum_buffer_area, 6),
            "dissolved_barrier_area_m2": rounded(barrier_area, 6),
            "barrier_overlap_area_m2": rounded(sum_buffer_area - barrier_area, 6),
            "barrier_area_balance_error_m2": rounded(area_balance_error, 9),
        },
        "validation": {
            "length_balance": "PASS",
            "area_balance": "PASS",
            "worked_segments_outside_barrier": "PENDING_OUTPUT_READBACK",
            "line_geometry_valid": "PENDING_OUTPUT_READBACK",
            "z_preserved_by_chainage_interpolation": "PENDING_OUTPUT_READBACK",
            "determinism": "RUN_ID_AND_STABLE_FEATURE_ORDER",
            "connector_count": 0,
        },
        "outputs": {
            "gpkg": str(output.resolve()),
            "metadata_json": str(metadata_output.resolve()),
            "layers": ["worked_segments", "power_barriers", "work_breaks"],
        },
    }

    write_output(
        output,
        metadata_output,
        worked,
        barrier_records,
        breaks,
        metadata,
        target,
    )
    validation = validate_generated(
        output, metadata_output, barrier_geometry, target, metadata
    )
    metadata["validation"].update(
        {
            "worked_segments_outside_barrier": "PASS",
            "line_geometry_valid": "PASS",
            "z_preserved_by_chainage_interpolation": "PASS",
            "output_readback": validation,
        }
    )
    # Re-write only metadata after output readback so embedded and sidecar copies agree.
    metadata_text = json.dumps(metadata, ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    metadata_output.write_text(metadata_text, encoding="utf-8")
    database = sqlite3.connect(output)
    try:
        database.execute(
            "UPDATE constraint_barrier_metadata SET metadata_json = ? WHERE run_id = ?",
            (metadata_text, run_id),
        )
        database.commit()
    finally:
        database.close()
    final_validation = validate_generated(
        output, metadata_output, barrier_geometry, target, metadata
    )
    return {
        "status": "success",
        "run_id": run_id,
        "output": str(output),
        "metadata": str(metadata_output),
        "metrics": metadata["metrics"],
        "validation": final_validation,
    }


def canonical_layer_snapshot(path: Path, layer: str) -> list[dict[str, Any]]:
    frame = gpd.read_file(path, layer=layer)
    rows = []
    for _, row in frame.iterrows():
        properties = {
            column: scalar(row[column])
            for column in frame.columns
            if column != frame.geometry.name
        }
        geometry = row.geometry
        coordinate_text = shapely.to_wkt(geometry, rounding_precision=9, trim=True, output_dimension=3)
        rows.append({"properties": properties, "geometry": coordinate_text})
    rows.sort(key=canonical_json)
    return rows


def write_synthetic_inputs(directory: Path) -> tuple[Path, Path]:
    work_path = directory / "synthetic_work.gpkg"
    barrier_path = directory / "synthetic_barriers.gpkg"
    work = gpd.GeoDataFrame(
        {
            "work_id": ["ROW-A", "ROW-B"],
            "field_code": ["F1", "F1"],
        },
        geometry=[
            LineString([(0.0, 0.0, 100.0), (100.0, 0.0, 110.0)]),
            LineString([(0.0, 20.0, 200.0), (100.0, 20.0, 200.0)]),
        ],
        crs="EPSG:31982",
    )
    barriers = gpd.GeoDataFrame(
        {"power_id": ["PL-1"]},
        geometry=[LineString([(50.0, -10.0), (50.0, 10.0)])],
        crs="EPSG:31982",
    )
    work.to_file(work_path, layer="work", driver="GPKG", index=False)
    barriers.to_file(barrier_path, layer="power", driver="GPKG", index=False)
    return work_path, barrier_path


def run_self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="power-barrier-self-test-") as temporary:
        directory = Path(temporary)
        work_path, barrier_path = write_synthetic_inputs(directory)
        parameters = RunParameters(
            half_width_m=5.0,
            min_fragment_m=0.0,
            target_crs="EPSG:31982",
            id_field="work_id",
            barrier_id_field="power_id",
        )
        output_a = directory / "result_a.gpkg"
        output_b = directory / "result_b.gpkg"
        result_a = run_pipeline(
            work_path,
            "work",
            barrier_path,
            "power",
            output_a,
            directory / "result_a.metadata.json",
            parameters,
        )
        result_b = run_pipeline(
            work_path,
            "work",
            barrier_path,
            "power",
            output_b,
            directory / "result_b.metadata.json",
            parameters,
        )

        metrics = result_a["metrics"]
        require(metrics["worked_segment_count"] == 3, "Self-test expected three worked segments.", BarrierValidationError)
        require(metrics["work_break_count"] == 1, "Self-test expected one power break.", BarrierValidationError)
        require(math.isclose(metrics["source_length_m"], 200.0, abs_tol=1e-6), "Self-test source length mismatch.", BarrierValidationError)
        require(math.isclose(metrics["worked_length_m"], 190.0, abs_tol=1e-6), "Self-test worked length mismatch.", BarrierValidationError)
        require(math.isclose(metrics["power_break_length_m"], 10.0, abs_tol=1e-6), "Self-test break length mismatch.", BarrierValidationError)
        require(metrics["connector_count"] == 0, "Self-test created a connector.", BarrierValidationError)

        breaks = gpd.read_file(output_a, layer="work_breaks")
        coordinates = list(breaks.geometry.iloc[0].coords)
        require(math.isclose(coordinates[0][0], 45.0, abs_tol=1e-6), "Self-test break start X mismatch.", BarrierValidationError)
        require(math.isclose(coordinates[-1][0], 55.0, abs_tol=1e-6), "Self-test break end X mismatch.", BarrierValidationError)
        require(math.isclose(coordinates[0][2], 104.5, abs_tol=1e-6), "Self-test interpolated start Z mismatch.", BarrierValidationError)
        require(math.isclose(coordinates[-1][2], 105.5, abs_tol=1e-6), "Self-test interpolated end Z mismatch.", BarrierValidationError)
        worked = gpd.read_file(output_a, layer="worked_segments")
        power_terminals = []
        for _, segment in worked.iterrows():
            for side in ("start", "end"):
                if segment[f"{side}_state"] == POWER_STATE:
                    power_terminals.append(segment)
                    require(
                        segment[f"{side}_termination_status"]
                        == "WORK_TERMINATION_REQUIRED_BY_POWER_BARRIER",
                        "Power terminal lacks required-break status.",
                        BarrierValidationError,
                    )
                    require(
                        segment[f"{side}_maneuver_status"] == "PROHIBITED_POWER_BARRIER",
                        "Power terminal incorrectly authorizes a maneuver.",
                        BarrierValidationError,
                    )
        require(len(power_terminals) == 2, "Self-test expected two power-terminal endpoints.", BarrierValidationError)

        snapshots_a = {
            layer: canonical_layer_snapshot(output_a, layer)
            for layer in ("worked_segments", "power_barriers", "work_breaks")
        }
        snapshots_b = {
            layer: canonical_layer_snapshot(output_b, layer)
            for layer in ("worked_segments", "power_barriers", "work_breaks")
        }
        require(result_a["run_id"] == result_b["run_id"], "Self-test run IDs are not deterministic.", BarrierValidationError)
        require(snapshots_a == snapshots_b, "Self-test feature output is not deterministic.", BarrierValidationError)

        # Explicitly exercise the registered minimum-fragment filter.
        short_work = directory / "short_work.gpkg"
        gpd.GeoDataFrame(
            {"work_id": ["SHORT"]},
            geometry=[LineString([(0.0, 0.0, 10.0), (100.0, 0.0, 20.0)])],
            crs="EPSG:31982",
        ).to_file(short_work, layer="work", driver="GPKG", index=False)
        short_barrier = directory / "short_barrier.gpkg"
        gpd.GeoDataFrame(
            {"power_id": ["PL-SHORT"]},
            geometry=[LineString([(10.0, -10.0), (10.0, 10.0)])],
            crs="EPSG:31982",
        ).to_file(short_barrier, layer="power", driver="GPKG", index=False)
        filtered = run_pipeline(
            short_work,
            "work",
            short_barrier,
            "power",
            directory / "filtered.gpkg",
            directory / "filtered.metadata.json",
            RunParameters(
                half_width_m=5.0,
                min_fragment_m=6.0,
                target_crs="EPSG:31982",
                id_field="work_id",
                barrier_id_field="power_id",
            ),
        )
        require(math.isclose(filtered["metrics"]["discarded_fragment_length_m"], 5.0, abs_tol=1e-6), "Self-test fragment filter mismatch.", BarrierValidationError)
        require(math.isclose(filtered["metrics"]["worked_length_m"], 85.0, abs_tol=1e-6), "Self-test filtered worked length mismatch.", BarrierValidationError)

        return {
            "status": "passed",
            "schema_version": SCHEMA_VERSION,
            "run_id": result_a["run_id"],
            "checks": {
                "difference_and_split": "PASS",
                "z_interpolation": "PASS",
                "length_balance": "PASS",
                "area_balance": "PASS",
                "outside_barrier": "PASS",
                "valid_geometry": "PASS",
                "minimum_fragment_filter": "PASS",
                "deterministic_ids_and_features": "PASS",
                "no_connectors": "PASS",
                "power_terminal_break_without_maneuver": "PASS",
            },
            "metrics": metrics,
        }


def default_output(work_path: Path) -> Path:
    return work_path.with_name(f"{work_path.stem}_power_barriers.gpkg")


def default_metadata_output(output: Path) -> Path:
    return output.with_suffix(".metadata.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Remove 3D work-line portions inside mandatory buffered power-line "
            "barriers. No crossing connector is generated."
        )
    )
    parser.add_argument("--work", type=Path, help="Input work-line datasource (normally GPKG).")
    parser.add_argument("--work-layer", help="Work LineString/LineStringZ layer; inferred only for a single-layer datasource.")
    parser.add_argument("--barriers", type=Path, help="Input power-line centerline vector datasource.")
    parser.add_argument("--barrier-layer", help="Barrier LineString layer; inferred only for a single-layer datasource.")
    parser.add_argument("--half-width-m", type=float, help="Mandatory positive half-width of the power barrier in metres.")
    parser.add_argument(
        "--min-fragment-m",
        type=float,
        default=0.0,
        help="Discard only outside fragments shorter than this recorded threshold (default: 0).",
    )
    parser.add_argument(
        "--target-crs",
        "--crs",
        dest="target_crs",
        help="Projected metric processing/output CRS, e.g. EPSG:31982; inferred when omitted.",
    )
    parser.add_argument("--work-crs", help="CRS only for a work datasource that has no CRS metadata.")
    parser.add_argument("--barrier-crs", help="CRS only for a barrier datasource that has no CRS metadata.")
    parser.add_argument("--id-field", help="Optional unique work feature identifier field.")
    parser.add_argument("--barrier-id-field", help="Optional unique barrier source identifier field.")
    parser.add_argument("--output", type=Path, help="Output GPKG; defaults beside --work.")
    parser.add_argument("--metadata-output", type=Path, help="Sidecar JSON; defaults to <output>.metadata.json.")
    parser.add_argument("--self-test", action="store_true", help="Run the synthetic deterministic self-test and exit.")
    return parser


def validate_cli_arguments(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.self_test:
        return
    missing = []
    for name in ("work", "barriers", "half_width_m"):
        if getattr(args, name) is None:
            missing.append(f"--{name.replace('_', '-')}")
    if missing:
        parser.error("the following arguments are required unless --self-test is used: " + ", ".join(missing))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    validate_cli_arguments(parser, args)
    if args.self_test:
        try:
            print(json.dumps(run_self_test(), indent=2, sort_keys=True))
            return EXIT_OK
        except Exception as exc:
            print(canonical_json({"status": "self_test_failed", "error": str(exc)}), file=sys.stderr)
            return EXIT_SELF_TEST

    output = args.output or default_output(args.work)
    metadata_output = args.metadata_output or default_metadata_output(output)
    parameters = RunParameters(
        half_width_m=args.half_width_m,
        min_fragment_m=args.min_fragment_m,
        target_crs=args.target_crs,
        work_crs=args.work_crs,
        barrier_crs=args.barrier_crs,
        id_field=args.id_field,
        barrier_id_field=args.barrier_id_field,
    )
    try:
        result = run_pipeline(
            args.work,
            args.work_layer,
            args.barriers,
            args.barrier_layer,
            output,
            metadata_output,
            parameters,
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return EXIT_OK
    except (BarrierInputError, BarrierValidationError) as exc:
        print(canonical_json({"status": "input_or_validation_error", "error": str(exc)}), file=sys.stderr)
        return EXIT_INPUT
    except Exception as exc:
        print(canonical_json({"status": "processing_error", "error": str(exc)}), file=sys.stderr)
        return EXIT_PROCESSING


if __name__ == "__main__":
    raise SystemExit(main())
