"""Static POA location and assignment optimizer.

The input JSON is a ``POA_STATIC_STAGE_MANIFEST`` prepared by an upstream
pipeline. It is not the central project-generation request and this CLI does
not translate that request automatically. An optional ``project_request_ref``
object carries its ``id`` and/or immutable ``hash`` for traceability.

Polygon POAs can produce an ``OPTIMIZED`` static assignment. Point candidates
are accepted for early siting analysis, but any selected Point downgrades the
result to ``GEOMETRIC_SCREENING`` because it does not prove a usable yard.

Run with the QGIS Python environment:

    & 'C:\\Program Files\\QGIS 3.32.1\\bin\\python-qgis.bat' `
      '.\\scripts\\optimize_static_poa.py' --request '.\\request.json'

The operational mode always uses a routed NetworkX graph. Euclidean distance is
available only through the explicitly named ``E0_EUCLIDEAN_SCREENING`` mode and
never produces an operational gain claim.

Exit codes:

* 0: completed (``OPTIMIZED`` or ``GEOMETRIC_SCREENING``)
* 2: invalid request contract
* 3: blocked execution or no feasible optimization result
* 4: unexpected runtime/read/write failure
* 5: deterministic self-test failure
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
import tempfile
import traceback
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import networkx as nx
from osgeo import gdal, ogr, osr
from shapely import wkb
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.ops import nearest_points, substring, unary_union
from shapely.strtree import STRtree


gdal.UseExceptions()
ogr.UseExceptions()

SCHEMA_VERSION = "1.0.0"
MANIFEST_TYPE = "POA_STATIC_STAGE_MANIFEST"
MODE_ROUTED = "ROUTED_STATIC"
MODE_E0 = "E0_EUCLIDEAN_SCREENING"

EXIT_OK = 0
EXIT_INVALID_REQUEST = 2
EXIT_BLOCKED = 3
EXIT_RUNTIME_ERROR = 4
EXIT_SELF_TEST_FAILED = 5

EPSILON = 1e-8
NODE_DIGITS = 6


class RequestValidationError(ValueError):
    """Raised when the JSON request does not satisfy the CLI contract."""

    def __init__(self, errors: Sequence[str]):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


class DataContractError(RuntimeError):
    """Raised when a declared spatial input cannot satisfy its contract."""


@dataclass
class VectorData:
    records: List[Dict[str, Any]]
    spatial_ref: Optional[osr.SpatialReference]
    projection_wkt: Optional[str]
    fields: List[str]


def blocker(
    code: str,
    stage: str,
    message: str,
    feature_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    record = {
        "code": code,
        "stage": stage,
        "message": message,
    }
    if feature_id is not None:
        record["feature_id"] = str(feature_id)
    if details:
        record["details"] = details
    return record


def rounded(value: Optional[float], digits: int = 3) -> Optional[float]:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(number, digits)


def is_positive_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
    )


def is_positive_integer(value: Any) -> bool:
    return is_positive_finite_number(value) and float(value).is_integer()


def percentile(values: Iterable[float], level: float) -> Optional[float]:
    ordered = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * level / 100.0
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return ordered[lower]
    fraction = rank - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def node_key(point: Point) -> Tuple[float, float]:
    return (round(float(point.x), NODE_DIGITS), round(float(point.y), NODE_DIGITS))


def point_from_key(key: Tuple[float, float]) -> Point:
    return Point(float(key[0]), float(key[1]))


def resolve_path(value: str, base_dir: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def validate_vector_spec(spec: Any, label: str, errors: List[str]) -> None:
    if not isinstance(spec, dict):
        errors.append(f"{label} must be an object")
        return
    if not isinstance(spec.get("path"), str) or not spec.get("path"):
        errors.append(f"{label}.path must be a non-empty string")
    if "layer" in spec and not isinstance(spec.get("layer"), str):
        errors.append(f"{label}.layer must be a string when provided")


def validate_request(request: Any) -> None:
    errors: List[str] = []
    if not isinstance(request, dict):
        raise RequestValidationError(["request root must be an object"])

    if request.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if request.get("manifest_type") != MANIFEST_TYPE:
        errors.append(f"manifest_type must be {MANIFEST_TYPE}")
    if request.get("mode") not in (MODE_ROUTED, MODE_E0):
        errors.append(f"mode must be {MODE_ROUTED} or {MODE_E0}")

    project_request_ref = request.get("project_request_ref")
    if project_request_ref is not None:
        if not isinstance(project_request_ref, dict):
            errors.append("project_request_ref must be an object when provided")
        else:
            for key in ("id", "hash"):
                value = project_request_ref.get(key)
                if value is not None and (not isinstance(value, str) or not value.strip()):
                    errors.append(f"project_request_ref.{key} must be a non-empty string")
            if not project_request_ref.get("id") and not project_request_ref.get("hash"):
                errors.append("project_request_ref requires id and/or hash")

    inputs = request.get("inputs")
    if not isinstance(inputs, dict):
        errors.append("inputs must be an object")
    else:
        validate_vector_spec(inputs.get("worked_lines"), "inputs.worked_lines", errors)
        validate_vector_spec(inputs.get("poa_candidates"), "inputs.poa_candidates", errors)
        if inputs.get("road_network") is not None:
            validate_vector_spec(inputs.get("road_network"), "inputs.road_network", errors)

    outputs = request.get("outputs")
    if not isinstance(outputs, dict):
        errors.append("outputs must be an object")
    else:
        for key in ("json", "gpkg"):
            if not isinstance(outputs.get(key), str) or not outputs.get(key):
                errors.append(f"outputs.{key} must be a non-empty string")

    constraints = request.get("constraints", {})
    if not isinstance(constraints, dict):
        errors.append("constraints must be an object")
    else:
        power = constraints.get("power_line_axis")
        declaration = constraints.get("power_absence_declaration")
        if power is not None:
            validate_vector_spec(power, "constraints.power_line_axis", errors)
            if not is_positive_finite_number(power.get("buffer_m")):
                errors.append("constraints.power_line_axis.buffer_m must be greater than zero")
        elif declaration is not None:
            if not isinstance(declaration, dict):
                errors.append("constraints.power_absence_declaration must be an object")
            else:
                required = ("declaration_id", "responsible", "date", "scope_ids")
                for field_name in required:
                    if not declaration.get(field_name):
                        errors.append(
                            f"constraints.power_absence_declaration.{field_name} is required"
                        )
                if declaration.get("confirmed_absent") is not True:
                    errors.append(
                        "constraints.power_absence_declaration.confirmed_absent must be true"
                    )
        else:
            errors.append(
                "provide constraints.power_line_axis or a valid power_absence_declaration"
            )

        exclusions = constraints.get("exclusions", [])
        if not isinstance(exclusions, list):
            errors.append("constraints.exclusions must be an array")
        else:
            for index, spec in enumerate(exclusions):
                validate_vector_spec(spec, f"constraints.exclusions[{index}]", errors)
                if isinstance(spec, dict) and "buffer_m" in spec:
                    value = spec.get("buffer_m")
                    if (
                        not isinstance(value, (int, float))
                        or isinstance(value, bool)
                        or not math.isfinite(float(value))
                        or value < 0
                    ):
                        errors.append(
                            f"constraints.exclusions[{index}].buffer_m must be non-negative"
                        )

    operation = request.get("operation", {})
    if operation is not None and not isinstance(operation, dict):
        errors.append("operation must be an object")

    if errors:
        raise RequestValidationError(errors)


def iter_lines(geometry: Any) -> Iterable[LineString]:
    if geometry is None or geometry.is_empty:
        return
    if isinstance(geometry, LineString):
        yield LineString([(float(x), float(y)) for x, y, *_ in geometry.coords])
    elif isinstance(geometry, MultiLineString):
        for item in geometry.geoms:
            yield from iter_lines(item)
    elif isinstance(geometry, GeometryCollection):
        for item in geometry.geoms:
            yield from iter_lines(item)


def iter_candidate_geometries(geometry: Any) -> Iterable[Any]:
    if geometry is None or geometry.is_empty:
        return
    if isinstance(geometry, (Point, Polygon)):
        yield geometry
    elif isinstance(geometry, (MultiPoint, MultiPolygon)):
        for item in geometry.geoms:
            yield item
    elif isinstance(geometry, GeometryCollection):
        for item in geometry.geoms:
            if isinstance(item, (Point, Polygon)):
                yield item


def read_vector(spec: Dict[str, Any], base_dir: Path) -> VectorData:
    path = resolve_path(spec["path"], base_dir)
    if not path.exists():
        raise DataContractError(f"Input does not exist: {path}")
    source = ogr.Open(str(path), 0)
    if source is None:
        raise DataContractError(f"Could not open vector input: {path}")
    layer_name = spec.get("layer")
    layer = source.GetLayerByName(layer_name) if layer_name else source.GetLayer(0)
    if layer is None:
        raise DataContractError(f"Layer {layer_name!r} not found in {path}")

    definition = layer.GetLayerDefn()
    fields = [definition.GetFieldDefn(index).GetName() for index in range(definition.GetFieldCount())]
    spatial_ref = layer.GetSpatialRef().Clone() if layer.GetSpatialRef() is not None else None
    projection_wkt = spatial_ref.ExportToWkt() if spatial_ref is not None else None
    records: List[Dict[str, Any]] = []
    for ordinal, feature in enumerate(layer):
        geometry_ref = feature.GetGeometryRef()
        if geometry_ref is None or geometry_ref.IsEmpty():
            continue
        geometry = wkb.loads(bytes(geometry_ref.ExportToWkb()))
        properties = {name: feature.GetField(name) for name in fields}
        records.append(
            {
                "fid": int(feature.GetFID()) if feature.GetFID() >= 0 else ordinal,
                "geometry": geometry,
                "properties": properties,
            }
        )
    source = None
    return VectorData(records, spatial_ref, projection_wkt, fields)


def field_value(
    record: Dict[str, Any],
    field_name: Optional[str],
    fallback: Any,
) -> Any:
    if field_name:
        value = record["properties"].get(field_name)
        if value is not None and value != "":
            return value
    return fallback


def same_crs(reference: Optional[osr.SpatialReference], other: Optional[osr.SpatialReference]) -> bool:
    if reference is None or other is None:
        return False
    return bool(reference.IsSame(other))


def validate_projected_metric(spatial_ref: Optional[osr.SpatialReference]) -> None:
    if spatial_ref is None:
        raise DataContractError("Spatial reference is missing")
    if not spatial_ref.IsProjected():
        raise DataContractError("A projected CRS is required for distance and mass calculations")
    units = float(spatial_ref.GetLinearUnits())
    if not math.isfinite(units) or abs(units - 1.0) > 1e-6:
        raise DataContractError("Projected CRS linear units must be metres")


def read_constraint_geometry(
    spec: Dict[str, Any],
    base_dir: Path,
    reference_srs: osr.SpatialReference,
    require_2d_lines: bool,
) -> Tuple[Any, List[Any]]:
    data = read_vector(spec, base_dir)
    if not same_crs(reference_srs, data.spatial_ref):
        raise DataContractError(f"Constraint CRS differs from the worked-line CRS: {spec['path']}")
    buffer_m = float(spec.get("buffer_m", 0.0))
    source_geometries: List[Any] = []
    buffered: List[Any] = []
    for record in data.records:
        geometry = record["geometry"]
        if require_2d_lines:
            parts = list(iter_lines(geometry))
            if not parts:
                raise DataContractError("power_line_axis accepts only LineString 2D features")
            if getattr(geometry, "has_z", False):
                raise DataContractError("power_line_axis must be 2D in V1")
            for part in parts:
                source_geometries.append(part)
                buffered.append(part.buffer(buffer_m))
        elif isinstance(geometry, (Polygon, MultiPolygon)):
            source_geometries.append(geometry)
            buffered.append(geometry.buffer(buffer_m) if buffer_m > 0 else geometry)
        else:
            parts = list(iter_lines(geometry))
            if not parts:
                raise DataContractError(
                    f"Constraint {spec['path']} must contain polygons or buffered lines"
                )
            if buffer_m <= 0:
                raise DataContractError(
                    f"Linear constraint {spec['path']} requires buffer_m greater than zero"
                )
            for part in parts:
                source_geometries.append(part)
                buffered.append(part.buffer(buffer_m))
    if require_2d_lines and not source_geometries:
        raise DataContractError("power_line_axis layer is empty")
    return (unary_union(buffered) if buffered else GeometryCollection()), source_geometries


def build_constraints(
    request: Dict[str, Any],
    base_dir: Path,
    reference_srs: osr.SpatialReference,
) -> Dict[str, Any]:
    constraints = request.get("constraints", {})
    power_spec = constraints.get("power_line_axis")
    power_barrier = GeometryCollection()
    power_axes: List[Any] = []
    if power_spec:
        power_barrier, power_axes = read_constraint_geometry(
            power_spec,
            base_dir,
            reference_srs,
            require_2d_lines=True,
        )

    exclusion_geometries: List[Any] = []
    exclusion_sources: List[Dict[str, Any]] = []
    if not power_barrier.is_empty:
        exclusion_geometries.append(power_barrier)
        exclusion_sources.append({"code": "POWER_LINE_BARRIER", "geometry": power_barrier})

    for index, spec in enumerate(constraints.get("exclusions", [])):
        geometry, source = read_constraint_geometry(
            spec,
            base_dir,
            reference_srs,
            require_2d_lines=False,
        )
        if not geometry.is_empty:
            exclusion_geometries.append(geometry)
            exclusion_sources.append(
                {
                    "code": str(spec.get("code", f"EXCLUSION_{index + 1}")),
                    "geometry": geometry,
                    "source_count": len(source),
                }
            )

    combined = unary_union(exclusion_geometries) if exclusion_geometries else GeometryCollection()
    return {
        "combined": combined,
        "power_barrier": power_barrier,
        "power_axes": power_axes,
        "sources": exclusion_sources,
        "power_status": "AXIS_BUFFERED" if power_spec else "DECLARED_ABSENT",
    }


def clip_worked_lines(
    data: VectorData,
    spec: Dict[str, Any],
    exclusion: Any,
    productivity: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    id_field = spec.get("id_field")
    shot_field = spec.get("shot_id_field")
    sequence_field = spec.get("sequence_field")
    yield_field = productivity.get("attribute") if productivity else None
    for field_name, label in (
        (id_field, "id_field"),
        (shot_field, "shot_id_field"),
        (sequence_field, "sequence_field"),
        (yield_field, "productivity.attribute"),
    ):
        if field_name and field_name not in data.fields:
            raise DataContractError(f"Worked-line {label} does not exist: {field_name}")

    segments: List[Dict[str, Any]] = []
    for ordinal, record in enumerate(data.records):
        source_id = str(field_value(record, id_field, f"SEG_{ordinal + 1:06d}"))
        shot_id = str(field_value(record, shot_field, source_id))
        raw_sequence = field_value(record, sequence_field, ordinal)
        try:
            source_sequence = float(raw_sequence)
        except (TypeError, ValueError):
            source_sequence = float(ordinal)
        source_yield = field_value(record, yield_field, None)
        for source_part_index, source_line in enumerate(iter_lines(record["geometry"])):
            allowed = source_line.difference(exclusion) if not exclusion.is_empty else source_line
            parts = list(iter_lines(allowed))
            parts.sort(key=lambda part: source_line.project(Point(part.coords[0])))
            for part_index, part in enumerate(parts):
                if part.length <= EPSILON:
                    continue
                suffix_needed = len(parts) > 1 or source_part_index > 0
                segment_id = (
                    f"{source_id}:P{source_part_index + 1}_{part_index + 1}"
                    if suffix_needed
                    else source_id
                )
                segments.append(
                    {
                        "segment_id": segment_id,
                        "source_segment_id": source_id,
                        "source_operational_run_id": shot_id,
                        "shot_id": shot_id,
                        "sequence": source_sequence
                        + source_part_index / 1000.0
                        + part_index / 1000000.0,
                        "geometry": part,
                        "source_yield_t_ha": source_yield,
                        "length_m": float(part.length),
                    }
                )
    return segments


def split_disconnected_operational_runs(
    segments: List[Dict[str, Any]],
) -> Dict[str, int]:
    """Prevent mass accumulation across disconnected pieces of one run.

    Connectivity is exact after coordinate rounding used by the routing graph;
    no undeclared spatial tolerance is introduced. A power-buffer cut therefore
    creates distinct operational-run components and cannot inherit a partial
    transshipment load across the barrier.
    """

    grouped: Dict[str, List[int]] = defaultdict(list)
    for index, segment in enumerate(segments):
        grouped[segment["source_operational_run_id"]].append(index)

    split_source_runs = 0
    operational_components = 0
    for source_run_id in sorted(grouped):
        indexes = grouped[source_run_id]
        component_graph = nx.Graph()
        component_graph.add_nodes_from(indexes)
        endpoint_members: Dict[Tuple[float, float], List[int]] = defaultdict(list)
        for index in indexes:
            line = segments[index]["geometry"]
            endpoint_members[node_key(Point(line.coords[0]))].append(index)
            endpoint_members[node_key(Point(line.coords[-1]))].append(index)
        for members in endpoint_members.values():
            if len(members) > 1:
                anchor = members[0]
                component_graph.add_edges_from((anchor, member) for member in members[1:])

        components = list(nx.connected_components(component_graph))
        components.sort(
            key=lambda component: min(
                (segments[index]["sequence"], segments[index]["segment_id"])
                for index in component
            )
        )
        if len(components) > 1:
            split_source_runs += 1
        for component_index, component in enumerate(components, start=1):
            operational_run_id = (
                source_run_id
                if len(components) == 1
                else f"{source_run_id}:C{component_index:03d}"
            )
            for index in component:
                segments[index]["shot_id"] = operational_run_id
                segments[index]["operational_run_id"] = operational_run_id
            operational_components += 1

    return {
        "source_operational_run_count": len(grouped),
        "operational_run_count_after_constraints": operational_components,
        "source_runs_split_by_disconnection": split_source_runs,
    }


def read_candidates(
    data: VectorData,
    spec: Dict[str, Any],
    operation: Dict[str, Any],
    exclusion: Any,
    power_barrier: Any,
    capacity_required: bool,
) -> List[Dict[str, Any]]:
    id_field = spec.get("id_field")
    capacity_field = spec.get("capacity_t_field")
    penalty_field = spec.get("fixed_penalty_tonne_m_field")
    for field_name, label in (
        (id_field, "id_field"),
        (capacity_field, "capacity_t_field"),
        (penalty_field, "fixed_penalty_tonne_m_field"),
    ):
        if field_name and field_name not in data.fields:
            raise DataContractError(f"POA candidate {label} does not exist: {field_name}")

    default_capacity = operation.get("poa_default_capacity_t")
    default_penalty = float(operation.get("poa_default_fixed_penalty_tonne_m", 0.0))
    candidates: List[Dict[str, Any]] = []
    for ordinal, record in enumerate(data.records):
        base_id = str(field_value(record, id_field, f"POA_{ordinal + 1:05d}"))
        geometries = list(iter_candidate_geometries(record["geometry"]))
        if not geometries:
            raise DataContractError(f"POA candidate {base_id} must be Polygon or Point")
        for part_index, geometry in enumerate(geometries):
            poa_id = base_id if len(geometries) == 1 else f"{base_id}:P{part_index + 1}"
            raw_capacity = field_value(record, capacity_field, default_capacity)
            capacity = None
            if raw_capacity is not None:
                try:
                    converted_capacity = float(raw_capacity)
                    if not isinstance(raw_capacity, bool) and math.isfinite(converted_capacity):
                        capacity = converted_capacity
                except (TypeError, ValueError):
                    capacity = None
            raw_penalty = field_value(record, penalty_field, default_penalty)
            try:
                penalty = float(raw_penalty)
            except (TypeError, ValueError):
                penalty = default_penalty
            if not math.isfinite(penalty):
                penalty = -1.0
            reasons: List[str] = []
            if not power_barrier.is_empty and geometry.intersects(power_barrier):
                reasons.append("INTERSECTS_POWER_LINE_BARRIER")
            elif not exclusion.is_empty and geometry.intersects(exclusion):
                reasons.append("INTERSECTS_HARD_EXCLUSION")
            if capacity_required and capacity is None:
                reasons.append("MISSING_CAPACITY_FOR_PLANNING_HORIZON")
            elif capacity is not None and capacity <= 0:
                reasons.append("NON_POSITIVE_CAPACITY")
            if penalty < 0:
                reasons.append("NEGATIVE_FIXED_PENALTY")
            candidates.append(
                {
                    "poa_id": poa_id,
                    "geometry": geometry,
                    "geometry_type": geometry.geom_type,
                    "capacity_t": capacity,
                    "fixed_penalty_tonne_m": penalty,
                    "eligible": not reasons,
                    "ineligibility_reasons": reasons,
                    "graph_node": None,
                    "access_connector_m": None,
                }
            )
    return candidates


def assign_productivity(
    segments: List[Dict[str, Any]],
    productivity: Dict[str, Any],
    harvest_width_m: float,
) -> None:
    mode = productivity.get("mode")
    if mode == "UNIFORM":
        uniform = float(productivity["tonnes_per_hectare"])
        if uniform <= 0:
            raise DataContractError("Uniform productivity must be greater than zero")
        for segment in segments:
            segment["yield_t_ha"] = uniform
    elif mode == "ATTRIBUTE":
        for segment in segments:
            try:
                value = float(segment["source_yield_t_ha"])
            except (TypeError, ValueError):
                raise DataContractError(
                    f"Missing or invalid productivity for {segment['segment_id']}"
                )
            if value <= 0:
                raise DataContractError(
                    f"Productivity must be positive for {segment['segment_id']}"
                )
            segment["yield_t_ha"] = value
    elif mode == "RASTER":
        raise DataContractError(
            "RASTER productivity is declared but not implemented in V1; use UNIFORM or ATTRIBUTE"
        )
    else:
        raise DataContractError("productivity.mode must be UNIFORM, ATTRIBUTE or RASTER")

    for segment in segments:
        segment["mass_t"] = (
            segment["yield_t_ha"] * segment["length_m"] * harvest_width_m / 10000.0
        )


def generate_load_events(
    segments: List[Dict[str, Any]],
    transshipment_capacity_t: float,
) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for segment in segments:
        grouped[segment["shot_id"]].append(segment)

    events: List[Dict[str, Any]] = []
    event_index = 0
    for shot_id in sorted(grouped):
        ordered = sorted(grouped[shot_id], key=lambda item: (item["sequence"], item["segment_id"]))
        accumulated = 0.0
        last_position: Optional[Tuple[Point, Dict[str, Any], float]] = None
        for segment in ordered:
            line = segment["geometry"]
            mass_per_m = segment["mass_t"] / max(segment["length_m"], EPSILON)
            cursor_m = 0.0
            remaining_segment_mass = segment["mass_t"]
            while accumulated + remaining_segment_mass >= transshipment_capacity_t - EPSILON:
                needed = max(0.0, transshipment_capacity_t - accumulated)
                advance = needed / max(mass_per_m, EPSILON)
                event_station = min(segment["length_m"], cursor_m + advance)
                point = line.interpolate(event_station)
                event_index += 1
                events.append(
                    {
                        "event_id": f"LOAD_{event_index:07d}",
                        "shot_id": shot_id,
                        "segment_id": segment["segment_id"],
                        "station_m": float(event_station),
                        "geometry": Point(float(point.x), float(point.y)),
                        "mass_t": float(transshipment_capacity_t),
                        "load_type": "FULL",
                        "graph_node": None,
                    }
                )
                consumed_mass = max(needed, 0.0)
                remaining_segment_mass -= consumed_mass
                cursor_m = event_station
                accumulated = 0.0
                if remaining_segment_mass <= EPSILON:
                    remaining_segment_mass = 0.0
                    break
            accumulated += remaining_segment_mass
            last_position = (Point(line.coords[-1]), segment, segment["length_m"])

        if accumulated > EPSILON and last_position is not None:
            point, segment, station_m = last_position
            event_index += 1
            events.append(
                {
                    "event_id": f"LOAD_{event_index:07d}",
                    "shot_id": shot_id,
                    "segment_id": segment["segment_id"],
                    "station_m": float(station_m),
                    "geometry": Point(float(point.x), float(point.y)),
                    "mass_t": float(accumulated),
                    "load_type": "PARTIAL_SHOT_END",
                    "graph_node": None,
                }
            )
    return events


def read_and_filter_roads(
    data: VectorData,
    exclusion: Any,
    power_barrier: Any,
) -> Tuple[List[LineString], Dict[str, float]]:
    original_length = 0.0
    power_barrier_removed_length = 0.0
    power_barrier_intersection_count = 0
    allowed_lines: List[LineString] = []
    for record in data.records:
        for line in iter_lines(record["geometry"]):
            original_length += float(line.length)
            if not power_barrier.is_empty and line.intersects(power_barrier):
                power_barrier_intersection_count += 1
                power_barrier_removed_length += float(
                    line.intersection(power_barrier).length
                )
            allowed = line.difference(exclusion) if not exclusion.is_empty else line
            for part in iter_lines(allowed):
                if part.length > EPSILON:
                    allowed_lines.append(part)
    allowed_length = sum(line.length for line in allowed_lines)
    return allowed_lines, {
        "original_road_length_m": float(original_length),
        "allowed_road_length_m": float(allowed_length),
        "removed_road_length_m": max(0.0, float(original_length - allowed_length)),
        "power_barrier_removed_road_length_m": float(power_barrier_removed_length),
        "road_parts_intersecting_power_barrier": power_barrier_intersection_count,
    }


def make_road_segments(lines: List[LineString]) -> List[LineString]:
    if not lines:
        return []
    noded = unary_union(lines)
    segments: List[LineString] = []
    for line in iter_lines(noded):
        coordinates = list(line.coords)
        for start, end in zip(coordinates[:-1], coordinates[1:]):
            segment = LineString(
                [(float(start[0]), float(start[1])), (float(end[0]), float(end[1]))]
            )
            if segment.length > EPSILON:
                segments.append(segment)
    return segments


def add_edge_minimum(
    graph: nx.Graph,
    start: Tuple[float, float],
    end: Tuple[float, float],
    geometry: LineString,
    kind: str,
) -> None:
    length = float(geometry.length)
    graph.add_node(start, x=float(start[0]), y=float(start[1]))
    graph.add_node(end, x=float(end[0]), y=float(end[1]))
    if start == end or length <= EPSILON:
        return
    if graph.has_edge(start, end) and graph.edges[start, end]["weight"] <= length + EPSILON:
        return
    graph.add_edge(start, end, weight=length, geometry=geometry, kind=kind)


def line_between_points(start: Point, end: Point) -> LineString:
    return LineString([(float(start.x), float(start.y)), (float(end.x), float(end.y))])


def connector_clear(connector: LineString, exclusion: Any) -> bool:
    if exclusion.is_empty or connector.length <= EPSILON:
        return True
    return not connector.intersects(exclusion)


def build_routed_graph(
    road_segments: List[LineString],
    worked_segments: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    exclusion: Any,
    snap_tolerance_m: float,
) -> Tuple[nx.Graph, Dict[str, Any], List[Dict[str, Any]]]:
    graph = nx.Graph()
    warnings: List[Dict[str, Any]] = []
    if not road_segments:
        return graph, {"road_snap_count": 0, "access_connector_length_m": 0.0}, warnings

    road_union = unary_union(road_segments)
    tree = STRtree(road_segments)
    split_distances: Dict[int, List[float]] = defaultdict(list)
    terminals: List[Dict[str, Any]] = []

    def register_terminal(
        terminal_id: str,
        category: str,
        source_geometry: Any,
        target: Dict[str, Any],
    ) -> None:
        if isinstance(source_geometry, Polygon):
            origin, snapped = nearest_points(source_geometry, road_union)
        else:
            origin = Point(float(source_geometry.x), float(source_geometry.y))
            _, snapped = nearest_points(origin, road_union)
        nearest_index = int(tree.nearest(snapped))
        road_segment = road_segments[nearest_index]
        station = float(road_segment.project(snapped))
        snapped = road_segment.interpolate(station)
        connector = line_between_points(origin, snapped)
        reasons: List[str] = []
        if connector.length > snap_tolerance_m + EPSILON:
            reasons.append("ACCESS_CONNECTOR_EXCEEDS_TOLERANCE")
        if not connector_clear(connector, exclusion):
            reasons.append("ACCESS_CONNECTOR_INTERSECTS_EXCLUSION")
        terminals.append(
            {
                "terminal_id": terminal_id,
                "category": category,
                "origin": origin,
                "snapped": snapped,
                "connector": connector,
                "road_segment_index": nearest_index,
                "station": station,
                "target": target,
                "reasons": reasons,
            }
        )
        if not reasons:
            split_distances[nearest_index].append(station)

    for segment in worked_segments:
        line = segment["geometry"]
        register_terminal(
            f"{segment['segment_id']}:START",
            "WORKED_ENDPOINT",
            Point(line.coords[0]),
            {"segment": segment, "endpoint": "START"},
        )
        register_terminal(
            f"{segment['segment_id']}:END",
            "WORKED_ENDPOINT",
            Point(line.coords[-1]),
            {"segment": segment, "endpoint": "END"},
        )

    for candidate in candidates:
        if candidate["eligible"]:
            register_terminal(
                candidate["poa_id"],
                "POA",
                candidate["geometry"],
                {"candidate": candidate},
            )

    for index, road_segment in enumerate(road_segments):
        stations = [0.0, float(road_segment.length)] + split_distances.get(index, [])
        stations = sorted(set(round(value, 9) for value in stations))
        for start_distance, end_distance in zip(stations[:-1], stations[1:]):
            if end_distance - start_distance <= EPSILON:
                continue
            piece = substring(road_segment, start_distance, end_distance)
            if not isinstance(piece, LineString) or piece.length <= EPSILON:
                continue
            start_key = node_key(Point(piece.coords[0]))
            end_key = node_key(Point(piece.coords[-1]))
            add_edge_minimum(graph, start_key, end_key, piece, "ROAD")

    connector_length = 0.0
    for terminal in terminals:
        target = terminal["target"]
        if terminal["reasons"]:
            if terminal["category"] == "POA":
                candidate = target["candidate"]
                candidate["eligible"] = False
                candidate["ineligibility_reasons"].extend(terminal["reasons"])
            else:
                warnings.append(
                    blocker(
                        "WORKED_ENDPOINT_NOT_CONNECTED",
                        "WARNING",
                        "Worked-line endpoint could not connect to the routed road graph",
                        terminal["terminal_id"],
                        {"reasons": terminal["reasons"]},
                    )
                )
            continue
        origin_key = node_key(terminal["origin"])
        snapped_key = node_key(terminal["snapped"])
        add_edge_minimum(
            graph,
            origin_key,
            snapped_key,
            terminal["connector"],
            "ACCESS_CONNECTOR",
        )
        connector_length += terminal["connector"].length
        if terminal["category"] == "POA":
            candidate = target["candidate"]
            candidate["graph_node"] = origin_key
            candidate["access_connector_m"] = float(terminal["connector"].length)

    events_by_segment: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        events_by_segment[event["segment_id"]].append(event)

    for segment in worked_segments:
        line = segment["geometry"]
        line_events = events_by_segment.get(segment["segment_id"], [])
        stations = [0.0, float(line.length)]
        for event in line_events:
            station = min(float(line.length), max(0.0, float(event["station_m"])))
            stations.append(station)
            point = line.interpolate(station)
            event["geometry"] = Point(float(point.x), float(point.y))
            event["graph_node"] = node_key(event["geometry"])
        stations = sorted(set(round(value, 9) for value in stations))
        for start_distance, end_distance in zip(stations[:-1], stations[1:]):
            if end_distance - start_distance <= EPSILON:
                continue
            piece = substring(line, start_distance, end_distance)
            if not isinstance(piece, LineString) or piece.length <= EPSILON:
                continue
            add_edge_minimum(
                graph,
                node_key(Point(piece.coords[0])),
                node_key(Point(piece.coords[-1])),
                piece,
                "WORKED_LINE_ACCESS",
            )

    return graph, {
        "road_snap_count": len(terminals),
        "access_connector_length_m": float(connector_length),
        "graph_nodes": graph.number_of_nodes(),
        "graph_edges": graph.number_of_edges(),
    }, warnings


def oriented_edge_coordinates(
    graph: nx.Graph,
    start: Tuple[float, float],
    end: Tuple[float, float],
) -> List[Tuple[float, float]]:
    geometry = graph.edges[start, end]["geometry"]
    coordinates = [(float(x), float(y)) for x, y, *_ in geometry.coords]
    first = Point(coordinates[0])
    if first.distance(point_from_key(start)) <= first.distance(point_from_key(end)):
        return coordinates
    return list(reversed(coordinates))


def path_geometry(graph: nx.Graph, path: List[Tuple[float, float]]) -> LineString:
    if len(path) == 1:
        point = point_from_key(path[0])
        return LineString([(point.x, point.y), (point.x, point.y)])
    coordinates: List[Tuple[float, float]] = []
    for start, end in zip(path[:-1], path[1:]):
        part = oriented_edge_coordinates(graph, start, end)
        if coordinates and coordinates[-1] == part[0]:
            coordinates.extend(part[1:])
        else:
            coordinates.extend(part)
    return LineString(coordinates)


def compute_route_matrix(
    graph: nx.Graph,
    events: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Dict[str, Any]]], List[Dict[str, Any]]]:
    eligible = [candidate for candidate in candidates if candidate["eligible"] and candidate["graph_node"]]
    matrix: Dict[str, Dict[str, Dict[str, Any]]] = {}
    result_blockers: List[Dict[str, Any]] = []
    for event in events:
        event_id = event["event_id"]
        matrix[event_id] = {}
        source = event.get("graph_node")
        if source is None or source not in graph:
            result_blockers.append(
                blocker(
                    "LOAD_EVENT_DISCONNECTED",
                    "RESULT",
                    "Load event is not connected to the routed graph",
                    event_id,
                )
            )
            continue
        try:
            lengths, paths = nx.single_source_dijkstra(graph, source, weight="weight")
        except nx.NetworkXError:
            lengths, paths = {}, {}
        for candidate in eligible:
            target = candidate["graph_node"]
            if target in lengths:
                matrix[event_id][candidate["poa_id"]] = {
                    "distance_m": float(lengths[target]),
                    "path": paths[target],
                }
        if not matrix[event_id]:
            result_blockers.append(
                blocker(
                    "LOAD_EVENT_WITHOUT_ROUTED_POA",
                    "RESULT",
                    "No eligible POA is reachable through the graph",
                    event_id,
                )
            )
    return matrix, result_blockers


def exact_capacitated_assignment(
    events: List[Dict[str, Any]],
    subset: Sequence[str],
    capacities: Dict[str, float],
    route_matrix: Dict[str, Dict[str, Dict[str, Any]]],
) -> Optional[Tuple[float, Dict[str, str]]]:
    options: Dict[str, List[Tuple[str, float]]] = {}
    for event in events:
        available = []
        for poa_id in subset:
            route = route_matrix.get(event["event_id"], {}).get(poa_id)
            if route is not None and capacities[poa_id] + EPSILON >= event["mass_t"]:
                available.append((poa_id, route["distance_m"] * event["mass_t"]))
        if not available:
            return None
        options[event["event_id"]] = sorted(available, key=lambda item: (item[1], item[0]))

    def regret(event: Dict[str, Any]) -> float:
        costs = [cost for _, cost in options[event["event_id"]]]
        if len(costs) == 1:
            return float("inf")
        return costs[1] - costs[0]

    ordered = sorted(
        events,
        key=lambda event: (
            len(options[event["event_id"]]),
            -float(event["mass_t"]),
            -regret(event),
            event["event_id"],
        ),
    )
    suffix_lower_bound = [0.0] * (len(ordered) + 1)
    for index in range(len(ordered) - 1, -1, -1):
        suffix_lower_bound[index] = (
            suffix_lower_bound[index + 1] + options[ordered[index]["event_id"]][0][1]
        )

    remaining = dict(capacities)
    best_cost = float("inf")
    best_assignment: Optional[Dict[str, str]] = None
    current: Dict[str, str] = {}

    def visit(index: int, cost: float) -> None:
        nonlocal best_cost, best_assignment
        if cost + suffix_lower_bound[index] >= best_cost - EPSILON:
            return
        if index == len(ordered):
            best_cost = cost
            best_assignment = dict(current)
            return
        event = ordered[index]
        event_id = event["event_id"]
        mass = float(event["mass_t"])
        for poa_id, assignment_cost in options[event_id]:
            if remaining[poa_id] + EPSILON < mass:
                continue
            remaining[poa_id] -= mass
            current[event_id] = poa_id
            visit(index + 1, cost + assignment_cost)
            current.pop(event_id, None)
            remaining[poa_id] += mass

    visit(0, 0.0)
    if best_assignment is None:
        return None
    return best_cost, best_assignment


def greedy_capacitated_assignment(
    events: List[Dict[str, Any]],
    subset: Sequence[str],
    capacities: Dict[str, float],
    route_matrix: Dict[str, Dict[str, Dict[str, Any]]],
) -> Optional[Tuple[float, Dict[str, str]]]:
    remaining = dict(capacities)
    unassigned = {event["event_id"]: event for event in events}
    assignment: Dict[str, str] = {}
    total_cost = 0.0
    while unassigned:
        choices: List[Tuple[float, float, str, List[Tuple[float, str]]]] = []
        for event_id, event in unassigned.items():
            feasible: List[Tuple[float, str]] = []
            for poa_id in subset:
                route = route_matrix.get(event_id, {}).get(poa_id)
                if route is None or remaining[poa_id] + EPSILON < event["mass_t"]:
                    continue
                feasible.append((route["distance_m"] * event["mass_t"], poa_id))
            feasible.sort(key=lambda item: (item[0], item[1]))
            if not feasible:
                return None
            regret = float("inf") if len(feasible) == 1 else feasible[1][0] - feasible[0][0]
            choices.append((-regret, -float(event["mass_t"]), event_id, feasible))
        choices.sort(key=lambda item: (item[0], item[1], item[2]))
        _, _, event_id, feasible = choices[0]
        cost, poa_id = feasible[0]
        event = unassigned.pop(event_id)
        remaining[poa_id] -= event["mass_t"]
        assignment[event_id] = poa_id
        total_cost += cost
    return total_cost, assignment


def evaluate_subset(
    events: List[Dict[str, Any]],
    subset: Sequence[str],
    candidate_lookup: Dict[str, Dict[str, Any]],
    route_matrix: Dict[str, Dict[str, Dict[str, Any]]],
    exact_assignment_limit: int,
) -> Optional[Dict[str, Any]]:
    capacities = {
        poa_id: float(candidate_lookup[poa_id]["capacity_t"])
        for poa_id in subset
    }
    total_mass = sum(float(event["mass_t"]) for event in events)
    if sum(capacities.values()) + EPSILON < total_mass:
        return None
    if any(not any(poa_id in route_matrix.get(event["event_id"], {}) for poa_id in subset) for event in events):
        return None
    if len(events) <= exact_assignment_limit:
        solved = exact_capacitated_assignment(events, subset, capacities, route_matrix)
        assignment_method = "EXACT_BRANCH_AND_BOUND"
    else:
        solved = greedy_capacitated_assignment(events, subset, capacities, route_matrix)
        assignment_method = "CAPACITATED_GREEDY_REGRET"
    if solved is None:
        return None
    routing_cost, assignment = solved
    site_penalty = sum(
        float(candidate_lookup[poa_id]["fixed_penalty_tonne_m"]) for poa_id in subset
    )
    return {
        "objective_tonne_m": float(routing_cost + site_penalty),
        "routing_tonne_m": float(routing_cost),
        "site_penalty_tonne_m": float(site_penalty),
        "selected_poa_ids": sorted(subset),
        "assignment": assignment,
        "assignment_method": assignment_method,
    }


def exact_combination_count(candidate_count: int, max_sites: int) -> int:
    return sum(math.comb(candidate_count, size) for size in range(1, min(max_sites, candidate_count) + 1))


def subset_coverage_score(
    events: List[Dict[str, Any]],
    subset: Sequence[str],
    candidate_lookup: Dict[str, Dict[str, Any]],
    route_matrix: Dict[str, Dict[str, Dict[str, Any]]],
) -> float:
    finite_distances = [
        route["distance_m"]
        for per_event in route_matrix.values()
        for route in per_event.values()
        if math.isfinite(route["distance_m"])
    ]
    penalty_distance = (max(finite_distances) if finite_distances else 1.0) * 20.0 + 1.0
    score = sum(
        float(candidate_lookup[poa_id]["fixed_penalty_tonne_m"]) for poa_id in subset
    )
    unreachable_mass = 0.0
    for event in events:
        distances = [
            route_matrix[event["event_id"]][poa_id]["distance_m"]
            for poa_id in subset
            if poa_id in route_matrix.get(event["event_id"], {})
        ]
        if distances:
            score += min(distances) * event["mass_t"]
        else:
            unreachable_mass += event["mass_t"]
    total_mass = sum(event["mass_t"] for event in events)
    total_capacity = sum(float(candidate_lookup[poa_id]["capacity_t"]) for poa_id in subset)
    capacity_shortfall = max(0.0, total_mass - total_capacity)
    score += penalty_distance * (unreachable_mass + capacity_shortfall)
    return float(score)


def optimize_sites(
    events: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    route_matrix: Dict[str, Dict[str, Dict[str, Any]]],
    operation: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    eligible = [
        candidate
        for candidate in candidates
        if candidate["eligible"]
        and candidate["graph_node"] is not None
        and candidate["capacity_t"] is not None
    ]
    candidate_lookup = {candidate["poa_id"]: candidate for candidate in eligible}
    ids = sorted(candidate_lookup)
    max_sites = min(int(operation.get("max_selected_sites", 1)), len(ids))
    exact_site_limit = int(operation.get("exact_site_candidate_limit", 14))
    exact_assignment_limit = int(operation.get("exact_assignment_event_limit", 16))
    max_combinations = int(operation.get("max_exact_site_combinations", 50000))
    combination_count = exact_combination_count(len(ids), max_sites) if ids else 0

    best: Optional[Dict[str, Any]] = None

    def consider(solution: Optional[Dict[str, Any]]) -> None:
        nonlocal best
        if solution is None:
            return
        key = (
            solution["objective_tonne_m"],
            len(solution["selected_poa_ids"]),
            tuple(solution["selected_poa_ids"]),
        )
        if best is None:
            best = solution
        else:
            best_key = (
                best["objective_tonne_m"],
                len(best["selected_poa_ids"]),
                tuple(best["selected_poa_ids"]),
            )
            if key < best_key:
                best = solution

    if len(ids) <= exact_site_limit and combination_count <= max_combinations:
        site_method = "EXACT_SUBSET_ENUMERATION"
        for size in range(1, max_sites + 1):
            for subset in itertools.combinations(ids, size):
                consider(
                    evaluate_subset(
                        events,
                        subset,
                        candidate_lookup,
                        route_matrix,
                        exact_assignment_limit,
                    )
                )
    else:
        site_method = "GREEDY_ADD_ONE_SWAP_HEURISTIC"
        selected: List[str] = []
        remaining = set(ids)
        for _ in range(max_sites):
            if not remaining:
                break
            next_id = min(
                remaining,
                key=lambda poa_id: (
                    subset_coverage_score(
                        events,
                        selected + [poa_id],
                        candidate_lookup,
                        route_matrix,
                    ),
                    poa_id,
                ),
            )
            selected.append(next_id)
            remaining.remove(next_id)
            consider(
                evaluate_subset(
                    events,
                    selected,
                    candidate_lookup,
                    route_matrix,
                    exact_assignment_limit,
                )
            )

        if selected:
            for old_id in list(selected):
                for new_id in sorted(set(ids) - set(selected)):
                    trial = sorted((set(selected) - {old_id}) | {new_id})
                    consider(
                        evaluate_subset(
                            events,
                            trial,
                            candidate_lookup,
                            route_matrix,
                            exact_assignment_limit,
                        )
                    )

    metadata = {
        "site_selection_method": site_method,
        "assignment_method": best["assignment_method"] if best else None,
        "eligible_candidate_count": len(ids),
        "max_selected_sites": max_sites,
        "site_subset_count_if_exact": combination_count,
        "exact_site_candidate_limit": exact_site_limit,
        "exact_assignment_event_limit": exact_assignment_limit,
        "optimality": (
            "GLOBAL_FOR_ENUMERATED_SITES_AND_EXACT_ASSIGNMENT"
            if best
            and site_method == "EXACT_SUBSET_ENUMERATION"
            and best["assignment_method"] == "EXACT_BRANCH_AND_BOUND"
            else "HEURISTIC_NO_GLOBAL_OPTIMALITY_CLAIM"
        ),
    }
    return best, metadata


def assignments_with_routes(
    solution: Dict[str, Any],
    events: List[Dict[str, Any]],
    route_matrix: Dict[str, Dict[str, Dict[str, Any]]],
    graph: nx.Graph,
) -> List[Dict[str, Any]]:
    event_lookup = {event["event_id"]: event for event in events}
    assignments: List[Dict[str, Any]] = []
    for event_id in sorted(solution["assignment"]):
        poa_id = solution["assignment"][event_id]
        event = event_lookup[event_id]
        route = route_matrix[event_id][poa_id]
        assignments.append(
            {
                "event_id": event_id,
                "shot_id": event["shot_id"],
                "segment_id": event["segment_id"],
                "poa_id": poa_id,
                "mass_t": float(event["mass_t"]),
                "distance_m": float(route["distance_m"]),
                "tonne_m": float(route["distance_m"] * event["mass_t"]),
                "route_kind": "NETWORKX_ROUTED",
                "geometry": path_geometry(graph, route["path"]),
            }
        )
    return assignments


def routed_metrics(
    segments: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    assignments: List[Dict[str, Any]],
    solution: Dict[str, Any],
) -> Dict[str, Any]:
    distances = [assignment["distance_m"] for assignment in assignments]
    total_mass = sum(event["mass_t"] for event in events)
    assigned_by_poa: Dict[str, float] = defaultdict(float)
    for assignment in assignments:
        assigned_by_poa[assignment["poa_id"]] += assignment["mass_t"]
    selected_lookup = {candidate["poa_id"]: candidate for candidate in candidates}
    utilization = {
        poa_id: rounded(assigned_by_poa[poa_id] / selected_lookup[poa_id]["capacity_t"])
        for poa_id in solution["selected_poa_ids"]
    }
    return {
        "worked_length_m": rounded(sum(segment["length_m"] for segment in segments)),
        "harvested_mass_t": rounded(sum(segment["mass_t"] for segment in segments)),
        "load_event_mass_t": rounded(total_mass),
        "load_event_count": len(events),
        "full_load_event_count": sum(event["load_type"] == "FULL" for event in events),
        "partial_load_event_count": sum(event["load_type"] != "FULL" for event in events),
        "selected_poa_count": len(solution["selected_poa_ids"]),
        "assigned_mass_t": rounded(sum(assignment["mass_t"] for assignment in assignments)),
        "route_distance_sum_m": rounded(sum(distances)),
        "route_distance_p50_m": rounded(percentile(distances, 50)),
        "route_distance_p95_m": rounded(percentile(distances, 95)),
        "route_distance_max_m": rounded(max(distances) if distances else None),
        "mass_weighted_mean_route_m": rounded(
            sum(assignment["tonne_m"] for assignment in assignments) / max(total_mass, EPSILON)
        ),
        "routing_objective_tonne_m": rounded(solution["routing_tonne_m"]),
        "site_penalty_tonne_m": rounded(solution["site_penalty_tonne_m"]),
        "objective_tonne_m": rounded(solution["objective_tonne_m"]),
        "selected_capacity_utilization": utilization,
        "gain_claimed": False,
        "gain_metrics": None,
    }


def e0_screening(
    segments: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    operation: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    eligible = [candidate for candidate in candidates if candidate["eligible"]]
    if not eligible or not segments:
        return None, [], {"method": "E0_EXACT_EUCLIDEAN_ENUMERATION"}
    demand = []
    for index, segment in enumerate(segments):
        point = segment["geometry"].interpolate(segment["geometry"].length / 2.0)
        weight = float(segment.get("mass_t", segment["length_m"]))
        demand.append(
            {
                "event_id": f"E0_PROXY_{index + 1:07d}",
                "shot_id": segment["shot_id"],
                "segment_id": segment["segment_id"],
                "geometry": Point(float(point.x), float(point.y)),
                "mass_t": weight,
                "load_type": "GEOMETRIC_PROXY",
            }
        )
    max_sites = min(int(operation.get("max_selected_sites", 1)), len(eligible))
    exact_limit = int(operation.get("exact_site_candidate_limit", 14))
    ids = sorted(candidate["poa_id"] for candidate in eligible)
    lookup = {candidate["poa_id"]: candidate for candidate in eligible}

    def access_point(candidate: Dict[str, Any]) -> Point:
        geometry = candidate["geometry"]
        return geometry if isinstance(geometry, Point) else geometry.representative_point()

    def score(subset: Sequence[str]) -> Tuple[float, Dict[str, str]]:
        assignment: Dict[str, str] = {}
        total = 0.0
        for event in demand:
            poa_id = min(
                subset,
                key=lambda candidate_id: (
                    event["geometry"].distance(access_point(lookup[candidate_id])),
                    candidate_id,
                ),
            )
            assignment[event["event_id"]] = poa_id
            total += event["mass_t"] * event["geometry"].distance(access_point(lookup[poa_id]))
        return total, assignment

    best: Optional[Tuple[float, Tuple[str, ...], Dict[str, str]]] = None
    if len(ids) <= exact_limit:
        method = "E0_EXACT_EUCLIDEAN_ENUMERATION"
        subsets = itertools.chain.from_iterable(
            itertools.combinations(ids, size) for size in range(1, max_sites + 1)
        )
    else:
        method = "E0_GREEDY_EUCLIDEAN_HEURISTIC"
        ranked = sorted(
            ids,
            key=lambda poa_id: sum(
                event["mass_t"] * event["geometry"].distance(access_point(lookup[poa_id]))
                for event in demand
            ),
        )
        subsets = [tuple(ranked[:max_sites])]
    for subset in subsets:
        objective, assignment = score(subset)
        key = (objective, len(subset), tuple(subset))
        if best is None or key < (best[0], len(best[1]), best[1]):
            best = (objective, tuple(subset), assignment)
    if best is None:
        return None, demand, {"method": method}

    assignments = []
    event_lookup = {event["event_id"]: event for event in demand}
    for event_id, poa_id in sorted(best[2].items()):
        event = event_lookup[event_id]
        destination = access_point(lookup[poa_id])
        distance = event["geometry"].distance(destination)
        assignments.append(
            {
                "event_id": event_id,
                "shot_id": event["shot_id"],
                "segment_id": event["segment_id"],
                "poa_id": poa_id,
                "mass_t": event["mass_t"],
                "distance_m": distance,
                "tonne_m": distance * event["mass_t"],
                "route_kind": "EUCLIDEAN_E0_SCREENING_ONLY",
                "geometry": line_between_points(event["geometry"], destination),
            }
        )
    solution = {
        "selected_poa_ids": list(best[1]),
        "objective_tonne_m": best[0],
        "assignment": best[2],
        "assignments": assignments,
    }
    return solution, demand, {
        "method": method,
        "distance_basis": "EUCLIDEAN_E0_SCREENING_ONLY",
        "optimality": "GEOMETRIC_ONLY_NO_OPERATIONAL_OPTIMALITY_CLAIM",
    }


def base_result(mode: str) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_type": MANIFEST_TYPE,
        "status": "BLOCKED",
        "mode": mode,
        "request_validation": {
            "validated": True,
            "contract": MANIFEST_TYPE,
            "central_project_request_translated": False,
        },
        "distance_basis": None,
        "solver": {},
        "selected_poa_ids": [],
        "assignments": [],
        "metrics": {
            "gain_claimed": False,
            "gain_metrics": None,
        },
        "blockers": {
            "execution": [],
            "result": [],
        },
        "warnings": [],
        "artifacts": {},
    }


def routed_execution_errors(
    inputs: Dict[str, Any],
    productivity: Any,
    operation: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> List[str]:
    errors: List[str] = []
    if inputs.get("road_network") is None:
        errors.append("ROUTED_STATIC requires inputs.road_network")
    if not isinstance(productivity, dict):
        errors.append("ROUTED_STATIC requires productivity")
    if inputs["worked_lines"].get("shot_id_field") != "operational_run_id":
        errors.append(
            "ROUTED_STATIC requires inputs.worked_lines.shot_id_field="
            "'operational_run_id' generated after hard-barrier cuts"
        )
    if not isinstance(operation.get("planning_horizon_id"), str) or not operation[
        "planning_horizon_id"
    ].strip():
        errors.append(
            "operation.planning_horizon_id is required to define POA capacity semantics"
        )
    for key in ("harvest_width_m", "transshipment_capacity_t", "network_snap_tolerance_m"):
        if not is_positive_finite_number(operation.get(key)):
            errors.append(f"operation.{key} must be greater than zero")
    for key in (
        "max_selected_sites",
        "exact_site_candidate_limit",
        "exact_assignment_event_limit",
        "max_exact_site_combinations",
    ):
        if key in operation and not is_positive_integer(operation[key]):
            errors.append(f"operation.{key} must be a positive integer")
    if "max_selected_sites" not in operation:
        errors.append("operation.max_selected_sites is required")

    capacity_field = inputs["poa_candidates"].get("capacity_t_field")
    default_capacity = operation.get("poa_default_capacity_t")
    if not capacity_field and default_capacity is None:
        errors.append(
            "ROUTED_STATIC requires a candidate capacity field or poa_default_capacity_t"
        )
    if default_capacity is not None and not is_positive_finite_number(default_capacity):
        errors.append("operation.poa_default_capacity_t must be greater than zero")
    if candidates and not any(
        is_positive_finite_number(candidate.get("capacity_t")) for candidate in candidates
    ):
        errors.append("No POA candidate has a valid positive capacity for the planning horizon")
    return errors


def optimize_request(
    request: Dict[str, Any],
    base_dir: Path,
) -> Tuple[Dict[str, Any], Dict[str, Any], int]:
    validate_request(request)
    mode = request["mode"]
    result = base_result(mode)
    result["project_request_ref"] = request.get("project_request_ref")
    context: Dict[str, Any] = {
        "spatial_ref": None,
        "segments": [],
        "events": [],
        "candidates": [],
        "assignments": [],
        "power_axes": [],
        "power_barrier": GeometryCollection(),
    }

    try:
        inputs = request["inputs"]
        operation = request.get("operation", {})
        worked_data = read_vector(inputs["worked_lines"], base_dir)
        candidate_data = read_vector(inputs["poa_candidates"], base_dir)
        validate_projected_metric(worked_data.spatial_ref)
        if not same_crs(worked_data.spatial_ref, candidate_data.spatial_ref):
            raise DataContractError("POA candidate CRS differs from worked-line CRS")
        context["spatial_ref"] = worked_data.spatial_ref

        constraint_context = build_constraints(
            request,
            base_dir,
            worked_data.spatial_ref,
        )
        context.update(
            {
                "power_axes": constraint_context["power_axes"],
                "power_barrier": constraint_context["power_barrier"],
            }
        )
        result["input_summary"] = {
            "power_status": constraint_context["power_status"],
            "constraint_count": len(constraint_context["sources"]),
        }

        productivity = request.get("productivity")
        segments = clip_worked_lines(
            worked_data,
            inputs["worked_lines"],
            constraint_context["combined"],
            productivity,
        )
        run_metrics = split_disconnected_operational_runs(segments)
        candidates = read_candidates(
            candidate_data,
            inputs["poa_candidates"],
            operation,
            constraint_context["combined"],
            constraint_context["power_barrier"],
            capacity_required=mode == MODE_ROUTED,
        )
        context["segments"] = segments
        context["candidates"] = candidates
        result["input_summary"].update(
            {
                "worked_segment_count_after_constraints": len(segments),
                "poa_candidate_count": len(candidates),
                "poa_candidate_ineligible_count": sum(not item["eligible"] for item in candidates),
                "operational_run_source_field": inputs["worked_lines"].get(
                    "shot_id_field"
                ),
                **run_metrics,
            }
        )
        result["warnings"].append(
            blocker(
                "STAGE_MANIFEST_NO_AUTOMATIC_TRANSLATION",
                "WARNING",
                "Input is a POA stage manifest; no central project request was translated",
            )
        )
        if run_metrics["source_runs_split_by_disconnection"]:
            result["warnings"].append(
                blocker(
                    "OPERATIONAL_RUNS_SPLIT_AT_DISCONNECTIONS",
                    "WARNING",
                    "Disconnected pieces received distinct operational_run_id values; "
                    "partial load mass was not carried across barriers",
                    details=run_metrics,
                )
            )
        result["candidate_screening"] = [
            {
                "poa_id": candidate["poa_id"],
                "geometry_type": candidate["geometry_type"],
                "eligible": candidate["eligible"],
                "ineligibility_reasons": list(candidate["ineligibility_reasons"]),
                "capacity_t_for_planning_horizon": rounded(candidate["capacity_t"]),
            }
            for candidate in candidates
        ]

        execution_errors: List[str] = []
        if mode == MODE_ROUTED:
            execution_errors = routed_execution_errors(
                inputs,
                productivity,
                operation,
                candidates,
            )
        elif not is_positive_integer(operation.get("max_selected_sites")):
            execution_errors.append(
                "E0_EUCLIDEAN_SCREENING requires a positive integer "
                "operation.max_selected_sites"
            )
        if execution_errors:
            result["blockers"]["execution"].extend(
                blocker("MISSING_OPERATIONAL_INPUT", "EXECUTION", message)
                for message in execution_errors
            )
            return result, context, EXIT_BLOCKED

        if not segments:
            result["blockers"]["result"].append(
                blocker(
                    "NO_WORKED_SEGMENTS_AFTER_CONSTRAINTS",
                    "RESULT",
                    "Hard constraints removed all worked segments",
                )
            )
            return result, context, EXIT_BLOCKED
        if not any(candidate["eligible"] for candidate in candidates):
            result["blockers"]["result"].append(
                blocker(
                    "NO_ELIGIBLE_POA_CANDIDATE",
                    "RESULT",
                    "All POA candidates intersect a hard exclusion or have invalid attributes",
                )
            )
            return result, context, EXIT_BLOCKED

        if mode == MODE_E0:
            if productivity and isinstance(productivity, dict):
                try:
                    harvest_width = float(operation.get("harvest_width_m", 1.0))
                    assign_productivity(segments, productivity, harvest_width)
                except (DataContractError, KeyError, TypeError, ValueError) as error:
                    result["warnings"].append(
                        blocker(
                            "E0_PRODUCTIVITY_IGNORED",
                            "WARNING",
                            str(error),
                        )
                    )
            solution, proxies, solver = e0_screening(segments, candidates, operation)
            context["events"] = proxies
            if solution is None:
                result["blockers"]["result"].append(
                    blocker("E0_NO_GEOMETRIC_SOLUTION", "RESULT", "No geometric screening solution")
                )
                return result, context, EXIT_BLOCKED
            context["assignments"] = solution["assignments"]
            result.update(
                {
                    "status": "GEOMETRIC_SCREENING",
                    "distance_basis": "EUCLIDEAN_E0_SCREENING_ONLY",
                    "solver": solver,
                    "selected_poa_ids": solution["selected_poa_ids"],
                    "assignments": [
                        {key: value for key, value in item.items() if key != "geometry"}
                        for item in solution["assignments"]
                    ],
                    "metrics": {
                        "proxy_count": len(proxies),
                        "euclidean_proxy_objective": rounded(solution["objective_tonne_m"]),
                        "gain_claimed": False,
                        "gain_metrics": None,
                        "operational_metrics_available": False,
                    },
                }
            )
            result["warnings"].append(
                blocker(
                    "E0_NOT_OPERATIONAL",
                    "WARNING",
                    "Euclidean screening does not represent a drivable route, capacity plan or gain",
                )
            )
            return result, context, EXIT_OK

        road_spec = inputs.get("road_network")
        harvest_width = float(operation["harvest_width_m"])
        transshipment_capacity = float(operation["transshipment_capacity_t"])
        assign_productivity(segments, productivity, harvest_width)
        events = generate_load_events(segments, transshipment_capacity)
        context["events"] = events
        if not events:
            result["blockers"]["result"].append(
                blocker("NO_LOAD_EVENTS", "RESULT", "No positive load demand was generated")
            )
            return result, context, EXIT_BLOCKED

        road_data = read_vector(road_spec, base_dir)
        if not same_crs(worked_data.spatial_ref, road_data.spatial_ref):
            raise DataContractError("Road-network CRS differs from worked-line CRS")
        road_lines, road_metrics = read_and_filter_roads(
            road_data,
            constraint_context["combined"],
            constraint_context["power_barrier"],
        )
        road_segments = make_road_segments(road_lines)
        result["input_summary"].update(road_metrics)
        result["input_summary"]["road_segment_count_after_constraints"] = len(road_segments)
        if not road_segments:
            result["blockers"]["execution"].append(
                blocker(
                    "ROAD_NETWORK_REMOVED_BY_CONSTRAINTS",
                    "EXECUTION",
                    "No routable road edge remains after applying hard barriers",
                )
            )
            return result, context, EXIT_BLOCKED

        snap_tolerance = float(operation["network_snap_tolerance_m"])
        graph, graph_metrics, graph_warnings = build_routed_graph(
            road_segments,
            segments,
            events,
            candidates,
            constraint_context["combined"],
            snap_tolerance,
        )
        result["input_summary"].update(graph_metrics)
        result["warnings"].extend(graph_warnings)
        if graph_metrics["access_connector_length_m"] > EPSILON:
            result["warnings"].append(
                blocker(
                    "INFERRED_ACCESS_CONNECTORS",
                    "WARNING",
                    "Routes include explicit straight access connectors to the road graph; "
                    "review them before operational use",
                    details={
                        "connector_length_m": rounded(
                            graph_metrics["access_connector_length_m"]
                        ),
                        "snap_tolerance_m": rounded(snap_tolerance),
                    },
                )
            )
        result["input_summary"]["poa_candidate_ineligible_count"] = sum(
            not item["eligible"] for item in candidates
        )
        result["candidate_screening"] = [
            {
                "poa_id": candidate["poa_id"],
                "geometry_type": candidate["geometry_type"],
                "eligible": candidate["eligible"],
                "ineligibility_reasons": list(candidate["ineligibility_reasons"]),
                "capacity_t_for_planning_horizon": rounded(candidate["capacity_t"]),
                "access_connector_m": rounded(candidate["access_connector_m"]),
            }
            for candidate in candidates
        ]

        route_matrix, route_blockers = compute_route_matrix(graph, events, candidates)
        if route_blockers:
            result["blockers"]["result"].extend(route_blockers)
            return result, context, EXIT_BLOCKED

        solution, solver_metadata = optimize_sites(
            events,
            candidates,
            route_matrix,
            operation,
        )
        result["solver"] = solver_metadata
        if solution is None:
            result["blockers"]["result"].append(
                blocker(
                    "NO_CAPACITATED_ROUTED_SOLUTION",
                    "RESULT",
                    "The valid instance has no feasible POA subset and indivisible load assignment",
                )
            )
            return result, context, EXIT_BLOCKED

        assignments = assignments_with_routes(solution, events, route_matrix, graph)
        route_violations = [
            assignment["event_id"]
            for assignment in assignments
            if not constraint_context["combined"].is_empty
            and assignment["geometry"].intersects(constraint_context["combined"])
        ]
        if route_violations:
            result["blockers"]["result"].append(
                blocker(
                    "ASSIGNMENT_ROUTE_INTERSECTS_HARD_EXCLUSION",
                    "RESULT",
                    "A computed route touches or enters a hard exclusion; result rejected",
                    details={"event_ids": route_violations},
                )
            )
            return result, context, EXIT_BLOCKED

        context["assignments"] = assignments
        candidate_lookup = {candidate["poa_id"]: candidate for candidate in candidates}
        selected_point_ids = [
            poa_id
            for poa_id in solution["selected_poa_ids"]
            if candidate_lookup[poa_id]["geometry_type"] == "Point"
        ]
        completion_status = "GEOMETRIC_SCREENING" if selected_point_ids else "OPTIMIZED"
        metrics = routed_metrics(
            segments,
            events,
            candidates,
            assignments,
            solution,
        )
        metrics.update(
            {
                "planning_horizon_id": operation["planning_horizon_id"],
                "poa_capacity_semantics": "TOTAL_ASSIGNABLE_TONNES_IN_PLANNING_HORIZON",
                "distance_semantics": "ONE_WAY_LOAD_EVENT_TO_POA",
                "operational_metrics_available": not selected_point_ids,
                "selected_point_candidate_count": len(selected_point_ids),
            }
        )
        result.update(
            {
                "status": completion_status,
                "distance_basis": "NETWORKX_ROUTED_GRAPH",
                "selected_poa_ids": solution["selected_poa_ids"],
                "assignments": [
                    {key: value for key, value in item.items() if key != "geometry"}
                    for item in assignments
                ],
                "metrics": metrics,
            }
        )
        result["warnings"].extend(
            [
                blocker(
                    "APPROXIMATE_LOAD_EVENTS",
                    "WARNING",
                    "Load events are deterministic mass increments along operational runs, "
                    "not observed transshipment telemetry",
                ),
                blocker(
                    "STATIC_ONE_WAY_NO_FLEET_CYCLE",
                    "WARNING",
                    "Distances are one-way assignments; fleet count, speed, return travel, "
                    "queues, loading time and road capacity are not simulated",
                ),
            ]
        )
        if selected_point_ids:
            result["warnings"].append(
                blocker(
                    "POINT_POA_IS_NOT_A_VALIDATED_YARD",
                    "WARNING",
                    "A selected Point candidate does not prove patio area or layout; "
                    "result downgraded to GEOMETRIC_SCREENING",
                    details={"poa_ids": selected_point_ids},
                )
            )
        return result, context, EXIT_OK
    except DataContractError as error:
        result["blockers"]["execution"].append(
            blocker("DATA_CONTRACT_ERROR", "EXECUTION", str(error))
        )
        return result, context, EXIT_BLOCKED


def ogr_geometry(geometry: Any) -> Optional[ogr.Geometry]:
    if geometry is None or geometry.is_empty:
        return None
    return ogr.CreateGeometryFromWkb(bytes(geometry.wkb))


def create_fields(layer: ogr.Layer, definitions: Sequence[Tuple[str, int]]) -> None:
    for name, field_type in definitions:
        definition = ogr.FieldDefn(name, field_type)
        if field_type == ogr.OFTString:
            definition.SetWidth(254)
        layer.CreateField(definition)


def add_ogr_feature(
    layer: ogr.Layer,
    values: Dict[str, Any],
    geometry: Optional[Any] = None,
) -> None:
    feature = ogr.Feature(layer.GetLayerDefn())
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, bool):
            feature.SetField(key, int(value))
        else:
            feature.SetField(key, value)
    converted = ogr_geometry(geometry)
    if converted is not None:
        feature.SetGeometry(converted)
    layer.CreateFeature(feature)
    feature = None


def write_gpkg(path: Path, result: Dict[str, Any], context: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    driver = ogr.GetDriverByName("GPKG")
    if path.exists():
        driver.DeleteDataSource(str(path))
    dataset = driver.CreateDataSource(str(path))
    if dataset is None:
        raise RuntimeError(f"Could not create {path}")
    spatial_ref = context.get("spatial_ref")

    candidate_layer = dataset.CreateLayer("poa_candidates", spatial_ref, ogr.wkbUnknown)
    create_fields(
        candidate_layer,
        (
            ("poa_id", ogr.OFTString),
            ("eligible", ogr.OFTInteger),
            ("selected", ogr.OFTInteger),
            ("capacity_t", ogr.OFTReal),
            ("assigned_t", ogr.OFTReal),
            ("access_m", ogr.OFTReal),
            ("reasons", ogr.OFTString),
        ),
    )
    selected_ids = set(result.get("selected_poa_ids", []))
    assigned_by_poa: Dict[str, float] = defaultdict(float)
    for assignment in result.get("assignments", []):
        assigned_by_poa[assignment["poa_id"]] += float(assignment.get("mass_t", 0.0))
    for candidate in context.get("candidates", []):
        add_ogr_feature(
            candidate_layer,
            {
                "poa_id": candidate["poa_id"],
                "eligible": candidate["eligible"],
                "selected": candidate["poa_id"] in selected_ids,
                "capacity_t": candidate.get("capacity_t"),
                "assigned_t": assigned_by_poa.get(candidate["poa_id"], 0.0),
                "access_m": candidate.get("access_connector_m"),
                "reasons": ";".join(candidate.get("ineligibility_reasons", [])),
            },
            candidate["geometry"],
        )

    selected_layer = dataset.CreateLayer("selected_poas", spatial_ref, ogr.wkbUnknown)
    create_fields(
        selected_layer,
        (
            ("poa_id", ogr.OFTString),
            ("capacity_t", ogr.OFTReal),
            ("assigned_t", ogr.OFTReal),
            ("status", ogr.OFTString),
        ),
    )
    for candidate in context.get("candidates", []):
        if candidate["poa_id"] not in selected_ids:
            continue
        add_ogr_feature(
            selected_layer,
            {
                "poa_id": candidate["poa_id"],
                "capacity_t": candidate.get("capacity_t"),
                "assigned_t": assigned_by_poa.get(candidate["poa_id"], 0.0),
                "status": result["status"],
            },
            candidate["geometry"],
        )

    event_layer = dataset.CreateLayer("load_events", spatial_ref, ogr.wkbPoint)
    create_fields(
        event_layer,
        (
            ("event_id", ogr.OFTString),
            ("shot_id", ogr.OFTString),
            ("segment_id", ogr.OFTString),
            ("load_type", ogr.OFTString),
            ("mass_t", ogr.OFTReal),
            ("poa_id", ogr.OFTString),
            ("route_m", ogr.OFTReal),
        ),
    )
    assignment_lookup = {
        assignment["event_id"]: assignment for assignment in result.get("assignments", [])
    }
    for event in context.get("events", []):
        assignment = assignment_lookup.get(event["event_id"], {})
        add_ogr_feature(
            event_layer,
            {
                "event_id": event["event_id"],
                "shot_id": event["shot_id"],
                "segment_id": event["segment_id"],
                "load_type": event["load_type"],
                "mass_t": event["mass_t"],
                "poa_id": assignment.get("poa_id"),
                "route_m": assignment.get("distance_m"),
            },
            event["geometry"],
        )

    route_layer = dataset.CreateLayer("assignment_routes", spatial_ref, ogr.wkbLineString)
    create_fields(
        route_layer,
        (
            ("event_id", ogr.OFTString),
            ("shot_id", ogr.OFTString),
            ("poa_id", ogr.OFTString),
            ("mass_t", ogr.OFTReal),
            ("distance_m", ogr.OFTReal),
            ("tonne_m", ogr.OFTReal),
            ("route_kind", ogr.OFTString),
        ),
    )
    for assignment in context.get("assignments", []):
        add_ogr_feature(
            route_layer,
            {
                "event_id": assignment["event_id"],
                "shot_id": assignment["shot_id"],
                "poa_id": assignment["poa_id"],
                "mass_t": assignment["mass_t"],
                "distance_m": assignment["distance_m"],
                "tonne_m": assignment["tonne_m"],
                "route_kind": assignment["route_kind"],
            },
            assignment["geometry"],
        )

    if context.get("power_axes"):
        power_layer = dataset.CreateLayer("power_line_axis", spatial_ref, ogr.wkbLineString)
        create_fields(power_layer, (("source", ogr.OFTString),))
        for geometry in context["power_axes"]:
            add_ogr_feature(power_layer, {"source": "REQUEST"}, geometry)
    power_barrier = context.get("power_barrier")
    if power_barrier is not None and not power_barrier.is_empty:
        barrier_layer = dataset.CreateLayer("power_barrier", spatial_ref, ogr.wkbUnknown)
        create_fields(barrier_layer, (("status", ogr.OFTString),))
        add_ogr_feature(barrier_layer, {"status": "ABSOLUTE_BARRIER_V1"}, power_barrier)

    blocker_layer = dataset.CreateLayer("blockers", None, ogr.wkbNone)
    create_fields(
        blocker_layer,
        (
            ("stage", ogr.OFTString),
            ("code", ogr.OFTString),
            ("feature_id", ogr.OFTString),
            ("message", ogr.OFTString),
            ("details", ogr.OFTString),
        ),
    )
    for category in ("execution", "result"):
        for item in result.get("blockers", {}).get(category, []):
            add_ogr_feature(
                blocker_layer,
                {
                    "stage": item.get("stage", category.upper()),
                    "code": item["code"],
                    "feature_id": item.get("feature_id"),
                    "message": item["message"],
                    "details": json.dumps(item.get("details", {}), ensure_ascii=False),
                },
            )

    metric_layer = dataset.CreateLayer("metrics", None, ogr.wkbNone)
    create_fields(metric_layer, (("metric", ogr.OFTString), ("value_json", ogr.OFTString)))
    for name, value in sorted(result.get("metrics", {}).items()):
        add_ogr_feature(
            metric_layer,
            {"metric": name, "value_json": json.dumps(value, ensure_ascii=False)},
        )
    dataset = None


def write_outputs(
    request: Dict[str, Any],
    base_dir: Path,
    result: Dict[str, Any],
    context: Dict[str, Any],
) -> None:
    json_path = resolve_path(request["outputs"]["json"], base_dir)
    gpkg_path = resolve_path(request["outputs"]["gpkg"], base_dir)
    result["artifacts"] = {
        "json": str(json_path),
        "gpkg": str(gpkg_path),
    }
    write_gpkg(gpkg_path, result, context)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def execute_request_file(path: Path) -> Tuple[Dict[str, Any], int]:
    request_path = path.resolve()
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        validate_request(request)
    except (OSError, json.JSONDecodeError, RequestValidationError) as error:
        errors = error.errors if isinstance(error, RequestValidationError) else [str(error)]
        result = {
            "schema_version": SCHEMA_VERSION,
            "manifest_type": MANIFEST_TYPE,
            "status": "INVALID_REQUEST",
            "request_validation": {
                "validated": False,
                "contract": MANIFEST_TYPE,
                "central_project_request_translated": False,
                "errors": errors,
            },
            "exit_code": EXIT_INVALID_REQUEST,
        }
        return result, EXIT_INVALID_REQUEST

    try:
        result, context, exit_code = optimize_request(request, request_path.parent)
        result["exit_code"] = exit_code
        write_outputs(request, request_path.parent, result, context)
        return result, exit_code
    except Exception as error:  # pragma: no cover - last-resort CLI boundary
        result = {
            "schema_version": SCHEMA_VERSION,
            "manifest_type": MANIFEST_TYPE,
            "status": "RUNTIME_ERROR",
            "request_validation": {
                "validated": True,
                "contract": MANIFEST_TYPE,
                "central_project_request_translated": False,
            },
            "error": str(error),
            "traceback": traceback.format_exc(),
            "exit_code": EXIT_RUNTIME_ERROR,
        }
        return result, EXIT_RUNTIME_ERROR


def create_fixture_layer(
    dataset: ogr.DataSource,
    name: str,
    spatial_ref: osr.SpatialReference,
    geometry_type: int,
    field_definitions: Sequence[Tuple[str, int]],
    features: Sequence[Tuple[Dict[str, Any], Any]],
) -> None:
    layer = dataset.CreateLayer(name, spatial_ref, geometry_type)
    create_fields(layer, field_definitions)
    for values, geometry in features:
        add_ogr_feature(layer, values, geometry)


def make_self_test_fixture(directory: Path) -> Path:
    fixture = directory / "known_optimum.gpkg"
    driver = ogr.GetDriverByName("GPKG")
    dataset = driver.CreateDataSource(str(fixture))
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromEPSG(31983)

    create_fixture_layer(
        dataset,
        "worked_lines",
        spatial_ref,
        ogr.wkbLineString,
        (
            ("segment_id", ogr.OFTString),
            ("operational_run_id", ogr.OFTString),
            ("sequence", ogr.OFTInteger),
        ),
        (
            (
                {
                    "segment_id": "S1",
                    "operational_run_id": "RUN_1",
                    "sequence": 1,
                },
                LineString([(10.0, 10.0), (10.0, 20.0)]),
            ),
            (
                {
                    "segment_id": "S2",
                    "operational_run_id": "RUN_2",
                    "sequence": 1,
                },
                LineString([(20.0, 10.0), (20.0, 20.0)]),
            ),
        ),
    )
    create_fixture_layer(
        dataset,
        "roads",
        spatial_ref,
        ogr.wkbLineString,
        (("road_id", ogr.OFTString),),
        (({"road_id": "R1"}, LineString([(0.0, 0.0), (100.0, 0.0)])),),
    )
    create_fixture_layer(
        dataset,
        "poa_candidates",
        spatial_ref,
        ogr.wkbPolygon,
        (("poa_id", ogr.OFTString), ("capacity_t", ogr.OFTReal)),
        (
            (
                {"poa_id": "POA_A", "capacity_t": 10.0},
                Polygon([(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)]),
            ),
            (
                {"poa_id": "POA_B", "capacity_t": 10.0},
                Polygon(
                    [(99.0, -1.0), (101.0, -1.0), (101.0, 1.0), (99.0, 1.0)]
                ),
            ),
            (
                {"poa_id": "POA_BLOCKED", "capacity_t": 10.0},
                Polygon([(49.0, -1.0), (51.0, -1.0), (51.0, 1.0), (49.0, 1.0)]),
            ),
        ),
    )
    create_fixture_layer(
        dataset,
        "point_candidates",
        spatial_ref,
        ogr.wkbPoint,
        (("poa_id", ogr.OFTString), ("capacity_t", ogr.OFTReal)),
        (
            ({"poa_id": "POA_A", "capacity_t": 10.0}, Point(0.0, 0.0)),
            ({"poa_id": "POA_B", "capacity_t": 10.0}, Point(100.0, 0.0)),
            ({"poa_id": "POA_BLOCKED", "capacity_t": 10.0}, Point(50.0, 0.0)),
        ),
    )
    create_fixture_layer(
        dataset,
        "power_line_axis",
        spatial_ref,
        ogr.wkbLineString,
        (("power_id", ogr.OFTString),),
        (({"power_id": "PWR_1"}, LineString([(50.0, -20.0), (50.0, 20.0)])),),
    )
    dataset = None
    return fixture


def make_self_test_request(
    directory: Path,
    fixture: Path,
    candidate_layer: str = "poa_candidates",
) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_type": MANIFEST_TYPE,
        "project_request_ref": {
            "id": "SELF_TEST_PROJECT_REQUEST",
            "hash": "sha256:self-test-static-fixture",
        },
        "mode": MODE_ROUTED,
        "inputs": {
            "worked_lines": {
                "path": str(fixture),
                "layer": "worked_lines",
                "id_field": "segment_id",
                "shot_id_field": "operational_run_id",
                "sequence_field": "sequence",
            },
            "poa_candidates": {
                "path": str(fixture),
                "layer": candidate_layer,
                "id_field": "poa_id",
                "capacity_t_field": "capacity_t",
            },
            "road_network": {"path": str(fixture), "layer": "roads"},
        },
        "constraints": {
            "power_line_axis": {
                "path": str(fixture),
                "layer": "power_line_axis",
                "buffer_m": 2.0,
            },
            "exclusions": [],
        },
        "productivity": {"mode": "UNIFORM", "tonnes_per_hectare": 100.0},
        "operation": {
            "planning_horizon_id": "SELF_TEST_HORIZON",
            "harvest_width_m": 10.0,
            "transshipment_capacity_t": 1.0,
            "max_selected_sites": 1,
            "network_snap_tolerance_m": 25.0,
            "exact_site_candidate_limit": 10,
            "exact_assignment_event_limit": 10,
        },
        "outputs": {
            "json": str(directory / "result.json"),
            "gpkg": str(directory / "result.gpkg"),
        },
    }


def run_self_test() -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="poa_static_self_test_") as temporary:
        directory = Path(temporary)
        fixture = make_self_test_fixture(directory)
        request = make_self_test_request(directory, fixture)
        result, context, exit_code = optimize_request(request, directory)
        result["exit_code"] = exit_code
        write_outputs(request, directory, result, context)

        failures = []
        if exit_code != EXIT_OK:
            failures.append(f"exit code is {exit_code}")
        if result.get("status") != "OPTIMIZED":
            failures.append(f"status is {result.get('status')}")
        if result.get("selected_poa_ids") != ["POA_A"]:
            failures.append(f"selected POAs are {result.get('selected_poa_ids')}")
        objective = result.get("metrics", {}).get("objective_tonne_m")
        if objective is None or abs(float(objective) - 70.0) > 1e-6:
            failures.append(f"objective is {objective}, expected 70.0")
        blocked_candidate = next(
            candidate for candidate in context["candidates"] if candidate["poa_id"] == "POA_BLOCKED"
        )
        if blocked_candidate["eligible"]:
            failures.append("POA inside power barrier remained eligible")
        if "INTERSECTS_POWER_LINE_BARRIER" not in blocked_candidate[
            "ineligibility_reasons"
        ]:
            failures.append("power-barrier candidate lacks the specific blocker reason")
        if (
            result.get("input_summary", {}).get(
                "power_barrier_removed_road_length_m", 0.0
            )
            <= 0
        ):
            failures.append("power barrier did not remove road length")
        if any(
            assignment["geometry"].intersects(context["power_barrier"])
            for assignment in context["assignments"]
        ):
            failures.append("an assignment route intersects the power barrier")
        if result.get("project_request_ref") != request["project_request_ref"]:
            failures.append("project-request traceability was not preserved")
        if result.get("request_validation", {}).get(
            "central_project_request_translated"
        ) is not False:
            failures.append("stage manifest incorrectly claims central-request translation")
        for output_name in ("result.json", "result.gpkg"):
            if not (directory / output_name).exists():
                failures.append(f"missing output {output_name}")
        if failures:
            raise AssertionError("; ".join(failures))
        return {
            "status": "SELF_TEST_PASSED",
            "known_optimum": {
                "selected_poa_ids": result["selected_poa_ids"],
                "objective_tonne_m": objective,
            },
            "power_barrier": {
                "candidate_inside_barrier_ineligible": True,
                "road_edges_clipped": True,
                "assignment_routes_clear": True,
                "automatic_crossing": False,
            },
            "manifest": {
                "type": result["manifest_type"],
                "project_request_ref_preserved": True,
                "automatic_translation": False,
            },
            "solver": result["solver"],
        }


def parse_arguments(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--request", type=Path, help="Validated JSON request")
    group.add_argument("--self-test", action="store_true", help="Run deterministic known-optimum test")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = parse_arguments(argv)
    if arguments.self_test:
        try:
            summary = run_self_test()
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return EXIT_OK
        except Exception as error:  # pragma: no cover - CLI boundary
            print(
                json.dumps(
                    {
                        "status": "SELF_TEST_FAILED",
                        "error": str(error),
                        "traceback": traceback.format_exc(),
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return EXIT_SELF_TEST_FAILED

    result, exit_code = execute_request_file(arguments.request)
    print(
        json.dumps(
            {
                "manifest_type": result.get("manifest_type"),
                "project_request_ref": result.get("project_request_ref"),
                "status": result.get("status"),
                "exit_code": exit_code,
                "distance_basis": result.get("distance_basis"),
                "selected_poa_ids": result.get("selected_poa_ids", []),
                "execution_blocker_count": len(
                    result.get("blockers", {}).get("execution", [])
                ),
                "result_blocker_count": len(result.get("blockers", {}).get("result", [])),
                "artifacts": result.get("artifacts", {}),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
