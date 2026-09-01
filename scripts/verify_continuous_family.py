"""Verify the independent CF0 continuous-family result package.

Run with the QGIS Python environment::

    & 'C:\\Program Files\\QGIS 3.32.1\\bin\\python-qgis.bat' `
      scripts/verify_continuous_family.py

CF0 is a geometric screening stage. This verifier deliberately rejects any
hydraulic approval claim, even when every hydraulic input happens to be present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from osgeo import gdal, ogr, osr
from scipy import ndimage
from shapely import wkb
from shapely.geometry import GeometryCollection, LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

try:  # Optional in the bundled QGIS Python; imperative validation remains mandatory.
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # pragma: no cover - current QGIS distribution
    Draft202012Validator = None


gdal.UseExceptions()
ogr.UseExceptions()

REPO = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO / "dataset" / "derived" / "continuous_family_manifest.json"
DEFAULT_SCHEMA = REPO / "schemas" / "continuous-family-stage.schema.json"

SCHEMA_VERSION = "1.2.0"
MANIFEST_TYPE = "CONTINUOUS_FAMILY_STAGE_RESULT"
RELEASE = "CF0_GEOMETRIC_SCREENING"
EXPECTED_CANDIDATES = {
    "CF0A_CONSERVACAO",
    "CF0B_EQUILIBRIO",
    "CF0C_OPERACAO",
}
EXPECTED_LAYERS = {
    "continuous_rows",
    "diagnostic_rows",
    "family_summary",
    "hydraulic_precheck",
}
HYDRAULIC_INPUT_FIELDS = (
    "idf_status",
    "soil_status",
    "contributing_area_status",
    "outlet_status",
    "receiver_status",
    "section_status",
    "roughness_status",
    "downstream_status",
)
HYDRAULIC_INPUT_STATES = {"PROVIDED", "MISSING", "UNCONFIRMED"}
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
PHASE_OFFSET_FRACTIONS = (0.0, 0.25, 0.5, 0.75)
PHASE_LEVEL_EQUATION = (
    "phase_level_m = phase_level_index * row_spacing_m + phase_offset_m"
)
PHASE_OFFSET_SELECTION_RULE = (
    "PASS_ONLY_MAX_MIN_RADIUS_MIN_SPACING_OUTSIDE_MIN_SPACING_P95_"
    "MIN_COVERAGE_ERROR_MIN_OFFSET"
)
PHASE_OFFSET_SELECTION_KEYS = [
    "MAXIMUM_MINIMUM_WORK_PATH_RADIUS_M",
    "MINIMUM_FINAL_SPACING_OUTSIDE_TOLERANCE_FRACTION",
    "MINIMUM_SPACING_ERROR_P95_M",
    "MINIMUM_ABSOLUTE_COVERAGE_PROXY_ERROR_FROM_1",
    "MINIMUM_PHASE_OFFSET_FRACTION",
]

ROW_FIELDS = {
    "row_id",
    "family_id",
    "candidate_id",
    "field_id",
    "work_block_id",
    "row_index",
    "phase_level_index",
    "phase_level_m",
    "phase_offset_m",
    "phase_offset_fraction",
    "length_m",
    "min_radius_m",
    "max_abs_grade_pct",
    "grade_p95_pct",
    "reversal_count",
    "start_surface",
    "end_surface",
    "geometry_status",
    "hydraulic_status",
    "topology_status",
    "blocker_codes",
}
DIAGNOSTIC_FIELDS = {
    *ROW_FIELDS,
    "diagnostic_status",
    "guidance_status",
}
SUMMARY_FIELDS = {
    "candidate_id",
    "field_id",
    "work_block_id",
    "family_id",
    "phase_offset_m",
    "phase_offset_fraction",
    "phase_offset_role",
    "phase_offset_selection_status",
    "geometry_status",
    "hydraulic_status",
    "row_count",
    "total_length_m",
    "diagnostic_row_count",
    "diagnostic_total_length_m",
    "spacing_p95_m",
    "eikonal_p95",
    "integrability_p95",
    "orientation_p95_deg",
    "min_radius_m",
    "radius_status",
    "max_grade_pct",
    "internal_endpoints",
    "intersections",
    "self_intersections",
    "loops",
    "blocker_codes",
}
HYDRAULIC_FIELDS = {
    "candidate_id",
    "field_id",
    "work_block_id",
    "family_id",
    "hydraulic_status",
    *HYDRAULIC_INPUT_FIELDS,
    "grade_p95_pct",
    "grade_max_pct",
    "reversal_count",
    "adverse_length_pct",
    "missing_inputs",
    "blocker_codes",
}

SOLVER_GATE_METRIC_FIELDS = {
    "solve_mask_component_count",
    "low_coherence_fraction",
    "abrupt_edge_fraction",
    "cycle_conflict_fraction",
    "integrability_residual_rms",
    "critical_fraction",
    "singular_fraction",
    "cut_locus_fraction",
    "solver_spacing_p05_m",
    "solver_spacing_p95_m",
    "solver_spacing_outside_tolerance_fraction",
    "final_spacing_outside_tolerance_fraction",
    "spline_failure_count",
}


class ContractError(RuntimeError):
    """Raised when the CF0 package violates its published contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def require_object(value: Any, label: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"{label} must be an object.")
    return value


def require_array(value: Any, label: str, *, nonempty: bool = False) -> list[Any]:
    require(isinstance(value, list), f"{label} must be an array.")
    if nonempty:
        require(bool(value), f"{label} must not be empty.")
    return value


def require_keys(
    node: dict[str, Any],
    label: str,
    required: Iterable[str],
    optional: Iterable[str] = (),
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    missing = sorted(required_set - set(node))
    extra = sorted(set(node) - allowed)
    require(not missing, f"{label} is missing keys: {missing}")
    require(not extra, f"{label} has unexpected keys: {extra}")


def finite_number(value: Any, label: str, *, minimum: float | None = None) -> float:
    require(
        isinstance(value, (int, float)) and not isinstance(value, bool),
        f"{label} must be numeric.",
    )
    result = float(value)
    require(math.isfinite(result), f"{label} must be finite.")
    if minimum is not None:
        require(result >= minimum, f"{label} must be >= {minimum}.")
    return result


def nullable_finite(value: Any, label: str, *, minimum: float = 0.0) -> float | None:
    if value is None:
        return None
    return finite_number(value, label, minimum=minimum)


def resolve_path(value: str, manifest_path: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    repo_candidate = (REPO / path).resolve()
    manifest_candidate = (manifest_path.parent / path).resolve()
    if repo_candidate.exists() or not manifest_candidate.exists():
        return repo_candidate
    return manifest_candidate


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_bundle_sha256(paths: list[Path]) -> str:
    """Reproduce project_request._dataset_bundle_sha256 exactly."""

    if len(paths) == 1:
        return sha256_file(paths[0])
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.lower().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
    return digest.hexdigest()


def validate_file_record(
    record: Any,
    label: str,
    manifest_path: Path,
    *,
    extra_keys: Iterable[str] = (),
) -> Path:
    item = require_object(record, label)
    require_keys(item, label, {"path", "size_bytes", "sha256"}, extra_keys)
    path_value = item["path"]
    require(isinstance(path_value, str) and path_value, f"{label}.path is invalid.")
    path = resolve_path(path_value, manifest_path)
    require(path.is_file(), f"{label} does not exist: {path}")
    size = item["size_bytes"]
    require(isinstance(size, int) and not isinstance(size, bool) and size > 0, f"{label}.size_bytes is invalid.")
    require(path.stat().st_size == size, f"{label} size mismatch: {path}")
    digest = item["sha256"]
    require(isinstance(digest, str) and SHA256_PATTERN.fullmatch(digest) is not None, f"{label}.sha256 is invalid.")
    require(sha256_file(path) == digest.lower(), f"{label} SHA-256 mismatch: {path}")
    return path


def validate_input_dataset(record: Any, label: str, manifest_path: Path) -> Path:
    item = require_object(record, label)
    require_keys(
        item,
        label,
        {"path", "integrity_scope", "source_bundle_sha256", "source_files"},
        {"layer", "role", "id_field", "field_ids"},
    )
    require(item["integrity_scope"] == "SOURCE_BUNDLE", f"{label}.integrity_scope must be SOURCE_BUNDLE.")
    main_path = resolve_path(item["path"], manifest_path)
    require(main_path.is_file(), f"{label}.path does not exist: {main_path}")
    source_records = require_array(item["source_files"], f"{label}.source_files", nonempty=True)
    source_paths = [
        validate_file_record(record, f"{label}.source_files[{index}]", manifest_path, extra_keys={"layer", "role"})
        for index, record in enumerate(source_records)
    ]
    require(
        any(path.resolve() == main_path.resolve() for path in source_paths),
        f"{label}.path is not a member of its declared source bundle.",
    )
    declared = item["source_bundle_sha256"]
    require(isinstance(declared, str) and SHA256_PATTERN.fullmatch(declared) is not None, f"{label}.source_bundle_sha256 is invalid.")
    require(
        source_bundle_sha256(source_paths) == declared.lower(),
        f"{label} source bundle SHA-256 mismatch.",
    )
    return main_path


def split_codes(value: Any) -> list[str]:
    if value is None:
        return []
    return [part.strip() for part in re.split(r"[;,|]", str(value)) if part.strip()]


def validate_manifest_shape(manifest: Any, manifest_path: Path) -> dict[str, Any]:
    node = require_object(manifest, "manifest")
    require_keys(
        node,
        "manifest",
        {
            "schema_version",
            "manifest_type",
            "release",
            "generated_at",
            "project_request_ref",
            "stage_status",
            "crs",
            "inputs",
            "constraints",
            "release_limitations",
            "domain_assembly",
            "solver_parameters",
            "candidates",
            "qa",
            "layer_counts",
            "outputs",
        },
    )
    require(node["schema_version"] == SCHEMA_VERSION, "Unsupported schema_version.")
    require(node["manifest_type"] == MANIFEST_TYPE, "Unexpected manifest_type.")
    require(node["release"] == RELEASE, "CF0 release declaration is missing.")
    require(node["stage_status"] in {"HYDRAULIC_UNCONFIRMED", "NO_FEASIBLE_FAMILY"}, "Invalid stage_status.")
    require(isinstance(node["generated_at"], str), "generated_at must be a string.")
    try:
        datetime.fromisoformat(node["generated_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("generated_at is not an ISO-8601 timestamp.") from exc

    crs = require_object(node["crs"], "crs")
    require_keys(crs, "crs", {"authority", "code", "projected", "linear_unit", "wkt_sha256"})
    require(crs["authority"] == "EPSG", "crs.authority must be EPSG.")
    require(isinstance(crs["code"], int) and crs["code"] > 0, "crs.code is invalid.")
    require(crs["projected"] is True, "CF0 requires a projected CRS.")
    require(str(crs["linear_unit"]).lower().startswith("met"), "CF0 CRS must use metres.")
    require(isinstance(crs["wkt_sha256"], str) and SHA256_PATTERN.fullmatch(crs["wkt_sha256"]) is not None, "crs.wkt_sha256 is invalid.")

    inputs = require_object(node["inputs"], "inputs")
    require_keys(
        inputs,
        "inputs",
        {"work_area", "terrain_dtm", "project_request"},
        {"operational_surfaces", "absolute_barriers"},
    )
    validate_input_dataset(inputs["work_area"], "inputs.work_area", manifest_path)
    validate_input_dataset(inputs["terrain_dtm"], "inputs.terrain_dtm", manifest_path)
    request_path = validate_file_record(
        inputs["project_request"],
        "inputs.project_request",
        manifest_path,
        extra_keys={"layer", "role"},
    )
    project_ref = require_object(node["project_request_ref"], "project_request_ref")
    require_keys(project_ref, "project_request_ref", {"id", "sha256"})
    require(isinstance(project_ref["id"], str) and project_ref["id"], "project_request_ref.id is invalid.")
    request_digest = sha256_file(request_path)
    require(str(project_ref["sha256"]).lower() == request_digest, "project_request_ref.sha256 disagrees with the resolved request file.")
    require(str(inputs["project_request"]["sha256"]).lower() == request_digest, "inputs.project_request.sha256 disagrees with the resolved request file.")
    try:
        request_document = json.loads(request_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ContractError("Resolved project request is not valid UTF-8 JSON.") from exc
    require(request_document.get("request_id") == project_ref["id"], "project_request_ref.id disagrees with the resolved request.")
    validate_source_crs_integrity(inputs, crs, manifest_path)
    if "operational_surfaces" in inputs:
        validate_input_dataset(inputs["operational_surfaces"], "inputs.operational_surfaces", manifest_path)
    for index, barrier in enumerate(inputs.get("absolute_barriers", [])):
        validate_input_dataset(barrier, f"inputs.absolute_barriers[{index}]", manifest_path)

    constraints = require_object(node["constraints"], "constraints")
    require_keys(
        constraints,
        "constraints",
        {"power_inventory_status", "power_barrier_application", "operational_surfaces_status", "absolute_barrier_count"},
    )
    power_expected = {
        "PROVIDED": "APPLIED",
        "DECLARED_NONE": "NOT_APPLICABLE_DECLARED_NONE",
        "NOT_REVIEWED": "NOT_APPLIED_NOT_REVIEWED",
    }
    require(constraints["power_inventory_status"] in power_expected, "Invalid power_inventory_status.")
    require(
        constraints["power_barrier_application"] == power_expected[constraints["power_inventory_status"]],
        "Power inventory and barrier application disagree.",
    )
    require(
        constraints["operational_surfaces_status"] in {"PROVIDED", "PARTIAL", "NOT_REVIEWED"},
        "Invalid operational_surfaces_status.",
    )
    declared_barriers = require_array(inputs.get("absolute_barriers", []), "inputs.absolute_barriers")
    require(
        isinstance(constraints["absolute_barrier_count"], int)
        and not isinstance(constraints["absolute_barrier_count"], bool)
        and constraints["absolute_barrier_count"] >= 0,
        "absolute_barrier_count must be a non-negative integer.",
    )
    require(
        constraints["absolute_barrier_count"] == len(declared_barriers),
        "absolute_barrier_count disagrees with inputs.absolute_barriers.",
    )
    if constraints["power_barrier_application"] == "APPLIED":
        require(bool(declared_barriers), "Applied power barrier has no declared absolute-barrier input.")

    limitations = require_array(node["release_limitations"], "release_limitations", nonempty=True)
    require(len(limitations) == len(set(limitations)), "release_limitations contains duplicates.")
    require("CF0_NOT_FOR_GUIDANCE" in limitations, "CF0_NOT_FOR_GUIDANCE limitation is mandatory.")
    require("HYDRAULIC_UNCONFIRMED" in limitations, "HYDRAULIC_UNCONFIRMED limitation is mandatory.")
    if constraints["power_inventory_status"] == "NOT_REVIEWED":
        require("POWER_INVENTORY_NOT_REVIEWED" in limitations, "Unreviewed power inventory is not disclosed.")

    domain = require_object(node["domain_assembly"], "domain_assembly")
    require_keys(
        domain,
        "domain_assembly",
        {
            "status",
            "cross_field_requested",
            "cadastral_boundaries_used_as_work_edges",
            "geometry_method",
            "outer_headland_m",
            "obstacle_clearance_m",
            "work_block_filter",
            "work_blocks",
            "excluded_components",
            "blocker_codes",
        },
    )
    require(
        domain["status"] in {
            "ONE_WORK_BLOCK_PER_FIELD",
            "CONNECTED_COMPONENTS_PER_FIELD",
            "CROSS_FIELD_DISSOLVED",
            "BLOCKED_CROSS_FIELD_UNCONFIRMED",
        },
        "Invalid domain_assembly.status.",
    )
    require(isinstance(domain["cross_field_requested"], bool), "cross_field_requested must be boolean.")
    require(isinstance(domain["cadastral_boundaries_used_as_work_edges"], bool), "cadastral boundary flag must be boolean.")
    require(domain["geometry_method"] == "BUFFERED_FIELD_BOUNDARY_V1", "Unsupported work-block geometry method.")
    finite_number(domain["outer_headland_m"], "domain_assembly.outer_headland_m", minimum=0)
    finite_number(domain["obstacle_clearance_m"], "domain_assembly.obstacle_clearance_m", minimum=0)
    work_block_filter = require_object(
        domain["work_block_filter"], "domain_assembly.work_block_filter"
    )
    require_keys(
        work_block_filter,
        "domain_assembly.work_block_filter",
        {
            "revision",
            "grid_cell_estimate_method",
            "minimum_grid_cell_count",
            "effective_minimum_area_m2",
            "width_method",
            "minimum_width_row_spacing_factor",
            "effective_minimum_width_m",
        },
    )
    require(
        work_block_filter["revision"] == "CF0_WORK_BLOCK_SUPPORT_V1",
        "Unsupported work-block filter revision.",
    )
    require(
        work_block_filter["grid_cell_estimate_method"]
        == "FLOOR_AREA_DIVIDED_BY_GRID_CELL_AREA",
        "Unsupported work-block cell estimate method.",
    )
    require(
        work_block_filter["width_method"]
        == "MINIMUM_ROTATED_RECTANGLE_SHORT_SIDE",
        "Unsupported work-block width method.",
    )
    require(
        isinstance(work_block_filter["minimum_grid_cell_count"], int)
        and not isinstance(work_block_filter["minimum_grid_cell_count"], bool)
        and work_block_filter["minimum_grid_cell_count"] >= 1,
        "minimum_grid_cell_count is invalid.",
    )
    for key in (
        "effective_minimum_area_m2",
        "minimum_width_row_spacing_factor",
        "effective_minimum_width_m",
    ):
        finite_number(work_block_filter[key], f"work_block_filter.{key}", minimum=1e-12)
    work_blocks = require_array(domain["work_blocks"], "domain_assembly.work_blocks", nonempty=True)
    work_block_ids: set[str] = set()
    work_block_fields: dict[str, set[str]] = {}
    work_block_components: set[tuple[frozenset[str], int]] = set()
    for index, block_value in enumerate(work_blocks):
        block = require_object(block_value, f"domain_assembly.work_blocks[{index}]")
        require_keys(block, f"domain_assembly.work_blocks[{index}]", {"work_block_id", "field_ids", "component_index", "usable_area_m2"})
        block_id = block["work_block_id"]
        require(isinstance(block_id, str) and block_id, "work_block_id is invalid.")
        require(block_id not in work_block_ids, f"Duplicate work_block_id: {block_id}")
        fields = require_array(block["field_ids"], f"work block {block_id}.field_ids", nonempty=True)
        require(all(isinstance(value, str) and value for value in fields), f"Invalid field_ids in {block_id}.")
        require(len(fields) == len(set(fields)), f"Duplicate field_ids in {block_id}.")
        component_index = block["component_index"]
        require(isinstance(component_index, int) and not isinstance(component_index, bool) and component_index >= 1, f"Invalid component_index in {block_id}.")
        component_key = (frozenset(fields), component_index)
        require(component_key not in work_block_components, f"Duplicate source component in {block_id}.")
        work_block_components.add(component_key)
        finite_number(block["usable_area_m2"], f"{block_id}.usable_area_m2", minimum=1e-12)
        work_block_ids.add(block_id)
        work_block_fields[block_id] = set(fields)
    excluded_components = require_array(
        domain["excluded_components"], "domain_assembly.excluded_components"
    )
    excluded_keys: set[tuple[str, int]] = set()
    excluded_field_ids: set[str] = set()
    for index, excluded_value in enumerate(excluded_components):
        label = f"domain_assembly.excluded_components[{index}]"
        excluded = require_object(excluded_value, label)
        require_keys(
            excluded,
            label,
            {
                "field_id",
                "component_index",
                "area_m2",
                "minimum_rotated_width_m",
                "estimated_grid_cell_count",
                "reason_codes",
            },
        )
        field_id = excluded["field_id"]
        component_index = excluded["component_index"]
        require(isinstance(field_id, str) and field_id, f"{label}.field_id is invalid.")
        require(
            isinstance(component_index, int)
            and not isinstance(component_index, bool)
            and component_index >= 1,
            f"{label}.component_index is invalid.",
        )
        component_key = (field_id, component_index)
        require(component_key not in excluded_keys, f"Duplicate excluded component: {component_key}")
        require(
            (frozenset({field_id}), component_index) not in work_block_components,
            f"Component is both included and excluded: {component_key}",
        )
        finite_number(excluded["area_m2"], f"{label}.area_m2", minimum=1e-12)
        finite_number(
            excluded["minimum_rotated_width_m"],
            f"{label}.minimum_rotated_width_m",
            minimum=0,
        )
        require(
            isinstance(excluded["estimated_grid_cell_count"], int)
            and not isinstance(excluded["estimated_grid_cell_count"], bool)
            and excluded["estimated_grid_cell_count"] >= 0,
            f"{label}.estimated_grid_cell_count is invalid.",
        )
        reason_codes = require_array(excluded["reason_codes"], f"{label}.reason_codes", nonempty=True)
        allowed_reasons = {
            "AREA_BELOW_CF0_WORK_BLOCK_MINIMUM",
            "WIDTH_BELOW_CF0_WORK_BLOCK_MINIMUM",
        }
        require(
            reason_codes == list(dict.fromkeys(reason_codes))
            and set(reason_codes) <= allowed_reasons,
            f"{label}.reason_codes is invalid.",
        )
        excluded_keys.add(component_key)
        excluded_field_ids.add(field_id)
    domain_blockers = require_array(domain["blocker_codes"], "domain_assembly.blocker_codes")
    declared_field_ids = {str(value) for value in inputs["work_area"].get("field_ids", [])}
    assembled_field_ids = set().union(*work_block_fields.values())
    require(
        assembled_field_ids | excluded_field_ids == declared_field_ids,
        "Included and excluded components do not cover exactly the declared work-area fields.",
    )
    if domain["status"] == "ONE_WORK_BLOCK_PER_FIELD":
        block_field_counts = Counter(
            next(iter(fields)) for fields in work_block_fields.values() if len(fields) == 1
        )
        require(all(len(fields) == 1 for fields in work_block_fields.values()), "ONE_WORK_BLOCK_PER_FIELD contains a multi-field block.")
        require(set(block_field_counts) == declared_field_ids and all(count == 1 for count in block_field_counts.values()), "ONE_WORK_BLOCK_PER_FIELD must contain exactly one block for each field.")
    if domain["status"] == "CROSS_FIELD_DISSOLVED":
        require(domain["cross_field_requested"] is True, "Dissolved domain was not requested.")
        require(any(len(fields) > 1 for fields in work_block_fields.values()), "Dissolved domain has no multi-field work block.")
        require(domain["cadastral_boundaries_used_as_work_edges"] is False, "Dissolved cadastral boundaries cannot remain work edges.")
    if domain["status"] == "CONNECTED_COMPONENTS_PER_FIELD":
        require(domain["cross_field_requested"] is False, "Per-field components cannot claim a cross-field request.")
        require(domain["cadastral_boundaries_used_as_work_edges"] is True, "Per-field component mode must disclose its boundary assumption.")
        require(all(len(fields) == 1 for fields in work_block_fields.values()), "Per-field component mode contains a multi-field work block.")
    if domain["status"] == "BLOCKED_CROSS_FIELD_UNCONFIRMED":
        require(domain["cross_field_requested"] is True and bool(domain_blockers), "Blocked cross-field assembly lacks its blocker.")

    parameters = validate_solver_parameters(node["solver_parameters"], limitations)
    grid_resolution = float(parameters["grid_resolution_m"])
    row_spacing = float(parameters["row_spacing_m"])
    expected_minimum_area = (
        int(work_block_filter["minimum_grid_cell_count"]) * grid_resolution**2
    )
    expected_minimum_width = max(
        grid_resolution,
        float(work_block_filter["minimum_width_row_spacing_factor"]) * row_spacing,
    )
    require(
        math.isclose(
            float(work_block_filter["effective_minimum_area_m2"]),
            expected_minimum_area,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ),
        "work_block_filter effective minimum area disagrees with grid resolution.",
    )
    require(
        math.isclose(
            float(work_block_filter["effective_minimum_width_m"]),
            expected_minimum_width,
            rel_tol=1e-12,
            abs_tol=1e-9,
        ),
        "work_block_filter effective minimum width disagrees with row spacing/grid.",
    )
    candidates = validate_candidate_results(node["candidates"], parameters, work_block_fields)
    validate_stage_counts(node, candidates)
    validate_output_records(node["outputs"], candidates, manifest_path)
    return node


def validate_solver_parameters(parameters_value: Any, limitations: list[Any]) -> dict[str, Any]:
    parameters = require_object(parameters_value, "solver_parameters")
    require_keys(
        parameters,
        "solver_parameters",
        {
            "solver_revision",
            "grid_resolution_m",
            "row_spacing_m",
            "minimum_work_path_radius_m",
            "radius_requirement_status",
            "orientation",
            "phase",
            "extraction",
            "candidate_profiles",
        },
    )
    require(
        parameters["solver_revision"] == "cf0-continuous-family-1.2.0",
        "Unsupported solver_revision.",
    )
    finite_number(parameters["grid_resolution_m"], "grid_resolution_m", minimum=1e-12)
    row_spacing = finite_number(parameters["row_spacing_m"], "row_spacing_m", minimum=1e-12)
    radius_status = parameters["radius_requirement_status"]
    require(radius_status in {"DECLARED", "NOT_PROVIDED"}, "Invalid radius_requirement_status.")
    radius = parameters["minimum_work_path_radius_m"]
    if radius_status == "DECLARED":
        finite_number(radius, "minimum_work_path_radius_m", minimum=1e-12)
    else:
        require(radius is None, "Missing radius requirement must be represented by null.")
        require("RADIUS_REQUIREMENT_NOT_PROVIDED" in limitations, "Missing radius requirement is not disclosed.")

    orientation = require_object(parameters["orientation"], "solver_parameters.orientation")
    require_keys(
        orientation,
        "solver_parameters.orientation",
        {
            "representation",
            "smoothing_radius_m",
            "minimum_coherence",
            "maximum_low_coherence_fraction",
            "maximum_frustration_deg",
            "maximum_abrupt_edge_fraction",
            "maximum_cycle_conflict_fraction",
            "minimum_terrain_gradient",
            "gradient_floor",
        },
    )
    require(orientation["representation"] == "AXIAL_DOUBLE_ANGLE", "Orientation must use axial double angles.")
    finite_number(orientation["smoothing_radius_m"], "orientation.smoothing_radius_m", minimum=0)
    coherence = finite_number(orientation["minimum_coherence"], "orientation.minimum_coherence", minimum=0)
    require(coherence <= 1, "minimum_coherence must be <= 1.")
    for key in (
        "maximum_low_coherence_fraction",
        "maximum_abrupt_edge_fraction",
        "maximum_cycle_conflict_fraction",
    ):
        value = finite_number(orientation[key], f"orientation.{key}", minimum=0)
        require(value <= 1, f"orientation.{key} must be <= 1.")
    frustration = finite_number(orientation["maximum_frustration_deg"], "orientation.maximum_frustration_deg", minimum=0)
    require(frustration <= 90, "maximum_frustration_deg must be <= 90.")
    finite_number(orientation["minimum_terrain_gradient"], "orientation.minimum_terrain_gradient", minimum=0)
    finite_number(orientation["gradient_floor"], "orientation.gradient_floor", minimum=0)

    phase = require_object(parameters["phase"], "solver_parameters.phase")
    require_keys(
        phase,
        "solver_parameters.phase",
        {
            "method",
            "gauge_method",
            "gauge_anchor_value_m",
            "eikonal_role",
            "seed_method",
            "target_gradient_norm",
            "eikonal_residual_tolerance",
            "integrability_residual_tolerance",
            "maximum_iterations",
            "convergence_tolerance",
            "regularization_weight",
            "boundary_weight",
            "phase_scale_min",
            "phase_scale_max",
            "phase_scale_relaxation",
            "lsqr_tolerance",
            "lsqr_iteration_limit",
            "maximum_integrability_residual_rms",
            "maximum_integrability_angular_error_p95_deg",
            "maximum_eikonal_residual_p95",
            "maximum_critical_fraction",
            "cut_locus_laplacian_threshold",
            "maximum_cut_locus_fraction",
            "solve_mask_required_component_count",
            "solve_mask_component_connectivity",
        },
    )
    require(phase["method"] == "ALTERNATING_POSITIVE_SCALE_PROJECTION", "Unexpected phase method.")
    require(
        phase["gauge_method"]
        == "ZERO_AT_LEXICOGRAPHIC_FIRST_VALID_CELL_PER_COMPONENT",
        "Unexpected phase gauge method.",
    )
    require(
        math.isclose(
            finite_number(phase["gauge_anchor_value_m"], "phase.gauge_anchor_value_m"),
            0.0,
            rel_tol=0,
            abs_tol=1e-12,
        ),
        "CF0 phase gauge anchor value must be zero.",
    )
    require(phase["eikonal_role"] == "RESIDUAL_GATE_ONLY", "CF0 eikonal role must remain diagnostic-only.")
    require(phase["seed_method"] == "POISSON_PROJECTION", "Unexpected phase seed method.")
    require(math.isclose(finite_number(phase["target_gradient_norm"], "phase.target_gradient_norm"), 1.0, abs_tol=1e-12), "Eikonal target must be 1.")
    finite_number(phase["eikonal_residual_tolerance"], "phase.eikonal_residual_tolerance", minimum=0)
    finite_number(phase["integrability_residual_tolerance"], "phase.integrability_residual_tolerance", minimum=0)
    require(isinstance(phase["maximum_iterations"], int) and phase["maximum_iterations"] > 0, "maximum_iterations is invalid.")
    finite_number(phase["convergence_tolerance"], "phase.convergence_tolerance", minimum=1e-15)
    for key in ("regularization_weight", "boundary_weight"):
        finite_number(phase[key], f"phase.{key}", minimum=0)
    phase_scale_min = finite_number(phase["phase_scale_min"], "phase.phase_scale_min", minimum=1e-15)
    phase_scale_max = finite_number(phase["phase_scale_max"], "phase.phase_scale_max", minimum=1e-15)
    require(phase_scale_min <= phase_scale_max, "phase_scale_min must not exceed phase_scale_max.")
    relaxation = finite_number(phase["phase_scale_relaxation"], "phase.phase_scale_relaxation", minimum=1e-15)
    require(relaxation <= 1, "phase_scale_relaxation must be <= 1.")
    finite_number(phase["lsqr_tolerance"], "phase.lsqr_tolerance", minimum=1e-15)
    require(
        isinstance(phase["lsqr_iteration_limit"], int)
        and not isinstance(phase["lsqr_iteration_limit"], bool)
        and phase["lsqr_iteration_limit"] >= 1,
        "phase.lsqr_iteration_limit is invalid.",
    )
    for key in (
        "maximum_integrability_residual_rms",
        "maximum_eikonal_residual_p95",
        "cut_locus_laplacian_threshold",
    ):
        finite_number(phase[key], f"phase.{key}", minimum=0)
    angular_limit = finite_number(
        phase["maximum_integrability_angular_error_p95_deg"],
        "phase.maximum_integrability_angular_error_p95_deg",
        minimum=0,
    )
    require(angular_limit <= 90, "maximum_integrability_angular_error_p95_deg must be <= 90.")
    for key in ("maximum_critical_fraction", "maximum_cut_locus_fraction"):
        value = finite_number(phase[key], f"phase.{key}", minimum=0)
        require(value <= 1, f"phase.{key} must be <= 1.")
    require(
        isinstance(phase["solve_mask_required_component_count"], int)
        and not isinstance(phase["solve_mask_required_component_count"], bool)
        and phase["solve_mask_required_component_count"] == 1,
        "CF0 solve mask must have exactly one required component.",
    )
    require(
        isinstance(phase["solve_mask_component_connectivity"], int)
        and not isinstance(phase["solve_mask_component_connectivity"], bool)
        and phase["solve_mask_component_connectivity"] == 4,
        "CF0 solve-mask component connectivity must equal four.",
    )
    require(
        math.isclose(
            float(phase["maximum_integrability_residual_rms"]),
            float(phase["integrability_residual_tolerance"]),
            rel_tol=0,
            abs_tol=1e-12,
        ),
        "maximum_integrability_residual_rms must equal integrability_residual_tolerance in CF0 1.2.",
    )
    require(
        math.isclose(
            angular_limit,
            frustration,
            rel_tol=0,
            abs_tol=1e-12,
        ),
        "maximum_integrability_angular_error_p95_deg must equal maximum_frustration_deg in CF0 1.2.",
    )
    require(
        math.isclose(
            float(phase["maximum_eikonal_residual_p95"]),
            float(phase["eikonal_residual_tolerance"]),
            rel_tol=0,
            abs_tol=1e-12,
        ),
        "maximum_eikonal_residual_p95 must equal eikonal_residual_tolerance in CF0 1.2.",
    )

    extraction = require_object(parameters["extraction"], "solver_parameters.extraction")
    require_keys(
        extraction,
        "solver_parameters.extraction",
        {
            "level_interval_m",
            "phase_level_equation",
            "phase_level_index_scope",
            "row_index_definition",
            "phase_offset_search_revision",
            "phase_offset_fractions",
            "phase_offset_selection_rule",
            "phase_offset_search_scope",
            "minimum_row_length_m",
            "endpoint_tolerance_m",
            "spacing_tolerance_fraction",
            "spacing_tolerance_m",
            "maximum_spacing_outside_tolerance_fraction",
            "coverage_proxy_minimum",
            "coverage_proxy_maximum",
            "curvature_sample_step_m",
            "spline_method",
            "spline_max_deviation_m",
            "endpoint_snap_tolerance_m",
            "endpoint_extension_limit_m",
            "endpoint_extension_factor",
            "solve_mask_rasterization",
            "contour_extrapolation_method",
            "contour_extrapolation_halo_cells",
            "contour_gradient_lsq_max_radius_cells",
            "contour_gradient_lsq_max_relative_residual",
            "contour_gradient_lsq_max_condition_number",
            "contour_gradient_lsq_minimum_neighbor_count",
            "contour_gradient_lsq_required_rank",
            "contour_gradient_component_connectivity",
            "endpoint_extension_mode",
            "spline_representation_method",
            "spline_representation_tolerance_fraction",
        },
    )
    validate_extraction_method_contract(extraction)
    require(extraction["phase_level_equation"] == PHASE_LEVEL_EQUATION, "Unexpected phase-level equation.")
    require(
        extraction["phase_level_index_scope"] == "CANDIDATE_WORK_BLOCK_PHASE_LEVEL",
        "Unexpected phase-level index scope.",
    )
    require(
        extraction["row_index_definition"]
        == "UNIQUE_NONNEGATIVE_OPERATIONAL_SEQUENCE_PER_CANDIDATE_WORK_BLOCK",
        "Unexpected row-index definition.",
    )
    require(
        extraction["phase_offset_search_revision"]
        == "CF0_PHASE_OFFSET_QUARTER_SPACING_V1",
        "Unsupported phase-offset search revision.",
    )
    fractions = require_array(
        extraction["phase_offset_fractions"], "extraction.phase_offset_fractions"
    )
    require(
        len(fractions) == len(PHASE_OFFSET_FRACTIONS)
        and all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isclose(float(value), expected, rel_tol=0, abs_tol=1e-12)
            for value, expected in zip(fractions, PHASE_OFFSET_FRACTIONS)
        ),
        "CF0 phase-offset search must use exactly 0, 1/4, 1/2 and 3/4 spacing.",
    )
    require(
        extraction["phase_offset_selection_rule"] == PHASE_OFFSET_SELECTION_RULE,
        "Unsupported phase-offset selection rule.",
    )
    require(
        extraction["phase_offset_search_scope"]
        == "DISCRETE_CONFIGURED_OFFSETS_NOT_CONTINUOUS_GAUGE_INVARIANCE",
        "Unexpected phase-offset search scope.",
    )
    level_interval = finite_number(extraction["level_interval_m"], "extraction.level_interval_m", minimum=1e-12)
    require(math.isclose(level_interval, row_spacing, rel_tol=0, abs_tol=1e-9), "Phase level interval must equal row spacing.")
    for key in ("minimum_row_length_m", "spacing_tolerance_fraction", "spacing_tolerance_m"):
        finite_number(extraction[key], f"extraction.{key}", minimum=0)
    for key in ("maximum_spacing_outside_tolerance_fraction",):
        value = finite_number(extraction[key], f"extraction.{key}", minimum=0)
        require(value <= 1, f"extraction.{key} must be <= 1.")
    coverage_minimum = finite_number(extraction["coverage_proxy_minimum"], "extraction.coverage_proxy_minimum", minimum=0)
    coverage_maximum = finite_number(extraction["coverage_proxy_maximum"], "extraction.coverage_proxy_maximum", minimum=0)
    require(coverage_minimum <= coverage_maximum, "coverage_proxy_minimum must not exceed coverage_proxy_maximum.")
    for key in ("endpoint_tolerance_m", "curvature_sample_step_m"):
        finite_number(extraction[key], f"extraction.{key}", minimum=1e-12)
    require(extraction["spline_method"] == "PARAMETRIC_BSPLINE_C2", "CF0 rows must use the declared C2 parametric spline stage.")
    for key in ("spline_max_deviation_m", "endpoint_snap_tolerance_m", "endpoint_extension_limit_m", "endpoint_extension_factor"):
        finite_number(extraction[key], f"extraction.{key}", minimum=1e-12)
    require(
        math.isclose(
            float(extraction["spacing_tolerance_m"]),
            row_spacing * float(extraction["spacing_tolerance_fraction"]),
            rel_tol=0,
            abs_tol=1e-9,
        ),
        "spacing_tolerance_m disagrees with row_spacing_m * spacing_tolerance_fraction.",
    )
    require(
        math.isclose(
            float(extraction["endpoint_tolerance_m"]),
            float(extraction["endpoint_snap_tolerance_m"]),
            rel_tol=0,
            abs_tol=1e-9,
        ),
        "endpoint_tolerance_m must equal endpoint_snap_tolerance_m.",
    )
    require(
        math.isclose(
            float(extraction["endpoint_extension_limit_m"]),
            float(parameters["grid_resolution_m"]) * float(extraction["endpoint_extension_factor"]),
            rel_tol=0,
            abs_tol=1e-9,
        ),
        "endpoint_extension_limit_m disagrees with grid_resolution_m * endpoint_extension_factor.",
    )

    profiles = require_array(parameters["candidate_profiles"], "candidate_profiles", nonempty=True)
    profile_ids: list[str] = []
    for index, profile_value in enumerate(profiles):
        profile = require_object(profile_value, f"candidate_profiles[{index}]")
        require_keys(profile, f"candidate_profiles[{index}]", {"candidate_id", "label", "orientation_weights"})
        candidate_id = profile["candidate_id"]
        require(candidate_id in EXPECTED_CANDIDATES, f"Unknown candidate profile: {candidate_id}")
        profile_ids.append(candidate_id)
        require(isinstance(profile["label"], str) and profile["label"], "Candidate label is empty.")
        orientation_weights = require_object(profile["orientation_weights"], f"{candidate_id}.orientation_weights")
        require_keys(orientation_weights, f"{candidate_id}.orientation_weights", {"contour", "long_axis", "boundary"})
        for key, value in orientation_weights.items():
            finite_number(value, f"{candidate_id}.{key}", minimum=0)
        require(sum(float(value) for value in orientation_weights.values()) > 0, f"{candidate_id} has zero orientation weights.")
    require(len(profile_ids) == len(set(profile_ids)), "candidate_profiles has duplicate candidate_id values.")
    require(set(profile_ids) == EXPECTED_CANDIDATES, "candidate_profiles must contain exactly CF0A, CF0B and CF0C.")
    return parameters


def validate_extraction_method_contract(extraction: dict[str, Any]) -> None:
    require(
        extraction["solve_mask_rasterization"] == "ALL_TOUCHED_SUPERCOVER",
        "CF0 solve mask must use ALL_TOUCHED_SUPERCOVER.",
    )
    require(
        extraction["contour_extrapolation_method"]
        == "FIRST_ORDER_LOCAL_LSQ_GRADIENT_FAIL_CLOSED",
        "Unsupported contour extrapolation method.",
    )
    require(
        isinstance(extraction["contour_extrapolation_halo_cells"], int)
        and not isinstance(extraction["contour_extrapolation_halo_cells"], bool)
        and extraction["contour_extrapolation_halo_cells"] == 1,
        "CF0 contour extrapolation halo must be exactly one cell.",
    )
    expected_lsq_values = {
        "contour_gradient_lsq_max_radius_cells": 2,
        "contour_gradient_lsq_minimum_neighbor_count": 3,
        "contour_gradient_lsq_required_rank": 2,
        "contour_gradient_component_connectivity": 4,
    }
    for key, expected in expected_lsq_values.items():
        require(
            isinstance(extraction[key], int)
            and not isinstance(extraction[key], bool)
            and extraction[key] == expected,
            f"CF0 extraction {key} must equal {expected}.",
        )
    expected_lsq_thresholds = {
        "contour_gradient_lsq_max_relative_residual": 0.30,
        "contour_gradient_lsq_max_condition_number": 100.0,
    }
    for key, expected in expected_lsq_thresholds.items():
        value = finite_number(extraction[key], f"extraction.{key}", minimum=1e-15)
        require(
            math.isclose(value, expected, rel_tol=0, abs_tol=1e-12),
            f"CF0 extraction {key} must equal {expected}.",
        )
    require(
        extraction["endpoint_extension_mode"] == "TANGENT_ONLY_FAIL_CLOSED",
        "CF0 endpoint extension must be tangent-only and fail closed.",
    )
    require(
        extraction["spline_representation_method"]
        == "ADAPTIVE_CHORD_ERROR_PRESERVE_VERTICES",
        "Unsupported spline representation method.",
    )
    tolerance_fraction = finite_number(
        extraction["spline_representation_tolerance_fraction"],
        "extraction.spline_representation_tolerance_fraction",
        minimum=1e-15,
    )
    require(tolerance_fraction < 1, "Spline representation tolerance fraction must be < 1.")
    require(
        math.isclose(tolerance_fraction, 0.10, rel_tol=0, abs_tol=1e-12),
        "CF0 spline representation tolerance fraction must be exactly 0.10.",
    )


def require_same_number(actual: Any, expected: Any, label: str, *, tolerance: float = 1e-8) -> None:
    if expected is None:
        require(actual is None, f"{label} must be null.")
        return
    actual_number = finite_number(actual, label)
    expected_number = finite_number(expected, f"expected {label}")
    require(
        math.isclose(actual_number, expected_number, rel_tol=0, abs_tol=tolerance),
        f"{label} disagrees with its linked value.",
    )


def validate_contour_extrapolation_qa(
    qa_value: Any,
    extraction: dict[str, Any],
    label: str,
    *,
    below_grid_support: bool,
) -> str:
    qa = require_object(qa_value, label)
    count_fields = {
        "halo_cell_count",
        "supported_halo_cell_count",
        "unsupported_halo_cell_count",
        "direct_gradient_source_count",
        "lsq_gradient_source_count",
        "unestimable_gradient_source_count",
        "missing_gradient_component_count",
        "gradient_lsq_max_radius_used_cells",
    }
    require_keys(
        qa,
        label,
        {
            "method",
            "gradient_estimation_status",
            "halo_cells",
            "gradient_lsq_max_radius_cells",
            "gradient_lsq_max_relative_residual",
            "gradient_lsq_max_condition_number",
            "gradient_lsq_minimum_neighbor_count",
            "gradient_lsq_required_rank",
            "gradient_component_connectivity",
            *count_fields,
            "gradient_lsq_residual_rms_max",
            "gradient_lsq_relative_residual_max",
            "gradient_lsq_condition_number_max",
        },
    )
    require(
        qa["method"] == extraction["contour_extrapolation_method"],
        f"{label}.method disagrees with solver extraction.",
    )
    config_mapping = {
        "halo_cells": "contour_extrapolation_halo_cells",
        "gradient_lsq_max_radius_cells": "contour_gradient_lsq_max_radius_cells",
        "gradient_lsq_minimum_neighbor_count": "contour_gradient_lsq_minimum_neighbor_count",
        "gradient_lsq_required_rank": "contour_gradient_lsq_required_rank",
        "gradient_component_connectivity": "contour_gradient_component_connectivity",
    }
    for qa_key, extraction_key in config_mapping.items():
        require(
            isinstance(qa[qa_key], int)
            and not isinstance(qa[qa_key], bool)
            and qa[qa_key] == extraction[extraction_key],
            f"{label}.{qa_key} disagrees with solver extraction.",
        )
    threshold_mapping = {
        "gradient_lsq_max_relative_residual": "contour_gradient_lsq_max_relative_residual",
        "gradient_lsq_max_condition_number": "contour_gradient_lsq_max_condition_number",
    }
    for qa_key, extraction_key in threshold_mapping.items():
        require_same_number(qa[qa_key], extraction[extraction_key], f"{label}.{qa_key}")
    for field in count_fields:
        require(
            isinstance(qa[field], int)
            and not isinstance(qa[field], bool)
            and qa[field] >= 0,
            f"{label}.{field} must be a non-negative integer.",
        )
    require(
        qa["halo_cell_count"]
        == qa["supported_halo_cell_count"] + qa["unsupported_halo_cell_count"],
        f"{label} halo-cell partition does not close.",
    )
    require(
        qa["gradient_lsq_max_radius_used_cells"]
        <= qa["gradient_lsq_max_radius_cells"],
        f"{label} reports an LSQ radius beyond the configured search.",
    )
    residual = nullable_finite(
        qa["gradient_lsq_residual_rms_max"],
        f"{label}.gradient_lsq_residual_rms_max",
    )
    relative_residual = nullable_finite(
        qa["gradient_lsq_relative_residual_max"],
        f"{label}.gradient_lsq_relative_residual_max",
    )
    condition_number = nullable_finite(
        qa["gradient_lsq_condition_number_max"],
        f"{label}.gradient_lsq_condition_number_max",
    )
    if qa["lsq_gradient_source_count"] == 0:
        require(residual is None, f"{label} reports an LSQ residual without LSQ sources.")
        require(
            relative_residual is None,
            f"{label} reports a relative LSQ residual without LSQ sources.",
        )
        require(
            condition_number is None,
            f"{label} reports an LSQ condition number without LSQ sources.",
        )
        require(
            qa["gradient_lsq_max_radius_used_cells"] == 0,
            f"{label} reports an LSQ radius without LSQ sources.",
        )
    else:
        require(residual is not None, f"{label} omits the LSQ residual.")
        require(relative_residual is not None, f"{label} omits the relative LSQ residual.")
        require(condition_number is not None, f"{label} omits the LSQ condition number.")
        require(
            qa["gradient_lsq_max_radius_used_cells"] >= 1,
            f"{label} omits the used LSQ radius.",
        )
        require(
            relative_residual <= qa["gradient_lsq_max_relative_residual"] + 1e-12,
            f"{label} accepted an LSQ source beyond the relative-residual threshold.",
        )
        require(
            condition_number >= 1
            and condition_number <= qa["gradient_lsq_max_condition_number"] + 1e-12,
            f"{label} accepted an LSQ source beyond the condition-number threshold.",
        )
    status = qa["gradient_estimation_status"]
    require(status in {"PASS", "FAIL_CLOSED", "NOT_EVALUATED"}, f"{label} has an invalid gradient status.")
    if below_grid_support:
        require(status == "NOT_EVALUATED", f"{label} must be NOT_EVALUATED below grid support.")
        require(all(qa[field] == 0 for field in count_fields), f"{label} has measurements below grid support.")
        require(residual is None, f"{label} has an LSQ residual below grid support.")
        require(relative_residual is None, f"{label} has a relative LSQ residual below grid support.")
        require(condition_number is None, f"{label} has an LSQ condition number below grid support.")
    elif status == "PASS":
        require(qa["unestimable_gradient_source_count"] == 0, f"{label} PASS retains unestimable gradients.")
        require(qa["unsupported_halo_cell_count"] == 0, f"{label} PASS retains unsupported halo cells.")
    else:
        require(status == "FAIL_CLOSED", f"{label} evaluated QA must PASS or fail closed.")
        require(qa["unestimable_gradient_source_count"] > 0, f"{label} FAIL_CLOSED lacks an unestimable source.")
        require(qa["unsupported_halo_cell_count"] > 0, f"{label} FAIL_CLOSED lacks unsupported halo cells.")
    return status


def _phase_trial_selection_key(trial: dict[str, Any]) -> tuple[float, float, float, float, float]:
    radius_class = trial["minimum_radius_order_class"]
    if radius_class == "UNBOUNDED_STRAIGHT":
        radius_key = -math.inf
    elif radius_class == "FINITE":
        radius_key = -float(trial["minimum_radius_m"])
    else:
        radius_key = math.inf
    final_outside = trial["final_spacing_outside_tolerance_fraction"]
    spacing_p95 = trial["spacing_error_p95_m"]
    coverage = trial["coverage_proxy_ratio"]
    return (
        radius_key,
        math.inf if final_outside is None else float(final_outside),
        math.inf if spacing_p95 is None else float(spacing_p95),
        math.inf if coverage is None else abs(float(coverage) - 1.0),
        float(trial["phase_offset_fraction"]),
    )


def validate_phase_offset_selection(
    candidate: dict[str, Any],
    parameters: dict[str, Any],
    label: str,
) -> dict[str, Any] | None:
    selection = require_object(candidate["phase_offset_selection"], f"{label}.phase_offset_selection")
    require_keys(
        selection,
        f"{label}.phase_offset_selection",
        {
            "revision",
            "selection_rule",
            "search_scope",
            "selection_status",
            "selection_key_order",
            "coverage_proxy_target_ratio",
            "phase_offset_role",
            "selected_phase_offset_fraction",
            "selected_phase_offset_m",
            "diagnostic_phase_offset_fraction",
            "diagnostic_phase_offset_m",
            "diagnostic_selection_rule",
            "attempted_offsets",
            "row_spacing_m",
            "level_equation",
            "phase_level_index_scope",
            "row_index_definition",
            "phase_gauge",
        },
    )
    extraction = parameters["extraction"]
    row_spacing = float(parameters["row_spacing_m"])
    require(selection["revision"] == extraction["phase_offset_search_revision"], f"{label} phase search revision disagrees.")
    require(selection["selection_rule"] == PHASE_OFFSET_SELECTION_RULE, f"{label} phase selection rule disagrees.")
    require(selection["search_scope"] == extraction["phase_offset_search_scope"], f"{label} phase search scope disagrees.")
    require(selection["selection_key_order"] == PHASE_OFFSET_SELECTION_KEYS, f"{label} phase selection key order disagrees.")
    require_same_number(selection["coverage_proxy_target_ratio"], 1.0, f"{label}.coverage_proxy_target_ratio")
    require_same_number(selection["row_spacing_m"], row_spacing, f"{label}.phase_offset_selection.row_spacing_m")
    require(selection["level_equation"] == PHASE_LEVEL_EQUATION, f"{label} phase-level equation disagrees.")
    require(selection["phase_level_index_scope"] == extraction["phase_level_index_scope"], f"{label} phase-level index scope disagrees.")
    require(selection["row_index_definition"] == extraction["row_index_definition"], f"{label} row-index definition disagrees.")
    require(selection["diagnostic_selection_rule"] == "LOWEST_CONFIGURED_PHASE_OFFSET_V1", f"{label} diagnostic selection rule disagrees.")

    blockers = set(candidate["blocker_codes"])
    below_grid_support = "WORK_BLOCK_BELOW_CF0_GRID_SUPPORT" in blockers
    gauge = require_object(selection["phase_gauge"], f"{label}.phase_gauge")
    require_keys(gauge, f"{label}.phase_gauge", {"method", "anchor_phase_value_m", "anchors"})
    anchors = require_array(gauge["anchors"], f"{label}.phase_gauge.anchors")
    if below_grid_support:
        require(gauge["method"] == "NOT_APPLICABLE_WORK_BLOCK_BELOW_GRID_SUPPORT", f"{label} has an active gauge below grid support.")
        require(gauge["anchor_phase_value_m"] is None and not anchors, f"{label} has gauge anchors below grid support.")
    else:
        require(gauge["method"] == parameters["phase"]["gauge_method"], f"{label} phase gauge method disagrees.")
        require_same_number(gauge["anchor_phase_value_m"], parameters["phase"]["gauge_anchor_value_m"], f"{label}.phase_gauge.anchor_phase_value_m")
        require(bool(anchors), f"{label} evaluated phase gauge has no anchors.")
        anchor_indices: set[tuple[int, int]] = set()
        for anchor_index, anchor_value in enumerate(anchors):
            anchor = require_object(anchor_value, f"{label}.phase_gauge.anchors[{anchor_index}]")
            require_keys(anchor, f"{label}.phase_gauge.anchors[{anchor_index}]", {"solver_row", "solver_column", "x_m", "y_m"})
            solver_row = anchor["solver_row"]
            solver_column = anchor["solver_column"]
            require(isinstance(solver_row, int) and not isinstance(solver_row, bool) and solver_row >= 0, f"{label} has an invalid gauge row.")
            require(isinstance(solver_column, int) and not isinstance(solver_column, bool) and solver_column >= 0, f"{label} has an invalid gauge column.")
            require((solver_row, solver_column) not in anchor_indices, f"{label} has duplicate phase-gauge anchors.")
            anchor_indices.add((solver_row, solver_column))
            finite_number(anchor["x_m"], f"{label}.phase_gauge.anchor.x_m")
            finite_number(anchor["y_m"], f"{label}.phase_gauge.anchor.y_m")

    attempts = require_array(selection["attempted_offsets"], f"{label}.attempted_offsets")
    status = selection["selection_status"]
    role = selection["phase_offset_role"]
    require(candidate["phase_offset_role"] == role, f"{label} candidate/selection phase roles disagree.")
    if below_grid_support:
        require(status == "NOT_EVALUATED_WORK_BLOCK_BELOW_GRID_SUPPORT", f"{label} has the wrong below-support selection status.")
        require(role == "NOT_EVALUATED" and not attempts, f"{label} evaluated offsets below grid support.")
        for key in (
            "phase_offset_m",
            "phase_offset_fraction",
        ):
            require(candidate[key] is None, f"{label}.{key} must be null below grid support.")
        for key in (
            "selected_phase_offset_fraction",
            "selected_phase_offset_m",
            "diagnostic_phase_offset_fraction",
            "diagnostic_phase_offset_m",
        ):
            require(selection[key] is None, f"{label}.{key} must be null below grid support.")
        return None

    require(len(attempts) == 4, f"{label} must persist exactly four phase-offset trials.")
    trial_keys = {
        "phase_offset_fraction",
        "phase_offset_m",
        "geometric_status",
        "blocker_codes",
        "extracted_row_count",
        "extracted_total_length_m",
        "minimum_radius_order_class",
        "minimum_radius_m",
        "radius_status",
        "spacing_error_p95_m",
        "final_spacing_outside_tolerance_fraction",
        "coverage_proxy_ratio",
        "spline_failure_count",
        "internal_endpoint_count",
        "intersection_count",
        "self_intersection_count",
        "loop_count",
    }
    validated_attempts: list[dict[str, Any]] = []
    radius_declared = parameters["radius_requirement_status"] == "DECLARED"
    for trial_index, (trial_value, expected_fraction) in enumerate(zip(attempts, PHASE_OFFSET_FRACTIONS)):
        trial_label = f"{label}.attempted_offsets[{trial_index}]"
        trial = require_object(trial_value, trial_label)
        require_keys(trial, trial_label, trial_keys)
        require_same_number(trial["phase_offset_fraction"], expected_fraction, f"{trial_label}.phase_offset_fraction")
        require_same_number(trial["phase_offset_m"], expected_fraction * row_spacing, f"{trial_label}.phase_offset_m")
        trial_blockers = require_array(trial["blocker_codes"], f"{trial_label}.blocker_codes")
        require(trial_blockers == sorted(set(trial_blockers)), f"{trial_label}.blocker_codes must be unique and sorted.")
        require(all(isinstance(code, str) and code for code in trial_blockers), f"{trial_label} has invalid blocker codes.")
        require("C2_SPLINE_FAILED" not in trial_blockers, f"{trial_label} uses the retired spline blocker.")
        trial_status = trial["geometric_status"]
        require(trial_status in {"GEOMETRIC_PASS", "NO_FEASIBLE_FAMILY"}, f"{trial_label} has an invalid status.")
        if trial_status == "GEOMETRIC_PASS":
            require(not trial_blockers, f"{trial_label} passes with blockers.")
        else:
            require(bool(trial_blockers), f"{trial_label} fails without blockers.")
        for count_key in (
            "extracted_row_count",
            "spline_failure_count",
            "internal_endpoint_count",
            "intersection_count",
            "self_intersection_count",
            "loop_count",
        ):
            require(isinstance(trial[count_key], int) and not isinstance(trial[count_key], bool) and trial[count_key] >= 0, f"{trial_label}.{count_key} is invalid.")
        trial_length = finite_number(trial["extracted_total_length_m"], f"{trial_label}.extracted_total_length_m", minimum=0)
        require((trial["extracted_row_count"] == 0) == math.isclose(trial_length, 0.0, abs_tol=1e-12), f"{trial_label} row count/length emptiness disagrees.")
        minimum_radius = nullable_finite(trial["minimum_radius_m"], f"{trial_label}.minimum_radius_m")
        radius_order_class = trial["minimum_radius_order_class"]
        require(
            radius_order_class in {"FINITE", "UNBOUNDED_STRAIGHT", "UNAVAILABLE"},
            f"{trial_label} has an invalid minimum-radius order class.",
        )
        if radius_order_class == "FINITE":
            require(minimum_radius is not None and minimum_radius > 0, f"{trial_label} FINITE radius class needs a positive radius.")
        else:
            require(minimum_radius is None, f"{trial_label} non-finite radius class must use null minimum_radius_m.")
        if radius_order_class == "UNBOUNDED_STRAIGHT":
            require(trial["extracted_row_count"] > 0, f"{trial_label} unbounded straight class has no rows.")
            require(trial["spline_failure_count"] == 0, f"{trial_label} unbounded straight class has spline failures.")
        spacing_error = nullable_finite(trial["spacing_error_p95_m"], f"{trial_label}.spacing_error_p95_m")
        final_outside = nullable_finite(trial["final_spacing_outside_tolerance_fraction"], f"{trial_label}.final_spacing_outside_tolerance_fraction")
        require(final_outside is None or final_outside <= 1, f"{trial_label} spacing-outside fraction exceeds one.")
        nullable_finite(trial["coverage_proxy_ratio"], f"{trial_label}.coverage_proxy_ratio")
        require(trial["radius_status"] in {"PASS", "FAIL", "NOT_EVALUATED"}, f"{trial_label} has an invalid radius status.")
        if radius_declared:
            if radius_order_class == "UNAVAILABLE" or trial["spline_failure_count"] > 0:
                expected_radius_status = "FAIL"
            elif radius_order_class == "UNBOUNDED_STRAIGHT":
                expected_radius_status = "PASS"
            else:
                expected_radius_status = (
                    "PASS"
                    if float(minimum_radius) + 1e-9
                    >= float(parameters["minimum_work_path_radius_m"])
                    else "FAIL"
                )
            require(trial["radius_status"] == expected_radius_status, f"{trial_label} radius status disagrees with its ordering evidence.")
        else:
            require(trial["radius_status"] == "NOT_EVALUATED", f"{trial_label} evaluated an undeclared radius requirement.")
        expected_trial_blockers: set[str] = set()
        if trial["spline_failure_count"] > 0:
            expected_trial_blockers.add("ROW_SPLINE_FIT_FAILED")
        require(
            ("ROW_SPLINE_FIT_FAILED" in trial_blockers)
            == (trial["spline_failure_count"] > 0),
            f"{trial_label} spline blocker disagrees with spline_failure_count.",
        )
        qa_failed = (
            candidate["contour_extrapolation_qa"]["gradient_estimation_status"]
            == "FAIL_CLOSED"
        )
        require(
            ("PHASE_HALO_GRADIENT_UNESTIMABLE" in trial_blockers) == qa_failed,
            f"{trial_label} phase-halo blocker disagrees with candidate QA.",
        )
        if qa_failed:
            expected_trial_blockers.add("PHASE_HALO_GRADIENT_UNESTIMABLE")
        solve_mask_component_count = candidate["metrics"]["solver_gate_metrics"][
            "solve_mask_component_count"
        ]
        solve_mask_disconnected = (
            solve_mask_component_count
            != parameters["phase"]["solve_mask_required_component_count"]
        )
        require(
            ("WORK_BLOCK_SOLVE_MASK_DISCONNECTED" in trial_blockers)
            == solve_mask_disconnected,
            f"{trial_label} solve-mask blocker disagrees with candidate connectivity QA.",
        )
        if solve_mask_disconnected:
            expected_trial_blockers.add("WORK_BLOCK_SOLVE_MASK_DISCONNECTED")
        if trial["internal_endpoint_count"] > 0:
            expected_trial_blockers.add("INTERNAL_UNSUPPORTED_ENDPOINT")
        if trial["intersection_count"] > 0:
            expected_trial_blockers.add("ROW_CROSSING_OR_OVERLAP")
        if trial["self_intersection_count"] > 0:
            expected_trial_blockers.add("ROW_SELF_INTERSECTION")
        if trial["loop_count"] > 0:
            expected_trial_blockers.add("CLOSED_LOOP_WITHOUT_APPROVED_ENTRY")
        if radius_declared and trial["radius_status"] != "PASS":
            expected_trial_blockers.add("MINIMUM_WORK_PATH_RADIUS_VIOLATION")
        if spacing_error is None or spacing_error > float(parameters["extraction"]["spacing_tolerance_m"]) + 1e-9:
            expected_trial_blockers.add("SPACING_P95_OUTSIDE_CF0_TOLERANCE")
        if final_outside is None or final_outside > float(parameters["extraction"]["maximum_spacing_outside_tolerance_fraction"]) + 1e-12:
            expected_trial_blockers.add("SPACING_OUTSIDE_TOLERANCE_AREA_EXCESS")
        coverage = trial["coverage_proxy_ratio"]
        if coverage is None or not (
            float(parameters["extraction"]["coverage_proxy_minimum"]) - 1e-9
            <= float(coverage)
            <= float(parameters["extraction"]["coverage_proxy_maximum"]) + 1e-9
        ):
            expected_trial_blockers.add("COVERAGE_RATIO_OUTSIDE_CF0_ENVELOPE")
        require(expected_trial_blockers <= set(trial_blockers), f"{trial_label} omits blockers derived from trial metrics: {sorted(expected_trial_blockers - set(trial_blockers))}")
        if trial_status == "GEOMETRIC_PASS":
            require(trial["extracted_row_count"] > 0 and trial_length > 0, f"{trial_label} passes without extracted rows.")
            require(not expected_trial_blockers, f"{trial_label} passes failed trial gates.")
        validated_attempts.append(trial)

    passing = [
        trial
        for trial in validated_attempts
        if trial["geometric_status"] == "GEOMETRIC_PASS" and not trial["blocker_codes"]
    ]
    if passing:
        materialized = min(passing, key=_phase_trial_selection_key)
        require(status == "SELECTED_GEOMETRIC_PASS", f"{label} did not select an available passing phase offset.")
        require(role == "SELECTED_PASS", f"{label} has the wrong selected phase role.")
        require(selection["diagnostic_phase_offset_fraction"] is None and selection["diagnostic_phase_offset_m"] is None, f"{label} retains diagnostic offsets after a pass.")
        require_same_number(selection["selected_phase_offset_fraction"], materialized["phase_offset_fraction"], f"{label}.selected_phase_offset_fraction")
        require_same_number(selection["selected_phase_offset_m"], materialized["phase_offset_m"], f"{label}.selected_phase_offset_m")
    else:
        materialized = validated_attempts[0]
        require(status == "NO_FEASIBLE_PHASE_OFFSET", f"{label} claims a selected offset without a passing trial.")
        require(role == "DIAGNOSTIC_ONLY", f"{label} has the wrong diagnostic phase role.")
        require(selection["selected_phase_offset_fraction"] is None and selection["selected_phase_offset_m"] is None, f"{label} selects a failed phase offset.")
        require_same_number(selection["diagnostic_phase_offset_fraction"], materialized["phase_offset_fraction"], f"{label}.diagnostic_phase_offset_fraction")
        require_same_number(selection["diagnostic_phase_offset_m"], materialized["phase_offset_m"], f"{label}.diagnostic_phase_offset_m")
        require(candidate["row_count"] == 0 and candidate["geometric_status"] == "NO_FEASIBLE_FAMILY", f"{label} publishes rows when no phase offset passes.")

    require_same_number(candidate["phase_offset_fraction"], materialized["phase_offset_fraction"], f"{label}.phase_offset_fraction")
    require_same_number(candidate["phase_offset_m"], materialized["phase_offset_m"], f"{label}.phase_offset_m")
    require(candidate["geometric_status"] == materialized["geometric_status"], f"{label} status disagrees with the materialized phase trial.")
    require(candidate["blocker_codes"] == materialized["blocker_codes"], f"{label} blockers disagree with the materialized phase trial.")
    if candidate["geometric_status"] == "GEOMETRIC_PASS":
        require(candidate["row_count"] == materialized["extracted_row_count"], f"{label} row count disagrees with selected trial.")
        require_same_number(candidate["total_length_m"], materialized["extracted_total_length_m"], f"{label}.total_length_m")
    else:
        require(candidate["diagnostic_row_count"] == materialized["extracted_row_count"], f"{label} diagnostic row count disagrees with selected trial.")
        require_same_number(candidate["diagnostic_total_length_m"], materialized["extracted_total_length_m"], f"{label}.diagnostic_total_length_m")
    trial_metric_links = {
        "minimum_radius_m": "minimum_radius_m",
        "radius_status": "radius_status",
        "spacing_error_p95_m": "spacing_error_p95_m",
        "coverage_proxy_ratio": "coverage_proxy_ratio",
        "internal_endpoint_count": "internal_endpoint_count",
        "intersection_count": "intersection_count",
        "self_intersection_count": "self_intersection_count",
        "loop_count": "loop_count",
    }
    for trial_key, metric_key in trial_metric_links.items():
        trial_value = materialized[trial_key]
        metric_value = candidate["metrics"][metric_key]
        if isinstance(trial_value, str) or isinstance(trial_value, int):
            require(metric_value == trial_value, f"{label}.{metric_key} disagrees with selected trial.")
        else:
            require_same_number(metric_value, trial_value, f"{label}.{metric_key}")
    require(candidate["metrics"]["solver_gate_metrics"]["spline_failure_count"] == materialized["spline_failure_count"], f"{label} spline-failure count disagrees with selected trial.")
    require_same_number(candidate["metrics"]["solver_gate_metrics"]["final_spacing_outside_tolerance_fraction"], materialized["final_spacing_outside_tolerance_fraction"], f"{label}.final_spacing_outside_tolerance_fraction")
    return materialized


def validate_candidate_results(
    candidates_value: Any,
    parameters: dict[str, Any],
    work_block_fields: dict[str, set[str]],
) -> list[dict[str, Any]]:
    candidates_raw = require_array(candidates_value, "candidates", nonempty=True)
    candidates: list[dict[str, Any]] = []
    keys: set[tuple[str, str, str]] = set()
    family_ids: set[str] = set()
    ids_by_field_block: dict[tuple[str, str], set[str]] = defaultdict(set)
    ids_by_work_block: dict[str, set[str]] = defaultdict(set)
    radius_declared = parameters["radius_requirement_status"] == "DECLARED"
    radius_requirement = parameters["minimum_work_path_radius_m"]
    phase_parameters = parameters["phase"]
    spacing_tolerance = float(parameters["extraction"]["spacing_tolerance_m"])

    metric_keys = {
        "spacing_error_p95_m",
        "eikonal_residual_p95",
        "integrability_residual_p95",
        "orientation_misalignment_p95_deg",
        "minimum_radius_m",
        "radius_status",
        "maximum_abs_grade_pct",
        "internal_endpoint_count",
        "intersection_count",
        "self_intersection_count",
        "loop_count",
        "coverage_proxy_ratio",
        "solver_gate_metrics",
    }
    for index, candidate_value in enumerate(candidates_raw):
        label = f"candidates[{index}]"
        candidate = require_object(candidate_value, label)
        require_keys(
            candidate,
            label,
            {
                "candidate_id",
                "field_id",
                "work_block_id",
                "family_id",
                "phase_offset_m",
                "phase_offset_fraction",
                "phase_offset_role",
                "phase_offset_selection",
                "contour_extrapolation_qa",
                "geometric_status",
                "hydraulic_status",
                "row_count",
                "total_length_m",
                "diagnostic_row_count",
                "diagnostic_total_length_m",
                "metrics",
                "blocker_codes",
            },
        )
        candidate_id = candidate["candidate_id"]
        field_id = candidate["field_id"]
        work_block_id = candidate["work_block_id"]
        family_id = candidate["family_id"]
        require(candidate_id in EXPECTED_CANDIDATES, f"Unknown candidate_id: {candidate_id}")
        require(all(isinstance(value, str) and value for value in (field_id, work_block_id, family_id)), f"Invalid identifiers in {label}.")
        require(work_block_id in work_block_fields, f"Unknown work_block_id: {work_block_id}")
        require(field_id in work_block_fields[work_block_id], f"Field {field_id} is not part of {work_block_id}.")
        key = (candidate_id, field_id, work_block_id)
        require(key not in keys, f"Duplicate candidate/field/work-block result: {key}")
        require(family_id not in family_ids, f"Duplicate family_id: {family_id}")
        keys.add(key)
        family_ids.add(family_id)
        ids_by_field_block[(field_id, work_block_id)].add(candidate_id)
        ids_by_work_block[work_block_id].add(candidate_id)

        geometric_status = candidate["geometric_status"]
        require(geometric_status in {"GEOMETRIC_PASS", "NO_FEASIBLE_FAMILY"}, f"Invalid geometric status in {family_id}.")
        require(candidate["hydraulic_status"] == "HYDRAULIC_UNCONFIRMED", f"CF0 cannot approve hydraulics: {family_id}")
        require(
            isinstance(candidate["row_count"], int)
            and not isinstance(candidate["row_count"], bool)
            and candidate["row_count"] >= 0,
            f"Invalid row_count in {family_id}.",
        )
        total_length = finite_number(candidate["total_length_m"], f"{family_id}.total_length_m", minimum=0)
        require(
            isinstance(candidate["diagnostic_row_count"], int)
            and not isinstance(candidate["diagnostic_row_count"], bool)
            and candidate["diagnostic_row_count"] >= 0,
            f"Invalid diagnostic_row_count in {family_id}.",
        )
        diagnostic_total_length = finite_number(
            candidate["diagnostic_total_length_m"],
            f"{family_id}.diagnostic_total_length_m",
            minimum=0,
        )
        blockers = require_array(candidate["blocker_codes"], f"{family_id}.blocker_codes")
        require(all(isinstance(code, str) and code for code in blockers), f"Invalid blocker code in {family_id}.")
        require(blockers == sorted(set(blockers)), f"Blocker codes must be unique and sorted in {family_id}.")
        require("C2_SPLINE_FAILED" not in blockers, f"Candidate {family_id} uses the retired spline blocker.")
        below_grid_support = "WORK_BLOCK_BELOW_CF0_GRID_SUPPORT" in blockers

        metrics = require_object(candidate["metrics"], f"{family_id}.metrics")
        require_keys(metrics, f"{family_id}.metrics", metric_keys)
        numeric_nullable = (
            "spacing_error_p95_m",
            "eikonal_residual_p95",
            "integrability_residual_p95",
            "orientation_misalignment_p95_deg",
            "minimum_radius_m",
            "maximum_abs_grade_pct",
            "coverage_proxy_ratio",
        )
        for metric in numeric_nullable:
            nullable_finite(metrics[metric], f"{family_id}.{metric}")
        for metric in ("internal_endpoint_count", "intersection_count", "self_intersection_count", "loop_count"):
            require(
                isinstance(metrics[metric], int)
                and not isinstance(metrics[metric], bool)
                and metrics[metric] >= 0,
                f"Invalid {metric} in {family_id}.",
            )
        require(metrics["radius_status"] in {"PASS", "FAIL", "NOT_EVALUATED"}, f"Invalid radius_status in {family_id}.")
        solver_metrics = require_object(
            metrics["solver_gate_metrics"], f"{family_id}.solver_gate_metrics"
        )
        require_keys(
            solver_metrics,
            f"{family_id}.solver_gate_metrics",
            SOLVER_GATE_METRIC_FIELDS,
        )
        for metric in SOLVER_GATE_METRIC_FIELDS - {
            "solve_mask_component_count",
            "spline_failure_count",
        }:
            nullable_finite(solver_metrics[metric], f"{family_id}.{metric}")
        for metric in (
            "low_coherence_fraction",
            "abrupt_edge_fraction",
            "cycle_conflict_fraction",
            "critical_fraction",
            "singular_fraction",
            "cut_locus_fraction",
            "solver_spacing_outside_tolerance_fraction",
            "final_spacing_outside_tolerance_fraction",
        ):
            value = solver_metrics[metric]
            require(value is None or float(value) <= 1, f"{family_id}.{metric} must be <= 1.")
        require(
            isinstance(solver_metrics["spline_failure_count"], int)
            and not isinstance(solver_metrics["spline_failure_count"], bool)
            and solver_metrics["spline_failure_count"] >= 0,
            f"Invalid spline_failure_count in {family_id}.",
        )
        solve_mask_component_count = solver_metrics["solve_mask_component_count"]
        if below_grid_support:
            require(
                solve_mask_component_count is None,
                f"{family_id} evaluates solve-mask connectivity below grid support.",
            )
            solve_mask_disconnected = False
        else:
            require(
                isinstance(solve_mask_component_count, int)
                and not isinstance(solve_mask_component_count, bool)
                and solve_mask_component_count >= 1,
                f"{family_id}.solve_mask_component_count must be a positive integer.",
            )
            solve_mask_disconnected = (
                solve_mask_component_count
                != phase_parameters["solve_mask_required_component_count"]
            )
        require(
            ("WORK_BLOCK_SOLVE_MASK_DISCONNECTED" in blockers)
            == solve_mask_disconnected,
            f"Candidate {family_id} solve-mask connectivity blocker disagrees with its count.",
        )
        extrapolation_status = validate_contour_extrapolation_qa(
            candidate["contour_extrapolation_qa"],
            parameters["extraction"],
            f"{family_id}.contour_extrapolation_qa",
            below_grid_support=below_grid_support,
        )
        require(
            ("PHASE_HALO_GRADIENT_UNESTIMABLE" in blockers)
            == (extrapolation_status == "FAIL_CLOSED"),
            f"Candidate {family_id} phase-halo blocker disagrees with extrapolation QA.",
        )
        require(
            ("ROW_SPLINE_FIT_FAILED" in blockers)
            == (solver_metrics["spline_failure_count"] > 0),
            f"Candidate {family_id} spline blocker disagrees with spline_failure_count.",
        )
        validate_phase_offset_selection(candidate, parameters, family_id)

        derived_blockers: set[str] = set()
        metric_gates_evaluable = not below_grid_support
        if metric_gates_evaluable:
            if metrics["spacing_error_p95_m"] is None or float(metrics["spacing_error_p95_m"]) > spacing_tolerance + 1e-9:
                derived_blockers.add("SPACING_P95_OUTSIDE_CF0_TOLERANCE")
            if metrics["eikonal_residual_p95"] is None or float(metrics["eikonal_residual_p95"]) > float(phase_parameters["eikonal_residual_tolerance"]) + 1e-12:
                derived_blockers.add("EIKONAL_P95_OUTSIDE_CF0_TOLERANCE")
            if metrics["integrability_residual_p95"] is None or float(metrics["integrability_residual_p95"]) > float(phase_parameters["integrability_residual_tolerance"]) + 1e-12:
                derived_blockers.add("INTEGRABILITY_P95_OUTSIDE_CF0_TOLERANCE")
            if metrics["orientation_misalignment_p95_deg"] is None or float(metrics["orientation_misalignment_p95_deg"]) > float(parameters["orientation"]["maximum_frustration_deg"]) + 1e-12:
                derived_blockers.add("ORIENTATION_P95_OUTSIDE_CF0_TOLERANCE")
            coverage_proxy = metrics["coverage_proxy_ratio"]
            extraction = parameters["extraction"]
            if coverage_proxy is None or not (
                float(extraction["coverage_proxy_minimum"]) - 1e-9
                <= float(coverage_proxy)
                <= float(extraction["coverage_proxy_maximum"]) + 1e-9
            ):
                derived_blockers.add("COVERAGE_RATIO_OUTSIDE_CF0_ENVELOPE")
            solver_gate_map = (
                ("low_coherence_fraction", parameters["orientation"]["maximum_low_coherence_fraction"], "LOW_ORIENTATION_COHERENCE"),
                ("abrupt_edge_fraction", parameters["orientation"]["maximum_abrupt_edge_fraction"], "AXIAL_ORIENTATION_DISCONTINUITY"),
                ("cycle_conflict_fraction", parameters["orientation"]["maximum_cycle_conflict_fraction"], "AXIAL_LIFT_CYCLE_CONFLICT"),
                ("integrability_residual_rms", phase_parameters["maximum_integrability_residual_rms"], "NONINTEGRABLE_ORIENTATION_RESIDUAL"),
                ("critical_fraction", phase_parameters["maximum_critical_fraction"], "CRITICAL_PHASE_GRADIENT"),
                ("singular_fraction", parameters["orientation"]["maximum_low_coherence_fraction"], "ORIENTATION_SINGULARITY"),
                ("cut_locus_fraction", phase_parameters["maximum_cut_locus_fraction"], "CUT_LOCUS_INDICATOR"),
            )
            for metric_name, limit, blocker_code in solver_gate_map:
                value = solver_metrics[metric_name]
                if value is None or float(value) > float(limit) + 1e-12:
                    derived_blockers.add(blocker_code)
            solver_p05 = solver_metrics["solver_spacing_p05_m"]
            solver_p95 = solver_metrics["solver_spacing_p95_m"]
            if (
                solver_p05 is None
                or solver_p95 is None
                or max(abs(float(solver_p05) - float(parameters["row_spacing_m"])), abs(float(solver_p95) - float(parameters["row_spacing_m"])))
                > spacing_tolerance + 1e-9
            ):
                derived_blockers.add("SOLVER_NORMAL_SPACING_OUTSIDE_CF0_TOLERANCE")
            solver_outside = solver_metrics["solver_spacing_outside_tolerance_fraction"]
            if (
                solver_outside is None
                or float(solver_outside)
                > float(extraction["maximum_spacing_outside_tolerance_fraction"]) + 1e-12
            ):
                derived_blockers.add("SOLVER_NORMAL_SPACING_AREA_EXCESS")
            final_outside = solver_metrics["final_spacing_outside_tolerance_fraction"]
            if (
                final_outside is None
                or float(final_outside)
                > float(extraction["maximum_spacing_outside_tolerance_fraction"]) + 1e-12
            ):
                derived_blockers.add("SPACING_OUTSIDE_TOLERANCE_AREA_EXCESS")
            if solver_metrics["spline_failure_count"] > 0:
                derived_blockers.add("ROW_SPLINE_FIT_FAILED")
            if extrapolation_status == "FAIL_CLOSED":
                derived_blockers.add("PHASE_HALO_GRADIENT_UNESTIMABLE")
            if solve_mask_disconnected:
                derived_blockers.add("WORK_BLOCK_SOLVE_MASK_DISCONNECTED")
        if metrics["internal_endpoint_count"] > 0:
            derived_blockers.add("INTERNAL_UNSUPPORTED_ENDPOINT")
        if metrics["intersection_count"] > 0:
            derived_blockers.update({"ROW_CROSSING", "ROW_CROSSING_OR_OVERLAP"})
        if metrics["self_intersection_count"] > 0:
            derived_blockers.add("ROW_SELF_INTERSECTION")
        if metrics["loop_count"] > 0:
            derived_blockers.add("CLOSED_LOOP_WITHOUT_APPROVED_ENTRY")
        if radius_declared and metrics["radius_status"] != "PASS":
            derived_blockers.add("MINIMUM_WORK_PATH_RADIUS_VIOLATION")
        require(
            derived_blockers <= set(blockers),
            f"Candidate {family_id} omits blockers derivable from its metrics: {sorted(derived_blockers - set(blockers))}",
        )

        if geometric_status == "GEOMETRIC_PASS":
            require(candidate["row_count"] > 0 and total_length > 0, f"Passed family {family_id} is empty.")
            require(
                candidate["diagnostic_row_count"] == 0 and diagnostic_total_length == 0,
                f"Passed family {family_id} contains diagnostic-only rows.",
            )
            require(not blockers, f"Passed family {family_id} still has geometric blockers.")
            for metric in (
                "spacing_error_p95_m",
                "eikonal_residual_p95",
                "integrability_residual_p95",
                "orientation_misalignment_p95_deg",
            ):
                require(metrics[metric] is not None, f"Passed family {family_id} lacks {metric}.")
            require(float(metrics["spacing_error_p95_m"]) <= spacing_tolerance + 1e-9, f"Spacing gate failed in {family_id}.")
            require(float(metrics["eikonal_residual_p95"]) <= float(phase_parameters["eikonal_residual_tolerance"]) + 1e-12, f"Eikonal gate failed in {family_id}.")
            require(float(metrics["integrability_residual_p95"]) <= float(phase_parameters["integrability_residual_tolerance"]) + 1e-12, f"Integrability gate failed in {family_id}.")
            require(float(metrics["orientation_misalignment_p95_deg"]) <= float(parameters["orientation"]["maximum_frustration_deg"]) + 1e-12, f"Orientation gate failed in {family_id}.")
            require(
                float(parameters["extraction"]["coverage_proxy_minimum"]) - 1e-9
                <= float(metrics["coverage_proxy_ratio"])
                <= float(parameters["extraction"]["coverage_proxy_maximum"]) + 1e-9,
                f"Coverage proxy gate failed in {family_id}.",
            )
            for metric in ("internal_endpoint_count", "intersection_count", "self_intersection_count", "loop_count"):
                require(metrics[metric] == 0, f"Topology gate {metric} failed in {family_id}.")
            if radius_declared:
                require(metrics["radius_status"] == "PASS", f"Radius was not passed in {family_id}.")
                if metrics["minimum_radius_m"] is not None:
                    require(float(metrics["minimum_radius_m"]) + 1e-9 >= float(radius_requirement), f"Minimum radius gate failed in {family_id}.")
            else:
                require(metrics["radius_status"] == "NOT_EVALUATED", f"Undeclared fleet radius cannot be marked PASS in {family_id}.")
        else:
            require(candidate["row_count"] == 0 and total_length == 0, f"NO_FEASIBLE family {family_id} contains rows.")
            require(bool(blockers), f"NO_FEASIBLE family {family_id} lacks blockers.")
            require(
                (candidate["diagnostic_row_count"] == 0)
                == math.isclose(diagnostic_total_length, 0.0, abs_tol=1e-12),
                f"Diagnostic count/length emptiness disagrees in {family_id}.",
            )
        candidates.append(candidate)

    for field_block, candidate_ids in ids_by_field_block.items():
        require(candidate_ids == EXPECTED_CANDIDATES, f"Field/work block {field_block} does not contain all three CF0 candidates.")
    require(set(ids_by_work_block) == set(work_block_fields), "Candidate results do not cover every declared work block.")
    for work_block_id, candidate_ids in ids_by_work_block.items():
        require(candidate_ids == EXPECTED_CANDIDATES, f"Work block {work_block_id} does not contain exactly the three CF0 candidates.")
    return candidates


def validate_stage_counts(manifest: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    qa = require_object(manifest["qa"], "qa")
    require_keys(
        qa,
        "qa",
        {
            "candidate_field_count",
            "geometric_pass_count",
            "no_feasible_family_count",
            "hydraulic_unconfirmed_count",
            "radius_not_evaluated_count",
            "invalid_geometry_count",
            "non_3d_geometry_count",
            "internal_endpoint_count",
            "intersection_count",
            "nonfinite_coordinate_count",
            "raster_invalid_count",
            "gate_failures",
        },
    )
    expected = {
        "candidate_field_count": len(candidates),
        "geometric_pass_count": sum(item["geometric_status"] == "GEOMETRIC_PASS" for item in candidates),
        "no_feasible_family_count": sum(item["geometric_status"] == "NO_FEASIBLE_FAMILY" for item in candidates),
        "hydraulic_unconfirmed_count": len(candidates),
        "radius_not_evaluated_count": sum(item["metrics"]["radius_status"] == "NOT_EVALUATED" for item in candidates),
        "internal_endpoint_count": sum(item["metrics"]["internal_endpoint_count"] for item in candidates),
        "intersection_count": sum(item["metrics"]["intersection_count"] for item in candidates),
    }
    for key, value in expected.items():
        require(qa[key] == value, f"qa.{key} disagrees with candidate results.")
    for key in ("invalid_geometry_count", "non_3d_geometry_count", "nonfinite_coordinate_count", "raster_invalid_count"):
        require(isinstance(qa[key], int) and qa[key] >= 0, f"qa.{key} is invalid.")
    gate_failures = require_array(qa["gate_failures"], "qa.gate_failures")
    require(
        all(isinstance(code, str) and code for code in gate_failures),
        "qa.gate_failures contains an invalid code.",
    )
    expected_gate_failures = sorted(
        {code for candidate in candidates for code in candidate["blocker_codes"]}
    )
    require(
        gate_failures == expected_gate_failures,
        "qa.gate_failures must equal the sorted union of candidate blocker_codes.",
    )

    pass_count = expected["geometric_pass_count"]
    expected_stage_status = "HYDRAULIC_UNCONFIRMED" if pass_count else "NO_FEASIBLE_FAMILY"
    require(manifest["stage_status"] == expected_stage_status, "stage_status disagrees with candidate results.")

    layer_counts = require_object(manifest["layer_counts"], "layer_counts")
    require_keys(layer_counts, "layer_counts", EXPECTED_LAYERS)
    require(layer_counts["continuous_rows"] == sum(item["row_count"] for item in candidates), "continuous_rows count disagrees with candidates.")
    require(
        layer_counts["diagnostic_rows"]
        == sum(item["diagnostic_row_count"] for item in candidates),
        "diagnostic_rows count disagrees with candidates.",
    )
    require(layer_counts["family_summary"] == len(candidates), "family_summary count disagrees with candidates.")
    require(layer_counts["hydraulic_precheck"] == len(candidates), "hydraulic_precheck count disagrees with candidates.")


def validate_output_records(outputs_value: Any, candidates: list[dict[str, Any]], manifest_path: Path) -> None:
    outputs = require_object(outputs_value, "outputs")
    require_keys(outputs, "outputs", {"geopackage", "map", "rasters"})
    validate_file_record(outputs["geopackage"], "outputs.geopackage", manifest_path, extra_keys={"layer", "role"})
    validate_file_record(outputs["map"], "outputs.map", manifest_path, extra_keys={"layer", "role"})
    rasters = require_array(outputs["rasters"], "outputs.rasters", nonempty=True)
    expected_pairs = {
        (item["candidate_id"], item["field_id"], item["work_block_id"])
        for item in candidates
    }
    seen: set[tuple[str, str, str, str]] = set()
    for index, raster_value in enumerate(rasters):
        label = f"outputs.rasters[{index}]"
        record = require_object(raster_value, label)
        require_keys(
            record,
            label,
            {
                "path",
                "size_bytes",
                "sha256",
                "candidate_id",
                "field_id",
                "work_block_id",
                "raster_role",
                "width",
                "height",
                "band_count",
                "geotransform",
                "nodata",
            },
        )
        validate_file_record(
            {key: record[key] for key in ("path", "size_bytes", "sha256")},
            label,
            manifest_path,
        )
        pair = (record["candidate_id"], record["field_id"], record["work_block_id"])
        require(pair in expected_pairs, f"Raster refers to unknown candidate/field/work-block: {pair}")
        require(record["raster_role"] in {"orientation_coherence", "phase"}, f"Invalid raster role in {label}.")
        key = (*pair, record["raster_role"])
        require(key not in seen, f"Duplicate raster role: {key}")
        seen.add(key)
        require(isinstance(record["width"], int) and record["width"] > 0, f"Invalid raster width in {label}.")
        require(isinstance(record["height"], int) and record["height"] > 0, f"Invalid raster height in {label}.")
        expected_bands = 2 if record["raster_role"] == "orientation_coherence" else 1
        require(record["band_count"] == expected_bands, f"{record['raster_role']} must have {expected_bands} band(s).")
        gt = require_array(record["geotransform"], f"{label}.geotransform")
        require(len(gt) == 6 and all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in gt), f"Invalid geotransform in {label}.")
        nullable_finite(record["nodata"], f"{label}.nodata", minimum=-math.inf)
    expected = {(*pair, role) for pair in expected_pairs for role in ("orientation_coherence", "phase")}
    require(seen == expected, f"Raster set is incomplete or unexpected: missing={sorted(expected - seen)}")


def expected_spatial_reference(epsg: int) -> osr.SpatialReference:
    spatial_ref = osr.SpatialReference()
    require(spatial_ref.ImportFromEPSG(epsg) == 0, f"Could not import EPSG:{epsg}.")
    if hasattr(spatial_ref, "SetAxisMappingStrategy"):
        spatial_ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return spatial_ref


def spatial_refs_match(actual: osr.SpatialReference | None, expected: osr.SpatialReference) -> bool:
    if actual is None:
        return False
    if hasattr(actual, "SetAxisMappingStrategy"):
        actual.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return bool(actual.IsSame(expected))


def validate_source_crs_integrity(
    inputs: dict[str, Any], crs: dict[str, Any], manifest_path: Path
) -> None:
    expected = expected_spatial_reference(int(crs["code"]))
    work_record = inputs["work_area"]
    work_source = ogr.Open(str(resolve_path(work_record["path"], manifest_path)))
    require(work_source is not None, "Could not reopen work area for CRS integrity.")
    work_layer = (
        work_source.GetLayerByName(work_record.get("layer"))
        if work_record.get("layer")
        else work_source.GetLayer(0)
    )
    require(work_layer is not None, "Could not reopen work-area layer for CRS integrity.")
    source_srs = work_layer.GetSpatialRef()
    require(source_srs is not None, "Work area has no CRS for integrity validation.")
    source_wkt = source_srs.ExportToWkt()
    source_wkt_hash = hashlib.sha256(source_wkt.encode("utf-8")).hexdigest()
    require(
        str(crs["wkt_sha256"]).lower() == source_wkt_hash,
        "crs.wkt_sha256 disagrees with the resolved FIELD_BOUNDARY WKT.",
    )
    require(spatial_refs_match(source_srs, expected), "FIELD_BOUNDARY CRS disagrees with crs.code.")

    terrain_record = inputs["terrain_dtm"]
    terrain = gdal.Open(str(resolve_path(terrain_record["path"], manifest_path)), gdal.GA_ReadOnly)
    require(terrain is not None, "Could not reopen terrain DTM for CRS integrity.")
    terrain_wkt = terrain.GetProjection()
    require(bool(terrain_wkt), "Terrain DTM has no CRS.")
    terrain_srs = osr.SpatialReference()
    require(terrain_srs.ImportFromWkt(terrain_wkt) == 0, "Terrain DTM CRS is invalid.")
    require(spatial_refs_match(terrain_srs, expected), "Terrain DTM CRS disagrees with crs.code.")
    terrain = None
    work_source = None


def layer_field_names(layer: ogr.Layer) -> set[str]:
    definition = layer.GetLayerDefn()
    return {definition.GetFieldDefn(index).GetName() for index in range(definition.GetFieldCount())}


def feature_value(feature: ogr.Feature, name: str) -> Any:
    index = feature.GetFieldIndex(name)
    require(index >= 0, f"Missing field {name}.")
    if not feature.IsFieldSetAndNotNull(index):
        return None
    return feature.GetField(index)


def validate_row_phase_fields(
    feature: ogr.Feature,
    candidate: dict[str, Any],
    parameters: dict[str, Any],
    label: str,
) -> None:
    phase_level_index = feature_value(feature, "phase_level_index")
    require(
        isinstance(phase_level_index, int)
        and not isinstance(phase_level_index, bool),
        f"{label}.phase_level_index must be a signed integer.",
    )
    phase_level_m = finite_number(
        feature_value(feature, "phase_level_m"), f"{label}.phase_level_m"
    )
    phase_offset_m = finite_number(
        feature_value(feature, "phase_offset_m"),
        f"{label}.phase_offset_m",
        minimum=0,
    )
    phase_offset_fraction = finite_number(
        feature_value(feature, "phase_offset_fraction"),
        f"{label}.phase_offset_fraction",
        minimum=0,
    )
    require(
        any(
            math.isclose(
                phase_offset_fraction, expected, rel_tol=0, abs_tol=1e-12
            )
            for expected in PHASE_OFFSET_FRACTIONS
        ),
        f"{label}.phase_offset_fraction is not a configured quarter-spacing offset.",
    )
    require_same_number(
        phase_offset_m,
        phase_offset_fraction * float(parameters["row_spacing_m"]),
        f"{label}.phase_offset_m",
    )
    require_same_number(
        phase_offset_m,
        candidate["phase_offset_m"],
        f"{label}.phase_offset_m",
    )
    require_same_number(
        phase_offset_fraction,
        candidate["phase_offset_fraction"],
        f"{label}.phase_offset_fraction",
    )
    expected_level = (
        phase_level_index * float(parameters["row_spacing_m"])
        + phase_offset_m
    )
    require_same_number(
        phase_level_m,
        expected_level,
        f"{label}.phase_level_m",
    )


def iter_polygons(geometry: Any) -> Iterable[Polygon]:
    if isinstance(geometry, Polygon):
        yield geometry
    elif isinstance(geometry, MultiPolygon):
        yield from geometry.geoms
    elif isinstance(geometry, GeometryCollection):
        for part in geometry.geoms:
            yield from iter_polygons(part)


def make_usable_geometry(
    geometry: Any, outer_headland_m: float, obstacle_clearance_m: float
) -> Any:
    """Rebuild BUFFERED_FIELD_BOUNDARY_V1 independently from the generator."""

    parts: list[Polygon] = []
    for polygon in iter_polygons(geometry):
        outer = Polygon(polygon.exterior).buffer(-outer_headland_m, join_style=2)
        if outer.is_empty:
            continue
        holes = [
            Polygon(ring).buffer(obstacle_clearance_m)
            for ring in polygon.interiors
        ]
        usable = outer.difference(unary_union(holes)) if holes else outer
        if not usable.is_empty:
            parts.extend(iter_polygons(usable))
    require(parts, "Configured headland and obstacle buffers removed a whole work block.")
    return unary_union(parts)


def minimum_rotated_width_m(geometry: Polygon) -> float:
    rectangle = geometry.minimum_rotated_rectangle
    coordinates = list(rectangle.exterior.coords)
    side_lengths = [
        math.hypot(
            coordinates[index + 1][0] - coordinates[index][0],
            coordinates[index + 1][1] - coordinates[index][1],
        )
        for index in range(4)
    ]
    return min(side_lengths) if side_lengths else 0.0


def load_work_blocks(
    manifest: dict[str, Any], manifest_path: Path, expected_srs: osr.SpatialReference
) -> dict[str, Any]:
    work_area = manifest["inputs"]["work_area"]
    require(isinstance(work_area.get("id_field"), str) and work_area["id_field"], "inputs.work_area.id_field is required for endpoint QA.")
    require(isinstance(work_area.get("field_ids"), list) and work_area["field_ids"], "inputs.work_area.field_ids is required for endpoint QA.")
    path = resolve_path(work_area["path"], manifest_path)
    source = ogr.Open(str(path))
    require(source is not None, f"Could not open work area: {path}")
    layer = source.GetLayerByName(work_area.get("layer")) if work_area.get("layer") else source.GetLayer(0)
    require(layer is not None, "Work-area layer could not be opened.")
    require(spatial_refs_match(layer.GetSpatialRef(), expected_srs), "Work-area CRS disagrees with manifest CRS.")
    require(work_area["id_field"] in layer_field_names(layer), "Work-area id_field is missing.")
    wanted = {str(value) for value in work_area["field_ids"]}
    polygons_by_field: dict[str, list[Any]] = defaultdict(list)
    layer.ResetReading()
    for feature in layer:
        field_id = feature.GetFieldAsString(work_area["id_field"])
        if field_id not in wanted:
            continue
        geometry_ogr = feature.GetGeometryRef()
        require(geometry_ogr is not None, f"Work area {field_id} has no geometry.")
        geometry = wkb.loads(bytes(geometry_ogr.ExportToWkb()))
        require(not geometry.is_empty and geometry.is_valid, f"Work area {field_id} is invalid.")
        polygons_by_field[field_id].append(geometry)
    require(set(polygons_by_field) == wanted, f"Work-area fields were not loaded: {sorted(wanted - set(polygons_by_field))}")
    field_geometries = {field_id: unary_union(parts) for field_id, parts in polygons_by_field.items()}
    work_blocks: dict[str, Any] = {}
    domain = manifest["domain_assembly"]
    outer_headland_m = float(domain["outer_headland_m"])
    obstacle_clearance_m = float(domain["obstacle_clearance_m"])
    included_by_component: dict[tuple[str, int], dict[str, Any]] = {}
    singleton_blocks = all(len(block["field_ids"]) == 1 for block in domain["work_blocks"])
    if singleton_blocks:
        for block in domain["work_blocks"]:
            key = (str(block["field_ids"][0]), int(block["component_index"]))
            require(key not in included_by_component, f"Duplicate included source component: {key}")
            included_by_component[key] = block

    reconstructed_by_field: dict[str, list[Polygon]] = {}
    for field_id, field_geometry in field_geometries.items():
        usable = make_usable_geometry(field_geometry, outer_headland_m, obstacle_clearance_m)
        reconstructed_by_field[field_id] = sorted(
            iter_polygons(usable), key=lambda polygon: (*polygon.bounds, polygon.area)
        )

    for block in domain["work_blocks"]:
        fields = block["field_ids"]
        require(all(field_id in field_geometries for field_id in fields), f"Unknown field in work block {block['work_block_id']}.")
        source_union = unary_union([field_geometries[field_id] for field_id in fields])
        usable = make_usable_geometry(
            source_union,
            outer_headland_m,
            obstacle_clearance_m,
        )
        components = sorted(
            iter_polygons(usable),
            key=lambda polygon: (*polygon.bounds, polygon.area),
        )
        component_index = int(block["component_index"])
        require(component_index <= len(components), f"component_index is outside the reconstructed work geometry for {block['work_block_id']}.")
        geometry = components[component_index - 1]
        require(not geometry.is_empty and geometry.is_valid, f"Invalid work block {block['work_block_id']}.")
        declared_area = float(block["usable_area_m2"])
        require(
            math.isclose(geometry.area, declared_area, rel_tol=1e-8, abs_tol=0.01),
            f"Usable area disagrees for work block {block['work_block_id']}.",
        )
        work_blocks[block["work_block_id"]] = geometry

    if singleton_blocks:
        filter_contract = domain["work_block_filter"]
        minimum_area = float(filter_contract["effective_minimum_area_m2"])
        minimum_width = float(filter_contract["effective_minimum_width_m"])
        grid_resolution = float(manifest["solver_parameters"]["grid_resolution_m"])
        excluded_by_component = {
            (str(item["field_id"]), int(item["component_index"])): item
            for item in domain["excluded_components"]
        }
        reconstructed_keys = {
            (field_id, component_index)
            for field_id, components in reconstructed_by_field.items()
            for component_index in range(1, len(components) + 1)
        }
        declared_keys = set(included_by_component) | set(excluded_by_component)
        require(
            declared_keys == reconstructed_keys,
            "Included/excluded work-block components are not an exact partition of the reconstructed geometry: "
            f"missing={sorted(reconstructed_keys - declared_keys)}, unexpected={sorted(declared_keys - reconstructed_keys)}",
        )
        for (field_id, component_index), geometry in (
            (key, reconstructed_by_field[key[0]][key[1] - 1])
            for key in sorted(reconstructed_keys)
        ):
            width = minimum_rotated_width_m(geometry)
            cell_count = int(math.floor(geometry.area / (grid_resolution**2) + 1e-12))
            expected_reasons: list[str] = []
            if geometry.area + 1e-9 < minimum_area:
                expected_reasons.append("AREA_BELOW_CF0_WORK_BLOCK_MINIMUM")
            if width + 1e-9 < minimum_width:
                expected_reasons.append("WIDTH_BELOW_CF0_WORK_BLOCK_MINIMUM")
            key = (field_id, component_index)
            if key in included_by_component:
                require(not expected_reasons, f"Included component {key} fails the declared work-block filter: {expected_reasons}")
                continue
            excluded = excluded_by_component[key]
            require(expected_reasons, f"Excluded component {key} passes every declared work-block filter.")
            require(
                excluded["reason_codes"] == expected_reasons,
                f"Excluded component {key} reason_codes disagree with reconstructed thresholds.",
            )
            require(
                math.isclose(float(excluded["area_m2"]), geometry.area, rel_tol=1e-8, abs_tol=0.01),
                f"Excluded component {key} area disagrees with reconstructed geometry.",
            )
            require(
                math.isclose(float(excluded["minimum_rotated_width_m"]), width, rel_tol=1e-8, abs_tol=0.01),
                f"Excluded component {key} minimum width disagrees with reconstructed geometry.",
            )
            require(
                int(excluded["estimated_grid_cell_count"]) == cell_count,
                f"Excluded component {key} estimated grid-cell count disagrees with reconstructed geometry.",
            )
    else:
        require(
            not domain["excluded_components"],
            "Cross-field work-block assembly cannot publish per-field excluded components in CF0 1.2.",
        )
    return work_blocks


def resampled_minimum_radius(line: LineString, step_m: float) -> float | None:
    if line.length <= step_m * 2:
        coordinates = list(line.coords)
    else:
        distances = np.linspace(0.0, line.length, max(3, int(math.ceil(line.length / step_m)) + 1))
        coordinates = [line.interpolate(float(distance)).coords[0] for distance in distances]
    minimum = math.inf
    for first, middle, last in zip(coordinates, coordinates[1:], coordinates[2:]):
        ax, ay = middle[0] - first[0], middle[1] - first[1]
        bx, by = last[0] - middle[0], last[1] - middle[1]
        cx, cy = last[0] - first[0], last[1] - first[1]
        a = math.hypot(ax, ay)
        b = math.hypot(bx, by)
        c = math.hypot(cx, cy)
        twice_area = abs(ax * by - ay * bx)
        if min(a, b, c) <= 1e-9 or twice_area <= 1e-10 * max(a * b, 1.0):
            continue
        radius = a * b * c / (2.0 * twice_area)
        if math.isfinite(radius):
            minimum = min(minimum, radius)
    return None if math.isinf(minimum) else minimum


def validate_conservative_declared_radius(
    declared_radius: Any,
    sampled_radius: float | None,
    tolerance_m: float,
    label: str,
    *,
    minimum_requirement_m: float | None = None,
) -> float:
    """Validate min(analytic spline radius, persisted-line radius) conservatively."""

    declared = finite_number(declared_radius, label, minimum=1e-12)
    if sampled_radius is not None:
        require(
            declared <= sampled_radius + tolerance_m,
            f"{label} is greater than the radius supported by sampled geometry.",
        )
    if minimum_requirement_m is not None:
        require(
            declared + 1e-9 >= minimum_requirement_m,
            f"{label} is below the declared fleet minimum radius.",
        )
    return declared


def count_pair_intersections(grouped: dict[tuple[str, str, str], list[LineString]]) -> dict[tuple[str, str, str], int]:
    counts: dict[tuple[str, str, str], int] = {}
    for key, geometries in grouped.items():
        if len(geometries) < 2:
            counts[key] = 0
            continue
        tree = STRtree(geometries)
        pairs: set[tuple[int, int]] = set()
        for first_index, geometry in enumerate(geometries):
            for second_index_raw in tree.query(geometry, predicate="intersects"):
                second_index = int(second_index_raw)
                if second_index <= first_index:
                    continue
                pairs.add((first_index, second_index))
        counts[key] = len(pairs)
    return counts


def validate_geopackage(
    manifest: dict[str, Any], manifest_path: Path, expected_srs: osr.SpatialReference
) -> dict[str, Any]:
    gpkg_path = resolve_path(manifest["outputs"]["geopackage"]["path"], manifest_path)
    source = ogr.Open(str(gpkg_path))
    require(source is not None, f"Could not open GeoPackage: {gpkg_path}")
    layer_names = {source.GetLayer(index).GetName() for index in range(source.GetLayerCount())}
    require(layer_names == EXPECTED_LAYERS, f"Unexpected GeoPackage layers: {sorted(layer_names)}")

    rows_layer = source.GetLayerByName("continuous_rows")
    diagnostic_layer = source.GetLayerByName("diagnostic_rows")
    summary_layer = source.GetLayerByName("family_summary")
    hydraulic_layer = source.GetLayerByName("hydraulic_precheck")
    require(ogr.GT_Flatten(rows_layer.GetGeomType()) == ogr.wkbLineString, "continuous_rows must be LineStringZ.")
    require(ogr.GT_HasZ(rows_layer.GetGeomType()) == 1, "continuous_rows layer does not declare Z.")
    require(ogr.GT_Flatten(diagnostic_layer.GetGeomType()) == ogr.wkbLineString, "diagnostic_rows must be LineStringZ.")
    require(ogr.GT_HasZ(diagnostic_layer.GetGeomType()) == 1, "diagnostic_rows layer does not declare Z.")
    require(summary_layer.GetGeomType() == ogr.wkbNone, "family_summary must be an attribute-only layer.")
    require(hydraulic_layer.GetGeomType() == ogr.wkbNone, "hydraulic_precheck must be an attribute-only layer.")
    require(spatial_refs_match(rows_layer.GetSpatialRef(), expected_srs), "continuous_rows CRS disagrees with manifest CRS.")
    require(spatial_refs_match(diagnostic_layer.GetSpatialRef(), expected_srs), "diagnostic_rows CRS disagrees with manifest CRS.")
    require(ROW_FIELDS <= layer_field_names(rows_layer), f"continuous_rows missing fields: {sorted(ROW_FIELDS - layer_field_names(rows_layer))}")
    require(DIAGNOSTIC_FIELDS <= layer_field_names(diagnostic_layer), f"diagnostic_rows missing fields: {sorted(DIAGNOSTIC_FIELDS - layer_field_names(diagnostic_layer))}")
    require(SUMMARY_FIELDS <= layer_field_names(summary_layer), f"family_summary missing fields: {sorted(SUMMARY_FIELDS - layer_field_names(summary_layer))}")
    require(HYDRAULIC_FIELDS <= layer_field_names(hydraulic_layer), f"hydraulic_precheck missing fields: {sorted(HYDRAULIC_FIELDS - layer_field_names(hydraulic_layer))}")

    declared_candidates = {
        (item["candidate_id"], item["field_id"], item["work_block_id"]): item
        for item in manifest["candidates"]
    }
    work_blocks = load_work_blocks(manifest, manifest_path, expected_srs)
    endpoint_tolerance = float(manifest["solver_parameters"]["extraction"]["endpoint_tolerance_m"])
    curvature_step = float(manifest["solver_parameters"]["extraction"]["curvature_sample_step_m"])
    radius_requirement = manifest["solver_parameters"]["minimum_work_path_radius_m"]
    row_ids: set[str] = set()
    row_indices: dict[tuple[str, str, str], set[int]] = defaultdict(set)
    row_counts: Counter[tuple[str, str, str]] = Counter()
    row_lengths: Counter[tuple[str, str, str]] = Counter()
    row_min_radii: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    grouped_geometries: dict[tuple[str, str, str], list[LineString]] = defaultdict(list)
    invalid_geometry_count = 0
    non_3d_count = 0
    nonfinite_count = 0
    internal_endpoints = Counter()
    self_intersections = Counter()
    loops = Counter()

    rows_layer.ResetReading()
    for feature in rows_layer:
        row_id = str(feature_value(feature, "row_id") or "")
        candidate_id = str(feature_value(feature, "candidate_id") or "")
        field_id = str(feature_value(feature, "field_id") or "")
        work_block_id = str(feature_value(feature, "work_block_id") or "")
        family_id = str(feature_value(feature, "family_id") or "")
        key = (candidate_id, field_id, work_block_id)
        require(key in declared_candidates, f"Row {row_id} refers to an unknown candidate result.")
        require(family_id == declared_candidates[key]["family_id"], f"Row {row_id} has the wrong family_id.")
        require(row_id and row_id not in row_ids, f"Duplicate or empty row_id: {row_id}")
        row_ids.add(row_id)
        row_index = feature_value(feature, "row_index")
        require(isinstance(row_index, int) and not isinstance(row_index, bool) and row_index >= 0, f"Invalid row_index: {row_id}")
        require(row_index not in row_indices[key], f"Duplicate row_index in family {key}: {row_index}")
        row_indices[key].add(row_index)
        validate_row_phase_fields(
            feature,
            declared_candidates[key],
            manifest["solver_parameters"],
            row_id,
        )
        declared_length = finite_number(feature_value(feature, "length_m"), f"{row_id}.length_m", minimum=0)
        require(declared_length > 0, f"Row {row_id} has zero length.")
        finite_number(feature_value(feature, "max_abs_grade_pct"), f"{row_id}.max_abs_grade_pct", minimum=0)
        grade_p95 = finite_number(feature_value(feature, "grade_p95_pct"), f"{row_id}.grade_p95_pct", minimum=0)
        require(grade_p95 <= float(feature_value(feature, "max_abs_grade_pct")) + 1e-9, f"Row {row_id} grade p95 exceeds its maximum grade.")
        reversal_count = feature_value(feature, "reversal_count")
        require(isinstance(reversal_count, int) and not isinstance(reversal_count, bool) and reversal_count >= 0, f"Invalid reversal_count: {row_id}")
        require(feature_value(feature, "geometry_status") == "GEOMETRIC_PASS", f"Published row is not GEOMETRIC_PASS: {row_id}")
        require(feature_value(feature, "hydraulic_status") == "HYDRAULIC_UNCONFIRMED", f"Published row claims hydraulic approval: {row_id}")
        require(feature_value(feature, "topology_status") == "PASS", f"Published row failed topology: {row_id}")
        require(not split_codes(feature_value(feature, "blocker_codes")), f"Published row has geometric blockers: {row_id}")
        for endpoint_name in ("start_surface", "end_surface"):
            surface = str(feature_value(feature, endpoint_name) or "")
            require(surface and surface != "INTERNAL_UNSUPPORTED", f"Unsupported endpoint on row {row_id}.")

        geometry_ogr = feature.GetGeometryRef()
        require(geometry_ogr is not None, f"Row {row_id} has no geometry.")
        if ogr.GT_Flatten(geometry_ogr.GetGeometryType()) != ogr.wkbLineString:
            invalid_geometry_count += 1
            raise ContractError(f"Row {row_id} is not a LineString.")
        if geometry_ogr.GetCoordinateDimension() < 3:
            non_3d_count += 1
            raise ContractError(f"Row {row_id} is not 3D.")
        coordinates = [geometry_ogr.GetPoint(index) for index in range(geometry_ogr.GetPointCount())]
        coordinate_failures = sum(not all(math.isfinite(float(value)) for value in point[:3]) for point in coordinates)
        nonfinite_count += coordinate_failures
        require(coordinate_failures == 0, f"Row {row_id} has non-finite XYZ coordinates.")
        geometry = wkb.loads(bytes(geometry_ogr.ExportToWkb()))
        require(not geometry.is_empty and geometry.is_valid, f"Row {row_id} has invalid geometry.")
        invalid_geometry_count += int(not geometry.is_valid)
        self_intersections[key] += int(not geometry.is_simple)
        loops[key] += int(geometry.is_ring)
        require(geometry.is_simple and not geometry.is_ring, f"Row {row_id} self-intersects or forms a loop.")
        require(abs(float(geometry.length) - declared_length) <= max(0.01, declared_length * 1e-5), f"Row {row_id} length disagrees with geometry.")

        block_geometry = work_blocks[work_block_id]
        require(block_geometry.buffer(endpoint_tolerance).covers(geometry), f"Row {row_id} leaves its work block.")
        start = geometry.coords[0]
        end = geometry.coords[-1]
        for point in (start, end):
            distance = block_geometry.boundary.distance(Point(point[:2]))
            if distance > endpoint_tolerance:
                internal_endpoints[key] += 1
        require(internal_endpoints[key] == 0, f"Row {row_id} has an internal endpoint without a physical work edge.")

        actual_radius = resampled_minimum_radius(geometry, curvature_step)
        declared_radius = feature_value(feature, "min_radius_m")
        if declared_radius is not None:
            tolerance = (
                max(
                    float(manifest["solver_parameters"]["grid_resolution_m"]) * 2.0,
                    actual_radius * 0.1,
                )
                if actual_radius is not None
                else 0.0
            )
            validate_conservative_declared_radius(
                declared_radius,
                actual_radius,
                tolerance,
                f"{row_id}.min_radius_m",
                minimum_requirement_m=(
                    float(radius_requirement) if radius_requirement is not None else None
                ),
            )
        elif radius_requirement is not None:
            require(actual_radius is None, f"Curved row {row_id} omitted its minimum radius.")
        if actual_radius is not None:
            row_min_radii[key].append(actual_radius)
            if radius_requirement is not None:
                tolerance = float(manifest["solver_parameters"]["grid_resolution_m"]) * 2.0
                require(actual_radius + tolerance >= float(radius_requirement), f"Fleet radius gate failed on row {row_id}.")

        row_counts[key] += 1
        row_lengths[key] += float(geometry.length)
        grouped_geometries[key].append(geometry)

    pair_intersections = count_pair_intersections(grouped_geometries)
    require(all(count == 0 for count in pair_intersections.values()), "Published rows intersect within a continuous family.")

    diagnostic_counts: Counter[tuple[str, str, str]] = Counter()
    diagnostic_lengths: Counter[tuple[str, str, str]] = Counter()
    diagnostic_grouped: dict[tuple[str, str, str], list[LineString]] = defaultdict(list)
    diagnostic_internal_endpoints: Counter[tuple[str, str, str]] = Counter()
    diagnostic_self_intersections: Counter[tuple[str, str, str]] = Counter()
    diagnostic_loops: Counter[tuple[str, str, str]] = Counter()
    diagnostic_layer.ResetReading()
    for feature in diagnostic_layer:
        row_id = str(feature_value(feature, "row_id") or "")
        candidate_id = str(feature_value(feature, "candidate_id") or "")
        field_id = str(feature_value(feature, "field_id") or "")
        work_block_id = str(feature_value(feature, "work_block_id") or "")
        family_id = str(feature_value(feature, "family_id") or "")
        key = (candidate_id, field_id, work_block_id)
        require(key in declared_candidates, f"Diagnostic row {row_id} refers to an unknown candidate result.")
        candidate = declared_candidates[key]
        require(candidate["geometric_status"] == "NO_FEASIBLE_FAMILY", f"Diagnostic row belongs to a passed family: {row_id}")
        require(family_id == candidate["family_id"], f"Diagnostic row {row_id} has the wrong family_id.")
        require(row_id and row_id not in row_ids, f"Duplicate or empty row_id across published/diagnostic layers: {row_id}")
        row_ids.add(row_id)
        row_index = feature_value(feature, "row_index")
        require(isinstance(row_index, int) and not isinstance(row_index, bool) and row_index >= 0, f"Invalid diagnostic row_index: {row_id}")
        require(row_index not in row_indices[key], f"Duplicate diagnostic row_index in family {key}: {row_index}")
        row_indices[key].add(row_index)
        validate_row_phase_fields(
            feature,
            candidate,
            manifest["solver_parameters"],
            row_id,
        )
        declared_length = finite_number(feature_value(feature, "length_m"), f"{row_id}.length_m", minimum=1e-12)
        maximum_grade = finite_number(feature_value(feature, "max_abs_grade_pct"), f"{row_id}.max_abs_grade_pct", minimum=0)
        grade_p95 = finite_number(feature_value(feature, "grade_p95_pct"), f"{row_id}.grade_p95_pct", minimum=0)
        require(grade_p95 <= maximum_grade + 1e-9, f"Diagnostic row {row_id} grade p95 exceeds its maximum grade.")
        reversal_count = feature_value(feature, "reversal_count")
        require(isinstance(reversal_count, int) and not isinstance(reversal_count, bool) and reversal_count >= 0, f"Invalid diagnostic reversal_count: {row_id}")
        require(feature_value(feature, "geometry_status") == "NO_FEASIBLE_FAMILY", f"Diagnostic row claims a geometric pass: {row_id}")
        require(feature_value(feature, "hydraulic_status") == "HYDRAULIC_UNCONFIRMED", f"Diagnostic row claims hydraulic approval: {row_id}")
        require(feature_value(feature, "diagnostic_status") == "NOT_APPROVED", f"Diagnostic row is not marked NOT_APPROVED: {row_id}")
        require(feature_value(feature, "guidance_status") == "NOT_AUTHORIZED", f"Diagnostic row authorizes guidance: {row_id}")
        topology_status = feature_value(feature, "topology_status")
        require(topology_status in {"PASS", "FAIL"}, f"Invalid diagnostic topology_status: {row_id}")
        blockers = split_codes(feature_value(feature, "blocker_codes"))
        require(blockers, f"Diagnostic row lacks blocker_codes: {row_id}")
        require(blockers == sorted(set(blockers)), f"Diagnostic row blocker_codes must be unique and sorted: {row_id}")
        require(set(candidate["blocker_codes"]) <= set(blockers), f"Diagnostic row omits candidate blockers: {row_id}")
        for endpoint_name in ("start_surface", "end_surface"):
            require(str(feature_value(feature, endpoint_name) or ""), f"Diagnostic row has empty {endpoint_name}: {row_id}")

        geometry_ogr = feature.GetGeometryRef()
        require(geometry_ogr is not None, f"Diagnostic row {row_id} has no geometry.")
        require(ogr.GT_Flatten(geometry_ogr.GetGeometryType()) == ogr.wkbLineString, f"Diagnostic row {row_id} is not a LineString.")
        require(geometry_ogr.GetCoordinateDimension() >= 3, f"Diagnostic row {row_id} is not 3D.")
        coordinates = [geometry_ogr.GetPoint(index) for index in range(geometry_ogr.GetPointCount())]
        require(coordinates and all(all(math.isfinite(float(value)) for value in point[:3]) for point in coordinates), f"Diagnostic row {row_id} has non-finite XYZ coordinates.")
        geometry = wkb.loads(bytes(geometry_ogr.ExportToWkb()))
        require(not geometry.is_empty and geometry.is_valid, f"Diagnostic row {row_id} has invalid geometry.")
        require(abs(float(geometry.length) - declared_length) <= max(0.01, declared_length * 1e-5), f"Diagnostic row {row_id} length disagrees with geometry.")
        require(work_blocks[work_block_id].buffer(endpoint_tolerance).covers(geometry), f"Diagnostic row {row_id} leaves its work block.")
        diagnostic_self_intersections[key] += int(not geometry.is_simple)
        diagnostic_loops[key] += int(geometry.is_ring)
        for point in (geometry.coords[0], geometry.coords[-1]):
            if work_blocks[work_block_id].boundary.distance(Point(point[:2])) > endpoint_tolerance:
                diagnostic_internal_endpoints[key] += 1
        if topology_status == "PASS":
            require(geometry.is_simple and not geometry.is_ring, f"Diagnostic row marked topology PASS is self-intersecting or closed: {row_id}")
            require(
                all(work_blocks[work_block_id].boundary.distance(Point(point[:2])) <= endpoint_tolerance for point in (geometry.coords[0], geometry.coords[-1])),
                f"Diagnostic row marked topology PASS has an unsupported endpoint: {row_id}",
            )
        actual_radius = resampled_minimum_radius(geometry, curvature_step)
        declared_radius = feature_value(feature, "min_radius_m")
        if declared_radius is not None:
            tolerance = (
                max(
                    float(manifest["solver_parameters"]["grid_resolution_m"]) * 2.0,
                    actual_radius * 0.1,
                )
                if actual_radius is not None
                else 0.0
            )
            validate_conservative_declared_radius(
                declared_radius,
                actual_radius,
                tolerance,
                f"{row_id}.min_radius_m",
            )
        diagnostic_counts[key] += 1
        diagnostic_lengths[key] += float(geometry.length)
        diagnostic_grouped[key].append(geometry)

    diagnostic_pair_intersections = count_pair_intersections(diagnostic_grouped)

    summary_records: dict[tuple[str, str, str], dict[str, Any]] = {}
    summary_layer.ResetReading()
    for feature in summary_layer:
        key = (
            str(feature_value(feature, "candidate_id") or ""),
            str(feature_value(feature, "field_id") or ""),
            str(feature_value(feature, "work_block_id") or ""),
        )
        require(key in declared_candidates and key not in summary_records, f"Unexpected or duplicate family_summary row: {key}")
        candidate = declared_candidates[key]
        require(feature_value(feature, "family_id") == candidate["family_id"], f"family_summary family_id mismatch: {key}")
        require_same_number(
            feature_value(feature, "phase_offset_m"),
            candidate["phase_offset_m"],
            f"family_summary.phase_offset_m: {key}",
        )
        require_same_number(
            feature_value(feature, "phase_offset_fraction"),
            candidate["phase_offset_fraction"],
            f"family_summary.phase_offset_fraction: {key}",
        )
        require(
            feature_value(feature, "phase_offset_role")
            == candidate["phase_offset_role"],
            f"family_summary phase_offset_role mismatch: {key}",
        )
        require(
            feature_value(feature, "phase_offset_selection_status")
            == candidate["phase_offset_selection"]["selection_status"],
            f"family_summary phase-offset selection status mismatch: {key}",
        )
        require(feature_value(feature, "geometry_status") == candidate["geometric_status"], f"family_summary geometry status mismatch: {key}")
        require(feature_value(feature, "hydraulic_status") == "HYDRAULIC_UNCONFIRMED", f"family_summary claims hydraulic approval: {key}")
        require(feature_value(feature, "radius_status") == candidate["metrics"]["radius_status"], f"family_summary radius status mismatch: {key}")
        require(feature_value(feature, "row_count") == candidate["row_count"] == row_counts[key], f"family_summary row count mismatch: {key}")
        total_length = float(feature_value(feature, "total_length_m") or 0.0)
        require(abs(total_length - candidate["total_length_m"]) <= max(0.01, total_length * 1e-6), f"family_summary manifest length mismatch: {key}")
        require(abs(total_length - row_lengths[key]) <= max(0.02, total_length * 1e-5), f"family_summary geometry length mismatch: {key}")
        require(
            feature_value(feature, "diagnostic_row_count")
            == candidate["diagnostic_row_count"]
            == diagnostic_counts[key],
            f"family_summary diagnostic row count mismatch: {key}",
        )
        diagnostic_total_length = float(feature_value(feature, "diagnostic_total_length_m") or 0.0)
        require(
            abs(diagnostic_total_length - float(candidate["diagnostic_total_length_m"]))
            <= max(0.01, diagnostic_total_length * 1e-6),
            f"family_summary diagnostic manifest length mismatch: {key}",
        )
        require(
            abs(diagnostic_total_length - diagnostic_lengths[key])
            <= max(0.02, diagnostic_total_length * 1e-5),
            f"family_summary diagnostic geometry length mismatch: {key}",
        )
        metric_mapping = {
            "spacing_p95_m": "spacing_error_p95_m",
            "eikonal_p95": "eikonal_residual_p95",
            "integrability_p95": "integrability_residual_p95",
            "orientation_p95_deg": "orientation_misalignment_p95_deg",
            "min_radius_m": "minimum_radius_m",
            "max_grade_pct": "maximum_abs_grade_pct",
            "internal_endpoints": "internal_endpoint_count",
            "intersections": "intersection_count",
            "self_intersections": "self_intersection_count",
            "loops": "loop_count",
        }
        for field_name, metric_name in metric_mapping.items():
            actual = feature_value(feature, field_name)
            declared = candidate["metrics"][metric_name]
            if declared is None:
                require(actual is None, f"family_summary {field_name} should be null: {key}")
            elif isinstance(declared, int):
                require(actual == declared, f"family_summary {field_name} mismatch: {key}")
            else:
                require(actual is not None and math.isclose(float(actual), float(declared), rel_tol=1e-6, abs_tol=1e-8), f"family_summary {field_name} mismatch: {key}")
        if candidate["geometric_status"] == "GEOMETRIC_PASS":
            require(feature_value(feature, "internal_endpoints") == internal_endpoints[key], f"Endpoint count mismatch: {key}")
            require(feature_value(feature, "intersections") == pair_intersections.get(key, 0), f"Intersection count mismatch: {key}")
            require(feature_value(feature, "self_intersections") == self_intersections[key], f"Self-intersection count mismatch: {key}")
            require(feature_value(feature, "loops") == loops[key], f"Loop count mismatch: {key}")
            require(not split_codes(feature_value(feature, "blocker_codes")), f"Passed family summary has blockers: {key}")
        else:
            require(
                set(split_codes(feature_value(feature, "blocker_codes")))
                == set(candidate["blocker_codes"]),
                f"NO_FEASIBLE summary blockers disagree with the candidate: {key}",
            )
            require(feature_value(feature, "internal_endpoints") == diagnostic_internal_endpoints[key], f"Diagnostic endpoint count mismatch: {key}")
            require(feature_value(feature, "intersections") == diagnostic_pair_intersections.get(key, 0), f"Diagnostic intersection count mismatch: {key}")
            require(feature_value(feature, "self_intersections") == diagnostic_self_intersections[key], f"Diagnostic self-intersection count mismatch: {key}")
            require(feature_value(feature, "loops") == diagnostic_loops[key], f"Diagnostic loop count mismatch: {key}")
        summary_records[key] = {"family_id": candidate["family_id"]}
    require(set(summary_records) == set(declared_candidates), "family_summary does not cover every declared candidate.")

    hydraulic_records: set[tuple[str, str, str]] = set()
    hydraulic_layer.ResetReading()
    for feature in hydraulic_layer:
        key = (
            str(feature_value(feature, "candidate_id") or ""),
            str(feature_value(feature, "field_id") or ""),
            str(feature_value(feature, "work_block_id") or ""),
        )
        require(key in declared_candidates and key not in hydraulic_records, f"Unexpected or duplicate hydraulic_precheck row: {key}")
        candidate = declared_candidates[key]
        require(feature_value(feature, "family_id") == candidate["family_id"], f"hydraulic_precheck family_id mismatch: {key}")
        require(feature_value(feature, "hydraulic_status") == "HYDRAULIC_UNCONFIRMED", f"CF0 hydraulic approval is forbidden: {key}")
        for field_name in HYDRAULIC_INPUT_FIELDS:
            require(feature_value(feature, field_name) in HYDRAULIC_INPUT_STATES, f"Invalid {field_name} in hydraulic_precheck: {key}")
        for field_name in ("grade_p95_pct", "grade_max_pct", "adverse_length_pct"):
            nullable_finite(feature_value(feature, field_name), f"hydraulic_precheck.{field_name}: {key}")
        hydraulic_grade_p95 = feature_value(feature, "grade_p95_pct")
        hydraulic_grade_max = feature_value(feature, "grade_max_pct")
        if hydraulic_grade_p95 is not None and hydraulic_grade_max is not None:
            require(float(hydraulic_grade_p95) <= float(hydraulic_grade_max) + 1e-9, f"Hydraulic grade p95 exceeds maximum: {key}")
        require(
            (hydraulic_grade_max is None and candidate["metrics"]["maximum_abs_grade_pct"] is None)
            or (
                hydraulic_grade_max is not None
                and candidate["metrics"]["maximum_abs_grade_pct"] is not None
                and math.isclose(float(hydraulic_grade_max), float(candidate["metrics"]["maximum_abs_grade_pct"]), rel_tol=1e-6, abs_tol=1e-8)
            ),
            f"hydraulic_precheck grade_max_pct disagrees with candidate metrics: {key}",
        )
        adverse_length = feature_value(feature, "adverse_length_pct")
        require(adverse_length is None or float(adverse_length) <= 100 + 1e-9, f"Invalid adverse_length_pct: {key}")
        require(feature_value(feature, "outlet_status") == feature_value(feature, "receiver_status"), f"Outlet/receiver hydraulic statuses disagree: {key}")
        reversal_count = feature_value(feature, "reversal_count")
        require(isinstance(reversal_count, int) and not isinstance(reversal_count, bool) and reversal_count >= 0, f"Invalid hydraulic reversal_count: {key}")
        expected_missing_by_status = {
            "idf_status": "RAIN_IDF_EVENT",
            "soil_status": "SOIL_INFILTRATION",
            "contributing_area_status": "COMPLETE_CONTRIBUTING_CATCHMENT",
            "section_status": "ROW_OR_STRUCTURE_SECTION",
            "roughness_status": "ROUGHNESS",
            "receiver_status": "VERIFIED_RECEIVER_NETWORK",
        }
        expected_missing = [
            code
            for status_field, code in expected_missing_by_status.items()
            if feature_value(feature, status_field) != "PROVIDED"
        ]
        missing_inputs = split_codes(feature_value(feature, "missing_inputs"))
        require(missing_inputs == expected_missing, f"hydraulic_precheck missing_inputs disagrees with input statuses: {key}")
        blockers = split_codes(feature_value(feature, "blocker_codes"))
        require(blockers, f"hydraulic_precheck lacks blockers: {key}")
        require("CF0_NO_HYDRAULIC_SOLVER" in blockers, f"hydraulic_precheck must disclose the absent hydraulic solver: {key}")
        expected_hydraulic_blockers = [
            "CF0_NO_HYDRAULIC_SOLVER",
            *[f"HYDRAULIC_INPUT_{code}_UNCONFIRMED" for code in expected_missing],
        ]
        require(blockers == expected_hydraulic_blockers, f"hydraulic_precheck blockers disagree with missing_inputs: {key}")
        hydraulic_records.add(key)
    require(hydraulic_records == set(declared_candidates), "hydraulic_precheck does not cover every declared candidate.")

    require(rows_layer.GetFeatureCount() == manifest["layer_counts"]["continuous_rows"], "continuous_rows layer count mismatch.")
    require(diagnostic_layer.GetFeatureCount() == manifest["layer_counts"]["diagnostic_rows"], "diagnostic_rows layer count mismatch.")
    require(summary_layer.GetFeatureCount() == manifest["layer_counts"]["family_summary"], "family_summary layer count mismatch.")
    require(hydraulic_layer.GetFeatureCount() == manifest["layer_counts"]["hydraulic_precheck"], "hydraulic_precheck layer count mismatch.")
    return {
        "row_count": rows_layer.GetFeatureCount(),
        "diagnostic_row_count": diagnostic_layer.GetFeatureCount(),
        "invalid_geometry_count": invalid_geometry_count,
        "non_3d_geometry_count": non_3d_count,
        "nonfinite_coordinate_count": nonfinite_count,
        "internal_endpoint_count": sum(internal_endpoints.values()),
        "intersection_count": sum(pair_intersections.values()),
        "self_intersection_count": sum(self_intersections.values()),
        "loop_count": sum(loops.values()),
    }


def valid_raster_mask(array: np.ndarray, nodata: float | None) -> np.ndarray:
    mask = np.isfinite(array)
    if nodata is not None and math.isfinite(float(nodata)):
        mask &= ~np.isclose(array, float(nodata), rtol=0, atol=1e-12)
    return mask


def undeclared_nonfinite_count(array: np.ndarray, nodata: float | None) -> int:
    nodata_mask = (
        np.isclose(array, float(nodata), rtol=0, atol=1e-12)
        if nodata is not None
        else np.zeros(array.shape, dtype=bool)
    )
    return int(np.count_nonzero(~np.isfinite(array) & ~nodata_mask))


def validate_grid_transform(
    geotransform: tuple[float, ...], resolution_m: float, label: str
) -> None:
    """Bind every published grid to the solver resolution and snap rule."""

    resolution = finite_number(
        resolution_m, "solver_parameters.grid_resolution_m", minimum=1e-12
    )
    require(len(geotransform) == 6, f"Invalid geotransform length: {label}")
    require(
        abs(geotransform[2]) <= 1e-12 and abs(geotransform[4]) <= 1e-12,
        f"Rotated raster is unsupported: {label}",
    )
    require(
        math.isclose(geotransform[1], resolution, rel_tol=0, abs_tol=1e-9)
        and math.isclose(geotransform[5], -resolution, rel_tol=0, abs_tol=1e-9),
        f"Raster pixel size disagrees with solver grid_resolution_m: {label}",
    )
    for origin, axis in ((geotransform[0], "x"), (geotransform[3], "y")):
        quotient = origin / resolution
        require(
            math.isclose(quotient, round(quotient), rel_tol=0, abs_tol=1e-8),
            f"Raster {axis}-origin is not snapped to grid_resolution_m: {label}",
        )


def rasterize_supercover_mask(
    geometry: Any,
    geotransform: tuple[float, ...],
    width: int,
    height: int,
    spatial_ref: osr.SpatialReference,
) -> np.ndarray:
    """Reconstruct the persisted solve mask without the extraction-only halo."""

    raster = gdal.GetDriverByName("MEM").Create("", width, height, 1, gdal.GDT_Byte)
    require(raster is not None, "Could not allocate in-memory solve-mask raster.")
    raster.SetGeoTransform(geotransform)
    raster.SetProjection(spatial_ref.ExportToWkt())
    datasource = ogr.GetDriverByName("Memory").CreateDataSource("")
    require(datasource is not None, "Could not allocate in-memory solve-mask datasource.")
    layer = datasource.CreateLayer("work_block", srs=spatial_ref, geom_type=ogr.wkbUnknown)
    require(layer is not None, "Could not create in-memory solve-mask layer.")
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(ogr.CreateGeometryFromWkb(geometry.wkb))
    require(layer.CreateFeature(feature) == ogr.OGRERR_NONE, "Could not stage work-block geometry for solve-mask validation.")
    raster.GetRasterBand(1).Fill(0)
    result = gdal.RasterizeLayer(
        raster,
        [1],
        layer,
        burn_values=[1],
        options=["ALL_TOUCHED=TRUE"],
    )
    require(result == 0, "Could not reconstruct ALL_TOUCHED solve mask.")
    mask = np.asarray(raster.GetRasterBand(1).ReadAsArray(), dtype=np.uint8).astype(bool)
    feature = layer = datasource = raster = None
    return mask


def validate_persisted_solve_mask(
    persisted_mask: np.ndarray, reconstructed_mask: np.ndarray, label: str
) -> None:
    require(
        persisted_mask.shape == reconstructed_mask.shape,
        f"Raster valid-cell mask shape disagrees with reconstructed solve mask: {label}",
    )
    require(
        np.array_equal(persisted_mask, reconstructed_mask),
        "Raster valid-cell mask is not the declared ALL_TOUCHED solve mask "
        f"(or includes the extraction-only halo): {label}",
    )


def solve_mask_component_count(mask: np.ndarray) -> int:
    """Count 4-connected components with the same convention as the solver."""

    boolean_mask = np.asarray(mask, dtype=bool)
    require(boolean_mask.ndim == 2, "Solve mask must be two-dimensional.")
    return int(
        ndimage.label(
            boolean_mask,
            structure=np.asarray(
                [[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.uint8
            ),
        )[1]
    )


def validate_rasters(
    manifest: dict[str, Any], manifest_path: Path, expected_srs: osr.SpatialReference
) -> dict[str, Any]:
    grids: dict[tuple[str, str, str], tuple[tuple[float, ...], int, int, str]] = {}
    valid_cell_counts: dict[str, int] = {}
    invalid_count = 0
    work_blocks = load_work_blocks(manifest, manifest_path, expected_srs)
    reconstructed_masks: dict[
        tuple[str, str, str], tuple[tuple[float, ...], int, int, np.ndarray]
    ] = {}
    declared_candidates = {
        (candidate["candidate_id"], candidate["field_id"], candidate["work_block_id"]): candidate
        for candidate in manifest["candidates"]
    }
    for record in manifest["outputs"]["rasters"]:
        path = resolve_path(record["path"], manifest_path)
        dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
        require(dataset is not None, f"Could not open raster: {path}")
        require(dataset.RasterXSize == record["width"] and dataset.RasterYSize == record["height"], f"Raster dimensions disagree with manifest: {path}")
        require(dataset.RasterCount == record["band_count"], f"Raster band count disagrees with manifest: {path}")
        geotransform = tuple(float(value) for value in dataset.GetGeoTransform())
        declared_gt = tuple(float(value) for value in record["geotransform"])
        require(all(math.isclose(a, b, rel_tol=0, abs_tol=1e-9) for a, b in zip(geotransform, declared_gt)), f"Raster geotransform mismatch: {path}")
        validate_grid_transform(
            geotransform,
            manifest["solver_parameters"]["grid_resolution_m"],
            str(path),
        )
        raster_srs = osr.SpatialReference()
        require(raster_srs.ImportFromWkt(dataset.GetProjection()) == 0, f"Raster has no valid CRS: {path}")
        require(spatial_refs_match(raster_srs, expected_srs), f"Raster CRS disagrees with manifest: {path}")

        arrays: list[np.ndarray] = []
        masks: list[np.ndarray] = []
        for band_index in range(1, dataset.RasterCount + 1):
            band = dataset.GetRasterBand(band_index)
            actual_nodata = band.GetNoDataValue()
            declared_nodata = record["nodata"]
            if declared_nodata is None:
                require(actual_nodata is None, f"Raster NoData mismatch: {path}, band {band_index}")
            else:
                require(actual_nodata is not None and math.isclose(float(actual_nodata), float(declared_nodata), rel_tol=0, abs_tol=1e-12), f"Raster NoData mismatch: {path}, band {band_index}")
            array = np.asarray(band.ReadAsArray(), dtype=float)
            mask = valid_raster_mask(array, actual_nodata)
            arrays.append(array)
            masks.append(mask)
        joint_mask = np.logical_and.reduce(masks)
        require(bool(joint_mask.any()), f"Raster has no valid cells: {path}")
        pair = (
            record["candidate_id"],
            record["field_id"],
            record["work_block_id"],
        )
        require(pair in declared_candidates, f"Raster refers to an unknown candidate: {pair}")
        require(pair[2] in work_blocks, f"Raster refers to unknown work block: {pair}")
        if pair not in reconstructed_masks:
            reconstructed_masks[pair] = (
                geotransform,
                dataset.RasterXSize,
                dataset.RasterYSize,
                rasterize_supercover_mask(
                    work_blocks[pair[2]],
                    geotransform,
                    dataset.RasterXSize,
                    dataset.RasterYSize,
                    expected_srs,
                ),
            )
        expected_gt, expected_width, expected_height, expected_mask = reconstructed_masks[pair]
        require(
            expected_width == dataset.RasterXSize
            and expected_height == dataset.RasterYSize
            and all(
                math.isclose(a, b, rel_tol=0, abs_tol=1e-9)
                for a, b in zip(expected_gt, geotransform)
            ),
            f"Candidate rasters do not share the reconstructed solve-mask grid: {pair}",
        )
        validate_persisted_solve_mask(joint_mask, expected_mask, str(path))
        candidate = declared_candidates[pair]
        if "WORK_BLOCK_BELOW_CF0_GRID_SUPPORT" not in candidate["blocker_codes"]:
            declared_component_count = candidate["metrics"]["solver_gate_metrics"][
                "solve_mask_component_count"
            ]
            require(
                declared_component_count == solve_mask_component_count(expected_mask),
                f"Candidate solve-mask component count disagrees with the reconstructed ALL_TOUCHED mask: {pair}",
            )
        for array in arrays:
            # A declared finite sentinel is ordinary NoData, not an invalid
            # pixel. NaN/Inf not represented by that declaration is invalid.
            invalid_count += undeclared_nonfinite_count(array, record["nodata"])
        if len(masks) > 1:
            first_mask = masks[0]
            invalid_count += int(
                sum(np.count_nonzero(mask != first_mask) for mask in masks[1:])
            )

        if record["raster_role"] == "orientation_coherence":
            orientation = arrays[0][joint_mask]
            coherence = arrays[1][joint_mask]
            orientation_invalid = (orientation < -1e-6) | (orientation >= 180.0 + 1e-6)
            coherence_invalid = (coherence < -1e-6) | (coherence > 1.0 + 1e-6)
            invalid_count += int(np.count_nonzero(orientation_invalid))
            invalid_count += int(np.count_nonzero(coherence_invalid))
        else:
            require(bool(np.all(np.isfinite(arrays[0][joint_mask]))), f"Phase raster contains non-finite values: {path}")
            gauge = declared_candidates[pair]["phase_offset_selection"]["phase_gauge"]
            if gauge["method"] == "ZERO_AT_LEXICOGRAPHIC_FIRST_VALID_CELL_PER_COMPONENT":
                for anchor in gauge["anchors"]:
                    raster_column = int(
                        round((float(anchor["x_m"]) - geotransform[0]) / geotransform[1] - 0.5)
                    )
                    raster_row = int(
                        round((float(anchor["y_m"]) - geotransform[3]) / geotransform[5] - 0.5)
                    )
                    require(
                        0 <= raster_row < dataset.RasterYSize
                        and 0 <= raster_column < dataset.RasterXSize,
                        f"Phase-gauge anchor lies outside the persisted raster: {pair}",
                    )
                    require(
                        raster_column == int(anchor["solver_column"])
                        and raster_row
                        == dataset.RasterYSize - 1 - int(anchor["solver_row"]),
                        f"Phase-gauge anchor indices disagree with its world coordinates: {pair}",
                    )
                    require(joint_mask[raster_row, raster_column], f"Phase-gauge anchor is outside the solve mask: {pair}")
                    require(
                        math.isclose(
                            float(arrays[0][raster_row, raster_column]),
                            float(gauge["anchor_phase_value_m"]),
                            rel_tol=0,
                            abs_tol=1e-5,
                        ),
                        f"Phase raster does not preserve the declared gauge anchor: {pair}",
                    )

        mask_digest = hashlib.sha256(np.ascontiguousarray(joint_mask).tobytes()).hexdigest()
        grid = (geotransform, dataset.RasterXSize, dataset.RasterYSize, mask_digest)
        if pair in grids:
            previous_gt, previous_width, previous_height, previous_mask_digest = grids[pair]
            require(previous_width == grid[1] and previous_height == grid[2], f"Orientation and phase grids differ: {pair}")
            require(all(math.isclose(a, b, rel_tol=0, abs_tol=1e-9) for a, b in zip(previous_gt, grid[0])), f"Orientation and phase geotransforms differ: {pair}")
            require(previous_mask_digest == mask_digest, f"Orientation and phase valid-cell masks differ: {pair}")
        else:
            grids[pair] = grid
        valid_cell_counts[f"{pair[0]}:{pair[1]}:{pair[2]}:{record['raster_role']}"] = int(np.count_nonzero(joint_mask))
        dataset = None
    require(invalid_count == manifest["qa"]["raster_invalid_count"], "qa.raster_invalid_count disagrees with raster inspection.")
    require(invalid_count == 0, "CF0 rasters contain invalid cells or inconsistent masks.")
    return {"raster_count": len(manifest["outputs"]["rasters"]), "valid_cell_counts": valid_cell_counts, "invalid_count": invalid_count}


def validate_json_schema_if_available(
    manifest: dict[str, Any], schema: dict[str, Any]
) -> str:
    """Apply Draft 2020-12 when installed; imperative checks always run too."""

    if Draft202012Validator is None:
        return "IMPERATIVE_CONTRACT_CHECKS_ONLY_JSONSCHEMA_UNAVAILABLE"
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(manifest),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        first = errors[0]
        path = ".".join(str(part) for part in first.absolute_path) or "manifest"
        raise ContractError(f"JSON Schema violation at {path}: {first.message}")
    return "JSON_SCHEMA_DRAFT_2020_12_PLUS_IMPERATIVE"


def verify_manifest_payload(
    manifest: dict[str, Any], manifest_path: Path, schema_path: Path
) -> dict[str, Any]:
    require(schema_path.is_file(), f"Schema does not exist: {schema_path}")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    require(schema.get("$id", "").endswith("continuous-family-stage.schema.json"), "Unexpected CF0 schema file.")
    schema_validation = validate_json_schema_if_available(manifest, schema)
    validate_manifest_shape(manifest, manifest_path)
    expected_srs = expected_spatial_reference(int(manifest["crs"]["code"]))
    gpkg = validate_geopackage(manifest, manifest_path, expected_srs)
    rasters = validate_rasters(manifest, manifest_path, expected_srs)

    qa = manifest["qa"]
    require(gpkg["invalid_geometry_count"] == qa["invalid_geometry_count"], "qa.invalid_geometry_count mismatch.")
    require(gpkg["non_3d_geometry_count"] == qa["non_3d_geometry_count"], "qa.non_3d_geometry_count mismatch.")
    require(gpkg["nonfinite_coordinate_count"] == qa["nonfinite_coordinate_count"], "qa.nonfinite_coordinate_count mismatch.")
    require(gpkg["internal_endpoint_count"] == 0, "Published GeoPackage contains an internal endpoint.")
    require(gpkg["intersection_count"] == 0, "Published GeoPackage contains intersecting rows.")
    return {
        "status": "VERIFIED",
        "release": RELEASE,
        "stage_status": manifest["stage_status"],
        "manifest": str(manifest_path.resolve()),
        "schema": str(schema_path.resolve()),
        "geopackage": gpkg,
        "rasters": rasters,
        "hydraulic_claim": "HYDRAULIC_UNCONFIRMED",
        "guidance_authorized": False,
        "schema_validation": schema_validation,
    }


def verify(manifest_path: Path, schema_path: Path) -> dict[str, Any]:
    require(manifest_path.is_file(), f"Manifest does not exist: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return verify_manifest_payload(manifest, manifest_path, schema_path)


def run_self_test() -> dict[str, Any]:
    checks: list[str] = []
    nodata = -9999.0
    sample = np.asarray([[1.0, nodata]], dtype=float)
    require(undeclared_nonfinite_count(sample, nodata) == 0, "Finite NoData sentinel was counted as invalid.")
    require(int(np.count_nonzero(valid_raster_mask(sample, nodata))) == 1, "NoData mask self-test failed.")
    require(undeclared_nonfinite_count(np.asarray([[math.nan]]), nodata) == 1, "Undeclared NaN self-test failed.")
    checks.append("FINITE_NODATA_IS_NOT_INVALID")

    validate_grid_transform((600.0, 6.0, 0.0, 1200.0, 0.0, -6.0), 6.0, "synthetic")
    try:
        validate_grid_transform((600.0, 6.0, 0.0, 1200.0, 0.0, -6.0), 7.0, "tampered")
    except ContractError:
        checks.append("GRID_RESOLUTION_TAMPERING_REJECTED")
    else:
        raise ContractError("Grid-resolution tampering self-test was not rejected.")

    extraction_methods = {
        "solve_mask_rasterization": "ALL_TOUCHED_SUPERCOVER",
        "contour_extrapolation_method": "FIRST_ORDER_LOCAL_LSQ_GRADIENT_FAIL_CLOSED",
        "contour_extrapolation_halo_cells": 1,
        "contour_gradient_lsq_max_radius_cells": 2,
        "contour_gradient_lsq_max_relative_residual": 0.30,
        "contour_gradient_lsq_max_condition_number": 100.0,
        "contour_gradient_lsq_minimum_neighbor_count": 3,
        "contour_gradient_lsq_required_rank": 2,
        "contour_gradient_component_connectivity": 4,
        "endpoint_extension_mode": "TANGENT_ONLY_FAIL_CLOSED",
        "spline_representation_method": "ADAPTIVE_CHORD_ERROR_PRESERVE_VERTICES",
        "spline_representation_tolerance_fraction": 0.10,
    }
    validate_extraction_method_contract(extraction_methods)
    tampered_extraction_methods = dict(extraction_methods)
    tampered_extraction_methods["contour_extrapolation_halo_cells"] = 2
    try:
        validate_extraction_method_contract(tampered_extraction_methods)
    except ContractError:
        checks.append("EXTRACTION_METHOD_TAMPERING_REJECTED")
    else:
        raise ContractError("Extraction-method tampering self-test was not rejected.")
    tampered_lsq_threshold = dict(extraction_methods)
    tampered_lsq_threshold["contour_gradient_lsq_max_relative_residual"] = 0.31
    try:
        validate_extraction_method_contract(tampered_lsq_threshold)
    except ContractError:
        checks.append("LSQ_ACCEPTANCE_THRESHOLD_TAMPERING_REJECTED")
    else:
        raise ContractError("LSQ acceptance-threshold tampering was not rejected.")
    tampered_spline_representation = dict(extraction_methods)
    tampered_spline_representation["spline_representation_tolerance_fraction"] = 0.20
    try:
        validate_extraction_method_contract(tampered_spline_representation)
    except ContractError:
        checks.append("SPLINE_REPRESENTATION_TOLERANCE_TAMPERING_REJECTED")
    else:
        raise ContractError("Spline representation tolerance tampering self-test was not rejected.")

    synthetic_srs = expected_spatial_reference(3857)
    synthetic_mask = rasterize_supercover_mask(
        Polygon(((0.2, 0.2), (1.2, 0.2), (1.2, 1.2), (0.2, 1.2))),
        (0.0, 1.0, 0.0, 3.0, 0.0, -1.0),
        3,
        3,
        synthetic_srs,
    )
    require(
        int(np.count_nonzero(synthetic_mask)) == 4,
        "ALL_TOUCHED solve-mask self-test accidentally included a halo.",
    )
    require(
        solve_mask_component_count(synthetic_mask) == 1,
        "Connected solve-mask self-test was misclassified.",
    )
    disconnected_mask = np.asarray(
        [[True, False, False], [False, False, False], [False, False, True]],
        dtype=bool,
    )
    require(
        solve_mask_component_count(disconnected_mask) == 2,
        "Disconnected solve-mask self-test was not detected.",
    )
    checks.append("SOLVE_MASK_COMPONENTS_RECOMPUTED")
    tampered_mask = synthetic_mask.copy()
    tampered_mask[0, 0] = True
    try:
        validate_persisted_solve_mask(tampered_mask, synthetic_mask, "synthetic-halo")
    except ContractError:
        checks.append("PERSISTED_EXTRACTION_HALO_REJECTED")
    else:
        raise ContractError("Persisted extraction-halo self-test was not rejected.")
    checks.append("ALL_TOUCHED_SOLVE_MASK_EXCLUDES_EXTRACTION_HALO")

    validate_conservative_declared_radius(
        31.451,
        48.477,
        12.0,
        "synthetic.min_radius_m",
        minimum_requirement_m=25.0,
    )
    try:
        validate_conservative_declared_radius(
            70.0,
            48.477,
            12.0,
            "tampered.min_radius_m",
        )
    except ContractError:
        checks.append("INFLATED_DECLARED_RADIUS_REJECTED")
    else:
        raise ContractError("Inflated declared radius self-test was not rejected.")
    try:
        validate_conservative_declared_radius(
            20.0,
            48.477,
            12.0,
            "failed_fleet_gate.min_radius_m",
            minimum_requirement_m=25.0,
        )
    except ContractError:
        checks.append("PASSED_ROW_RADIUS_REQUIREMENT_ENFORCED")
    else:
        raise ContractError("Passed-row fleet radius self-test was not rejected.")

    parameters = {
        "row_spacing_m": 1.5,
        "radius_requirement_status": "NOT_PROVIDED",
        "minimum_work_path_radius_m": None,
        "phase": {
            "gauge_method": "ZERO_AT_LEXICOGRAPHIC_FIRST_VALID_CELL_PER_COMPONENT",
            "gauge_anchor_value_m": 0.0,
            "eikonal_residual_tolerance": 0.2,
            "integrability_residual_tolerance": 0.2,
            "maximum_integrability_residual_rms": 0.2,
            "maximum_critical_fraction": 0.1,
            "maximum_cut_locus_fraction": 0.1,
            "solve_mask_required_component_count": 1,
            "solve_mask_component_connectivity": 4,
        },
        "orientation": {
            "maximum_frustration_deg": 35.0,
            "maximum_low_coherence_fraction": 0.1,
            "maximum_abrupt_edge_fraction": 0.1,
            "maximum_cycle_conflict_fraction": 0.1,
        },
        "extraction": {
            "phase_level_index_scope": "CANDIDATE_WORK_BLOCK_PHASE_LEVEL",
            "row_index_definition": "UNIQUE_NONNEGATIVE_OPERATIONAL_SEQUENCE_PER_CANDIDATE_WORK_BLOCK",
            "phase_offset_search_revision": "CF0_PHASE_OFFSET_QUARTER_SPACING_V1",
            "phase_offset_search_scope": "DISCRETE_CONFIGURED_OFFSETS_NOT_CONTINUOUS_GAUGE_INVARIANCE",
            "spacing_tolerance_m": 0.3,
            "maximum_spacing_outside_tolerance_fraction": 0.1,
            "coverage_proxy_minimum": 0.7,
            "coverage_proxy_maximum": 1.3,
            **extraction_methods,
        },
    }
    synthetic_blockers = sorted(
        {
            "CLOSED_LOOP_WITHOUT_APPROVED_ENTRY",
            "COVERAGE_RATIO_OUTSIDE_CF0_ENVELOPE",
            "EIKONAL_P95_OUTSIDE_CF0_TOLERANCE",
            "INTEGRABILITY_P95_OUTSIDE_CF0_TOLERANCE",
            "INTERNAL_UNSUPPORTED_ENDPOINT",
            "ORIENTATION_P95_OUTSIDE_CF0_TOLERANCE",
            "ROW_CROSSING",
            "ROW_CROSSING_OR_OVERLAP",
            "ROW_SELF_INTERSECTION",
            "SPACING_P95_OUTSIDE_CF0_TOLERANCE",
            "SYNTHETIC_GEOMETRIC_REJECTION",
        }
    )
    candidates = []
    for candidate_id in sorted(EXPECTED_CANDIDATES):
        candidates.append(
            {
                "candidate_id": candidate_id,
                "field_id": "F1",
                "work_block_id": "WB_F1_C001",
                "family_id": f"{candidate_id}:WB_F1_C001",
                "phase_offset_m": 0.0,
                "phase_offset_fraction": 0.0,
                "phase_offset_role": "DIAGNOSTIC_ONLY",
                "phase_offset_selection": {
                    "revision": "CF0_PHASE_OFFSET_QUARTER_SPACING_V1",
                    "selection_rule": PHASE_OFFSET_SELECTION_RULE,
                    "search_scope": "DISCRETE_CONFIGURED_OFFSETS_NOT_CONTINUOUS_GAUGE_INVARIANCE",
                    "selection_status": "NO_FEASIBLE_PHASE_OFFSET",
                    "selection_key_order": list(PHASE_OFFSET_SELECTION_KEYS),
                    "coverage_proxy_target_ratio": 1.0,
                    "phase_offset_role": "DIAGNOSTIC_ONLY",
                    "selected_phase_offset_fraction": None,
                    "selected_phase_offset_m": None,
                    "diagnostic_phase_offset_fraction": 0.0,
                    "diagnostic_phase_offset_m": 0.0,
                    "diagnostic_selection_rule": "LOWEST_CONFIGURED_PHASE_OFFSET_V1",
                    "attempted_offsets": [
                        {
                            "phase_offset_fraction": fraction,
                            "phase_offset_m": fraction * 1.5,
                            "geometric_status": "NO_FEASIBLE_FAMILY",
                            "blocker_codes": list(synthetic_blockers),
                            "extracted_row_count": 2,
                            "extracted_total_length_m": 100.0,
                            "minimum_radius_order_class": "UNBOUNDED_STRAIGHT",
                            "minimum_radius_m": None,
                            "radius_status": "NOT_EVALUATED",
                            "spacing_error_p95_m": 0.9,
                            "final_spacing_outside_tolerance_fraction": 0.01,
                            "coverage_proxy_ratio": 0.4,
                            "spline_failure_count": 0,
                            "internal_endpoint_count": 4,
                            "intersection_count": 3,
                            "self_intersection_count": 2,
                            "loop_count": 1,
                        }
                        for fraction in PHASE_OFFSET_FRACTIONS
                    ],
                    "row_spacing_m": 1.5,
                    "level_equation": PHASE_LEVEL_EQUATION,
                    "phase_level_index_scope": "CANDIDATE_WORK_BLOCK_PHASE_LEVEL",
                    "row_index_definition": "UNIQUE_NONNEGATIVE_OPERATIONAL_SEQUENCE_PER_CANDIDATE_WORK_BLOCK",
                    "phase_gauge": {
                        "method": "ZERO_AT_LEXICOGRAPHIC_FIRST_VALID_CELL_PER_COMPONENT",
                        "anchor_phase_value_m": 0.0,
                        "anchors": [
                            {
                                "solver_row": 0,
                                "solver_column": 0,
                                "x_m": 0.5,
                                "y_m": 0.5,
                            }
                        ],
                    },
                },
                "contour_extrapolation_qa": {
                    "method": "FIRST_ORDER_LOCAL_LSQ_GRADIENT_FAIL_CLOSED",
                    "gradient_estimation_status": "PASS",
                    "halo_cells": 1,
                    "gradient_lsq_max_radius_cells": 2,
                    "gradient_lsq_max_relative_residual": 0.30,
                    "gradient_lsq_max_condition_number": 100.0,
                    "gradient_lsq_minimum_neighbor_count": 3,
                    "gradient_lsq_required_rank": 2,
                    "gradient_component_connectivity": 4,
                    "halo_cell_count": 4,
                    "supported_halo_cell_count": 4,
                    "unsupported_halo_cell_count": 0,
                    "direct_gradient_source_count": 2,
                    "lsq_gradient_source_count": 0,
                    "unestimable_gradient_source_count": 0,
                    "missing_gradient_component_count": 0,
                    "gradient_lsq_max_radius_used_cells": 0,
                    "gradient_lsq_residual_rms_max": None,
                    "gradient_lsq_relative_residual_max": None,
                    "gradient_lsq_condition_number_max": None,
                },
                "geometric_status": "NO_FEASIBLE_FAMILY",
                "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
                "row_count": 0,
                "total_length_m": 0.0,
                "diagnostic_row_count": 2,
                "diagnostic_total_length_m": 100.0,
                "metrics": {
                    "spacing_error_p95_m": 0.9,
                    "eikonal_residual_p95": 0.8,
                    "integrability_residual_p95": 0.7,
                    "orientation_misalignment_p95_deg": 60.0,
                    "minimum_radius_m": None,
                    "radius_status": "NOT_EVALUATED",
                    "maximum_abs_grade_pct": None,
                    "internal_endpoint_count": 4,
                    "intersection_count": 3,
                    "self_intersection_count": 2,
                    "loop_count": 1,
                    "coverage_proxy_ratio": 0.4,
                    "solver_gate_metrics": {
                        "solve_mask_component_count": 1,
                        "low_coherence_fraction": 0.01,
                        "abrupt_edge_fraction": 0.01,
                        "cycle_conflict_fraction": 0.01,
                        "integrability_residual_rms": 0.1,
                        "critical_fraction": 0.01,
                        "singular_fraction": 0.01,
                        "cut_locus_fraction": 0.01,
                        "solver_spacing_p05_m": 1.4,
                        "solver_spacing_p95_m": 1.6,
                        "solver_spacing_outside_tolerance_fraction": 0.01,
                        "final_spacing_outside_tolerance_fraction": 0.01,
                        "spline_failure_count": 0,
                    },
                },
                "blocker_codes": list(synthetic_blockers),
            }
        )
    validate_candidate_results(candidates, parameters, {"WB_F1_C001": {"F1"}})
    checks.append("NO_FEASIBLE_RETAINS_DIAGNOSTICS_WITHOUT_PUBLISHED_ROWS")

    tampered_fraction = json.loads(json.dumps(candidates))
    tampered_fraction[0]["phase_offset_selection"]["attempted_offsets"][1][
        "phase_offset_fraction"
    ] = 0.3
    try:
        validate_candidate_results(
            tampered_fraction, parameters, {"WB_F1_C001": {"F1"}}
        )
    except ContractError:
        checks.append("PHASE_OFFSET_FRACTION_TAMPERING_REJECTED")
    else:
        raise ContractError("Phase-offset fraction tampering self-test was not rejected.")

    forged_pass = json.loads(json.dumps(candidates))
    forged_trial = forged_pass[0]["phase_offset_selection"]["attempted_offsets"][1]
    forged_trial.update(
        {
            "geometric_status": "GEOMETRIC_PASS",
            "blocker_codes": [],
            "spacing_error_p95_m": 0.1,
            "coverage_proxy_ratio": 1.0,
            "spline_failure_count": 0,
            "internal_endpoint_count": 0,
            "intersection_count": 0,
            "self_intersection_count": 0,
            "loop_count": 0,
        }
    )
    try:
        validate_candidate_results(
            forged_pass, parameters, {"WB_F1_C001": {"F1"}}
        )
    except ContractError:
        checks.append("PHASE_PASS_SELECTION_RECOMPUTED")
    else:
        raise ContractError("Forged unselected phase PASS self-test was not rejected.")

    passing_selection = json.loads(json.dumps(candidates[0]))
    selection = passing_selection["phase_offset_selection"]
    for trial_index, radius_class, radius_value in (
        (1, "FINITE", 20.0),
        (2, "UNBOUNDED_STRAIGHT", None),
    ):
        trial = selection["attempted_offsets"][trial_index]
        trial.update(
            {
                "geometric_status": "GEOMETRIC_PASS",
                "blocker_codes": [],
                "minimum_radius_order_class": radius_class,
                "minimum_radius_m": radius_value,
                "spacing_error_p95_m": 0.1,
                "coverage_proxy_ratio": 1.0,
                "spline_failure_count": 0,
                "internal_endpoint_count": 0,
                "intersection_count": 0,
                "self_intersection_count": 0,
                "loop_count": 0,
            }
        )
    selection.update(
        {
            "selection_status": "SELECTED_GEOMETRIC_PASS",
            "phase_offset_role": "SELECTED_PASS",
            "selected_phase_offset_fraction": 0.5,
            "selected_phase_offset_m": 0.75,
            "diagnostic_phase_offset_fraction": None,
            "diagnostic_phase_offset_m": None,
        }
    )
    passing_selection.update(
        {
            "phase_offset_m": 0.75,
            "phase_offset_fraction": 0.5,
            "phase_offset_role": "SELECTED_PASS",
            "geometric_status": "GEOMETRIC_PASS",
            "row_count": 2,
            "total_length_m": 100.0,
            "diagnostic_row_count": 0,
            "diagnostic_total_length_m": 0.0,
            "blocker_codes": [],
        }
    )
    passing_selection["metrics"].update(
        {
            "minimum_radius_m": None,
            "spacing_error_p95_m": 0.1,
            "coverage_proxy_ratio": 1.0,
            "internal_endpoint_count": 0,
            "intersection_count": 0,
            "self_intersection_count": 0,
            "loop_count": 0,
        }
    )
    validate_phase_offset_selection(
        passing_selection, parameters, "synthetic-passing-selection"
    )
    wrongly_selected_finite = json.loads(json.dumps(passing_selection))
    wrongly_selected_finite["phase_offset_selection"].update(
        {
            "selected_phase_offset_fraction": 0.25,
            "selected_phase_offset_m": 0.375,
        }
    )
    try:
        validate_phase_offset_selection(
            wrongly_selected_finite, parameters, "tampered-passing-selection"
        )
    except ContractError:
        checks.append("PHASE_SELECTION_ORDER_RECOMPUTED")
    else:
        raise ContractError("Phase-offset selection-order tampering was not rejected.")

    phase_rows = ogr.GetDriverByName("Memory").CreateDataSource("")
    require(phase_rows is not None, "Could not allocate phase-row self-test datasource.")
    phase_layer = phase_rows.CreateLayer("phase_rows", geom_type=ogr.wkbNone)
    require(phase_layer is not None, "Could not allocate phase-row self-test layer.")
    for field_name, field_type in (
        ("phase_level_index", ogr.OFTInteger64),
        ("phase_level_m", ogr.OFTReal),
        ("phase_offset_m", ogr.OFTReal),
        ("phase_offset_fraction", ogr.OFTReal),
    ):
        require(
            phase_layer.CreateField(ogr.FieldDefn(field_name, field_type))
            == ogr.OGRERR_NONE,
            f"Could not create self-test field {field_name}.",
        )

    phase_features: list[ogr.Feature] = []
    for suffix in ("first", "repeated"):
        phase_feature = ogr.Feature(phase_layer.GetLayerDefn())
        phase_feature.SetField("phase_level_index", -4)
        phase_feature.SetField("phase_level_m", -5.25)
        phase_feature.SetField("phase_offset_m", 0.75)
        phase_feature.SetField("phase_offset_fraction", 0.5)
        validate_row_phase_fields(
            phase_feature,
            passing_selection,
            parameters,
            f"signed-repeatable-phase-index-{suffix}",
        )
        phase_features.append(phase_feature)
    checks.append("SIGNED_REPEATABLE_PHASE_LEVEL_INDEX_ACCEPTED")

    phase_features[0].SetField("phase_level_m", -5.0)
    try:
        validate_row_phase_fields(
            phase_features[0],
            passing_selection,
            parameters,
            "tampered-row-phase-level",
        )
    except ContractError:
        checks.append("ROW_PHASE_LEVEL_EQUATION_TAMPERING_REJECTED")
    else:
        raise ContractError("Row phase-level equation tampering was not rejected.")
    phase_features = []
    phase_layer = phase_rows = None

    published_without_pass = json.loads(json.dumps(candidates))
    published_without_pass[0]["row_count"] = 1
    published_without_pass[0]["total_length_m"] = 100.0
    try:
        validate_candidate_results(
            published_without_pass, parameters, {"WB_F1_C001": {"F1"}}
        )
    except ContractError:
        checks.append("NO_PHASE_PASS_PUBLICATION_REJECTED")
    else:
        raise ContractError("Publication without a passing phase offset was not rejected.")

    tampered_halo = json.loads(json.dumps(candidates))
    halo_qa = tampered_halo[0]["contour_extrapolation_qa"]
    halo_qa["gradient_estimation_status"] = "FAIL_CLOSED"
    halo_qa["supported_halo_cell_count"] = 3
    halo_qa["unsupported_halo_cell_count"] = 1
    halo_qa["unestimable_gradient_source_count"] = 1
    try:
        validate_candidate_results(
            tampered_halo, parameters, {"WB_F1_C001": {"F1"}}
        )
    except ContractError:
        checks.append("PHASE_HALO_BLOCKER_OMISSION_REJECTED")
    else:
        raise ContractError("Phase-halo blocker omission self-test was not rejected.")

    tampered_lsq_qa = json.loads(json.dumps(candidates))
    lsq_qa = tampered_lsq_qa[0]["contour_extrapolation_qa"]
    lsq_qa.update(
        {
            "direct_gradient_source_count": 1,
            "lsq_gradient_source_count": 1,
            "gradient_lsq_max_radius_used_cells": 1,
            "gradient_lsq_residual_rms_max": 0.01,
            "gradient_lsq_relative_residual_max": 0.31,
            "gradient_lsq_condition_number_max": 2.0,
        }
    )
    try:
        validate_candidate_results(
            tampered_lsq_qa, parameters, {"WB_F1_C001": {"F1"}}
        )
    except ContractError:
        checks.append("LSQ_OBSERVED_THRESHOLD_TAMPERING_REJECTED")
    else:
        raise ContractError("Observed LSQ-threshold tampering was not rejected.")

    retired_spline_blocker = json.loads(json.dumps(candidates))
    retired_spline_blocker[0]["blocker_codes"].append("C2_SPLINE_FAILED")
    retired_spline_blocker[0]["blocker_codes"].sort()
    try:
        validate_candidate_results(
            retired_spline_blocker, parameters, {"WB_F1_C001": {"F1"}}
        )
    except ContractError:
        checks.append("RETIRED_SPLINE_BLOCKER_REJECTED")
    else:
        raise ContractError("Retired spline blocker self-test was not rejected.")

    tampered_level_equation = json.loads(json.dumps(candidates))
    tampered_level_equation[0]["phase_offset_selection"]["level_equation"] = (
        "phase_level_m = row_index * row_spacing_m"
    )
    try:
        validate_candidate_results(
            tampered_level_equation, parameters, {"WB_F1_C001": {"F1"}}
        )
    except ContractError:
        checks.append("PHASE_LEVEL_EQUATION_TAMPERING_REJECTED")
    else:
        raise ContractError("Phase-level equation tampering self-test was not rejected.")

    qa_manifest = {
        "stage_status": "NO_FEASIBLE_FAMILY",
        "qa": {
            "candidate_field_count": 3,
            "geometric_pass_count": 0,
            "no_feasible_family_count": 3,
            "hydraulic_unconfirmed_count": 3,
            "radius_not_evaluated_count": 3,
            "invalid_geometry_count": 0,
            "non_3d_geometry_count": 0,
            "internal_endpoint_count": 12,
            "intersection_count": 9,
            "nonfinite_coordinate_count": 0,
            "raster_invalid_count": 0,
            "gate_failures": sorted(
                {code for candidate in candidates for code in candidate["blocker_codes"]}
            ),
        },
        "layer_counts": {
            "continuous_rows": 0,
            "diagnostic_rows": 6,
            "family_summary": 3,
            "hydraulic_precheck": 3,
        },
    }
    validate_stage_counts(qa_manifest, candidates)
    qa_manifest["layer_counts"]["diagnostic_rows"] = 5
    try:
        validate_stage_counts(qa_manifest, candidates)
    except ContractError:
        checks.append("DIAGNOSTIC_LAYER_COUNT_TAMPERING_REJECTED")
    else:
        raise ContractError("Diagnostic layer-count tampering self-test was not rejected.")
    qa_manifest["layer_counts"]["diagnostic_rows"] = 6
    qa_manifest["qa"]["gate_failures"] = []
    try:
        validate_stage_counts(qa_manifest, candidates)
    except ContractError:
        checks.append("QA_GATE_FAILURE_TAMPERING_REJECTED")
    else:
        raise ContractError("QA gate-failure tampering self-test was not rejected.")

    tampered_candidates = json.loads(json.dumps(candidates))
    tampered_candidates[0]["metrics"]["solver_gate_metrics"]["critical_fraction"] = 0.9
    try:
        validate_candidate_results(
            tampered_candidates, parameters, {"WB_F1_C001": {"F1"}}
        )
    except ContractError:
        checks.append("SOLVER_GATE_METRIC_TAMPERING_REJECTED")
    else:
        raise ContractError("Solver gate-metric tampering self-test was not rejected.")

    disconnected_without_blocker = json.loads(json.dumps(candidates))
    disconnected_without_blocker[0]["metrics"]["solver_gate_metrics"][
        "solve_mask_component_count"
    ] = 2
    try:
        validate_candidate_results(
            disconnected_without_blocker, parameters, {"WB_F1_C001": {"F1"}}
        )
    except ContractError:
        checks.append("SOLVE_MASK_DISCONNECT_BLOCKER_OMISSION_REJECTED")
    else:
        raise ContractError("Solve-mask disconnect blocker omission was not rejected.")

    malformed = dict(parameters)
    malformed.pop("phase")
    try:
        validate_candidate_results(candidates, malformed, {"WB_F1_C001": {"F1"}})
    except (ContractError, KeyError):
        checks.append("NEGATIVE_CONTRACT_CASE_REJECTED")
    else:
        raise ContractError("Negative contract self-test was not rejected.")
    return {"status": "PASS", "checks": checks}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the CF0 continuous-family package.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        try:
            print(json.dumps(run_self_test(), ensure_ascii=False, indent=2))
            return 0
        except (ContractError, KeyError, RuntimeError) as exc:
            print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
    manifest_path = args.manifest if args.manifest.is_absolute() else (REPO / args.manifest).resolve()
    schema_path = args.schema if args.schema.is_absolute() else (REPO / args.schema).resolve()
    try:
        result = verify(manifest_path, schema_path)
    except (ContractError, json.JSONDecodeError, OSError, RuntimeError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
