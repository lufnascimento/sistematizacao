"""Load, validate and resolve a TerraFlux project generation request.

This module deliberately has no GIS dependency.  It turns the validated request
contract into explicit engine parameters and traceable dataset paths; geometry
stages remain responsible for consuming spatial constraints.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from validate_project_inputs import (
        ContractError,
        _validate_value,
        load_json,
        validate_catalog,
        validate_request,
        validate_schema,
    )
except ModuleNotFoundError:  # Allows ``python -m scripts...`` from the repo root.
    from scripts.validate_project_inputs import (
        ContractError,
        _validate_value,
        load_json,
        validate_catalog,
        validate_request,
        validate_schema,
    )


REPO = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = REPO / "config" / "catalogo_parametros_projeto.json"
DEFAULT_SCHEMA = REPO / "schemas" / "project-generation-request.schema.json"

POWER_STATUS_ID = "constraints.overhead_power_line_inventory_status"
DTM_DATASET_ID = "terrain.dtm_dataset_ref"

# Only direct semantic equivalents belong here. Algorithm-search controls remain
# versioned internals until the request contract gives them explicit definitions.
ENGINE_PARAMETER_BINDINGS = {
    "agronomy.row_spacing_m": "row_spacing_m",
    "conservation.max_furrow_grade_pct": "reference_alert_grade_percent",
    "e0.reference_alert_grade_pct": "reference_alert_grade_percent",
    "e0.nominal_field_speed_kmh": "assumed_work_speed_kmh",
    "e0.maneuver_time_s": "assumed_turn_seconds",
    "terrain.smoothing_sigma_m": "terrain_smoothing_sigma_m",
}

E0_METADATA_PARAMETERS = {
    "e0.yield_proxy_t_ha",
    "agronomy.expected_yield_t_ha",
}


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO).as_posix()
    except ValueError:
        return str(resolved)


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _dataset_files(path: Path, dataset_format: str) -> list[Path]:
    if dataset_format.upper() != "SHP":
        return [path]
    files = sorted(
        candidate
        for candidate in path.parent.iterdir()
        if candidate.is_file() and candidate.stem.lower() == path.stem.lower()
    )
    return files or [path]


def _dataset_bundle_sha256(files: list[Path]) -> str:
    if len(files) == 1:
        return _sha256_file(files[0])
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.lower().encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256_file(path)))
    return digest.hexdigest()


def _resolve_source_path(source_ref: str, request_path: Path) -> tuple[Path, str]:
    source = Path(source_ref)
    if source.is_absolute():
        return source.resolve(), "ABSOLUTE_SOURCE_REF"

    repo_candidate = (REPO / source).resolve()
    request_candidate = (request_path.parent / source).resolve()
    if repo_candidate.exists():
        return repo_candidate, "REPOSITORY_RELATIVE_SOURCE_REF"
    if request_candidate.exists():
        return request_candidate, "REQUEST_RELATIVE_SOURCE_REF"
    return repo_candidate, "UNRESOLVED_REPOSITORY_RELATIVE_SOURCE_REF"


@dataclass(frozen=True)
class ResolvedProjectRequest:
    request_path: Path
    request: dict[str, Any]
    validation_report: dict[str, Any]
    catalog_by_id: dict[str, dict[str, Any]]
    parameters: dict[str, dict[str, Any]]
    applied_overrides: tuple[dict[str, Any], ...]
    catalog_path: Path
    schema_path: Path

    @property
    def requested_delivery_level(self) -> str:
        return self.request["requested_delivery_level"]

    @property
    def is_e0(self) -> bool:
        return self.requested_delivery_level == "E0_TRIAGEM"

    @property
    def power_inventory_status(self) -> str:
        return self.parameters[POWER_STATUS_ID]["value"]

    @property
    def requires_power_barrier_stage(self) -> bool:
        return self.power_inventory_status == "PROVIDED"

    @property
    def blocks_this_generator(self) -> bool:
        # This generator does not apply constraint geometry. A supplied power
        # axis must be buffered and split by the mandatory barrier stage before
        # any package from this request can claim topology PASS, including E0.
        return self.requires_power_barrier_stage

    def parameter(self, parameter_id: str) -> dict[str, Any] | None:
        return self.parameters.get(parameter_id)

    def value(self, parameter_id: str, default: Any = None) -> Any:
        record = self.parameter(parameter_id)
        return record["value"] if record is not None else default

    def engine_parameter_overrides(self) -> dict[str, Any]:
        legacy_grade = self.value("conservation.max_furrow_grade_pct")
        alert_grade = self.value("e0.reference_alert_grade_pct")
        if legacy_grade is not None and alert_grade is not None and legacy_grade != alert_grade:
            raise ContractError("Conflicting legacy and E0 grade references; resolve them before screening.")
        overrides = {
            engine_name: self.parameters[parameter_id]["value"]
            for parameter_id, engine_name in ENGINE_PARAMETER_BINDINGS.items()
            if parameter_id in self.parameters
        }
        headland_widths = self.value("fleet.required_headland_width_m")
        if isinstance(headland_widths, dict) and headland_widths:
            overrides["outer_headland_m"] = max(float(value) for value in headland_widths.values())
        return overrides

    def conservative_operation_limit(self, parameter_id: str, reducer: str) -> float | None:
        """Resolve a per-operation fleet dictionary without hiding its scope."""

        values = self.value(parameter_id)
        if not isinstance(values, dict) or not values:
            return None
        operations = set(self.value("fleet.operations_in_scope", []))
        applicable = [float(value) for operation, value in values.items() if operation in operations]
        if not applicable:
            return None
        if reducer == "max":
            return max(applicable)
        if reducer == "min":
            return min(applicable)
        raise ValueError(f"Unknown conservative reducer: {reducer}")

    def parameter_applications(self) -> dict[str, dict[str, Any]]:
        applications: dict[str, dict[str, Any]] = {}
        for parameter_id, engine_name in ENGINE_PARAMETER_BINDINGS.items():
            if parameter_id in self.parameters:
                applications[parameter_id] = {
                    "application": "ENGINE_PARAMETER",
                    "engine_parameter": engine_name,
                }
        for parameter_id in E0_METADATA_PARAMETERS:
            if parameter_id in self.parameters:
                applications[parameter_id] = {
                    "application": "E0_METADATA_ONLY",
                    "reason": "The geometric E0 generator does not calculate crop mass.",
                }
        return applications

    def dataset(self, dataset_id: str) -> dict[str, Any]:
        for dataset in self.request["input_datasets"]:
            if dataset["dataset_id"] == dataset_id:
                return dataset
        raise ContractError(f"Request parameter references an unknown dataset: {dataset_id}")

    def dataset_path(self, dataset_id: str, expected_role: str | None = None) -> tuple[Path, dict[str, Any]]:
        dataset = self.dataset(dataset_id)
        if expected_role is not None and dataset.get("role") != expected_role:
            raise ContractError(
                f"Dataset {dataset_id} has role {dataset.get('role')!r}; expected {expected_role!r}."
            )
        path, resolution = _resolve_source_path(dataset["source_ref"], self.request_path)
        if not path.is_file():
            raise ContractError(f"Resolved dataset path does not exist: {path}")
        files = _dataset_files(path, dataset["format"])
        bundle_sha256 = _dataset_bundle_sha256(files)
        declared_sha256 = dataset.get("checksum_sha256")
        if declared_sha256 is not None and declared_sha256.lower() != bundle_sha256:
            raise ContractError(
                f"Dataset {dataset_id} checksum differs from the resolved source bundle."
            )
        return path, {
            "dataset_id": dataset_id,
            "role": dataset["role"],
            "source_ref": dataset["source_ref"],
            "layer_name": dataset.get("layer_name"),
            "id_field": dataset.get("id_field"),
            "revision": dataset["revision"],
            "resolved_path": _display_path(path),
            "path_resolution": resolution,
            "source_bundle_sha256": bundle_sha256,
            "declared_checksum_sha256": declared_sha256,
            "source_files": [
                {
                    "path": _display_path(source_file),
                    "size_bytes": source_file.stat().st_size,
                    "sha256": _sha256_file(source_file),
                }
                for source_file in files
            ],
        }

    def dtm_path(self) -> tuple[Path, dict[str, Any]]:
        dataset_id = self.value(DTM_DATASET_ID)
        if not isinstance(dataset_id, str) or not dataset_id:
            raise ContractError(f"{DTM_DATASET_ID} must identify the DTM input dataset.")
        return self.dataset_path(dataset_id, expected_role="DTM")

    def field_boundary_path(self) -> tuple[Path, dict[str, Any]]:
        boundary_datasets = [
            dataset
            for dataset in self.request["input_datasets"]
            if dataset.get("role") == "FIELD_BOUNDARY"
        ]
        if len(boundary_datasets) != 1:
            raise ContractError(
                "A validated request must resolve exactly one FIELD_BOUNDARY dataset."
            )
        return self.dataset_path(
            boundary_datasets[0]["dataset_id"],
            expected_role="FIELD_BOUNDARY",
        )

    def metrics_manifest(
        self,
        *,
        dtm_resolution: dict[str, Any],
        boundary_resolution: dict[str, Any],
        output_paths: dict[str, Path],
    ) -> dict[str, Any]:
        if self.power_inventory_status == "PROVIDED":
            power_stage = "REQUIRED_SEPARATE_STAGE_NOT_APPLIED"
        elif self.power_inventory_status == "DECLARED_NONE":
            power_stage = "NOT_REQUIRED_BY_DECLARATION"
        else:
            power_stage = "UNRESOLVED_RELEASE_CAPPED_AT_E0"

        consumed_ids = set(self.parameter_applications())
        resolved_parameters = {
            parameter_id: {
                "value": record["value"],
                "unit": record["unit"],
                "parameter_class": record["parameter_class"],
                "provenance": record["provenance"],
                **(
                    {"applied_override": record["applied_override"]}
                    if "applied_override" in record
                    else {}
                ),
            }
            for parameter_id, record in sorted(self.parameters.items())
        }
        return {
            "mode": "VALIDATED_PROJECT_REQUEST",
            "request_id": self.request["request_id"],
            "project_id": self.request["project_id"],
            "request_path": _display_path(self.request_path),
            "request_sha256": hashlib.sha256(self.request_path.read_bytes()).hexdigest(),
            "canonical_request_sha256": _canonical_sha256(self.request),
            "parameter_catalog": {
                "path": _display_path(self.catalog_path),
                "sha256": hashlib.sha256(self.catalog_path.read_bytes()).hexdigest(),
            },
            "request_schema": {
                "path": _display_path(self.schema_path),
                "sha256": hashlib.sha256(self.schema_path.read_bytes()).hexdigest(),
            },
            "created_at": self.request["created_at"],
            "requested_delivery_level": self.requested_delivery_level,
            "revisions": self.request["revisions"],
            "scope": self.request["scope"],
            "validation": self.validation_report,
            "resolved_parameters": resolved_parameters,
            "parameter_applications": self.parameter_applications(),
            "registered_but_not_consumed_parameter_ids": sorted(set(self.parameters) - consumed_ids),
            "applied_overrides": list(self.applied_overrides),
            "constraint_stage": {
                "overhead_power_line_inventory_status": self.power_inventory_status,
                "power_barrier_application": power_stage,
                "geometry_clipping_applied_by_this_generator": False,
                "v1_policy": "NO_CROSSING; a provided axis must be buffered and clipped in a separate stage.",
            },
            "path_resolution": {
                "dtm": dtm_resolution,
                "boundary": boundary_resolution,
                "outputs": {
                    name: {
                        "resolved_path": _display_path(path),
                        "path_resolution": "LEGACY_FIXED_OUTPUT_PATH",
                    }
                    for name, path in output_paths.items()
                },
            },
        }


def _resolve_parameter_records(
    request: dict[str, Any],
    catalog_by_id: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], tuple[dict[str, Any], ...]]:
    records = {
        item["parameter_id"]: copy.deepcopy(item)
        for item in request["parameter_values"]
    }
    applied: list[dict[str, Any]] = []
    for override in request["overrides"]:
        if override["approval"]["status"] != "APPROVED":
            continue
        parameter_id = override["parameter_id"]
        if parameter_id not in records:
            raise ContractError(
                f"Approved override {override['override_id']} has no base parameter value: {parameter_id}"
            )
        _validate_value(
            override["proposed_value"],
            catalog_by_id[parameter_id]["value_schema"],
            f"override {override['override_id']}",
        )
        previous_value = records[parameter_id]["value"]
        records[parameter_id]["value"] = copy.deepcopy(override["proposed_value"])
        resolution = {
            "override_id": override["override_id"],
            "effect": override["effect"],
            "previous_value": previous_value,
            "resolved_value": override["proposed_value"],
            "reason": override["reason"],
            "requested_by": override["requested_by"],
            "requested_at": override["requested_at"],
            "approval": override["approval"],
        }
        records[parameter_id]["applied_override"] = resolution
        applied.append(resolution)
    return records, tuple(applied)


def load_project_request(
    request_path: Path,
    *,
    catalog_path: Path = DEFAULT_CATALOG,
    schema_path: Path = DEFAULT_SCHEMA,
) -> ResolvedProjectRequest:
    request_path = request_path.resolve()
    catalog = load_json(catalog_path.resolve())
    schema = load_json(schema_path.resolve())
    request = load_json(request_path)
    catalog_by_id = validate_catalog(catalog)
    validate_schema(schema, catalog)
    validate_request(request, schema, catalog_by_id)
    parameters, applied_overrides = _resolve_parameter_records(request, catalog_by_id)
    resolved_request = copy.deepcopy(request)
    resolved_request["parameter_values"] = [
        {
            key: copy.deepcopy(value)
            for key, value in parameters[item["parameter_id"]].items()
            if key != "applied_override"
        }
        for item in request["parameter_values"]
    ]
    validation_report = validate_request(resolved_request, schema, catalog_by_id)
    return ResolvedProjectRequest(
        request_path=request_path,
        request=request,
        validation_report=validation_report,
        catalog_by_id=catalog_by_id,
        parameters=parameters,
        applied_overrides=applied_overrides,
        catalog_path=catalog_path.resolve(),
        schema_path=schema_path.resolve(),
    )


def legacy_metrics_manifest(
    parameters: dict[str, Any],
    *,
    dtm_path: Path,
    boundary_path: Path,
    output_paths: dict[str, Path],
) -> dict[str, Any]:
    provenance = {
        parameter_id: {
            "origin": "E0_ASSUMPTION",
            "source_ref": "scripts/generate_sulcation_scenarios.py#Parameters",
            "revision": "legacy-e0-defaults",
            "applicability": "SCENARIO",
        }
        for parameter_id in sorted(parameters)
    }
    return {
        "mode": "LEGACY_E0_ASSUMPTIONS",
        "request_id": None,
        "project_id": None,
        "requested_delivery_level": "E0_TRIAGEM",
        "revisions": None,
        "engine_parameter_sha256": _canonical_sha256(parameters),
        "parameter_provenance": provenance,
        "constraint_stage": {
            "overhead_power_line_inventory_status": "NOT_REVIEWED",
            "power_barrier_application": "UNRESOLVED_RELEASE_CAPPED_AT_E0",
            "geometry_clipping_applied_by_this_generator": False,
        },
        "path_resolution": {
            "dtm": {
                "resolved_path": _display_path(dtm_path),
                "path_resolution": "LEGACY_FIXED_PATH",
            },
            "boundary": {
                "resolved_path": _display_path(boundary_path),
                "path_resolution": "LEGACY_FIXED_PATH",
            },
            "outputs": {
                name: {
                    "resolved_path": _display_path(path),
                    "path_resolution": "LEGACY_FIXED_OUTPUT_PATH",
                }
                for name, path in output_paths.items()
            },
        },
    }
