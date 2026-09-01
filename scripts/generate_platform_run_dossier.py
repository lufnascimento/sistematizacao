#!/usr/bin/env python3
"""Generate a searchable, fail-closed PDF dossier for one platform run.

The dossier summarizes the immutable request/configuration and any available
topography E0, sulcation E0 and continuous-family CF0 artifacts.  It is a
review document only and never promotes screening outputs to agronomic,
hydraulic or machine-guidance authorization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import textwrap
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "1.0.0"
MANIFEST_TYPE = "PLATFORM_RUN_REVIEW_DOSSIER"
RELEASE = "E0_CF0_C1_CONCEPT_SCREENING_REVIEW_ONLY"
SEAL = "TRIAGEM E0/CF0/C1 CONCEITUAL | NAO USAR PARA GUIAMENTO"
A4_LANDSCAPE_IN = (11.6929, 8.2677)

INK = "#17221d"
DEEP = "#1e4a38"
GREEN = "#197a48"
BLUE = "#217da6"
AMBER = "#b77a12"
RED = "#b74335"
GRAY = "#637069"
PALE = "#f4f7f4"
LINE = "#bcc7bf"

BASE_LIMITATIONS = (
    "DOCUMENTO DE TRIAGEM E REVISAO; NAO E PROJETO EXECUTIVO.",
    "NAO USAR PARA GUIAMENTO, PLANTIO, SULCACAO OU OPERACAO DE MAQUINAS.",
    "VALIDACAO HIDRAULICA NAO REALIZADA; RECEPTORES, CHUVA, SOLO E ESTRUTURAS DEVEM SER DIMENSIONADOS.",
    "VALIDACAO AGRONOMICA E RESPONSABILIDADE TECNICA PERMANECEM PENDENTES.",
    "CONFERENCIA DE CAMPO, ACURACIA ALTIMETRICA E INVENTARIO DE RESTRICOES SAO OBRIGATORIOS.",
)

mpl.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 8.2,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
    }
)


class DossierError(RuntimeError):
    """Raised when inputs cannot be published as a safe review dossier."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DossierError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return str(resolved)


def file_record(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    require(resolved.is_file(), f"Arquivo ausente: {resolved}")
    return {
        "path": display_path(resolved),
        "size_bytes": resolved.stat().st_size,
        "sha256": sha256_file(resolved),
    }


def read_json(path: Path, label: str) -> dict[str, Any]:
    require(path.is_file(), f"{label} ausente: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DossierError(f"{label} invalido: {path}") from exc
    require(isinstance(value, dict), f"{label} deve ser um objeto JSON.")
    return value


def iter_nodes(value: Any, trail: str = "") -> Iterable[tuple[str, Any]]:
    yield trail, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from iter_nodes(child, f"{trail}.{key}" if trail else str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_nodes(child, f"{trail}[{index}]")


def assert_safe_source(value: dict[str, Any], label: str) -> None:
    """Reject positive hydraulic/guidance claims in generated product sources."""
    positive = {"TRUE", "PASS", "PASSED", "APPROVED", "AUTHORIZED", "AUTORIZADO", "APROVADO"}
    for trail, node in iter_nodes(value):
        key = trail.rsplit(".", 1)[-1].lower()
        upper = str(node).upper()
        if "guidance_authorized" in key and (node is True or upper in positive):
            raise DossierError(f"{label} contem autorizacao de guiamento insegura em {trail}.")
        if key.endswith("guidance_status") and upper in positive:
            raise DossierError(f"{label} contem status de guiamento inseguro em {trail}.")
        if key.endswith("hydraulic_status") and upper in positive:
            raise DossierError(f"{label} contem status hidraulico inseguro em {trail}.")
        if key.endswith("agronomic_status") and upper in positive:
            raise DossierError(f"{label} contem status agronomico inseguro em {trail}.")
        if any(token in key for token in ("hydraulic_approved", "hydraulic_authorized", "agronomic_approved")):
            if node is True or str(node).upper() in positive:
                raise DossierError(f"{label} contem aprovacao insegura em {trail}.")


def compact(value: Any, limit: int = 70) -> str:
    if value is None:
        return "nao informado"
    if isinstance(value, bool):
        return "sim" if value else "nao"
    if isinstance(value, (list, tuple)):
        text = ", ".join(compact(item, limit=30) for item in value)
    else:
        text = str(value).replace("_", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def nested(value: dict[str, Any], *paths: str, default: Any = None) -> Any:
    for path in paths:
        cursor: Any = value
        for key in path.split("."):
            if not isinstance(cursor, dict) or key not in cursor:
                break
            cursor = cursor[key]
        else:
            if cursor is not None:
                return cursor
    return default


def flatten_scalars(value: Any, prefix: str = "", depth: int = 0) -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    if depth > 4:
        return rows
    if isinstance(value, dict):
        for key, child in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(flatten_scalars(child, name, depth + 1))
    elif isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value) and len(value) <= 8:
            rows.append((prefix, value))
    else:
        rows.append((prefix, value))
    return rows


def aggregate_e0(metrics: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in metrics.get("scenario_summary") or []:
        if isinstance(row, dict) and row.get("scenario_id"):
            grouped[str(row["scenario_id"])].append(row)
    result: list[dict[str, Any]] = []
    for scenario_id, rows in grouped.items():
        segments = sum(float(row.get("segment_count") or 0) for row in rows)
        total_km = sum(float(row.get("total_line_km") or 0) for row in rows)
        radius_violations = sum(int(row.get("radius_violation_line_count") or 0) for row in rows)

        evaluated_rows = [
            row for row in rows
            if isinstance(row.get("radius_violation_line_count"), (int, float))
            and bool(str(row.get("operational_continuity_status") or "").strip())
        ]
        eligible_rows = [
            row for row in evaluated_rows
            if int(row.get("radius_violation_line_count") or 0) == 0
            and not str(row.get("operational_continuity_status") or "").upper().startswith("FAIL")
            and not str(row.get("continuity_blocker_codes") or "").strip()
        ]
        if not evaluated_rows:
            eligibility_status = "NAO AVALIADA"
        elif len(eligible_rows) == len(rows):
            eligibility_status = "GEOMETRICAMENTE ELEGIVEL E0"
        elif eligible_rows:
            eligibility_status = "PARCIALMENTE ELEGIVEL E0"
        else:
            eligibility_status = "NAO ELEGIVEL E0"

        def weighted(key: str, weight: str = "total_line_km") -> float | None:
            pairs = [
                (float(row[key]), float(row.get(weight) or 0))
                for row in rows
                if isinstance(row.get(key), (int, float))
            ]
            denominator = sum(item[1] for item in pairs)
            return sum(value * item_weight for value, item_weight in pairs) / denominator if denominator else None

        result.append(
            {
                "scenario_id": scenario_id,
                "scenario_name": rows[0].get("scenario_name") or scenario_id,
                "field_count": len({str(row.get("field_code")) for row in rows}),
                "total_line_km": total_km,
                "segment_count": int(segments),
                "mean_shot_m": (total_km * 1000 / segments) if segments else None,
                "p90_shot_m": weighted("segment_length_p90_m", "segment_count"),
                "coverage_pct": weighted("coverage_proxy_percent"),
                "grade_p95_pct": weighted("absolute_grade_p95_weighted_percent"),
                "cross_grade_p95_pct": weighted("cross_grade_p95_weighted_percent"),
                "planning_efficiency_pct": weighted("planning_field_efficiency_percent"),
                "eligibility_status": eligibility_status,
                "eligible_field_count": len(eligible_rows),
                "evaluated_field_count": len(evaluated_rows),
                "radius_violation_line_count": radius_violations,
                "hydraulic_status": rows[0].get("hydraulic_status") or "NOT_EVALUATED",
            }
        )
    return sorted(result, key=lambda item: item["scenario_id"])


def aggregate_cf0(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in manifest.get("candidates") or []:
        if isinstance(candidate, dict):
            key = str(candidate.get("candidate_id") or candidate.get("family_id") or "CF0")
            grouped[key].append(candidate)
    result: list[dict[str, Any]] = []
    for candidate_id, rows in grouped.items():
        metrics_rows = [row.get("metrics") or {} for row in rows]
        eligible_rows = [
            row for row in rows
            if str(row.get("geometric_status") or "").upper() in {"PASS", "GEOMETRIC_PASS"}
        ]
        diagnostic_rows = [row for row in rows if row not in eligible_rows]

        def average(key: str) -> float | None:
            values = [float(item[key]) for item in metrics_rows if isinstance(item.get(key), (int, float))]
            return sum(values) / len(values) if values else None

        result.append(
            {
                "candidate_id": candidate_id,
                "block_count": len(rows),
                "eligible_block_count": len(eligible_rows),
                "eligible_row_count": sum(int(row.get("row_count") or 0) for row in eligible_rows),
                "eligible_total_length_km": sum(float(row.get("total_length_m") or 0) for row in eligible_rows) / 1000,
                "diagnostic_block_count": len(diagnostic_rows),
                "diagnostic_row_count": sum(int(row.get("diagnostic_row_count") or 0) for row in diagnostic_rows),
                "diagnostic_total_length_km": sum(float(row.get("diagnostic_total_length_m") or 0) for row in diagnostic_rows) / 1000,
                "spacing_error_p95_m": average("spacing_error_p95_m"),
                "orientation_p95_deg": average("orientation_misalignment_p95_deg"),
                "minimum_radius_m": min(
                    [float(item["minimum_radius_m"]) for item in metrics_rows if isinstance(item.get("minimum_radius_m"), (int, float))],
                    default=None,
                ),
                "hydraulic_status": rows[0].get("hydraulic_status") or "HYDRAULIC_UNCONFIRMED",
            }
        )
    return sorted(result, key=lambda item: item["candidate_id"])


def scope_field_ids(request: dict[str, Any], topography: dict[str, Any] | None) -> tuple[list[str], str]:
    requested = nested(request, "scope.field_ids", default=[])
    if isinstance(requested, list) and requested:
        return [str(item) for item in requested], "REQUEST_SCOPE"
    topographic = nested(topography or {}, "scope.field_ids", default=[])
    if isinstance(topographic, list) and topographic:
        return [str(item) for item in topographic], "TOPOGRAPHY_MANIFEST_FALLBACK"
    return [], "NOT_INFORMED"


def requested_special_products(request: dict[str, Any], run: dict[str, Any], config: dict[str, Any]) -> set[str]:
    values: list[Any] = []
    for source in (
        run.get("product_ids"),
        request.get("product_ids"),
        request.get("selected_product_ids"),
        nested(request, "configuration_snapshot.selected_product_ids", default=[]),
        nested(request, "scenario_request.conservation_macros", default=[]),
        nested(request, "scenario_request.poa_strategy_modes", default=[]),
        config.get("selected_product_ids"),
        nested(config, "conservation.scenario_families", default=[]),
    ):
        if isinstance(source, list):
            values.extend(source)
        elif source is not None:
            values.append(source)
    tokens = {str(value).strip().upper() for value in values if str(value).strip()}
    requested: set[str] = set()
    for token in tokens:
        if token.startswith("C1") or "CURVA_EMBUTIDA" in token or "EMBUTIDA_TI" in token:
            requested.add("C1_CURVA_EMBUTIDA")
        if token.startswith("C2") or "BASE_LARGA" in token or "PASSANTE" in token:
            requested.add("C2_BASE_LARGA_PASSANTE")
        if token == "ESD" or token.startswith("C3") or token.endswith("_ESD"):
            requested.add("C3_ESD")
        if token != "NOT_REQUESTED" and token.startswith("POA"):
            requested.add("POA_LOGISTICS")
    if nested(config, "logistics.poa_enabled", default=False) is True:
        requested.add("POA_LOGISTICS")
    return requested


def c1_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    release = nested(manifest, "release", "source_snapshot.release")
    candidates = manifest.get("candidates") if isinstance(manifest.get("candidates"), list) else []
    snapshot = manifest.get("source_snapshot") if isinstance(manifest.get("source_snapshot"), dict) else {}
    boundary = manifest.get("release_boundary") if isinstance(manifest.get("release_boundary"), dict) else {}
    variant_status = manifest.get("variant_status") if isinstance(manifest.get("variant_status"), dict) else {}
    ti = variant_status.get("EMBUTIDA_TI") if isinstance(variant_status.get("EMBUTIDA_TI"), dict) else {}
    return {
        "release": release,
        "stage_status": nested(manifest, "stage_status", "source_snapshot.stage_status", default="NAO INFORMADO"),
        "candidate_count": len(candidates) if candidates else int(snapshot.get("ti_candidate_count") or 0),
        "axis_count": sum(int(row.get("axis_count") or 0) for row in candidates) if candidates else int(snapshot.get("axis_count") or 0),
        "strip_count": sum(int(row.get("strip_count") or 0) for row in candidates) if candidates else int(snapshot.get("strip_count") or 0),
        "pce_status": ti.get("pce_status") or boundary.get("pce_status") or "NOT_EVALUATED",
        "pcx_status": ti.get("pcx_status") or boundary.get("pcx_status") or "NOT_EVALUATED",
        "hydraulic_status": ti.get("hydraulic_status") or boundary.get("hydraulic_status") or "HYDRAULIC_UNCONFIRMED",
        "guidance_status": boundary.get("guidance_status") or "NOT_AUTHORIZED",
    }


def new_page(number: int, title: str, subtitle: str = "") -> plt.Figure:
    fig = plt.figure(figsize=A4_LANDSCAPE_IN)
    fig.add_artist(Rectangle((0, 0.94), 1, 0.06, transform=fig.transFigure, color=DEEP))
    fig.text(0.035, 0.967, f"{number:02d}  {title}", color="white", weight="bold", size=13, va="center")
    fig.text(0.965, 0.967, SEAL, color="white", weight="bold", size=7.2, va="center", ha="right")
    if subtitle:
        fig.text(0.035, 0.922, subtitle, color=GRAY, size=8.4, va="top")
    fig.add_artist(Rectangle((0.03, 0.035), 0.94, 0.0012, transform=fig.transFigure, color=LINE))
    fig.text(0.035, 0.018, "Dossie rastreavel de revisao. Validacao profissional e de campo obrigatoria.", color=GRAY, size=6.8)
    fig.text(0.965, 0.018, f"Pagina {number}", color=GRAY, size=6.8, ha="right")
    return fig


def panel(fig: plt.Figure, xywh: tuple[float, float, float, float], title: str, body: Iterable[str], accent: str = GREEN) -> None:
    x, y, width, height = xywh
    fig.add_artist(Rectangle((x, y), width, height, transform=fig.transFigure, facecolor=PALE, edgecolor=LINE, linewidth=0.8))
    fig.add_artist(Rectangle((x, y + height - 0.033), width, 0.033, transform=fig.transFigure, facecolor=accent, edgecolor=accent))
    fig.text(x + 0.012, y + height - 0.0165, title, color="white", weight="bold", size=8, va="center")
    lines: list[str] = []
    for item in body:
        lines.extend(textwrap.wrap(str(item), width=max(28, int(width * 100)), break_long_words=False) or [""])
    line_height = min(0.024, max(0.014, (height - 0.055) / max(len(lines), 1)))
    cursor = y + height - 0.052
    for line in lines:
        if cursor < y + 0.012:
            break
        fig.text(x + 0.014, cursor, line, color=INK, size=7.6, va="top")
        cursor -= line_height


def add_map(fig: plt.Figure, path: Path | None, bounds: tuple[float, float, float, float], missing_label: str) -> None:
    axis = fig.add_axes(bounds)
    axis.set_axis_off()
    if path is None:
        axis.add_patch(Rectangle((0, 0), 1, 1, transform=axis.transAxes, facecolor=PALE, edgecolor=LINE))
        axis.text(0.5, 0.5, missing_label, ha="center", va="center", color=GRAY, size=10, wrap=True)
        return
    try:
        image = plt.imread(path)
    except Exception as exc:
        raise DossierError(f"Mapa invalido: {path}") from exc
    axis.imshow(image)
    axis.set_aspect("equal", adjustable="box")


def fmt_number(value: Any, decimals: int = 1) -> str:
    return "-" if value is None else f"{float(value):,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def add_table(fig: plt.Figure, rows: list[list[str]], columns: list[str], bounds: tuple[float, float, float, float]) -> None:
    axis = fig.add_axes(bounds)
    axis.axis("off")
    if not rows:
        axis.text(0.5, 0.5, "Produto nao fornecido nesta execucao.", ha="center", va="center", color=GRAY, size=10)
        return
    table = axis.table(cellText=rows, colLabels=columns, cellLoc="center", colLoc="center", bbox=(0, 0, 1, 1))
    table.auto_set_font_size(False)
    table.set_fontsize(6.6 if len(columns) > 7 else 7.2)
    for (row, _), cell in table.get_celld().items():
        cell.set_edgecolor(LINE)
        cell.set_linewidth(0.45)
        if row == 0:
            cell.set_facecolor(DEEP)
            cell.get_text().set_color("white")
            cell.get_text().set_weight("bold")
        elif row % 2 == 0:
            cell.set_facecolor(PALE)


def build_pdf(
    output_path: Path,
    project: dict[str, Any],
    run: dict[str, Any],
    request: dict[str, Any],
    config: dict[str, Any],
    topography: dict[str, Any] | None,
    topography_map: Path | None,
    slope_map: Path | None,
    e0_metrics: dict[str, Any] | None,
    e0_map: Path | None,
    cf0_manifest: dict[str, Any] | None,
    cf0_map: Path | None,
    c1_manifest: dict[str, Any] | None,
    c1_map: Path | None,
) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    project_id = nested(project, "id", "project_id", default=nested(request, "project_id", default="nao informado"))
    project_name = nested(project, "name", "project_name", default=project_id)
    run_id = nested(run, "id", "run_id", default="nao informado")
    request_id = nested(request, "request_id", "id", default=nested(run, "request_id", default="nao informado"))
    e0_rows = aggregate_e0(e0_metrics or {})
    cf0_rows = aggregate_cf0(cf0_manifest or {})
    fields, field_source = scope_field_ids(request, topography)
    requested_special = requested_special_products(request, run, config)
    c1_data = c1_summary(c1_manifest) if c1_manifest else None
    availability = {
        "Topografia E0": topography is not None,
        "Mapa de declividade": slope_map is not None,
        "Sulcacao E0": e0_metrics is not None,
        "Familia continua CF0": cf0_manifest is not None,
        "C1 conceitual nao dimensionada": c1_manifest is not None,
    }

    with PdfPages(output_path) as pdf:
        fig = plt.figure(figsize=A4_LANDSCAPE_IN)
        fig.add_artist(Rectangle((0, 0), 1, 1, transform=fig.transFigure, color="#f6f8f5"))
        fig.add_artist(Rectangle((0, 0), 0.025, 1, transform=fig.transFigure, color=GREEN))
        fig.text(0.065, 0.83, "TERRAFLUX | SISTEMATIZACAO", color=GREEN, weight="bold", size=10)
        fig.text(0.065, 0.68, "Dossie tecnico\nda execucao", color=INK, weight="bold", size=34, va="top")
        fig.text(0.065, 0.48, compact(project_name, 80), color=DEEP, weight="bold", size=17)
        fig.text(0.065, 0.425, f"Projeto {project_id}  |  Execucao {run_id}", color=GRAY, size=9.5)
        fig.text(0.065, 0.39, f"Requisicao imutavel: {request_id}", color=GRAY, size=9.5)
        fig.add_artist(Rectangle((0.62, 0.18), 0.32, 0.65, transform=fig.transFigure, facecolor="white", edgecolor=LINE))
        fig.text(0.65, 0.775, "Produtos presentes", color=INK, weight="bold", size=12)
        y = 0.71
        for name, available in availability.items():
            fig.text(0.65, y, "DISPONIVEL" if available else "NAO FORNECIDO", color=GREEN if available else GRAY, weight="bold", size=8)
            fig.text(0.76, y, name, color=INK, size=9)
            y -= 0.062
        fig.text(0.65, 0.36, "LIMITE DE LIBERACAO", color=RED, weight="bold", size=7.5, va="top")
        fig.text(0.65, 0.315, "TRIAGEM E0/CF0/C1 CONCEITUAL\nNAO USAR PARA GUIAMENTO\nHIDRAULICA NAO VALIDADA", color=RED, weight="bold", size=10.5, linespacing=1.4, va="top")
        fig.text(0.065, 0.09, datetime.now(timezone.utc).strftime("Gerado em %Y-%m-%d %H:%M UTC"), color=GRAY, size=8)
        pdf.savefig(fig, bbox_inches=None)
        plt.close(fig)
        pages.append({"page": 1, "section": "COVER", "title": "Dossie tecnico da execucao"})

        page_number = 2
        fig = new_page(page_number, "Identificacao e parametros", "Dados rastreaveis da plataforma e valores usados na solicitacao")
        panel(fig, (0.04, 0.57, 0.28, 0.29), "PROJETO", [
            f"Nome: {project_name}", f"ID: {project_id}", f"CRS: {nested(project, 'crs', 'target_crs', default='nao informado')}",
            f"Status: {nested(project, 'status', default='nao informado')}",
        ], BLUE)
        panel(fig, (0.35, 0.57, 0.28, 0.29), "EXECUCAO", [
            f"Run: {run_id}", f"Motor: {nested(run, 'engine_id', default='nao informado')}",
            f"Status: {nested(run, 'status', default='nao informado')}", f"Produtos: {nested(run, 'product_ids', default=[])}",
        ], GREEN)
        panel(fig, (0.66, 0.57, 0.30, 0.29), "REQUISICAO", [
            f"Request: {request_id}", f"Nivel solicitado: {nested(request, 'requested_delivery_level', default='nao informado')}",
            f"Talhoes: {fields or 'nao informados'}", f"Fonte dos talhoes: {field_source}",
            f"Entre talhoes: {nested(request, 'scope.cross_field_generation', default=nested(config, 'sulcation.allow_cross_field', default=False))}",
            f"Entre propriedades: {nested(request, 'scope.cross_property_generation', default=False)}",
        ], AMBER)
        parameter_rows = []
        for key, value in flatten_scalars(config):
            if key.lower().endswith(("created_at", "updated_at")):
                continue
            parameter_rows.append([compact(key, 45), compact(value, 55)])
            if len(parameter_rows) >= 14:
                break
        add_table(fig, parameter_rows, ["Parametro de configuracao", "Valor"], (0.04, 0.09, 0.92, 0.40))
        pdf.savefig(fig)
        plt.close(fig)
        pages.append({"page": page_number, "section": "IDENTIFICATION", "title": "Identificacao e parametros", "field_ids": fields, "field_id_source": field_source})

        page_number += 1
        fig = new_page(page_number, "Topografia E0", "Modelo de elevacao e curvas: leitura de triagem")
        add_map(fig, topography_map, (0.035, 0.075, 0.66, 0.80), "Mapa topografico nao fornecido")
        if topography:
            scope = topography.get("scope") or {}
            metrics = topography.get("metrics") or {}
            dtm = metrics.get("dtm") or {}
            slope = metrics.get("slope_percent") or {}
            lines = [
                f"Release: {topography.get('release', 'nao informado')}",
                f"Area: {fmt_number(scope.get('area_ha'), 3)} ha | Talhoes: {scope.get('field_count', '-')}",
                f"CRS: {nested(topography, 'configuration.target_crs', default='-')}",
                f"Resolucao: {fmt_number(nested(topography, 'configuration.resolution_m'), 2)} m",
                f"Intervalo das curvas: {fmt_number(nested(topography, 'configuration.contour_interval_m'), 2)} m",
                f"Elevacao min/mediana/max: {fmt_number(dtm.get('min'), 2)} / {fmt_number(dtm.get('median'), 2)} / {fmt_number(dtm.get('max'), 2)} m",
                f"Declividade P50/P95/max: {fmt_number(slope.get('median'), 2)} / {fmt_number(slope.get('p95'), 2)} / {fmt_number(slope.get('max'), 2)} %",
                f"Curvas: {metrics.get('contour_feature_count', '-')}",
            ]
        else:
            lines = ["Manifesto topografico nao fornecido.", "Esta ausencia nao e interpretada como topografia aprovada ou dispensada."]
        panel(fig, (0.70, 0.48, 0.265, 0.39), "INDICADORES", lines, BLUE)
        panel(fig, (0.70, 0.075, 0.265, 0.34), "LEITURA SEGURA", [
            "Produto E0 de triagem.", "Acuracia vertical deve ser validada.", "Hidrologia e conservacao ainda exigem dados e dimensionamento.", "Nao autoriza guiamento nem implantacao.",
        ], RED)
        pdf.savefig(fig)
        plt.close(fig)
        pages.append({"page": page_number, "section": "TOPOGRAPHY_E0", "title": "Topografia E0", "available": topography is not None})

        if slope_map is not None:
            page_number += 1
            fig = new_page(page_number, "Declividade E0", "Mapa de declividade para leitura topografica preliminar")
            add_map(fig, slope_map, (0.035, 0.075, 0.70, 0.80), "Mapa de declividade nao fornecido")
            panel(fig, (0.765, 0.47, 0.20, 0.40), "USO DE TRIAGEM", [
                "Declividade derivada do modelo de terreno.",
                "Nao substitui acuracia vertical, vistoria de campo ou dimensionamento hidraulico.",
                "Classes e limiares devem ser confirmados para solo, chuva, frota e pratica conservacionista locais.",
            ], BLUE)
            panel(fig, (0.765, 0.075, 0.20, 0.32), "LIMITE", [
                "Mapa informativo E0.", "Nao autoriza sulcacao, terraco, ESD ou guiamento.",
            ], RED)
            pdf.savefig(fig)
            plt.close(fig)
            pages.append({"page": page_number, "section": "SLOPE_E0", "title": "Declividade E0", "available": True})

        page_number += 1
        fig = new_page(page_number, "Cenarios de sulcacao E0", "Comparacao geometrica e operacional preliminar por cenario")
        add_map(fig, e0_map, (0.035, 0.39, 0.62, 0.49), "Mapa de cenarios E0 nao fornecido")
        e0_table = [[
            compact(item["scenario_id"], 16), str(item["field_count"]), compact(item["eligibility_status"], 28),
            str(item["radius_violation_line_count"]), fmt_number(item["total_line_km"]),
            fmt_number(item["mean_shot_m"]), fmt_number(item["coverage_pct"]), fmt_number(item["grade_p95_pct"], 2),
        ] for item in e0_rows]
        add_table(fig, e0_table, ["Cenario", "Talhoes", "Elegibilidade geometrica E0", "Violacoes de raio", "Linhas km", "Tiro medio m", "Cobertura %", "Greide P95 %"], (0.035, 0.075, 0.93, 0.245))
        panel(fig, (0.68, 0.42, 0.285, 0.44), "INTERPRETACAO", [
            f"{len(e0_rows)} cenarios agregados." if e0_rows else "Metricas E0 nao fornecidas.",
            "Elegibilidade exige raio avaliado, zero violacao e nenhum bloqueador geometrico em todos os talhoes.",
            "Tiro medio, cobertura, greide e eficiencia sao indicadores comparativos de planejamento.",
            "Nenhum cenario e automaticamente recomendado ou aprovado por este dossie.",
            "Manobras, trafego, solo, chuva, receptores e estruturas permanecem pendentes.",
        ], GREEN if e0_rows else GRAY)
        pdf.savefig(fig)
        plt.close(fig)
        pages.append({"page": page_number, "section": "SULCATION_E0", "title": "Cenarios de sulcacao E0", "scenario_count": len(e0_rows)})

        page_number += 1
        fig = new_page(page_number, "Familia curva continua CF0", "Triagem geometrica: orientacao local, fase, espacamento e continuidade")
        add_map(fig, cf0_map, (0.035, 0.39, 0.62, 0.49), "Mapa de familia continua CF0 nao fornecido")
        cf0_table = [[
            compact(item["candidate_id"], 18), str(item["block_count"]), str(item["eligible_block_count"]),
            str(item["eligible_row_count"]), fmt_number(item["eligible_total_length_km"]),
            str(item["diagnostic_block_count"]), str(item["diagnostic_row_count"]), fmt_number(item["diagnostic_total_length_km"]),
        ] for item in cf0_rows]
        add_table(fig, cf0_table, ["Candidato", "Blocos", "Geometricamente\nelegivel: blocos", "Geometricamente\nelegivel: linhas", "Geometricamente\nelegivel: km", "Diagnostico:\nblocos", "Diagnostico:\nlinhas", "Diagnostico:\nkm"], (0.035, 0.075, 0.93, 0.245))
        qa = (cf0_manifest or {}).get("qa") or {}
        panel(fig, (0.68, 0.42, 0.285, 0.44), "GATES CF0", [
            f"Candidatos/campo: {qa.get('candidate_field_count', len(cf0_rows))}",
            f"Blocos geometricamente elegiveis: {qa.get('geometric_pass_count', '-')}",
            f"Sem familia viavel: {qa.get('no_feasible_family_count', '-')}",
            f"Hidraulica nao confirmada: {qa.get('hydraulic_unconfirmed_count', '-')}",
            "Geometricamente elegivel significa apenas que os gates geometricos CF0 foram atendidos; nao e liberacao agronomica, hidraulica ou operacional.",
        ], AMBER if cf0_rows else GRAY)
        pdf.savefig(fig)
        plt.close(fig)
        pages.append({"page": page_number, "section": "CF0", "title": "Familia curva continua CF0", "candidate_count": len(cf0_rows)})

        if c1_manifest is not None:
            page_number += 1
            fig = new_page(page_number, "C1 curva embutida | conceito E0", "Alinhamento conceitual de sensibilidade; secao e hidraulica nao dimensionadas")
            add_map(fig, c1_map, (0.035, 0.075, 0.68, 0.80), "Mapa C1 conceitual nao fornecido")
            panel(fig, (0.745, 0.47, 0.22, 0.40), "ESCOPO C1", [
                f"Release: {c1_data['release']}",
                f"Estagio: {c1_data['stage_status']}",
                f"Candidatos conceituais: {c1_data['candidate_count']}",
                f"Eixos: {c1_data['axis_count']} | Faixas: {c1_data['strip_count']}",
                f"PCE: {c1_data['pce_status']} | PCX: {c1_data['pcx_status']}",
            ], AMBER)
            panel(fig, (0.745, 0.075, 0.22, 0.33), "NAO DIMENSIONADA", [
                "C1 E0 e alinhamento conceitual, nao projeto de terraco.",
                f"Hidraulica: {c1_data['hydraulic_status']}",
                f"Guiamento: {c1_data['guidance_status']}",
                "Secao, cota de projeto, PCE, PCX, descarga, receptor, movimento de terra e implantacao permanecem bloqueados.",
            ], RED)
            pdf.savefig(fig)
            plt.close(fig)
            pages.append({"page": page_number, "section": "C1_CONCEPT", "title": "C1 curva embutida conceitual", "candidate_count": c1_data["candidate_count"], "dimensioned": False})

        page_number += 1
        fig = new_page(page_number, "Limites e rastreabilidade", "O que este pacote prova, o que permanece pendente e como auditar os arquivos")
        panel(fig, (0.04, 0.47, 0.44, 0.40), "LIMITES DE LIBERACAO", BASE_LIMITATIONS, RED)
        supplied = [name for name, available in availability.items() if available]
        missing = [name for name, available in availability.items() if not available]
        special_available = {"C1_CURVA_EMBUTIDA"} if c1_manifest is not None else set()
        requested_missing = sorted(requested_special - special_available)
        panel(fig, (0.52, 0.47, 0.44, 0.40), "COBERTURA DA EXECUCAO", [
            f"Presentes: {', '.join(supplied) if supplied else 'nenhum produto derivado'}",
            f"Nao fornecidos: {', '.join(missing) if missing else 'nenhum'}",
            f"Solicitados mas ausentes: {', '.join(requested_missing) if requested_missing else 'nenhum entre C1/C2/C3/POA'}",
            "Ausencia de produto e registrada; nunca convertida em aprovacao.",
            "O manifesto JSON contem caminhos, tamanhos e SHA-256 de cada fonte e do PDF.",
        ], BLUE)
        source_lines = [
            f"Projeto: {compact(project_id, 44)}",
            f"Execucao: {compact(run_id, 44)}",
            f"Requisicao: {compact(request_id, 44)}",
            f"Topografia: {nested(topography or {}, 'release', default='NAO FORNECIDA')}",
            f"Sulcacao: {nested(e0_metrics or {}, 'status', 'maximum_output_delivery_level', default='NAO FORNECIDA')}",
            f"CF0: {nested(cf0_manifest or {}, 'release', default='NAO FORNECIDA')}",
            f"C1: {nested(c1_manifest or {}, 'release', 'source_snapshot.release', default='NAO FORNECIDA')}",
        ]
        panel(fig, (0.04, 0.09, 0.92, 0.30), "LINHAGEM RESUMIDA", source_lines, GREEN)
        pdf.savefig(fig)
        plt.close(fig)
        pages.append({"page": page_number, "section": "LIMITATIONS_AND_LINEAGE", "title": "Limites e rastreabilidade", "requested_missing_products": requested_missing})

    return pages


def generate_dossier(
    *, project_json: Path, run_json: Path, request_json: Path, config_json: Path,
    output_pdf: Path, output_manifest: Path, topography_manifest: Path | None = None,
    topography_map: Path | None = None, slope_map: Path | None = None, e0_metrics: Path | None = None,
    e0_map: Path | None = None, cf0_manifest: Path | None = None, cf0_map: Path | None = None,
    c1_manifest: Path | None = None, c1_map: Path | None = None,
) -> dict[str, Any]:
    paths: dict[str, Path] = {
        "project_json": project_json, "run_json": run_json, "request_json": request_json, "config_json": config_json,
    }
    optional = {
        "topography_manifest": topography_manifest, "topography_map": topography_map,
        "slope_map": slope_map, "e0_metrics": e0_metrics, "e0_map": e0_map,
        "cf0_manifest": cf0_manifest, "cf0_map": cf0_map,
        "c1_manifest": c1_manifest, "c1_map": c1_map,
    }
    for label, path in optional.items():
        if path is not None:
            require(path.is_file(), f"{label} ausente: {path}")
            paths[label] = path

    project = read_json(project_json, "project_json")
    run = read_json(run_json, "run_json")
    request = read_json(request_json, "request_json")
    config = read_json(config_json, "config_json")
    topography = read_json(topography_manifest, "topography_manifest") if topography_manifest else None
    e0 = read_json(e0_metrics, "e0_metrics") if e0_metrics else None
    cf0 = read_json(cf0_manifest, "cf0_manifest") if cf0_manifest else None
    c1 = read_json(c1_manifest, "c1_manifest") if c1_manifest else None
    for label, value in (("topography_manifest", topography), ("e0_metrics", e0), ("cf0_manifest", cf0), ("c1_manifest", c1)):
        if value is not None:
            assert_safe_source(value, label)
    require(c1_map is None or c1 is not None, "c1_map exige c1_manifest para classificar o produto com seguranca.")
    if c1 is not None:
        require(
            nested(c1, "release", "source_snapshot.release") == "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED",
            "c1_manifest nao declara a liberacao conceitual nao dimensionada exigida.",
        )

    project_id = str(nested(project, "id", "project_id", default=nested(request, "project_id", default="")))
    request_project = str(nested(request, "project_id", default=project_id))
    run_project = str(nested(run, "project_id", default=project_id))
    require(bool(project_id), "project_json/request_json sem identificador de projeto.")
    require(request_project == project_id, "request_json pertence a outro projeto.")
    require(run_project == project_id, "run_json pertence a outro projeto.")
    request_id = nested(request, "request_id", "id")
    run_request = nested(run, "request_id")
    if request_id is not None and run_request is not None:
        require(str(request_id) == str(run_request), "run_json pertence a outra requisicao.")
    if topography is not None and topography.get("project_id") is not None:
        require(str(topography["project_id"]) in {project_id, str(request.get("project_id"))}, "Topografia pertence a outro projeto.")

    output_pdf = output_pdf.resolve()
    output_manifest = output_manifest.resolve()
    require(output_pdf != output_manifest, "PDF e manifesto devem ter caminhos diferentes.")
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    staged_pdf: Path | None = None
    staged_manifest: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="run-dossier-", suffix=".pdf", dir=output_pdf.parent, delete=False) as stream:
            staged_pdf = Path(stream.name)
        pages = build_pdf(
            staged_pdf, project, run, request, config, topography, topography_map, slope_map,
            e0, e0_map, cf0, cf0_map, c1, c1_map,
        )
        reader = PdfReader(str(staged_pdf))
        require(len(reader.pages) == len(pages), "Contagem de paginas divergente.")
        extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
        for token in ("NAO USAR PARA GUIAMENTO", "VALIDACAO HIDRAULICA NAO REALIZADA", "VALIDACAO AGRONOMICA"):
            require(token in extracted, f"Aviso obrigatorio ausente no PDF: {token}")

        pdf_record = {
            "path": display_path(output_pdf),
            "size_bytes": staged_pdf.stat().st_size,
            "sha256": sha256_file(staged_pdf),
        }
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "manifest_type": MANIFEST_TYPE,
            "release": RELEASE,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project_id": project_id,
            "run_id": nested(run, "id", "run_id"),
            "request_id": request_id,
            "guidance_authorized": False,
            "hydraulic_status": "NOT_EVALUATED_OR_UNCONFIRMED",
            "agronomic_status": "NOT_VALIDATED",
            "available_products": {
                "TOPOGRAPHY_E0": topography is not None,
                "SULCATION_E0": e0 is not None,
                "CF0_CONTINUOUS": cf0 is not None,
                "C1_CONCEPT_NOT_DIMENSIONED": c1 is not None,
                "SLOPE_MAP": slope_map is not None,
            },
            "source_integrity": {label: file_record(path) for label, path in paths.items()},
            "scope": {
                "field_ids": scope_field_ids(request, topography)[0],
                "field_id_source": scope_field_ids(request, topography)[1],
            },
            "requested_missing_products": next(
                (page.get("requested_missing_products", []) for page in pages if page.get("section") == "LIMITATIONS_AND_LINEAGE"),
                [],
            ),
            "scenario_counts": {
                "sulcation_e0": len(aggregate_e0(e0 or {})),
                "cf0": len(aggregate_cf0(cf0 or {})),
                "c1_concept": c1_summary(c1)["candidate_count"] if c1 else 0,
            },
            "pages": pages,
            "page_count": len(pages),
            "release_limitations": list(BASE_LIMITATIONS),
            "pdf": pdf_record,
            "preflight": {"status": "PASS", "searchable_pdf": True, "manifest_published_last": True},
        }
        payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        with tempfile.NamedTemporaryFile(prefix="run-dossier-manifest-", suffix=".json", dir=output_manifest.parent, delete=False) as stream:
            staged_manifest = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged_pdf, output_pdf)
        staged_pdf = None
        os.replace(staged_manifest, output_manifest)
        staged_manifest = None
        return manifest
    finally:
        for staged in (staged_pdf, staged_manifest):
            if staged is not None and staged.exists():
                staged.unlink()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gera dossie PDF rastreavel de uma execucao da plataforma.")
    parser.add_argument("--project-json", type=Path, required=True)
    parser.add_argument("--run-json", type=Path, required=True)
    parser.add_argument("--request-json", type=Path, required=True)
    parser.add_argument("--config-json", type=Path, required=True)
    parser.add_argument("--topography-manifest", type=Path)
    parser.add_argument("--topography-map", type=Path)
    parser.add_argument("--slope-map", type=Path)
    parser.add_argument("--e0-metrics", type=Path)
    parser.add_argument("--e0-map", type=Path)
    parser.add_argument("--cf0-manifest", type=Path)
    parser.add_argument("--cf0-map", type=Path)
    parser.add_argument("--c1-manifest", type=Path)
    parser.add_argument("--c1-map", type=Path)
    parser.add_argument("--output-pdf", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = generate_dossier(**vars(args))
    except DossierError as exc:
        print(f"PLATFORM RUN DOSSIER FAILED: {exc}")
        return 2
    print(json.dumps({"status": "PASS", "release": manifest["release"], "pdf": manifest["pdf"], "page_count": manifest["page_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
