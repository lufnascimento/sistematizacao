#!/usr/bin/env python3
"""Generate the searchable C1 E0 embedded-terrace screening report.

The report is a comparison artifact for the geometric sensitivity matrix. It
consumes a *verified* ``embedded_terrace_screening_manifest.json`` package and
never promotes isolines, topology strips, or clipped CF0C rows to an approved
terrace, PCE/PCX result, hydraulic design, construction surface, or guidance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import textwrap
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

os.environ.setdefault("SOURCE_DATE_EPOCH", "1786676400")

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib.patches import Rectangle
from osgeo import ogr
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
    else:  # pragma: no cover - environment contract
        raise ModuleNotFoundError("pypdf is required for searchable-PDF preflight.")

try:
    from verify_embedded_terrace_screening import verify as verify_source_package
except ModuleNotFoundError:
    from scripts.verify_embedded_terrace_screening import verify as verify_source_package


ROOT = Path(__file__).resolve().parents[1]
DERIVED = ROOT / "dataset" / "derived"
DEFAULT_SOURCE_MANIFEST = DERIVED / "embedded_terrace_screening_manifest.json"
DEFAULT_SOURCE_SCHEMA = ROOT / "schemas" / "embedded-terrace-screening-stage.schema.json"
DEFAULT_PDF = DERIVED / "Relatorio_Triagem_C1_Curva_Embutida_E0.pdf"
DEFAULT_REPORT_MANIFEST = DERIVED / "embedded_terrace_screening_report_manifest.json"

REPORT_SCHEMA_VERSION = "1.0.0"
ARTIFACT_ID = "terraflux-c1-e0-embedded-terrace-screening-report"
SOURCE_RELEASE = "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED"
SEAL = "C1 E0 | ALINHAMENTO CONCEITUAL NÃO DIMENSIONADO | NÃO USAR PARA GUIAMENTO"
A4_LANDSCAPE_IN = (11.6929, 8.2677)
A4_LANDSCAPE_PT = (841.8898, 595.2756)
EXPECTED_SOURCE_LAYERS = {
    "terrace_alignment_candidates",
    "interterrace_strips",
    "row_candidates",
    "candidate_summary",
}
REQUIRED_TEXT_TOKENS = (
    "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED",
    "SCREENING_ONLY_PCE_PCX_UNCONFIRMED",
    "EMBUTIDA_TI",
    "EMBUTIDA_TD",
    "PCE: NOT_EVALUATED",
    "PCX: NOT_EVALUATED",
    "HYDRAULIC_UNCONFIRMED",
    "NOT_GENERATED_RECEIVER_MISSING",
    "NOT_AUTHORIZED",
    "CF0C_OPERACAO",
    "FAIL-CLOSED",
    "NÃO USAR PARA GUIAMENTO",
)
PROHIBITED_EXECUTIVE_CLAIMS = (
    "APROVADO PARA GUIAMENTO",
    "LIBERADO PARA GUIAMENTO",
    "APROVADO PARA PLANTIO",
    "PROJETO EXECUTIVO APROVADO",
    "LIBERADO PARA EXECUÇÃO",
    "TERRAÇO DIMENSIONADO",
)

INK = "#18231f"
DEEP = "#184536"
GREEN = "#178654"
MAGENTA = "#c82e81"
BLUE = "#267ba6"
YELLOW = "#c79520"
RED = "#bd4338"
GRAY = "#65716b"
LINE = "#bdc7c0"
PALE = "#f4f7f3"
LIGHT_GREEN = "#dfeee5"
LIGHT_RED = "#f5e5e2"

mpl.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 8.0,
        "axes.titlesize": 9.5,
        "axes.labelsize": 7.0,
        "xtick.labelsize": 6.0,
        "ytick.labelsize": 6.0,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
    }
)


class ReportError(RuntimeError):
    """Raised when the C1 screening report cannot be published safely."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReportError(message)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError(f"Invalid JSON input: {path}") from exc
    require(isinstance(payload, dict), f"JSON input must be an object: {path}")
    return payload


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def resolve_reference(value: str, owner_path: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    repository_candidate = (ROOT / path).resolve()
    if repository_candidate.exists():
        return repository_candidate
    return (owner_path.parent / path).resolve()


def file_record(path: Path, *, published_path: Path | None = None) -> dict[str, Any]:
    return {
        "path": relative_path(published_path or path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def fmt(value: Any, decimals: int = 1, suffix: str = "") -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(number):
        return "—"
    return f"{number:,.{decimals}f}{suffix}".replace(",", "X").replace(".", ",").replace("X", ".")


def wrapped(value: Any, width: int = 74) -> list[str]:
    lines: list[str] = []
    for paragraph in str(value).splitlines() or [str(value)]:
        lines.extend(textwrap.wrap(paragraph, width=width, break_long_words=False) or [""])
    return lines


def candidate_diagnostics(candidate: dict[str, Any]) -> dict[str, float | None]:
    metrics = candidate["metrics"]
    source_count = int(metrics["source_cf0c_row_count"])
    segment_count = int(metrics["diagnostic_segment_count"])
    source_length = float(metrics["source_cf0c_total_length_m"])
    diagnostic_length = float(metrics["diagnostic_total_length_m"])
    return {
        "fragmentation_factor": segment_count / source_count if source_count else None,
        "retained_length_pct": 100.0 * diagnostic_length / source_length if source_length else None,
        "split_source_row_pct": (
            100.0 * float(metrics["split_source_row_count"]) / source_count if source_count else None
        ),
    }


def assert_reportable_source(manifest: dict[str, Any]) -> None:
    require(manifest.get("release") == SOURCE_RELEASE, "Unexpected C1 source release.")
    require(
        manifest.get("stage_status")
        in {"SCREENING_ONLY_PCE_PCX_UNCONFIRMED", "NO_GEOMETRIC_PRECURSOR"},
        "Unexpected C1 screening stage status.",
    )
    variants = manifest.get("variant_status", {})
    ti = variants.get("EMBUTIDA_TI", {})
    td = variants.get("EMBUTIDA_TD", {})
    require(ti.get("status") == "GENERATED_SCREENING_ONLY_NOT_DIMENSIONED", "TI status is unsafe.")
    require(ti.get("pce_status") == "NOT_EVALUATED", "TI contains a forbidden PCE claim.")
    require(ti.get("pcx_status") == "NOT_EVALUATED", "TI contains a forbidden PCX claim.")
    require(ti.get("hydraulic_status") == "HYDRAULIC_UNCONFIRMED", "TI hydraulic status is unsafe.")
    require(td.get("status") == "NOT_GENERATED_RECEIVER_MISSING", "TD must remain not generated.")
    require(td.get("geometry_count") == 0, "TD geometry exists without a verified receiver.")
    require(td.get("pce_status") == "NOT_EVALUATED", "TD contains a forbidden PCE claim.")
    require(td.get("pcx_status") == "NOT_EVALUATED", "TD contains a forbidden PCX claim.")
    require(td.get("hydraulic_status") == "HYDRAULIC_UNCONFIRMED", "TD hydraulic status is unsafe.")
    assumptions = manifest.get("assumptions", {})
    require(assumptions.get("parameter_class") == "E0_ASSUMPTION", "Sensitivity values lost E0 status.")
    require(
        assumptions.get("selection_meaning") == "GEOMETRIC_SENSITIVITY_GRID_ONLY",
        "Sensitivity grid gained design meaning.",
    )
    require(assumptions.get("row_source_candidate_id") == "CF0C_OPERACAO", "Unexpected row source.")
    qa = manifest.get("qa", {})
    for key in (
        "invalid_geometry_count",
        "non_3d_line_count",
        "forbidden_td_geometry_count",
        "hydraulic_pass_claim_count",
        "guidance_authorized_claim_count",
    ):
        require(qa.get(key) == 0, f"Source QA is unsafe: {key}={qa.get(key)}")
    require(
        qa.get("power_inventory_status") in {"PROVIDED", "DECLARED_NONE", "NOT_REVIEWED"},
        "Invalid overhead-power inventory status.",
    )
    if qa.get("power_inventory_status") == "DECLARED_NONE":
        require(
            qa.get("power_constraint_effect") == "NOT_APPLICABLE_DECLARED_NONE",
            "Declared-none power was converted into a blocker.",
        )
    for candidate in manifest.get("candidates", []):
        require(candidate.get("variant") == "EMBUTIDA_TI", "Non-TI geometry entered the candidate matrix.")
        require(candidate.get("pce_status") == "NOT_EVALUATED", "Candidate contains a PCE claim.")
        require(candidate.get("pcx_status") == "NOT_EVALUATED", "Candidate contains a PCX claim.")
        require(candidate.get("hydraulic_status") == "HYDRAULIC_UNCONFIRMED", "Candidate hydraulic claim changed.")
        require(candidate.get("guidance_status") == "NOT_AUTHORIZED", "Candidate authorizes guidance.")


def build_page_plan(manifest: dict[str, Any], *, candidates_per_page: int = 12, offsets_per_page: int = 4) -> list[dict[str, Any]]:
    candidates = sorted(
        manifest.get("candidates", []),
        key=lambda item: (str(item["field_id"]), float(item["vertical_interval_m"]), float(item["offset_fraction"])),
    )
    intervals = [float(value) for value in manifest["assumptions"]["vertical_interval_candidates_m"]]
    offsets = [float(value) for value in manifest["assumptions"]["offset_fractions"]]
    pages: list[dict[str, Any]] = [
        {"title": "Triagem C1 de curva embutida", "kind": "cover"},
        {"title": "Como ler este produto", "kind": "scope"},
        {"title": "QA, insumos e restrições", "kind": "qa"},
        {"title": "Sensibilidade por intervalo vertical", "kind": "interval_summary"},
    ]
    chunks = [candidates[index : index + candidates_per_page] for index in range(0, len(candidates), candidates_per_page)] or [[]]
    for index, chunk in enumerate(chunks, start=1):
        pages.append(
            {
                "title": f"Comparativo TI {index}/{len(chunks)}",
                "kind": "candidate_table",
                "candidate_ids": [item["candidate_id"] for item in chunk],
            }
        )
    offset_chunks = [offsets[index : index + offsets_per_page] for index in range(0, len(offsets), offsets_per_page)] or [[]]
    for interval in intervals:
        for index, chunk in enumerate(offset_chunks, start=1):
            suffix = f" {index}/{len(offset_chunks)}" if len(offset_chunks) > 1 else ""
            pages.append(
                {
                    "title": f"Mapas TI | intervalo {interval:g} m{suffix}",
                    "kind": "interval_maps",
                    "vertical_interval_m": interval,
                    "offsets": chunk,
                }
            )
    pages.extend(
        [
            {"title": "TD não gerada", "kind": "td"},
            {"title": "Gaps para dimensionamento", "kind": "gaps"},
            {"title": "Rastreabilidade e conclusão", "kind": "traceability"},
        ]
    )
    for number, page in enumerate(pages, start=1):
        page["page"] = number
    return pages


def load_vector_parts(path: Path) -> dict[str, dict[str, list[np.ndarray]]]:
    datasource = ogr.Open(str(path), 0)
    require(datasource is not None, f"Could not open C1 GeoPackage: {path}")
    names = {datasource.GetLayerByIndex(index).GetName() for index in range(datasource.GetLayerCount())}
    require(names == EXPECTED_SOURCE_LAYERS, f"Unexpected C1 GeoPackage layers: {sorted(names)}")
    result: dict[str, dict[str, list[np.ndarray]]] = {
        "terrace_alignment_candidates": defaultdict(list),
        "interterrace_strips": defaultdict(list),
        "row_candidates": defaultdict(list),
    }
    for layer_name in result:
        layer = datasource.GetLayerByName(layer_name)
        for feature in layer:
            candidate_id = str(feature.GetField("candidate_id"))
            geometry = feature.GetGeometryRef()
            if geometry is None or geometry.IsEmpty():
                continue
            result[layer_name][candidate_id].extend(geometry_xy_parts(geometry))
    datasource = None
    return {layer: dict(by_candidate) for layer, by_candidate in result.items()}


def geometry_xy_parts(geometry: ogr.Geometry) -> list[np.ndarray]:
    flattened = ogr.GT_Flatten(geometry.GetGeometryType())
    if flattened in {ogr.wkbLineString, ogr.wkbLinearRing}:
        points = np.asarray(geometry.GetPoints(), dtype=float)
        return [points[:, :2]] if len(points) >= 2 else []
    if flattened == ogr.wkbPolygon:
        parts: list[np.ndarray] = []
        for index in range(geometry.GetGeometryCount()):
            parts.extend(geometry_xy_parts(geometry.GetGeometryRef(index)))
        return parts
    parts = []
    for index in range(geometry.GetGeometryCount()):
        parts.extend(geometry_xy_parts(geometry.GetGeometryRef(index)))
    return parts


def validate_source_map(path: Path) -> dict[str, Any]:
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            extrema = ImageStat.Stat(rgb.resize((128, 128))).extrema
    except OSError as exc:
        raise ReportError(f"C1 screening map is unreadable: {path}") from exc
    require(width >= 500 and height >= 300, "C1 screening map is too small for the report.")
    nonblank_channels = sum(1 for low, high in extrema if high - low >= 8)
    require(nonblank_channels >= 2, "C1 screening map appears blank or monochromatic.")
    return {"width_px": width, "height_px": height, "nonblank_channels": nonblank_channels}


def add_header(fig: plt.Figure, page: dict[str, Any], page_count: int) -> None:
    fig.add_artist(Rectangle((0, 0.938), 1, 0.062, transform=fig.transFigure, color=DEEP, zorder=0))
    fig.text(0.032, 0.969, f"{page['page']:02d}  {page['title']}", color="white", size=12.5, weight="bold", va="center")
    fig.text(0.968, 0.969, SEAL, color="white", size=6.4, weight="bold", ha="right", va="center")
    fig.add_artist(Rectangle((0.03, 0.035), 0.94, 0.0012, transform=fig.transFigure, color=LINE))
    fig.text(
        0.033,
        0.018,
        "Produto de triagem geométrica. Exige validação de campo, hidráulica e responsabilidade profissional.",
        color=GRAY,
        size=6.5,
    )
    fig.text(0.95, 0.018, f"Página {page['page']} de {page_count}", color=GRAY, size=6.5, ha="right")


def add_panel(
    fig: plt.Figure,
    box: tuple[float, float, float, float],
    title: str,
    body: Iterable[str],
    *,
    accent: str = GREEN,
    face: str = PALE,
    font_size: float = 7.6,
    width: int = 70,
) -> None:
    x, y, w, h = box
    fig.add_artist(Rectangle((x, y), w, h, transform=fig.transFigure, facecolor=face, edgecolor=LINE, linewidth=0.7))
    fig.add_artist(Rectangle((x, y + h - 0.029), w, 0.029, transform=fig.transFigure, facecolor=accent, edgecolor=accent))
    fig.text(x + 0.011, y + h - 0.0145, title, color="white", size=7.6, weight="bold", va="center")
    text_lines: list[str] = []
    for value in body:
        text_lines.extend(wrapped(value, width=width))
    available = h - 0.052
    step = min(0.023, available / max(len(text_lines), 1))
    cursor = y + h - 0.048
    for value in text_lines:
        if cursor < y + 0.012:
            break
        fig.text(x + 0.012, cursor, value, color=INK, size=min(font_size, step * 430), va="top")
        cursor -= step


def new_page(page: dict[str, Any], page_count: int) -> plt.Figure:
    figure = plt.figure(figsize=A4_LANDSCAPE_IN)
    add_header(figure, page, page_count)
    return figure


def render_cover(pdf: PdfPages, page: dict[str, Any], page_count: int, manifest: dict[str, Any], source_map: Path) -> None:
    fig = new_page(page, page_count)
    fig.text(0.045, 0.855, "CURVA EMBUTIDA", size=10, color=GREEN, weight="bold")
    fig.text(0.045, 0.785, "Triagem geométrica\nde alternativas TI", size=27, color=INK, weight="bold", va="top")
    fig.text(
        0.045,
        0.595,
        "Intervalos verticais e offsets comparados sobre o relevo,\ncom linhas CF0C recortadas para diagnóstico operacional.",
        size=11,
        color=GRAY,
        va="top",
    )
    qa = manifest["qa"]
    add_panel(
        fig,
        (0.045, 0.31, 0.23, 0.18),
        "ESTÁGIO",
        [manifest["stage_status"], SOURCE_RELEASE, "FAIL-CLOSED"],
        accent=RED,
        face=LIGHT_RED,
        font_size=7.2,
        width=38,
    )
    add_panel(
        fig,
        (0.29, 0.31, 0.20, 0.18),
        "MATRIZ TI",
        [
            f"{qa['ti_candidate_count']} alternativas",
            f"{qa['field_count']} talhões",
            f"{qa['axis_count']} eixos conceituais",
            f"{qa['diagnostic_row_segment_count']} segmentos diagnósticos",
        ],
        accent=MAGENTA,
        width=34,
    )
    fig.text(0.045, 0.245, "PCE: NOT_EVALUATED  |  PCX: NOT_EVALUATED", color=RED, size=9, weight="bold")
    fig.text(0.045, 0.212, "Hidráulica: HYDRAULIC_UNCONFIRMED", color=RED, size=9, weight="bold")
    fig.text(0.045, 0.179, "Guiamento: NOT_AUTHORIZED", color=RED, size=9, weight="bold")
    axis = fig.add_axes([0.53, 0.09, 0.43, 0.80])
    axis.imshow(plt.imread(source_map))
    axis.set_title("Mapa de conjunto emitido pelo estágio C1 E0", loc="left", fontsize=8.2, color=INK, weight="bold")
    axis.set_axis_off()
    pdf.savefig(fig)
    plt.close(fig)


def render_scope(pdf: PdfPages, page: dict[str, Any], page_count: int, manifest: dict[str, Any]) -> None:
    fig = new_page(page, page_count)
    add_panel(
        fig,
        (0.035, 0.57, 0.29, 0.31),
        "O QUE FOI GERADO",
        [
            "EMBUTIDA_TI: matriz de isolinhas de sensibilidade por intervalo vertical e offset.",
            "Faixas entre eixos: partições topológicas para medir fragmentação; não representam largura ou seção construída.",
            "Linhas verdes: recortes de CF0C_OPERACAO; servem para comparar tiros, não para resolver sulcação dentro de cada faixa.",
        ],
        accent=GREEN,
        width=48,
    )
    add_panel(
        fig,
        (0.355, 0.57, 0.29, 0.31),
        "O QUE NÃO FOI GERADO",
        [
            "EMBUTIDA_TD: NOT_GENERATED_RECEIVER_MISSING.",
            "PCE: NOT_EVALUATED. PCX: NOT_EVALUATED.",
            "Nenhuma seção embutida, cota de projeto, corte/aterro, superfície proposta ou verificação hidráulica.",
            "Nenhum arquivo de orientação de máquina: NOT_AUTHORIZED.",
        ],
        accent=RED,
        face=LIGHT_RED,
        width=48,
    )
    add_panel(
        fig,
        (0.675, 0.57, 0.29, 0.31),
        "COMO COMPARAR",
        [
            "Mais eixos e maior extensão conceitual tendem a ampliar a intervenção potencial.",
            "Mais segmentos por linha-fonte e maior percentual de linhas partidas indicam maior fragmentação diagnóstica.",
            "P50/P95 maiores indicam tiros diagnósticos mais longos; ainda faltam manobras, roteamento e colheitabilidade validados.",
        ],
        accent=BLUE,
        width=48,
    )
    add_panel(
        fig,
        (0.035, 0.10, 0.93, 0.40),
        "REGRA DE DECISÃO FAIL-CLOSED",
        [
            "Este caderno não escolhe automaticamente uma alternativa. A geometria permite enxergar o efeito relativo de intervalo e offset, mas não demonstra espaçamento admissível (PCE), espaçamento crítico (PCX), capacidade, estabilidade do solo, segurança do receptor ou desempenho de colheita.",
            f"Status de origem: {manifest['stage_status']}.",
            "Estados fail-closed previstos: SCREENING_ONLY_PCE_PCX_UNCONFIRMED ou NO_GEOMETRIC_PRECURSOR.",
            "Uma alternativa só pode avançar quando os dados locais e os solucionadores correspondentes estiverem presentes; até lá, todas permanecem hipóteses E0.",
        ],
        accent=YELLOW,
        width=145,
        font_size=8.4,
    )
    pdf.savefig(fig)
    plt.close(fig)


def render_qa(pdf: PdfPages, page: dict[str, Any], page_count: int, manifest: dict[str, Any], source_records: dict[str, Any]) -> None:
    fig = new_page(page, page_count)
    qa = manifest["qa"]
    counts = manifest["layer_counts"]
    power_status = qa["power_inventory_status"]
    power_body = [f"Inventário: {power_status}", f"Efeito: {qa['power_constraint_effect']}"]
    if power_status == "DECLARED_NONE":
        power_body.append("A rede aérea foi declarada inexistente pelo cliente; não é insumo pendente nesta execução.")
    elif power_status == "PROVIDED":
        power_body.append("Há inventário fornecido; a etapa de barreira deve ser aplicada antes de qualquer avanço espacial.")
    else:
        power_body.append("Inventário não revisado; tratar como pendência até declaração ou dado válido.")
    add_panel(
        fig,
        (0.035, 0.57, 0.29, 0.31),
        "QA GEOMÉTRICO",
        [
            f"Geometrias inválidas: {qa['invalid_geometry_count']}",
            f"Linhas sem Z finito: {qa['non_3d_line_count']}",
            f"Geometrias TD proibidas: {qa['forbidden_td_geometry_count']}",
            f"Claims hidráulicos indevidos: {qa['hydraulic_pass_claim_count']}",
            f"Claims de guiamento indevidos: {qa['guidance_authorized_claim_count']}",
        ],
        accent=GREEN,
        width=46,
    )
    add_panel(
        fig,
        (0.355, 0.57, 0.29, 0.31),
        "CAMADAS PUBLICADAS",
        [
            f"Eixos conceituais: {counts['terrace_alignment_candidates']}",
            f"Faixas topológicas: {counts['interterrace_strips']}",
            f"Linhas diagnósticas: {counts['row_candidates']}",
            f"Resumos TI + TD: {counts['candidate_summary']}",
        ],
        accent=MAGENTA,
        width=46,
    )
    add_panel(
        fig,
        (0.675, 0.57, 0.29, 0.31),
        "REDE ELÉTRICA AÉREA",
        power_body,
        accent=GREEN if power_status == "DECLARED_NONE" else RED,
        face=LIGHT_GREEN if power_status == "DECLARED_NONE" else LIGHT_RED,
        width=48,
    )
    input_lines = []
    for key, item in source_records.items():
        input_lines.append(f"{key}: {item['path']} | sha256 {item['sha256'][:16]}…")
    input_lines.extend(
        [
            f"Inventário geral de restrições: {qa['general_constraint_inventory_status']}.",
            "A ausência declarada de rede elétrica não resolve cursos d'água, APP, carreadores, drenagem, obstáculos, servidões ou receptores.",
        ]
    )
    add_panel(fig, (0.035, 0.10, 0.93, 0.40), "INSUMOS E ESCOPO DE RESTRIÇÕES", input_lines, accent=BLUE, width=145, font_size=7.6)
    pdf.savefig(fig)
    plt.close(fig)


def interval_summary_rows(manifest: dict[str, Any]) -> list[list[str]]:
    grouped: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for candidate in manifest["candidates"]:
        grouped[float(candidate["vertical_interval_m"])].append(candidate)
    rows: list[list[str]] = []
    for interval in sorted(grouped):
        candidates = grouped[interval]
        source_rows = sum(int(item["metrics"]["source_cf0c_row_count"]) for item in candidates)
        segments = sum(int(item["metrics"]["diagnostic_segment_count"]) for item in candidates)
        source_length = sum(float(item["metrics"]["source_cf0c_total_length_m"]) for item in candidates)
        retained = sum(float(item["metrics"]["diagnostic_total_length_m"]) for item in candidates)
        p50_values = [float(item["metrics"]["diagnostic_length_p50_m"]) for item in candidates if item["metrics"]["diagnostic_length_p50_m"] is not None]
        rows.append(
            [
                fmt(interval, 1),
                str(len(candidates)),
                str(sum(int(item["axis_count"]) for item in candidates)),
                fmt(sum(float(item["metrics"]["terrace_total_length_m"]) for item in candidates) / 1000.0, 2),
                str(segments),
                fmt(segments / source_rows if source_rows else None, 2),
                fmt(np.median(p50_values) if p50_values else None, 1),
                fmt(100.0 * retained / source_length if source_length else None, 1),
                str(sum(int(item["metrics"]["split_source_row_count"]) for item in candidates)),
            ]
        )
    return rows


def add_table(axis: plt.Axes, rows: list[list[str]], columns: list[str], *, font_size: float = 6.5, widths: list[float] | None = None) -> None:
    axis.set_axis_off()
    table = axis.table(cellText=rows, colLabels=columns, loc="upper left", cellLoc="center", colLoc="center", colWidths=widths)
    table.auto_set_font_size(False)
    table.set_fontsize(font_size)
    table.scale(1.0, 1.48)
    for (row, _column), cell in table.get_celld().items():
        cell.set_edgecolor(LINE)
        cell.set_linewidth(0.45)
        if row == 0:
            cell.set_facecolor(DEEP)
            cell.get_text().set_color("white")
            cell.get_text().set_weight("bold")
        else:
            cell.set_facecolor("white" if row % 2 else PALE)
            cell.get_text().set_color(INK)


def render_interval_summary(pdf: PdfPages, page: dict[str, Any], page_count: int, manifest: dict[str, Any]) -> None:
    fig = new_page(page, page_count)
    fig.text(
        0.035,
        0.905,
        "Agregação de todas as combinações de offset e talhão. Os totais servem para sensibilidade relativa, não para eleger espaçamento.",
        color=GRAY,
        size=8.0,
    )
    axis = fig.add_axes([0.035, 0.49, 0.93, 0.37])
    add_table(
        axis,
        interval_summary_rows(manifest),
        ["VI\n(m)", "alternativas", "eixos", "eixo\n(km)", "segmentos", "frag.\nseg/fonte", "tiro P50\n(m)", "retenção\n(%)", "fontes\npartidas"],
        font_size=7.2,
        widths=[0.07, 0.10, 0.08, 0.09, 0.10, 0.11, 0.11, 0.10, 0.11],
    )
    add_panel(
        fig,
        (0.035, 0.10, 0.45, 0.31),
        "LEITURA OPERACIONAL",
        [
            "Fragmentação = segmentos diagnósticos / linhas-fonte CF0C. Valor maior sugere mais cortes produzidos pelos eixos conceituais.",
            "Retenção = comprimento diagnóstico após recorte / comprimento das linhas-fonte. Não mede área plantável nem rendimento de campo.",
            "Tiro P50 é a mediana observada dos segmentos, sem roteamento, manobra, transposição de carreador ou validação de colheita.",
        ],
        accent=BLUE,
        width=72,
    )
    add_panel(
        fig,
        (0.515, 0.10, 0.45, 0.31),
        "LIMITE DA COMPARAÇÃO",
        [
            "O intervalo vertical desta rodada é E0_ASSUMPTION e não PCE calculado.",
            "O conjunto não contém PCX, capacidade de armazenamento/escoamento, risco erosivo, seção construtiva ou receptor verificado.",
            "Qualquer preferência aparente precisa ser reavaliada após a camada hidráulica e as regras agronômicas locais.",
        ],
        accent=RED,
        face=LIGHT_RED,
        width=72,
    )
    pdf.savefig(fig)
    plt.close(fig)


def candidate_table_rows(candidates: Sequence[dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for candidate in candidates:
        diagnostics = candidate_diagnostics(candidate)
        metrics = candidate["metrics"]
        rows.append(
            [
                str(candidate["field_id"]),
                fmt(candidate["vertical_interval_m"], 1),
                fmt(candidate["offset_fraction"], 2),
                str(candidate["axis_count"]),
                str(candidate["strip_count"]),
                fmt(metrics["terrace_total_length_m"] / 1000.0, 2),
                str(metrics["diagnostic_segment_count"]),
                fmt(metrics["diagnostic_length_p50_m"], 1),
                fmt(metrics["diagnostic_length_p95_m"], 1),
                fmt(diagnostics["fragmentation_factor"], 2),
                fmt(diagnostics["retained_length_pct"], 1),
                str(metrics["split_source_row_count"]),
            ]
        )
    return rows


def render_candidate_table(pdf: PdfPages, page: dict[str, Any], page_count: int, manifest: dict[str, Any]) -> None:
    by_id = {item["candidate_id"]: item for item in manifest["candidates"]}
    candidates = [by_id[item] for item in page["candidate_ids"]]
    fig = new_page(page, page_count)
    fig.text(
        0.035,
        0.905,
        "Todas as métricas são diagnósticas. Eixos magenta e recortes verdes não constituem projeto, solução por faixa ou arquivo de máquina.",
        size=7.8,
        color=GRAY,
    )
    axis = fig.add_axes([0.025, 0.22, 0.95, 0.64])
    add_table(
        axis,
        candidate_table_rows(candidates),
        ["talhão", "VI\n(m)", "offset", "eixos", "faixas", "eixo\n(km)", "seg.", "P50\n(m)", "P95\n(m)", "frag.", "ret.\n(%)", "fontes\npartidas"],
        font_size=6.2,
        widths=[0.075, 0.065, 0.07, 0.065, 0.065, 0.075, 0.075, 0.075, 0.075, 0.075, 0.075, 0.085],
    )
    fig.text(
        0.035,
        0.145,
        "frag. = segmentos / linhas-fonte CF0C  |  ret. = comprimento dos segmentos / comprimento-fonte  |  P50/P95 = comprimento de tiro diagnóstico",
        size=7.2,
        color=INK,
        weight="bold",
    )
    fig.text(
        0.035,
        0.105,
        "Status comum: PCE/PCX NOT_EVALUATED · hidráulica HYDRAULIC_UNCONFIRMED · operação DIAGNOSTIC_ONLY_NOT_ROUTED · guiamento NOT_AUTHORIZED",
        size=7.2,
        color=RED,
        weight="bold",
    )
    pdf.savefig(fig)
    plt.close(fig)


def add_geometry_collections(axis: plt.Axes, candidate_ids: set[str], vector_parts: dict[str, dict[str, list[np.ndarray]]]) -> bool:
    polygons = [part for candidate_id in candidate_ids for part in vector_parts["interterrace_strips"].get(candidate_id, [])]
    rows = [part for candidate_id in candidate_ids for part in vector_parts["row_candidates"].get(candidate_id, [])]
    axes = [part for candidate_id in candidate_ids for part in vector_parts["terrace_alignment_candidates"].get(candidate_id, [])]
    if polygons:
        axis.add_collection(PolyCollection(polygons, facecolors="#dfe8df", edgecolors="#a8b7ab", linewidths=0.22, alpha=0.55))
    if rows:
        axis.add_collection(LineCollection(rows, colors=GREEN, linewidths=0.24, alpha=0.55))
    if axes:
        axis.add_collection(LineCollection(axes, colors=MAGENTA, linewidths=0.82, alpha=0.95))
    all_parts = polygons + rows + axes
    if not all_parts:
        return False
    x_values = np.concatenate([part[:, 0] for part in all_parts if len(part)])
    y_values = np.concatenate([part[:, 1] for part in all_parts if len(part)])
    if len(x_values) == 0 or len(y_values) == 0:
        return False
    x_min, x_max = float(np.min(x_values)), float(np.max(x_values))
    y_min, y_max = float(np.min(y_values)), float(np.max(y_values))
    padding = 0.035 * max(x_max - x_min, y_max - y_min, 1.0)
    axis.set_xlim(x_min - padding, x_max + padding)
    axis.set_ylim(y_min - padding, y_max + padding)
    axis.set_aspect("equal", adjustable="box")
    return True


def render_interval_maps(
    pdf: PdfPages,
    page: dict[str, Any],
    page_count: int,
    manifest: dict[str, Any],
    vector_parts: dict[str, dict[str, list[np.ndarray]]],
) -> None:
    fig = new_page(page, page_count)
    interval = float(page["vertical_interval_m"])
    offsets = [float(value) for value in page["offsets"]]
    fig.text(
        0.035,
        0.905,
        "Magenta: isolinhas de sensibilidade TI. Verde: linhas CF0C recortadas. Fundo claro: faixas topológicas, não seções construídas.",
        color=GRAY,
        size=7.8,
    )
    columns = 2
    rows_count = max(1, math.ceil(len(offsets) / columns))
    grid = fig.add_gridspec(rows_count, columns, left=0.035, right=0.965, bottom=0.09, top=0.86, hspace=0.16, wspace=0.08)
    candidates = manifest["candidates"]
    for index in range(rows_count * columns):
        axis = fig.add_subplot(grid[index // columns, index % columns])
        if index >= len(offsets):
            axis.set_axis_off()
            continue
        offset = offsets[index]
        selected = [
            item
            for item in candidates
            if math.isclose(float(item["vertical_interval_m"]), interval, abs_tol=1e-9)
            and math.isclose(float(item["offset_fraction"]), offset, abs_tol=1e-9)
        ]
        selected_ids = {item["candidate_id"] for item in selected}
        visible = add_geometry_collections(axis, selected_ids, vector_parts)
        axis.set_facecolor("#f7f8f6")
        axis.set_xticks([])
        axis.set_yticks([])
        for spine in axis.spines.values():
            spine.set_color(LINE)
        total_axes = sum(int(item["axis_count"]) for item in selected)
        total_segments = sum(int(item["diagnostic_row_segment_count"]) for item in selected)
        p50_values = [float(item["metrics"]["diagnostic_length_p50_m"]) for item in selected if item["metrics"]["diagnostic_length_p50_m"] is not None]
        axis.set_title(
            f"Offset {offset:.2f} | {len(selected)} talhão(ões) | {total_axes} eixos | {total_segments} segmentos | P50 mediano {fmt(np.median(p50_values) if p50_values else None, 1)} m",
            loc="left",
            fontsize=7.2,
            weight="bold",
            color=INK,
            pad=4,
        )
        if not visible:
            axis.text(0.5, 0.5, "NO_GEOMETRIC_PRECURSOR", transform=axis.transAxes, ha="center", va="center", color=RED, weight="bold")
    pdf.savefig(fig)
    plt.close(fig)


def render_td(pdf: PdfPages, page: dict[str, Any], page_count: int, manifest: dict[str, Any]) -> None:
    fig = new_page(page, page_count)
    status = manifest["variant_status"]["EMBUTIDA_TD"]
    fig.text(0.045, 0.84, "EMBUTIDA_TD", color=RED, size=11, weight="bold")
    fig.text(0.045, 0.765, status["status"], color=INK, size=22, weight="bold")
    fig.text(0.045, 0.708, "Nenhuma geometria TD foi criada: geometry_count = 0.", color=GRAY, size=10)
    records = manifest["td_not_generated"]
    half = max(1, math.ceil(len(records) / 2))
    for index, chunk in enumerate((records[:half], records[half:])):
        if not chunk:
            continue
        body = []
        for record in chunk:
            body.append(f"Talhão {record['field_id']} | {record['status']} | {', '.join(record['blocker_codes'])}")
        add_panel(fig, (0.045 + index * 0.47, 0.33, 0.44, 0.29), f"REGISTROS TD {index + 1}", body, accent=RED, face=LIGHT_RED, width=69)
    add_panel(
        fig,
        (0.045, 0.10, 0.91, 0.16),
        "CONDIÇÃO PARA GERAR TD",
        [
            f"Motivo contratual: {status['reason']}.",
            "É necessário um receptor verificado, regra de gradiente longitudinal, ligação até o receptor e checagem hidráulica. O traçado não pode ser inferido apenas do MDE.",
        ],
        accent=YELLOW,
        width=140,
    )
    pdf.savefig(fig)
    plt.close(fig)


def render_gaps(pdf: PdfPages, page: dict[str, Any], page_count: int, manifest: dict[str, Any]) -> None:
    fig = new_page(page, page_count)
    limitations = set(manifest["release_limitations"])
    add_panel(
        fig,
        (0.035, 0.56, 0.29, 0.32),
        "SOLO E CHUVA",
        [
            "Levantamento de solos/unidades de manejo e propriedades hidráulicas/erosivas com confiança conhecida.",
            "Chuva de projeto/IDF e período de retorno definidos para a política local.",
            "Modelo e parâmetros locais necessários para calcular PCE e PCX; não substituir por intervalo E0.",
            "PCE: NOT_EVALUATED | PCX: NOT_EVALUATED.",
        ],
        accent=BLUE,
        width=48,
    )
    add_panel(
        fig,
        (0.355, 0.56, 0.29, 0.32),
        "HIDRÁULICA E CONSTRUÇÃO",
        [
            "Seção alvo, capacidade, armazenamento/escoamento, folga, estabilidade e critério de extravasamento.",
            "Receptores e saídas seguros, verificados em campo e conectados à drenagem.",
            "Superfície proposta, volumes de corte/aterro e compatibilidade com equipamentos.",
            "Status atual: HYDRAULIC_UNCONFIRMED.",
        ],
        accent=MAGENTA,
        width=48,
    )
    add_panel(
        fig,
        (0.675, 0.56, 0.29, 0.32),
        "OPERAÇÃO E RESTRIÇÕES",
        [
            "Inventário geral de obstáculos, APP, hidrografia, carreadores, linhas enterradas, servidões e cruzamentos.",
            "Raio mínimo e envelope real dos conjuntos, cabeceiras, manobras, transições e roteamento por faixa.",
            "Validação de tiros, colheitabilidade, logística e inspeção profissional no campo.",
            f"Inventário geral: {manifest['qa']['general_constraint_inventory_status']}.",
        ],
        accent=YELLOW,
        width=48,
    )
    add_panel(
        fig,
        (0.035, 0.10, 0.93, 0.38),
        "LIMITAÇÕES FORMALMENTE GRAVADAS",
        [" · ".join(sorted(limitations)), "O avanço precisa remover bloqueios com evidência; ocultar avisos ou renomear o produto não muda o estágio."],
        accent=RED,
        face=LIGHT_RED,
        width=145,
        font_size=7.2,
    )
    pdf.savefig(fig)
    plt.close(fig)


def render_traceability(
    pdf: PdfPages,
    page: dict[str, Any],
    page_count: int,
    manifest: dict[str, Any],
    source_records: dict[str, Any],
    generated_at: str,
) -> None:
    fig = new_page(page, page_count)
    refs = [
        f"Pedido de projeto: {manifest['project_request_ref']['id']} | {manifest['project_request_ref']['sha256']}",
        f"Pedido de sensibilidade: {manifest['sensitivity_request_ref']['id']} | {manifest['sensitivity_request_ref']['sha256']}",
        f"Manifesto C1: {source_records['source_manifest']['sha256']}",
        f"GeoPackage C1: {source_records['source_geopackage']['sha256']}",
        f"Mapa C1: {source_records['source_map']['sha256']}",
        f"Contrato JSON Schema: {source_records['source_schema']['sha256']}",
        f"Relatório gerado em UTC: {generated_at}",
    ]
    add_panel(fig, (0.035, 0.49, 0.93, 0.39), "RASTREABILIDADE", refs, accent=DEEP, width=145, font_size=7.2)
    add_panel(
        fig,
        (0.035, 0.10, 0.93, 0.31),
        "CONCLUSÃO DESTA RODADA",
        [
            f"Foram comparadas {manifest['qa']['ti_candidate_count']} alternativas EMBUTIDA_TI em {manifest['qa']['field_count']} talhões, com métricas de eixos, faixas, fragmentação e tiros diagnósticos.",
            "A matriz permite eliminar alternativas geometricamente frágeis e direcionar a coleta de dados, mas ainda não sustenta seleção agronômica ou hidráulica.",
            "EMBUTIDA_TD permanece NOT_GENERATED_RECEIVER_MISSING. PCE e PCX permanecem NOT_EVALUATED. O pacote permanece NOT_AUTHORIZED para guiamento.",
            "Próximo gate: evidência local + solucionadores PCE/PCX + receptor/saída + seção/hidráulica + operação por faixa + validação de campo e profissional.",
        ],
        accent=RED,
        face=LIGHT_RED,
        width=145,
        font_size=8.0,
    )
    pdf.savefig(fig)
    plt.close(fig)


def render_report(
    output_pdf: Path,
    pages: Sequence[dict[str, Any]],
    manifest: dict[str, Any],
    source_map: Path,
    source_records: dict[str, Any],
    vector_parts: dict[str, dict[str, list[np.ndarray]]],
    generated_at: str,
) -> None:
    metadata = {
        "Title": "Relatório de Triagem C1 - Curva Embutida E0",
        "Author": "TerraFlux",
        "Subject": SOURCE_RELEASE,
        "Keywords": "C1, curva embutida, TI, TD, PCE, PCX, E0, fail-closed",
        "Creator": "TerraFlux C1 screening report generator",
        "Producer": "Matplotlib PdfPages",
    }
    by_kind = {
        "cover": lambda pdf, item: render_cover(pdf, item, len(pages), manifest, source_map),
        "scope": lambda pdf, item: render_scope(pdf, item, len(pages), manifest),
        "qa": lambda pdf, item: render_qa(pdf, item, len(pages), manifest, source_records),
        "interval_summary": lambda pdf, item: render_interval_summary(pdf, item, len(pages), manifest),
        "candidate_table": lambda pdf, item: render_candidate_table(pdf, item, len(pages), manifest),
        "interval_maps": lambda pdf, item: render_interval_maps(pdf, item, len(pages), manifest, vector_parts),
        "td": lambda pdf, item: render_td(pdf, item, len(pages), manifest),
        "gaps": lambda pdf, item: render_gaps(pdf, item, len(pages), manifest),
        "traceability": lambda pdf, item: render_traceability(pdf, item, len(pages), manifest, source_records, generated_at),
    }
    with PdfPages(output_pdf, metadata=metadata) as pdf:
        for page in pages:
            by_kind[page["kind"]](pdf, page)


def inspect_pdf(pdf_path: Path, pages: Sequence[dict[str, Any]]) -> dict[str, Any]:
    reader = PdfReader(str(pdf_path))
    require(len(reader.pages) == len(pages), "PDF page count differs from the page plan.")
    extracted_pages: list[str] = []
    for expected, pdf_page in zip(pages, reader.pages):
        width = float(pdf_page.mediabox.width)
        height = float(pdf_page.mediabox.height)
        require(width > height, f"Page {expected['page']} is not landscape.")
        require(math.isclose(width, A4_LANDSCAPE_PT[0], abs_tol=2.0), f"Page {expected['page']} is not A4 width.")
        require(math.isclose(height, A4_LANDSCAPE_PT[1], abs_tol=2.0), f"Page {expected['page']} is not A4 height.")
        text = pdf_page.extract_text() or ""
        require(len(text.strip()) >= 100, f"Page {expected['page']} is empty or not searchable.")
        require(SEAL in text, f"Page {expected['page']} lacks the release seal.")
        require(expected["title"] in text, f"Page {expected['page']} title is not searchable.")
        contents = pdf_page.get_contents()
        data = contents.get_data() if contents is not None else b""
        require(len(data) > 250, f"Page {expected['page']} content stream is empty.")
        extracted_pages.append(text)
    extracted = "\n".join(extracted_pages)
    for token in REQUIRED_TEXT_TOKENS:
        require(token in extracted, f"Required searchable token is missing: {token}")
    upper = extracted.upper()
    for claim in PROHIBITED_EXECUTIVE_CLAIMS:
        require(claim not in upper, f"Prohibited claim found in PDF: {claim}")
    return {
        "status": "PASS",
        "pdf_page_count": len(reader.pages),
        "all_pages_a4_landscape": True,
        "all_pages_searchable": True,
        "every_page_has_release_seal": True,
        "required_token_count": len(REQUIRED_TEXT_TOKENS),
        "prohibited_claim_count": 0,
    }


def build_report_manifest(
    *,
    source: dict[str, Any],
    source_verification: dict[str, Any],
    source_records: dict[str, Any],
    staged_pdf: Path,
    output_pdf: Path,
    pages: Sequence[dict[str, Any]],
    generated_at: str,
    preflight: dict[str, Any],
    map_preflight: dict[str, Any],
) -> dict[str, Any]:
    qa = source["qa"]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "artifact_id": ARTIFACT_ID,
        "generated_at": generated_at,
        "inputs": source_records,
        "source_snapshot": {
            "release": source["release"],
            "stage_status": source["stage_status"],
            "source_verification_status": source_verification["status"],
            "project_request_sha256": source["project_request_ref"]["sha256"],
            "sensitivity_request_sha256": source["sensitivity_request_ref"]["sha256"],
            "ti_candidate_count": qa["ti_candidate_count"],
            "td_not_generated_count": qa["td_not_generated_count"],
            "axis_count": qa["axis_count"],
            "strip_count": qa["strip_count"],
            "diagnostic_row_segment_count": qa["diagnostic_row_segment_count"],
        },
        "release_boundary": {
            "document_role": "C1_E0_GEOMETRIC_SENSITIVITY_COMPARISON",
            "release": SOURCE_RELEASE,
            "seal": SEAL,
            "ti_status": source["variant_status"]["EMBUTIDA_TI"]["status"],
            "td_status": source["variant_status"]["EMBUTIDA_TD"]["status"],
            "pce_status": "NOT_EVALUATED",
            "pcx_status": "NOT_EVALUATED",
            "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
            "guidance_status": "NOT_AUTHORIZED",
            "power_inventory_status": qa["power_inventory_status"],
            "power_constraint_effect": qa["power_constraint_effect"],
            "general_constraint_inventory_status": qa["general_constraint_inventory_status"],
        },
        "pdf": file_record(staged_pdf, published_path=output_pdf),
        "page_count": len(pages),
        "pages": list(pages),
        "required_text_tokens": list(REQUIRED_TEXT_TOKENS),
        "prohibited_executive_claims": list(PROHIBITED_EXECUTIVE_CLAIMS),
        "map_preflight": map_preflight,
        "preflight": preflight,
    }


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(temporary, path)


def generate(
    source_manifest_path: Path,
    source_schema_path: Path,
    output_pdf: Path,
    output_manifest: Path,
) -> dict[str, Any]:
    source_manifest_path = source_manifest_path.resolve()
    source_schema_path = source_schema_path.resolve()
    output_pdf = output_pdf.resolve()
    output_manifest = output_manifest.resolve()
    require(source_manifest_path.is_file(), f"C1 source manifest does not exist: {source_manifest_path}")
    require(source_schema_path.is_file(), f"C1 source schema does not exist: {source_schema_path}")
    source_verification = verify_source_package(source_manifest_path, source_schema_path)
    require(source_verification.get("status") == "VERIFIED", "C1 source package did not verify.")
    source = read_json(source_manifest_path)
    assert_reportable_source(source)
    source_geopackage = resolve_reference(source["outputs"]["geopackage"]["path"], source_manifest_path)
    source_map = resolve_reference(source["outputs"]["map"]["path"], source_manifest_path)
    require(source_geopackage.is_file(), f"C1 GeoPackage does not exist: {source_geopackage}")
    require(source_map.is_file(), f"C1 map does not exist: {source_map}")
    source_records = {
        "source_manifest": file_record(source_manifest_path),
        "source_schema": file_record(source_schema_path),
        "source_geopackage": file_record(source_geopackage),
        "source_map": file_record(source_map),
    }
    require(source_records["source_geopackage"]["sha256"] == source["outputs"]["geopackage"]["sha256"], "GeoPackage hash differs from source manifest.")
    require(source_records["source_map"]["sha256"] == source["outputs"]["map"]["sha256"], "Map hash differs from source manifest.")
    map_preflight = validate_source_map(source_map)
    vector_parts = load_vector_parts(source_geopackage)
    pages = build_page_plan(source)
    generated_at = datetime.now(timezone.utc).isoformat()
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="c1-screening-report-", dir=output_pdf.parent) as temporary:
        staged_pdf = Path(temporary) / output_pdf.name
        render_report(staged_pdf, pages, source, source_map, source_records, vector_parts, generated_at)
        preflight = inspect_pdf(staged_pdf, pages)
        report_manifest = build_report_manifest(
            source=source,
            source_verification=source_verification,
            source_records=source_records,
            staged_pdf=staged_pdf,
            output_pdf=output_pdf,
            pages=pages,
            generated_at=generated_at,
            preflight=preflight,
            map_preflight=map_preflight,
        )
        os.replace(staged_pdf, output_pdf)
        atomic_write_json(output_manifest, report_manifest)
    return report_manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--source-schema", type=Path, default=DEFAULT_SOURCE_SCHEMA)
    parser.add_argument("--output-pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--output-manifest", type=Path, default=DEFAULT_REPORT_MANIFEST)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = generate(args.source_manifest, args.source_schema, args.output_pdf, args.output_manifest)
    except (ReportError, RuntimeError, OSError, ValueError) as exc:
        print(f"FAIL: {exc}")
        return 1
    print(
        json.dumps(
            {
                "status": "PASS",
                "pdf": result["pdf"]["path"],
                "page_count": result["page_count"],
                "manifest": relative_path(args.output_manifest),
                "release": result["release_boundary"]["release"],
                "guidance_status": result["release_boundary"]["guidance_status"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
