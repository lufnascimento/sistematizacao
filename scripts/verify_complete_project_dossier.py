#!/usr/bin/env python3
"""Verify the complete configuration + E0 + CF0 review dossier."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from assemble_complete_project_dossier import (
    A4_LANDSCAPE_POINTS,
    FORBIDDEN_CLAIMS,
    MANIFEST_TYPE,
    PAGE_TOLERANCE_POINTS,
    RELEASE,
    REQUIRED_TEXT,
    ROOT,
    DERIVED,
    page_content_sha256,
    resolve_path,
    sha256_file,
)


DEFAULT_MANIFEST = DERIVED / "complete_project_dossier_manifest.json"


class VerificationError(RuntimeError):
    """Raised when dossier integrity or release boundaries are invalid."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def load_json(path: Path) -> dict[str, Any]:
    require(path.is_file(), f"Manifest is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(f"Invalid manifest JSON: {path}") from exc
    require(isinstance(value, dict), "Manifest root must be an object.")
    return value


def validate_record(value: Any, manifest_path: Path, label: str) -> Path:
    require(isinstance(value, dict), f"{label} is not a record.")
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


def verify(manifest_path: Path) -> dict[str, Any]:
    manifest = load_json(manifest_path)
    require(manifest.get("schema_version") == "1.1.0", "Unsupported dossier schema.")
    require(manifest.get("manifest_type") == MANIFEST_TYPE, "Unexpected dossier type.")
    require(manifest.get("release") == RELEASE, "Unexpected dossier release.")
    boundary = manifest.get("release_boundary") or {}
    require(boundary.get("maximum_delivery_level") == "E0_TRIAGEM", "Dossier improperly promotes delivery level.")
    require(boundary.get("hydraulic_status") == "HYDRAULIC_UNCONFIRMED", "Hydraulic warning is missing.")
    require(boundary.get("guidance_authorized") is False, "Guidance must remain unauthorized.")
    blocked = set(boundary.get("not_generated_or_blocked") or [])
    for product in ("C1_CURVA_EMBUTIDA", "C2_BASE_LARGA_PASSANTE", "C3_ESD", "POA_LOGISTICS", "GUIDANCE_EXPORT"):
        require(product in blocked, f"Missing fail-closed product status: {product}")

    package_values = manifest.get("source_packages")
    require(isinstance(package_values, list) and len(package_values) == 4, "Exactly four source packages are required.")
    expected_ids = ["CONFIGURATION_OPTIONS", "E0_REVIEW", "CF0_REVIEW", "C1_E0_REVIEW"]
    next_page = 1
    package_readers: list[tuple[dict[str, Any], PdfReader]] = []
    for expected_id, package in zip(expected_ids, package_values):
        require(isinstance(package, dict), f"Source package {expected_id} is invalid.")
        require(package.get("package_id") == expected_id, f"Unexpected package order for {expected_id}.")
        validate_record(package.get("manifest"), manifest_path, f"{expected_id}.manifest")
        source_pdf = validate_record(package.get("pdf"), manifest_path, f"{expected_id}.pdf")
        reader = PdfReader(str(source_pdf))
        count = package.get("source_page_count")
        require(isinstance(count, int) and count == len(reader.pages) and count > 0, f"{expected_id} page count mismatch.")
        require(package.get("dossier_page_start") == next_page, f"{expected_id} start page is not contiguous.")
        require(package.get("dossier_page_end") == next_page + count - 1, f"{expected_id} end page is invalid.")
        fingerprints = package.get("page_content_sha256")
        require(isinstance(fingerprints, list) and len(fingerprints) == count, f"{expected_id} fingerprints are incomplete.")
        actual_source_hashes = [page_content_sha256(page) for page in reader.pages]
        require(fingerprints == actual_source_hashes, f"{expected_id} source page content changed.")
        package_readers.append((package, reader))
        next_page += count

    output_path = validate_record(manifest.get("output"), manifest_path, "output")
    output_reader = PdfReader(str(output_path))
    declared_count = manifest.get("page_count")
    require(isinstance(declared_count, int) and declared_count == next_page - 1, "Declared dossier page count is invalid.")
    require(len(output_reader.pages) == declared_count, "Output PDF page count mismatch.")

    text_parts: list[str] = []
    for index, page in enumerate(output_reader.pages, start=1):
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        require(width > height, f"Output page {index} is not landscape.")
        require(abs(width - A4_LANDSCAPE_POINTS[0]) <= PAGE_TOLERANCE_POINTS, f"Output page {index} width is not A4.")
        require(abs(height - A4_LANDSCAPE_POINTS[1]) <= PAGE_TOLERANCE_POINTS, f"Output page {index} height is not A4.")
        text = (page.extract_text() or "").strip()
        require(len(text) >= 20, f"Output page {index} is blank or not searchable.")
        text_parts.append(text)

    for package, _ in package_readers:
        start = package["dossier_page_start"] - 1
        end = package["dossier_page_end"]
        output_hashes = [page_content_sha256(page) for page in output_reader.pages[start:end]]
        require(output_hashes == package["page_content_sha256"], f"Dossier changed pages from {package['package_id']}.")

    extracted = "\n".join(text_parts)
    for token in REQUIRED_TEXT:
        require(token in extracted, f"Required dossier text is missing: {token}")
    for pattern in FORBIDDEN_CLAIMS:
        require(pattern.search(extracted) is None, f"Unsafe executive claim found: {pattern.pattern}")
    require((manifest.get("preflight") or {}).get("status") == "PASS", "Manifest preflight is not PASS.")

    return {
        "status": "PASS",
        "manifest": str(manifest_path.resolve()),
        "pdf": str(output_path),
        "page_count": declared_count,
        "source_package_count": len(package_values),
        "source_page_preservation": "VERIFIED",
        "maximum_delivery_level": "E0_TRIAGEM",
        "guidance_authorized": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = verify(args.manifest.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
