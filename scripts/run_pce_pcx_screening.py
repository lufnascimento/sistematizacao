#!/usr/bin/env python3
"""Run the PCE0/PCX0 numeric screening contract from an immutable JSON request."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from .pce_pcx import (
        PCE_RELEASE,
        PCX_RELEASE,
        RELEASE_LIMITATIONS,
        PcxInterval,
        RusleFactors,
        calculate_pce0_rusle,
        calculate_pcx0_mass_balance,
        validate_release_boundary,
    )
except ImportError:
    from pce_pcx import (
        PCE_RELEASE,
        PCX_RELEASE,
        RELEASE_LIMITATIONS,
        PcxInterval,
        RusleFactors,
        calculate_pce0_rusle,
        calculate_pcx0_mass_balance,
        validate_release_boundary,
    )


ROOT = Path(__file__).resolve().parents[1]
REQUEST_RELEASE = "PCE0_PCX0_NUMERIC_SCREENING_ONLY"
RESULT_RELEASE = "PCE0_PCX0_SCREENING_NOT_DESIGN"
EVIDENCE_STATES = {"PROJECT_EVIDENCE", "E0_ASSUMPTION", "SYNTHETIC_TEST_ONLY"}


class ScreeningRequestError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ScreeningRequestError(message)


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_object)
    except (OSError, json.JSONDecodeError) as exc:
        raise ScreeningRequestError(f"cannot read request: {exc}") from exc
    require(isinstance(value, dict), "request root must be an object")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_ref(path_value: str, request_path: Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path.resolve()
    root_candidate = (ROOT / path).resolve()
    if root_candidate.is_file():
        return root_candidate
    return (request_path.parent / path).resolve()


def validate_timestamp(value: Any, label: str) -> str:
    require(isinstance(value, str) and value, f"{label} is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScreeningRequestError(f"{label} must be ISO-8601") from exc
    require(parsed.tzinfo is not None, f"{label} must include timezone")
    return value


def validate_source(record: Any, label: str) -> dict[str, Any]:
    require(isinstance(record, dict), f"{label} must be an object")
    require(set(record) == {"source_id", "evidence_state", "captured_at"}, f"{label} fields changed")
    require(isinstance(record["source_id"], str) and record["source_id"], f"{label}.source_id is required")
    require(record["evidence_state"] in EVIDENCE_STATES, f"{label}.evidence_state is invalid")
    validate_timestamp(record["captured_at"], f"{label}.captured_at")
    return record


def build_result(request: dict[str, Any], request_path: Path) -> dict[str, Any]:
    expected_root = {
        "schema_version",
        "analysis_id",
        "created_at",
        "requested_release",
        "project_request_ref",
        "pce0",
        "pcx0",
        "authorization_claims",
    }
    require(set(request) == expected_root, "request fields changed")
    require(request["schema_version"] == "1.0.0", "unsupported schema_version")
    require(request["requested_release"] == REQUEST_RELEASE, "requested_release exceeds screening boundary")
    require(isinstance(request["analysis_id"], str) and request["analysis_id"], "analysis_id is required")
    validate_timestamp(request["created_at"], "created_at")

    claims = request["authorization_claims"]
    require(isinstance(claims, dict), "authorization_claims must be an object")
    expected_claims = {
        "project_executive",
        "hydraulic_capacity",
        "receiver_approved",
        "guidance_authorized",
    }
    require(set(claims) == expected_claims, "authorization_claim fields changed")
    require(all(value is False for value in claims.values()), "authorization claims must all be false")

    project_ref = request["project_request_ref"]
    require(isinstance(project_ref, dict), "project_request_ref must be an object")
    require(set(project_ref) == {"id", "path", "sha256"}, "project_request_ref fields changed")
    require(isinstance(project_ref["id"], str) and project_ref["id"], "project request id is required")
    require(isinstance(project_ref["path"], str) and project_ref["path"], "project request path is required")
    require(isinstance(project_ref["sha256"], str) and len(project_ref["sha256"]) == 64, "project request SHA-256 is invalid")
    project_path = resolve_ref(project_ref["path"], request_path)
    require(project_path.is_file(), "project request file is missing")
    require(sha256_file(project_path) == project_ref["sha256"].lower(), "project request SHA-256 changed")

    pce = request["pce0"]
    require(isinstance(pce, dict), "pce0 must be an object")
    require(set(pce) == {"factors", "factor_sources"}, "pce0 fields changed")
    factor_keys = {
        "rainfall_erosivity_r",
        "soil_erodibility_k",
        "slope_length_steepness_ls",
        "cover_management_c",
        "support_practice_p",
        "soil_loss_tolerance_t_ha_year",
    }
    require(isinstance(pce["factors"], dict) and set(pce["factors"]) == factor_keys, "PCE factor set changed")
    require(isinstance(pce["factor_sources"], dict) and set(pce["factor_sources"]) == factor_keys, "PCE source set changed")
    sources = {
        key: validate_source(pce["factor_sources"][key], f"pce0.factor_sources.{key}")
        for key in sorted(factor_keys)
    }
    factors = RusleFactors(**pce["factors"])
    pce_result = calculate_pce0_rusle(factors)
    validate_release_boundary(pce_result)
    pce_result["factor_sources"] = sources

    pcx = request["pcx0"]
    require(isinstance(pcx, dict), "pcx0 must be an object")
    require(
        set(pcx)
        == {
            "catchment_area_ha",
            "rainfall_excess_method_ref",
            "relative_residual_tolerance",
            "intervals",
        },
        "pcx0 fields changed",
    )
    method_ref = validate_source(pcx["rainfall_excess_method_ref"], "pcx0.rainfall_excess_method_ref")
    require(isinstance(pcx["intervals"], list), "pcx0.intervals must be an array")
    interval_keys = {
        "duration_s",
        "rainfall_excess_mm",
        "external_inflow_m3",
        "controlled_outflow_m3",
        "storage_start_m3",
        "storage_end_m3",
    }
    intervals: list[PcxInterval] = []
    for index, item in enumerate(pcx["intervals"]):
        require(isinstance(item, dict) and set(item) == interval_keys, f"pcx0.intervals[{index}] fields changed")
        intervals.append(PcxInterval(**item))
    pcx_result = calculate_pcx0_mass_balance(
        pcx["catchment_area_ha"],
        intervals,
        relative_residual_tolerance=pcx["relative_residual_tolerance"],
    )
    validate_release_boundary(pcx_result)
    pcx_result["rainfall_excess_method_ref"] = method_ref

    blockers = [
        "PCE_LOCAL_CALIBRATION_AND_PROFESSIONAL_REVIEW_REQUIRED",
        "PCX_RAINFALL_EXCESS_METHOD_NOT_EVALUATED",
        "PCX_HYDROGRAPH_ROUTING_NOT_RUN",
        "PCX_SECTION_CAPACITY_NOT_EVALUATED",
        "PCX_RECEIVER_NOT_APPROVED",
        "GUIDANCE_NOT_AUTHORIZED",
    ]
    if any(source["evidence_state"] != "PROJECT_EVIDENCE" for source in sources.values()):
        blockers.append("PCE_PROJECT_EVIDENCE_INCOMPLETE")
    if method_ref["evidence_state"] != "PROJECT_EVIDENCE":
        blockers.append("PCX_PROJECT_EVIDENCE_INCOMPLETE")
    if pcx_result["numeric_continuity_status"] != "PASS":
        blockers.append("PCX_NUMERIC_MASS_BALANCE_FAILED")
    if pce_result["screening_status"] == "TOLERANCE_NOT_DECLARED":
        blockers.append("PCE_TOLERANCE_NOT_DECLARED")

    return {
        "schema_version": "1.0.0",
        "manifest_type": "PCE0_PCX0_SCREENING_RESULT",
        "release": RESULT_RELEASE,
        "analysis_id": request["analysis_id"],
        "generated_at": request["created_at"],
        "screening_request_ref": {
            "path": str(request_path.resolve()),
            "sha256": sha256_file(request_path),
        },
        "project_request_ref": {
            **project_ref,
            "path": str(project_path),
            "sha256": project_ref["sha256"].lower(),
        },
        "component_releases": {"pce0": PCE_RELEASE, "pcx0": PCX_RELEASE},
        "pce0": pce_result,
        "pcx0": pcx_result,
        "stage_status": "SCREENING_WITH_EXPLICIT_BLOCKERS",
        "blocker_codes": sorted(set(blockers)),
        "authorization_claims": dict(claims),
        "release_limitations": list(RELEASE_LIMITATIONS),
    }


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    request_path = args.request.resolve()
    result = build_result(load_json(request_path), request_path)
    if not args.check:
        require(args.output is not None, "--output is required unless --check is used")
        write_json_atomic(args.output.resolve(), result)
    print(
        json.dumps(
            {
                "status": "VALID",
                "release": result["release"],
                "analysis_id": result["analysis_id"],
                "pce_status": result["pce0"]["screening_status"],
                "pcx_continuity": result["pcx0"]["numeric_continuity_status"],
                "blocker_count": len(result["blocker_codes"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ScreeningRequestError, ValueError) as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(2)
