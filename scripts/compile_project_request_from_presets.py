"""Compile a validated E0 project request with traceable preset lineage.

The compiler is deliberately conservative. The base request remains authoritative
for project facts and evidence. Presets may only add non-safety E0 assumptions or
optimizer controls; every other unresolved proposal is retained in the manifest
as a withheld decision instead of being promoted to project evidence.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from . import resolve_project_presets as preset_resolver
    from .project_request import load_project_request
    from .validate_project_inputs import ContractError, validate_request
except ImportError:  # Direct script execution adds scripts/ to sys.path.
    import resolve_project_presets as preset_resolver
    from project_request import load_project_request
    from validate_project_inputs import ContractError, validate_request


REPO = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE = REPO / "config" / "compilacao_presets_e0_dataset_atual.json"
DEFAULT_PACKAGE_SCHEMA = REPO / "schemas" / "project-preset-compilation-package.schema.json"
DEFAULT_MANIFEST_SCHEMA = REPO / "schemas" / "project-preset-compilation-manifest.schema.json"
COMPILER_REVISION = "1.0.0"
PACKAGE_VERSION = "1.0.0"
MANIFEST_VERSION = "1.0.0"

SOURCE_KEYS = {
    "base_request",
    "preset_selection",
    "preset_resolution",
    "parameter_catalog",
    "request_schema",
    "acquisition_catalog",
    "reference_model_catalog",
    "preset_catalog",
}
POLICY = {
    "parameter_merge": "BASE_REQUEST_AUTHORITATIVE",
    "system_e0_materialization": "E0_ASSUMPTION_AND_OPTIMIZER_ONLY",
    "custom_value_materialization": "BASE_REQUEST_WITH_FULL_PROVENANCE_ONLY",
    "reference_materialization": "NEVER",
    "unresolved_evidence_materialization": "NEVER",
    "conflict_policy": "KEEP_BASE_AND_REPORT",
}
AUTO_CLASSES = {"E0_ASSUMPTION", "OPTIMIZER"}
AUTO_STATUSES = {"SYSTEM_E0_VALUE"}
RFC3339_DATETIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2})$"
)
SUPPORTED_SCHEMA_KEYWORDS = {
    "$defs",
    "$id",
    "$ref",
    "$schema",
    "additionalProperties",
    "allOf",
    "const",
    "contains",
    "description",
    "else",
    "enum",
    "format",
    "if",
    "items",
    "maxContains",
    "maxItems",
    "maxProperties",
    "minContains",
    "minItems",
    "minLength",
    "minProperties",
    "minimum",
    "not",
    "pattern",
    "properties",
    "required",
    "then",
    "title",
    "type",
    "uniqueItems",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"Cannot load JSON {path}: {exc}") from exc
    require(isinstance(value, dict), f"JSON root must be an object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _schema_type_matches(value: Any, expected_type: str) -> bool:
    if expected_type == "object":
        return isinstance(value, dict)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "null":
        return value is None
    raise ContractError(f"Unsupported JSON Schema type: {expected_type}")


def _json_schema_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    return left == right


def _resolve_schema_ref(root_schema: dict[str, Any], ref: str) -> Any:
    require(ref.startswith("#/"), f"Only internal JSON Schema refs are supported: {ref}")
    current: Any = root_schema
    for raw_part in ref[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        require(isinstance(current, dict) and part in current, f"Broken JSON Schema ref: {ref}")
        current = current[part]
    return current


def validate_schema_instance(
    value: Any,
    schema: Any,
    label: str,
    *,
    root_schema: dict[str, Any] | None = None,
) -> None:
    """Validate the JSON Schema subset used by the project contracts.

    Keeping this validator local avoids making the safety checks depend on an
    optional package. Unsupported keywords are rejected when they are added to
    the compilation contracts, instead of being silently ignored.
    """

    if isinstance(schema, bool):
        require(schema, f"{label} is forbidden by its JSON Schema.")
        return
    require(isinstance(schema, dict), f"Invalid JSON Schema fragment for {label}.")
    unsupported = set(schema) - SUPPORTED_SCHEMA_KEYWORDS
    require(
        not unsupported,
        f"Unsupported JSON Schema keywords for {label}: {', '.join(sorted(unsupported))}",
    )
    root = root_schema or schema

    if "$ref" in schema:
        validate_schema_instance(
            value,
            _resolve_schema_ref(root, schema["$ref"]),
            label,
            root_schema=root,
        )

    for index, fragment in enumerate(schema.get("allOf", [])):
        validate_schema_instance(value, fragment, f"{label}.allOf[{index}]", root_schema=root)

    if "if" in schema:
        try:
            validate_schema_instance(value, schema["if"], label, root_schema=root)
            condition_matches = True
        except ContractError:
            condition_matches = False
        branch = schema.get("then") if condition_matches else schema.get("else")
        if branch is not None:
            validate_schema_instance(value, branch, label, root_schema=root)

    if "not" in schema:
        try:
            validate_schema_instance(value, schema["not"], label, root_schema=root)
        except ContractError:
            pass
        else:
            raise ContractError(f"{label} matches a forbidden JSON Schema branch.")

    if "const" in schema:
        require(_json_schema_equal(value, schema["const"]), f"{label} differs from its JSON Schema constant.")
    if "enum" in schema:
        require(
            any(_json_schema_equal(value, candidate) for candidate in schema["enum"]),
            f"{label} is outside its JSON Schema enum.",
        )

    expected = schema.get("type")
    if expected is not None:
        expected_types = expected if isinstance(expected, list) else [expected]
        require(
            any(_schema_type_matches(value, item) for item in expected_types),
            f"{label} has the wrong JSON Schema type.",
        )

    if isinstance(value, str):
        if "minLength" in schema:
            require(len(value) >= schema["minLength"], f"{label} is shorter than allowed.")
        if "pattern" in schema:
            require(re.search(schema["pattern"], value) is not None, f"{label} does not match its pattern.")
        if schema.get("format") == "date-time":
            _parse_datetime(value, label)

    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema:
        require(value >= schema["minimum"], f"{label} is below its minimum.")

    if isinstance(value, dict):
        required = schema.get("required", [])
        require(isinstance(required, list), f"Invalid required declaration for {label}.")
        missing = set(required) - set(value)
        require(not missing, f"{label} is missing schema fields: {', '.join(sorted(missing))}")
        if "minProperties" in schema:
            require(len(value) >= schema["minProperties"], f"{label} has too few properties.")
        if "maxProperties" in schema:
            require(len(value) <= schema["maxProperties"], f"{label} has too many properties.")
        properties = schema.get("properties", {})
        require(isinstance(properties, dict), f"Invalid properties declaration for {label}.")
        for key, item in value.items():
            if key in properties:
                validate_schema_instance(item, properties[key], f"{label}.{key}", root_schema=root)
                continue
            additional = schema.get("additionalProperties", True)
            require(additional is not False, f"{label} has an unexpected field: {key}")
            if isinstance(additional, dict):
                validate_schema_instance(item, additional, f"{label}.{key}", root_schema=root)

    if isinstance(value, list):
        if "minItems" in schema:
            require(len(value) >= schema["minItems"], f"{label} has too few items.")
        if "maxItems" in schema:
            require(len(value) <= schema["maxItems"], f"{label} has too many items.")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(item, ensure_ascii=False, sort_keys=True) for item in value]
            require(len(serialized) == len(set(serialized)), f"{label} has duplicate items.")
        if "items" in schema:
            for index, item in enumerate(value):
                validate_schema_instance(item, schema["items"], f"{label}[{index}]", root_schema=root)
        if "contains" in schema:
            matches = 0
            for item in value:
                try:
                    validate_schema_instance(item, schema["contains"], label, root_schema=root)
                except ContractError:
                    continue
                matches += 1
            require(matches >= schema.get("minContains", 1), f"{label} has too few matching items.")
            if "maxContains" in schema:
                require(matches <= schema["maxContains"], f"{label} has too many matching items.")


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO).as_posix()
    except ValueError:
        return str(resolved)


def resolve_path(value: str, package_path: Path, *, output: bool = False) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    repo_candidate = (REPO / path).resolve()
    package_candidate = (package_path.parent / path).resolve()
    if output or repo_candidate.exists() or not package_candidate.exists():
        return repo_candidate
    return package_candidate


def validate_file_ref(
    record: Any,
    label: str,
    package_path: Path,
) -> Path:
    require(isinstance(record, dict), f"{label} must be a file reference object.")
    require(set(record) == {"path", "sha256"}, f"{label} has unexpected fields.")
    require(isinstance(record["path"], str) and record["path"], f"{label}.path is required.")
    require(
        isinstance(record["sha256"], str)
        and len(record["sha256"]) == 64
        and record["sha256"] == record["sha256"].lower()
        and all(character in "0123456789abcdef" for character in record["sha256"]),
        f"{label}.sha256 must be a lowercase hexadecimal SHA-256 digest.",
    )
    path = resolve_path(record["path"], package_path)
    require(path.is_file(), f"Referenced file does not exist: {path}")
    actual = sha256_file(path)
    require(actual == record["sha256"], f"Referenced file hash changed: {label}")
    return path


def _parse_datetime(value: Any, label: str) -> None:
    require(
        isinstance(value, str) and RFC3339_DATETIME.fullmatch(value) is not None,
        f"{label} must be an RFC 3339 date-time with timezone.",
    )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00"))
    except ValueError as exc:
        raise ContractError(f"{label} must be an RFC 3339 date-time with timezone.") from exc
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None, f"{label} needs a timezone.")


def validate_package(package: dict[str, Any]) -> None:
    expected_root = {
        "schema_version",
        "package_id",
        "project_id",
        "created_at",
        "compiler_revision",
        "sources",
        "policy",
        "output",
    }
    require(set(package) == expected_root, "Compilation package root fields changed.")
    require(package["schema_version"] == PACKAGE_VERSION, "Unsupported compilation package version.")
    require(isinstance(package["package_id"], str) and package["package_id"], "package_id is required.")
    require(isinstance(package["project_id"], str) and package["project_id"], "project_id is required.")
    _parse_datetime(package["created_at"], "created_at")
    require(package["compiler_revision"] == COMPILER_REVISION, "Compiler revision mismatch.")
    sources = package["sources"]
    require(isinstance(sources, dict) and set(sources) == SOURCE_KEYS, "Compilation source set changed.")
    require(package["policy"] == POLICY, "Compilation safety policy cannot be relaxed by configuration.")
    output = package["output"]
    require(
        isinstance(output, dict)
        and set(output) == {"request_path", "manifest_path", "request_id", "created_at"},
        "Compilation output contract changed.",
    )
    for key in ("request_path", "manifest_path", "request_id"):
        require(isinstance(output[key], str) and output[key], f"output.{key} is required.")
    _parse_datetime(output["created_at"], "output.created_at")


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve()))


def _same_path(left: Path, right: Path) -> bool:
    if _path_key(left) == _path_key(right):
        return True
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def validate_output_paths(
    package_path: Path,
    source_paths: dict[str, Path],
    request_path: Path,
    manifest_path: Path,
) -> None:
    require(not _same_path(request_path, manifest_path), "Request and manifest outputs must be different files.")
    protected = {"compilation_package": package_path, **source_paths}
    for output_label, output_path in {
        "output_request": request_path,
        "output_manifest": manifest_path,
    }.items():
        for source_label, source_path in protected.items():
            require(
                not _same_path(output_path, source_path),
                f"{output_label} collides with protected source {source_label}: {output_path}",
            )


def stable_resolution(value: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(value)
    result.pop("generated_at", None)
    return result


def file_record(path: Path, role: str) -> dict[str, Any]:
    return {
        "path": display_path(path),
        "role": role,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _preset_source_ref(resolution_path: Path, parameter_id: str) -> str:
    return f"{display_path(resolution_path)}#parameter={parameter_id}"


def _compiled_provenance(
    row: dict[str, Any],
    package: dict[str, Any],
    resolution_path: Path,
) -> dict[str, Any]:
    parameter_class = row["parameter_class"]
    origin = "E0_ASSUMPTION" if parameter_class == "E0_ASSUMPTION" else "OPTIMIZER"
    return {
        "origin": origin,
        "source_ref": _preset_source_ref(resolution_path, row["parameter_id"]),
        "captured_at": package["created_at"],
        "responsible": {
            "name": "terraflux-preset-compiler",
            "organization": "TerraFlux",
            "role": "system-compiler",
            "identifier": None,
        },
        "confidence": "LOW",
        "revision": f"preset-compiler-{COMPILER_REVISION}",
        "applicability": "SCENARIO",
        "method": (
            "E0-only preset materialization; this value does not resolve project "
            "evidence or authorize operational, conservational or executive release."
        ),
    }


def _decision_for_existing(
    base_record: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    proposed = row.get("proposed_value")
    if proposed is None:
        relation = "NO_PRESET_VALUE"
    elif base_record["value"] == proposed:
        relation = "MATCHES_PRESET_PROPOSAL"
    else:
        relation = "BASE_OVERRIDES_PRESET_PROPOSAL"
    decision = {
        "parameter_id": row["parameter_id"],
        "decision": "PRESERVE_BASE_REQUEST",
        "preset_status": row["status"],
        "parameter_class": row["parameter_class"],
        "relation": relation,
        "base_value": copy.deepcopy(base_record["value"]),
        "preset_proposed_value": copy.deepcopy(proposed),
        "project_evidence_resolved_by_compiler": False,
    }
    if row.get("status") == "CUSTOM_VALUE_PENDING_EVIDENCE_VALIDATION":
        decision["custom_provenance_matches_base"] = _custom_provenance_matches_base(
            base_record,
            row,
        )
    return decision


def _custom_provenance_matches_base(
    base_record: dict[str, Any],
    row: dict[str, Any],
) -> bool:
    custom = row.get("custom_provenance")
    base = base_record.get("provenance")
    if not isinstance(custom, dict) or not isinstance(base, dict):
        return False
    for key in ("origin", "source_ref", "captured_at", "revision"):
        if custom.get(key) != base.get(key):
            return False
    custom_responsible = custom.get("responsible")
    base_responsible = base.get("responsible")
    if isinstance(custom_responsible, str) and isinstance(base_responsible, dict):
        return custom_responsible == base_responsible.get("name")
    return custom_responsible == base_responsible


def _can_materialize(row: dict[str, Any]) -> bool:
    return (
        row.get("status") in AUTO_STATUSES
        and row.get("parameter_class") in AUTO_CLASSES
        and row.get("proposed_value") is not None
        and row.get("release_ceiling") == "E0_TRIAGEM"
        and row.get("safety_critical") is False
        and row.get("project_evidence_resolved") is False
    )


def compile_request(
    package_path: Path,
    *,
    output_request_path: Path | None = None,
    output_manifest_path: Path | None = None,
    write_outputs: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    package_path = package_path.resolve()
    package = load_json(package_path)
    validate_schema_instance(
        package,
        load_json(DEFAULT_PACKAGE_SCHEMA),
        "compilation package",
    )
    validate_package(package)

    source_paths = {
        key: validate_file_ref(package["sources"][key], f"sources.{key}", package_path)
        for key in sorted(SOURCE_KEYS)
    }
    base = load_project_request(
        source_paths["base_request"],
        catalog_path=source_paths["parameter_catalog"],
        schema_path=source_paths["request_schema"],
    )
    request_schema = load_json(source_paths["request_schema"])
    validate_schema_instance(base.request, request_schema, "base request")
    selection = load_json(source_paths["preset_selection"])
    stored_resolution = load_json(source_paths["preset_resolution"])
    live_resolution = preset_resolver.resolve(
        source_paths["preset_selection"],
        source_paths["parameter_catalog"],
        source_paths["acquisition_catalog"],
        source_paths["reference_model_catalog"],
        source_paths["preset_catalog"],
    )

    require(
        stable_resolution(stored_resolution) == stable_resolution(live_resolution),
        "Stored preset resolution is stale or differs from the selected catalogs.",
    )
    require(selection["project_id"] == package["project_id"], "Selection project_id differs from package.")
    require(base.request["project_id"] == package["project_id"], "Base request project_id differs from package.")
    require(stored_resolution["project_id"] == package["project_id"], "Resolution project_id differs from package.")
    require(
        selection["requested_delivery_level"]
        == base.request["requested_delivery_level"]
        == stored_resolution["requested_delivery_level"]
        == "E0_TRIAGEM",
        "This compiler accepts only aligned E0_TRIAGEM inputs.",
    )
    require(
        stored_resolution["summary"]["project_evidence_resolved_by_selection"] == 0,
        "Preset resolution cannot claim project evidence.",
    )
    require(
        all(row.get("project_evidence_resolved") is False for row in stored_resolution["parameters"]),
        "Every preset parameter must remain evidence-neutral.",
    )

    compiled = copy.deepcopy(base.request)
    compiled["request_id"] = package["output"]["request_id"]
    compiled["created_at"] = package["output"]["created_at"]
    base_by_id = {item["parameter_id"]: item for item in compiled["parameter_values"]}
    decisions: list[dict[str, Any]] = []
    materialized_ids: list[str] = []

    for row in sorted(stored_resolution["parameters"], key=lambda item: item["parameter_id"]):
        parameter_id = row["parameter_id"]
        existing = base_by_id.get(parameter_id)
        if existing is not None:
            decisions.append(_decision_for_existing(existing, row))
            continue
        if _can_materialize(row):
            record = {
                "parameter_id": parameter_id,
                "parameter_class": row["parameter_class"],
                "value": copy.deepcopy(row["proposed_value"]),
                "unit": row["unit"],
                "provenance": _compiled_provenance(row, package, source_paths["preset_resolution"]),
                "quality_flags": ["PRESET_E0_ONLY", "PROJECT_EVIDENCE_UNRESOLVED"],
            }
            compiled["parameter_values"].append(record)
            base_by_id[parameter_id] = record
            materialized_ids.append(parameter_id)
            decisions.append(
                {
                    "parameter_id": parameter_id,
                    "decision": "MATERIALIZE_E0_NON_EVIDENCE_VALUE",
                    "preset_status": row["status"],
                    "parameter_class": row["parameter_class"],
                    "relation": "NOT_PRESENT_IN_BASE",
                    "base_value": None,
                    "preset_proposed_value": copy.deepcopy(row["proposed_value"]),
                    "project_evidence_resolved_by_compiler": False,
                }
            )
            continue
        decisions.append(
            {
                "parameter_id": parameter_id,
                "decision": "WITHHOLD_PRESET_PROPOSAL",
                "preset_status": row["status"],
                "parameter_class": row["parameter_class"],
                "relation": "NOT_PRESENT_IN_BASE",
                "base_value": None,
                "preset_proposed_value": copy.deepcopy(row.get("proposed_value")),
                "withhold_reason": (
                    "Only non-safety SYSTEM_E0_VALUE entries in E0_ASSUMPTION or OPTIMIZER "
                    "classes may be materialized automatically."
                ),
                "project_evidence_resolved_by_compiler": False,
            }
        )

    catalog_by_id = base.catalog_by_id
    validate_schema_instance(compiled, request_schema, "compiled request")
    validation_report = validate_request(compiled, request_schema, catalog_by_id)
    conflict_ids = [
        item["parameter_id"]
        for item in decisions
        if item["relation"] == "BASE_OVERRIDES_PRESET_PROPOSAL"
    ]
    withheld_ids = [
        item["parameter_id"]
        for item in decisions
        if item["decision"] == "WITHHOLD_PRESET_PROPOSAL"
    ]
    custom_selection_ids = {
        row["parameter_id"]
        for row in stored_resolution["parameters"]
        if row["status"] == "CUSTOM_VALUE_PENDING_EVIDENCE_VALIDATION"
    }
    custom_confirmed_ids = sorted(
        item["parameter_id"]
        for item in decisions
        if item["parameter_id"] in custom_selection_ids
        and item["decision"] == "PRESERVE_BASE_REQUEST"
        and item["relation"] == "MATCHES_PRESET_PROPOSAL"
        and item.get("custom_provenance_matches_base") is True
    )
    preserved_fail_closed_ids = sorted(
        item["parameter_id"]
        for item in decisions
        if item["preset_status"] == "FAIL_CLOSED"
        and item["decision"] == "PRESERVE_BASE_REQUEST"
    )
    withheld_fail_closed_ids = sorted(
        item["parameter_id"]
        for item in decisions
        if item["preset_status"] == "FAIL_CLOSED"
        and item["decision"] == "WITHHOLD_PRESET_PROPOSAL"
    )

    request_path = (
        output_request_path.resolve()
        if output_request_path is not None
        else resolve_path(package["output"]["request_path"], package_path, output=True)
    )
    manifest_path = (
        output_manifest_path.resolve()
        if output_manifest_path is not None
        else resolve_path(package["output"]["manifest_path"], package_path, output=True)
    )
    validate_output_paths(package_path, source_paths, request_path, manifest_path)
    request_payload = json.dumps(compiled, ensure_ascii=False, indent=2) + "\n"
    request_sha256 = hashlib.sha256(request_payload.encode("utf-8")).hexdigest()
    package_record = file_record(package_path, "COMPILATION_PACKAGE")
    source_records = {
        key: file_record(path, key.upper())
        for key, path in sorted(source_paths.items())
    }
    manifest = {
        "schema_version": MANIFEST_VERSION,
        "manifest_type": "PROJECT_REQUEST_PRESET_COMPILATION",
        "compiler_revision": COMPILER_REVISION,
        "status": "COMPILED_E0_EVIDENCE_NEUTRAL",
        "generated_at": package["output"]["created_at"],
        "package_id": package["package_id"],
        "project_id": package["project_id"],
        "compilation_package": package_record,
        "sources": source_records,
        "policy": copy.deepcopy(POLICY),
        "summary": {
            "base_parameter_count": len(base.request["parameter_values"]),
            "compiled_parameter_count": len(compiled["parameter_values"]),
            "preserved_base_parameter_count": len(base.request["parameter_values"]),
            "materialized_e0_parameter_count": len(materialized_ids),
            "withheld_parameter_count": len(withheld_ids),
            "base_preset_conflict_count": len(conflict_ids),
            "custom_selection_parameter_count": len(custom_selection_ids),
            "custom_selection_confirmed_by_base_count": len(custom_confirmed_ids),
            "auto_materialized_fail_closed_count": 0,
            "preserved_base_fail_closed_parameter_count": len(preserved_fail_closed_ids),
            "withheld_fail_closed_parameter_count": len(withheld_fail_closed_ids),
            "project_evidence_resolved_by_selection": 0,
            "project_evidence_resolved_by_compiler": 0,
            "requested_delivery_level": "E0_TRIAGEM",
            "maximum_delivery_level_from_compilation": "E0_TRIAGEM",
        },
        "materialized_parameter_ids": materialized_ids,
        "withheld_parameter_ids": withheld_ids,
        "base_preset_conflict_ids": conflict_ids,
        "provenance_breakdown": {
            "preset_materialized_parameter_ids": materialized_ids,
            "custom_selection_confirmed_by_base_parameter_ids": custom_confirmed_ids,
            "fail_closed_materialized_parameter_ids": [],
            "fail_closed_preserved_from_base_parameter_ids": preserved_fail_closed_ids,
            "fail_closed_withheld_parameter_ids": withheld_fail_closed_ids,
        },
        "parameter_decisions": decisions,
        "request_validation": validation_report,
        "release_boundary": {
            "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
            "guidance_authorized": False,
            "project_evidence_promoted": False,
            "release_blockers": validation_report["release_blockers"],
        },
        "output_request": {
            "path": display_path(request_path),
            "role": "COMPILED_PROJECT_GENERATION_REQUEST",
            "sha256": request_sha256,
            "canonical_sha256": canonical_sha256(compiled),
            "request_id": compiled["request_id"],
        },
        "engine_lineage_join": {
            "request_sha256": request_sha256,
            "e0_manifest_pointer": "generation_request.request_sha256",
            "cf0_manifest_pointer": "project_request_ref.sha256",
            "rule": "An engine result belongs to this compilation only when its request SHA-256 matches exactly.",
        },
        "warnings": [
            "The compiled request preserves project facts and provenance from the base request.",
            "Reference values, custom proposals without complete request provenance, user facts and rule packs were not auto-materialized.",
            "Preset materialization does not resolve evidence and does not authorize E1, E2, E3 or machine guidance.",
        ],
    }
    validate_schema_instance(
        manifest,
        load_json(DEFAULT_MANIFEST_SCHEMA),
        "compilation manifest",
    )

    if write_outputs:
        request_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        temporary_request = request_path.with_name(f".{request_path.name}.{token}.tmp")
        temporary_manifest = manifest_path.with_name(f".{manifest_path.name}.{token}.tmp")
        request_bytes = request_payload.encode("utf-8")
        manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        try:
            temporary_request.write_bytes(request_bytes)
            temporary_manifest.write_bytes(manifest_bytes)
            require(
                sha256_file(temporary_request) == request_sha256,
                "Staged compiled request verification failed.",
            )
            require(
                sha256_file(temporary_manifest) == manifest_sha256,
                "Staged compilation manifest verification failed.",
            )
            temporary_request.replace(request_path)
            temporary_manifest.replace(manifest_path)
            require(sha256_file(request_path) == request_sha256, "Compiled request write verification failed.")
            require(
                sha256_file(manifest_path) == manifest_sha256,
                "Compilation manifest write verification failed.",
            )
        finally:
            temporary_request.unlink(missing_ok=True)
            temporary_manifest.unlink(missing_ok=True)
    return compiled, manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--output-request", type=Path)
    parser.add_argument("--output-manifest", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        _, manifest = compile_request(
            args.package,
            output_request_path=args.output_request,
            output_manifest_path=args.output_manifest,
        )
    except (ContractError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"PRESET REQUEST COMPILATION FAILED: {exc}", file=sys.stderr)
        return 1
    summary = manifest["summary"]
    print(
        "PRESET REQUEST COMPILATION OK: "
        f"{summary['preserved_base_parameter_count']} base values preserved, "
        f"{summary['materialized_e0_parameter_count']} E0 values materialized, "
        f"{summary['withheld_parameter_count']} unresolved proposals withheld, "
        "0 evidence items promoted."
    )
    print(f"Wrote {manifest['output_request']['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
