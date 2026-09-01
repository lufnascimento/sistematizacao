#!/usr/bin/env python3
"""Fail-closed verification for the C1 E0 embedded-terrace report."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Sequence

try:
    from pypdf import PdfReader
except ModuleNotFoundError:
    _pdf_sites = [
        *Path.home().glob(
            "AppData/Local/Packages/PythonSoftwareFoundation.Python.*/LocalCache/local-packages/Python*/site-packages"
        ),
        *Path.home().glob("AppData/Roaming/Python/Python*/site-packages"),
        *Path.home().glob("AppData/Local/Programs/Python/Python*/Lib/site-packages"),
    ]
    for _pdf_site in _pdf_sites:
        sys.path.append(str(_pdf_site))
        try:
            from pypdf import PdfReader
        except ModuleNotFoundError:
            continue
        break
    else:  # pragma: no cover - environment contract
        raise ModuleNotFoundError("pypdf is required to verify the C1 report.")

try:
    from generate_c1_screening_report import (
        A4_LANDSCAPE_PT,
        ARTIFACT_ID,
        PROHIBITED_EXECUTIVE_CLAIMS,
        REPORT_SCHEMA_VERSION,
        REQUIRED_TEXT_TOKENS,
        SEAL,
        SOURCE_RELEASE,
    )
    from verify_embedded_terrace_screening import verify as verify_source_package
except ModuleNotFoundError:
    from scripts.generate_c1_screening_report import (
        A4_LANDSCAPE_PT,
        ARTIFACT_ID,
        PROHIBITED_EXECUTIVE_CLAIMS,
        REPORT_SCHEMA_VERSION,
        REQUIRED_TEXT_TOKENS,
        SEAL,
        SOURCE_RELEASE,
    )
    from scripts.verify_embedded_terrace_screening import verify as verify_source_package


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "dataset" / "derived" / "embedded_terrace_screening_report_manifest.json"
EXPECTED_INPUTS = {"source_manifest", "source_schema", "source_geopackage", "source_map"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class VerificationError(RuntimeError):
    """Raised when the C1 report package violates its release contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"Cannot read JSON: {path}") from exc
    require(isinstance(payload, dict), f"JSON must be an object: {path}")
    return payload


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_path(value: str, manifest_path: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    repository_candidate = (ROOT / path).resolve()
    if repository_candidate.exists():
        return repository_candidate
    return (manifest_path.parent / path).resolve()


def validate_record(value: Any, label: str, manifest_path: Path) -> Path:
    require(isinstance(value, dict), f"{label} must be an object.")
    require(set(value) == {"path", "size_bytes", "sha256"}, f"{label} fields changed.")
    require(isinstance(value["path"], str) and value["path"], f"{label}.path is invalid.")
    require(isinstance(value["size_bytes"], int) and value["size_bytes"] > 0, f"{label}.size_bytes is invalid.")
    require(isinstance(value["sha256"], str) and SHA256.fullmatch(value["sha256"]), f"{label}.sha256 is invalid.")
    path = resolve_path(value["path"], manifest_path)
    require(path.is_file(), f"{label} is missing: {path}")
    require(path.stat().st_size == value["size_bytes"], f"{label} size mismatch.")
    require(sha256_file(path) == value["sha256"], f"{label} SHA-256 mismatch.")
    return path


def validate_release_boundary(boundary: Any) -> None:
    require(isinstance(boundary, dict), "Missing release boundary.")
    require(boundary.get("document_role") == "C1_E0_GEOMETRIC_SENSITIVITY_COMPARISON", "Document role changed.")
    require(boundary.get("release") == SOURCE_RELEASE, "Report release changed.")
    require(boundary.get("seal") == SEAL, "Report seal changed.")
    require(boundary.get("ti_status") == "GENERATED_SCREENING_ONLY_NOT_DIMENSIONED", "TI status changed.")
    require(boundary.get("td_status") == "NOT_GENERATED_RECEIVER_MISSING", "TD status changed.")
    require(boundary.get("pce_status") == "NOT_EVALUATED", "PCE status changed.")
    require(boundary.get("pcx_status") == "NOT_EVALUATED", "PCX status changed.")
    require(boundary.get("hydraulic_status") == "HYDRAULIC_UNCONFIRMED", "Hydraulic status changed.")
    require(boundary.get("guidance_status") == "NOT_AUTHORIZED", "Guidance status changed.")
    require(boundary.get("power_inventory_status") in {"PROVIDED", "DECLARED_NONE", "NOT_REVIEWED"}, "Power status is invalid.")
    if boundary.get("power_inventory_status") == "DECLARED_NONE":
        require(boundary.get("power_constraint_effect") == "NOT_APPLICABLE_DECLARED_NONE", "Declared-none power became a blocker.")
    require(boundary.get("general_constraint_inventory_status") in {"COMPLETE", "PARTIAL", "NOT_REVIEWED"}, "General constraint inventory status is invalid.")


def inspect_pdf(pdf_path: Path, pages: Sequence[dict[str, Any]]) -> dict[str, Any]:
    reader = PdfReader(str(pdf_path))
    require(len(reader.pages) == len(pages), "PDF page count differs from report manifest.")
    extracted_pages: list[str] = []
    for expected, pdf_page in zip(pages, reader.pages):
        width = float(pdf_page.mediabox.width)
        height = float(pdf_page.mediabox.height)
        require(width > height, f"PDF page {expected['page']} is not landscape.")
        require(math.isclose(width, A4_LANDSCAPE_PT[0], abs_tol=2.0), f"PDF page {expected['page']} is not A4 width.")
        require(math.isclose(height, A4_LANDSCAPE_PT[1], abs_tol=2.0), f"PDF page {expected['page']} is not A4 height.")
        text = pdf_page.extract_text() or ""
        require(len(text.strip()) >= 100, f"PDF page {expected['page']} is empty or not searchable.")
        require(SEAL in text, f"PDF page {expected['page']} lacks the release seal.")
        require(expected["title"] in text, f"PDF page {expected['page']} title is not searchable.")
        contents = pdf_page.get_contents()
        content_data = contents.get_data() if contents is not None else b""
        require(len(content_data) > 250, f"PDF page {expected['page']} has an empty content stream.")
        extracted_pages.append(text)
    extracted = "\n".join(extracted_pages)
    for token in REQUIRED_TEXT_TOKENS:
        require(token in extracted, f"Required searchable token missing: {token}")
    upper = extracted.upper()
    for claim in PROHIBITED_EXECUTIVE_CLAIMS:
        require(claim not in upper, f"Prohibited executive claim found: {claim}")
    return {"page_count": len(reader.pages), "all_pages_a4_landscape": True, "all_pages_searchable": True}


def verify(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    require(manifest_path.is_file(), f"Report manifest does not exist: {manifest_path}")
    manifest = read_json(manifest_path)
    require(manifest.get("schema_version") == REPORT_SCHEMA_VERSION, "Unexpected report schema version.")
    require(manifest.get("artifact_id") == ARTIFACT_ID, "Unexpected report artifact id.")
    inputs = manifest.get("inputs")
    require(isinstance(inputs, dict) and set(inputs) == EXPECTED_INPUTS, "Report input set changed.")
    paths = {name: validate_record(value, f"inputs.{name}", manifest_path) for name, value in inputs.items()}
    source = read_json(paths["source_manifest"])
    require(source.get("outputs", {}).get("geopackage", {}).get("sha256") == inputs["source_geopackage"]["sha256"], "Report GeoPackage differs from C1 manifest.")
    require(source.get("outputs", {}).get("map", {}).get("sha256") == inputs["source_map"]["sha256"], "Report map differs from C1 manifest.")
    source_result = verify_source_package(paths["source_manifest"], paths["source_schema"])
    require(source_result.get("status") == "VERIFIED", "C1 source package is no longer verified.")
    snapshot = manifest.get("source_snapshot")
    require(isinstance(snapshot, dict), "Missing source snapshot.")
    require(snapshot.get("release") == source.get("release") == SOURCE_RELEASE, "Source release snapshot mismatch.")
    require(snapshot.get("stage_status") == source.get("stage_status"), "Source stage status snapshot mismatch.")
    require(snapshot.get("source_verification_status") == "VERIFIED", "Source verification snapshot changed.")
    require(snapshot.get("project_request_sha256") == source["project_request_ref"]["sha256"], "Project-request lineage mismatch.")
    require(snapshot.get("sensitivity_request_sha256") == source["sensitivity_request_ref"]["sha256"], "Sensitivity-request lineage mismatch.")
    for key in ("ti_candidate_count", "td_not_generated_count", "axis_count", "strip_count", "diagnostic_row_segment_count"):
        require(snapshot.get(key) == source["qa"][key], f"Source snapshot mismatch for {key}.")
    validate_release_boundary(manifest.get("release_boundary"))
    require(tuple(manifest.get("required_text_tokens", [])) == REQUIRED_TEXT_TOKENS, "Required-token contract changed.")
    require(tuple(manifest.get("prohibited_executive_claims", [])) == PROHIBITED_EXECUTIVE_CLAIMS, "Prohibited-claim contract changed.")
    pages = manifest.get("pages")
    require(isinstance(pages, list) and len(pages) >= 8, "Report page plan is missing or too short.")
    require([page.get("page") for page in pages] == list(range(1, len(pages) + 1)), "Report page numbering is invalid.")
    require(manifest.get("page_count") == len(pages), "Declared page count differs from page plan.")
    required_kinds = {"cover", "scope", "qa", "interval_summary", "candidate_table", "interval_maps", "td", "gaps", "traceability"}
    require(required_kinds.issubset({page.get("kind") for page in pages}), "Required report page kinds are missing.")
    pdf_path = validate_record(manifest.get("pdf"), "pdf", manifest_path)
    pdf_check = inspect_pdf(pdf_path, pages)
    preflight = manifest.get("preflight")
    require(isinstance(preflight, dict) and preflight.get("status") == "PASS", "Generator preflight is not PASS.")
    require(preflight.get("pdf_page_count") == len(pages), "Generator preflight page count mismatch.")
    map_preflight = manifest.get("map_preflight")
    require(isinstance(map_preflight, dict) and map_preflight.get("nonblank_channels", 0) >= 2, "Source-map preflight is invalid.")
    boundary = manifest["release_boundary"]
    if boundary["power_inventory_status"] == "DECLARED_NONE":
        extracted = "\n".join((page.extract_text() or "") for page in PdfReader(str(pdf_path)).pages)
        require("DECLARED_NONE" in extracted, "Declared absence of power is not visible in the PDF.")
        require("não é insumo pendente nesta execução" in extracted, "Power is still presented as pending.")
    return {
        "status": "PASS",
        "manifest": str(manifest_path),
        "pdf": str(pdf_path),
        "page_count": pdf_check["page_count"],
        "source_status": source_result["status"],
        "release": SOURCE_RELEASE,
        "pce_status": "NOT_EVALUATED",
        "pcx_status": "NOT_EVALUATED",
        "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
        "td_status": "NOT_GENERATED_RECEIVER_MISSING",
        "guidance_status": "NOT_AUTHORIZED",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args(argv)
    try:
        result = verify(args.manifest)
    except (VerificationError, RuntimeError, OSError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
