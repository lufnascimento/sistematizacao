"""Validate evidence contracts and report project readiness without inventing inputs."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
DEFAULT_PARAMETER_CATALOG = REPO / "config" / "catalogo_parametros_projeto.json"
DEFAULT_ACQUISITION_CATALOG = REPO / "config" / "catalogo_aquisicao_insumos.json"
DEFAULT_CAPABILITY_CATALOG = REPO / "config" / "catalogo_capacidades_motor.json"
DEFAULT_INVENTORY = REPO / "config" / "inventario_insumos_dataset_atual.json"
DEFAULT_OUTPUT = REPO / "dataset" / "derived" / "input_readiness_report.json"

EXTERNAL_PARAMETER_CLASSES = {"USER_FACT", "RULE_PACK"}
EVIDENCE_STATUSES = {
    "AVAILABLE_VERIFIED",
    "AVAILABLE_SCREENING",
    "PARTIAL",
    "MISSING",
    "NOT_APPLICABLE",
}
GAP_CLASSES = {
    "NONE",
    "PROJECT_INPUT_MISSING",
    "CALIBRATION_MISSING",
    "FIELD_VALIDATION_MISSING",
    "LEGAL_APPROVAL_MISSING",
}
CAPABILITY_STATUSES = {
    "IMPLEMENTED_VERIFIED",
    "IMPLEMENTED_LIMITED",
    "SPECIFIED_NOT_IMPLEMENTED",
    "NOT_IMPLEMENTED",
    "BLOCKED_BY_EVIDENCE",
}
DELIVERY_LEVELS = ("E0_TRIAGEM", "E1_OPERACIONAL", "E2_CONSERVACIONISTA", "E3_EXECUTIVO")
SCENARIOS = ("CF0_GEOMETRY", "C1_CURVA_EMBUTIDA", "C2_BASE_LARGA_PASSANTE", "C3_ESD", "POA_LOGISTICS", "GUIDANCE_EXPORT")
EVIDENCE_DOMAINS = {"SCOPE", "TERRAIN", "SOIL", "HYDROLOGY", "CONSERVATION", "FLEET", "CONSTRAINTS", "CONNECTIONS", "POA", "LOGISTICS", "QUALITY", "LIFECYCLE", "QA"}
ACQUISITION_MODES = {"CLIENT_UPLOAD", "FIELD_SURVEY", "LAB_TEST", "TELEMETRY", "OFFICIAL_DATA", "OEM_DOCUMENT", "PROFESSIONAL_CALIBRATION", "SYSTEM_CALCULATION"}
ABSENCE_POLICIES = {"SCREENING_ONLY", "NOT_EVALUATED", "BLOCK_E1", "BLOCK_E2", "BLOCK_E3"}


class ContractError(ValueError):
    """Raised when a committed readiness contract is internally inconsistent."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    require(isinstance(value, dict), f"JSON root must be an object: {path}")
    return value


def _unique_by_id(items: Any, label: str, key: str = "id") -> dict[str, dict[str, Any]]:
    require(isinstance(items, list) and items, f"{label} must be a non-empty array.")
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        require(isinstance(item, dict), f"{label}[{index}] must be an object.")
        item_id = item.get(key)
        require(isinstance(item_id, str) and item_id, f"{label}[{index}] needs {key}.")
        require(item_id not in result, f"Duplicate {label} id: {item_id}")
        result[item_id] = item
    return result


def validate_parameter_catalog(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    require(catalog.get("schema_version") == "1.2.0", "Unexpected project parameter catalog version.")
    parameters = _unique_by_id(catalog.get("parameters"), "parameter")
    for parameter_id, item in parameters.items():
        require(item.get("parameter_class") in {"USER_FACT", "RULE_PACK", "CALCULATED", "OPTIMIZER", "E0_ASSUMPTION"}, f"Unknown class: {parameter_id}")
        require(item.get("default", {}).get("policy") in {"NO_DEFAULT", "FAIL_CLOSED", "E0_ONLY"}, f"Missing default policy: {parameter_id}")
    return parameters


def validate_acquisition_catalog(
    catalog: dict[str, Any],
    parameters: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    require(catalog.get("schema_version") == "1.0.0", "Unexpected acquisition catalog version.")
    require(catalog.get("catalog_id") == "terraflux-input-acquisition-catalog", "Unexpected acquisition catalog id.")
    packages = _unique_by_id(catalog.get("evidence_packages"), "evidence package")
    primary_owner: dict[str, str] = {}
    for package_id, package in packages.items():
        required = {
            "title", "domain", "purpose", "primary_parameter_ids", "supporting_parameter_ids",
            "required_for", "acquisition_modes", "data_contract", "field_protocol",
            "qa_requirements", "freshness", "owner_roles", "absence_policy", "blocker_codes",
        }
        require(required.issubset(package), f"Evidence package is incomplete: {package_id}")
        require(package["domain"] in EVIDENCE_DOMAINS, f"Unknown evidence domain: {package_id}")
        require(package["absence_policy"] in ABSENCE_POLICIES, f"Unknown absence policy: {package_id}")
        primary_ids = package["primary_parameter_ids"]
        supporting_ids = package["supporting_parameter_ids"]
        require(isinstance(primary_ids, list), f"primary_parameter_ids must be an array: {package_id}")
        require(len(primary_ids) == len(set(primary_ids)), f"Duplicate primary parameter in {package_id}")
        require(isinstance(supporting_ids, list), f"supporting_parameter_ids must be an array: {package_id}")
        require(len(supporting_ids) == len(set(supporting_ids)), f"Duplicate supporting parameter in {package_id}")
        for parameter_id in primary_ids + supporting_ids:
            require(parameter_id in parameters, f"Unknown parameter {parameter_id} in {package_id}")
        for parameter_id in primary_ids:
            require(parameters[parameter_id]["parameter_class"] in EXTERNAL_PARAMETER_CLASSES, f"Calculated/optimizer parameter cannot have an acquisition owner: {parameter_id}")
            require(parameter_id not in primary_owner, f"Parameter has two acquisition owners: {parameter_id}")
            primary_owner[parameter_id] = package_id

        required_for = package["required_for"]
        require(set(required_for) == {"delivery_levels", "scenarios", "gates"}, f"Invalid required_for contract: {package_id}")
        require(set(required_for["delivery_levels"]) <= set(DELIVERY_LEVELS), f"Unknown delivery level: {package_id}")
        require(set(required_for["scenarios"]) <= set(SCENARIOS), f"Unknown scenario requirement: {package_id}")
        modes = package["acquisition_modes"]
        require(isinstance(modes, list) and modes, f"No acquisition mode: {package_id}")
        priorities = [mode.get("priority") for mode in modes]
        require(all(isinstance(value, int) and value > 0 for value in priorities), f"Invalid acquisition priority: {package_id}")
        require(len(priorities) == len(set(priorities)), f"Duplicate acquisition priority: {package_id}")
        for mode in modes:
            required_mode = {"mode", "priority", "authority", "source_ref", "method", "can_prefill", "can_release", "limitations"}
            require(required_mode.issubset(mode), f"Acquisition mode is incomplete: {package_id}")
            require(mode["mode"] in ACQUISITION_MODES, f"Unknown acquisition mode: {package_id}")
            if mode["mode"] == "OFFICIAL_DATA":
                require(mode["can_release"] is False, f"Public screening data cannot release a project by itself: {package_id}")
        data_contract = package["data_contract"]
        require(isinstance(data_contract.get("accepted_formats"), list) and data_contract["accepted_formats"], f"No accepted format: {package_id}")
        require(isinstance(data_contract.get("required_metadata"), list), f"No metadata contract: {package_id}")
        fields = data_contract.get("required_fields")
        require(isinstance(fields, list), f"No field contract: {package_id}")
        field_names = [field.get("name") for field in fields if isinstance(field, dict)]
        require(len(field_names) == len(fields) and all(isinstance(name, str) and name for name in field_names), f"Invalid field contract: {package_id}")
        require(len(field_names) == len(set(field_names)), f"Duplicate field contract: {package_id}")
        require(all({"name", "type", "unit", "required"} <= set(field) for field in fields), f"Incomplete field contract: {package_id}")
        require(isinstance(package["qa_requirements"], list) and package["qa_requirements"], f"No QA rules: {package_id}")
        require(isinstance(package["blocker_codes"], list) and package["blocker_codes"], f"No blockers: {package_id}")
        freshness = package["freshness"]
        require(set(freshness) == {"policy", "max_age_days", "refresh_triggers"}, f"Invalid freshness contract: {package_id}")
        require(isinstance(package["owner_roles"], list) and package["owner_roles"], f"No owner roles: {package_id}")

    externally_resolved = {
        parameter_id
        for parameter_id, item in parameters.items()
        if item["parameter_class"] in EXTERNAL_PARAMETER_CLASSES
    }
    missing = sorted(externally_resolved - set(primary_owner))
    extra = sorted(set(primary_owner) - externally_resolved)
    require(not missing, f"External parameters lack an acquisition owner: {', '.join(missing)}")
    require(not extra, f"Non-external parameters have an acquisition owner: {', '.join(extra)}")
    return packages


def validate_capability_catalog(
    catalog: dict[str, Any],
    packages: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    require(catalog.get("schema_version") == "1.0.0", "Unexpected capability catalog version.")
    require(catalog.get("catalog_id") == "terraflux-engine-capability-catalog", "Unexpected capability catalog id.")
    capabilities = _unique_by_id(catalog.get("capabilities"), "capability")
    for capability_id, capability in capabilities.items():
        require(capability.get("declared_status") in CAPABILITY_STATUSES, f"Unknown capability status: {capability_id}")
        evidence_dependencies = capability.get("required_evidence_packages")
        capability_dependencies = capability.get("required_capabilities")
        require(isinstance(evidence_dependencies, list), f"Missing evidence dependencies: {capability_id}")
        require(isinstance(capability_dependencies, list), f"Missing capability dependencies: {capability_id}")
        unknown_packages = sorted(set(evidence_dependencies) - set(packages))
        require(not unknown_packages, f"Unknown evidence dependencies on {capability_id}: {', '.join(unknown_packages)}")
        unknown_capabilities = sorted(set(capability_dependencies) - set(capabilities))
        require(not unknown_capabilities, f"Unknown capability dependencies on {capability_id}: {', '.join(unknown_capabilities)}")
        require(capability_id not in capability_dependencies, f"Capability depends on itself: {capability_id}")
    return capabilities


def validate_inventory(
    inventory: dict[str, Any],
    packages: dict[str, dict[str, Any]],
    capabilities: dict[str, dict[str, Any]],
    parameters: dict[str, dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    require(inventory.get("schema_version") == "1.0.0", "Unexpected evidence inventory version.")
    require(
        inventory.get("catalog_revision") == "terraflux-input-acquisition-catalog-1.0.0",
        "Inventory references another acquisition catalog revision.",
    )
    try:
        datetime.fromisoformat(str(inventory.get("assessed_at", "")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractError("Inventory assessed_at is not an ISO date-time.") from exc
    entries = _unique_by_id(inventory.get("entries"), "inventory entry", "package_id")
    assessments = _unique_by_id(inventory.get("capability_assessments"), "capability assessment", "capability_id")
    require(set(entries) == set(packages), "Inventory must assess every evidence package exactly once.")
    require(set(assessments) == set(capabilities), "Inventory must assess every engine capability exactly once.")
    for package_id, entry in entries.items():
        status = entry.get("status")
        gap_class = entry.get("gap_class")
        evidence_refs = entry.get("evidence_refs")
        resolved = entry.get("resolved_parameter_ids")
        require(status in EVIDENCE_STATUSES, f"Unknown inventory status: {package_id}")
        require(gap_class in GAP_CLASSES, f"Unknown gap class: {package_id}")
        require(isinstance(evidence_refs, list), f"evidence_refs must be an array: {package_id}")
        require(isinstance(resolved, list), f"resolved_parameter_ids must be an array: {package_id}")
        require(set(resolved) <= set(packages[package_id]["primary_parameter_ids"]), f"Resolved parameter is owned by another package: {package_id}")
        require(all(parameter_id in parameters for parameter_id in resolved), f"Unknown resolved parameter: {package_id}")
        if status in {"AVAILABLE_VERIFIED", "AVAILABLE_SCREENING"}:
            require(evidence_refs, f"Available evidence needs a reference: {package_id}")
        if status == "AVAILABLE_VERIFIED":
            require(gap_class == "NONE", f"Verified evidence cannot retain a gap: {package_id}")
        if status == "MISSING":
            require(gap_class != "NONE", f"Missing evidence needs a gap class: {package_id}")
        if status == "NOT_APPLICABLE":
            require(gap_class == "NONE", f"Not-applicable evidence cannot retain a gap: {package_id}")
    for capability_id, assessment in assessments.items():
        require(assessment.get("status") in CAPABILITY_STATUSES, f"Unknown assessed capability status: {capability_id}")
        require(isinstance(assessment.get("evidence_refs"), list), f"Capability evidence must be an array: {capability_id}")
        if assessment["status"] in {"IMPLEMENTED_VERIFIED", "IMPLEMENTED_LIMITED"}:
            require(assessment["evidence_refs"], f"Implemented capability needs code/test evidence: {capability_id}")
    return entries, assessments


def _package_ready(status: str, delivery_level: str) -> bool:
    if status == "NOT_APPLICABLE":
        return True
    if delivery_level == "E0_TRIAGEM":
        return status in {"AVAILABLE_VERIFIED", "AVAILABLE_SCREENING", "PARTIAL"}
    return status == "AVAILABLE_VERIFIED"


def build_report(
    parameter_catalog: dict[str, Any],
    acquisition_catalog: dict[str, Any],
    capability_catalog: dict[str, Any],
    inventory: dict[str, Any],
) -> dict[str, Any]:
    parameters = validate_parameter_catalog(parameter_catalog)
    packages = validate_acquisition_catalog(acquisition_catalog, parameters)
    capabilities = validate_capability_catalog(capability_catalog, packages)
    entries, assessments = validate_inventory(inventory, packages, capabilities, parameters)

    status_counts = Counter(entry["status"] for entry in entries.values())
    gap_counts = Counter(entry["gap_class"] for entry in entries.values() if entry["gap_class"] != "NONE")
    capability_counts = Counter(item["status"] for item in assessments.values())
    delivery_readiness: dict[str, Any] = {}
    for level in DELIVERY_LEVELS:
        required = sorted(package_id for package_id, package in packages.items() if level in package["required_for"]["delivery_levels"])
        blockers = [
            {
                "package_id": package_id,
                "status": entries[package_id]["status"],
                "gap_class": entries[package_id]["gap_class"],
                "blocker_codes": packages[package_id]["blocker_codes"],
                "next_actions": entries[package_id]["next_actions"],
            }
            for package_id in required
            if not _package_ready(entries[package_id]["status"], level)
        ]
        delivery_readiness[level] = {
            "ready": not blockers,
            "required_package_count": len(required),
            "blocker_count": len(blockers),
            "blockers": blockers,
        }

    scenario_readiness: dict[str, Any] = {}

    def capability_closure(seed_ids: list[str]) -> set[str]:
        pending = list(seed_ids)
        resolved: set[str] = set()
        while pending:
            capability_id = pending.pop()
            if capability_id in resolved:
                continue
            resolved.add(capability_id)
            pending.extend(capabilities[capability_id]["required_capabilities"])
        return resolved

    for scenario in SCENARIOS:
        matching_capabilities = [
            capability_id
            for capability_id, capability in capabilities.items()
            if scenario in capability.get("outputs", []) or capability_id == scenario
        ]
        relevant_capabilities = capability_closure(matching_capabilities)
        direct_packages = {
            package_id
            for package_id, package in packages.items()
            if scenario in package["required_for"]["scenarios"]
        }
        transitive_capability_packages = {
            package_id
            for capability_id in relevant_capabilities
            for package_id in capabilities[capability_id]["required_evidence_packages"]
        }
        required_packages = sorted(direct_packages | transitive_capability_packages)
        package_blockers = [
            package_id
            for package_id in required_packages
            if not _package_ready(entries[package_id]["status"], "E2_CONSERVACIONISTA")
        ]
        implementation_blockers = [
            capability_id
            for capability_id in sorted(relevant_capabilities)
            if assessments[capability_id]["status"] not in {"IMPLEMENTED_VERIFIED", "IMPLEMENTED_LIMITED"}
        ]
        scenario_readiness[scenario] = {
            "ready_for_approved_output": not package_blockers and not implementation_blockers,
            "required_evidence_packages": required_packages,
            "evidence_blockers": package_blockers,
            "implementation_blockers": implementation_blockers,
        }

    unresolved_parameters = sorted(
        parameter_id
        for package_id, package in packages.items()
        for parameter_id in package["primary_parameter_ids"]
        if parameter_id not in set(entries[package_id]["resolved_parameter_ids"])
    )
    return {
        "schema_version": "1.0.0",
        "report_id": f"{inventory['inventory_id']}-readiness",
        "project_id": inventory["project_id"],
        "assessed_at": inventory["assessed_at"],
        "contracts": {
            "parameter_catalog_revision": parameter_catalog["schema_version"],
            "acquisition_catalog_revision": acquisition_catalog["schema_version"],
            "capability_catalog_revision": capability_catalog["schema_version"],
        },
        "summary": {
            "parameter_count": len(parameters),
            "external_parameter_count": sum(item["parameter_class"] in EXTERNAL_PARAMETER_CLASSES for item in parameters.values()),
            "evidence_package_count": len(packages),
            "evidence_status_counts": dict(sorted(status_counts.items())),
            "gap_class_counts": dict(sorted(gap_counts.items())),
            "capability_status_counts": dict(sorted(capability_counts.items())),
            "unresolved_external_parameter_count": len(unresolved_parameters),
        },
        "delivery_readiness": delivery_readiness,
        "scenario_readiness": scenario_readiness,
        "unresolved_external_parameter_ids": unresolved_parameters,
        "evidence_inventory": [entries[package_id] for package_id in sorted(entries)],
        "capability_inventory": [assessments[capability_id] for capability_id in sorted(assessments)],
        "interpretation": {
            "missing_project_input": "Cliente, levantamento, laboratorio, telemetria ou documento ainda nao forneceu a evidencia.",
            "calibration_missing": "Metodo existe, mas a regra regional ou modelo local ainda nao foi aprovado.",
            "field_validation_missing": "Ha dado de triagem, mas falta confronto independente ou ensaio no campo.",
            "legal_approval_missing": "Geometria ou acao carece de permissao valida e espacializada.",
            "solver_missing": "Mesmo com todos os dados, a capacidade indicada continua sem implementacao executavel.",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parameter-catalog", type=Path, default=DEFAULT_PARAMETER_CATALOG)
    parser.add_argument("--acquisition-catalog", type=Path, default=DEFAULT_ACQUISITION_CATALOG)
    parser.add_argument("--capability-catalog", type=Path, default=DEFAULT_CAPABILITY_CATALOG)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true", help="Validate and print without writing the report.")
    args = parser.parse_args()
    try:
        report = build_report(
            load_json(args.parameter_catalog),
            load_json(args.acquisition_catalog),
            load_json(args.capability_catalog),
            load_json(args.inventory),
        )
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    if not args.check:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    print(json.dumps({"status": "VALID", "output": None if args.check else str(args.output), "summary": report["summary"], "delivery_readiness": {key: value["ready"] for key, value in report["delivery_readiness"].items()}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
