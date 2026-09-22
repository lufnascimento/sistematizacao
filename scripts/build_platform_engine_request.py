"""Build a validated E0 project request for the E0 and CF0 engines.

The builder accepts either explicit terrain/boundary inputs or the manifest
emitted by ``run_project_topography.py``. It has no GIS dependency: spatial
content remains the responsibility of the topography QA stage and the engines.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

try:
    from project_request import DEFAULT_CATALOG, DEFAULT_SCHEMA, load_project_request
    from validate_project_inputs import ContractError, load_json, validate_catalog, validate_schema
except ModuleNotFoundError:  # Supports ``python -m scripts...`` from the repository root.
    from scripts.project_request import DEFAULT_CATALOG, DEFAULT_SCHEMA, load_project_request
    from scripts.validate_project_inputs import (
        ContractError,
        load_json,
        validate_catalog,
        validate_schema,
    )


REQUEST_SCHEMA_VERSION = "1.0.0"
SYSTEM_MODEL_REVISION = "platform-e0-request-model-1.2.0"
SUPPORTED_BOUNDARY_FORMATS = {
    ".shp": "SHP",
    ".gpkg": "GPKG",
    ".geojson": "GEOJSON",
    ".json": "GEOJSON",
    ".kml": "KML",
    ".dxf": "DXF",
}


class RequestBuildError(RuntimeError):
    """Raised when the supplied platform configuration is incomplete or unsafe."""


@dataclass(frozen=True)
class RequestInputs:
    project_id: str
    request_id: str
    dtm_path: Path
    boundary_path: Path
    field_ids: tuple[str, ...]
    property_ids: tuple[str, ...]
    crs: str
    boundary_id_field: str
    boundary_layer: str | None
    row_spacing_m: float
    headland_m: float
    minimum_work_path_radius_m: float
    minimum_shot_length_m: float
    nominal_speed_kmh: float
    power_status: str
    cross_field: bool
    cross_property: bool
    cross_property_permission: str
    expected_yield_t_ha: float | None = None
    maneuver_time_s: float | None = None
    max_cross_slope_pct: float | None = None
    terrain_smoothing_sigma_m: float | None = None
    reference_alert_grade_pct: float | None = None
    constraint_review_status: str = "NOT_REVIEWED"
    created_at: str | None = None
    input_qa_status: str = "UPLOADED"
    input_captured_at: str | None = None
    topography_manifest_path: Path | None = None
    user_name: str = "platform-user"
    user_organization: str = "client-organization"
    user_role: str = "project-configurator"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_text(value: str, label: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise RequestBuildError(f"{label} must not be empty.")
    return normalized


def _unique_text(values: Sequence[str], label: str) -> tuple[str, ...]:
    normalized: list[str] = []
    for raw in values:
        for item in str(raw).split(","):
            value = item.strip()
            if value and value not in normalized:
                normalized.append(value)
    if not normalized:
        raise RequestBuildError(f"At least one {label} is required.")
    return tuple(normalized)


def _finite(value: float, label: str, *, minimum: float, exclusive: bool = False) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise RequestBuildError(f"{label} must be finite.")
    invalid = number <= minimum if exclusive else number < minimum
    if invalid:
        comparator = "greater than" if exclusive else "at least"
        raise RequestBuildError(f"{label} must be {comparator} {minimum}.")
    return number


def _parse_timestamp(value: str, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise RequestBuildError(f"{label} must be an ISO 8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise RequestBuildError(f"{label} must include a UTC offset.")
    return parsed.isoformat()


def _resolve_existing_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise RequestBuildError(f"{label} does not exist or is not a file: {resolved}")
    return resolved


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _dataset_files(path: Path, dataset_format: str) -> list[Path]:
    if dataset_format != "SHP":
        return [path]
    return sorted(
        candidate
        for candidate in path.parent.iterdir()
        if candidate.is_file() and candidate.stem.lower() == path.stem.lower()
    ) or [path]


def _bundle_sha256(path: Path, dataset_format: str) -> str:
    files = _dataset_files(path, dataset_format)
    if len(files) == 1:
        return _file_sha256(files[0])
    digest = hashlib.sha256()
    for candidate in files:
        digest.update(candidate.name.lower().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(_file_sha256(candidate)))
    return digest.hexdigest()


def _responsible(name: str, organization: str, role: str) -> dict[str, Any]:
    return {
        "name": _require_text(name, "user name"),
        "organization": _require_text(organization, "user organization"),
        "role": _require_text(role, "user role"),
        "identifier": None,
    }


def _system_responsible() -> dict[str, Any]:
    return {
        "name": "terraflux-request-builder",
        "organization": "TerraFlux",
        "role": "system-model",
        "identifier": None,
    }


class ParameterFactory:
    """Materialize catalog-backed parameter values with truthful provenance."""

    def __init__(
        self,
        catalog_by_id: dict[str, dict[str, Any]],
        *,
        request_id: str,
        captured_at: str,
        user_responsible: dict[str, Any],
    ) -> None:
        self.catalog_by_id = catalog_by_id
        self.request_id = request_id
        self.captured_at = captured_at
        self.user_responsible = user_responsible

    def make(
        self,
        parameter_id: str,
        value: Any,
        *,
        source_kind: str,
        origin: str | None = None,
        confidence: str = "HIGH",
        applicability: str = "PROJECT",
        method: str | None = None,
    ) -> dict[str, Any]:
        if parameter_id not in self.catalog_by_id:
            raise RequestBuildError(f"Parameter is absent from the active catalog: {parameter_id}")
        catalog_item = self.catalog_by_id[parameter_id]
        parameter_class = catalog_item["parameter_class"]
        if source_kind == "USER_CONFIG":
            default_origins = {"USER_FACT": "DECLARED", "OPTIMIZER": "DECLARED"}
            resolved_origin = origin or default_origins.get(parameter_class)
            responsible = self.user_responsible
            revision = f"user-config:{self.request_id}"
        elif source_kind == "SYSTEM_MODEL":
            default_origins = {
                "USER_FACT": "IMPORTED",
                "CALCULATED": "CALCULATED",
                "RULE_PACK": "RULE_PACK",
                "OPTIMIZER": "OPTIMIZER",
                "E0_ASSUMPTION": "E0_ASSUMPTION",
            }
            resolved_origin = origin or default_origins.get(parameter_class)
            responsible = _system_responsible()
            revision = SYSTEM_MODEL_REVISION
        else:
            raise RequestBuildError(f"Unsupported provenance source kind: {source_kind}")
        if not resolved_origin:
            raise RequestBuildError(
                f"No schema-compatible provenance origin for {parameter_id} from {source_kind}."
            )
        source_ref = f"{source_kind}:{self.request_id}:{parameter_id}"
        provenance_method = method or f"{source_kind} materialization by the platform request builder"
        return {
            "parameter_id": parameter_id,
            "parameter_class": parameter_class,
            "value": value,
            "unit": catalog_item["unit"],
            "provenance": {
                "origin": resolved_origin,
                "source_ref": source_ref,
                "captured_at": self.captured_at,
                "responsible": responsible,
                "confidence": confidence,
                "revision": revision,
                "applicability": applicability,
                "method": provenance_method,
            },
        }


def _load_contract(
    catalog_path: Path,
    schema_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]]:
    catalog = load_json(catalog_path.resolve())
    schema = load_json(schema_path.resolve())
    catalog_by_id = validate_catalog(catalog)
    validate_schema(schema, catalog)
    return catalog, schema, catalog_by_id


def _validate_inputs(inputs: RequestInputs) -> RequestInputs:
    project_id = _require_text(inputs.project_id, "project_id")
    request_id = _require_text(inputs.request_id, "request_id")
    dtm_path = _resolve_existing_file(inputs.dtm_path, "DTM")
    boundary_path = _resolve_existing_file(inputs.boundary_path, "field boundary")
    if dtm_path.suffix.lower() not in {".tif", ".tiff"}:
        raise RequestBuildError("The E0/CF0 DTM must be a GeoTIFF (.tif or .tiff).")
    if boundary_path.suffix.lower() not in SUPPORTED_BOUNDARY_FORMATS:
        raise RequestBuildError(
            "Field boundary must be SHP, GPKG, GeoJSON, KML or DXF."
        )
    boundary_layer = inputs.boundary_layer.strip() if inputs.boundary_layer else None
    if boundary_path.suffix.lower() == ".gpkg" and not boundary_layer:
        raise RequestBuildError("A GeoPackage field boundary requires --boundary-layer.")
    field_ids = _unique_text(inputs.field_ids, "field id")
    property_ids = _unique_text(inputs.property_ids or (project_id,), "property id")
    cross_property_permission = inputs.cross_property_permission
    if inputs.cross_property and not inputs.cross_field:
        raise RequestBuildError("Cross-property generation also requires cross-field generation.")
    if inputs.cross_property and cross_property_permission != "APPROVED":
        raise RequestBuildError(
            "Cross-property generation requires --cross-property-permission APPROVED."
        )
    if inputs.cross_property and len(property_ids) < 2:
        raise RequestBuildError("Cross-property generation requires at least two --property-id values.")
    if not inputs.cross_property:
        cross_property_permission = "NOT_APPLICABLE"
    if inputs.power_status not in {"DECLARED_NONE", "NOT_REVIEWED"}:
        raise RequestBuildError("power_status must be DECLARED_NONE or NOT_REVIEWED.")
    if inputs.constraint_review_status not in {"COMPLETE", "PARTIAL", "NOT_REVIEWED"}:
        raise RequestBuildError("Invalid constraint_review_status.")
    if inputs.input_qa_status not in {"UPLOADED", "VALIDATED"}:
        raise RequestBuildError("input_qa_status must be UPLOADED or VALIDATED.")
    created_at = _parse_timestamp(inputs.created_at or _utc_now(), "created_at")
    captured_at = _parse_timestamp(inputs.input_captured_at or created_at, "input_captured_at")
    expected_yield = (
        None
        if inputs.expected_yield_t_ha is None
        else _finite(inputs.expected_yield_t_ha, "expected_yield_t_ha", minimum=0)
    )
    return RequestInputs(
        project_id=project_id,
        request_id=request_id,
        dtm_path=dtm_path,
        boundary_path=boundary_path,
        field_ids=field_ids,
        property_ids=property_ids,
        crs=_require_text(inputs.crs, "crs"),
        boundary_id_field=_require_text(inputs.boundary_id_field, "boundary_id_field"),
        boundary_layer=boundary_layer,
        row_spacing_m=_finite(inputs.row_spacing_m, "row_spacing_m", minimum=0, exclusive=True),
        headland_m=_finite(inputs.headland_m, "headland_m", minimum=0),
        minimum_work_path_radius_m=_finite(
            inputs.minimum_work_path_radius_m,
            "minimum_work_path_radius_m",
            minimum=0,
            exclusive=True,
        ),
        minimum_shot_length_m=_finite(
            inputs.minimum_shot_length_m,
            "minimum_shot_length_m",
            minimum=0,
        ),
        nominal_speed_kmh=_finite(
            inputs.nominal_speed_kmh,
            "nominal_speed_kmh",
            minimum=0,
            exclusive=True,
        ),
        power_status=inputs.power_status,
        cross_field=bool(inputs.cross_field),
        cross_property=bool(inputs.cross_property),
        cross_property_permission=cross_property_permission,
        expected_yield_t_ha=expected_yield,
        maneuver_time_s=None if inputs.maneuver_time_s is None else _finite(inputs.maneuver_time_s, "maneuver_time_s", minimum=0),
        max_cross_slope_pct=None if inputs.max_cross_slope_pct is None else _finite(inputs.max_cross_slope_pct, "max_cross_slope_pct", minimum=0),
        terrain_smoothing_sigma_m=None if inputs.terrain_smoothing_sigma_m is None else _finite(inputs.terrain_smoothing_sigma_m, "terrain_smoothing_sigma_m", minimum=0),
        reference_alert_grade_pct=None if inputs.reference_alert_grade_pct is None else _finite(inputs.reference_alert_grade_pct, "reference_alert_grade_pct", minimum=0, exclusive=True),
        constraint_review_status=inputs.constraint_review_status,
        created_at=created_at,
        input_qa_status=inputs.input_qa_status,
        input_captured_at=captured_at,
        topography_manifest_path=(
            inputs.topography_manifest_path.resolve()
            if inputs.topography_manifest_path is not None
            else None
        ),
        user_name=inputs.user_name,
        user_organization=inputs.user_organization,
        user_role=inputs.user_role,
    )


def build_request(
    inputs: RequestInputs,
    *,
    catalog_path: Path = DEFAULT_CATALOG,
    schema_path: Path = DEFAULT_SCHEMA,
) -> dict[str, Any]:
    """Build an in-memory request using the active catalog definitions."""

    resolved = _validate_inputs(inputs)
    catalog, _schema, catalog_by_id = _load_contract(catalog_path, schema_path)
    created_at = resolved.created_at or _utc_now()
    captured_at = resolved.input_captured_at or created_at
    user = _responsible(
        resolved.user_name,
        resolved.user_organization,
        resolved.user_role,
    )
    parameters = ParameterFactory(
        catalog_by_id,
        request_id=resolved.request_id,
        captured_at=created_at,
        user_responsible=user,
    )
    dtm_dataset_id = f"{resolved.project_id}:dtm"
    boundary_dataset_id = f"{resolved.project_id}:field-boundary"
    terrain_revision = f"sha256:{_bundle_sha256(resolved.dtm_path, 'TIF')[:16]}"
    boundary_format = SUPPORTED_BOUNDARY_FORMATS[resolved.boundary_path.suffix.lower()]
    boundary_revision = f"sha256:{_bundle_sha256(resolved.boundary_path, boundary_format)[:16]}"
    topography_source = (
        str(resolved.topography_manifest_path)
        if resolved.topography_manifest_path is not None
        else str(resolved.dtm_path)
    )
    system_method_suffix = (
        f" using {resolved.topography_manifest_path}"
        if resolved.topography_manifest_path is not None
        else " from explicit platform configuration"
    )

    parameter_values = [
        parameters.make(
            "scope.target_field_ids",
            list(resolved.field_ids),
            source_kind="USER_CONFIG" if resolved.topography_manifest_path is None else "SYSTEM_MODEL",
            origin="DECLARED" if resolved.topography_manifest_path is None else "IMPORTED",
            method=(
                "USER_CONFIG explicit field selection"
                if resolved.topography_manifest_path is None
                else "SYSTEM_MODEL import of the field scope validated by the topography stage"
            ),
        ),
        parameters.make(
            "scope.target_property_ids",
            list(resolved.property_ids),
            source_kind="USER_CONFIG",
            confidence="LOW" if resolved.property_ids == (resolved.project_id,) else "HIGH",
            method=(
                "USER_CONFIG project identifier used as the operational property scope"
                if resolved.property_ids == (resolved.project_id,)
                else "USER_CONFIG explicit property scope"
            ),
        ),
        parameters.make(
            "scope.allow_cross_field_work",
            resolved.cross_field,
            source_kind="USER_CONFIG",
            method="USER_CONFIG cross-field generation decision",
        ),
        parameters.make(
            "scope.allow_cross_property_work",
            resolved.cross_property,
            source_kind="USER_CONFIG",
            method="USER_CONFIG cross-property generation decision",
        ),
        parameters.make(
            "scope.cross_property_permission_status",
            resolved.cross_property_permission,
            source_kind="USER_CONFIG",
            method="USER_CONFIG cross-property permission decision",
        ),
        parameters.make(
            "terrain.dtm_dataset_ref",
            dtm_dataset_id,
            source_kind="USER_CONFIG" if resolved.topography_manifest_path is None else "SYSTEM_MODEL",
            origin="UPLOADED" if resolved.topography_manifest_path is None else "IMPORTED",
            confidence="HIGH" if resolved.topography_manifest_path is not None else "MEDIUM",
            method=f"{'SYSTEM_MODEL' if resolved.topography_manifest_path else 'USER_CONFIG'} DTM reference{system_method_suffix}",
        ),
        parameters.make(
            "terrain.horizontal_crs",
            resolved.crs,
            source_kind="USER_CONFIG" if resolved.topography_manifest_path is None else "SYSTEM_MODEL",
            origin="DECLARED" if resolved.topography_manifest_path is None else "IMPORTED",
            method=f"{'SYSTEM_MODEL' if resolved.topography_manifest_path else 'USER_CONFIG'} horizontal CRS{system_method_suffix}",
        ),
        parameters.make(
            "agronomy.row_spacing_m",
            resolved.row_spacing_m,
            source_kind="USER_CONFIG",
            method="USER_CONFIG row spacing selected for this generation request",
        ),
        parameters.make(
            "fleet.operations_in_scope",
            ["FURROW"],
            source_kind="USER_CONFIG",
            method="USER_CONFIG limits this request to furrow generation",
        ),
        parameters.make(
            "fleet.minimum_work_path_radius_m",
            {"FURROW": resolved.minimum_work_path_radius_m},
            source_kind="USER_CONFIG",
            applicability="FLEET",
            method="USER_CONFIG minimum work-path radius for the furrowing configuration",
        ),
        parameters.make(
            "fleet.required_headland_width_m",
            {"FURROW": resolved.headland_m},
            source_kind="SYSTEM_MODEL",
            applicability="FLEET",
            method="SYSTEM_MODEL materialization of the user-configured E0 headland width; operational release still requires fleet evidence",
        ),
        parameters.make(
            "constraints.inventory_review_status",
            resolved.constraint_review_status,
            source_kind="USER_CONFIG",
            method="USER_CONFIG general constraint review status",
        ),
        parameters.make(
            "constraints.overhead_power_line_inventory_status",
            resolved.power_status,
            source_kind=("USER_CONFIG" if resolved.power_status == "DECLARED_NONE" else "SYSTEM_MODEL"),
            origin=("DECLARED" if resolved.power_status == "DECLARED_NONE" else "IMPORTED"),
            confidence="HIGH" if resolved.power_status == "DECLARED_NONE" else "VERIFIED",
            method=(
                "USER_CONFIG explicit declaration that no overhead power line is present"
                if resolved.power_status == "DECLARED_NONE"
                else "SYSTEM_MODEL fail-closed status because overhead power was not reviewed"
            ),
        ),
        parameters.make(
            "connections.minimum_preferred_shot_length_m",
            resolved.minimum_shot_length_m,
            source_kind="USER_CONFIG",
            applicability="SCENARIO",
            method="USER_CONFIG soft preference for minimum operational shot length",
        ),
        parameters.make(
            "objectives.selection_mode",
            "PARETO",
            source_kind="SYSTEM_MODEL",
            applicability="SCENARIO",
            method="SYSTEM_MODEL hard-gate-first Pareto portfolio policy",
        ),
        parameters.make(
            "qa.rule_pack_revision",
            SYSTEM_MODEL_REVISION,
            source_kind="SYSTEM_MODEL",
            confidence="VERIFIED",
            method="SYSTEM_MODEL revision used to materialize this E0 request",
        ),
        parameters.make(
            "qa.constraint_revision",
            f"constraints:{resolved.constraint_review_status.lower()}:{resolved.request_id}",
            source_kind="SYSTEM_MODEL",
            confidence="VERIFIED",
            method="SYSTEM_MODEL revision derived from the explicit constraint-review state",
        ),
        parameters.make(
            "qa.release_block_on_unreviewed_constraints",
            True,
            source_kind="SYSTEM_MODEL",
            confidence="VERIFIED",
            method="SYSTEM_MODEL fail-closed release rule",
        ),
        parameters.make(
            "e0.nominal_field_speed_kmh",
            resolved.nominal_speed_kmh,
            source_kind="SYSTEM_MODEL",
            confidence="LOW",
            applicability="SCENARIO",
            method="SYSTEM_MODEL E0 speed assumption selected in user configuration; not operational evidence",
        ),
    ]
    if resolved.terrain_smoothing_sigma_m is not None:
        parameter_values.append(parameters.make(
            "terrain.smoothing_sigma_m", resolved.terrain_smoothing_sigma_m,
            source_kind="USER_CONFIG", applicability="SCENARIO",
            method="USER_CONFIG Gaussian standard deviation in metres; zero disables smoothing; not an axial orientation radius or hydraulic conditioning",
        ))
    if resolved.reference_alert_grade_pct is not None:
        parameter_values.append(parameters.make(
            "e0.reference_alert_grade_pct", resolved.reference_alert_grade_pct,
            source_kind="USER_CONFIG", origin="E0_ASSUMPTION", applicability="SCENARIO", confidence="LOW",
            method="USER_CONFIG E0 reference alert grade; screening only, not an approved hydraulic or operational limit",
        ))
    if resolved.maneuver_time_s is not None:
        parameter_values.append(parameters.make(
            "e0.maneuver_time_s", resolved.maneuver_time_s, source_kind="SYSTEM_MODEL",
            confidence="LOW", applicability="SCENARIO",
            method="SYSTEM_MODEL preliminary maneuver duration selected in user configuration; not telemetry",
        ))
    if resolved.max_cross_slope_pct is not None:
        parameter_values.append(parameters.make(
            "fleet.max_cross_slope_pct", {"FURROW": resolved.max_cross_slope_pct}, source_kind="USER_CONFIG",
            origin="RULE_PACK", confidence="LOW", applicability="FLEET",
            method="USER_CONFIG preliminary transverse slope restriction; not engineer approval or fleet evidence",
        ))
    if resolved.expected_yield_t_ha is not None:
        parameter_values.append(
            parameters.make(
                "agronomy.expected_yield_t_ha",
                resolved.expected_yield_t_ha,
                source_kind="USER_CONFIG",
                confidence="MEDIUM",
                method="USER_CONFIG expected yield supplied for scenario comparison metadata",
            )
        )

    dtm_dataset = {
        "dataset_id": dtm_dataset_id,
        "role": "DTM",
        "format": "TIF",
        "source_ref": str(resolved.dtm_path),
        "revision": terrain_revision,
        "captured_at": captured_at,
        "responsible": (
            _system_responsible()
            if resolved.topography_manifest_path is not None
            else user
        ),
        "crs": {
            "horizontal": resolved.crs,
            "vertical": None,
            "horizontal_unit": "m",
            "vertical_unit": "m",
        },
        "geometry_type": None,
        "checksum_sha256": _bundle_sha256(resolved.dtm_path, "TIF"),
        "qa_status": resolved.input_qa_status,
    }
    boundary_dataset: dict[str, Any] = {
        "dataset_id": boundary_dataset_id,
        "role": "FIELD_BOUNDARY",
        "format": boundary_format,
        "source_ref": str(resolved.boundary_path),
        "id_field": resolved.boundary_id_field,
        "revision": boundary_revision,
        "captured_at": captured_at,
        "responsible": user,
        "crs": {
            "horizontal": resolved.crs,
            "vertical": None,
            "horizontal_unit": "m",
            "vertical_unit": None,
        },
        "geometry_type": "Polygon",
        "checksum_sha256": _bundle_sha256(resolved.boundary_path, boundary_format),
        "qa_status": resolved.input_qa_status,
    }
    if resolved.boundary_layer:
        boundary_dataset["layer_name"] = resolved.boundary_layer

    declaration_ref = (
        f"USER_CONFIG:{resolved.request_id}:overhead-power-declaration"
        if resolved.power_status == "DECLARED_NONE"
        else None
    )
    power_inventory: dict[str, Any] = {
        "status": resolved.power_status,
        "layer_ids": [],
        "declaration_ref": declaration_ref,
        "declared_at": created_at,
        "responsible": user if resolved.power_status == "DECLARED_NONE" else _system_responsible(),
    }
    request = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "request_id": resolved.request_id,
        "project_id": resolved.project_id,
        "created_at": created_at,
        "requested_delivery_level": "E0_TRIAGEM",
        "revisions": {
            "parameter_catalog_revision": catalog["schema_version"],
            "terrain_revision": terrain_revision,
            "fleet_revision": f"e0-user-config:{resolved.request_id}",
            "rule_pack_revision": SYSTEM_MODEL_REVISION,
            "constraint_revision": f"constraints:{resolved.constraint_review_status.lower()}:{resolved.request_id}",
            "hydrology_revision": None,
            "logistics_revision": None,
            "uncertainty_set_revision": None,
        },
        "scope": {
            "field_ids": list(resolved.field_ids),
            "property_ids": list(resolved.property_ids),
            "cross_field_generation": resolved.cross_field,
            "cross_property_generation": resolved.cross_property,
            "analysis_buffer_m": 0,
        },
        "input_datasets": [dtm_dataset, boundary_dataset],
        "constraint_inventory": {
            "general_review_status": resolved.constraint_review_status,
            "overhead_power_line": power_inventory,
            "notes": (
                "Overhead power absence was explicitly declared in USER_CONFIG; other constraints follow the declared review status."
                if resolved.power_status == "DECLARED_NONE"
                else "Overhead power and other constraints remain unreviewed; release is capped at E0 geometric screening."
            ),
        },
        "constraint_layers": [],
        "parameter_values": parameter_values,
        "scenario_request": {
            "conservation_macros": [
                "C1_CURVA_EMBUTIDA",
                "C2_BASE_LARGA_PASSANTE",
                "C3_ESD",
            ],
            "connection_modes": ["OC2_GUIA_CONTINUA"] if resolved.cross_field else ["OC0_ISOLADO"],
            "poa_strategy_modes": ["NOT_REQUESTED"],
            "poa_objective_profiles": ["NOT_REQUESTED"],
            "logistics_simulation_modes": ["NOT_REQUESTED"],
            "coupling_policy": "SEQUENTIAL_SCREENING",
            "uncertainty_cases": ["E0_TOPOGRAPHIC_ONLY"],
        },
        "objective_policy": {
            "selection_mode": "PARETO",
            "hard_gate_policy": "FILTER_BEFORE_SCORING",
            "objectives": [
                {
                    "metric_id": "operational_shot_length",
                    "direction": "MAXIMIZE",
                    "priority": 1,
                    "weight": None,
                },
                {
                    "metric_id": "terrain_alignment_screening",
                    "direction": "MAXIMIZE",
                    "priority": 2,
                    "weight": None,
                },
            ],
        },
        "release_policy": {
            "block_on_unreviewed_constraints": True,
            "maximum_release_with_unreviewed_power": "E0_GEOMETRIC_ONLY",
            "allow_safety_gate_override": False,
            "require_professional_approval": [
                "ELECTRICAL_BARRIER",
                "HYDRAULIC_DESIGN",
                "POA_SITE",
                "E2_RELEASE",
                "E3_RELEASE",
            ],
        },
        "overrides": [],
    }
    # Keep the topography lineage traceable without adding non-schema fields.
    if resolved.topography_manifest_path is not None:
        dtm_dataset["revision"] = f"{terrain_revision}:topography-manifest"
        request["revisions"]["terrain_revision"] = dtm_dataset["revision"]
        request["revisions"]["hydrology_revision"] = "not-evaluated-by-topography-stage"
        request["constraint_inventory"]["notes"] += f" Topography lineage: {topography_source}."
    return request


def write_validated_request(
    request: dict[str, Any],
    output_path: Path,
    *,
    catalog_path: Path = DEFAULT_CATALOG,
    schema_path: Path = DEFAULT_SCHEMA,
) -> dict[str, Any]:
    """Validate through ``load_project_request`` and atomically publish one JSON."""

    output = output_path.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            suffix=".json",
            prefix=f".{output.stem}.",
            dir=output.parent,
            delete=False,
        ) as temporary:
            json.dump(request, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary_path = Path(temporary.name)
        resolved = load_project_request(
            temporary_path,
            catalog_path=catalog_path,
            schema_path=schema_path,
        )
        os.replace(temporary_path, output)
        temporary_path = None
        return resolved.validation_report
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _manifest_value(manifest: dict[str, Any], path: Sequence[str], label: str) -> Any:
    value: Any = manifest
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise RequestBuildError(f"Topography manifest is missing {label}.")
        value = value[key]
    return value


def _inputs_from_args(args: argparse.Namespace) -> RequestInputs:
    manifest_path: Path | None = None
    manifest: dict[str, Any] | None = None
    if args.topography_manifest:
        manifest_path = _resolve_existing_file(args.topography_manifest, "topography manifest")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RequestBuildError(f"Cannot read topography manifest: {manifest_path}") from exc
        if manifest.get("schema_version") != "1.0.0":
            raise RequestBuildError("Unsupported topography manifest schema_version.")

    def choose(explicit: Any, manifest_path_keys: Sequence[str], label: str) -> Any:
        if explicit not in (None, [], ()):
            return explicit
        if manifest is None:
            raise RequestBuildError(f"{label} is required without --topography-manifest.")
        return _manifest_value(manifest, manifest_path_keys, label)

    dtm_path = Path(choose(args.dtm, ("outputs", "dtm", "path"), "DTM"))
    boundary_path = Path(choose(args.boundary, ("inputs", "boundary", "path"), "field boundary"))
    field_ids = choose(args.field_ids, ("scope", "field_ids"), "field ids")
    crs = choose(args.crs, ("configuration", "target_crs"), "CRS")
    id_field = choose(
        args.boundary_id_field,
        ("configuration", "field_id_column"),
        "boundary id field",
    )
    input_captured_at = (
        manifest.get("generated_at") if manifest is not None else args.created_at
    )
    return RequestInputs(
        project_id=args.project_id,
        request_id=args.request_id,
        dtm_path=dtm_path,
        boundary_path=boundary_path,
        field_ids=_unique_text(field_ids, "field id"),
        property_ids=_unique_text(args.property_ids or (args.project_id,), "property id"),
        crs=str(crs),
        boundary_id_field=str(id_field),
        boundary_layer=args.boundary_layer,
        row_spacing_m=args.row_spacing_m,
        headland_m=args.headland_m,
        minimum_work_path_radius_m=args.minimum_work_path_radius_m,
        minimum_shot_length_m=args.minimum_shot_length_m,
        nominal_speed_kmh=args.nominal_speed_kmh,
        power_status=args.power_status,
        cross_field=args.cross_field,
        cross_property=args.cross_property,
        cross_property_permission=args.cross_property_permission,
        expected_yield_t_ha=args.expected_yield_t_ha,
        maneuver_time_s=args.maneuver_time_s,
        max_cross_slope_pct=args.max_cross_slope_pct,
        terrain_smoothing_sigma_m=args.terrain_smoothing_sigma_m,
        reference_alert_grade_pct=args.reference_alert_grade_pct,
        constraint_review_status=args.constraint_review_status,
        created_at=args.created_at,
        input_qa_status="VALIDATED" if manifest is not None else "UPLOADED",
        input_captured_at=input_captured_at,
        topography_manifest_path=manifest_path,
        user_name=args.user_name,
        user_organization=args.user_organization,
        user_role=args.user_role,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--topography-manifest", type=Path)
    parser.add_argument("--dtm", type=Path)
    parser.add_argument("--boundary", type=Path)
    parser.add_argument(
        "--field-id",
        dest="field_ids",
        action="append",
        default=[],
        help="Target field id; may be repeated or comma-separated.",
    )
    parser.add_argument(
        "--property-id",
        dest="property_ids",
        action="append",
        default=[],
        help="Property scope id; may be repeated or comma-separated.",
    )
    parser.add_argument("--crs", help="Projected horizontal CRS, for example EPSG:31982.")
    parser.add_argument("--boundary-id-field")
    parser.add_argument("--boundary-layer")
    parser.add_argument("--row-spacing-m", type=float, required=True)
    parser.add_argument("--headland-m", type=float, required=True)
    parser.add_argument("--minimum-work-path-radius-m", type=float, required=True)
    parser.add_argument("--minimum-shot-length-m", type=float, required=True)
    parser.add_argument("--nominal-speed-kmh", type=float, required=True)
    parser.add_argument("--expected-yield-t-ha", type=float)
    parser.add_argument("--maneuver-time-s", type=float)
    parser.add_argument("--max-cross-slope-pct", type=float)
    parser.add_argument("--terrain-smoothing-sigma-m", type=float)
    parser.add_argument("--reference-alert-grade-pct", type=float)
    parser.add_argument(
        "--power-status",
        choices=["DECLARED_NONE", "NOT_REVIEWED"],
        default="NOT_REVIEWED",
    )
    parser.add_argument(
        "--constraint-review-status",
        choices=["COMPLETE", "PARTIAL", "NOT_REVIEWED"],
        default="NOT_REVIEWED",
    )
    parser.add_argument("--cross-field", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--cross-property", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--cross-property-permission",
        choices=["APPROVED", "DENIED", "NOT_REVIEWED"],
        default="NOT_REVIEWED",
    )
    parser.add_argument("--created-at", help="Optional deterministic ISO 8601 timestamp.")
    parser.add_argument("--user-name", default="platform-user")
    parser.add_argument("--user-organization", default="client-organization")
    parser.add_argument("--user-role", default="project-configurator")
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        inputs = _inputs_from_args(args)
        output = args.output.expanduser().resolve()
        protected_paths = {
            inputs.dtm_path.expanduser().resolve(),
            inputs.boundary_path.expanduser().resolve(),
            *(
                [inputs.topography_manifest_path.expanduser().resolve()]
                if inputs.topography_manifest_path is not None
                else []
            ),
        }
        if output in protected_paths:
            raise RequestBuildError("--output must not overwrite an input or topography manifest.")
        request = build_request(
            inputs,
            catalog_path=args.catalog,
            schema_path=args.schema,
        )
        report = write_validated_request(
            request,
            output,
            catalog_path=args.catalog,
            schema_path=args.schema,
        )
    except (RequestBuildError, ContractError, OSError, ValueError, KeyError) as exc:
        print(f"REQUEST_BUILD_BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "status": "VALID",
                "output": str(output),
                "request_id": request["request_id"],
                "parameter_count": report["parameter_count"],
                "power_inventory_status": report["power_inventory_status"],
                "release_blockers": report["release_blockers"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
