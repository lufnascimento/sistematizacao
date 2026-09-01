#!/usr/bin/env python3
"""Assemble the E0 and CF0 review PDFs into one fail-closed dossier.

The source packages are validated before any output is staged.  New cover,
contents and divider pages are searchable vector PDF pages; source pages are
copied with pypdf and are never rasterized.  The dossier manifest is the last
artifact published.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages

try:
    from pypdf import PdfReader, PdfWriter
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
            from pypdf import PdfReader, PdfWriter
        except ModuleNotFoundError:
            continue
        break
    else:
        raise ModuleNotFoundError("pypdf is required to assemble the final dossier.")


ROOT = Path(__file__).resolve().parents[1]
DERIVED = ROOT / "dataset" / "derived"
DEFAULT_E0_MANIFEST = DERIVED / "review_atlas_manifest.json"
DEFAULT_CF0_MANIFEST = DERIVED / "cf0_review_report_manifest.json"
DEFAULT_OUTPUT_PDF = DERIVED / "Dossie_Final_Revisao_E0_CF0.pdf"
DEFAULT_OUTPUT_MANIFEST = DERIVED / "final_dossier_manifest.json"

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


class DossierContractError(RuntimeError):
    """Raised when a source package or assembled dossier is unsafe."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DossierContractError(message)


def require_object(value: Any, label: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"{label} must be an object.")
    return value


def require_array(value: Any, label: str, *, nonempty: bool = False) -> list[Any]:
    require(isinstance(value, list), f"{label} must be an array.")
    if nonempty:
        require(bool(value), f"{label} must not be empty.")
    return value


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


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def file_record(path: Path, *, declared_path: Path | None = None) -> dict[str, Any]:
    resolved = path.resolve()
    require(resolved.is_file(), f"Cannot record missing file: {resolved}")
    return {
        "path": display_path(declared_path or resolved),
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
    path_value = record.get("path")
    size = record.get("size_bytes")
    digest = record.get("sha256")
    require(isinstance(path_value, str) and bool(path_value), f"{label}.path is invalid.")
    require(isinstance(size, int) and not isinstance(size, bool) and size > 0, f"{label}.size_bytes is invalid.")
    require(isinstance(digest, str) and SHA256_PATTERN.fullmatch(digest) is not None, f"{label}.sha256 is invalid.")
    path = resolve_path(path_value, manifest_path)
    require(path.is_file(), f"{label} does not exist: {path}")
    require(path.stat().st_size == size, f"{label} size mismatch.")
    require(sha256_file(path) == digest, f"{label} SHA-256 mismatch.")
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
    declared_count: Any,
    label: str,
    *,
    exact_count: int | None = None,
) -> list[dict[str, Any]]:
    pages = require_array(pages_value, f"{label}.pages", nonempty=True)
    require(isinstance(declared_count, int) and not isinstance(declared_count, bool), f"{label}.page_count is invalid.")
    require(declared_count == len(pages), f"{label}.page_count disagrees with pages.")
    if exact_count is not None:
        require(declared_count == exact_count, f"{label} must contain exactly {exact_count} pages.")
    slugs: list[str] = []
    for index, page_value in enumerate(pages, start=1):
        page = require_object(page_value, f"{label}.pages[{index - 1}]")
        require(page.get("number") == index, f"{label} page numbers must be contiguous and one-based.")
        require(isinstance(page.get("title"), str) and page["title"].strip(), f"{label} page {index} title is empty.")
        require(isinstance(page.get("slug"), str) and page["slug"].strip(), f"{label} page {index} slug is empty.")
        slugs.append(page["slug"])
    require(len(slugs) == len(set(slugs)), f"{label} page slugs are duplicated.")
    return pages


def find_declared_pdf(
    output_integrity: Any,
    label: str,
    manifest_path: Path,
) -> tuple[dict[str, Any], Path]:
    records = list(integrity_records(output_integrity))
    pdf_records = [
        (trail, record)
        for trail, record in records
        if str(record.get("path", "")).casefold().endswith(".pdf")
    ]
    require(len(pdf_records) == 1, f"{label} must declare exactly one source PDF.")
    trail, record = pdf_records[0]
    path = validate_file_record(record, f"{label}.{trail}", manifest_path)
    return record, path


def find_forbidden_claims(text: str) -> list[str]:
    normalized = normalized_text(text)
    return [pattern.pattern for pattern in FORBIDDEN_EXECUTIVE_PATTERNS if pattern.search(normalized)]


@dataclass(frozen=True)
class SourcePackage:
    section_id: str
    release: str
    manifest_path: Path
    manifest_record: dict[str, Any]
    pdf_path: Path
    pdf_record: dict[str, Any]
    pages: list[dict[str, Any]]
    page_checks: list[dict[str, Any]]
    integrity_record_count: int


def inspect_source_pdf(
    pdf_path: Path,
    pages: list[dict[str, Any]],
    *,
    section_id: str,
) -> list[dict[str, Any]]:
    reader = PdfReader(str(pdf_path))
    require(not reader.is_encrypted, f"{section_id} source PDF must not be encrypted.")
    require(len(reader.pages) == len(pages), f"{section_id} source PDF page count disagrees with manifest.")
    checks: list[dict[str, Any]] = []
    combined: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        a4_landscape = (
            width > height
            and abs(width - A4_LANDSCAPE_POINTS[0]) <= PAGE_SIZE_TOLERANCE_POINTS
            and abs(height - A4_LANDSCAPE_POINTS[1]) <= PAGE_SIZE_TOLERANCE_POINTS
        )
        require(a4_landscape, f"{section_id} source PDF page {index} is not A4 landscape.")
        text = page.extract_text() or ""
        normalized = normalized_text(text)
        searchable_count = sum(not character.isspace() for character in text)
        require(searchable_count >= 20, f"{section_id} source PDF page {index} is not sufficiently searchable.")
        require(GUIDANCE_WARNING in normalized, f"{section_id} source PDF page {index} lacks the guidance warning.")
        if section_id == "CF0":
            require(CF0_SEAL in normalized, f"CF0 source PDF page {index} lacks the CF0 seal.")
            require(HYDRAULIC_STATUS in normalized, f"CF0 source PDF page {index} lacks hydraulic status.")
        combined.append(text)
        checks.append(
            {
                "page": index,
                "width_pt": round(width, 3),
                "height_pt": round(height, 3),
                "searchable_characters": searchable_count,
                "text_sha256": text_sha256(text),
                "content_sha256": sha256_bytes(page_content_bytes(page)),
            }
        )
    combined_text = "\n".join(combined)
    require(not find_forbidden_claims(combined_text), f"{section_id} source PDF contains an executive-release claim.")
    if section_id == "CF0":
        require(all(line in normalized_text(combined_text) for line in ROADMAP_LINES), "CF0 source PDF does not keep C1/C2/C3 as NOT_GENERATED.")
    return checks


def load_e0_package(manifest_path: Path) -> SourcePackage:
    require(manifest_path.is_file(), f"E0 manifest does not exist: {manifest_path}")
    manifest = require_object(json.loads(manifest_path.read_text(encoding="utf-8")), "E0 manifest")
    require(manifest.get("status") == E0_RELEASE, "Unexpected E0 release status.")
    require(manifest.get("legacy_pdfs_read") is False, "E0 report must not depend on legacy PDFs.")
    require(GUIDANCE_WARNING in normalized_text(str(manifest.get("release_warning", ""))), "E0 manifest lacks guidance warning.")
    pages = validate_page_descriptors(
        manifest.get("pages"),
        manifest.get("page_count"),
        "E0 manifest",
        exact_count=EXPECTED_E0_PAGE_COUNT,
    )
    preflight = require_object(manifest.get("preflight"), "E0 manifest.preflight")
    require(preflight.get("status") == "PASS", "E0 recorded preflight did not pass.")
    require(preflight.get("pdf_page_count") == EXPECTED_E0_PAGE_COUNT, "E0 preflight page count disagrees.")
    require(preflight.get("all_pages_a4_landscape") is True, "E0 preflight did not approve A4 landscape.")
    require(preflight.get("all_page_assets_nonblank") is True, "E0 preflight contains blank assets.")
    source_records = validate_integrity_tree(manifest.get("source_integrity"), "E0 source_integrity", manifest_path)
    output_records = validate_integrity_tree(manifest.get("output_integrity"), "E0 output_integrity", manifest_path)
    pdf_record, pdf_path = find_declared_pdf(manifest.get("output_integrity"), "E0 output_integrity", manifest_path)
    page_checks = inspect_source_pdf(pdf_path, pages, section_id="E0")
    return SourcePackage(
        section_id="E0",
        release=E0_RELEASE,
        manifest_path=manifest_path.resolve(),
        manifest_record=file_record(manifest_path),
        pdf_path=pdf_path,
        pdf_record=dict(pdf_record),
        pages=pages,
        page_checks=page_checks,
        integrity_record_count=len(source_records) + len(output_records),
    )


def load_cf0_package(manifest_path: Path) -> SourcePackage:
    require(manifest_path.is_file(), f"CF0 report manifest does not exist: {manifest_path}")
    manifest = require_object(json.loads(manifest_path.read_text(encoding="utf-8")), "CF0 report manifest")
    require(manifest.get("schema_version") == "1.2.0", "CF0 report is not contract 1.2.")
    require(manifest.get("manifest_type") == "CF0_REVIEW_REPORT", "Unexpected CF0 report manifest type.")
    require(manifest.get("release") == CF0_RELEASE, "Unexpected CF0 report release.")
    require(manifest.get("release_seal") == f"{CF0_SEAL} | {GUIDANCE_WARNING}", "CF0 report seal differs.")
    require(manifest.get("hydraulic_status") == HYDRAULIC_STATUS, "CF0 report cannot approve hydraulics.")
    require(manifest.get("guidance_authorized") is False, "CF0 report cannot authorize guidance.")
    require(manifest.get("legacy_pdfs_read") is False, "CF0 report must not depend on legacy PDFs.")
    require(manifest.get("source_layers") == list(EXPECTED_CF0_SOURCE_LAYERS), "CF0 report must declare exactly four source layers.")
    require(manifest.get("roadmap") == ROADMAP, "CF0 report must keep C1/C2/C3 as NOT_GENERATED.")
    diagnostics = require_object(manifest.get("diagnostic_rows"), "CF0 diagnostic_rows")
    require(diagnostics.get("layer_required") is True, "CF0 diagnostic_rows must be mandatory.")
    require(diagnostics.get("status") == "PRESENT_NOT_APPROVED", "CF0 diagnostic_rows cannot be absent or approved.")
    require(diagnostics.get("review_status") == "NOT_APPROVED", "CF0 diagnostic rows must remain NOT_APPROVED.")
    require(diagnostics.get("guidance_status") == "NOT_AUTHORIZED", "CF0 diagnostic rows must remain NOT_AUTHORIZED.")
    require(diagnostics.get("release_authorized") is False, "CF0 diagnostic rows cannot authorize release.")
    pages = validate_page_descriptors(manifest.get("pages"), manifest.get("page_count"), "CF0 report manifest")
    preflight = require_object(manifest.get("preflight"), "CF0 report manifest.preflight")
    require(preflight.get("status") == "PASS", "CF0 recorded preflight did not pass.")
    require(preflight.get("pdf_page_count") == len(pages), "CF0 preflight page count disagrees.")
    require(preflight.get("all_pages_a4_landscape") is True, "CF0 preflight did not approve A4 landscape.")
    require(preflight.get("every_page_has_release_seal") is True, "CF0 preflight lacks every-page seal.")
    require(preflight.get("every_page_has_hydraulic_warning") is True, "CF0 preflight lacks hydraulic warnings.")
    source_records = validate_integrity_tree(manifest.get("source_package"), "CF0 source_package", manifest_path)
    output_records = validate_integrity_tree(manifest.get("output_integrity"), "CF0 output_integrity", manifest_path)
    pdf_record, pdf_path = find_declared_pdf(manifest.get("output_integrity"), "CF0 output_integrity", manifest_path)
    generator = manifest.get("generator")
    if isinstance(generator, dict) and "size_bytes" in generator:
        validate_file_record(generator, "CF0 generator", manifest_path)
    page_checks = inspect_source_pdf(pdf_path, pages, section_id="CF0")
    return SourcePackage(
        section_id="CF0",
        release=CF0_RELEASE,
        manifest_path=manifest_path.resolve(),
        manifest_record=file_record(manifest_path),
        pdf_path=pdf_path,
        pdf_record=dict(pdf_record),
        pages=pages,
        page_checks=page_checks,
        integrity_record_count=len(source_records) + len(output_records),
    )


def style_page(fig: Any, title: str, eyebrow: str, page_number: int) -> Any:
    fig.patch.set_facecolor("#f5f6f2")
    axis = fig.add_axes((0, 0, 1, 1))
    axis.set_axis_off()
    axis.add_patch(plt.Rectangle((0, 0.93), 1, 0.07, color="#173a31", transform=axis.transAxes))
    axis.text(0.055, 0.962, eyebrow, transform=axis.transAxes, va="center", ha="left", color="white", fontsize=9.5, fontweight="bold")
    axis.text(0.055, 0.875, title, transform=axis.transAxes, va="top", ha="left", color="#17211e", fontsize=25, fontweight="bold")
    axis.plot((0.055, 0.945), (0.105, 0.105), transform=axis.transAxes, color="#bdc9c3", linewidth=0.9)
    footer = f"{DOSSIER_SEAL} | {EXECUTIVE_WARNING} | {HYDRAULIC_STATUS}"
    axis.text(0.055, 0.064, footer, transform=axis.transAxes, va="center", ha="left", color="#263b35", fontsize=7.7, fontweight="bold")
    axis.text(0.945, 0.064, f"{page_number:02d}", transform=axis.transAxes, va="center", ha="right", color="#52645e", fontsize=8)
    return axis


def draw_status_strip(axis: Any, y: float) -> None:
    axis.add_patch(plt.Rectangle((0.055, y - 0.06), 0.89, 0.12, transform=axis.transAxes, facecolor="#e8ece9", edgecolor="#aab8b2", linewidth=0.8))
    axis.text(
        0.075,
        y,
        "  |  ".join(ROADMAP_LINES),
        transform=axis.transAxes,
        va="center",
        ha="left",
        color="#263b35",
        fontsize=9.2,
        fontweight="bold",
    )


def render_generated_pages(path: Path, e0_count: int, cf0_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    first_e0_page = 5
    last_e0_page = first_e0_page + e0_count - 1
    cf0_divider_page = last_e0_page + 1
    first_cf0_page = cf0_divider_page + 1
    last_cf0_page = first_cf0_page + cf0_count - 1
    metadata = {
        "Title": "Dossiê técnico consolidado E0 + CF0",
        "Author": "Projeto de Sistematização",
        "Subject": "Revisão topográfica e geométrica sem autorização executiva",
        "Keywords": "E0 CF0 HYDRAULIC_UNCONFIRMED NOT_GENERATED",
        "Creator": "scripts/assemble_final_dossier.py",
    }
    with PdfPages(path, metadata=metadata) as pdf:
        fig = plt.figure(figsize=(11.69, 8.27))
        axis = style_page(fig, GENERATED_TITLES[0], "SISTEMATIZAÇÃO DA CANA | REVISÃO CONSOLIDADA", 1)
        axis.text(0.055, 0.72, "E0 + CF0", transform=axis.transAxes, fontsize=48, color="#1f6a52", fontweight="bold", va="top")
        axis.text(0.055, 0.57, "Topografia, triagem geométrica e família contínua", transform=axis.transAxes, fontsize=17, color="#263b35", va="top")
        axis.text(0.055, 0.47, "E0 descreve terreno e primitivas de triagem. CF0 descreve famílias contínuas geométricas.", transform=axis.transAxes, fontsize=11.5, color="#33443f", va="top")
        axis.text(0.055, 0.41, "Nenhum dos dois fecha PCE/PCX, receptores, colheitabilidade completa ou exportação para máquina.", transform=axis.transAxes, fontsize=11.5, color="#33443f", va="top")
        draw_status_strip(axis, 0.25)
        pdf.savefig(fig)
        plt.close(fig)

        fig = plt.figure(figsize=(11.69, 8.27))
        axis = style_page(fig, GENERATED_TITLES[1], "MAPA DE LEITURA E LIMITES", 2)
        rows = (
            ("Capa, sumário e maturidade", "1-3", "limites, aderência e próximos gates"),
            ("Divisória E0", "4", "como ler o diagnóstico topográfico"),
            ("Caderno E0", f"{first_e0_page}-{last_e0_page}", f"{e0_count} páginas preservadas"),
            ("Divisória CF0", str(cf0_divider_page), "como ler a família contínua"),
            ("Caderno CF0", f"{first_cf0_page}-{last_cf0_page}", f"{cf0_count} páginas preservadas"),
        )
        for row, (label, pages, description) in enumerate(rows):
            y = 0.72 - row * 0.105
            axis.text(0.07, y, label, transform=axis.transAxes, fontsize=12, fontweight="bold", color="#173a31", va="center")
            axis.text(0.35, y, pages, transform=axis.transAxes, fontsize=11, color="#1f6a52", va="center")
            axis.text(0.48, y, description, transform=axis.transAxes, fontsize=10.5, color="#33443f", va="center")
            axis.plot((0.07, 0.93), (y - 0.045, y - 0.045), transform=axis.transAxes, color="#d1d8d4", linewidth=0.7)
        draw_status_strip(axis, 0.18)
        pdf.savefig(fig)
        plt.close(fig)

        fig = plt.figure(figsize=(11.69, 8.27))
        axis = style_page(fig, GENERATED_TITLES[2], "ADERÊNCIA AUDITADA | 24 RECORTES", 3)
        maturity = (
            ("6", "IMPLEMENTADO", "recortes executáveis em escopo limitado", "#1f6a52"),
            ("13", "ESPECIFICADO", "contratos sem solver integrado", "#8b6a1f"),
            ("5", "BLOQUEADO", "dependências de dado, método ou validação", "#8a352d"),
        )
        for column, (count, status, description, color) in enumerate(maturity):
            x = 0.06 + column * 0.30
            axis.add_patch(plt.Rectangle((x, 0.54), 0.26, 0.20, transform=axis.transAxes, facecolor="#ffffff", edgecolor=color, linewidth=1.3))
            axis.text(x + 0.025, 0.685, count, transform=axis.transAxes, fontsize=31, fontweight="bold", color=color, va="top")
            axis.text(x + 0.095, 0.68, status, transform=axis.transAxes, fontsize=10.5, fontweight="bold", color=color, va="top")
            axis.text(x + 0.025, 0.585, description, transform=axis.transAxes, fontsize=8.7, color="#33443f", va="top", wrap=True)
        axis.text(0.06, 0.46, "Fronteira honesta", transform=axis.transAxes, fontsize=11.5, fontweight="bold", color="#17211e")
        axis.text(0.06, 0.405, "CF0 geométrico e POA estático têm implementação limitada. PCE/PCX, frota completa e C1-C3 ainda não executam.", transform=axis.transAxes, fontsize=10.2, color="#33443f")
        axis.text(0.06, 0.34, "Próximos gates", transform=axis.transAxes, fontsize=11.5, fontweight="bold", color="#17211e")
        axis.text(0.06, 0.285, "incerteza do MDT → estados de linha/controlador/frota → PCE/PCX/receptores → C1/C2/C3 → logística e ciclo de vida", transform=axis.transAxes, fontsize=9.8, color="#33443f")
        axis.text(0.06, 0.225, "Fontes: docs/MATRIZ_AGRONOMICA_CONSERVACAO_COLHEITABILIDADE.md | docs/ROADMAP_FECHAMENTO_ADERENCIA_AGRONOMICA.md", transform=axis.transAxes, fontsize=8.2, color="#52645e")
        draw_status_strip(axis, 0.145)
        pdf.savefig(fig)
        plt.close(fig)

        fig = plt.figure(figsize=(11.69, 8.27))
        axis = style_page(fig, GENERATED_TITLES[3], "PARTE I | DIAGNÓSTICO E0", 4)
        axis.text(0.055, 0.69, E0_RELEASE, transform=axis.transAxes, fontsize=13, fontweight="bold", color="#1f6a52", va="top")
        axis.text(0.055, 0.60, "Inclui", transform=axis.transAxes, fontsize=12, fontweight="bold", color="#17211e", va="top")
        axis.text(0.075, 0.54, "MDT, densidade, declividade, curvas, fluxo topográfico e primitivas geométricas de triagem.", transform=axis.transAxes, fontsize=11, color="#33443f", va="top")
        axis.text(0.055, 0.43, "Não inclui", transform=axis.transAxes, fontsize=12, fontweight="bold", color="#8a352d", va="top")
        axis.text(0.075, 0.37, "Projeto hidráulico, ESD aprovado, colheitabilidade completa, POA dimensionado ou linha de guiamento.", transform=axis.transAxes, fontsize=11, color="#5b3a35", va="top")
        draw_status_strip(axis, 0.20)
        pdf.savefig(fig)
        plt.close(fig)

        fig = plt.figure(figsize=(11.69, 8.27))
        axis = style_page(fig, GENERATED_TITLES[4], "PARTE II | FAMÍLIA CONTÍNUA CF0", cf0_divider_page)
        axis.text(0.055, 0.69, CF0_RELEASE, transform=axis.transAxes, fontsize=13, fontweight="bold", color="#1f6a52", va="top")
        axis.text(0.055, 0.60, "Inclui", transform=axis.transAxes, fontsize=12, fontweight="bold", color="#17211e", va="top")
        axis.text(0.075, 0.54, "Campo axial local, fase, linhas contínuas, espaçamento, topologia, raio conservador e diagnósticos.", transform=axis.transAxes, fontsize=11, color="#33443f", va="top")
        axis.text(0.055, 0.43, "Permanece bloqueado", transform=axis.transAxes, fontsize=12, fontweight="bold", color="#8a352d", va="top")
        axis.text(0.075, 0.37, f"{CF0_SEAL}. {HYDRAULIC_STATUS}. Diagnósticos não aprovados e nenhuma autorização de guidance.", transform=axis.transAxes, fontsize=11, color="#5b3a35", va="top")
        draw_status_strip(axis, 0.20)
        pdf.savefig(fig)
        plt.close(fig)


def build_page_map(e0: SourcePackage, cf0: SourcePackage) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    page_map: list[dict[str, Any]] = [
        {"final_page": 1, "section_id": "FRONTMATTER", "kind": "GENERATED", "title": GENERATED_TITLES[0], "source_page": None},
        {"final_page": 2, "section_id": "FRONTMATTER", "kind": "GENERATED", "title": GENERATED_TITLES[1], "source_page": None},
        {"final_page": 3, "section_id": "FRONTMATTER", "kind": "GENERATED", "title": GENERATED_TITLES[2], "source_page": None},
        {"final_page": 4, "section_id": "E0_DIVIDER", "kind": "GENERATED", "title": GENERATED_TITLES[3], "source_page": None},
    ]
    for descriptor, check in zip(e0.pages, e0.page_checks):
        page_map.append(
            {
                "final_page": len(page_map) + 1,
                "section_id": "E0",
                "kind": "SOURCE_PAGE",
                "title": descriptor["title"],
                "source_page": descriptor["number"],
                "source_page_text_sha256": check["text_sha256"],
                "source_page_content_sha256": check["content_sha256"],
            }
        )
    cf0_divider_page = len(page_map) + 1
    page_map.append(
        {"final_page": cf0_divider_page, "section_id": "CF0_DIVIDER", "kind": "GENERATED", "title": GENERATED_TITLES[4], "source_page": None}
    )
    for descriptor, check in zip(cf0.pages, cf0.page_checks):
        page_map.append(
            {
                "final_page": len(page_map) + 1,
                "section_id": "CF0",
                "kind": "SOURCE_PAGE",
                "title": descriptor["title"],
                "source_page": descriptor["number"],
                "source_page_text_sha256": check["text_sha256"],
                "source_page_content_sha256": check["content_sha256"],
            }
        )
    sections = [
        {"id": "FRONTMATTER", "title": "Capa, sumário e maturidade", "start_page": 1, "end_page": 3, "page_count": 3, "origin": "GENERATED_VECTOR_PDF"},
        {"id": "E0", "title": "Parte I | E0", "start_page": 4, "end_page": 4 + len(e0.pages), "page_count": 1 + len(e0.pages), "origin": "DIVIDER_PLUS_SOURCE_PDF"},
        {"id": "CF0", "title": "Parte II | CF0", "start_page": cf0_divider_page, "end_page": len(page_map), "page_count": 1 + len(cf0.pages), "origin": "DIVIDER_PLUS_SOURCE_PDF"},
    ]
    return page_map, sections


def write_combined_pdf(
    path: Path,
    generated_pdf: Path,
    e0: SourcePackage,
    cf0: SourcePackage,
    page_map: list[dict[str, Any]],
) -> None:
    generated = PdfReader(str(generated_pdf))
    e0_reader = PdfReader(str(e0.pdf_path))
    cf0_reader = PdfReader(str(cf0.pdf_path))
    require(len(generated.pages) == 5, "Generated front matter must contain five pages.")
    writer = PdfWriter()
    writer.add_metadata(
        {
            "/Title": "Dossiê técnico consolidado E0 + CF0",
            "/Author": "Projeto de Sistematização",
            "/Subject": "Revisão não executiva e sem autorização de guiamento",
            "/Keywords": "E0 CF0 HYDRAULIC_UNCONFIRMED NOT_GENERATED",
            "/Creator": "scripts/assemble_final_dossier.py",
        }
    )
    for page in generated.pages[:4]:
        writer.add_page(page)
    for page in e0_reader.pages:
        writer.add_page(page)
    writer.add_page(generated.pages[4])
    for page in cf0_reader.pages:
        writer.add_page(page)
    require(len(writer.pages) == len(page_map), "Internal page map disagrees with pypdf writer.")

    writer.add_outline_item(GENERATED_TITLES[0], 0, bold=True)
    writer.add_outline_item(GENERATED_TITLES[1], 1)
    writer.add_outline_item(GENERATED_TITLES[2], 2)
    e0_parent = writer.add_outline_item(GENERATED_TITLES[3], 3, bold=True)
    for item in page_map:
        if item["section_id"] == "E0" and item["kind"] == "SOURCE_PAGE":
            writer.add_outline_item(item["title"], item["final_page"] - 1, parent=e0_parent)
    cf0_divider = next(item for item in page_map if item["section_id"] == "CF0_DIVIDER")
    cf0_parent = writer.add_outline_item(GENERATED_TITLES[4], cf0_divider["final_page"] - 1, bold=True)
    for item in page_map:
        if item["section_id"] == "CF0" and item["kind"] == "SOURCE_PAGE":
            writer.add_outline_item(item["title"], item["final_page"] - 1, parent=cf0_parent)
    with path.open("wb") as stream:
        writer.write(stream)


def inspect_assembled_pdf(path: Path, page_map: list[dict[str, Any]]) -> dict[str, Any]:
    reader = PdfReader(str(path))
    require(not reader.is_encrypted, "Final dossier must not be encrypted.")
    require(len(reader.pages) == len(page_map), "Final dossier page count disagrees with page map.")
    media_boxes: list[dict[str, Any]] = []
    searchable_counts: list[int] = []
    source_preserved = 0
    combined_text: list[str] = []
    for index, (page, mapping) in enumerate(zip(reader.pages, page_map), start=1):
        require(mapping["final_page"] == index, "Page map is not contiguous.")
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        a4 = (
            width > height
            and abs(width - A4_LANDSCAPE_POINTS[0]) <= PAGE_SIZE_TOLERANCE_POINTS
            and abs(height - A4_LANDSCAPE_POINTS[1]) <= PAGE_SIZE_TOLERANCE_POINTS
        )
        require(a4, f"Final dossier page {index} is not A4 landscape.")
        text = page.extract_text() or ""
        normalized = normalized_text(text)
        count = sum(not character.isspace() for character in text)
        require(count >= 20, f"Final dossier page {index} is not sufficiently searchable.")
        require(GUIDANCE_WARNING in normalized, f"Final dossier page {index} lacks guidance warning.")
        if mapping["kind"] == "GENERATED":
            require(mapping["title"] in normalized, f"Generated dossier page {index} lacks its title.")
            require(EXECUTIVE_WARNING in normalized, f"Generated dossier page {index} lacks executive warning.")
            require(HYDRAULIC_STATUS in normalized, f"Generated dossier page {index} lacks hydraulic status.")
            require(all(line in normalized for line in ROADMAP_LINES), f"Generated dossier page {index} lacks C1/C2/C3 status.")
        else:
            require(text_sha256(text) == mapping["source_page_text_sha256"], f"Source text changed on final page {index}.")
            require(sha256_bytes(page_content_bytes(page)) == mapping["source_page_content_sha256"], f"Source content stream changed on final page {index}; rasterization or mutation is suspected.")
            source_preserved += 1
            if mapping["section_id"] == "CF0":
                require(CF0_SEAL in normalized, f"Final CF0 page {index} lacks CF0 seal.")
                require(HYDRAULIC_STATUS in normalized, f"Final CF0 page {index} lacks hydraulic status.")
        combined_text.append(text)
        searchable_counts.append(count)
        media_boxes.append({"page": index, "width_pt": round(width, 3), "height_pt": round(height, 3), "a4_landscape": True})
    joined = normalized_text("\n".join(combined_text))
    required_tokens = {
        E0_RELEASE: E0_RELEASE in joined,
        CF0_RELEASE: CF0_RELEASE in joined,
        GUIDANCE_WARNING: GUIDANCE_WARNING in joined,
        EXECUTIVE_WARNING: EXECUTIVE_WARNING in joined,
        HYDRAULIC_STATUS: HYDRAULIC_STATUS in joined,
        **{line: line in joined for line in ROADMAP_LINES},
    }
    require(all(required_tokens.values()), f"Final dossier lacks required terms: {[key for key, value in required_tokens.items() if not value]}")
    forbidden = find_forbidden_claims(joined)
    require(not forbidden, f"Final dossier contains executive-release claims: {forbidden}")
    expected_source_pages = sum(item["kind"] == "SOURCE_PAGE" for item in page_map)
    require(source_preserved == expected_source_pages, "Not every source page was preserved without rasterization.")
    return {
        "status": "PASS",
        "pdf_page_count": len(reader.pages),
        "all_pages_a4_landscape": True,
        "every_page_searchable": True,
        "every_page_has_guidance_warning": True,
        "source_pages_preserved_without_rasterization": True,
        "source_page_count": source_preserved,
        "concatenation_method": "PYPDF_PAGE_OBJECT_APPEND_NO_RASTERIZATION",
        "required_tokens": required_tokens,
        "forbidden_claims": forbidden,
        "searchable_characters_by_page": searchable_counts,
        "media_boxes": media_boxes,
    }


def json_dump_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".tmp")
    staging.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    staging.replace(path)


def package_manifest_record(source: SourcePackage) -> dict[str, Any]:
    return {
        "manifest": source.manifest_record,
        "pdf": source.pdf_record,
        "release": source.release,
        "page_count": len(source.pages),
        "preflight": {
            "status": "PASS",
            "all_pages_a4_landscape": True,
            "every_page_searchable": True,
            "integrity_record_count": source.integrity_record_count,
        },
    }


def assemble_dossier(
    e0_manifest_path: Path,
    cf0_manifest_path: Path,
    output_pdf: Path,
    output_manifest: Path,
) -> dict[str, Any]:
    e0_manifest_path = e0_manifest_path.resolve()
    cf0_manifest_path = cf0_manifest_path.resolve()
    output_pdf = output_pdf.resolve()
    output_manifest = output_manifest.resolve()
    require(output_pdf.suffix.casefold() == ".pdf", "Output dossier must use .pdf extension.")
    require(output_manifest.suffix.casefold() == ".json", "Output manifest must use .json extension.")
    protected = {e0_manifest_path, cf0_manifest_path}
    require(output_pdf not in protected and output_manifest not in protected, "Outputs cannot overwrite source manifests.")

    e0 = load_e0_package(e0_manifest_path)
    cf0 = load_cf0_package(cf0_manifest_path)
    require(output_pdf not in {e0.pdf_path.resolve(), cf0.pdf_path.resolve()}, "Output PDF cannot overwrite a source PDF.")
    page_map, sections = build_page_map(e0, cf0)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    require(output_pdf.parent == output_manifest.parent, "PDF and manifest must be published in the same directory for atomic staging.")

    with tempfile.TemporaryDirectory(prefix="final_dossier_", dir=output_pdf.parent) as temporary:
        staging_root = Path(temporary)
        generated_pdf = staging_root / "generated_vector_pages.pdf"
        staged_pdf = staging_root / output_pdf.name
        render_generated_pages(generated_pdf, len(e0.pages), len(cf0.pages))
        write_combined_pdf(staged_pdf, generated_pdf, e0, cf0, page_map)
        preflight = inspect_assembled_pdf(staged_pdf, page_map)
        generated_at = datetime.now(timezone.utc).isoformat()
        output_record = file_record(staged_pdf, declared_path=output_pdf)
        generator_path = Path(__file__).resolve()
        verifier_path = ROOT / "scripts" / "verify_final_dossier.py"
        require(verifier_path.is_file(), f"Final dossier verifier is missing: {verifier_path}")
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "manifest_type": MANIFEST_TYPE,
            "release": RELEASE,
            "generated_at": generated_at,
            "release_seal": DOSSIER_SEAL,
            "hydraulic_status": HYDRAULIC_STATUS,
            "guidance_authorized": False,
            "executive_project": False,
            "concatenation": {
                "library": "pypdf",
                "method": "PYPDF_PAGE_OBJECT_APPEND_NO_RASTERIZATION",
                "source_page_text_and_content_hashes_required": True,
            },
            "roadmap": dict(ROADMAP),
            "source_packages": {
                "E0": package_manifest_record(e0),
                "CF0": package_manifest_record(cf0),
            },
            "generated_pages": [
                {"final_page": item["final_page"], "title": item["title"], "section_id": item["section_id"]}
                for item in page_map
                if item["kind"] == "GENERATED"
            ],
            "page_count": len(page_map),
            "sections": sections,
            "page_map": page_map,
            "output_integrity": {"pdf": output_record},
            "preflight": preflight,
            "publication_order": [
                "SOURCE_MANIFESTS_HASHES_AND_PDFS_VALIDATED",
                "PDF_ATOMIC_REPLACE",
                "MANIFEST_ATOMIC_REPLACE_LAST",
            ],
            "generator": file_record(generator_path),
            "verifier": file_record(verifier_path),
        }
        staged_pdf.replace(output_pdf)
        json_dump_atomic(output_manifest, manifest)
    return {
        "status": "ASSEMBLED",
        "release": RELEASE,
        "pdf": str(output_pdf),
        "manifest": str(output_manifest),
        "page_count": len(page_map),
        "source_pages": {"E0": len(e0.pages), "CF0": len(cf0.pages)},
        "roadmap": ROADMAP,
        "hydraulic_status": HYDRAULIC_STATUS,
        "guidance_authorized": False,
        "executive_project": False,
    }


def create_synthetic_pdf(path: Path, titles: list[str], section_id: str) -> None:
    with PdfPages(path) as pdf:
        for index, title in enumerate(titles, start=1):
            fig = plt.figure(figsize=(11.69, 8.27))
            axis = fig.add_axes((0, 0, 1, 1))
            axis.set_axis_off()
            axis.text(0.08, 0.82, title, fontsize=24, fontweight="bold", color="#173a31")
            axis.text(0.08, 0.66, f"Página sintética {index} do pacote {section_id}, com conteúdo pesquisável para validar preservação.", fontsize=12)
            axis.text(0.08, 0.57, GUIDANCE_WARNING, fontsize=11, fontweight="bold", color="#8a352d")
            if section_id == "CF0":
                axis.text(0.08, 0.49, CF0_SEAL, fontsize=11, fontweight="bold")
                axis.text(0.08, 0.42, HYDRAULIC_STATUS, fontsize=11, fontweight="bold")
                axis.text(0.08, 0.33, " | ".join(ROADMAP_LINES), fontsize=9, fontweight="bold")
            else:
                axis.text(0.08, 0.49, E0_RELEASE, fontsize=11, fontweight="bold")
            axis.plot((0.08, 0.92), (0.20, 0.20), color="#1f6a52", linewidth=3)
            pdf.savefig(fig)
            plt.close(fig)


def write_synthetic_manifests(root: Path) -> tuple[Path, Path]:
    e0_pdf = root / "e0.pdf"
    cf0_pdf = root / "cf0.pdf"
    e0_titles = [f"E0 sintético {index:02d}" for index in range(1, EXPECTED_E0_PAGE_COUNT + 1)]
    cf0_titles = ["CF0 capa sintética", "CF0 método sintético", "CF0 limites sintéticos"]
    create_synthetic_pdf(e0_pdf, e0_titles, "E0")
    create_synthetic_pdf(cf0_pdf, cf0_titles, "CF0")
    dummy = root / "source.txt"
    dummy.write_text("synthetic source integrity\n", encoding="utf-8")
    dummy_record = file_record(dummy)
    e0_manifest = {
        "atlas_id": "synthetic-e0",
        "status": E0_RELEASE,
        "generated_at": "2026-08-20T00:00:00Z",
        "deterministic_metadata": True,
        "legacy_pdfs_read": False,
        "release_warning": GUIDANCE_WARNING,
        "page_count": len(e0_titles),
        "pages": [
            {"number": index, "title": title, "slug": f"e0-{index:02d}"}
            for index, title in enumerate(e0_titles, start=1)
        ],
        "source_integrity": [dummy_record],
        "output_integrity": {"pdf": file_record(e0_pdf)},
        "preflight": {
            "status": "PASS",
            "pdf_page_count": len(e0_titles),
            "all_pages_a4_landscape": True,
            "all_page_assets_nonblank": True,
        },
    }
    cf0_manifest = {
        "schema_version": "1.2.0",
        "manifest_type": "CF0_REVIEW_REPORT",
        "release": CF0_RELEASE,
        "generated_at": "2026-08-20T00:00:00Z",
        "release_seal": f"{CF0_SEAL} | {GUIDANCE_WARNING}",
        "hydraulic_status": HYDRAULIC_STATUS,
        "guidance_authorized": False,
        "legacy_pdfs_read": False,
        "source_package": {"fixture": dummy_record},
        "source_layers": list(EXPECTED_CF0_SOURCE_LAYERS),
        "diagnostic_rows": {
            "layer_required": True,
            "status": "PRESENT_NOT_APPROVED",
            "review_status": "NOT_APPROVED",
            "guidance_status": "NOT_AUTHORIZED",
            "feature_count": 2,
            "release_authorized": False,
        },
        "roadmap": dict(ROADMAP),
        "page_count": len(cf0_titles),
        "pages": [
            {"number": index, "title": title, "slug": f"cf0-{index:02d}", "kind": "synthetic", "payload": None}
            for index, title in enumerate(cf0_titles, start=1)
        ],
        "output_integrity": {"pdf": file_record(cf0_pdf)},
        "preflight": {
            "status": "PASS",
            "pdf_page_count": len(cf0_titles),
            "all_pages_a4_landscape": True,
            "every_page_has_release_seal": True,
            "every_page_has_hydraulic_warning": True,
        },
    }
    e0_path = root / "e0_manifest.json"
    cf0_path = root / "cf0_manifest.json"
    e0_path.write_text(json.dumps(e0_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    cf0_path.write_text(json.dumps(cf0_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return e0_path, cf0_path


def run_self_test() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="final_dossier_self_test_") as temporary:
        root = Path(temporary)
        e0_manifest, cf0_manifest = write_synthetic_manifests(root)
        output_pdf = root / "dossier.pdf"
        output_manifest = root / "dossier_manifest.json"
        assembled = assemble_dossier(e0_manifest, cf0_manifest, output_pdf, output_manifest)
        try:
            from verify_final_dossier import verify_dossier
        except ModuleNotFoundError:
            from scripts.verify_final_dossier import verify_dossier
        verified = verify_dossier(output_manifest)
        require(verified["status"] == "VERIFIED", "Synthetic dossier did not pass independent verification.")
        require(assembled["page_count"] == 5 + EXPECTED_E0_PAGE_COUNT + 3, "Synthetic page count is wrong.")

        tampered = json.loads(output_manifest.read_text(encoding="utf-8"))
        tampered["roadmap"]["C3_ESD"] = "GENERATED"
        tampered_path = root / "tampered_manifest.json"
        tampered_path.write_text(json.dumps(tampered, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            verify_dossier(tampered_path)
        except Exception:
            pass
        else:
            raise RuntimeError("Synthetic self-test accepted a generated C3 claim.")
        return {
            "status": "PASS",
            "checks": [
                "source_manifest_and_hash_preflight",
                "pypdf_page_object_concatenation",
                "source_text_and_content_stream_preservation",
                "a4_landscape_and_searchable_pages",
                "manifest_published_last_contract",
                "independent_verifier",
                "c1_c2_c3_fail_closed",
            ],
            "synthetic_page_count": assembled["page_count"],
        }


def cli_path(path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--e0-manifest", type=Path, default=DEFAULT_E0_MANIFEST)
    parser.add_argument("--cf0-manifest", type=Path, default=DEFAULT_CF0_MANIFEST)
    parser.add_argument("--output-pdf", type=Path, default=DEFAULT_OUTPUT_PDF)
    parser.add_argument("--output-manifest", type=Path, default=DEFAULT_OUTPUT_MANIFEST)
    parser.add_argument("--self-test", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        print(json.dumps(run_self_test(), ensure_ascii=False, indent=2))
        return 0
    result = assemble_dossier(
        cli_path(args.e0_manifest),
        cli_path(args.cf0_manifest),
        cli_path(args.output_pdf),
        cli_path(args.output_manifest),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"FINAL_DOSSIER_ASSEMBLY_FAILED: {error}")
        raise SystemExit(2)
