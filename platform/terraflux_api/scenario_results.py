"""Conservative adapters for real E0 and CF0 engine results.

The geometry engines intentionally stop before hydraulic approval and machine
guidance.  This module keeps that release boundary while translating their
manifests into records that the platform can persist and compare.
"""

from __future__ import annotations

import json
import hashlib
import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any


E0_STATUS = "E0_topographic_sulcation_geometry_screening"
E0_RELEASE_LEVEL = "E0_topographic_screening"
CF0_SCHEMA_VERSION = "1.2.0"
CF0_MANIFEST_TYPE = "CONTINUOUS_FAMILY_STAGE_RESULT"
CF0_RELEASE = "CF0_GEOMETRIC_SCREENING"
HYDRAULIC_UNCONFIRMED = "HYDRAULIC_UNCONFIRMED"
CF0_NO_FEASIBLE_FAMILY = "NO_FEASIBLE_FAMILY"
C1_SCHEMA_VERSION = "1.0.0"
C1_MANIFEST_TYPE = "EMBEDDED_TERRACE_SCREENING_STAGE_RESULT"
C1_RELEASE = "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED"
C1_SCREENING_ONLY = "SCREENING_ONLY_PCE_PCX_UNCONFIRMED"
C1_NO_GEOMETRIC_PRECURSOR = "NO_GEOMETRIC_PRECURSOR"
C1_REQUIRED_LIMITATIONS = frozenset(
    {
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
    }
)
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_CF0_GEOMETRIC_STATUSES = {"GEOMETRIC_PASS", "NO_FEASIBLE_FAMILY"}


class ScenarioResultError(ValueError):
    """Raised when an engine result cannot be safely exposed by the platform."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ScenarioResultError(message)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    _require(isinstance(value, list), f"{label} must be an array")
    return value


def _text(value: Any, label: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), f"{label} must be a non-empty string")
    return value.strip()


def _number(value: Any, label: str, *, minimum: float | None = None) -> float:
    _require(not isinstance(value, bool) and isinstance(value, (int, float)), f"{label} must be numeric")
    result = float(value)
    _require(math.isfinite(result), f"{label} must be finite")
    if minimum is not None:
        _require(result >= minimum, f"{label} must be at least {minimum}")
    return result


def _integer(value: Any, label: str, *, minimum: int = 0) -> int:
    number = _number(value, label, minimum=float(minimum))
    _require(number.is_integer(), f"{label} must be an integer")
    return int(number)


def _sha256(value: Any, label: str) -> str:
    text = _text(value, label)
    _require(bool(_SHA256.fullmatch(text)), f"{label} must be a SHA-256 digest")
    return text.lower()


def _load(source: Mapping[str, Any] | str | Path, label: str) -> dict[str, Any]:
    if isinstance(source, Mapping):
        return dict(source)
    path = Path(source)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioResultError(f"cannot read {label}: {exc}") from exc
    return dict(_mapping(payload, label))


def _reject_authorization_claims(value: Any, path: str = "result") -> None:
    """Reject explicit approval claims wherever an engine adds them later."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            item_path = f"{path}.{key}"
            normalized_key = str(key).lower()
            if normalized_key == "guidance_authorized":
                _require(item is False, f"{item_path} cannot authorize machine guidance")
            elif normalized_key == "guidance_status" and item is not None:
                _require(
                    str(item).upper() in {"NOT_AUTHORIZED", "NOT_EVALUATED", "PENDING", "UNCONFIRMED"},
                    f"{item_path} contains a forbidden guidance claim",
                )
            _reject_authorization_claims(item, item_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_authorization_claims(item, f"{path}[{index}]")


def _validate_output_record(record: Any, label: str) -> dict[str, Any]:
    item = dict(_mapping(record, label))
    item["path"] = _text(item.get("path"), f"{label}.path")
    item["size_bytes"] = _integer(item.get("size_bytes"), f"{label}.size_bytes")
    item["sha256"] = _sha256(item.get("sha256"), f"{label}.sha256")
    return item


def _validate_c1_file_record(record: Any, label: str, expected_role: str) -> dict[str, Any]:
    item = dict(_mapping(record, label))
    _require(
        set(item) == {"path", "size_bytes", "sha256", "role"},
        f"{label} fields changed",
    )
    item["path"] = _text(item.get("path"), f"{label}.path")
    item["size_bytes"] = _integer(item.get("size_bytes"), f"{label}.size_bytes", minimum=1)
    item["sha256"] = _sha256(item.get("sha256"), f"{label}.sha256")
    _require(item.get("role") == expected_role, f"{label}.role must be {expected_role}")
    return item


def _validate_c1_request_ref(reference: Any, label: str) -> dict[str, Any]:
    item = dict(_mapping(reference, label))
    _require(set(item) == {"id", "path", "sha256"}, f"{label} fields changed")
    item["id"] = _text(item.get("id"), f"{label}.id")
    item["path"] = _text(item.get("path"), f"{label}.path")
    item["sha256"] = _sha256(item.get("sha256"), f"{label}.sha256")
    return item


def _c1_candidate_id(field_id: str, vertical_interval_m: float, offset_fraction: float) -> str:
    interval = f"{vertical_interval_m:.3f}".rstrip("0").rstrip(".").replace(".", "P")
    offset = f"{offset_fraction:.2f}".replace(".", "P")
    return f"C1E0_TI_{field_id}_VI{interval}_O{offset}"


def validate_e0_result(
    source: Mapping[str, Any] | str | Path,
    *,
    expected_project_id: str | None = None,
    expected_request_id: str | None = None,
    expected_request_sha256: str | None = None,
) -> dict[str, Any]:
    """Load and validate the E0 metric manifest and its immutable lineage."""

    result = _load(source, "E0 result")
    _require(result.get("status") == E0_STATUS, f"E0 status must be {E0_STATUS}")
    _require(result.get("source_files_unchanged") is True, "E0 source files must remain unchanged")
    _require(result.get("maximum_output_delivery_level") == "E0_TRIAGEM", "E0 delivery boundary changed")
    _require(result.get("requested_level_achieved") is True, "E0 generation did not achieve its requested level")

    forbidden_uses = {_text(item, "E0 not_authorized_for item").lower() for item in _list(result.get("not_authorized_for"), "E0 not_authorized_for")}
    _require("machine guidance" in forbidden_uses, "E0 result lost the machine-guidance prohibition")
    _require("hydraulic approval" in forbidden_uses, "E0 result lost the hydraulic-approval prohibition")

    generation = _mapping(result.get("generation_request"), "E0 generation_request")
    _require(generation.get("mode") == "VALIDATED_PROJECT_REQUEST", "E0 was not generated from a validated project request")
    project_id = _text(generation.get("project_id"), "E0 generation_request.project_id")
    request_id = _text(generation.get("request_id"), "E0 generation_request.request_id")
    request_sha256 = _sha256(generation.get("request_sha256"), "E0 generation_request.request_sha256")
    if expected_project_id is not None:
        _require(project_id == expected_project_id, "E0 project_id differs from the immutable platform request")
    if expected_request_id is not None:
        _require(request_id == expected_request_id, "E0 request_id differs from the immutable platform request")
    if expected_request_sha256 is not None:
        _require(request_sha256 == _sha256(expected_request_sha256, "expected_request_sha256"), "E0 request SHA-256 differs from the immutable platform request")

    definitions = _list(result.get("scenario_definitions"), "E0 scenario_definitions")
    definition_by_id: dict[str, Mapping[str, Any]] = {}
    for index, raw in enumerate(definitions):
        definition = _mapping(raw, f"E0 scenario_definitions[{index}]")
        scenario_id = _text(definition.get("id"), f"E0 scenario_definitions[{index}].id")
        _require(scenario_id not in definition_by_id, f"duplicate E0 scenario definition {scenario_id}")
        definition_by_id[scenario_id] = definition

    rows = _list(result.get("scenario_summary"), "E0 scenario_summary")
    _require(bool(rows), "E0 scenario_summary cannot be empty")
    observed_pairs: set[tuple[str, str]] = set()
    observed_scenarios: set[str] = set()
    for index, raw in enumerate(rows):
        row = _mapping(raw, f"E0 scenario_summary[{index}]")
        scenario_id = _text(row.get("scenario_id"), f"E0 scenario_summary[{index}].scenario_id")
        field_code = _text(row.get("field_code"), f"E0 scenario_summary[{index}].field_code")
        _require(scenario_id in definition_by_id, f"E0 row references undefined scenario {scenario_id}")
        pair = (scenario_id, field_code)
        _require(pair not in observed_pairs, f"duplicate E0 summary row for {scenario_id}/{field_code}")
        observed_pairs.add(pair)
        observed_scenarios.add(scenario_id)
        _require(row.get("release_level") == E0_RELEASE_LEVEL, f"{scenario_id}/{field_code} is outside the E0 release boundary")
        hydraulic = _text(row.get("hydraulic_status"), f"{scenario_id}/{field_code}.hydraulic_status").lower()
        _require(hydraulic.startswith("not_evaluated"), f"{scenario_id}/{field_code} contains a hydraulic approval claim")
        for key in ("gross_area_ha", "usable_area_ha", "total_line_km", "segment_count", "fragmentation_count"):
            _number(row.get(key), f"{scenario_id}/{field_code}.{key}", minimum=0.0)
        for key in ("internal_endpoint_count", "radius_violation_line_count"):
            if key in row:
                _integer(row.get(key), f"{scenario_id}/{field_code}.{key}")
        for key in ("candidate_conservation_loss", "candidate_harvestability_loss", "candidate_performance_loss"):
            loss = _number(row.get(key), f"{scenario_id}/{field_code}.{key}", minimum=0.0)
            _require(loss <= 1.0, f"{scenario_id}/{field_code}.{key} must not exceed 1")

    _require(observed_scenarios == set(definition_by_id), "E0 definitions and summary scenarios differ")
    integrity = _mapping(result.get("output_integrity"), "E0 output_integrity")
    for key in ("geopackage", "map"):
        _validate_output_record(integrity.get(key), f"E0 output_integrity.{key}")
    _reject_authorization_claims(result, "E0")
    return result


def _weighted(rows: Sequence[Mapping[str, Any]], key: str, weight_key: str) -> float:
    denominator = sum(_number(row.get(weight_key), f"{key}.{weight_key}", minimum=0.0) for row in rows)
    if denominator <= 0:
        return 0.0
    return sum(
        _number(row.get(key), key) * _number(row.get(weight_key), f"{key}.{weight_key}", minimum=0.0)
        for row in rows
    ) / denominator


def aggregate_e0_scenarios(
    source: Mapping[str, Any] | str | Path,
    *,
    run_id: str,
    project_id: str,
    expected_request_id: str | None = None,
    expected_request_sha256: str | None = None,
    objective_weights: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Aggregate field-level E0 rows into comparison records ready for the UI.

    ``recommended`` means only the best E0 representative under the supplied
    comparison weights.  It never means hydraulic, agronomic or guidance
    approval.
    """

    run_id = _text(run_id, "run_id")
    project_id = _text(project_id, "project_id")
    result = validate_e0_result(
        source,
        expected_project_id=project_id,
        expected_request_id=expected_request_id,
        expected_request_sha256=expected_request_sha256,
    )
    weights = dict(objective_weights or {"conservation": 1.0, "harvestability": 1.0, "performance": 1.0})
    _require(set(weights) == {"conservation", "harvestability", "performance"}, "objective weights must contain conservation, harvestability and performance")
    for key, value in weights.items():
        weights[key] = _number(value, f"objective_weights.{key}", minimum=0.0)
    weight_total = sum(weights.values())
    _require(weight_total > 0, "at least one objective weight must be positive")

    definitions = {item["id"]: item for item in result["scenario_definitions"]}
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in result["scenario_summary"]:
        grouped[row["scenario_id"]].append(row)

    records: list[dict[str, Any]] = []
    for scenario_id in sorted(grouped):
        rows = grouped[scenario_id]
        definition = definitions[scenario_id]
        total_line_km = sum(_number(row["total_line_km"], "total_line_km", minimum=0.0) for row in rows)
        segment_count = sum(_integer(row["segment_count"], "segment_count") for row in rows)
        fragmentation_count = sum(_integer(row["fragmentation_count"], "fragmentation_count") for row in rows)
        usable_area_ha = sum(_number(row["usable_area_ha"], "usable_area_ha", minimum=0.0) for row in rows)
        conservation_score = 100.0 * (1.0 - _weighted(rows, "candidate_conservation_loss", "usable_area_ha"))
        harvestability_score = 100.0 * (1.0 - _weighted(rows, "candidate_harvestability_loss", "usable_area_ha"))
        operational_score = 100.0 * (1.0 - _weighted(rows, "candidate_performance_loss", "usable_area_ha"))
        comparison_score = (
            conservation_score * weights["conservation"]
            + harvestability_score * weights["harvestability"]
            + operational_score * weights["performance"]
        ) / weight_total
        hydraulic_statuses = sorted({str(row["hydraulic_status"]) for row in rows})
        blocker_codes = sorted(
            {
                code.strip()
                for row in rows
                for code in str(row.get("continuity_blocker_codes") or "").split(",")
                if code.strip()
            }
        )
        geometry_eligible = not blocker_codes
        metrics = {
            "total_line_km": round(total_line_km, 3),
            "usable_area_ha": round(usable_area_ha, 4),
            "segment_count": segment_count,
            "fragmentation_count": fragmentation_count,
            "average_shot_m": round(total_line_km * 1000.0 / segment_count, 2) if segment_count else 0.0,
            # The engine publishes field P90, not a project P95. Keep the UI P95 empty.
            "p95_shot_m": None,
            "shot_p90_weighted_m": round(_weighted(rows, "segment_length_p90_m", "segment_count"), 2),
            "maneuvers_per_ha": None,
            "segment_density_per_ha": round(segment_count / usable_area_ha, 3) if usable_area_ha else None,
            "fragmentation_per_ha": round(fragmentation_count / usable_area_ha, 3) if usable_area_ha else None,
            "internal_endpoint_count": sum(int(row.get("internal_endpoint_count") or 0) for row in rows),
            "radius_violation_line_count": sum(int(row.get("radius_violation_line_count") or 0) for row in rows),
            "cross_slope_p95_pct": round(_weighted(rows, "cross_grade_p95_weighted_percent", "total_line_km"), 3),
            "absolute_grade_p95_pct": round(_weighted(rows, "absolute_grade_p95_weighted_percent", "total_line_km"), 3),
            "coverage_proxy_percent": round(_weighted(rows, "coverage_proxy_percent", "usable_area_ha"), 3),
            "conservation_score": round(conservation_score, 2),
            "harvestability_score": round(harvestability_score, 2),
            "operational_score": round(operational_score, 2),
            "comparison_score": round(comparison_score, 2),
        }
        records.append(
            {
                "id": f"{run_id}:{scenario_id}",
                "run_id": run_id,
                "project_id": project_id,
                "code": scenario_id,
                "name": _text(definition.get("name"), f"scenario {scenario_id} name"),
                "family": "E0 - triagem geometrica topografica",
                "status": (
                    "E0_SCREENING_ONLY_NOT_AUTHORIZED"
                    if geometry_eligible
                    else "E0_INFEASIBLE_GEOMETRY_DIAGNOSTIC_ONLY"
                ),
                "geometry_eligible": geometry_eligible,
                "blocker_codes": blocker_codes,
                "recommended": False,
                "recommendation_scope": "E0_COMPARISON_REPRESENTATIVE_ONLY",
                "selection_strategy": _text(definition.get("selection"), f"scenario {scenario_id} selection"),
                "metrics": metrics,
                "field_ids": sorted(str(row["field_code"]) for row in rows),
                "hydraulic_statuses": hydraulic_statuses,
                "guidance_authorized": False,
                "release_level": E0_RELEASE_LEVEL,
                "source_lineage": {
                    "request_id": result["generation_request"]["request_id"],
                    "request_sha256": result["generation_request"]["request_sha256"].lower(),
                    "metric_status": E0_STATUS,
                },
            }
        )

    eligible = [item for item in records if item["geometry_eligible"]]
    if eligible:
        representative = min(
            eligible,
            key=lambda item: (-item["metrics"]["comparison_score"], item["code"]),
        )
        representative["recommended"] = True
    return records


def validate_cf0_result(
    source: Mapping[str, Any] | str | Path,
    *,
    expected_request_id: str | None = None,
    expected_request_sha256: str | None = None,
) -> dict[str, Any]:
    """Load a CF0 stage manifest without promoting it beyond screening."""

    result = _load(source, "CF0 result")
    _require(result.get("schema_version") == CF0_SCHEMA_VERSION, f"CF0 schema must be {CF0_SCHEMA_VERSION}")
    _require(result.get("manifest_type") == CF0_MANIFEST_TYPE, f"CF0 manifest_type must be {CF0_MANIFEST_TYPE}")
    _require(result.get("release") == CF0_RELEASE, f"CF0 release must be {CF0_RELEASE}")
    _require(
        result.get("stage_status") in {HYDRAULIC_UNCONFIRMED, CF0_NO_FEASIBLE_FAMILY},
        "CF0 cannot claim hydraulic approval",
    )
    limitations = {_text(item, "CF0 release limitation") for item in _list(result.get("release_limitations"), "CF0 release_limitations")}
    _require("CF0_NOT_FOR_GUIDANCE" in limitations, "CF0 lost its guidance prohibition")
    _require(HYDRAULIC_UNCONFIRMED in limitations, "CF0 lost its hydraulic warning")

    request_ref = _mapping(result.get("project_request_ref"), "CF0 project_request_ref")
    request_id = _text(request_ref.get("id"), "CF0 project_request_ref.id")
    request_sha256 = _sha256(request_ref.get("sha256"), "CF0 project_request_ref.sha256")
    if expected_request_id is not None:
        _require(request_id == expected_request_id, "CF0 request_id differs from the immutable platform request")
    if expected_request_sha256 is not None:
        _require(request_sha256 == _sha256(expected_request_sha256, "expected_request_sha256"), "CF0 request SHA-256 differs from the immutable platform request")

    candidates = _list(result.get("candidates"), "CF0 candidates")
    _require(bool(candidates), "CF0 candidates cannot be empty")
    observed: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(candidates):
        item = _mapping(raw, f"CF0 candidates[{index}]")
        candidate_id = _text(item.get("candidate_id"), f"CF0 candidates[{index}].candidate_id")
        field_id = _text(item.get("field_id"), f"CF0 candidates[{index}].field_id")
        work_block_id = _text(item.get("work_block_id"), f"CF0 candidates[{index}].work_block_id")
        identity = (candidate_id, field_id, work_block_id)
        _require(identity not in observed, f"duplicate CF0 candidate block {identity}")
        observed.add(identity)
        geometric_status = _text(item.get("geometric_status"), f"{identity}.geometric_status")
        _require(geometric_status in _CF0_GEOMETRIC_STATUSES, f"{identity} has an unknown geometric status")
        _require(item.get("hydraulic_status") == HYDRAULIC_UNCONFIRMED, f"{identity} cannot claim hydraulic approval")
        row_count = _integer(item.get("row_count"), f"{identity}.row_count")
        total_length_m = _number(item.get("total_length_m"), f"{identity}.total_length_m", minimum=0.0)
        blockers = _list(item.get("blocker_codes"), f"{identity}.blocker_codes")
        if geometric_status == "NO_FEASIBLE_FAMILY":
            _require(bool(blockers), f"{identity} failed without blocker codes")
            _require(row_count == 0 and total_length_m == 0.0, f"{identity} failed but exposes released rows")

    pass_count = sum(item.get("geometric_status") == "GEOMETRIC_PASS" for item in candidates)
    expected_stage = HYDRAULIC_UNCONFIRMED if pass_count else CF0_NO_FEASIBLE_FAMILY
    _require(
        result.get("stage_status") == expected_stage,
        "CF0 stage_status is inconsistent with its geometric candidates",
    )
    qa = _mapping(result.get("qa"), "CF0 qa")
    expected_qa = {
        "candidate_field_count": len(candidates),
        "geometric_pass_count": pass_count,
        "no_feasible_family_count": len(candidates) - pass_count,
        "hydraulic_unconfirmed_count": len(candidates),
    }
    for key, expected in expected_qa.items():
        _require(
            _integer(qa.get(key), f"CF0 qa.{key}") == expected,
            f"CF0 qa.{key} is inconsistent with its candidates",
        )

    outputs = _mapping(result.get("outputs"), "CF0 outputs")
    _validate_output_record(outputs.get("geopackage"), "CF0 outputs.geopackage")
    _validate_output_record(outputs.get("map"), "CF0 outputs.map")
    for index, record in enumerate(_list(outputs.get("rasters"), "CF0 outputs.rasters")):
        _validate_output_record(record, f"CF0 outputs.rasters[{index}]")
    _reject_authorization_claims(result, "CF0")
    return result


def summarize_cf0_candidates(
    source: Mapping[str, Any] | str | Path,
    *,
    run_id: str,
    project_id: str,
    expected_request_id: str | None = None,
    expected_request_sha256: str | None = None,
) -> list[dict[str, Any]]:
    """Return technical CF0 screening summaries grouped by candidate ID."""

    result = validate_cf0_result(
        source,
        expected_request_id=expected_request_id,
        expected_request_sha256=expected_request_sha256,
    )
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in result["candidates"]:
        grouped[item["candidate_id"]].append(item)

    summaries: list[dict[str, Any]] = []
    for candidate_id in sorted(grouped):
        blocks = grouped[candidate_id]
        passed = sum(item["geometric_status"] == "GEOMETRIC_PASS" for item in blocks)
        status = "CF0_GEOMETRIC_PASS_HYDRAULIC_UNCONFIRMED" if passed == len(blocks) else (
            "CF0_PARTIAL_GEOMETRIC_SCREENING" if passed else "CF0_NO_FEASIBLE_FAMILY"
        )
        summaries.append(
            {
                "id": f"{run_id}:{candidate_id}",
                "run_id": _text(run_id, "run_id"),
                "project_id": _text(project_id, "project_id"),
                "code": candidate_id,
                "name": candidate_id.replace("_", " ").title(),
                "family": "CF0 - familia curva continua",
                "status": status,
                "recommended": False,
                "guidance_authorized": False,
                "hydraulic_status": HYDRAULIC_UNCONFIRMED,
                "metrics": {
                    "work_block_count": len(blocks),
                    "geometric_pass_count": passed,
                    "row_count": sum(_integer(item["row_count"], "row_count") for item in blocks),
                    "total_line_km": round(sum(_number(item["total_length_m"], "total_length_m", minimum=0.0) for item in blocks) / 1000.0, 3),
                    "field_count": len({str(item["field_id"]) for item in blocks}),
                },
                "blocker_codes": sorted({str(code) for item in blocks for code in item["blocker_codes"]}),
                "source_lineage": {
                    "request_id": result["project_request_ref"]["id"],
                    "request_sha256": result["project_request_ref"]["sha256"].lower(),
                    "release": CF0_RELEASE,
                },
            }
        )
    return summaries


def cf0_artifact_records(source: Mapping[str, Any] | str | Path) -> list[dict[str, Any]]:
    """Normalize the CF0 vector, map and diagnostic raster declarations."""

    result = validate_cf0_result(source)
    outputs = result["outputs"]
    artifacts: list[dict[str, Any]] = []
    for key, artifact_type, media_type, previewable in (
        ("geopackage", "CF0_VECTOR_PACKAGE", "application/geopackage+sqlite3", False),
        ("map", "CF0_COMPARATIVE_MAP", "image/png", True),
    ):
        item = _validate_output_record(outputs[key], f"CF0 outputs.{key}")
        artifacts.append(
            {
                "artifact_type": artifact_type,
                "role": item.get("role") or artifact_type,
                "path": item["path"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
                "media_type": media_type,
                "previewable": previewable,
            }
        )
    for index, raw in enumerate(outputs["rasters"]):
        item = _validate_output_record(raw, f"CF0 outputs.rasters[{index}]")
        artifacts.append(
            {
                "artifact_type": "CF0_DIAGNOSTIC_RASTER",
                "role": _text(item.get("raster_role"), f"CF0 raster {index}.raster_role"),
                "path": item["path"],
                "sha256": item["sha256"],
                "size_bytes": item["size_bytes"],
                "media_type": "image/tiff; application=geotiff",
                "previewable": False,
                "candidate_id": _text(item.get("candidate_id"), f"CF0 raster {index}.candidate_id"),
                "field_id": _text(item.get("field_id"), f"CF0 raster {index}.field_id"),
                "work_block_id": _text(item.get("work_block_id"), f"CF0 raster {index}.work_block_id"),
            }
        )
    return artifacts


def validate_c1_result(
    source: Mapping[str, Any] | str | Path,
    *,
    expected_project_request_id: str | None = None,
    expected_project_request_sha256: str | None = None,
    expected_sensitivity_request_id: str | None = None,
    expected_sensitivity_request_sha256: str | None = None,
    expected_cf0_manifest_sha256: str | None = None,
    expected_cf0_geopackage_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate a C1 TI sensitivity manifest without promoting it to design.

    The result is intentionally limited to a complete geometric sensitivity
    matrix.  PCE, PCX, hydraulic function, construction, operation and machine
    guidance must all remain unresolved.
    """

    result = _load(source, "C1 result")
    expected_top_level = {
        "schema_version",
        "manifest_type",
        "release",
        "generated_at",
        "project_request_ref",
        "sensitivity_request_ref",
        "stage_status",
        "crs",
        "inputs",
        "assumptions",
        "variant_status",
        "release_limitations",
        "candidates",
        "td_not_generated",
        "qa",
        "layer_counts",
        "outputs",
    }
    _require(set(result) == expected_top_level, "C1 top-level fields changed")
    _require(result.get("schema_version") == C1_SCHEMA_VERSION, f"C1 schema must be {C1_SCHEMA_VERSION}")
    _require(result.get("manifest_type") == C1_MANIFEST_TYPE, f"C1 manifest_type must be {C1_MANIFEST_TYPE}")
    _require(result.get("release") == C1_RELEASE, f"C1 release must be {C1_RELEASE}")
    generated_at = _text(result.get("generated_at"), "C1 generated_at")
    try:
        datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScenarioResultError("C1 generated_at must be an ISO-8601 timestamp") from exc

    project_ref = _validate_c1_request_ref(result.get("project_request_ref"), "C1 project_request_ref")
    sensitivity_ref = _validate_c1_request_ref(result.get("sensitivity_request_ref"), "C1 sensitivity_request_ref")
    for expected, observed, label in (
        (expected_project_request_id, project_ref["id"], "C1 project request id"),
        (expected_sensitivity_request_id, sensitivity_ref["id"], "C1 sensitivity request id"),
    ):
        if expected is not None:
            _require(observed == _text(expected, f"expected {label}"), f"{label} differs from the immutable request")
    for expected, observed, label in (
        (expected_project_request_sha256, project_ref["sha256"], "C1 project request SHA-256"),
        (expected_sensitivity_request_sha256, sensitivity_ref["sha256"], "C1 sensitivity request SHA-256"),
    ):
        if expected is not None:
            _require(observed == _sha256(expected, f"expected {label}"), f"{label} differs from the immutable request")

    inputs = _mapping(result.get("inputs"), "C1 inputs")
    expected_input_roles = {
        "terrain_dtm": "DTM_E0_SCREENING",
        "field_boundary": "FIELD_BOUNDARY",
        "cf0_manifest": "CF0_STAGE_MANIFEST",
        "cf0_geopackage": "CF0_VECTOR_SOURCE",
    }
    _require(set(inputs) == set(expected_input_roles), "C1 input set changed")
    validated_inputs = {
        key: _validate_c1_file_record(inputs.get(key), f"C1 inputs.{key}", role)
        for key, role in expected_input_roles.items()
    }
    if expected_cf0_manifest_sha256 is not None:
        _require(
            validated_inputs["cf0_manifest"]["sha256"]
            == _sha256(expected_cf0_manifest_sha256, "expected_cf0_manifest_sha256"),
            "C1 CF0 manifest SHA-256 differs from its dependency",
        )
    if expected_cf0_geopackage_sha256 is not None:
        _require(
            validated_inputs["cf0_geopackage"]["sha256"]
            == _sha256(expected_cf0_geopackage_sha256, "expected_cf0_geopackage_sha256"),
            "C1 CF0 GeoPackage SHA-256 differs from its dependency",
        )

    crs = _mapping(result.get("crs"), "C1 crs")
    _require(
        set(crs) == {"type", "authority", "code", "horizontal_unit", "wkt", "wkt_sha256"},
        "C1 CRS fields changed",
    )
    _require(crs.get("type") == "PROJECTED", "C1 CRS must be projected")
    _require(crs.get("authority") == "EPSG", "C1 CRS authority must be EPSG")
    _integer(crs.get("code"), "C1 crs.code", minimum=1)
    _require(crs.get("horizontal_unit") == "m", "C1 CRS horizontal unit must be metre")
    wkt = _text(crs.get("wkt"), "C1 crs.wkt")
    _require(
        hashlib.sha256(wkt.encode("utf-8")).hexdigest() == _sha256(crs.get("wkt_sha256"), "C1 crs.wkt_sha256"),
        "C1 CRS WKT hash mismatch",
    )

    assumptions = _mapping(result.get("assumptions"), "C1 assumptions")
    expected_assumption_fields = {
        "parameter_class",
        "selection_meaning",
        "selection_rule",
        "interval_role",
        "vertical_accuracy_status",
        "vertical_interval_candidates_m",
        "offset_fractions",
        "topology_gap_half_width_m",
        "topology_gap_role",
        "row_source_candidate_id",
        "row_source_method",
    }
    _require(set(assumptions) == expected_assumption_fields, "C1 assumption fields changed")
    expected_assumptions = {
        "parameter_class": "E0_ASSUMPTION",
        "selection_meaning": "GEOMETRIC_SENSITIVITY_GRID_ONLY",
        "selection_rule": "DATASET_RELIEF_LEGIBILITY_GRID_V1",
        "interval_role": "ELEVATION_ISOLINE_SAMPLING_NOT_PCE_SPACING",
        "vertical_accuracy_status": "NOT_VALIDATED",
        "topology_gap_role": "NUMERICAL_PARTITION_SUPPORT_NOT_SECTION_WIDTH",
        "row_source_candidate_id": "CF0C_OPERACAO",
        "row_source_method": "CLIP_EXISTING_CF0C_ROWS_BY_TOPOLOGY_ONLY_INTERTERRACE_STRIPS",
    }
    for key, expected in expected_assumptions.items():
        _require(assumptions.get(key) == expected, f"C1 assumptions.{key} must be {expected}")
    intervals = [
        _number(value, f"C1 vertical_interval_candidates_m[{index}]", minimum=0.0)
        for index, value in enumerate(_list(assumptions.get("vertical_interval_candidates_m"), "C1 vertical_interval_candidates_m"))
    ]
    _require(intervals == [2.0, 4.0, 6.0], "C1 vertical interval grid changed without a new selection rule")
    offsets = [
        _number(value, f"C1 offset_fractions[{index}]", minimum=0.0)
        for index, value in enumerate(_list(assumptions.get("offset_fractions"), "C1 offset_fractions"))
    ]
    _require(bool(offsets), "C1 offset fractions cannot be empty")
    _require(offsets == sorted(set(offsets)), "C1 offset fractions must be unique and increasing")
    _require(all(value < 1.0 for value in offsets), "C1 offset fractions must be below 1")
    topology_gap = _number(assumptions.get("topology_gap_half_width_m"), "C1 topology_gap_half_width_m", minimum=0.0)
    _require(topology_gap > 0.0, "C1 topology gap must be positive")

    limitations = {
        _text(item, "C1 release limitation")
        for item in _list(result.get("release_limitations"), "C1 release_limitations")
    }
    _require(limitations == C1_REQUIRED_LIMITATIONS, "C1 release limitations changed")

    variants = _mapping(result.get("variant_status"), "C1 variant_status")
    _require(set(variants) == {"EMBUTIDA_TI", "EMBUTIDA_TD"}, "C1 variant set changed")
    ti_variant = _mapping(variants.get("EMBUTIDA_TI"), "C1 EMBUTIDA_TI")
    _require(
        dict(ti_variant)
        == {
            "status": "GENERATED_SCREENING_ONLY_NOT_DIMENSIONED",
            "geometry_role": "TERRAIN_ISOLINE_SENSITIVITY_NOT_APPROVED_TI",
            "pce_status": "NOT_EVALUATED",
            "pcx_status": "NOT_EVALUATED",
            "hydraulic_status": HYDRAULIC_UNCONFIRMED,
        },
        "C1 TI release boundary changed",
    )
    td_variant = _mapping(variants.get("EMBUTIDA_TD"), "C1 EMBUTIDA_TD")
    _require(
        dict(td_variant)
        == {
            "status": "NOT_GENERATED_RECEIVER_MISSING",
            "reason": "VERIFIED_RECEIVER_AND_LONGITUDINAL_GRADE_RULE_REQUIRED",
            "geometry_count": 0,
            "pce_status": "NOT_EVALUATED",
            "pcx_status": "NOT_EVALUATED",
            "hydraulic_status": HYDRAULIC_UNCONFIRMED,
        },
        "C1 TD release boundary changed",
    )

    candidate_fields = {
        "candidate_id",
        "field_id",
        "variant",
        "vertical_interval_m",
        "offset_fraction",
        "screening_status",
        "eligibility_status",
        "axis_count",
        "strip_count",
        "diagnostic_row_segment_count",
        "partition_qa",
        "metrics",
        "pce_status",
        "pcx_status",
        "hydraulic_status",
        "construction_status",
        "operational_status",
        "guidance_status",
        "blocker_codes",
    }
    partition_fields = {
        "method",
        "topology_gap_half_width_m",
        "topology_gap_area_m2",
        "retained_strip_count",
        "excluded_small_component_count",
        "excluded_small_component_area_m2",
        "partitioned_area_m2",
        "scope_note",
    }
    metric_fields = {
        "terrace_total_length_m",
        "topology_gap_area_m2",
        "partitioned_area_m2",
        "source_cf0c_row_count",
        "source_cf0c_total_length_m",
        "diagnostic_segment_count",
        "diagnostic_total_length_m",
        "diagnostic_length_p05_m",
        "diagnostic_length_p50_m",
        "diagnostic_length_p95_m",
        "diagnostic_length_max_m",
        "split_source_row_count",
    }
    required_candidate_blockers = {
        "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED",
        "EMBEDDED_SECTION_NOT_APPLIED",
        "GUIDANCE_NOT_AUTHORIZED",
        "PCE_SOLVER_NOT_RUN",
        "PCX_SOLVER_NOT_RUN",
        "PROPOSED_DTM_NOT_GENERATED",
        "ROW_CANDIDATES_DERIVED_FROM_CF0C_NOT_RESOLVED_PER_STRIP",
        "TI_HYDRAULIC_FUNCTION_NOT_CONFIRMED",
        "VERTICAL_ACCURACY_NOT_VALIDATED",
    }
    candidates = _list(result.get("candidates"), "C1 candidates")
    _require(bool(candidates), "C1 candidates cannot be empty")
    observed_ids: set[str] = set()
    observed_matrix: set[tuple[str, float, float]] = set()
    normalized_candidates: list[dict[str, Any]] = []
    for index, raw in enumerate(candidates):
        label = f"C1 candidates[{index}]"
        candidate = dict(_mapping(raw, label))
        _require(set(candidate) == candidate_fields, f"{label} fields changed")
        candidate_id = _text(candidate.get("candidate_id"), f"{label}.candidate_id")
        field_id = _text(candidate.get("field_id"), f"{label}.field_id")
        interval = _number(candidate.get("vertical_interval_m"), f"{label}.vertical_interval_m", minimum=0.0)
        offset = _number(candidate.get("offset_fraction"), f"{label}.offset_fraction", minimum=0.0)
        _require(interval in intervals and offset in offsets, f"{label} is outside the declared sensitivity matrix")
        _require(candidate_id == _c1_candidate_id(field_id, interval, offset), f"{label}.candidate_id is inconsistent")
        _require(candidate_id not in observed_ids, f"duplicate C1 candidate_id {candidate_id}")
        matrix_key = (field_id, interval, offset)
        _require(matrix_key not in observed_matrix, f"duplicate C1 sensitivity case {matrix_key}")
        observed_ids.add(candidate_id)
        observed_matrix.add(matrix_key)
        _require(candidate.get("variant") == "EMBUTIDA_TI", f"{label} is not a TI sensitivity")
        screening_status = _text(candidate.get("screening_status"), f"{label}.screening_status")
        _require(
            screening_status in {"GEOMETRIC_PRECURSOR", C1_NO_GEOMETRIC_PRECURSOR},
            f"{label} has an unknown screening status",
        )
        _require(candidate.get("eligibility_status") == "GEOMETRIC_PRECURSOR", f"{label} eligibility changed")
        axis_count = _integer(candidate.get("axis_count"), f"{label}.axis_count")
        strip_count = _integer(candidate.get("strip_count"), f"{label}.strip_count")
        segment_count = _integer(candidate.get("diagnostic_row_segment_count"), f"{label}.diagnostic_row_segment_count")
        _require(
            (axis_count > 0 and strip_count > 0) == (screening_status == "GEOMETRIC_PRECURSOR"),
            f"{label} screening status is inconsistent with its geometry counts",
        )

        partition = _mapping(candidate.get("partition_qa"), f"{label}.partition_qa")
        _require(set(partition) == partition_fields, f"{label}.partition_qa fields changed")
        _require(partition.get("method") == "AXIS_BUFFER_TOPOLOGY_GAP_NOT_CROSS_SECTION", f"{label} partition method changed")
        _require(
            math.isclose(
                _number(partition.get("topology_gap_half_width_m"), f"{label}.partition_qa.topology_gap_half_width_m", minimum=0.0),
                topology_gap,
                abs_tol=1e-9,
            ),
            f"{label} topology gap differs from the declared assumption",
        )
        partition_gap_area = _number(partition.get("topology_gap_area_m2"), f"{label}.partition_qa.topology_gap_area_m2", minimum=0.0)
        retained_strips = _integer(partition.get("retained_strip_count"), f"{label}.partition_qa.retained_strip_count")
        _integer(partition.get("excluded_small_component_count"), f"{label}.partition_qa.excluded_small_component_count")
        _number(partition.get("excluded_small_component_area_m2"), f"{label}.partition_qa.excluded_small_component_area_m2", minimum=0.0)
        partitioned_area = _number(partition.get("partitioned_area_m2"), f"{label}.partition_qa.partitioned_area_m2", minimum=0.0)
        _text(partition.get("scope_note"), f"{label}.partition_qa.scope_note")
        _require(retained_strips == strip_count, f"{label} retained strip count mismatch")

        metrics = _mapping(candidate.get("metrics"), f"{label}.metrics")
        _require(set(metrics) == metric_fields, f"{label}.metrics fields changed")
        terrace_length = _number(metrics.get("terrace_total_length_m"), f"{label}.metrics.terrace_total_length_m", minimum=0.0)
        metric_gap_area = _number(metrics.get("topology_gap_area_m2"), f"{label}.metrics.topology_gap_area_m2", minimum=0.0)
        metric_partitioned_area = _number(metrics.get("partitioned_area_m2"), f"{label}.metrics.partitioned_area_m2", minimum=0.0)
        source_row_count = _integer(metrics.get("source_cf0c_row_count"), f"{label}.metrics.source_cf0c_row_count")
        _number(metrics.get("source_cf0c_total_length_m"), f"{label}.metrics.source_cf0c_total_length_m", minimum=0.0)
        metric_segment_count = _integer(metrics.get("diagnostic_segment_count"), f"{label}.metrics.diagnostic_segment_count")
        diagnostic_length = _number(metrics.get("diagnostic_total_length_m"), f"{label}.metrics.diagnostic_total_length_m", minimum=0.0)
        split_source_rows = _integer(metrics.get("split_source_row_count"), f"{label}.metrics.split_source_row_count")
        _require(metric_segment_count == segment_count, f"{label} diagnostic segment count mismatch")
        _require(split_source_rows <= source_row_count, f"{label} split source row count exceeds its source")
        _require(math.isclose(metric_gap_area, partition_gap_area, abs_tol=1e-6), f"{label} topology gap area mismatch")
        _require(math.isclose(metric_partitioned_area, partitioned_area, abs_tol=1e-6), f"{label} partitioned area mismatch")
        if axis_count == 0:
            _require(terrace_length == 0.0, f"{label} has terrace length without axes")
        percentile_values = []
        for key in (
            "diagnostic_length_p05_m",
            "diagnostic_length_p50_m",
            "diagnostic_length_p95_m",
            "diagnostic_length_max_m",
        ):
            value = metrics.get(key)
            if metric_segment_count:
                percentile_values.append(_number(value, f"{label}.metrics.{key}", minimum=0.0))
            else:
                _require(value is None, f"{label}.metrics.{key} must be null without segments")
        if percentile_values:
            _require(percentile_values == sorted(percentile_values), f"{label} diagnostic length quantiles are inconsistent")
            _require(diagnostic_length > 0.0, f"{label} has diagnostic segments without length")

        for key, expected in (
            ("pce_status", "NOT_EVALUATED"),
            ("pcx_status", "NOT_EVALUATED"),
            ("hydraulic_status", HYDRAULIC_UNCONFIRMED),
            ("construction_status", "NOT_EVALUATED"),
            ("operational_status", "DIAGNOSTIC_ONLY_NOT_ROUTED"),
            ("guidance_status", "NOT_AUTHORIZED"),
        ):
            _require(candidate.get(key) == expected, f"{label}.{key} must be {expected}")
        blocker_list = _list(candidate.get("blocker_codes"), f"{label}.blocker_codes")
        blockers = {_text(value, f"{label}.blocker_code") for value in blocker_list}
        _require(len(blockers) == len(blocker_list), f"{label}.blocker_codes contains duplicates")
        _require(required_candidate_blockers <= blockers, f"{label} lost mandatory concept-only blockers")
        normalized_candidates.append(candidate)

    candidate_field_ids = {field_id for field_id, _, _ in observed_matrix}
    expected_matrix = {
        (field_id, interval, offset)
        for field_id in candidate_field_ids
        for interval in intervals
        for offset in offsets
    }
    _require(observed_matrix == expected_matrix, "C1 TI sensitivity matrix is incomplete")

    td_fields = {
        "candidate_id",
        "field_id",
        "variant",
        "status",
        "geometry_count",
        "blocker_codes",
    }
    required_td_blockers = {
        "TD_RECEIVER_MISSING",
        "TD_GRADE_RULE_MISSING",
        "PCE_SOLVER_NOT_RUN",
        "PCX_SOLVER_NOT_RUN",
        "EMBEDDED_SECTION_NOT_APPLIED",
        "PROPOSED_DTM_NOT_GENERATED",
        "GUIDANCE_NOT_AUTHORIZED",
    }
    td_records = _list(result.get("td_not_generated"), "C1 td_not_generated")
    _require(bool(td_records), "C1 TD blocker records cannot be empty")
    observed_td_fields: set[str] = set()
    normalized_td: list[dict[str, Any]] = []
    for index, raw in enumerate(td_records):
        label = f"C1 td_not_generated[{index}]"
        record = dict(_mapping(raw, label))
        _require(set(record) == td_fields, f"{label} fields changed")
        field_id = _text(record.get("field_id"), f"{label}.field_id")
        candidate_id = _text(record.get("candidate_id"), f"{label}.candidate_id")
        _require(candidate_id == f"C1E0_TD_{field_id}_NOT_GENERATED", f"{label}.candidate_id is inconsistent")
        _require(candidate_id not in observed_ids, f"duplicate C1 candidate_id {candidate_id}")
        _require(field_id not in observed_td_fields, f"duplicate C1 TD blocker for field {field_id}")
        observed_ids.add(candidate_id)
        observed_td_fields.add(field_id)
        _require(record.get("variant") == "EMBUTIDA_TD", f"{label} is not TD")
        _require(record.get("status") == "NOT_GENERATED_RECEIVER_MISSING", f"{label} TD status changed")
        _require(_integer(record.get("geometry_count"), f"{label}.geometry_count") == 0, f"{label} exposes forbidden TD geometry")
        blocker_list = _list(record.get("blocker_codes"), f"{label}.blocker_codes")
        blockers = {_text(value, f"{label}.blocker_code") for value in blocker_list}
        _require(len(blockers) == len(blocker_list), f"{label}.blocker_codes contains duplicates")
        _require(required_td_blockers <= blockers, f"{label} lost mandatory TD blockers")
        normalized_td.append(record)
    _require(observed_td_fields == candidate_field_ids, "C1 TD blocker fields differ from TI sensitivity fields")

    qa = _mapping(result.get("qa"), "C1 qa")
    expected_qa_fields = {
        "field_count",
        "ti_candidate_count",
        "td_not_generated_count",
        "axis_count",
        "strip_count",
        "diagnostic_row_segment_count",
        "invalid_geometry_count",
        "non_3d_line_count",
        "forbidden_td_geometry_count",
        "hydraulic_pass_claim_count",
        "guidance_authorized_claim_count",
        "general_constraint_inventory_status",
        "power_inventory_status",
        "power_constraint_effect",
    }
    _require(set(qa) == expected_qa_fields, "C1 QA fields changed")
    expected_counts = {
        "field_count": len(candidate_field_ids),
        "ti_candidate_count": len(normalized_candidates),
        "td_not_generated_count": len(normalized_td),
        "axis_count": sum(_integer(item["axis_count"], "C1 axis_count") for item in normalized_candidates),
        "strip_count": sum(_integer(item["strip_count"], "C1 strip_count") for item in normalized_candidates),
        "diagnostic_row_segment_count": sum(
            _integer(item["diagnostic_row_segment_count"], "C1 diagnostic_row_segment_count")
            for item in normalized_candidates
        ),
    }
    for key, expected in expected_counts.items():
        _require(_integer(qa.get(key), f"C1 qa.{key}") == expected, f"C1 qa.{key} is inconsistent")
    for key in (
        "invalid_geometry_count",
        "non_3d_line_count",
        "forbidden_td_geometry_count",
        "hydraulic_pass_claim_count",
        "guidance_authorized_claim_count",
    ):
        _require(_integer(qa.get(key), f"C1 qa.{key}") == 0, f"C1 qa.{key} must be zero")
    _require(
        qa.get("general_constraint_inventory_status") in {"COMPLETE", "PARTIAL", "NOT_REVIEWED"},
        "C1 general constraint inventory status is invalid",
    )
    power_effects = {
        "PROVIDED": "BLOCKED_REQUIRES_BARRIER_STAGE",
        "DECLARED_NONE": "NOT_APPLICABLE_DECLARED_NONE",
        "NOT_REVIEWED": "PENDING_NOT_REVIEWED",
    }
    power_status = _text(qa.get("power_inventory_status"), "C1 qa.power_inventory_status")
    _require(power_status in power_effects, "C1 power inventory status is invalid")
    _require(qa.get("power_constraint_effect") == power_effects[power_status], "C1 power constraint effect is inconsistent")

    layer_counts = _mapping(result.get("layer_counts"), "C1 layer_counts")
    _require(
        set(layer_counts) == {"terrace_alignment_candidates", "interterrace_strips", "row_candidates", "candidate_summary"},
        "C1 layer count fields changed",
    )
    expected_layers = {
        "terrace_alignment_candidates": expected_counts["axis_count"],
        "interterrace_strips": expected_counts["strip_count"],
        "row_candidates": expected_counts["diagnostic_row_segment_count"],
        "candidate_summary": len(normalized_candidates) + len(normalized_td),
    }
    for key, expected in expected_layers.items():
        _require(_integer(layer_counts.get(key), f"C1 layer_counts.{key}") == expected, f"C1 layer_counts.{key} is inconsistent")

    expected_stage = (
        C1_SCREENING_ONLY
        if any(item["screening_status"] == "GEOMETRIC_PRECURSOR" for item in normalized_candidates)
        else C1_NO_GEOMETRIC_PRECURSOR
    )
    _require(result.get("stage_status") == expected_stage, "C1 stage_status is inconsistent with its candidates")

    outputs = _mapping(result.get("outputs"), "C1 outputs")
    _require(set(outputs) == {"geopackage", "map"}, "C1 output set changed")
    _validate_c1_file_record(outputs.get("geopackage"), "C1 outputs.geopackage", "C1_E0_SCREENING_VECTOR_PACKAGE")
    _validate_c1_file_record(outputs.get("map"), "C1 outputs.map", "C1_E0_SCREENING_MAP")
    _reject_authorization_claims(result, "C1")
    return result


def summarize_c1_screening(
    source: Mapping[str, Any] | str | Path,
    *,
    run_id: str,
    project_id: str,
    expected_project_request_id: str | None = None,
    expected_project_request_sha256: str | None = None,
    expected_sensitivity_request_id: str | None = None,
    expected_sensitivity_request_sha256: str | None = None,
    expected_cf0_manifest_sha256: str | None = None,
    expected_cf0_geopackage_sha256: str | None = None,
) -> list[dict[str, Any]]:
    """Normalize C1 TI sensitivities and blocked TD variants for comparison."""

    run_id = _text(run_id, "run_id")
    project_id = _text(project_id, "project_id")
    result = validate_c1_result(
        source,
        expected_project_request_id=expected_project_request_id,
        expected_project_request_sha256=expected_project_request_sha256,
        expected_sensitivity_request_id=expected_sensitivity_request_id,
        expected_sensitivity_request_sha256=expected_sensitivity_request_sha256,
        expected_cf0_manifest_sha256=expected_cf0_manifest_sha256,
        expected_cf0_geopackage_sha256=expected_cf0_geopackage_sha256,
    )
    lineage = {
        "project_request_id": result["project_request_ref"]["id"],
        "project_request_sha256": result["project_request_ref"]["sha256"].lower(),
        "sensitivity_request_id": result["sensitivity_request_ref"]["id"],
        "sensitivity_request_sha256": result["sensitivity_request_ref"]["sha256"].lower(),
        "cf0_manifest_sha256": result["inputs"]["cf0_manifest"]["sha256"].lower(),
        "cf0_geopackage_sha256": result["inputs"]["cf0_geopackage"]["sha256"].lower(),
        "release": C1_RELEASE,
    }
    records: list[dict[str, Any]] = []
    for candidate in sorted(result["candidates"], key=lambda item: item["candidate_id"]):
        metrics = candidate["metrics"]
        records.append(
            {
                "id": f"{run_id}:{candidate['candidate_id']}",
                "run_id": run_id,
                "project_id": project_id,
                "code": candidate["candidate_id"],
                "name": (
                    f"TI conceitual {candidate['field_id']} | "
                    f"VI {candidate['vertical_interval_m']:g} m | offset {candidate['offset_fraction']:.2f}"
                ),
                "family": "C1 - curva embutida TI conceitual",
                "variant": "EMBUTIDA_TI",
                "status": "CONCEPT_ONLY",
                "screening_status": candidate["screening_status"],
                "geometric_precursor_available": candidate["screening_status"] == "GEOMETRIC_PRECURSOR",
                "geometry_eligible": False,
                "selection_eligible": False,
                "recommended": False,
                "recommendation_scope": "NO_RECOMMENDATION_CONCEPT_ONLY",
                "release_level": "CONCEPT_ONLY",
                "pce_status": "NOT_EVALUATED",
                "pcx_status": "NOT_EVALUATED",
                "hydraulic_status": HYDRAULIC_UNCONFIRMED,
                "hydraulic_evaluation_status": "NOT_EVALUATED",
                "construction_status": "NOT_EVALUATED",
                "operational_status": "DIAGNOSTIC_ONLY_NOT_ROUTED",
                "guidance_authorized": False,
                "field_ids": [candidate["field_id"]],
                "metrics": {
                    "vertical_interval_m": candidate["vertical_interval_m"],
                    "offset_fraction": candidate["offset_fraction"],
                    "axis_count": candidate["axis_count"],
                    "strip_count": candidate["strip_count"],
                    "concept_alignment_km": round(metrics["terrace_total_length_m"] / 1000.0, 3),
                    "partitioned_area_ha": round(metrics["partitioned_area_m2"] / 10_000.0, 4),
                    "topology_gap_area_m2": metrics["topology_gap_area_m2"],
                    "source_cf0c_row_count": metrics["source_cf0c_row_count"],
                    "diagnostic_segment_count": metrics["diagnostic_segment_count"],
                    "diagnostic_cf0_row_km": round(metrics["diagnostic_total_length_m"] / 1000.0, 3),
                    "diagnostic_length_p50_m": metrics["diagnostic_length_p50_m"],
                    "diagnostic_length_p95_m": metrics["diagnostic_length_p95_m"],
                    "split_source_row_count": metrics["split_source_row_count"],
                    "pce_spacing_m": None,
                    "pcx_section": None,
                    "hydraulic_capacity": None,
                },
                "blocker_codes": sorted(candidate["blocker_codes"]),
                "source_lineage": dict(lineage),
            }
        )

    for td in sorted(result["td_not_generated"], key=lambda item: item["candidate_id"]):
        records.append(
            {
                "id": f"{run_id}:{td['candidate_id']}",
                "run_id": run_id,
                "project_id": project_id,
                "code": td["candidate_id"],
                "name": f"TD bloqueada {td['field_id']}",
                "family": "C1 - curva embutida TD bloqueada",
                "variant": "EMBUTIDA_TD",
                "status": "BLOCKED",
                "td_status": "NOT_GENERATED_RECEIVER_MISSING",
                "geometry_eligible": False,
                "selection_eligible": False,
                "recommended": False,
                "recommendation_scope": "NO_RECOMMENDATION_CONCEPT_ONLY",
                "release_level": "CONCEPT_ONLY",
                "pce_status": "NOT_EVALUATED",
                "pcx_status": "NOT_EVALUATED",
                "hydraulic_status": HYDRAULIC_UNCONFIRMED,
                "hydraulic_evaluation_status": "NOT_EVALUATED",
                "construction_status": "NOT_EVALUATED",
                "operational_status": "NOT_GENERATED",
                "guidance_authorized": False,
                "field_ids": [td["field_id"]],
                "metrics": {
                    "geometry_count": 0,
                    "pce_spacing_m": None,
                    "pcx_section": None,
                    "hydraulic_capacity": None,
                },
                "blocker_codes": sorted(td["blocker_codes"]),
                "source_lineage": dict(lineage),
            }
        )
    return records


def c1_artifact_records(source: Mapping[str, Any] | str | Path) -> list[dict[str, Any]]:
    """Normalize C1 screening vector and map declarations as concept-only artifacts."""

    result = validate_c1_result(source)
    records: list[dict[str, Any]] = []
    for key, artifact_type, media_type, previewable in (
        ("geopackage", "C1_CONCEPT_VECTOR_PACKAGE", "application/geopackage+sqlite3", False),
        ("map", "C1_CONCEPT_SCREENING_MAP", "image/png", True),
    ):
        item = result["outputs"][key]
        records.append(
            {
                "artifact_type": artifact_type,
                "role": item["role"],
                "path": item["path"],
                "sha256": item["sha256"].lower(),
                "size_bytes": item["size_bytes"],
                "media_type": media_type,
                "previewable": previewable,
                "delivery_level": "CONCEPT_ONLY",
                "guidance_authorized": False,
            }
        )
    return records


__all__ = [
    "C1_RELEASE",
    "CF0_RELEASE",
    "E0_STATUS",
    "HYDRAULIC_UNCONFIRMED",
    "ScenarioResultError",
    "aggregate_e0_scenarios",
    "c1_artifact_records",
    "cf0_artifact_records",
    "summarize_c1_screening",
    "summarize_cf0_candidates",
    "validate_c1_result",
    "validate_cf0_result",
    "validate_e0_result",
]
