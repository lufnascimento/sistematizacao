#!/usr/bin/env python3
"""Assemble configuration, E0, CF0 and C1 E0 review packages into one dossier.

The source PDFs are copied page-for-page without rasterization.  The dossier is
only a review package: dimensioned C1, C2, C3, POA and guidance remain fail-closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter


ROOT = Path(__file__).resolve().parents[1]
DERIVED = ROOT / "dataset" / "derived"
DEFAULT_OPTIONS_MANIFEST = DERIVED / "preset_options_report_manifest.json"
DEFAULT_E0_MANIFEST = DERIVED / "review_atlas_manifest.json"
DEFAULT_CF0_MANIFEST = DERIVED / "cf0_review_report_manifest.json"
DEFAULT_C1_MANIFEST = DERIVED / "embedded_terrace_screening_report_manifest.json"
DEFAULT_OUTPUT = DERIVED / "Dossie_Completo_Sistematizacao_E0_CF0_C1_Opcoes_2026-08-24.pdf"
DEFAULT_MANIFEST = DERIVED / "complete_project_dossier_manifest.json"

SCHEMA_VERSION = "1.1.0"
MANIFEST_TYPE = "COMPLETE_PROJECT_REVIEW_DOSSIER"
RELEASE = "E0_CF0_C1_CONFIGURATION_REVIEW"
RELEASE_SEAL = "NÃO USAR PARA GUIAMENTO | NÃO É PROJETO EXECUTIVO"
A4_LANDSCAPE_POINTS = (841.68, 595.44)
PAGE_TOLERANCE_POINTS = 2.5
REQUIRED_TEXT = (
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
)
FORBIDDEN_CLAIMS = (
    re.compile(r"\bAPROVADO\s+PARA\s+GUIAMENTO\b", re.IGNORECASE),
    re.compile(r"\bLIBERADO\s+PARA\s+GUIAMENTO\b", re.IGNORECASE),
    re.compile(r"\bPROJETO\s+EXECUTIVO\s+APROVADO\b", re.IGNORECASE),
    re.compile(r"\bGUIDANCE_AUTHORIZED\s*[:=]\s*TRUE\b", re.IGNORECASE),
    re.compile(r"\bHYDRAULIC_APPROVED\b", re.IGNORECASE),
)


class DossierError(RuntimeError):
    """Raised when a source package cannot be published safely."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DossierError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def page_content_sha256(page: Any) -> str:
    contents = page.get_contents()
    return sha256_bytes(b"" if contents is None else contents.get_data())


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def record(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    require(resolved.is_file(), f"Missing file: {resolved}")
    return {
        "path": display_path(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def resolve_path(value: str, manifest_path: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    root_candidate = (ROOT / path).resolve()
    local_candidate = (manifest_path.parent / path).resolve()
    return root_candidate if root_candidate.exists() or not local_candidate.exists() else local_candidate


def load_manifest(path: Path) -> dict[str, Any]:
    require(path.is_file(), f"Source manifest is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DossierError(f"Invalid source manifest: {path}") from exc
    require(isinstance(value, dict), f"Source manifest must be an object: {path}")
    return value


def validate_record(value: Any, manifest_path: Path, label: str) -> Path:
    require(isinstance(value, dict), f"{label} is not a file record.")
    path_value = value.get("path")
    size = value.get("size_bytes")
    digest = value.get("sha256")
    require(isinstance(path_value, str) and path_value, f"{label}.path is invalid.")
    require(isinstance(size, int) and size > 0, f"{label}.size_bytes is invalid.")
    require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None, f"{label}.sha256 is invalid.")
    path = resolve_path(path_value, manifest_path)
    require(path.is_file(), f"{label} is missing: {path}")
    require(path.stat().st_size == size, f"{label} size mismatch.")
    require(sha256_file(path) == digest, f"{label} SHA-256 mismatch.")
    return path


def source_pdf_record(package_id: str, manifest: dict[str, Any]) -> Any:
    if package_id in {"CONFIGURATION_OPTIONS", "C1_E0_REVIEW"}:
        return manifest.get("pdf")
    return (manifest.get("output_integrity") or {}).get("pdf")


def validate_source(package_id: str, manifest_path: Path) -> tuple[dict[str, Any], Path, PdfReader]:
    manifest = load_manifest(manifest_path)
    preflight = manifest.get("preflight") or {}
    require(preflight.get("status") in {"PASS", "PASSED"}, f"{package_id} preflight is not PASS.")

    if package_id == "CONFIGURATION_OPTIONS":
        require(manifest.get("schema_version") == "1.1.0", "Unsupported options report schema.")
        require((manifest.get("release_boundary") or {}).get("hydraulic_status") == "HYDRAULIC_UNCONFIRMED", "Options report lost hydraulic warning.")
    elif package_id == "E0_REVIEW":
        require(manifest.get("status") == "E0_TOPOGRAPHIC_AND_GEOMETRIC_REVIEW", "Unexpected E0 release.")
    elif package_id == "CF0_REVIEW":
        require(manifest.get("manifest_type") == "CF0_REVIEW_REPORT", "Unexpected CF0 report type.")
        require(manifest.get("hydraulic_status") == "HYDRAULIC_UNCONFIRMED", "CF0 hydraulic state is unsafe.")
        require(manifest.get("guidance_authorized") is False, "CF0 guidance must remain unauthorized.")
    elif package_id == "C1_E0_REVIEW":
        boundary = manifest.get("release_boundary") or {}
        require(manifest.get("schema_version") == "1.0.0", "Unsupported C1 E0 report schema.")
        require(
            manifest.get("artifact_id") == "terraflux-c1-e0-embedded-terrace-screening-report",
            "Unexpected C1 E0 report type.",
        )
        require(
            boundary.get("release") == "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED",
            "Unexpected C1 E0 release.",
        )
        require(boundary.get("hydraulic_status") == "HYDRAULIC_UNCONFIRMED", "C1 E0 hydraulic state is unsafe.")
        require(boundary.get("guidance_status") == "NOT_AUTHORIZED", "C1 E0 guidance must remain unauthorized.")
        require(boundary.get("td_status") == "NOT_GENERATED_RECEIVER_MISSING", "C1 E0 TD state is unsafe.")
    else:  # pragma: no cover - internal contract
        raise DossierError(f"Unknown package: {package_id}")

    pdf_path = validate_record(source_pdf_record(package_id, manifest), manifest_path, f"{package_id}.pdf")
    reader = PdfReader(str(pdf_path))
    declared_count = manifest.get("page_count")
    require(isinstance(declared_count, int) and declared_count > 0, f"{package_id}.page_count is invalid.")
    require(len(reader.pages) == declared_count, f"{package_id} PDF page count mismatch.")
    return manifest, pdf_path, reader


def validate_page_shape(page: Any, label: str) -> None:
    width = float(page.mediabox.width)
    height = float(page.mediabox.height)
    require(width > height, f"{label} is not landscape.")
    require(abs(width - A4_LANDSCAPE_POINTS[0]) <= PAGE_TOLERANCE_POINTS, f"{label} width is not A4.")
    require(abs(height - A4_LANDSCAPE_POINTS[1]) <= PAGE_TOLERANCE_POINTS, f"{label} height is not A4.")
    require(len((page.extract_text() or "").strip()) >= 20, f"{label} is not searchable or is empty.")


def build_dossier(
    options_manifest_path: Path,
    e0_manifest_path: Path,
    cf0_manifest_path: Path,
    c1_manifest_path: Path,
    output_path: Path,
    output_manifest_path: Path,
) -> dict[str, Any]:
    definitions = (
        ("CONFIGURATION_OPTIONS", options_manifest_path),
        ("E0_REVIEW", e0_manifest_path),
        ("CF0_REVIEW", cf0_manifest_path),
        ("C1_E0_REVIEW", c1_manifest_path),
    )
    packages: list[dict[str, Any]] = []
    writer = PdfWriter()
    next_page = 1

    for package_id, manifest_path in definitions:
        _, pdf_path, reader = validate_source(package_id, manifest_path)
        source_hashes: list[str] = []
        for index, page in enumerate(reader.pages, start=1):
            validate_page_shape(page, f"{package_id} page {index}")
            source_hashes.append(page_content_sha256(page))
            writer.add_page(page)
        page_count = len(reader.pages)
        packages.append(
            {
                "package_id": package_id,
                "manifest": record(manifest_path),
                "pdf": record(pdf_path),
                "source_page_count": page_count,
                "dossier_page_start": next_page,
                "dossier_page_end": next_page + page_count - 1,
                "page_content_sha256": source_hashes,
            }
        )
        next_page += page_count

    writer.add_metadata(
        {
            "/Title": "Dossiê completo de sistematização | Opções + E0 + CF0 + C1 E0",
            "/Subject": RELEASE_SEAL,
            "/Author": "TerraFlux | pipeline técnico",
            "/Keywords": "sistematização, cana, sulcação, conservação, CF0, ESD, POA",
        }
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix="complete-dossier-", suffix=".pdf", dir=output_path.parent, delete=False) as stream:
        staged_path = Path(stream.name)
    try:
        with staged_path.open("wb") as stream:
            writer.write(stream)
        staged_reader = PdfReader(str(staged_path))
        require(len(staged_reader.pages) == next_page - 1, "Assembled dossier page count mismatch.")
        extracted = "\n".join(page.extract_text() or "" for page in staged_reader.pages)
        for token in REQUIRED_TEXT:
            require(token in extracted, f"Required dossier text is missing: {token}")
        for pattern in FORBIDDEN_CLAIMS:
            require(pattern.search(extracted) is None, f"Unsafe executive claim found: {pattern.pattern}")
        staged_path.replace(output_path)
    finally:
        if staged_path.exists():
            staged_path.unlink()

    result = {
        "schema_version": SCHEMA_VERSION,
        "manifest_type": MANIFEST_TYPE,
        "release": RELEASE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "release_boundary": {
            "seal": RELEASE_SEAL,
            "maximum_delivery_level": "E0_TRIAGEM",
            "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
            "guidance_authorized": False,
            "generated_products": ["CONFIGURATION_OPTIONS", "E0_REVIEW", "CF0_REVIEW", "C1_E0_REVIEW"],
            "not_generated_or_blocked": [
                "C1_CURVA_EMBUTIDA",
                "C2_BASE_LARGA_PASSANTE",
                "C3_ESD",
                "POA_LOGISTICS",
                "HYDRAULIC_DESIGN",
                "GUIDANCE_EXPORT",
            ],
        },
        "source_packages": packages,
        "output": record(output_path),
        "page_count": next_page - 1,
        "required_text_tokens": list(REQUIRED_TEXT),
        "preflight": {
            "status": "PASS",
            "source_package_count": len(packages),
            "all_source_pages_preserved": True,
            "all_pages_a4_landscape": True,
            "all_pages_searchable": True,
            "forbidden_claim_count": 0,
        },
    }
    output_manifest_path.parent.mkdir(parents=True, exist_ok=True)
    staged_manifest = output_manifest_path.with_name(f".{output_manifest_path.stem}.staging.json")
    staged_manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    staged_manifest.replace(output_manifest_path)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--options-manifest", type=Path, default=DEFAULT_OPTIONS_MANIFEST)
    parser.add_argument("--e0-manifest", type=Path, default=DEFAULT_E0_MANIFEST)
    parser.add_argument("--cf0-manifest", type=Path, default=DEFAULT_CF0_MANIFEST)
    parser.add_argument("--c1-manifest", type=Path, default=DEFAULT_C1_MANIFEST)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = build_dossier(
        args.options_manifest.resolve(),
        args.e0_manifest.resolve(),
        args.cf0_manifest.resolve(),
        args.c1_manifest.resolve(),
        args.output.resolve(),
        args.manifest.resolve(),
    )
    print(
        json.dumps(
            {
                "status": "PASS",
                "output": result["output"]["path"],
                "manifest": display_path(args.manifest),
                "page_count": result["page_count"],
                "source_package_count": len(result["source_packages"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
