"""Verify the fail-closed C1 embedded-terrace E0 screening package."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from osgeo import ogr, osr

try:
    from jsonschema import Draft202012Validator
except ImportError:  # Imperative checks remain mandatory.
    Draft202012Validator = None


REPO = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO / "dataset" / "derived" / "embedded_terrace_screening_manifest.json"
DEFAULT_SCHEMA = REPO / "schemas" / "embedded-terrace-screening-stage.schema.json"

SCHEMA_VERSION = "1.0.0"
MANIFEST_TYPE = "EMBEDDED_TERRACE_SCREENING_STAGE_RESULT"
RELEASE = "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED"
EXPECTED_LAYERS = {
    "terrace_alignment_candidates",
    "interterrace_strips",
    "row_candidates",
    "candidate_summary",
}
EXPECTED_LIMITATIONS = {
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
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ContractError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def resolve_path(value: str, manifest_path: Path) -> Path:
    source = Path(value)
    if source.is_absolute():
        return source.resolve()
    repository_candidate = (REPO / source).resolve()
    if repository_candidate.exists():
        return repository_candidate
    return (manifest_path.parent / source).resolve()


def validate_file_record(record: Any, label: str, manifest_path: Path) -> Path:
    require(isinstance(record, dict), f"{label} must be an object.")
    require(set(record) == {"path", "size_bytes", "sha256", "role"}, f"{label} fields changed.")
    require(isinstance(record["role"], str) and record["role"], f"{label}.role is required.")
    require(isinstance(record["sha256"], str) and SHA256_PATTERN.fullmatch(record["sha256"]), f"{label}.sha256 is invalid.")
    path = resolve_path(record["path"], manifest_path)
    require(path.is_file(), f"{label} does not exist: {path}")
    require(path.stat().st_size == record["size_bytes"], f"{label}.size_bytes mismatch.")
    require(sha256_file(path) == record["sha256"], f"{label}.sha256 mismatch.")
    return path


def finite_number(value: Any, label: str, *, minimum: float | None = None) -> float:
    require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{label} must be numeric.")
    number = float(value)
    require(math.isfinite(number), f"{label} must be finite.")
    if minimum is not None:
        require(number >= minimum, f"{label} must be >= {minimum}.")
    return number


def validate_forbidden_claims(manifest: dict[str, Any]) -> None:
    require(manifest.get("release") == RELEASE, "Release is not the C1 E0 screening release.")
    require(set(manifest.get("release_limitations", [])) == EXPECTED_LIMITATIONS, "Release limitations changed.")
    variants = manifest.get("variant_status", {})
    ti = variants.get("EMBUTIDA_TI", {})
    td = variants.get("EMBUTIDA_TD", {})
    require(ti.get("status") == "GENERATED_SCREENING_ONLY_NOT_DIMENSIONED", "TI status changed.")
    require(ti.get("geometry_role") == "TERRAIN_ISOLINE_SENSITIVITY_NOT_APPROVED_TI", "TI geometry role changed.")
    require(ti.get("pce_status") == "NOT_EVALUATED", "TI made a PCE claim.")
    require(ti.get("pcx_status") == "NOT_EVALUATED", "TI made a PCX claim.")
    require(ti.get("hydraulic_status") == "HYDRAULIC_UNCONFIRMED", "TI made a hydraulic claim.")
    require(td.get("status") == "NOT_GENERATED_RECEIVER_MISSING", "TD must remain not generated.")
    require(td.get("geometry_count") == 0, "TD geometry count must be zero.")
    require(td.get("pce_status") == "NOT_EVALUATED", "TD made a PCE claim.")
    require(td.get("pcx_status") == "NOT_EVALUATED", "TD made a PCX claim.")
    require(td.get("hydraulic_status") == "HYDRAULIC_UNCONFIRMED", "TD made a hydraulic claim.")
    assumptions = manifest.get("assumptions", {})
    require(assumptions.get("parameter_class") == "E0_ASSUMPTION", "Intervals are not labelled E0 assumptions.")
    require(assumptions.get("selection_meaning") == "GEOMETRIC_SENSITIVITY_GRID_ONLY", "Intervals gained agronomic meaning.")
    require(assumptions.get("selection_rule") == "DATASET_RELIEF_LEGIBILITY_GRID_V1", "Interval selection rule changed.")
    require(assumptions.get("interval_role") == "ELEVATION_ISOLINE_SAMPLING_NOT_PCE_SPACING", "Intervals gained PCE spacing meaning.")
    require(assumptions.get("vertical_accuracy_status") == "NOT_VALIDATED", "Vertical accuracy gained unsupported validation.")
    require(assumptions.get("vertical_interval_candidates_m") == [2.0, 4.0, 6.0], "Dataset legibility grid changed without a new rule revision.")
    require(assumptions.get("topology_gap_role") == "NUMERICAL_PARTITION_SUPPORT_NOT_SECTION_WIDTH", "Topology gap gained section meaning.")
    require(assumptions.get("row_source_candidate_id") == "CF0C_OPERACAO", "Unexpected diagnostic row source.")

    candidate_ids: set[str] = set()
    for index, candidate in enumerate(manifest.get("candidates", [])):
        label = f"candidates[{index}]"
        candidate_id = candidate.get("candidate_id")
        require(isinstance(candidate_id, str) and candidate_id, f"{label}.candidate_id is required.")
        require(candidate_id not in candidate_ids, f"Duplicate candidate_id: {candidate_id}")
        candidate_ids.add(candidate_id)
        require(candidate.get("variant") == "EMBUTIDA_TI", f"{label} is not TI screening.")
        require(candidate.get("eligibility_status") == "GEOMETRIC_PRECURSOR", f"{label} eligibility changed.")
        require(candidate.get("pce_status") == "NOT_EVALUATED", f"{label} made a PCE claim.")
        require(candidate.get("pcx_status") == "NOT_EVALUATED", f"{label} made a PCX claim.")
        require(candidate.get("hydraulic_status") == "HYDRAULIC_UNCONFIRMED", f"{label} made a hydraulic claim.")
        require(candidate.get("construction_status") == "NOT_EVALUATED", f"{label} made a construction claim.")
        require(candidate.get("operational_status") == "DIAGNOSTIC_ONLY_NOT_ROUTED", f"{label} made an operational claim.")
        require(candidate.get("guidance_status") == "NOT_AUTHORIZED", f"{label} authorized guidance.")
        blockers = candidate.get("blocker_codes", [])
        require("PCE_SOLVER_NOT_RUN" in blockers and "PCX_SOLVER_NOT_RUN" in blockers, f"{label} lacks PCE/PCX blockers.")
        require("VERTICAL_ACCURACY_NOT_VALIDATED" in blockers, f"{label} hides unvalidated vertical accuracy.")
        require("ROW_CANDIDATES_DERIVED_FROM_CF0C_NOT_RESOLVED_PER_STRIP" in blockers, f"{label} hides row provenance limitation.")

    td_ids: set[str] = set()
    for index, record in enumerate(manifest.get("td_not_generated", [])):
        label = f"td_not_generated[{index}]"
        require(record.get("variant") == "EMBUTIDA_TD", f"{label}.variant changed.")
        require(record.get("status") == "NOT_GENERATED_RECEIVER_MISSING", f"{label} status changed.")
        require(record.get("geometry_count") == 0, f"{label} has geometry.")
        require("TD_RECEIVER_MISSING" in record.get("blocker_codes", []), f"{label} lacks receiver blocker.")
        candidate_id = record.get("candidate_id")
        require(candidate_id not in candidate_ids and candidate_id not in td_ids, f"Duplicate TD candidate_id: {candidate_id}")
        td_ids.add(candidate_id)


def validate_schema(manifest: dict[str, Any], schema_path: Path) -> str:
    require(schema_path.is_file(), f"Schema does not exist: {schema_path}")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    require(schema.get("$id", "").endswith("embedded-terrace-screening-stage.schema.json"), "Unexpected schema file.")
    if Draft202012Validator is None:
        return "IMPERATIVE_CONTRACT_CHECKS_ONLY_JSONSCHEMA_UNAVAILABLE"
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(manifest),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "manifest"
        raise ContractError(f"JSON Schema violation at {location}: {first.message}")
    return "JSON_SCHEMA_DRAFT_2020_12_PLUS_IMPERATIVE"


def spatial_refs_match(first: osr.SpatialReference | None, second: osr.SpatialReference) -> bool:
    if first is None:
        return False
    left = first.Clone()
    right = second.Clone()
    left.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    right.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return bool(left.IsSame(right))


def geometry_has_finite_z(geometry: ogr.Geometry) -> bool:
    if geometry is None or geometry.IsEmpty() or geometry.GetCoordinateDimension() < 3:
        return False
    flattened = ogr.GT_Flatten(geometry.GetGeometryType())
    if flattened == ogr.wkbLineString:
        return all(len(point) >= 3 and all(math.isfinite(float(value)) for value in point[:3]) for point in geometry.GetPoints())
    return all(geometry_has_finite_z(geometry.GetGeometryRef(index)) for index in range(geometry.GetGeometryCount()))


def feature_value(feature: ogr.Feature, name: str) -> Any:
    require(feature.GetFieldIndex(name) >= 0, f"Layer lacks required field {name}.")
    return feature.GetField(name)


def validate_geopackage(manifest: dict[str, Any], path: Path) -> dict[str, Any]:
    datasource = ogr.Open(str(path), 0)
    require(datasource is not None, f"Could not open GeoPackage: {path}")
    layer_names = {datasource.GetLayerByIndex(index).GetName() for index in range(datasource.GetLayerCount())}
    require(layer_names == EXPECTED_LAYERS, f"Unexpected layer set: {sorted(layer_names)}")
    expected_srs = osr.SpatialReference()
    expected_srs.ImportFromWkt(manifest["crs"]["wkt"])
    grouped = {
        "axis": Counter(),
        "strip": Counter(),
        "row": Counter(),
        "summary": Counter(),
    }
    invalid_geometry_count = 0
    non_3d_line_count = 0
    forbidden_td_geometry_count = 0
    hydraulic_pass_claim_count = 0
    guidance_authorized_claim_count = 0

    for layer_name in sorted(EXPECTED_LAYERS):
        layer = datasource.GetLayerByName(layer_name)
        expected_count = manifest["layer_counts"][layer_name]
        require(layer.GetFeatureCount() == expected_count, f"{layer_name} count mismatch.")
        if layer_name != "candidate_summary":
            require(spatial_refs_match(layer.GetSpatialRef(), expected_srs), f"{layer_name} CRS mismatch.")
        for feature in layer:
            candidate_id = str(feature_value(feature, "candidate_id"))
            geometry = feature.GetGeometryRef()
            if geometry is not None:
                invalid_geometry_count += int(geometry.IsEmpty() or not bool(geometry.IsValid()))
                if layer_name in {"terrace_alignment_candidates", "row_candidates"}:
                    non_3d_line_count += int(not geometry_has_finite_z(geometry))
            variant = str(feature_value(feature, "variant"))
            if layer_name != "candidate_summary":
                forbidden_td_geometry_count += int(variant == "EMBUTIDA_TD")
            if layer_name == "terrace_alignment_candidates":
                grouped["axis"][candidate_id] += 1
                require(variant == "EMBUTIDA_TI", "Axis is not TI screening geometry.")
                require(feature_value(feature, "axis_status") == "SCREENING_ONLY_NOT_DIMENSIONED", "Axis gained project status.")
                require(feature_value(feature, "pce_status") == "NOT_EVALUATED", "Axis gained PCE status.")
                require(feature_value(feature, "pcx_status") == "NOT_EVALUATED", "Axis gained PCX status.")
                require("C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED" in str(feature_value(feature, "blocker_codes")), "Axis lacks screening blocker.")
            elif layer_name == "interterrace_strips":
                grouped["strip"][candidate_id] += 1
                require(variant == "EMBUTIDA_TI", "Strip is not TI screening geometry.")
                require(feature_value(feature, "partition_status") == "TOPOLOGY_ONLY_NOT_SECTION", "Strip gained section meaning.")
                require(feature_value(feature, "section_status") == "NOT_EVALUATED", "Strip gained section status.")
            elif layer_name == "row_candidates":
                grouped["row"][candidate_id] += 1
                require(feature_value(feature, "source_candidate_id") == "CF0C_OPERACAO", "Row source changed.")
                require(feature_value(feature, "diagnostic_status") == "NOT_APPROVED", "Diagnostic row was approved.")
                require("ROW_CANDIDATES_DERIVED_FROM_CF0C_NOT_RESOLVED_PER_STRIP" in str(feature_value(feature, "blocker_codes")), "Row hides source limitation.")
            else:
                grouped["summary"][candidate_id] += 1
                require(grouped["summary"][candidate_id] == 1, f"Duplicate summary for {candidate_id}.")

            if feature.GetFieldIndex("hydraulic_status") >= 0:
                hydraulic_pass_claim_count += int(str(feature_value(feature, "hydraulic_status")) != "HYDRAULIC_UNCONFIRMED")
            if feature.GetFieldIndex("guidance_status") >= 0:
                guidance_authorized_claim_count += int(str(feature_value(feature, "guidance_status")) != "NOT_AUTHORIZED")

    candidate_by_id = {candidate["candidate_id"]: candidate for candidate in manifest["candidates"]}
    td_by_id = {record["candidate_id"]: record for record in manifest["td_not_generated"]}
    require(set(grouped["summary"]) == set(candidate_by_id) | set(td_by_id), "Summary candidate set mismatch.")
    for candidate_id, candidate in candidate_by_id.items():
        require(grouped["axis"][candidate_id] == candidate["axis_count"], f"{candidate_id} axis count mismatch.")
        require(grouped["strip"][candidate_id] == candidate["strip_count"], f"{candidate_id} strip count mismatch.")
        require(grouped["row"][candidate_id] == candidate["diagnostic_row_segment_count"], f"{candidate_id} row count mismatch.")
        require(candidate["metrics"]["diagnostic_segment_count"] == candidate["diagnostic_row_segment_count"], f"{candidate_id} metric row count mismatch.")
        finite_number(candidate["metrics"]["terrace_total_length_m"], f"{candidate_id}.terrace_total_length_m", minimum=0)
        finite_number(candidate["metrics"]["diagnostic_total_length_m"], f"{candidate_id}.diagnostic_total_length_m", minimum=0)
    for candidate_id in td_by_id:
        require(grouped["axis"][candidate_id] == grouped["strip"][candidate_id] == grouped["row"][candidate_id] == 0, f"{candidate_id} has forbidden geometry.")
    datasource = None

    observed = {
        "invalid_geometry_count": invalid_geometry_count,
        "non_3d_line_count": non_3d_line_count,
        "forbidden_td_geometry_count": forbidden_td_geometry_count,
        "hydraulic_pass_claim_count": hydraulic_pass_claim_count,
        "guidance_authorized_claim_count": guidance_authorized_claim_count,
    }
    for key, value in observed.items():
        require(value == 0, f"GeoPackage {key} must be zero, found {value}.")
        require(manifest["qa"][key] == value, f"qa.{key} mismatch.")
    require(sum(grouped["axis"].values()) == manifest["qa"]["axis_count"], "qa.axis_count mismatch.")
    require(sum(grouped["strip"].values()) == manifest["qa"]["strip_count"], "qa.strip_count mismatch.")
    require(sum(grouped["row"].values()) == manifest["qa"]["diagnostic_row_segment_count"], "qa diagnostic row count mismatch.")
    return {
        "layers": {name: manifest["layer_counts"][name] for name in sorted(EXPECTED_LAYERS)},
        **observed,
    }


def validate_manifest_shape(manifest: dict[str, Any], manifest_path: Path) -> tuple[Path, Path]:
    require(manifest.get("schema_version") == SCHEMA_VERSION, "Manifest schema version mismatch.")
    require(manifest.get("manifest_type") == MANIFEST_TYPE, "Manifest type mismatch.")
    require(manifest.get("stage_status") in {"SCREENING_ONLY_PCE_PCX_UNCONFIRMED", "NO_GEOMETRIC_PRECURSOR"}, "Invalid stage status.")
    validate_forbidden_claims(manifest)
    request_ref = manifest.get("project_request_ref", {})
    sensitivity_ref = manifest.get("sensitivity_request_ref", {})
    for label, reference in (("project_request_ref", request_ref), ("sensitivity_request_ref", sensitivity_ref)):
        require(set(reference) == {"id", "path", "sha256"}, f"{label} fields changed.")
        path = resolve_path(reference["path"], manifest_path)
        require(path.is_file(), f"{label} path does not exist.")
        require(sha256_file(path) == reference["sha256"], f"{label} hash mismatch.")
    inputs = manifest.get("inputs", {})
    require(set(inputs) == {"terrain_dtm", "field_boundary", "cf0_manifest", "cf0_geopackage"}, "Input set changed.")
    input_paths = {key: validate_file_record(record, f"inputs.{key}", manifest_path) for key, record in inputs.items()}
    cf0_manifest = json.loads(input_paths["cf0_manifest"].read_text(encoding="utf-8"))
    require(cf0_manifest.get("release") == "CF0_GEOMETRIC_SCREENING", "Input CF0 release mismatch.")
    require(cf0_manifest.get("project_request_ref", {}).get("sha256") == request_ref["sha256"], "CF0 and C1 screening request hashes differ.")
    require(cf0_manifest.get("outputs", {}).get("geopackage", {}).get("sha256") == inputs["cf0_geopackage"]["sha256"], "CF0 GeoPackage hash disagrees with CF0 manifest.")

    crs = manifest.get("crs", {})
    require(crs.get("type") == "PROJECTED" and crs.get("authority") == "EPSG", "CRS must be projected EPSG.")
    require(crs.get("horizontal_unit") == "m", "CRS unit must be metre.")
    require(hashlib.sha256(crs.get("wkt", "").encode("utf-8")).hexdigest() == crs.get("wkt_sha256"), "CRS WKT hash mismatch.")
    intervals = manifest["assumptions"]["vertical_interval_candidates_m"]
    offsets = manifest["assumptions"]["offset_fractions"]
    require(intervals == sorted(set(intervals)) and all(float(value) > 0 for value in intervals), "Vertical intervals are invalid.")
    require(offsets == sorted(set(offsets)) and all(0 <= float(value) < 1 for value in offsets), "Offset fractions are invalid.")
    expected_ti_count = manifest["qa"]["field_count"] * len(intervals) * len(offsets)
    require(len(manifest["candidates"]) == expected_ti_count, "TI sensitivity matrix is incomplete.")
    require(len(manifest["td_not_generated"]) == manifest["qa"]["field_count"], "TD blocker records are incomplete.")
    require(manifest["qa"]["ti_candidate_count"] == len(manifest["candidates"]), "qa.ti_candidate_count mismatch.")
    require(manifest["qa"]["td_not_generated_count"] == len(manifest["td_not_generated"]), "qa.td_not_generated_count mismatch.")
    require(manifest["layer_counts"]["candidate_summary"] == len(manifest["candidates"]) + len(manifest["td_not_generated"]), "candidate_summary count mismatch.")
    if manifest["qa"]["power_inventory_status"] == "DECLARED_NONE":
        require(manifest["qa"]["power_constraint_effect"] == "NOT_APPLICABLE_DECLARED_NONE", "Declared-none power status was converted into a blocker.")
    return input_paths["cf0_manifest"], input_paths["cf0_geopackage"]


def verify(manifest_path: Path, schema_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    require(manifest_path.is_file(), f"Manifest does not exist: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema_validation = validate_schema(manifest, schema_path.resolve())
    validate_manifest_shape(manifest, manifest_path)
    output_gpkg = validate_file_record(manifest["outputs"]["geopackage"], "outputs.geopackage", manifest_path)
    validate_file_record(manifest["outputs"]["map"], "outputs.map", manifest_path)
    gpkg = validate_geopackage(manifest, output_gpkg)
    return {
        "status": "VERIFIED",
        "release": RELEASE,
        "stage_status": manifest["stage_status"],
        "manifest": str(manifest_path),
        "schema_validation": schema_validation,
        "geopackage": gpkg,
        "pce_status": "NOT_EVALUATED",
        "pcx_status": "NOT_EVALUATED",
        "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
        "td_status": "NOT_GENERATED_RECEIVER_MISSING",
        "guidance_authorized": False,
    }


def run_self_test() -> dict[str, Any]:
    base = {
        "release": RELEASE,
        "release_limitations": sorted(EXPECTED_LIMITATIONS),
        "assumptions": {
            "parameter_class": "E0_ASSUMPTION",
            "selection_meaning": "GEOMETRIC_SENSITIVITY_GRID_ONLY",
            "selection_rule": "DATASET_RELIEF_LEGIBILITY_GRID_V1",
            "interval_role": "ELEVATION_ISOLINE_SAMPLING_NOT_PCE_SPACING",
            "vertical_accuracy_status": "NOT_VALIDATED",
            "vertical_interval_candidates_m": [2.0, 4.0, 6.0],
            "topology_gap_role": "NUMERICAL_PARTITION_SUPPORT_NOT_SECTION_WIDTH",
            "row_source_candidate_id": "CF0C_OPERACAO",
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
                "geometry_count": 0,
                "pce_status": "NOT_EVALUATED",
                "pcx_status": "NOT_EVALUATED",
                "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
            },
        },
        "candidates": [
            {
                "candidate_id": "C1E0_TI_F1_VI5_O0P00",
                "variant": "EMBUTIDA_TI",
                "eligibility_status": "GEOMETRIC_PRECURSOR",
                "pce_status": "NOT_EVALUATED",
                "pcx_status": "NOT_EVALUATED",
                "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
                "construction_status": "NOT_EVALUATED",
                "operational_status": "DIAGNOSTIC_ONLY_NOT_ROUTED",
                "guidance_status": "NOT_AUTHORIZED",
                "blocker_codes": [
                    "PCE_SOLVER_NOT_RUN",
                    "PCX_SOLVER_NOT_RUN",
                    "VERTICAL_ACCURACY_NOT_VALIDATED",
                    "ROW_CANDIDATES_DERIVED_FROM_CF0C_NOT_RESOLVED_PER_STRIP",
                ],
            }
        ],
        "td_not_generated": [
            {
                "candidate_id": "C1E0_TD_F1_NOT_GENERATED",
                "variant": "EMBUTIDA_TD",
                "status": "NOT_GENERATED_RECEIVER_MISSING",
                "geometry_count": 0,
                "blocker_codes": ["TD_RECEIVER_MISSING", "PCX_SOLVER_NOT_RUN"],
            }
        ],
    }
    validate_forbidden_claims(base)
    checks = ["FAIL_CLOSED_BASE_ACCEPTED"]
    tampered = json.loads(json.dumps(base))
    tampered["assumptions"]["vertical_interval_candidates_m"] = [2.0, 3.0, 4.0]
    try:
        validate_forbidden_claims(tampered)
    except ContractError:
        checks.append("INTERVAL_GRID_TAMPERING_REJECTED")
    else:
        raise ContractError("Interval-grid tampering was not rejected.")
    tampered = json.loads(json.dumps(base))
    tampered["variant_status"]["EMBUTIDA_TI"]["hydraulic_status"] = "HYDRAULIC_PASS"
    try:
        validate_forbidden_claims(tampered)
    except ContractError:
        checks.append("HYDRAULIC_CLAIM_REJECTED")
    else:
        raise ContractError("Hydraulic claim tampering was not rejected.")
    tampered = json.loads(json.dumps(base))
    tampered["variant_status"]["EMBUTIDA_TD"]["geometry_count"] = 1
    try:
        validate_forbidden_claims(tampered)
    except ContractError:
        checks.append("TD_GEOMETRY_REJECTED")
    else:
        raise ContractError("TD geometry tampering was not rejected.")
    tampered = json.loads(json.dumps(base))
    tampered["candidates"][0]["guidance_status"] = "AUTHORIZED"
    try:
        validate_forbidden_claims(tampered)
    except ContractError:
        checks.append("GUIDANCE_CLAIM_REJECTED")
    else:
        raise ContractError("Guidance claim tampering was not rejected.")
    return {"status": "SELF_TEST_VERIFIED", "checks": checks}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = run_self_test() if args.self_test else verify(args.manifest, args.schema)
    except (ContractError, OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"C1_E0_VERIFICATION_FAILED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
