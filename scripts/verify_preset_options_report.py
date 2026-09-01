#!/usr/bin/env python3
"""Fail-closed verification for the preset-options decision report."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

try:
    from pypdf import PdfReader
except ModuleNotFoundError as exc:  # pragma: no cover - environment contract
    raise SystemExit("pypdf is required to verify PDF output.") from exc


ROOT = Path(__file__).resolve().parents[1]
DERIVED = ROOT / "dataset" / "derived"
DEFAULT_MANIFEST = DERIVED / "preset_options_report_manifest.json"
SCHEMA_VERSION = "1.1.0"
EXPECTED_INPUTS = {
    "system_parameter_options",
    "example_preset_resolution",
    "input_readiness_report",
    "source_product_metrics",
    "audit_metrics",
    "sulcation_scenario_metrics",
    "multifield_connection_screening",
    "continuous_family_manifest",
    "embedded_terrace_screening_manifest",
    "sulcation_scenarios_map",
    "continuous_family_map",
    "embedded_terrace_screening_map",
}
REQUIRED_TOKENS = (
    "NÃO USAR PARA GUIAMENTO",
    "E0_TRIAGEM",
    "CF0_GEOMETRIC_SCREENING",
    "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED",
    "HYDRAULIC_UNCONFIRMED",
    "C1",
    "C2",
    "C3",
    "ESD",
    "POA",
    "NOT_GENERATED",
    "BLOQUEADO",
    "FAIL-CLOSED",
)
PROHIBITED_EXECUTIVE_CLAIMS = (
    "APROVADO PARA GUIAMENTO",
    "LIBERADO PARA GUIAMENTO",
    "APROVADO PARA PLANTIO",
    "PROJETO EXECUTIVO APROVADO",
    "LIBERADO PARA EXECUÇÃO",
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
A4_LANDSCAPE_PT = (841.8898, 595.2756)


class VerificationError(RuntimeError):
    """Raised when the published package violates its release boundary."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"Cannot read manifest: {path}") from exc
    require(isinstance(payload, dict), "Manifest must be a JSON object.")
    return payload


def resolve_path(value: str, manifest_path: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    root_path = (ROOT / path).resolve()
    local_path = (manifest_path.parent / path).resolve()
    return root_path if root_path.exists() or not local_path.exists() else local_path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_record(value: Any, label: str, manifest_path: Path) -> Path:
    require(isinstance(value, dict), f"{label} must be an object.")
    require(set(value) == {"path", "size_bytes", "sha256"}, f"{label} has invalid keys.")
    require(isinstance(value["path"], str) and value["path"], f"{label}.path is invalid.")
    require(isinstance(value["size_bytes"], int) and value["size_bytes"] > 0, f"{label}.size_bytes is invalid.")
    require(isinstance(value["sha256"], str) and SHA256.fullmatch(value["sha256"]) is not None, f"{label}.sha256 is invalid.")
    path = resolve_path(value["path"], manifest_path)
    require(path.is_file(), f"{label} is missing: {path}")
    require(path.stat().st_size == value["size_bytes"], f"{label} size mismatch.")
    require(sha256_file(path) == value["sha256"], f"{label} SHA-256 mismatch.")
    return path


def verify(manifest_path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    manifest = read_json(manifest_path)
    require(manifest.get("schema_version") == SCHEMA_VERSION, "Unexpected report schema version.")
    require(manifest.get("artifact_id") == "terraflux-preset-options-decision-report", "Unexpected artifact id.")
    require(isinstance(manifest.get("inputs"), dict), "Manifest inputs must be an object.")
    require(set(manifest["inputs"]) == EXPECTED_INPUTS, "Manifest inputs must match the fail-closed input set.")
    for name, item in manifest["inputs"].items():
        validate_record(item, f"inputs.{name}", manifest_path)
    pdf_path = validate_record(manifest.get("pdf"), "pdf", manifest_path)
    require(manifest.get("page_count") == 14, "Report must declare exactly 14 pages.")
    pages = manifest.get("pages")
    require(isinstance(pages, list) and len(pages) == 14, "Manifest must enumerate 14 pages.")
    require([item.get("page") for item in pages] == list(range(1, 15)), "Page numbering is invalid.")
    boundary = manifest.get("release_boundary")
    require(isinstance(boundary, dict), "Missing release boundary.")
    require(boundary.get("allowed") == ["E0_TRIAGEM", "CF0_GEOMETRIC_SCREENING", "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED"], "Allowed release boundary changed.")
    require(boundary.get("not_generated_or_blocked") == ["C1_CURVA_EMBUTIDA", "C2_BASE_LARGA_PASSANTE", "C3_ESD", "POA_LOGISTICS", "GUIDANCE_EXPORT"], "Blocked product boundary changed.")
    require(boundary.get("seal") == "NÃO USAR PARA GUIAMENTO | TRIAGEM E0/CF0/C1", "Release seal changed.")
    require(boundary.get("hydraulic_status") == "HYDRAULIC_UNCONFIRMED", "Hydraulic boundary changed.")
    power_status = boundary.get("overhead_power_line_status")
    require(power_status in {"PROVIDED", "DECLARED_NONE", "NOT_REVIEWED"}, "Power inventory status is invalid.")
    require(tuple(manifest.get("required_text_tokens", [])) == REQUIRED_TOKENS, "Required text token contract changed.")
    require(tuple(manifest.get("prohibited_executive_claims", [])) == PROHIBITED_EXECUTIVE_CLAIMS, "Executive claim contract changed.")
    preflight = manifest.get("preflight")
    require(isinstance(preflight, dict) and preflight.get("status") == "PASS", "Manifest preflight is not PASS.")
    require(preflight.get("verified_input_count") == len(EXPECTED_INPUTS), "Preflight input count mismatch.")
    require(preflight.get("pdf_page_count") == 14, "Preflight PDF page count mismatch.")

    reader = PdfReader(str(pdf_path))
    require(len(reader.pages) == 14, "PDF page count does not match manifest.")
    all_text: list[str] = []
    for index, pdf_page in enumerate(reader.pages, start=1):
        width = float(pdf_page.mediabox.width)
        height = float(pdf_page.mediabox.height)
        require(width > height, f"PDF page {index} is not landscape.")
        require(math.isclose(width, A4_LANDSCAPE_PT[0], abs_tol=2.0), f"PDF page {index} width is not A4 landscape.")
        require(math.isclose(height, A4_LANDSCAPE_PT[1], abs_tol=2.0), f"PDF page {index} height is not A4 landscape.")
        text = pdf_page.extract_text() or ""
        require(len(text.strip()) >= 80, f"PDF page {index} is empty or not searchable.")
        require("NÃO USAR PARA GUIAMENTO" in text, f"PDF page {index} lacks the no-guidance seal.")
        contents = pdf_page.get_contents()
        content_data = contents.get_data() if contents is not None else b""
        require(len(content_data) > 200, f"PDF page {index} has an empty content stream.")
        all_text.append(text)
    extracted = "\n".join(all_text)
    for token in REQUIRED_TOKENS:
        require(token in extracted, f"Required text token missing from searchable PDF: {token}")
    upper = extracted.upper()
    for claim in PROHIBITED_EXECUTIVE_CLAIMS:
        require(claim not in upper, f"Prohibited executive claim found in PDF: {claim}")
    require("NOT_GENERATED / BLOQUEADO" in extracted, "Blocked C1/C2/C3/POA condition is not explicit.")
    require("PROCESS_ONLY" in extracted, "ESD process-only boundary is not explicit.")
    if power_status == "DECLARED_NONE":
        require("DECLARED_NONE" in extracted, "Declared absence of overhead power is not visible in the report.")
        require("não é insumo pendente" in extracted, "Power declaration is still presented as a pending input.")
    return {
        "status": "PASS",
        "manifest": str(manifest_path),
        "pdf": str(pdf_path),
        "page_count": len(reader.pages),
        "verified_input_count": len(EXPECTED_INPUTS),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    try:
        result = verify(args.manifest)
    except VerificationError as exc:
        print(f"FAIL: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
