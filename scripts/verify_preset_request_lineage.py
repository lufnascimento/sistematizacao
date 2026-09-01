"""Verify preset compilation lineage and optional E0/CF0 engine manifests."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from .compile_project_request_from_presets import (
        DEFAULT_MANIFEST_SCHEMA,
        DEFAULT_PACKAGE_SCHEMA,
        REPO,
        SOURCE_KEYS,
        canonical_sha256,
        compile_request,
        load_json,
        require,
        sha256_file,
        validate_file_ref,
        validate_package,
        validate_schema_instance,
    )
    from .project_request import load_project_request
    from .validate_project_inputs import ContractError
except ImportError:  # Direct script execution adds scripts/ to sys.path.
    from compile_project_request_from_presets import (
        DEFAULT_MANIFEST_SCHEMA,
        DEFAULT_PACKAGE_SCHEMA,
        REPO,
        SOURCE_KEYS,
        canonical_sha256,
        compile_request,
        load_json,
        require,
        sha256_file,
        validate_file_ref,
        validate_package,
        validate_schema_instance,
    )
    from project_request import load_project_request
    from validate_project_inputs import ContractError


DEFAULT_COMPILATION_MANIFEST = REPO / "dataset" / "derived" / "preset_request_compilation_manifest.json"
DEFAULT_E0_MANIFEST = REPO / "dataset" / "derived" / "sulcation_scenario_metrics.json"
DEFAULT_CF0_MANIFEST = REPO / "dataset" / "derived" / "continuous_family_manifest.json"


def resolve_record_path(value: str, manifest_path: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    repo_candidate = (REPO / path).resolve()
    local_candidate = (manifest_path.parent / path).resolve()
    if repo_candidate.exists() or not local_candidate.exists():
        return repo_candidate
    return local_candidate


def validate_file_record(
    record: Any,
    label: str,
    manifest_path: Path,
    *,
    expected_role: str,
) -> Path:
    require(isinstance(record, dict), f"{label} must be a file record.")
    require(
        set(record) == {"path", "role", "size_bytes", "sha256"},
        f"{label} has unexpected file-record fields.",
    )
    require(isinstance(record.get("path"), str) and record["path"], f"{label}.path is required.")
    require(record.get("role") == expected_role, f"{label}.role differs from the contract.")
    require(
        isinstance(record.get("size_bytes"), int) and record["size_bytes"] > 0,
        f"{label}.size_bytes is invalid.",
    )
    require(
        isinstance(record.get("sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", record["sha256"]) is not None,
        f"{label}.sha256 is invalid.",
    )
    path = resolve_record_path(record["path"], manifest_path)
    require(path.is_file(), f"{label} file is missing: {path}")
    require(path.stat().st_size == record["size_bytes"], f"{label} size differs from the manifest.")
    require(sha256_file(path) == record["sha256"], f"{label} hash differs from the manifest.")
    return path


def validate_output_request_record(record: Any, manifest_path: Path) -> Path:
    require(isinstance(record, dict), "output_request must be a file record.")
    path = resolve_record_path(record["path"], manifest_path)
    require(path.is_file(), f"output_request file is missing: {path}")
    require(sha256_file(path) == record["sha256"], "output_request hash differs from the manifest.")
    return path


def _validate_e0_manifest(
    path: Path,
    expected_request_id: str,
    expected_sha256: str,
) -> dict[str, Any]:
    value = load_json(path)
    request = value.get("generation_request")
    require(isinstance(request, dict), "E0 manifest has no generation_request lineage.")
    require(request.get("mode") == "VALIDATED_PROJECT_REQUEST", "E0 manifest did not use a validated request.")
    require(request.get("request_id") == expected_request_id, "E0 manifest request_id differs from compilation.")
    require(request.get("request_sha256") == expected_sha256, "E0 manifest request hash differs from compilation.")
    return {
        "manifest_path": str(path.resolve()),
        "request_id": request["request_id"],
        "request_sha256": request["request_sha256"],
        "status": "MATCH",
    }


def _validate_cf0_manifest(
    path: Path,
    expected_request_id: str,
    expected_sha256: str,
) -> dict[str, Any]:
    value = load_json(path)
    request = value.get("project_request_ref")
    require(isinstance(request, dict), "CF0 manifest has no project_request_ref lineage.")
    require(request.get("id") == expected_request_id, "CF0 manifest request_id differs from compilation.")
    require(request.get("sha256") == expected_sha256, "CF0 manifest request hash differs from compilation.")
    input_request = (value.get("inputs") or {}).get("project_request") or {}
    if "sha256" in input_request:
        require(input_request["sha256"] == expected_sha256, "CF0 input request hash is internally inconsistent.")
    return {
        "manifest_path": str(path.resolve()),
        "request_id": request["id"],
        "request_sha256": request["sha256"],
        "status": "MATCH",
    }


def verify(
    compilation_manifest_path: Path,
    *,
    e0_manifest_path: Path | None = None,
    cf0_manifest_path: Path | None = None,
) -> dict[str, Any]:
    compilation_manifest_path = compilation_manifest_path.resolve()
    manifest = load_json(compilation_manifest_path)
    validate_schema_instance(
        manifest,
        load_json(DEFAULT_MANIFEST_SCHEMA),
        "compilation manifest",
    )
    require(manifest.get("schema_version") == "1.0.0", "Unsupported compilation manifest version.")
    require(manifest.get("manifest_type") == "PROJECT_REQUEST_PRESET_COMPILATION", "Unexpected manifest type.")
    require(manifest.get("status") == "COMPILED_E0_EVIDENCE_NEUTRAL", "Compilation is not evidence-neutral E0.")
    summary = manifest.get("summary") or {}
    require(summary.get("project_evidence_resolved_by_selection") == 0, "Selection promoted project evidence.")
    require(summary.get("project_evidence_resolved_by_compiler") == 0, "Compiler promoted project evidence.")
    boundary = manifest.get("release_boundary") or {}
    require(boundary.get("guidance_authorized") is False, "Compilation cannot authorize guidance.")
    require(boundary.get("project_evidence_promoted") is False, "Compilation cannot promote evidence.")

    package_path = validate_file_record(
        manifest.get("compilation_package"),
        "compilation_package",
        compilation_manifest_path,
        expected_role="COMPILATION_PACKAGE",
    )
    package = load_json(package_path)
    validate_schema_instance(
        package,
        load_json(DEFAULT_PACKAGE_SCHEMA),
        "compilation package",
    )
    validate_package(package)
    require(package["package_id"] == manifest["package_id"], "Package id differs from the manifest.")
    require(package["project_id"] == manifest["project_id"], "Package project_id differs from the manifest.")
    require(package["compiler_revision"] == manifest["compiler_revision"], "Compiler revision differs from the package.")
    require(package["policy"] == manifest["policy"], "Compilation policy differs from the package.")

    package_source_paths = {
        key: validate_file_ref(package["sources"][key], f"package.sources.{key}", package_path)
        for key in sorted(SOURCE_KEYS)
    }
    sources = manifest.get("sources")
    require(isinstance(sources, dict) and set(sources) == SOURCE_KEYS, "Compilation source inventory changed.")
    for source_id in sorted(SOURCE_KEYS):
        source_path = validate_file_record(
            sources[source_id],
            f"sources.{source_id}",
            compilation_manifest_path,
            expected_role=source_id.upper(),
        )
        require(
            source_path == package_source_paths[source_id],
            f"sources.{source_id} path differs from the compilation package.",
        )
        require(
            sources[source_id]["sha256"] == package["sources"][source_id]["sha256"],
            f"sources.{source_id} hash differs from the compilation package.",
        )

    request_record = manifest.get("output_request")
    request_path = validate_output_request_record(request_record, compilation_manifest_path)
    request = load_json(request_path)
    require(canonical_sha256(request) == request_record.get("canonical_sha256"), "Compiled request canonical hash differs.")
    require(request.get("request_id") == request_record.get("request_id"), "Compiled request id differs from manifest.")
    expected_request, expected_manifest = compile_request(
        package_path,
        output_request_path=request_path,
        output_manifest_path=compilation_manifest_path,
        write_outputs=False,
    )
    require(
        canonical_sha256(request) == canonical_sha256(expected_request),
        "Compiled request differs from deterministic recompilation.",
    )
    require(request == expected_request, "Compiled request structure differs from deterministic recompilation.")
    require(
        manifest["parameter_decisions"] == expected_manifest["parameter_decisions"],
        "Parameter decisions differ from deterministic recompilation.",
    )
    require(
        manifest["summary"] == expected_manifest["summary"],
        "Compilation counters differ from deterministic recompilation.",
    )
    require(
        canonical_sha256(manifest) == canonical_sha256(expected_manifest),
        "Compilation manifest differs from deterministic recompilation.",
    )
    resolved = load_project_request(
        request_path,
        catalog_path=package_source_paths["parameter_catalog"],
        schema_path=package_source_paths["request_schema"],
    )
    require(resolved.requested_delivery_level == "E0_TRIAGEM", "Compiled request exceeds E0.")
    expected_power_status = (manifest.get("request_validation") or {}).get("power_inventory_status")
    require(
        resolved.power_inventory_status == expected_power_status,
        "Compiled request power status differs from its compilation validation.",
    )

    allowed_materialized = set(manifest.get("materialized_parameter_ids") or [])
    parameter_by_id = {item["parameter_id"]: item for item in request["parameter_values"]}
    for parameter_id in allowed_materialized:
        require(parameter_id in parameter_by_id, f"Materialized parameter is absent from request: {parameter_id}")
        record = parameter_by_id[parameter_id]
        require(record["parameter_class"] in {"E0_ASSUMPTION", "OPTIMIZER"}, f"Unsafe materialized class: {parameter_id}")
        require("PROJECT_EVIDENCE_UNRESOLVED" in record.get("quality_flags", []), f"Missing evidence warning: {parameter_id}")

    request_sha256 = request_record["sha256"]
    request_id = request_record["request_id"]
    engine_results: dict[str, Any] = {}
    if e0_manifest_path is not None:
        engine_results["E0"] = _validate_e0_manifest(
            e0_manifest_path.resolve(), request_id, request_sha256
        )
    if cf0_manifest_path is not None:
        engine_results["CF0"] = _validate_cf0_manifest(
            cf0_manifest_path.resolve(), request_id, request_sha256
        )
    return {
        "status": "PASS",
        "compilation_manifest": str(compilation_manifest_path),
        "compiled_request": str(request_path),
        "request_id": request_id,
        "request_sha256": request_sha256,
        "power_inventory_status": resolved.power_inventory_status,
        "project_evidence_resolved": 0,
        "engine_manifests": engine_results,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compilation-manifest", type=Path, default=DEFAULT_COMPILATION_MANIFEST)
    parser.add_argument("--e0-manifest", type=Path)
    parser.add_argument("--cf0-manifest", type=Path)
    parser.add_argument(
        "--current-engine-manifests",
        action="store_true",
        help="Verify the current default E0 and CF0 manifests against the compiled request.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    e0 = DEFAULT_E0_MANIFEST if args.current_engine_manifests else args.e0_manifest
    cf0 = DEFAULT_CF0_MANIFEST if args.current_engine_manifests else args.cf0_manifest
    try:
        report = verify(
            args.compilation_manifest,
            e0_manifest_path=e0,
            cf0_manifest_path=cf0,
        )
    except (ContractError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"PRESET REQUEST LINEAGE FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
