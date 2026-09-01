"""Generate C1 embedded-terrace concept alignments for E0 review only.

The stage evaluates terrain-isoline sensitivity intervals, partitions the work
area with a topology-only gap, and clips existing CF0C rows into diagnostic
segments.  It does not run PCE or PCX, apply an embedded cross section, build a
proposed DTM, calculate earthwork, approve TI/TD, or authorize guidance.

Run with the QGIS Python environment::

    & 'C:\\Program Files\\QGIS 3.32.1\\bin\\python-qgis.bat' `
      '.\\scripts\\generate_embedded_terrace_screening.py'
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LightSource
from osgeo import ogr, osr
from shapely import wkb
from shapely.geometry import LineString, Point, Polygon

try:
    import generate_sulcation_scenarios as e0
    from embedded_terrace_screening import (
        SCREENING_BLOCKERS,
        TD_BLOCKERS,
        ScreeningPolicy,
        clip_rows_to_strips,
        extract_ti_sensitivity_axes,
        iter_lines,
        iter_polygons,
        partition_interterrace_strips,
        scenario_id,
        segment_length_metrics,
    )
    from generate_continuous_family import extend_line_to_physical_boundary
    from project_request import ContractError, ResolvedProjectRequest, load_project_request
except ModuleNotFoundError:  # Allows ``python -m scripts...`` from the repo root.
    from scripts import generate_sulcation_scenarios as e0
    from scripts.embedded_terrace_screening import (
        SCREENING_BLOCKERS,
        TD_BLOCKERS,
        ScreeningPolicy,
        clip_rows_to_strips,
        extract_ti_sensitivity_axes,
        iter_lines,
        iter_polygons,
        partition_interterrace_strips,
        scenario_id,
        segment_length_metrics,
    )
    from scripts.generate_continuous_family import extend_line_to_physical_boundary
    from scripts.project_request import ContractError, ResolvedProjectRequest, load_project_request


ogr.UseExceptions()

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "dataset" / "derived"
DEFAULT_SCREENING_REQUEST = REPO / "config" / "exemplo_pedido_c1_screening_dataset_atual.json"
DEFAULT_GPKG = DERIVED / "embedded_terrace_screening.gpkg"
DEFAULT_MANIFEST = DERIVED / "embedded_terrace_screening_manifest.json"
DEFAULT_MAP = DERIVED / "embedded_terrace_screening_map.png"

RELEASE = "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED"
MANIFEST_TYPE = "EMBEDDED_TERRACE_SCREENING_STAGE_RESULT"
SCHEMA_VERSION = "1.0.0"
RELEASE_LIMITATIONS = [
    "NOT_A_C1_PROJECT",
    "NOT_PCE_SPACING",
    "NOT_PCX_DIMENSIONING",
    "NOT_TI_APPROVAL",
    "TD_NOT_GENERATED",
    "EMBEDDED_SECTION_NOT_APPLIED",
    "NO_PROPOSED_DTM_OR_EARTHWORK",
    "ROWS_ARE_CF0C_DIAGNOSTIC_CLIPS_NOT_STRIP_SOLUTIONS",
    "NOT_FOR_GUIDANCE",
    "VERTICAL_ACCURACY_NOT_VALIDATED",
    "FIELD_AND_PROFESSIONAL_VALIDATION_REQUIRED",
]


def rounded(value: Any, digits: int = 6) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(number, digits)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO).as_posix()
    except ValueError:
        return str(resolved)


def resolve_reference(value: str, owner_path: Path) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        return candidate.resolve()
    repository_path = (REPO / candidate).resolve()
    if repository_path.exists():
        return repository_path
    return (owner_path.parent / candidate).resolve()


def file_record(path: Path, role: str, *, published_path: Path | None = None) -> dict[str, Any]:
    return {
        "path": display_path(published_path or path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
        "role": role,
    }


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def validate_screening_request(payload: dict[str, Any]) -> ScreeningPolicy:
    require(payload.get("schema_version") == "1.0.0", "Unexpected screening request version.")
    require(payload.get("requested_release") == RELEASE, "Screening request release mismatch.")
    assumptions = payload.get("assumption_contract")
    require(isinstance(assumptions, dict), "assumption_contract is required.")
    require(assumptions.get("parameter_class") == "E0_ASSUMPTION", "Sensitivity values must be E0 assumptions.")
    require(
        assumptions.get("selection_meaning") == "GEOMETRIC_SENSITIVITY_GRID_ONLY",
        "Sensitivity values must be labelled geometric only.",
    )
    require(
        assumptions.get("selection_rule") == "DATASET_RELIEF_LEGIBILITY_GRID_V1",
        "Sensitivity values must retain the dataset-relief legibility rule.",
    )
    require(
        assumptions.get("interval_role") == "ELEVATION_ISOLINE_SAMPLING_NOT_PCE_SPACING",
        "Vertical intervals cannot be labelled as PCE spacing.",
    )
    require(
        assumptions.get("vertical_accuracy_status") == "NOT_VALIDATED",
        "This E0 stage cannot imply validated vertical accuracy.",
    )
    for forbidden_claim in (
        "agronomic_default",
        "pce_spacing_claim",
        "pcx_dimensioning_claim",
        "ti_approval_claim",
        "td_approval_claim",
        "guidance_claim",
    ):
        require(assumptions.get(forbidden_claim) is False, f"{forbidden_claim} must remain false.")

    parameters = payload.get("screening_parameters")
    require(isinstance(parameters, dict), "screening_parameters is required.")
    policy = ScreeningPolicy(
        vertical_interval_candidates_m=tuple(parameters.get("vertical_interval_candidates_m", [])),
        offset_fractions=tuple(parameters.get("offset_fractions", [])),
        topology_gap_half_width_m=float(parameters.get("topology_gap_half_width_m", math.nan)),
        minimum_axis_length_m=float(parameters.get("minimum_axis_length_m", math.nan)),
        minimum_strip_area_m2=float(parameters.get("minimum_strip_area_m2", math.nan)),
        minimum_row_segment_length_m=float(parameters.get("minimum_diagnostic_row_segment_m", math.nan)),
    ).validate()
    require(
        policy.vertical_interval_candidates_m == (2.0, 4.0, 6.0),
        "DATASET_RELIEF_LEGIBILITY_GRID_V1 requires the 2/4/6 m geometric sampling grid.",
    )
    profile_step = float(parameters.get("profile_sample_step_m", math.nan))
    require(math.isfinite(profile_step) and profile_step > 0, "profile_sample_step_m must be positive.")
    map_offset = float(parameters.get("map_offset_fraction", math.nan))
    require(map_offset in policy.offset_fractions, "map_offset_fraction must be one configured offset.")

    variant = payload.get("variant_policy")
    require(isinstance(variant, dict), "variant_policy is required.")
    require(
        variant.get("EMBUTIDA_TI") == "GENERATE_ISOLINE_SENSITIVITY_NOT_DIMENSIONED",
        "TI must remain a non-dimensioned isoline sensitivity.",
    )
    require(
        variant.get("EMBUTIDA_TD") == "NOT_GENERATED_WITHOUT_VERIFIED_RECEIVER_AND_GRADE_RULE",
        "TD policy must fail closed without receiver and grade rule.",
    )
    require(variant.get("receiver_dataset_ref") is None, "This V1 cannot consume a receiver silently.")
    require(variant.get("td_longitudinal_grade_rule_ref") is None, "This V1 cannot consume a TD grade rule silently.")

    row_source = payload.get("row_source_policy")
    require(isinstance(row_source, dict), "row_source_policy is required.")
    require(row_source.get("source_candidate_id") == "CF0C_OPERACAO", "Only CF0C_OPERACAO is supported as diagnostic row source.")
    require(row_source.get("status") == "DIAGNOSTIC_ONLY_NOT_RESOLVED_PER_STRIP", "CF0C row source must remain diagnostic.")
    require(
        row_source.get("limitation_code")
        == "ROW_CANDIDATES_DERIVED_FROM_CF0C_NOT_RESOLVED_PER_STRIP",
        "The per-strip solver limitation must be explicit.",
    )
    return policy


def spatial_reference_manifest(projection: str) -> dict[str, Any]:
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromWkt(projection)
    require(bool(spatial_ref.IsProjected()), "C1 screening requires a projected CRS.")
    require(abs(float(spatial_ref.GetLinearUnits()) - 1.0) <= 1e-9, "C1 screening requires metre units.")
    spatial_ref.AutoIdentifyEPSG()
    authority = spatial_ref.GetAuthorityName(None) or spatial_ref.GetAuthorityName("PROJCS")
    code = spatial_ref.GetAuthorityCode(None) or spatial_ref.GetAuthorityCode("PROJCS")
    require(authority == "EPSG" and code is not None, "Projected CRS must resolve to EPSG.")
    return {
        "type": "PROJECTED",
        "authority": "EPSG",
        "code": int(code),
        "horizontal_unit": "m",
        "wkt": projection,
        "wkt_sha256": hashlib.sha256(projection.encode("utf-8")).hexdigest(),
    }


def load_cf0_rows(path: Path, source_candidate_id: str, field_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    datasource = ogr.Open(str(path), 0)
    require(datasource is not None, f"Could not open CF0 GeoPackage: {path}")
    layer = datasource.GetLayerByName("continuous_rows")
    require(layer is not None, "CF0 GeoPackage lacks continuous_rows.")
    rows: dict[str, list[dict[str, Any]]] = {field_id: [] for field_id in sorted(field_ids)}
    for feature in layer:
        if str(feature.GetField("candidate_id")) != source_candidate_id:
            continue
        field_id = str(feature.GetField("field_id"))
        if field_id not in rows:
            continue
        require(str(feature.GetField("geometry_status")) == "GEOMETRIC_PASS", "CF0C source row is not a geometric pass.")
        geometry_ref = feature.GetGeometryRef()
        require(geometry_ref is not None and not geometry_ref.IsEmpty(), "CF0C source row has empty geometry.")
        geometry = wkb.loads(bytes(geometry_ref.ExportToWkb()))
        require(isinstance(geometry, LineString) and geometry.is_valid, "CF0C source row geometry is invalid.")
        rows[field_id].append(
            {
                "source_row_id": str(feature.GetField("row_id")),
                "source_family_id": str(feature.GetField("family_id")),
                "geometry": geometry,
                "length_m": float(geometry.length),
            }
        )
    datasource = None
    for field_id, items in rows.items():
        items.sort(key=lambda item: item["source_row_id"])
        require(items, f"No {source_candidate_id} geometric-pass rows found for field {field_id}.")
    return rows


def drape_line(line: LineString, terrain: e0.Terrain, sample_step_m: float) -> tuple[LineString, np.ndarray, np.ndarray]:
    count = max(2, int(math.ceil(line.length / sample_step_m)) + 1)
    regular_distances = np.linspace(0.0, float(line.length), count)
    source_coordinates = np.asarray(line.coords, dtype=float)[:, :2]
    source_distances = np.concatenate(
        (
            np.asarray([0.0]),
            np.cumsum(np.hypot(np.diff(source_coordinates[:, 0]), np.diff(source_coordinates[:, 1]))),
        )
    )
    # Preserve every source vertex so the published LineStringZ cannot cut a
    # corner of the extracted isoline while adding profile samples.
    distances = np.unique(np.concatenate((regular_distances, source_distances)))
    coordinates = np.asarray([line.interpolate(float(distance)).coords[0][:2] for distance in distances], dtype=float)
    elevations = terrain.sample(terrain.elevation, coordinates[:, 0], coordinates[:, 1])
    require(bool(np.isfinite(elevations).all()), "A screening line crosses invalid terrain.")
    return (
        LineString([(float(x), float(y), float(z)) for (x, y), z in zip(coordinates, elevations)]),
        distances,
        elevations,
    )


def axis_profile_metrics(distances: np.ndarray, elevations: np.ndarray, target_elevation_m: float) -> dict[str, Any]:
    residual = np.abs(np.asarray(elevations, dtype=float) - float(target_elevation_m))
    delta_distance = np.diff(np.asarray(distances, dtype=float))
    grade = np.divide(
        np.abs(np.diff(np.asarray(elevations, dtype=float))),
        delta_distance,
        out=np.zeros_like(delta_distance),
        where=delta_distance > 0,
    ) * 100.0
    return {
        "elevation_residual_p95_m": rounded(np.percentile(residual, 95) if residual.size else None),
        "grade_p95_pct": rounded(np.percentile(grade, 95) if grade.size else 0.0),
        "grade_max_pct": rounded(np.max(grade) if grade.size else 0.0),
    }


def build_ti_candidate(
    *,
    field: dict[str, Any],
    terrain: e0.Terrain,
    source_rows: list[dict[str, Any]],
    vertical_interval_m: float,
    offset_fraction: float,
    policy: ScreeningPolicy,
    profile_sample_step_m: float,
    general_constraints_status: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    field_id = str(field["code"])
    candidate_id = scenario_id(field_id, vertical_interval_m, offset_fraction)
    mask = terrain.raster_mask(field["usable"])
    row_slice, column_slice = e0.field_window(mask, padding=1)
    x_coordinates, y_coordinates = terrain.xy_grids(row_slice, column_slice)
    local_elevation = terrain.elevation[row_slice, column_slice]
    local_mask = mask[row_slice, column_slice]
    # GeoTIFF rows increase southward, while the deterministic contour helper
    # requires Cartesian coordinates in increasing order.
    if y_coordinates.size > 1 and y_coordinates[1] < y_coordinates[0]:
        y_coordinates = y_coordinates[::-1]
        local_elevation = local_elevation[::-1, :]
        local_mask = local_mask[::-1, :]
    raw_axes = extract_ti_sensitivity_axes(
        local_elevation,
        local_mask,
        x_coordinates,
        y_coordinates,
        field["usable"],
        vertical_interval_m=vertical_interval_m,
        offset_fraction=offset_fraction,
        minimum_axis_length_m=policy.minimum_axis_length_m,
    )

    axis_records: list[dict[str, Any]] = []
    partition_axes: list[LineString] = []
    unsupported_axis_count = 0
    extension_limit = max(terrain.resolution_x, terrain.resolution_y) * 4.0
    snap_tolerance = max(terrain.resolution_x, terrain.resolution_y) * 1.6
    for axis_index, item in enumerate(raw_axes, start=1):
        line = item["geometry"]
        if line.is_ring:
            endpoint_status = "CLOSED_ISOLINE_SCREENING"
        else:
            extended, supported = extend_line_to_physical_boundary(
                line,
                field["usable"],
                maximum_extension_m=extension_limit,
                snap_tolerance_m=snap_tolerance,
            )
            line = extended
            endpoint_status = "SUPPORTED_PHYSICAL_BOUNDARY" if supported else "UNSUPPORTED_SCREENING_FRAGMENT"
            unsupported_axis_count += int(not supported)
        line_3d, distances, elevations = drape_line(line, terrain, profile_sample_step_m)
        profile = axis_profile_metrics(distances, elevations, item["target_elevation_m"])
        terrace_id = f"{candidate_id}:AX{axis_index:04d}"
        axis_records.append(
            {
                "terrace_id": terrace_id,
                "candidate_id": candidate_id,
                "field_id": field_id,
                "variant": "EMBUTIDA_TI",
                "vertical_interval_m": float(vertical_interval_m),
                "offset_fraction": float(offset_fraction),
                "level_index": int(item["level_index"]),
                "target_elevation_m": float(item["target_elevation_m"]),
                "length_m": float(line.length),
                **profile,
                "endpoint_status": endpoint_status,
                "axis_status": "SCREENING_ONLY_NOT_DIMENSIONED",
                "pce_status": "NOT_EVALUATED",
                "pcx_status": "NOT_EVALUATED",
                "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
                "guidance_status": "NOT_AUTHORIZED",
                "blocker_codes": ";".join(SCREENING_BLOCKERS),
                "geometry": line_3d,
            }
        )
        partition_axes.append(line)

    strips, partition_qa = partition_interterrace_strips(
        field["usable"],
        partition_axes,
        topology_gap_half_width_m=policy.topology_gap_half_width_m,
        minimum_strip_area_m2=policy.minimum_strip_area_m2,
    )
    strip_records = [
        {
            "strip_id": f"{candidate_id}:ST{strip_index:04d}",
            "candidate_id": candidate_id,
            "field_id": field_id,
            "variant": "EMBUTIDA_TI",
            "vertical_interval_m": float(vertical_interval_m),
            "offset_fraction": float(offset_fraction),
            "area_ha": float(strip.area / 10_000.0),
            "partition_status": "TOPOLOGY_ONLY_NOT_SECTION",
            "section_status": "NOT_EVALUATED",
            "blocker_codes": ";".join(SCREENING_BLOCKERS),
            "geometry": strip,
        }
        for strip_index, strip in enumerate(strips, start=1)
    ]

    raw_segments = clip_rows_to_strips(
        source_rows,
        strips,
        minimum_segment_length_m=policy.minimum_row_segment_length_m,
    )
    row_records: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(raw_segments, start=1):
        line_3d, _, _ = drape_line(segment["geometry"], terrain, profile_sample_step_m)
        strip_id = f"{candidate_id}:ST{segment['strip_index']:04d}"
        row_records.append(
            {
                "segment_id": f"{candidate_id}:RW{segment_index:06d}",
                "candidate_id": candidate_id,
                "field_id": field_id,
                "strip_id": strip_id,
                "source_row_id": segment["source_row_id"],
                "source_family_id": segment["source_family_id"],
                "source_candidate_id": "CF0C_OPERACAO",
                "length_m": float(segment["length_m"]),
                "diagnostic_status": "NOT_APPROVED",
                "guidance_status": "NOT_AUTHORIZED",
                "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
                "blocker_codes": ";".join(SCREENING_BLOCKERS),
                "geometry": line_3d,
            }
        )

    source_part_counts = Counter(record["source_row_id"] for record in row_records)
    segment_metrics = segment_length_metrics(row_records)
    blockers = list(SCREENING_BLOCKERS)
    if unsupported_axis_count:
        blockers.append("INTERNAL_AXIS_ENDPOINT_SCREENING_FRAGMENT")
    if general_constraints_status != "COMPLETE":
        blockers.append("GENERAL_CONSTRAINT_INVENTORY_NOT_REVIEWED")
    if not axis_records:
        blockers.append("NO_TERRAIN_ISOLINE_AT_SENSITIVITY_LEVELS")
    if not row_records:
        blockers.append("NO_DIAGNOSTIC_ROW_SEGMENTS_AFTER_PARTITION")

    metrics = {
        "terrace_total_length_m": rounded(sum(item["length_m"] for item in axis_records)),
        "topology_gap_area_m2": rounded(partition_qa["topology_gap_area_m2"]),
        "partitioned_area_m2": rounded(partition_qa["partitioned_area_m2"]),
        "source_cf0c_row_count": len(source_rows),
        "source_cf0c_total_length_m": rounded(sum(item["length_m"] for item in source_rows)),
        "diagnostic_segment_count": segment_metrics["segment_count"],
        "diagnostic_total_length_m": rounded(segment_metrics["total_length_m"]),
        "diagnostic_length_p05_m": rounded(segment_metrics["length_p05_m"]),
        "diagnostic_length_p50_m": rounded(segment_metrics["length_p50_m"]),
        "diagnostic_length_p95_m": rounded(segment_metrics["length_p95_m"]),
        "diagnostic_length_max_m": rounded(segment_metrics["length_max_m"]),
        "split_source_row_count": sum(count > 1 for count in source_part_counts.values()),
    }
    partition_manifest = {
        key: rounded(value) if isinstance(value, float) else value
        for key, value in partition_qa.items()
    }
    candidate = {
        "candidate_id": candidate_id,
        "field_id": field_id,
        "variant": "EMBUTIDA_TI",
        "vertical_interval_m": float(vertical_interval_m),
        "offset_fraction": float(offset_fraction),
        "screening_status": "GEOMETRIC_PRECURSOR" if axis_records and strips else "NO_GEOMETRIC_PRECURSOR",
        "eligibility_status": "GEOMETRIC_PRECURSOR",
        "axis_count": len(axis_records),
        "strip_count": len(strip_records),
        "diagnostic_row_segment_count": len(row_records),
        "partition_qa": partition_manifest,
        "metrics": metrics,
        "pce_status": "NOT_EVALUATED",
        "pcx_status": "NOT_EVALUATED",
        "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
        "construction_status": "NOT_EVALUATED",
        "operational_status": "DIAGNOSTIC_ONLY_NOT_ROUTED",
        "guidance_status": "NOT_AUTHORIZED",
        "blocker_codes": sorted(set(blockers)),
    }
    summary = {
        **{key: candidate[key] for key in (
            "candidate_id", "field_id", "variant", "vertical_interval_m", "offset_fraction",
            "screening_status", "axis_count", "strip_count", "diagnostic_row_segment_count",
            "pce_status", "pcx_status", "hydraulic_status", "construction_status",
            "operational_status", "guidance_status",
        )},
        "terrace_total_length_m": metrics["terrace_total_length_m"],
        "diagnostic_total_length_m": metrics["diagnostic_total_length_m"],
        "diagnostic_length_p50_m": metrics["diagnostic_length_p50_m"],
        "diagnostic_length_p95_m": metrics["diagnostic_length_p95_m"],
        "split_source_row_count": metrics["split_source_row_count"],
        "blocker_codes": ";".join(candidate["blocker_codes"]),
    }
    return candidate, axis_records, strip_records, row_records, summary


def td_not_generated_record(field_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate_id = f"C1E0_TD_{field_id}_NOT_GENERATED"
    record = {
        "candidate_id": candidate_id,
        "field_id": str(field_id),
        "variant": "EMBUTIDA_TD",
        "status": "NOT_GENERATED_RECEIVER_MISSING",
        "geometry_count": 0,
        "blocker_codes": list(TD_BLOCKERS),
    }
    summary = {
        "candidate_id": candidate_id,
        "field_id": str(field_id),
        "variant": "EMBUTIDA_TD",
        "vertical_interval_m": None,
        "offset_fraction": None,
        "screening_status": "NOT_GENERATED_RECEIVER_MISSING",
        "axis_count": 0,
        "strip_count": 0,
        "diagnostic_row_segment_count": 0,
        "terrace_total_length_m": 0.0,
        "diagnostic_total_length_m": 0.0,
        "diagnostic_length_p50_m": None,
        "diagnostic_length_p95_m": None,
        "split_source_row_count": 0,
        "pce_status": "NOT_EVALUATED",
        "pcx_status": "NOT_EVALUATED",
        "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
        "construction_status": "NOT_EVALUATED",
        "operational_status": "NOT_EVALUATED",
        "guidance_status": "NOT_AUTHORIZED",
        "blocker_codes": ";".join(TD_BLOCKERS),
    }
    return record, summary


def create_fields(layer: ogr.Layer, definitions: list[tuple[str, int]]) -> None:
    for name, field_type in definitions:
        layer.CreateField(ogr.FieldDefn(name, field_type))


def set_fields(feature: ogr.Feature, record: dict[str, Any], names: Iterable[str]) -> None:
    for name in names:
        value = record.get(name)
        if value is not None:
            feature.SetField(name, value)


def write_geopackage(
    path: Path,
    projection: str,
    axes: list[dict[str, Any]],
    strips: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
) -> None:
    if path.exists():
        path.unlink()
    driver = ogr.GetDriverByName("GPKG")
    datasource = driver.CreateDataSource(str(path))
    require(datasource is not None, f"Could not create GeoPackage: {path}")
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromWkt(projection)

    axis_fields = [
        ("terrace_id", ogr.OFTString), ("candidate_id", ogr.OFTString), ("field_id", ogr.OFTString),
        ("variant", ogr.OFTString), ("vertical_interval_m", ogr.OFTReal), ("offset_fraction", ogr.OFTReal),
        ("level_index", ogr.OFTInteger), ("target_elevation_m", ogr.OFTReal), ("length_m", ogr.OFTReal),
        ("elevation_residual_p95_m", ogr.OFTReal), ("grade_p95_pct", ogr.OFTReal), ("grade_max_pct", ogr.OFTReal),
        ("endpoint_status", ogr.OFTString), ("axis_status", ogr.OFTString), ("pce_status", ogr.OFTString),
        ("pcx_status", ogr.OFTString), ("hydraulic_status", ogr.OFTString), ("guidance_status", ogr.OFTString),
        ("blocker_codes", ogr.OFTString),
    ]
    axis_layer = datasource.CreateLayer("terrace_alignment_candidates", srs=spatial_ref, geom_type=ogr.wkbLineString25D)
    create_fields(axis_layer, axis_fields)
    for record in axes:
        feature = ogr.Feature(axis_layer.GetLayerDefn())
        set_fields(feature, record, (name for name, _ in axis_fields))
        feature.SetGeometry(ogr.CreateGeometryFromWkb(record["geometry"].wkb))
        axis_layer.CreateFeature(feature)

    strip_fields = [
        ("strip_id", ogr.OFTString), ("candidate_id", ogr.OFTString), ("field_id", ogr.OFTString),
        ("variant", ogr.OFTString), ("vertical_interval_m", ogr.OFTReal), ("offset_fraction", ogr.OFTReal),
        ("area_ha", ogr.OFTReal), ("partition_status", ogr.OFTString), ("section_status", ogr.OFTString),
        ("blocker_codes", ogr.OFTString),
    ]
    strip_layer = datasource.CreateLayer("interterrace_strips", srs=spatial_ref, geom_type=ogr.wkbPolygon)
    create_fields(strip_layer, strip_fields)
    for record in strips:
        feature = ogr.Feature(strip_layer.GetLayerDefn())
        set_fields(feature, record, (name for name, _ in strip_fields))
        feature.SetGeometry(ogr.CreateGeometryFromWkb(record["geometry"].wkb))
        strip_layer.CreateFeature(feature)

    row_fields = [
        ("segment_id", ogr.OFTString), ("candidate_id", ogr.OFTString), ("field_id", ogr.OFTString),
        ("variant", ogr.OFTString),
        ("strip_id", ogr.OFTString), ("source_row_id", ogr.OFTString), ("source_family_id", ogr.OFTString),
        ("source_candidate_id", ogr.OFTString), ("length_m", ogr.OFTReal), ("diagnostic_status", ogr.OFTString),
        ("guidance_status", ogr.OFTString), ("hydraulic_status", ogr.OFTString), ("blocker_codes", ogr.OFTString),
    ]
    row_layer = datasource.CreateLayer("row_candidates", srs=spatial_ref, geom_type=ogr.wkbLineString25D)
    create_fields(row_layer, row_fields)
    for record in rows:
        feature = ogr.Feature(row_layer.GetLayerDefn())
        set_fields(feature, record, (name for name, _ in row_fields))
        feature.SetGeometry(ogr.CreateGeometryFromWkb(record["geometry"].wkb))
        row_layer.CreateFeature(feature)

    summary_fields = [
        ("candidate_id", ogr.OFTString), ("field_id", ogr.OFTString), ("variant", ogr.OFTString),
        ("vertical_interval_m", ogr.OFTReal), ("offset_fraction", ogr.OFTReal), ("screening_status", ogr.OFTString),
        ("axis_count", ogr.OFTInteger), ("strip_count", ogr.OFTInteger), ("diagnostic_row_segment_count", ogr.OFTInteger),
        ("terrace_total_length_m", ogr.OFTReal), ("diagnostic_total_length_m", ogr.OFTReal),
        ("diagnostic_length_p50_m", ogr.OFTReal), ("diagnostic_length_p95_m", ogr.OFTReal),
        ("split_source_row_count", ogr.OFTInteger), ("pce_status", ogr.OFTString), ("pcx_status", ogr.OFTString),
        ("hydraulic_status", ogr.OFTString), ("construction_status", ogr.OFTString),
        ("operational_status", ogr.OFTString), ("guidance_status", ogr.OFTString), ("blocker_codes", ogr.OFTString),
    ]
    summary_layer = datasource.CreateLayer("candidate_summary", geom_type=ogr.wkbNone)
    create_fields(summary_layer, summary_fields)
    for record in summaries:
        feature = ogr.Feature(summary_layer.GetLayerDefn())
        set_fields(feature, record, (name for name, _ in summary_fields))
        summary_layer.CreateFeature(feature)
    datasource = None


def geometry_has_finite_z(geometry: ogr.Geometry) -> bool:
    if geometry is None or geometry.IsEmpty() or geometry.GetCoordinateDimension() < 3:
        return False
    flattened = ogr.GT_Flatten(geometry.GetGeometryType())
    if flattened == ogr.wkbLineString:
        return all(len(point) >= 3 and all(math.isfinite(float(value)) for value in point[:3]) for point in geometry.GetPoints())
    return all(geometry_has_finite_z(geometry.GetGeometryRef(index)) for index in range(geometry.GetGeometryCount()))


def validate_staged_geopackage(path: Path, expected_counts: dict[str, int]) -> dict[str, int]:
    datasource = ogr.Open(str(path), 0)
    require(datasource is not None, "Could not reopen staged C1 screening GeoPackage.")
    names = {datasource.GetLayerByIndex(index).GetName() for index in range(datasource.GetLayerCount())}
    require(names == set(expected_counts), f"Unexpected C1 screening layer set: {sorted(names)}")
    invalid = 0
    non_3d = 0
    forbidden_td = 0
    hydraulic_pass = 0
    guidance_authorized = 0
    for layer_name, expected_count in expected_counts.items():
        layer = datasource.GetLayerByName(layer_name)
        require(layer.GetFeatureCount() == expected_count, f"{layer_name} count mismatch.")
        for feature in layer:
            geometry = feature.GetGeometryRef()
            if geometry is not None:
                invalid += int(geometry.IsEmpty() or not bool(geometry.IsValid()))
                if layer_name in {"terrace_alignment_candidates", "row_candidates"}:
                    non_3d += int(not geometry_has_finite_z(geometry))
            variant_index = feature.GetFieldIndex("variant")
            if layer_name != "candidate_summary" and variant_index >= 0:
                forbidden_td += int(str(feature.GetField("variant")) == "EMBUTIDA_TD")
            hydraulic_index = feature.GetFieldIndex("hydraulic_status")
            if hydraulic_index >= 0:
                hydraulic_pass += int(str(feature.GetField("hydraulic_status")) != "HYDRAULIC_UNCONFIRMED")
            guidance_index = feature.GetFieldIndex("guidance_status")
            if guidance_index >= 0:
                guidance_authorized += int(str(feature.GetField("guidance_status")) != "NOT_AUTHORIZED")
    datasource = None
    require(invalid == 0, "Staged C1 screening GeoPackage contains invalid geometry.")
    require(non_3d == 0, "Staged C1 screening line layers must be finite 3D.")
    require(forbidden_td == 0, "TD geometry was generated without a receiver.")
    require(hydraulic_pass == 0, "A forbidden hydraulic claim was persisted.")
    require(guidance_authorized == 0, "A forbidden guidance claim was persisted.")
    return {
        "invalid_geometry_count": invalid,
        "non_3d_line_count": non_3d,
        "forbidden_td_geometry_count": forbidden_td,
        "hydraulic_pass_claim_count": hydraulic_pass,
        "guidance_authorized_claim_count": guidance_authorized,
    }


def plot_polygon(axis: plt.Axes, geometry: Any, **style: Any) -> None:
    for polygon in iter_polygons(geometry):
        coordinates = np.asarray(polygon.exterior.coords)
        axis.plot(coordinates[:, 0], coordinates[:, 1], **style)
        for interior in polygon.interiors:
            coordinates = np.asarray(interior.coords)
            axis.plot(coordinates[:, 0], coordinates[:, 1], **style)


def render_map(
    path: Path,
    fields: list[dict[str, Any]],
    terrain: e0.Terrain,
    intervals: tuple[float, ...],
    map_offset_fraction: float,
    axes: list[dict[str, Any]],
    rows: list[dict[str, Any]],
) -> None:
    union = fields[0]["usable"]
    for field in fields[1:]:
        union = union.union(field["usable"])
    mask = terrain.raster_mask(union)
    row_slice, column_slice = e0.field_window(mask, padding=5)
    elevation = terrain.elevation[row_slice, column_slice]
    x_coordinates, y_coordinates = terrain.xy_grids(row_slice, column_slice)
    extent = [
        float(min(x_coordinates[0], x_coordinates[-1])),
        float(max(x_coordinates[0], x_coordinates[-1])),
        float(min(y_coordinates[0], y_coordinates[-1])),
        float(max(y_coordinates[0], y_coordinates[-1])),
    ]
    hillshade = LightSource(azdeg=315, altdeg=40).hillshade(
        np.nan_to_num(elevation, nan=float(np.nanmedian(elevation))),
        vert_exag=1.2,
        dx=terrain.resolution_x,
        dy=terrain.resolution_y,
    )

    figure, panels = plt.subplots(1, len(intervals), figsize=(6.0 * len(intervals), 7.6), dpi=150)
    if len(intervals) == 1:
        panels = [panels]
    for panel, interval in zip(panels, intervals):
        panel.imshow(hillshade, extent=extent, origin="upper", cmap="gray", alpha=0.78)
        candidate_ids = {
            scenario_id(str(field["code"]), interval, map_offset_fraction) for field in fields
        }
        for record in rows:
            if record["candidate_id"] not in candidate_ids:
                continue
            coordinates = np.asarray(record["geometry"].coords)
            panel.plot(coordinates[:, 0], coordinates[:, 1], color="#1a8f57", linewidth=0.24, alpha=0.48)
        for record in axes:
            if record["candidate_id"] not in candidate_ids:
                continue
            coordinates = np.asarray(record["geometry"].coords)
            panel.plot(coordinates[:, 0], coordinates[:, 1], color="#d52a87", linewidth=1.05, alpha=0.95)
        for field in fields:
            plot_polygon(panel, field["usable"], color="#f2c84b", linewidth=1.15)
            point = field["usable"].representative_point()
            panel.text(point.x, point.y, str(field["code"]), fontsize=8, weight="bold", color="#101914")
        panel.set_title(
            f"Amostragem de isolinhas {interval:g} m | offset {map_offset_fraction:.2f}",
            fontsize=10,
            weight="bold",
        )
        panel.set_aspect("equal")
        panel.set_axis_off()
    figure.suptitle("C1 E0 | sensibilidade geometrica de alinhamentos embutidos", fontsize=15, weight="bold", y=0.98)
    figure.text(
        0.5,
        0.025,
        "Magenta: isolinhas de amostragem, nao terracos dimensionados. Verde: recortes diagnosticos CF0C. "
        "Grade 2/4/6 m sem significado PCE; acuracia vertical, PCE/PCX, secao, TI/TD e guiamento nao validados.",
        ha="center",
        va="bottom",
        fontsize=8.5,
        color="#7a1b1b",
        weight="bold",
    )
    figure.tight_layout(rect=[0.01, 0.06, 0.99, 0.95])
    figure.savefig(path, facecolor="#f2f4f1")
    plt.close(figure)


def build_manifest(
    *,
    request: ResolvedProjectRequest,
    screening_request_path: Path,
    screening_payload: dict[str, Any],
    policy: ScreeningPolicy,
    projection: str,
    dtm_path: Path,
    boundary_path: Path,
    cf0_manifest_path: Path,
    cf0_gpkg_path: Path,
    candidates: list[dict[str, Any]],
    td_records: list[dict[str, Any]],
    layer_counts: dict[str, int],
    geometry_qa: dict[str, int],
    staged_gpkg: Path,
    staged_map: Path,
    output_gpkg: Path,
    output_map: Path,
) -> dict[str, Any]:
    general_status = request.request["constraint_inventory"].get("general_review_status", "NOT_REVIEWED")
    power_status = request.power_inventory_status
    power_effect = {
        "DECLARED_NONE": "NOT_APPLICABLE_DECLARED_NONE",
        "PROVIDED": "BLOCKED_REQUIRES_BARRIER_STAGE",
        "NOT_REVIEWED": "PENDING_NOT_REVIEWED",
    }[power_status]
    stage_status = (
        "SCREENING_ONLY_PCE_PCX_UNCONFIRMED"
        if any(candidate["screening_status"] == "GEOMETRIC_PRECURSOR" for candidate in candidates)
        else "NO_GEOMETRIC_PRECURSOR"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_type": MANIFEST_TYPE,
        "release": RELEASE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_request_ref": {
            "id": request.request["request_id"],
            "path": display_path(request.request_path),
            "sha256": sha256_file(request.request_path),
        },
        "sensitivity_request_ref": {
            "id": screening_payload["screening_id"],
            "path": display_path(screening_request_path),
            "sha256": sha256_file(screening_request_path),
        },
        "stage_status": stage_status,
        "crs": spatial_reference_manifest(projection),
        "inputs": {
            "terrain_dtm": file_record(dtm_path, "DTM_E0_SCREENING"),
            "field_boundary": file_record(boundary_path, "FIELD_BOUNDARY"),
            "cf0_manifest": file_record(cf0_manifest_path, "CF0_STAGE_MANIFEST"),
            "cf0_geopackage": file_record(cf0_gpkg_path, "CF0_VECTOR_SOURCE"),
        },
        "assumptions": {
            "parameter_class": "E0_ASSUMPTION",
            "selection_meaning": "GEOMETRIC_SENSITIVITY_GRID_ONLY",
            "selection_rule": screening_payload["assumption_contract"]["selection_rule"],
            "interval_role": screening_payload["assumption_contract"]["interval_role"],
            "vertical_accuracy_status": screening_payload["assumption_contract"]["vertical_accuracy_status"],
            "vertical_interval_candidates_m": list(policy.vertical_interval_candidates_m),
            "offset_fractions": list(policy.offset_fractions),
            "topology_gap_half_width_m": policy.topology_gap_half_width_m,
            "topology_gap_role": "NUMERICAL_PARTITION_SUPPORT_NOT_SECTION_WIDTH",
            "row_source_candidate_id": screening_payload["row_source_policy"]["source_candidate_id"],
            "row_source_method": screening_payload["row_source_policy"]["method"],
        },
        "variant_status": {
            "EMBUTIDA_TI": {
                "status": "GENERATED_SCREENING_ONLY_NOT_DIMENSIONED",
                "geometry_role": "TERRAIN_ISOLINE_SENSITIVITY_NOT_APPROVED_TI",
                "pce_status": "NOT_EVALUATED",
                "pcx_status": "NOT_EVALUATED",
                "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
            },
            "EMBUTIDA_TD": {
                "status": "NOT_GENERATED_RECEIVER_MISSING",
                "reason": "VERIFIED_RECEIVER_AND_LONGITUDINAL_GRADE_RULE_REQUIRED",
                "geometry_count": 0,
                "pce_status": "NOT_EVALUATED",
                "pcx_status": "NOT_EVALUATED",
                "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
            },
        },
        "release_limitations": RELEASE_LIMITATIONS,
        "candidates": candidates,
        "td_not_generated": td_records,
        "qa": {
            "field_count": len({candidate["field_id"] for candidate in candidates}),
            "ti_candidate_count": len(candidates),
            "td_not_generated_count": len(td_records),
            "axis_count": layer_counts["terrace_alignment_candidates"],
            "strip_count": layer_counts["interterrace_strips"],
            "diagnostic_row_segment_count": layer_counts["row_candidates"],
            **geometry_qa,
            "general_constraint_inventory_status": general_status,
            "power_inventory_status": power_status,
            "power_constraint_effect": power_effect,
        },
        "layer_counts": layer_counts,
        "outputs": {
            "geopackage": file_record(staged_gpkg, "C1_E0_SCREENING_VECTOR_PACKAGE", published_path=output_gpkg),
            "map": file_record(staged_map, "C1_E0_SCREENING_MAP", published_path=output_map),
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screening-request", type=Path, default=DEFAULT_SCREENING_REQUEST)
    parser.add_argument("--field-id-column")
    parser.add_argument("--output-gpkg", type=Path, default=DEFAULT_GPKG)
    parser.add_argument("--output-manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-map", type=Path, default=DEFAULT_MAP)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    screening_request_path = args.screening_request.resolve()
    try:
        require(screening_request_path.is_file(), f"Screening request does not exist: {screening_request_path}")
        screening_payload = json.loads(screening_request_path.read_text(encoding="utf-8"))
        policy = validate_screening_request(screening_payload)
        project_request_path = resolve_reference(screening_payload["project_request_ref"], screening_request_path)
        cf0_manifest_path = resolve_reference(screening_payload["cf0_manifest_ref"], screening_request_path)
        request = load_project_request(project_request_path)
        require(request.requested_delivery_level == "E0_TRIAGEM", "This stage is limited to E0_TRIAGEM.")
        require(request.power_inventory_status != "PROVIDED", "POWER_BARRIER_STAGE_REQUIRED before C1 screening.")
        require(not request.request.get("constraint_layers"), "CONSTRAINT_LAYER_STAGE_REQUIRED before C1 screening.")
        require(not request.request["scope"].get("cross_field_generation", False), "CROSS_FIELD_C1_SCREENING_NOT_IMPLEMENTED.")
        require(cf0_manifest_path.is_file(), f"CF0 manifest does not exist: {cf0_manifest_path}")
        cf0_manifest = json.loads(cf0_manifest_path.read_text(encoding="utf-8"))
        require(cf0_manifest.get("release") == "CF0_GEOMETRIC_SCREENING", "Unexpected CF0 input release.")
        require(
            cf0_manifest.get("project_request_ref", {}).get("sha256") == sha256_file(project_request_path),
            "CF0 manifest was not generated from the configured compiled project request.",
        )
        cf0_gpkg_path = resolve_reference(cf0_manifest["outputs"]["geopackage"]["path"], cf0_manifest_path)
        require(cf0_gpkg_path.is_file(), "CF0 GeoPackage does not exist.")
        require(
            cf0_manifest["outputs"]["geopackage"]["sha256"] == sha256_file(cf0_gpkg_path),
            "CF0 GeoPackage hash differs from its manifest.",
        )

        e0.PARAMS = replace(e0.Parameters(), **request.engine_parameter_overrides())
        dtm_path, _ = request.dtm_path()
        boundary_path, _ = request.field_boundary_path()
        boundary_dataset = next(item for item in request.request["input_datasets"] if item["role"] == "FIELD_BOUNDARY")
        fields, projection, _ = e0.read_fields(
            boundary_path=boundary_path,
            boundary_layer=boundary_dataset.get("layer_name"),
            target_field_ids=[str(item) for item in request.request["scope"]["field_ids"]],
            field_id_column=args.field_id_column or boundary_dataset.get("id_field"),
            return_metadata=True,
        )
        e0.validate_spatial_references(projection, dtm_path, request.value("terrain.horizontal_crs"))
        terrain = e0.Terrain(dtm_path)
        for field in fields:
            terrain.validate_geometry_coverage(field["usable"])
        source_rows = load_cf0_rows(
            cf0_gpkg_path,
            screening_payload["row_source_policy"]["source_candidate_id"],
            {str(field["code"]) for field in fields},
        )
    except (ContractError, RuntimeError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"C1_E0_INPUT_BLOCKED: {exc}", file=sys.stderr)
        return 2

    DERIVED.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(tempfile.mkdtemp(prefix=".embedded-terrace-screening-", dir=DERIVED))
    staged_gpkg = temporary_root / args.output_gpkg.name
    staged_manifest = temporary_root / args.output_manifest.name
    staged_map = temporary_root / args.output_map.name
    axes: list[dict[str, Any]] = []
    strips: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    td_records: list[dict[str, Any]] = []
    try:
        profile_step = float(screening_payload["screening_parameters"]["profile_sample_step_m"])
        general_status = request.request["constraint_inventory"].get("general_review_status", "NOT_REVIEWED")
        for field in fields:
            field_id = str(field["code"])
            for interval in policy.vertical_interval_candidates_m:
                for offset in policy.offset_fractions:
                    candidate, candidate_axes, candidate_strips, candidate_rows, summary = build_ti_candidate(
                        field=field,
                        terrain=terrain,
                        source_rows=source_rows[field_id],
                        vertical_interval_m=interval,
                        offset_fraction=offset,
                        policy=policy,
                        profile_sample_step_m=profile_step,
                        general_constraints_status=general_status,
                    )
                    candidates.append(candidate)
                    axes.extend(candidate_axes)
                    strips.extend(candidate_strips)
                    rows.extend(candidate_rows)
                    summaries.append(summary)
                    print(
                        json.dumps(
                            {
                                "event": "C1_E0_TI_SCREENING_CANDIDATE_COMPLETE",
                                "candidate_id": candidate["candidate_id"],
                                "axis_count": candidate["axis_count"],
                                "strip_count": candidate["strip_count"],
                                "diagnostic_row_segment_count": candidate["diagnostic_row_segment_count"],
                                "status": candidate["screening_status"],
                            },
                            ensure_ascii=True,
                        ),
                        flush=True,
                    )
            td_record, td_summary = td_not_generated_record(field_id)
            td_records.append(td_record)
            summaries.append(td_summary)

        write_geopackage(staged_gpkg, projection, axes, strips, rows, summaries)
        render_map(
            staged_map,
            fields,
            terrain,
            policy.vertical_interval_candidates_m,
            float(screening_payload["screening_parameters"]["map_offset_fraction"]),
            axes,
            rows,
        )
        layer_counts = {
            "terrace_alignment_candidates": len(axes),
            "interterrace_strips": len(strips),
            "row_candidates": len(rows),
            "candidate_summary": len(summaries),
        }
        geometry_qa = validate_staged_geopackage(staged_gpkg, layer_counts)
        manifest = build_manifest(
            request=request,
            screening_request_path=screening_request_path,
            screening_payload=screening_payload,
            policy=policy,
            projection=projection,
            dtm_path=dtm_path,
            boundary_path=boundary_path,
            cf0_manifest_path=cf0_manifest_path,
            cf0_gpkg_path=cf0_gpkg_path,
            candidates=candidates,
            td_records=td_records,
            layer_counts=layer_counts,
            geometry_qa=geometry_qa,
            staged_gpkg=staged_gpkg,
            staged_map=staged_map,
            output_gpkg=args.output_gpkg,
            output_map=args.output_map,
        )
        staged_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        for output in (args.output_gpkg, args.output_map, args.output_manifest):
            output.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_gpkg, args.output_gpkg)
        os.replace(staged_map, args.output_map)
        os.replace(staged_manifest, args.output_manifest)
    except Exception as exc:
        print(f"C1_E0_GENERATION_FAILED: {exc}", file=sys.stderr)
        return 3
    finally:
        terrain.dataset = None
        shutil.rmtree(temporary_root, ignore_errors=True)

    print(
        json.dumps(
            {
                "status": manifest["stage_status"],
                "release": RELEASE,
                "ti_candidate_count": len(candidates),
                "td_status": "NOT_GENERATED_RECEIVER_MISSING",
                "axis_count": len(axes),
                "strip_count": len(strips),
                "diagnostic_row_segment_count": len(rows),
                "manifest": display_path(args.output_manifest),
                "geopackage": display_path(args.output_gpkg),
                "map": display_path(args.output_map),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
