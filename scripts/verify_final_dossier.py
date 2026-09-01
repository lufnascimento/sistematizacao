#!/usr/bin/env python3
"""Independently verify the consolidated E0 + CF0 review dossier."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Sequence

import fitz
from PIL import Image, ImageStat

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
    else:
        raise ModuleNotFoundError("pypdf is required to verify the final dossier.")


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "dataset" / "derived" / "final_dossier_manifest.json"

SCHEMA_VERSION = "1.0.0"
MANIFEST_TYPE = "FINAL_REVIEW_DOSSIER"
RELEASE = "E0_CF0_REVIEW_DOSSIER"
DOSSIER_SEAL = "DOSSIÊ DE REVISÃO | NÃO USAR PARA GUIAMENTO"
GUIDANCE_WARNING = "NÃO USAR PARA GUIAMENTO"
EXECUTIVE_WARNING = "NÃO É PROJETO EXECUTIVO"
HYDRAULIC_STATUS = "HYDRAULIC_UNCONFIRMED"
CF0_SEAL = "CF0 PROTÓTIPO GEOMÉTRICO"
E0_RELEASE = "E0_TOPOGRAPHIC_AND_GEOMETRIC_REVIEW"
CF0_RELEASE = "CF0_GEOMETRIC_SCREENING_REVIEW"
EXPECTED_E0_PAGE_COUNT = 26
EXPECTED_CF0_SOURCE_LAYERS = (
    "continuous_rows",
    "diagnostic_rows",
    "family_summary",
    "hydraulic_precheck",
)
ROADMAP = {
    "C1_CURVA_EMBUTIDA": "NOT_GENERATED",
    "C2_BASE_LARGA_PASSANTE": "NOT_GENERATED",
    "C3_ESD": "NOT_GENERATED",
}
ROADMAP_LINES = tuple(f"{key} = {value}" for key, value in ROADMAP.items())
GENERATED_TITLES = (
    "Dossiê técnico consolidado",
    "Sumário e mapa de leitura",
    "Maturidade atual do produto",
    "Parte I | E0",
    "Parte II | CF0",
)
MATURITY_TOKENS = (
    "24 RECORTES",
    "6 IMPLEMENTADO",
    "13 ESPECIFICADO",
    "5 BLOQUEADO",
    "docs/MATRIZ_AGRONOMICA_CONSERVACAO_COLHEITABILIDADE.md",
    "docs/ROADMAP_FECHAMENTO_ADERENCIA_AGRONOMICA.md",
)
A4_LANDSCAPE_POINTS = (841.68, 595.44)
PAGE_SIZE_TOLERANCE_POINTS = 2.5
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_EXECUTIVE_PATTERNS = (
    re.compile(r"\bLIBERADO\s+PARA\s+GUIAMENTO\b", re.IGNORECASE),
    re.compile(r"\bPROJETO\s+EXECUTIVO\s+APROVADO\b", re.IGNORECASE),
    re.compile(r"\bHYDRAULIC_APPROVED\b", re.IGNORECASE),
    re.compile(r"\bGUIDANCE_AUTHORIZED\s*[:=]\s*TRUE\b", re.IGNORECASE),
    re.compile(r"\bAPROVAÇÃO\s+HIDRÁULICA\s+CONCLUÍDA\b", re.IGNORECASE),
    re.compile(
        r"\b(?:C1_CURVA_EMBUTIDA|C2_BASE_LARGA_PASSANTE|C3_ESD)\s*[:=]\s*(?:GENERATED|GERADO)\b",
        re.IGNORECASE,
    ),
)


class DossierVerificationError(RuntimeError):
    """Raised when dossier integrity or release language is invalid."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DossierVerificationError(message)


def require_object(value: Any, label: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"{label} must be an object.")
    return value


def require_array(value: Any, label: str, *, nonempty: bool = False) -> list[Any]:
    require(isinstance(value, list), f"{label} must be an array.")
    if nonempty:
        require(bool(value), f"{label} must not be empty.")
    return value


def require_keys(
    node: dict[str, Any],
    label: str,
    required: Iterable[str],
    optional: Iterable[str] = (),
) -> None:
    required_set = set(required)
    allowed = required_set | set(optional)
    require(required_set <= set(node), f"{label} missing keys: {sorted(required_set - set(node))}")
    require(set(node) <= allowed, f"{label} has unexpected keys: {sorted(set(node) - allowed)}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def normalized_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value).replace("\u00ad", "")
    return re.sub(r"\s+", " ", value).strip()


def text_sha256(value: str) -> str:
    return sha256_bytes(normalized_text(value).encode("utf-8"))


def page_content_bytes(page: Any) -> bytes:
    contents = page.get_contents()
    return b"" if contents is None else contents.get_data()


def resolve_path(value: str, manifest_path: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    root_candidate = (ROOT / path).resolve()
    local_candidate = (manifest_path.parent / path).resolve()
    return root_candidate if root_candidate.exists() or not local_candidate.exists() else local_candidate


def integrity_records(node: Any, trail: str = "") -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(node, dict):
        if isinstance(node.get("path"), str) and isinstance(node.get("sha256"), str):
            yield trail or "root", node
            return
        for key, value in node.items():
            child = f"{trail}.{key}" if trail else str(key)
            yield from integrity_records(value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            child = f"{trail}[{index}]"
            yield from integrity_records(value, child)


def validate_file_record(record_value: Any, label: str, manifest_path: Path) -> Path:
    record = require_object(record_value, label)
    require_keys(record, label, {"path", "size_bytes", "sha256"}, {"page"})
    require(isinstance(record["path"], str) and record["path"], f"{label}.path is invalid.")
    require(isinstance(record["size_bytes"], int) and not isinstance(record["size_bytes"], bool) and record["size_bytes"] > 0, f"{label}.size_bytes is invalid.")
    require(isinstance(record["sha256"], str) and SHA256_PATTERN.fullmatch(record["sha256"]) is not None, f"{label}.sha256 is invalid.")
    path = resolve_path(record["path"], manifest_path)
    require(path.is_file(), f"{label} does not exist: {path}")
    require(path.stat().st_size == record["size_bytes"], f"{label} size mismatch.")
    require(sha256_file(path) == record["sha256"], f"{label} SHA-256 mismatch.")
    return path


def validate_integrity_tree(node: Any, label: str, manifest_path: Path) -> list[tuple[str, Path]]:
    records = list(integrity_records(node))
    require(bool(records), f"{label} contains no integrity records.")
    return [
        (trail, validate_file_record(record, f"{label}.{trail}", manifest_path))
        for trail, record in records
    ]


def validate_page_descriptors(
    pages_value: Any,
    count_value: Any,
    label: str,
    *,
    exact_count: int | None = None,
) -> list[dict[str, Any]]:
    pages = require_array(pages_value, f"{label}.pages", nonempty=True)
    require(isinstance(count_value, int) and not isinstance(count_value, bool), f"{label}.page_count is invalid.")
    require(count_value == len(pages), f"{label}.page_count disagrees with pages.")
    if exact_count is not None:
        require(count_value == exact_count, f"{label} must contain exactly {exact_count} pages.")
    slugs: list[str] = []
    for index, page_value in enumerate(pages, start=1):
        page = require_object(page_value, f"{label}.pages[{index - 1}]")
        require(page.get("number") == index, f"{label} page numbers must be contiguous.")
        require(isinstance(page.get("title"), str) and page["title"].strip(), f"{label} page {index} title is empty.")
        require(isinstance(page.get("slug"), str) and page["slug"].strip(), f"{label} page {index} slug is empty.")
        slugs.append(page["slug"])
    require(len(slugs) == len(set(slugs)), f"{label} page slugs are duplicated.")
    return pages


def declared_pdf_record(output_integrity: Any, label: str) -> dict[str, Any]:
    pdf_records = [
        record
        for _, record in integrity_records(output_integrity)
        if str(record.get("path", "")).casefold().endswith(".pdf")
    ]
    require(len(pdf_records) == 1, f"{label} must declare exactly one PDF.")
    return pdf_records[0]


def find_forbidden_claims(text: str) -> list[str]:
    normalized = normalized_text(text)
    return [pattern.pattern for pattern in FORBIDDEN_EXECUTIVE_PATTERNS if pattern.search(normalized)]


def validate_e0_source(manifest_path: Path) -> dict[str, Any]:
    manifest = require_object(json.loads(manifest_path.read_text(encoding="utf-8")), "E0 source manifest")
    require(manifest.get("status") == E0_RELEASE, "Unexpected E0 source release.")
    require(manifest.get("legacy_pdfs_read") is False, "E0 source cannot depend on legacy PDFs.")
    require(GUIDANCE_WARNING in normalized_text(str(manifest.get("release_warning", ""))), "E0 source lacks guidance warning.")
    pages = validate_page_descriptors(manifest.get("pages"), manifest.get("page_count"), "E0 source manifest", exact_count=EXPECTED_E0_PAGE_COUNT)
    preflight = require_object(manifest.get("preflight"), "E0 source preflight")
    require(preflight.get("status") == "PASS", "E0 source preflight did not pass.")
    require(preflight.get("pdf_page_count") == len(pages), "E0 source preflight page count disagrees.")
    require(preflight.get("all_pages_a4_landscape") is True, "E0 source preflight lacks A4 approval.")
    require(preflight.get("all_page_assets_nonblank") is True, "E0 source preflight has blank assets.")
    validate_integrity_tree(manifest.get("source_integrity"), "E0 source_integrity", manifest_path)
    validate_integrity_tree(manifest.get("output_integrity"), "E0 output_integrity", manifest_path)
    record = declared_pdf_record(manifest.get("output_integrity"), "E0 output_integrity")
    pdf_path = validate_file_record(record, "E0 output PDF", manifest_path)
    return {"release": E0_RELEASE, "pages": pages, "pdf_record": record, "pdf_path": pdf_path}


def validate_cf0_source(manifest_path: Path) -> dict[str, Any]:
    manifest = require_object(json.loads(manifest_path.read_text(encoding="utf-8")), "CF0 source manifest")
    require(manifest.get("schema_version") == "1.2.0", "CF0 source is not contract 1.2.")
    require(manifest.get("manifest_type") == "CF0_REVIEW_REPORT", "Unexpected CF0 source manifest type.")
    require(manifest.get("release") == CF0_RELEASE, "Unexpected CF0 source release.")
    require(manifest.get("release_seal") == f"{CF0_SEAL} | {GUIDANCE_WARNING}", "CF0 source seal differs.")
    require(manifest.get("hydraulic_status") == HYDRAULIC_STATUS, "CF0 source cannot approve hydraulics.")
    require(manifest.get("guidance_authorized") is False, "CF0 source cannot authorize guidance.")
    require(manifest.get("legacy_pdfs_read") is False, "CF0 source cannot depend on legacy PDFs.")
    require(manifest.get("source_layers") == list(EXPECTED_CF0_SOURCE_LAYERS), "CF0 source does not declare four contractual layers.")
    require(manifest.get("roadmap") == ROADMAP, "CF0 source must keep C1/C2/C3 NOT_GENERATED.")
    diagnostics = require_object(manifest.get("diagnostic_rows"), "CF0 source diagnostic_rows")
    require(diagnostics.get("layer_required") is True, "CF0 diagnostic_rows must be mandatory.")
    require(diagnostics.get("status") == "PRESENT_NOT_APPROVED", "CF0 diagnostic_rows cannot be absent or approved.")
    require(diagnostics.get("review_status") == "NOT_APPROVED", "CF0 diagnostic rows must remain NOT_APPROVED.")
    require(diagnostics.get("guidance_status") == "NOT_AUTHORIZED", "CF0 diagnostic rows must remain NOT_AUTHORIZED.")
    require(diagnostics.get("release_authorized") is False, "CF0 diagnostic rows cannot authorize release.")
    pages = validate_page_descriptors(manifest.get("pages"), manifest.get("page_count"), "CF0 source manifest")
    preflight = require_object(manifest.get("preflight"), "CF0 source preflight")
    require(preflight.get("status") == "PASS", "CF0 source preflight did not pass.")
    require(preflight.get("pdf_page_count") == len(pages), "CF0 source preflight page count disagrees.")
    require(preflight.get("all_pages_a4_landscape") is True, "CF0 source preflight lacks A4 approval.")
    require(preflight.get("every_page_has_release_seal") is True, "CF0 source preflight lacks seals.")
    require(preflight.get("every_page_has_hydraulic_warning") is True, "CF0 source preflight lacks hydraulic warnings.")
    validate_integrity_tree(manifest.get("source_package"), "CF0 source_package", manifest_path)
    validate_integrity_tree(manifest.get("output_integrity"), "CF0 output_integrity", manifest_path)
    record = declared_pdf_record(manifest.get("output_integrity"), "CF0 output_integrity")
    pdf_path = validate_file_record(record, "CF0 output PDF", manifest_path)
    return {"release": CF0_RELEASE, "pages": pages, "pdf_record": record, "pdf_path": pdf_path}


def validate_manifest_shape(manifest_value: Any) -> dict[str, Any]:
    manifest = require_object(manifest_value, "dossier manifest")
    require_keys(
        manifest,
        "dossier manifest",
        {
            "schema_version",
            "manifest_type",
            "release",
            "generated_at",
            "release_seal",
            "hydraulic_status",
            "guidance_authorized",
            "executive_project",
            "concatenation",
            "roadmap",
            "source_packages",
            "generated_pages",
            "page_count",
            "sections",
            "page_map",
            "output_integrity",
            "preflight",
            "publication_order",
            "generator",
            "verifier",
        },
    )
    require(manifest["schema_version"] == SCHEMA_VERSION, "Unsupported dossier schema_version.")
    require(manifest["manifest_type"] == MANIFEST_TYPE, "Unexpected dossier manifest_type.")
    require(manifest["release"] == RELEASE, "Unexpected dossier release.")
    require(isinstance(manifest["generated_at"], str) and manifest["generated_at"], "generated_at is invalid.")
    require(manifest["release_seal"] == DOSSIER_SEAL, "Dossier release seal differs.")
    require(manifest["hydraulic_status"] == HYDRAULIC_STATUS, "Dossier cannot approve hydraulics.")
    require(manifest["guidance_authorized"] is False, "Dossier cannot authorize guidance.")
    require(manifest["executive_project"] is False, "Dossier cannot claim executive status.")
    require(
        manifest["concatenation"]
        == {
            "library": "pypdf",
            "method": "PYPDF_PAGE_OBJECT_APPEND_NO_RASTERIZATION",
            "source_page_text_and_content_hashes_required": True,
        },
        "Dossier concatenation contract differs.",
    )
    require(manifest["roadmap"] == ROADMAP, "Dossier must keep C1/C2/C3 NOT_GENERATED.")
    require(
        manifest["publication_order"]
        == [
            "SOURCE_MANIFESTS_HASHES_AND_PDFS_VALIDATED",
            "PDF_ATOMIC_REPLACE",
            "MANIFEST_ATOMIC_REPLACE_LAST",
        ],
        "Dossier publication order does not publish manifest last.",
    )
    require(isinstance(manifest["page_count"], int) and manifest["page_count"] > 0, "Dossier page_count is invalid.")
    return manifest


def records_equivalent(left: dict[str, Any], right: dict[str, Any], left_manifest: Path, right_manifest: Path) -> bool:
    return (
        resolve_path(left["path"], left_manifest) == resolve_path(right["path"], right_manifest)
        and left["size_bytes"] == right["size_bytes"]
        and left["sha256"] == right["sha256"]
    )


def validate_source_packages(manifest: dict[str, Any], dossier_manifest_path: Path) -> dict[str, dict[str, Any]]:
    packages = require_object(manifest["source_packages"], "source_packages")
    require(set(packages) == {"E0", "CF0"}, "source_packages must contain exactly E0 and CF0.")
    validated: dict[str, dict[str, Any]] = {}
    for section_id, validator, expected_release in (
        ("E0", validate_e0_source, E0_RELEASE),
        ("CF0", validate_cf0_source, CF0_RELEASE),
    ):
        package = require_object(packages[section_id], f"source_packages.{section_id}")
        require_keys(package, f"source_packages.{section_id}", {"manifest", "pdf", "release", "page_count", "preflight"})
        require(package["release"] == expected_release, f"{section_id} packaged release differs.")
        manifest_path = validate_file_record(package["manifest"], f"source_packages.{section_id}.manifest", dossier_manifest_path)
        source = validator(manifest_path)
        validate_file_record(package["pdf"], f"source_packages.{section_id}.pdf", dossier_manifest_path)
        require(records_equivalent(package["pdf"], source["pdf_record"], dossier_manifest_path, manifest_path), f"{section_id} packaged PDF record differs from its source manifest.")
        require(package["page_count"] == len(source["pages"]), f"{section_id} packaged page count differs.")
        package_preflight = require_object(package["preflight"], f"source_packages.{section_id}.preflight")
        require_keys(package_preflight, f"source_packages.{section_id}.preflight", {"status", "all_pages_a4_landscape", "every_page_searchable", "integrity_record_count"})
        require(package_preflight["status"] == "PASS", f"{section_id} packaged preflight did not pass.")
        require(package_preflight["all_pages_a4_landscape"] is True, f"{section_id} packaged preflight lacks A4 approval.")
        require(package_preflight["every_page_searchable"] is True, f"{section_id} packaged preflight lacks searchable pages.")
        require(isinstance(package_preflight["integrity_record_count"], int) and package_preflight["integrity_record_count"] > 0, f"{section_id} packaged integrity count is invalid.")
        validated[section_id] = {**source, "manifest_path": manifest_path}
    return validated


def validate_page_map(
    manifest: dict[str, Any],
    sources: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    page_map = require_array(manifest["page_map"], "page_map", nonempty=True)
    e0_count = len(sources["E0"]["pages"])
    cf0_count = len(sources["CF0"]["pages"])
    expected_count = 5 + e0_count + cf0_count
    require(manifest["page_count"] == expected_count, "Dossier page_count disagrees with source and generated pages.")
    require(len(page_map) == expected_count, "page_map length disagrees with dossier page_count.")
    cf0_divider_page = 4 + e0_count + 1
    for index, item_value in enumerate(page_map, start=1):
        item = require_object(item_value, f"page_map[{index - 1}]")
        require(item.get("final_page") == index, "page_map final_page values must be contiguous.")
        require(item.get("kind") in {"GENERATED", "SOURCE_PAGE"}, f"Invalid page_map kind at page {index}.")
        require(isinstance(item.get("title"), str) and item["title"], f"page_map page {index} has no title.")
        if item["kind"] == "GENERATED":
            require_keys(item, f"page_map[{index - 1}]", {"final_page", "section_id", "kind", "title", "source_page"})
            require(item["source_page"] is None, f"Generated page {index} cannot declare source_page.")
        else:
            require_keys(
                item,
                f"page_map[{index - 1}]",
                {
                    "final_page",
                    "section_id",
                    "kind",
                    "title",
                    "source_page",
                    "source_page_text_sha256",
                    "source_page_content_sha256",
                },
            )
            require(item["section_id"] in {"E0", "CF0"}, f"Source page {index} has invalid section.")
            require(isinstance(item["source_page"], int), f"Source page {index} has invalid source_page.")
            require(SHA256_PATTERN.fullmatch(str(item["source_page_text_sha256"])) is not None, f"Source page {index} has invalid text hash.")
            require(SHA256_PATTERN.fullmatch(str(item["source_page_content_sha256"])) is not None, f"Source page {index} has invalid content hash.")

    expected_generated = {
        1: ("FRONTMATTER", GENERATED_TITLES[0]),
        2: ("FRONTMATTER", GENERATED_TITLES[1]),
        3: ("FRONTMATTER", GENERATED_TITLES[2]),
        4: ("E0_DIVIDER", GENERATED_TITLES[3]),
        cf0_divider_page: ("CF0_DIVIDER", GENERATED_TITLES[4]),
    }
    for page_number, (section_id, title) in expected_generated.items():
        item = page_map[page_number - 1]
        require(item["kind"] == "GENERATED" and item["section_id"] == section_id and item["title"] == title, f"Generated page {page_number} differs from contract.")
    for source_index, descriptor in enumerate(sources["E0"]["pages"], start=1):
        item = page_map[4 + source_index - 1]
        require(item["section_id"] == "E0" and item["source_page"] == source_index, "E0 page map is out of order.")
        require(item["title"] == descriptor["title"], "E0 page title differs from source manifest.")
    cf0_start = cf0_divider_page + 1
    for source_index, descriptor in enumerate(sources["CF0"]["pages"], start=1):
        item = page_map[cf0_start + source_index - 2]
        require(item["section_id"] == "CF0" and item["source_page"] == source_index, "CF0 page map is out of order.")
        require(item["title"] == descriptor["title"], "CF0 page title differs from source manifest.")

    generated_pages = require_array(manifest["generated_pages"], "generated_pages", nonempty=True)
    expected_generated_records = [
        {"final_page": item["final_page"], "title": item["title"], "section_id": item["section_id"]}
        for item in page_map
        if item["kind"] == "GENERATED"
    ]
    require(generated_pages == expected_generated_records, "generated_pages disagrees with page_map.")
    sections = require_array(manifest["sections"], "sections", nonempty=True)
    expected_sections = [
        {"id": "FRONTMATTER", "title": "Capa, sumário e maturidade", "start_page": 1, "end_page": 3, "page_count": 3, "origin": "GENERATED_VECTOR_PDF"},
        {"id": "E0", "title": "Parte I | E0", "start_page": 4, "end_page": 4 + e0_count, "page_count": 1 + e0_count, "origin": "DIVIDER_PLUS_SOURCE_PDF"},
        {"id": "CF0", "title": "Parte II | CF0", "start_page": cf0_divider_page, "end_page": expected_count, "page_count": 1 + cf0_count, "origin": "DIVIDER_PLUS_SOURCE_PDF"},
    ]
    require(sections == expected_sections, "Dossier sections disagree with page map.")
    return page_map


def raster_nonblank(document: fitz.Document, page_index: int) -> dict[str, Any]:
    page = document.load_page(page_index)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(0.75, 0.75), colorspace=fitz.csGRAY, alpha=False)
    image = Image.frombytes("L", (pixmap.width, pixmap.height), pixmap.samples)
    statistics = ImageStat.Stat(image)
    stddev = float(statistics.stddev[0])
    histogram = image.histogram()
    pixel_count = max(1, image.width * image.height)
    dark_fraction = sum(histogram[:245]) / pixel_count
    extrema = image.getextrema()
    nonblank = stddev >= 1.0 and dark_fraction >= 0.0003 and (extrema[1] - extrema[0]) >= 8
    return {
        "width_px": image.width,
        "height_px": image.height,
        "stddev": round(stddev, 4),
        "dark_pixel_fraction": round(dark_fraction, 6),
        "dynamic_range": int(extrema[1] - extrema[0]),
        "nonblank": nonblank,
    }


def inspect_pdf(
    pdf_path: Path,
    page_map: list[dict[str, Any]],
    sources: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    reader = PdfReader(str(pdf_path))
    require(not reader.is_encrypted, "Dossier PDF must not be encrypted.")
    require(len(reader.pages) == len(page_map), "Dossier PDF page count disagrees with page map.")
    source_readers = {
        section_id: PdfReader(str(source["pdf_path"]))
        for section_id, source in sources.items()
    }
    rendered = fitz.open(pdf_path)
    media_boxes: list[dict[str, Any]] = []
    searchable_counts: list[int] = []
    nonblank_checks: list[dict[str, Any]] = []
    source_preserved = 0
    combined_text: list[str] = []
    try:
        require(rendered.page_count == len(reader.pages), "Rendered dossier page count disagrees with pypdf.")
        for index, (page, mapping) in enumerate(zip(reader.pages, page_map), start=1):
            width = float(page.mediabox.width)
            height = float(page.mediabox.height)
            a4 = (
                width > height
                and abs(width - A4_LANDSCAPE_POINTS[0]) <= PAGE_SIZE_TOLERANCE_POINTS
                and abs(height - A4_LANDSCAPE_POINTS[1]) <= PAGE_SIZE_TOLERANCE_POINTS
            )
            require(a4, f"Dossier PDF page {index} is not A4 landscape.")
            text = page.extract_text() or ""
            normalized = normalized_text(text)
            searchable_count = sum(not character.isspace() for character in text)
            require(searchable_count >= 20, f"Dossier PDF page {index} is not sufficiently searchable.")
            require(GUIDANCE_WARNING in normalized, f"Dossier PDF page {index} lacks guidance warning.")
            nonblank = raster_nonblank(rendered, index - 1)
            require(nonblank["nonblank"], f"Dossier PDF page {index} appears blank.")
            nonblank["page"] = index
            nonblank_checks.append(nonblank)
            if mapping["kind"] == "GENERATED":
                require(mapping["title"] in normalized, f"Generated page {index} lacks its searchable title.")
                require(EXECUTIVE_WARNING in normalized, f"Generated page {index} lacks executive warning.")
                require(HYDRAULIC_STATUS in normalized, f"Generated page {index} lacks hydraulic status.")
                require(all(line in normalized for line in ROADMAP_LINES), f"Generated page {index} lacks C1/C2/C3 NOT_GENERATED status.")
                if mapping["title"] == GENERATED_TITLES[2]:
                    for token in MATURITY_TOKENS:
                        require(token in normalized, f"Maturity page lacks required token: {token}")
            else:
                section_id = mapping["section_id"]
                source_page = source_readers[section_id].pages[mapping["source_page"] - 1]
                source_text = source_page.extract_text() or ""
                actual_text_hash = text_sha256(source_text)
                actual_content_hash = sha256_bytes(page_content_bytes(source_page))
                require(mapping["source_page_text_sha256"] == actual_text_hash, f"Page map text hash differs from {section_id} source page {mapping['source_page']}.")
                require(mapping["source_page_content_sha256"] == actual_content_hash, f"Page map content hash differs from {section_id} source page {mapping['source_page']}.")
                require(text_sha256(text) == actual_text_hash, f"Dossier page {index} text differs from source.")
                require(sha256_bytes(page_content_bytes(page)) == actual_content_hash, f"Dossier page {index} content stream differs from source; rasterization or mutation is suspected.")
                source_preserved += 1
                if section_id == "CF0":
                    require(CF0_SEAL in normalized, f"Dossier CF0 page {index} lacks CF0 seal.")
                    require(HYDRAULIC_STATUS in normalized, f"Dossier CF0 page {index} lacks hydraulic warning.")
            combined_text.append(text)
            searchable_counts.append(searchable_count)
            media_boxes.append({"page": index, "width_pt": round(width, 3), "height_pt": round(height, 3), "a4_landscape": True})
    finally:
        rendered.close()
    joined = normalized_text("\n".join(combined_text))
    required_tokens = {
        E0_RELEASE: E0_RELEASE in joined,
        CF0_RELEASE: CF0_RELEASE in joined,
        GUIDANCE_WARNING: GUIDANCE_WARNING in joined,
        EXECUTIVE_WARNING: EXECUTIVE_WARNING in joined,
        HYDRAULIC_STATUS: HYDRAULIC_STATUS in joined,
        **{line: line in joined for line in ROADMAP_LINES},
        **{token: token in joined for token in MATURITY_TOKENS},
    }
    require(all(required_tokens.values()), f"Dossier PDF lacks required terms: {[key for key, value in required_tokens.items() if not value]}")
    forbidden = find_forbidden_claims(joined)
    require(not forbidden, f"Dossier PDF contains executive-release claims: {forbidden}")
    expected_source_pages = len(sources["E0"]["pages"]) + len(sources["CF0"]["pages"])
    require(source_preserved == expected_source_pages, "Not every source PDF page was preserved without rasterization.")
    return {
        "page_count": len(reader.pages),
        "all_pages_a4_landscape": True,
        "every_page_searchable": True,
        "every_page_nonblank": True,
        "every_page_has_guidance_warning": True,
        "source_pages_preserved_without_rasterization": True,
        "source_page_count": source_preserved,
        "required_tokens": required_tokens,
        "forbidden_claims": forbidden,
        "searchable_characters_by_page": searchable_counts,
        "media_boxes": media_boxes,
        "nonblank_checks": nonblank_checks,
    }


def verify_dossier(manifest_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    require(manifest_path.is_file(), f"Dossier manifest does not exist: {manifest_path}")
    manifest = validate_manifest_shape(json.loads(manifest_path.read_text(encoding="utf-8")))
    sources = validate_source_packages(manifest, manifest_path)
    page_map = validate_page_map(manifest, sources)
    outputs = require_object(manifest["output_integrity"], "output_integrity")
    require_keys(outputs, "output_integrity", {"pdf"})
    pdf_path = validate_file_record(outputs["pdf"], "output_integrity.pdf", manifest_path)
    validate_file_record(manifest["generator"], "generator", manifest_path)
    verifier_path = validate_file_record(manifest["verifier"], "verifier", manifest_path)
    require(verifier_path.resolve() == Path(__file__).resolve(), "Manifest verifier record does not identify this verifier.")
    pdf_checks = inspect_pdf(pdf_path, page_map, sources)
    preflight = require_object(manifest["preflight"], "preflight")
    require(preflight.get("status") == "PASS", "Recorded dossier preflight did not pass.")
    require(preflight.get("pdf_page_count") == manifest["page_count"], "Recorded dossier preflight page count disagrees.")
    require(preflight.get("all_pages_a4_landscape") is True, "Recorded dossier preflight lacks A4 approval.")
    require(preflight.get("every_page_searchable") is True, "Recorded dossier preflight lacks searchable pages.")
    require(preflight.get("every_page_has_guidance_warning") is True, "Recorded dossier preflight lacks guidance warnings.")
    require(preflight.get("source_pages_preserved_without_rasterization") is True, "Recorded dossier preflight does not prove source-page preservation.")
    require(preflight.get("concatenation_method") == "PYPDF_PAGE_OBJECT_APPEND_NO_RASTERIZATION", "Recorded concatenation method differs.")
    require(preflight.get("forbidden_claims") == [], "Recorded dossier preflight contains executive claims.")
    return {
        "status": "VERIFIED",
        "release": RELEASE,
        "manifest": str(manifest_path),
        "pdf": str(pdf_path),
        "page_count": manifest["page_count"],
        "source_page_count": pdf_checks["source_page_count"],
        "generated_page_count": len(manifest["generated_pages"]),
        "all_pages_a4_landscape": True,
        "every_page_searchable": True,
        "every_page_nonblank": True,
        "source_pages_preserved_without_rasterization": True,
        "roadmap": ROADMAP,
        "hydraulic_status": HYDRAULIC_STATUS,
        "guidance_authorized": False,
        "executive_project": False,
        "pdf_checks": pdf_checks,
    }


def run_self_test() -> dict[str, Any]:
    try:
        from assemble_final_dossier import assemble_dossier, write_synthetic_manifests
    except ModuleNotFoundError:
        from scripts.assemble_final_dossier import assemble_dossier, write_synthetic_manifests
    with tempfile.TemporaryDirectory(prefix="verify_final_dossier_self_test_") as temporary:
        root = Path(temporary)
        e0_manifest, cf0_manifest = write_synthetic_manifests(root)
        output_pdf = root / "dossier.pdf"
        output_manifest = root / "dossier_manifest.json"
        assemble_dossier(e0_manifest, cf0_manifest, output_pdf, output_manifest)
        verified = verify_dossier(output_manifest)
        require(verified["status"] == "VERIFIED", "Synthetic dossier did not verify.")

        invalid = json.loads(output_manifest.read_text(encoding="utf-8"))
        invalid["guidance_authorized"] = True
        invalid_path = root / "invalid_guidance.json"
        invalid_path.write_text(json.dumps(invalid, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            verify_dossier(invalid_path)
        except DossierVerificationError:
            pass
        else:
            raise RuntimeError("Verifier self-test accepted guidance authorization.")

        invalid = json.loads(output_manifest.read_text(encoding="utf-8"))
        invalid["page_map"][4]["source_page_content_sha256"] = "0" * 64
        invalid_path = root / "invalid_content_hash.json"
        invalid_path.write_text(json.dumps(invalid, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            verify_dossier(invalid_path)
        except DossierVerificationError:
            pass
        else:
            raise RuntimeError("Verifier self-test accepted a mutated source-page hash.")
        return {
            "status": "PASS",
            "checks": [
                "synthetic_end_to_end_verification",
                "a4_searchable_nonblank",
                "source_page_text_and_content_preservation",
                "maturity_6_13_5",
                "guidance_claim_fail_closed",
                "content_hash_fail_closed",
                "c1_c2_c3_not_generated",
            ],
            "synthetic_page_count": verified["page_count"],
        }


def cli_path(path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        print(json.dumps(run_self_test(), ensure_ascii=False, indent=2))
        return 0
    result = verify_dossier(cli_path(args.manifest))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"FINAL_DOSSIER_VERIFICATION_FAILED: {error}")
        raise SystemExit(2)
