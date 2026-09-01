#!/usr/bin/env python3
"""Generate the technical CF0 continuous-family review report.

The report consumes only a verified CF0 package. It never reads legacy PDFs,
never creates C1/C2/C3 geometries, and repeats the release seal on every page:

    CF0 PROTÓTIPO GEOMÉTRICO | NÃO USAR PARA GUIAMENTO

Run after CF0 generation with the QGIS Python environment::

    & 'C:\\Program Files\\QGIS 3.32.1\\bin\\python-qgis.bat' `
      scripts/generate_cf0_review_report.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

os.environ.setdefault("SOURCE_DATE_EPOCH", "1786676400")

import geopandas as gpd
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import LightSource, Normalize
from matplotlib.lines import Line2D
from matplotlib.patches import FancyBboxPatch, Rectangle
from matplotlib.ticker import FuncFormatter
from osgeo import gdal, ogr
from PIL import Image, ImageStat
from shapely import wkb
from shapely.geometry import LineString, Polygon

try:
    from pypdf import PdfReader
except ModuleNotFoundError:
    # The bundled QGIS Python may not expose the user's pure-Python packages.
    # Reuse an existing pypdf installation without modifying either environment.
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
        raise ModuleNotFoundError(
            "pypdf is required for searchable-text and media-box preflight."
        )

try:
    from verify_continuous_family import (
        expected_spatial_reference,
        load_work_blocks,
        resolve_path,
        verify as verify_cf0_package,
    )
except ModuleNotFoundError:
    from scripts.verify_continuous_family import (
        expected_spatial_reference,
        load_work_blocks,
        resolve_path,
        verify as verify_cf0_package,
    )


gdal.UseExceptions()
ogr.UseExceptions()

ROOT = Path(__file__).resolve().parents[1]
DERIVED = ROOT / "dataset" / "derived"
DEFAULT_CF0_MANIFEST = DERIVED / "continuous_family_manifest.json"
DEFAULT_CF0_SCHEMA = ROOT / "schemas" / "continuous-family-stage.schema.json"
DEFAULT_PDF = DERIVED / "Relatorio_Tecnico_CF0_Familias_Continuas.pdf"
DEFAULT_REPORT_MANIFEST = DERIVED / "cf0_review_report_manifest.json"
DEFAULT_ASSETS = DERIVED / "cf0_review_report_assets"

REPORT_SCHEMA_VERSION = "1.2.0"
SOURCE_SCHEMA_VERSION = "1.2.0"
EXPECTED_SOURCE_LAYERS = (
    "continuous_rows",
    "diagnostic_rows",
    "family_summary",
    "hydraulic_precheck",
)
EXPECTED_BOUNDARY_CONTRACT = {
    "solve_mask_rasterization": "ALL_TOUCHED_SUPERCOVER",
    "contour_extrapolation_method": "FIRST_ORDER_LOCAL_LSQ_GRADIENT_FAIL_CLOSED",
    "contour_extrapolation_halo_cells": 1,
    "contour_gradient_lsq_max_radius_cells": 2,
    "contour_gradient_lsq_max_relative_residual": 0.30,
    "contour_gradient_lsq_max_condition_number": 100.0,
    "contour_gradient_lsq_minimum_neighbor_count": 3,
    "contour_gradient_lsq_required_rank": 2,
    "contour_gradient_component_connectivity": 4,
    "endpoint_extension_mode": "TANGENT_ONLY_FAIL_CLOSED",
}
EXPECTED_PHASE_CONTRACT = {
    "gauge_method": "ZERO_AT_LEXICOGRAPHIC_FIRST_VALID_CELL_PER_COMPONENT",
    "gauge_anchor_value_m": 0,
    "solve_mask_required_component_count": 1,
    "solve_mask_component_connectivity": 4,
}
EXPECTED_PHASE_OFFSET_CONTRACT = {
    "phase_level_equation": "phase_level_m = phase_level_index * row_spacing_m + phase_offset_m",
    "phase_level_index_scope": "CANDIDATE_WORK_BLOCK_PHASE_LEVEL",
    "row_index_definition": "UNIQUE_NONNEGATIVE_OPERATIONAL_SEQUENCE_PER_CANDIDATE_WORK_BLOCK",
    "phase_offset_search_revision": "CF0_PHASE_OFFSET_QUARTER_SPACING_V1",
    "phase_offset_fractions": [0, 0.25, 0.5, 0.75],
    "phase_offset_selection_rule": (
        "PASS_ONLY_MAX_MIN_RADIUS_MIN_SPACING_OUTSIDE_MIN_SPACING_P95_"
        "MIN_COVERAGE_ERROR_MIN_OFFSET"
    ),
    "phase_offset_search_scope": (
        "DISCRETE_CONFIGURED_OFFSETS_NOT_CONTINUOUS_GAUGE_INVARIANCE"
    ),
}
EXPECTED_BLOCKER_CONTRACT = {
    "spline_fit_failure": "ROW_SPLINE_FIT_FAILED",
    "halo_gradient_failure": "PHASE_HALO_GRADIENT_UNESTIMABLE",
    "solve_mask_failure": "WORK_BLOCK_SOLVE_MASK_DISCONNECTED",
}
EXPECTED_SPLINE_REPRESENTATION_CONTRACT = {
    "spline_representation_method": "ADAPTIVE_CHORD_ERROR_PRESERVE_VERTICES",
    "spline_representation_tolerance_fraction": 0.10,
}
EXPECTED_SPLINE_MAX_DEVIATION_M = 0.12

SEAL = "CF0 PROTÓTIPO GEOMÉTRICO | NÃO USAR PARA GUIAMENTO"
HYDRAULIC_SEAL = "HIDRÁULICA: HYDRAULIC_UNCONFIRMED"

INK = "#18231f"
DEEP = "#173b31"
GREEN = "#168653"
BLUE = "#2679a8"
YELLOW = "#dca629"
RED = "#c63b32"
PURPLE = "#725aa5"
GRAY = "#64716b"
LIGHT = "#edf1ed"
PALE = "#f7f8f6"
BOUNDARY = "#111815"
PROFILE_COLORS = {
    "CF0A_CONSERVACAO": GREEN,
    "CF0B_EQUILIBRIO": YELLOW,
    "CF0C_OPERACAO": BLUE,
}
PROFILE_SHORT = {
    "CF0A_CONSERVACAO": "CF0A",
    "CF0B_EQUILIBRIO": "CF0B",
    "CF0C_OPERACAO": "CF0C",
}

mpl.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 8.3,
        "axes.titlesize": 10.5,
        "axes.labelsize": 7.5,
        "axes.linewidth": 0.6,
        "axes.edgecolor": "#a7b0aa",
        "xtick.labelsize": 6.5,
        "ytick.labelsize": 6.5,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "savefig.facecolor": "white",
        "figure.facecolor": "white",
        "figure.dpi": 120,
    }
)


@dataclass(frozen=True)
class PageDefinition:
    number: int
    title: str
    slug: str
    kind: str
    payload: str | None = None


@dataclass
class RasterData:
    path: Path
    arrays: list[np.ndarray]
    masks: list[np.ndarray]
    extent: tuple[float, float, float, float]
    transform: tuple[float, ...]


@dataclass
class ReportData:
    manifest_path: Path
    manifest: dict[str, Any]
    verification: dict[str, Any]
    rows: gpd.GeoDataFrame
    diagnostic_rows: gpd.GeoDataFrame
    hydraulic: pd.DataFrame
    fields: gpd.GeoDataFrame
    field_id_column: str
    work_blocks: dict[str, Any]
    terrain: RasterData
    rasters: dict[tuple[str, str, str, str], RasterData]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


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


def file_record(path: Path, *, published_path: Path | None = None) -> dict[str, Any]:
    return {
        "path": relative_path(published_path or path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def format_number(value: Any, decimals: int = 2, suffix: str = "") -> str:
    if value is None:
        return "—"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(number):
        return "∞" if number > 0 else "—"
    text = f"{number:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text}{suffix}"


def wrap_text(value: str, width: int = 68) -> str:
    return "\n".join(textwrap.wrap(str(value), width=width, break_long_words=False))


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower())
    return normalized.strip("-") or "page"


def chunks(values: Sequence[Any], size: int) -> list[list[Any]]:
    return [list(values[index : index + size]) for index in range(0, len(values), size)]


def boundary_contract(manifest: dict[str, Any]) -> dict[str, Any]:
    extraction = manifest["solver_parameters"]["extraction"]
    return {key: extraction.get(key) for key in EXPECTED_BOUNDARY_CONTRACT}


def phase_contract(manifest: dict[str, Any]) -> dict[str, Any]:
    phase = manifest["solver_parameters"]["phase"]
    return {key: phase.get(key) for key in EXPECTED_PHASE_CONTRACT}


def phase_offset_contract(manifest: dict[str, Any]) -> dict[str, Any]:
    extraction = manifest["solver_parameters"]["extraction"]
    return {key: extraction.get(key) for key in EXPECTED_PHASE_OFFSET_CONTRACT}


def spline_representation_contract(manifest: dict[str, Any]) -> dict[str, Any]:
    extraction = manifest["solver_parameters"]["extraction"]
    return {key: extraction.get(key) for key in EXPECTED_SPLINE_REPRESENTATION_CONTRACT}


def candidate_phase_audit(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for candidate in manifest["candidates"]:
        records.append(
            {
                "candidate_id": candidate["candidate_id"],
                "field_id": candidate["field_id"],
                "work_block_id": candidate["work_block_id"],
                "geometric_status": candidate["geometric_status"],
                "phase_offset_m": candidate["phase_offset_m"],
                "phase_offset_fraction": candidate["phase_offset_fraction"],
                "phase_offset_role": candidate["phase_offset_role"],
                "phase_offset_selection": candidate["phase_offset_selection"],
                "contour_extrapolation_qa": candidate["contour_extrapolation_qa"],
                "solve_mask_component_count": candidate["metrics"]["solver_gate_metrics"][
                    "solve_mask_component_count"
                ],
                "row_count": candidate["row_count"],
                "diagnostic_row_count": candidate["diagnostic_row_count"],
                "blocker_codes": candidate["blocker_codes"],
            }
        )
    return records


def build_page_definitions(manifest: dict[str, Any]) -> list[PageDefinition]:
    definitions: list[tuple[str, str, str | None]] = [
        ("Caderno técnico CF0", "cover", None),
        ("Escopo e leitura segura", "scope", None),
        ("Método da família contínua", "method", None),
        ("Gauge, níveis e offsets de fase", "phase_contract", None),
        ("Contrato de extração e borda", "extraction_contract", None),
        ("Domínios físicos e insumos", "domains", None),
    ]
    excluded_chunks = chunks(manifest["domain_assembly"]["excluded_components"], 12) or [[]]
    for index, _ in enumerate(excluded_chunks, start=1):
        suffix = f" {index}/{len(excluded_chunks)}" if len(excluded_chunks) > 1 else ""
        definitions.append((f"Componentes excluídos do CF0{suffix}", "domain_exclusions", str(index - 1)))
    definitions.append(("QA do estágio CF0", "qa", None))
    phase_audit_chunks = chunks(manifest["candidates"], 8)
    for index, _ in enumerate(phase_audit_chunks, start=1):
        suffix = f" {index}/{len(phase_audit_chunks)}" if len(phase_audit_chunks) > 1 else ""
        definitions.append((f"Offsets, máscara e halo{suffix}", "phase_audit", str(index - 1)))
    for block in manifest["domain_assembly"]["work_blocks"]:
        block_id = block["work_block_id"]
        field_id = ", ".join(block["field_ids"])
        definitions.extend(
            [
                (f"Cenários no bloco {block_id} · talhão {field_id}", "block_map", block_id),
                (f"Fase e coerência · {block_id}", "block_rasters", block_id),
            ]
        )
    candidate_chunks = chunks(manifest["candidates"], 8)
    for index, _ in enumerate(candidate_chunks, start=1):
        suffix = f" {index}/{len(candidate_chunks)}" if len(candidate_chunks) > 1 else ""
        definitions.append((f"Comparativo dos gates{suffix}", "gates", str(index - 1)))
    hydraulic_chunks = chunks(manifest["candidates"], 8)
    for index, _ in enumerate(hydraulic_chunks, start=1):
        suffix = f" {index}/{len(hydraulic_chunks)}" if len(hydraulic_chunks) > 1 else ""
        definitions.append((f"Pré-checagem hidráulica{suffix}", "hydraulic", str(index - 1)))
    definitions.extend(
        [
            ("Limitações de liberação", "limitations", None),
            ("Roadmap C1, C2 e C3", "roadmap", None),
            ("Rastreabilidade e conclusão", "traceability", None),
        ]
    )
    pages = [
        PageDefinition(index, title, f"{index:02d}-{slugify(title)}", kind, payload)
        for index, (title, kind, payload) in enumerate(definitions, start=1)
    ]
    if len({page.slug for page in pages}) != len(pages):
        raise RuntimeError("Dynamic report page slugs are not unique.")
    return pages


def read_raster(path: Path) -> RasterData:
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    if dataset is None:
        raise RuntimeError(f"Não foi possível abrir raster: {path}")
    transform = tuple(float(value) for value in dataset.GetGeoTransform())
    arrays: list[np.ndarray] = []
    masks: list[np.ndarray] = []
    for index in range(1, dataset.RasterCount + 1):
        band = dataset.GetRasterBand(index)
        array = np.asarray(band.ReadAsArray(), dtype=float)
        nodata = band.GetNoDataValue()
        mask = np.isfinite(array)
        if nodata is not None:
            mask &= ~np.isclose(array, float(nodata), rtol=0, atol=1e-12)
        arrays.append(array)
        masks.append(mask)
    width, height = dataset.RasterXSize, dataset.RasterYSize
    extent = (
        transform[0],
        transform[0] + width * transform[1],
        transform[3] + height * transform[5],
        transform[3],
    )
    dataset = None
    return RasterData(path, arrays, masks, extent, transform)


def read_attribute_table(path: Path, layer_name: str) -> pd.DataFrame:
    source = ogr.Open(str(path))
    if source is None:
        raise RuntimeError(f"Não foi possível abrir GeoPackage: {path}")
    layer = source.GetLayerByName(layer_name)
    if layer is None:
        raise RuntimeError(f"Camada ausente: {layer_name}")
    definition = layer.GetLayerDefn()
    names = [definition.GetFieldDefn(index).GetName() for index in range(definition.GetFieldCount())]
    records = [{name: feature.GetField(name) for name in names} for feature in layer]
    source = None
    return pd.DataFrame(records, columns=names)


def read_rows(
    path: Path,
    crs_code: int,
    layer_name: str = "continuous_rows",
    *,
    required: bool = True,
) -> gpd.GeoDataFrame:
    source = ogr.Open(str(path))
    if source is None:
        raise RuntimeError(f"Não foi possível abrir GeoPackage: {path}")
    layer = source.GetLayerByName(layer_name)
    if layer is None:
        source = None
        if required:
            raise RuntimeError(f"Camada ausente: {layer_name}")
        return gpd.GeoDataFrame(
            columns=["candidate_id", "field_id", "work_block_id", "geometry"],
            geometry="geometry",
            crs=f"EPSG:{crs_code}",
        )
    definition = layer.GetLayerDefn()
    names = [definition.GetFieldDefn(index).GetName() for index in range(definition.GetFieldCount())]
    records: list[dict[str, Any]] = []
    geometries = []
    for feature in layer:
        records.append({name: feature.GetField(name) for name in names})
        geometry = feature.GetGeometryRef()
        geometries.append(wkb.loads(bytes(geometry.ExportToWkb())) if geometry else None)
    source = None
    frame = pd.DataFrame(records, columns=names)
    return gpd.GeoDataFrame(frame, geometry=geometries, crs=f"EPSG:{crs_code}")


def load_report_data(manifest_path: Path, schema_path: Path) -> ReportData:
    verification = verify_cf0_package(manifest_path, schema_path)
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != SOURCE_SCHEMA_VERSION:
        raise RuntimeError(
            f"O relatório CF0 {REPORT_SCHEMA_VERSION} exige pacote-fonte CF0 {SOURCE_SCHEMA_VERSION}."
        )
    observed_phase_contract = phase_contract(manifest)
    if observed_phase_contract != EXPECTED_PHASE_CONTRACT:
        raise RuntimeError(
            "Contrato de gauge/máscara CF0 incompatível: "
            f"observado={observed_phase_contract}, esperado={EXPECTED_PHASE_CONTRACT}."
        )
    observed_phase_offset_contract = phase_offset_contract(manifest)
    if observed_phase_offset_contract != EXPECTED_PHASE_OFFSET_CONTRACT:
        raise RuntimeError(
            "Contrato de níveis/offsets CF0 incompatível: "
            f"observado={observed_phase_offset_contract}, "
            f"esperado={EXPECTED_PHASE_OFFSET_CONTRACT}."
        )
    observed_boundary_contract = boundary_contract(manifest)
    if observed_boundary_contract != EXPECTED_BOUNDARY_CONTRACT:
        raise RuntimeError(
            "Contrato de borda CF0 incompatível: "
            f"observado={observed_boundary_contract}, esperado={EXPECTED_BOUNDARY_CONTRACT}."
        )
    observed_spline_contract = spline_representation_contract(manifest)
    if observed_spline_contract != EXPECTED_SPLINE_REPRESENTATION_CONTRACT:
        raise RuntimeError(
            "Contrato de representação da spline CF0 incompatível: "
            f"observado={observed_spline_contract}, "
            f"esperado={EXPECTED_SPLINE_REPRESENTATION_CONTRACT}."
        )
    spline_max_deviation_m = manifest["solver_parameters"]["extraction"].get(
        "spline_max_deviation_m"
    )
    if not math.isclose(
        float(spline_max_deviation_m),
        EXPECTED_SPLINE_MAX_DEVIATION_M,
        rel_tol=0,
        abs_tol=1e-12,
    ):
        raise RuntimeError("spline_max_deviation_m não pode ser afrouxado além de 0,12 m.")
    epsg = int(manifest["crs"]["code"])
    expected_srs = expected_spatial_reference(epsg)
    work_blocks = load_work_blocks(manifest, manifest_path, expected_srs)
    gpkg_path = resolve_path(manifest["outputs"]["geopackage"]["path"], manifest_path)
    source = ogr.Open(str(gpkg_path))
    if source is None:
        raise RuntimeError(f"Não foi possível abrir GeoPackage: {gpkg_path}")
    layer_names = tuple(source.GetLayerByIndex(index).GetName() for index in range(source.GetLayerCount()))
    source = None
    if set(layer_names) != set(EXPECTED_SOURCE_LAYERS) or len(layer_names) != len(EXPECTED_SOURCE_LAYERS):
        raise RuntimeError(
            "O GeoPackage CF0 1.2 deve conter exatamente quatro camadas: "
            + ", ".join(EXPECTED_SOURCE_LAYERS)
        )
    rows = read_rows(gpkg_path, epsg)
    diagnostic_rows = read_rows(
        gpkg_path,
        epsg,
        "diagnostic_rows",
        required=True,
    )
    summary = read_attribute_table(gpkg_path, "family_summary")
    hydraulic = read_attribute_table(gpkg_path, "hydraulic_precheck")
    expected_counts = manifest["layer_counts"]
    observed_counts = {
        "continuous_rows": len(rows),
        "diagnostic_rows": len(diagnostic_rows),
        "family_summary": len(summary),
        "hydraulic_precheck": len(hydraulic),
    }
    if observed_counts != {name: int(expected_counts[name]) for name in EXPECTED_SOURCE_LAYERS}:
        raise RuntimeError(f"Contagens das quatro camadas divergem do manifesto: {observed_counts}")
    required_phase_fields = {
        "row_index",
        "phase_level_index",
        "phase_level_m",
        "phase_offset_m",
        "phase_offset_fraction",
    }
    missing_published_fields = required_phase_fields - set(rows.columns)
    if missing_published_fields:
        raise RuntimeError(
            "continuous_rows não contém os campos contratuais de fase: "
            + ", ".join(sorted(missing_published_fields))
        )
    required_diagnostic_fields = {
        *required_phase_fields,
        "diagnostic_status",
        "guidance_status",
    }
    if not required_diagnostic_fields <= set(diagnostic_rows.columns):
        raise RuntimeError(
            "diagnostic_rows não contém os estados fail-closed obrigatórios: "
            + ", ".join(sorted(required_diagnostic_fields - set(diagnostic_rows.columns)))
        )
    if not diagnostic_rows.empty:
        if set(diagnostic_rows["diagnostic_status"].astype(str)) != {"NOT_APPROVED"}:
            raise RuntimeError("Toda feição de diagnostic_rows deve permanecer NOT_APPROVED.")
        if set(diagnostic_rows["guidance_status"].astype(str)) != {"NOT_AUTHORIZED"}:
            raise RuntimeError("Toda feição de diagnostic_rows deve permanecer NOT_AUTHORIZED.")
    if not rows.empty and "geometry_status" in rows.columns:
        if set(rows["geometry_status"].astype(str)) != {"GEOMETRIC_PASS"}:
            raise RuntimeError("continuous_rows só pode conter linhas geométricas aprovadas.")

    work_input = manifest["inputs"]["work_area"]
    work_path = resolve_path(work_input["path"], manifest_path)
    fields = gpd.read_file(work_path, layer=work_input.get("layer"))
    field_id_column = work_input["id_field"]
    fields[field_id_column] = fields[field_id_column].astype(str)
    if fields.crs is None or fields.crs.to_epsg() != epsg:
        raise RuntimeError("CRS dos talhões difere do manifesto CF0.")

    terrain_path = resolve_path(manifest["inputs"]["terrain_dtm"]["path"], manifest_path)
    terrain = read_raster(terrain_path)
    rasters: dict[tuple[str, str, str, str], RasterData] = {}
    for record in manifest["outputs"]["rasters"]:
        key = (
            record["candidate_id"],
            record["field_id"],
            record["work_block_id"],
            record["raster_role"],
        )
        rasters[key] = read_raster(resolve_path(record["path"], manifest_path))
    return ReportData(
        manifest_path,
        manifest,
        verification,
        rows,
        diagnostic_rows,
        hydraulic,
        fields,
        field_id_column,
        work_blocks,
        terrain,
        rasters,
    )


def polygon_segments(geometries: Iterable[Any]) -> list[np.ndarray]:
    segments: list[np.ndarray] = []

    def append_geometry(geometry: Any) -> None:
        if geometry is None or geometry.is_empty:
            return
        if geometry.geom_type == "Polygon":
            segments.append(np.asarray(geometry.exterior.coords, dtype=float)[:, :2])
            segments.extend(
                np.asarray(interior.coords, dtype=float)[:, :2]
                for interior in geometry.interiors
            )
            return
        if hasattr(geometry, "geoms"):
            for part in geometry.geoms:
                append_geometry(part)

    for item in geometries:
        append_geometry(item)
    return segments


def line_segments(geometries: Iterable[Any]) -> list[np.ndarray]:
    segments: list[np.ndarray] = []

    def append_geometry(geometry: Any) -> None:
        if geometry is None or geometry.is_empty:
            return
        if geometry.geom_type in {"LineString", "LinearRing"}:
            segments.append(np.asarray(geometry.coords, dtype=float)[:, :2])
            return
        if hasattr(geometry, "geoms"):
            for part in geometry.geoms:
                append_geometry(part)

    for item in geometries:
        append_geometry(item)
    return segments


def masked_band(raster: RasterData, band_index: int = 0) -> np.ma.MaskedArray:
    return np.ma.array(raster.arrays[band_index], mask=~raster.masks[band_index])


def set_map_extent(ax: plt.Axes, bounds: Sequence[float], pad_fraction: float = 0.04) -> None:
    xmin, ymin, xmax, ymax = map(float, bounds)
    width = max(xmax - xmin, 1.0)
    height = max(ymax - ymin, 1.0)
    padding = max(width, height) * pad_fraction
    ax.set_xlim(xmin - padding, xmax + padding)
    ax.set_ylim(ymin - padding, ymax + padding)
    ax.set_aspect("equal", adjustable="box")
    ax.ticklabel_format(style="plain", useOffset=False)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value / 1000:.2f}"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value / 1000:.2f}"))
    ax.set_xlabel("E (km)")
    ax.set_ylabel("N (km)")
    ax.grid(False)


def add_geometry_outline(
    ax: plt.Axes,
    geometries: Iterable[Any],
    *,
    color: str = BOUNDARY,
    linewidth: float = 1.0,
    linestyle: str = "solid",
    zorder: int = 8,
) -> None:
    segments = polygon_segments(geometries)
    if segments:
        ax.add_collection(
            mpl.collections.LineCollection(
                segments,
                colors=color,
                linewidths=linewidth,
                linestyles=linestyle,
                zorder=zorder,
            )
        )


def add_linework(
    ax: plt.Axes,
    geometries: Iterable[Any],
    *,
    color: str,
    linewidth: float = 0.75,
    alpha: float = 0.95,
    zorder: int = 7,
    linestyle: str = "solid",
) -> None:
    segments = line_segments(geometries)
    if segments:
        ax.add_collection(
            mpl.collections.LineCollection(
                segments,
                colors=color,
                linewidths=linewidth,
                linestyles=linestyle,
                alpha=alpha,
                zorder=zorder,
            )
        )


def add_terrain(ax: plt.Axes, terrain: RasterData, alpha: float = 0.58) -> None:
    values = masked_band(terrain)
    if values.count() == 0:
        return
    finite = values.compressed()
    lower, upper = np.percentile(finite, [2, 98])
    if math.isclose(float(lower), float(upper)):
        lower, upper = float(lower) - 1.0, float(upper) + 1.0
    filled = values.filled(float(np.median(finite)))
    xres = abs(float(terrain.transform[1]))
    yres = abs(float(terrain.transform[5]))
    shade = LightSource(azdeg=315, altdeg=40).hillshade(
        filled,
        vert_exag=1.2,
        dx=max(xres, 1e-9),
        dy=max(yres, 1e-9),
    )
    shade = np.ma.array(shade, mask=np.ma.getmaskarray(values))
    ax.imshow(
        shade,
        extent=terrain.extent,
        origin="upper",
        cmap="gray",
        vmin=0.08,
        vmax=0.96,
        alpha=alpha,
        zorder=0,
    )
    ax.imshow(
        values,
        extent=terrain.extent,
        origin="upper",
        cmap="terrain",
        norm=Normalize(vmin=float(lower), vmax=float(upper)),
        alpha=0.18,
        zorder=1,
    )


def add_north_arrow(ax: plt.Axes) -> None:
    ax.annotate(
        "N",
        xy=(0.94, 0.91),
        xytext=(0.94, 0.78),
        xycoords="axes fraction",
        textcoords="axes fraction",
        ha="center",
        va="center",
        fontsize=8,
        weight="bold",
        arrowprops={"arrowstyle": "-|>", "color": INK, "lw": 1.0},
        zorder=20,
    )


def add_intro(fig: plt.Figure, text: str, y: float = 0.892) -> None:
    fig.text(0.045, y, text, fontsize=8.4, color=GRAY, va="top")


def add_card(
    fig: plt.Figure,
    xywh: tuple[float, float, float, float],
    title: str,
    body: str,
    *,
    value: str | None = None,
    accent: str = GREEN,
    body_size: float = 7.5,
) -> None:
    x, y, width, height = xywh
    fig.add_artist(
        FancyBboxPatch(
            (x, y),
            width,
            height,
            boxstyle="round,pad=0.007,rounding_size=0.005",
            transform=fig.transFigure,
            facecolor=PALE,
            edgecolor="#d2d9d4",
            linewidth=0.7,
        )
    )
    fig.add_artist(Rectangle((x, y), 0.005, height, transform=fig.transFigure, color=accent))
    fig.text(x + 0.015, y + height - 0.020, title.upper(), fontsize=6.8, color=accent, weight="bold", va="top")
    body_y = y + height - 0.047
    if value is not None:
        fig.text(x + 0.015, body_y, value, fontsize=15, color=INK, weight="bold", va="top")
        body_y -= 0.045
    fig.text(x + 0.015, body_y, body, fontsize=body_size, color=INK, va="top", linespacing=1.30)


def styled_table(
    ax: plt.Axes,
    rows: Sequence[Sequence[Any]],
    columns: Sequence[str],
    *,
    widths: Sequence[float] | None = None,
    font_size: float = 6.7,
    scale_y: float = 1.45,
) -> Any:
    ax.axis("off")
    table = ax.table(
        cellText=[[str(value) for value in row] for row in rows],
        colLabels=list(columns),
        cellLoc="left",
        colLoc="left",
        loc="upper left",
        colWidths=list(widths) if widths else None,
    )
    table.auto_set_font_size(False)
    table.set_fontsize(font_size)
    table.scale(1, scale_y)
    for (row, _column), cell in table.get_celld().items():
        cell.set_edgecolor("#d4dad6")
        cell.set_linewidth(0.45)
        if row == 0:
            cell.set_facecolor(DEEP)
            cell.get_text().set_color("white")
            cell.get_text().set_weight("bold")
        elif row % 2 == 0:
            cell.set_facecolor("#f1f4f1")
        else:
            cell.set_facecolor("white")
    return table


class ReportBook:
    def __init__(self, pdf_path: Path, assets_dir: Path, pages: Sequence[PageDefinition]):
        self.pdf_path = pdf_path
        self.assets_dir = assets_dir
        self.pages = list(pages)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        metadata = {
            "Title": "Relatório técnico CF0 - famílias contínuas",
            "Author": "Projeto de Sistematização",
            "Subject": "Prototipagem geométrica CF0 para revisão",
            "Keywords": "CF0; sulcação; fase; coerência; QA; hidráulica",
            "Creator": "scripts/generate_cf0_review_report.py",
            "Producer": "Matplotlib PdfPages",
            "CreationDate": datetime(2026, 8, 14, tzinfo=timezone.utc),
            "ModDate": datetime(2026, 8, 14, tzinfo=timezone.utc),
        }
        self.pdf = PdfPages(pdf_path, metadata=metadata)
        self.assets: list[dict[str, Any]] = []

    def new(self, page: PageDefinition, kicker: str = "REVISÃO TÉCNICA CF0") -> plt.Figure:
        fig = plt.figure(figsize=(11.69, 8.27))
        fig.subplots_adjust(0, 0, 1, 1)
        fig.add_artist(Rectangle((0, 0.922), 1, 0.078, transform=fig.transFigure, color=DEEP, zorder=-1))
        fig.text(0.045, 0.975, kicker, color="#b9d6c1", fontsize=7.2, weight="bold", va="top")
        fig.text(0.045, 0.944, page.title, color="white", fontsize=16.5, weight="bold", va="center")
        fig.add_artist(
            FancyBboxPatch(
                (0.903, 0.943),
                0.048,
                0.028,
                boxstyle="round,pad=0.005,rounding_size=0.004",
                transform=fig.transFigure,
                facecolor=YELLOW,
                edgecolor="none",
            )
        )
        fig.text(0.927, 0.957, "CF0", ha="center", va="center", fontsize=8.3, weight="bold", color=INK)
        return fig

    def finish(self, fig: plt.Figure, page: PageDefinition) -> None:
        fig.add_artist(Rectangle((0, 0), 1, 0.050, transform=fig.transFigure, color=LIGHT, zorder=-1))
        fig.text(0.045, 0.032, SEAL, color=RED, fontsize=6.9, weight="bold", va="center")
        fig.text(0.5, 0.032, HYDRAULIC_SEAL, color=GRAY, fontsize=6.2, ha="center", va="center")
        fig.text(
            0.955,
            0.032,
            f"{page.number:02d} / {len(self.pages):02d}",
            color=GRAY,
            fontsize=6.7,
            ha="right",
            va="center",
        )
        asset = self.assets_dir / f"cf0_page_{page.number:02d}_{page.slug}.png"
        fig.savefig(asset, dpi=144, facecolor="white", metadata={"Software": "generate_cf0_review_report.py"})
        self.pdf.savefig(fig, dpi=200, facecolor="white")
        plt.close(fig)
        self.assets.append({"staged_path": asset, "page": page.number})

    def close(self) -> None:
        self.pdf.close()


def block_record(manifest: dict[str, Any], work_block_id: str) -> dict[str, Any]:
    return next(
        block
        for block in manifest["domain_assembly"]["work_blocks"]
        if block["work_block_id"] == work_block_id
    )


def candidates_for_block(manifest: dict[str, Any], work_block_id: str) -> list[dict[str, Any]]:
    values = [item for item in manifest["candidates"] if item["work_block_id"] == work_block_id]
    order = {candidate_id: index for index, candidate_id in enumerate(PROFILE_COLORS)}
    return sorted(values, key=lambda item: order[item["candidate_id"]])


def fields_for_block(data: ReportData, work_block_id: str) -> gpd.GeoDataFrame:
    wanted = set(block_record(data.manifest, work_block_id)["field_ids"])
    return data.fields[data.fields[data.field_id_column].astype(str).isin(wanted)]


def percentage(value: Any, decimals: int = 1) -> str:
    return format_number(float(value) * 100.0, decimals, "%")


def stage_status_color(status: str) -> str:
    return RED if status == "NO_FEASIBLE_FAMILY" else YELLOW


def render_cover(book: ReportBook, page: PageDefinition, data: ReportData) -> None:
    fig = book.new(page, kicker="CADERNO DE REVISÃO · FAMÍLIAS CONTÍNUAS")
    manifest = data.manifest
    fig.text(0.055, 0.825, "Famílias de sulcação\ncontínua", fontsize=27, color=INK, weight="bold", va="top")
    fig.text(
        0.058,
        0.685,
        "Comparação geométrica CF0 por componente físico,\ncom fase, coerência axial e gates verificáveis.",
        fontsize=11,
        color=GRAY,
        va="top",
        linespacing=1.35,
    )
    fig.add_artist(Rectangle((0.055, 0.530), 0.385, 0.072, transform=fig.transFigure, facecolor=RED))
    fig.text(0.2475, 0.566, SEAL, color="white", fontsize=8.8, weight="bold", ha="center", va="center")
    add_card(
        fig,
        (0.055, 0.310, 0.18, 0.150),
        "Blocos físicos",
        "Componentes conexos avaliados\nseparadamente.",
        value=str(len(manifest["domain_assembly"]["work_blocks"])),
        accent=GREEN,
    )
    add_card(
        fig,
        (0.255, 0.310, 0.18, 0.150),
        "Famílias",
        "Três perfis geométricos\nem cada bloco.",
        value=str(len(manifest["candidates"])),
        accent=BLUE,
    )
    status = manifest["stage_status"]
    fig.text(0.057, 0.245, "STATUS DO ESTÁGIO", color=GRAY, fontsize=7, weight="bold")
    fig.text(0.057, 0.214, status, color=stage_status_color(status), fontsize=12, weight="bold")
    fig.text(
        0.057,
        0.165,
        "A validação do pacote-fonte é pré-condição deste relatório.\nAprovação hidráulica e liberação operacional não fazem parte do CF0.",
        fontsize=7.8,
        color=INK,
        va="top",
    )

    ax = fig.add_axes((0.485, 0.115, 0.465, 0.745))
    add_terrain(ax, data.terrain, alpha=0.62)
    for block_id, geometry in data.work_blocks.items():
        add_geometry_outline(ax, [geometry], color="#f6f8f5", linewidth=2.5, zorder=7)
        add_geometry_outline(ax, [geometry], color=DEEP, linewidth=0.8, zorder=8)
        point = geometry.representative_point()
        ax.text(
            point.x,
            point.y,
            block_id,
            fontsize=6.4,
            color=INK,
            ha="center",
            va="center",
            bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.72, "pad": 1.2},
            zorder=15,
        )
    for candidate_id, color in PROFILE_COLORS.items():
        subset = data.rows[data.rows["candidate_id"] == candidate_id]
        add_linework(ax, subset.geometry, color=color, linewidth=0.45, alpha=0.62)
    add_geometry_outline(ax, data.fields.geometry, color=BOUNDARY, linewidth=1.0, zorder=10)
    if not data.fields.empty:
        set_map_extent(ax, data.fields.total_bounds, pad_fraction=0.025)
    add_north_arrow(ax)
    ax.set_title("Visão geral dos domínios e famílias publicadas", loc="left", weight="bold")
    handles = [
        Line2D([0], [0], color=color, lw=1.5, label=PROFILE_SHORT[candidate])
        for candidate, color in PROFILE_COLORS.items()
    ]
    ax.legend(handles=handles, loc="lower right", frameon=True, fontsize=6.4, ncol=3)
    book.finish(fig, page)


def render_scope(book: ReportBook, page: PageDefinition, data: ReportData) -> None:
    fig = book.new(page)
    add_intro(fig, "O CF0 responde se uma família geométrica contínua é coerente e extraível; não dimensiona conservação ou hidráulica.")
    add_card(
        fig,
        (0.045, 0.600, 0.275, 0.215),
        "Produzido",
        "Campo axial local, fase escalar, isolinhas C2, métricas geométricas, topologia, declividade ao longo das linhas e rasters de revisão.",
        value="CF0",
        accent=GREEN,
    )
    add_card(
        fig,
        (0.345, 0.600, 0.275, 0.215),
        "Gate de publicação",
        "Somente linhas de famílias GEOMETRIC_PASS entram em continuous_rows. Famílias rejeitadas permanecem sem linha executiva.",
        value="GEOMETRIA",
        accent=BLUE,
    )
    add_card(
        fig,
        (0.645, 0.600, 0.31, 0.215),
        "Não produzido",
        "Vazão, capacidade, erosão, terraço, ESD, base larga/passante, locação de POA, sequência de tráfego e arquivo de guiamento.",
        value="NÃO GERADO",
        accent=RED,
    )
    fig.text(
        0.045,
        0.245,
        "GeoPackage-fonte: 4 camadas obrigatórias · continuous_rows · diagnostic_rows · family_summary · hydraulic_precheck.",
        fontsize=7.0,
        color=GRAY,
        weight="bold",
    )
    profiles = data.manifest["solver_parameters"]["candidate_profiles"]
    for index, profile in enumerate(profiles):
        candidate_id = profile["candidate_id"]
        weights = profile["orientation_weights"]
        x = 0.045 + index * 0.305
        add_card(
            fig,
            (x, 0.285, 0.275, 0.215),
            candidate_id,
            (
                f"{profile['label']}\n"
                f"Peso contorno: {format_number(weights['contour'], 2)} · "
                f"eixo longo: {format_number(weights['long_axis'], 2)} · "
                f"borda: {format_number(weights['boundary'], 2)}"
            ),
            value=PROFILE_SHORT[candidate_id],
            accent=PROFILE_COLORS[candidate_id],
        )
    fig.text(
        0.045,
        0.205,
        "Os pesos são parâmetros versionados da busca geométrica CF0. Não são limites agronômicos nem uma recomendação de manejo.",
        fontsize=8.3,
        color=INK,
        weight="bold",
    )
    fig.text(
        0.045,
        0.148,
        "Leitura segura: compare os cenários, revise os blockers e use os rasters para auditoria. Nenhum resultado desta etapa deve ser enviado à máquina.",
        fontsize=8.2,
        color=GRAY,
    )
    book.finish(fig, page)


def render_method(book: ReportBook, page: PageDefinition, data: ReportData) -> None:
    fig = book.new(page)
    add_intro(fig, "Fluxo declarado no manifesto e revalidado antes da abertura do GeoPackage e dos rasters.")
    stages = [
        ("1", "Domínio", "Superfície útil por componente conexo; nenhum salto sobre gap físico."),
        ("2", "Orientação axial", "Mistura contorno/eixo longo em representação de ângulo duplo."),
        ("3", "Fase", "Gauge explícito e projeção alternada; eikonal usado somente como gate residual."),
        ("4", "Extração", "Quatro offsets discretos, halo LSQ, recorte físico e extensão tangente fail-closed."),
        ("5", "QA", "Espaçamento normal, integrabilidade, orientação, raio, topologia e cobertura proxy."),
    ]
    for index, (number, title, body) in enumerate(stages):
        x = 0.045 + index * 0.185
        add_card(
            fig,
            (x, 0.595, 0.165, 0.205),
            title,
            body,
            value=number,
            accent=[GREEN, BLUE, PURPLE, YELLOW, RED][index],
            body_size=7.0,
        )
    phase = data.manifest["solver_parameters"]["phase"]
    orientation = data.manifest["solver_parameters"]["orientation"]
    extraction = data.manifest["solver_parameters"]["extraction"]
    parameters = [
        ["Solver", data.manifest["solver_parameters"]["solver_revision"], "rastreabilidade"],
        ["Resolução", format_number(data.manifest["solver_parameters"]["grid_resolution_m"], 2, " m"), "grade CF0"],
        ["Espaçamento", format_number(data.manifest["solver_parameters"]["row_spacing_m"], 3, " m"), "intervalo de fase"],
        ["Suavização axial", format_number(orientation["smoothing_radius_m"], 2, " m"), "campo de orientação"],
        ["Coerência mínima", format_number(orientation["minimum_coherence"], 3), "gate"],
        ["Frustração máxima", format_number(orientation["maximum_frustration_deg"], 2, "°"), "gate"],
        ["Eikonal P95", format_number(phase["eikonal_residual_tolerance"], 4), "RESIDUAL_GATE_ONLY"],
        ["Integrabilidade P95", format_number(phase["integrability_residual_tolerance"], 4), "gate"],
        ["Spline", extraction["spline_method"], "geometria final"],
        ["Snap de ponta", format_number(extraction["endpoint_snap_tolerance_m"], 3, " m"), "borda física"],
        ["Máscara da fase", extraction["solve_mask_rasterization"], "supercover"],
        ["Extrapolação do contorno", extraction["contour_extrapolation_method"], "halo numérico"],
        ["Halo de extrapolação", f"{extraction['contour_extrapolation_halo_cells']} célula", "fora da borda"],
        ["Extensão de endpoint", extraction["endpoint_extension_mode"], "sem ligação lateral"],
    ]
    ax = fig.add_axes((0.045, 0.145, 0.91, 0.400))
    styled_table(ax, parameters, ["Parâmetro", "Valor", "Papel"], widths=[0.25, 0.43, 0.32], font_size=6.2, scale_y=1.10)
    fig.text(
        0.045,
        0.085,
        "O halo LSQ existe apenas para suporte numérico da extração do contorno. A fase continua resolvida no supercover; linhas são recortadas na geometria física e endpoints não recebem ligação lateral à nearest edge.",
        fontsize=6.8,
        color=RED,
        weight="bold",
    )
    book.finish(fig, page)


def render_phase_contract(book: ReportBook, page: PageDefinition, data: ReportData) -> None:
    fig = book.new(page, kicker="CONTRATO CF0 1.2 · BUSCA DISCRETA")
    parameters = data.manifest["solver_parameters"]
    phase = parameters["phase"]
    extraction = parameters["extraction"]
    add_intro(
        fig,
        "O gauge torna a fase reproduzível; quatro offsets configurados são avaliados e apenas um PASS pode ser selecionado.",
    )
    cards = [
        (
            "Gauge",
            "ÂNCORA ZERO",
            "Primeira célula válida em ordem lexicográfica por componente da máscara.",
            BLUE,
        ),
        (
            "Busca de offset",
            "4 TRIALS",
            "Frações 0, 0,25, 0,50 e 0,75 do espaçamento; seleção somente entre PASS.",
            PURPLE,
        ),
        (
            "Índices",
            "SEM ROTA",
            "phase_level_index é assinado; row_index é só sequência única local, não rota de máquina.",
            RED,
        ),
    ]
    for index, (title, value, body, color) in enumerate(cards):
        add_card(
            fig,
            (0.045 + index * 0.305, 0.650, 0.275, 0.155),
            title,
            body,
            value=value,
            accent=color,
            body_size=6.3,
        )
    table_rows = [
        ["gauge_method", phase["gauge_method"]],
        ["gauge_anchor_value_m", format_number(phase["gauge_anchor_value_m"], 1, " m")],
        ["solve_mask_required_component_count", phase["solve_mask_required_component_count"]],
        ["solve_mask_component_connectivity", phase["solve_mask_component_connectivity"]],
        ["phase_level_index_scope", extraction["phase_level_index_scope"]],
        ["phase_offset_search_revision", extraction["phase_offset_search_revision"]],
        ["phase_offset_fractions", json.dumps(extraction["phase_offset_fractions"], separators=(",", ":"))],
        ["phase_offset_selection_rule", wrap_text(extraction["phase_offset_selection_rule"], 72)],
    ]
    table_ax = fig.add_axes((0.045, 0.290, 0.91, 0.265))
    styled_table(
        table_ax,
        table_rows,
        ["Campo", "Valor declarado no manifesto"],
        widths=[0.35, 0.65],
        font_size=6.0,
        scale_y=1.08,
    )
    fig.text(
        0.045,
        0.230,
        f"phase_level_equation={extraction['phase_level_equation']}",
        fontsize=5.7,
        color=INK,
        weight="bold",
    )
    fig.text(
        0.045,
        0.190,
        f"phase_offset_search_scope={extraction['phase_offset_search_scope']}",
        fontsize=5.7,
        color=INK,
        weight="bold",
    )
    fig.text(
        0.045,
        0.150,
        f"row_index_definition={extraction['row_index_definition']}",
        fontsize=5.7,
        color=INK,
        weight="bold",
    )
    fig.text(
        0.045,
        0.105,
        "ROW_INDEX_IS_NOT_OPERATIONAL_ROUTE · row_index não é rota, ordem de trabalho, sequência do controlador, continuidade entre talhões ou alcance hidráulico.",
        fontsize=6.6,
        color=RED,
        weight="bold",
    )
    book.finish(fig, page)


def render_domains(book: ReportBook, page: PageDefinition, data: ReportData) -> None:
    fig = book.new(page)
    domain = data.manifest["domain_assembly"]
    add_intro(
        fig,
        f"Montagem: {domain['status']} · método {domain['geometry_method']} · cabeceira {format_number(domain['outer_headland_m'], 2, ' m')}.",
    )
    ax = fig.add_axes((0.045, 0.135, 0.50, 0.690))
    add_terrain(ax, data.terrain, alpha=0.60)
    add_geometry_outline(ax, data.fields.geometry, color=BOUNDARY, linewidth=1.0, zorder=10)
    for index, (block_id, geometry) in enumerate(data.work_blocks.items()):
        color = list(PROFILE_COLORS.values())[index % len(PROFILE_COLORS)]
        add_geometry_outline(ax, [geometry], color="white", linewidth=2.6, zorder=7)
        add_geometry_outline(ax, [geometry], color=color, linewidth=1.3, zorder=8)
        point = geometry.representative_point()
        ax.text(point.x, point.y, block_id, fontsize=6.0, ha="center", va="center", weight="bold", color=INK,
                bbox={"facecolor": "white", "edgecolor": color, "alpha": 0.86, "pad": 1.5}, zorder=15)
    set_map_extent(ax, data.fields.total_bounds, pad_fraction=0.03)
    add_north_arrow(ax)
    ax.set_title("Talhões de entrada e componentes úteis reconstruídos", loc="left", weight="bold")

    table_rows = []
    for block in domain["work_blocks"]:
        table_rows.append(
            [
                block["work_block_id"],
                ", ".join(block["field_ids"]),
                block["component_index"],
                format_number(float(block["usable_area_m2"]) / 10000.0, 3, " ha"),
            ]
        )
    table_ax = fig.add_axes((0.575, 0.390, 0.38, 0.435))
    styled_table(table_ax, table_rows, ["Bloco", "Talhão", "Comp.", "Área útil"], widths=[0.40, 0.22, 0.14, 0.24], font_size=6.5, scale_y=1.25)
    constraints = data.manifest["constraints"]
    add_card(
        fig,
        (0.575, 0.205, 0.18, 0.125),
        "Rede elétrica",
        "Aplicação da barreira",
        value=constraints["power_barrier_application"],
        accent=RED if constraints["power_inventory_status"] == "NOT_REVIEWED" else GREEN,
        body_size=6.7,
    )
    add_card(
        fig,
        (0.775, 0.205, 0.18, 0.125),
        "Superfícies operacionais",
        "Inventário de carreadores e acessos",
        value=constraints["operational_surfaces_status"],
        accent=YELLOW,
        body_size=6.7,
    )
    fig.text(
        0.575,
        0.135,
        "Divisas cadastrais foram mantidas como bordas de trabalho neste CF0. O modo cross-field não foi executado neste pacote.",
        fontsize=7.3,
        color=GRAY,
        va="top",
    )
    book.finish(fig, page)


def render_extraction_contract(book: ReportBook, page: PageDefinition, data: ReportData) -> None:
    fig = book.new(page, kicker="CONTRATO NUMÉRICO FAIL-CLOSED")
    extraction = data.manifest["solver_parameters"]["extraction"]
    chord_error_m = (
        float(extraction["spline_max_deviation_m"])
        * float(extraction["spline_representation_tolerance_fraction"])
    )
    add_intro(
        fig,
        "Supercover, halo LSQ fail-closed, recorte, extensão de endpoint e representação da spline têm papéis separados e versionados.",
    )
    cards = [
        (
            "Domínio da fase",
            "SUPERCOVER",
            "A fase é resolvida em uma única componente 4-conexa do supercover; o halo não amplia o domínio.",
            BLUE,
        ),
        (
            "Endpoint",
            "FAIL-CLOSED",
            "Somente tangente até a borda física; sem ligação lateral.",
            RED,
        ),
        (
            "Gradiente do halo",
            "LSQ LOCAL",
            "Estimativa local aceita só com vizinhança, rank, resíduo relativo e condição dentro do contrato.",
            GREEN,
        ),
    ]
    for index, (title, value, body, color) in enumerate(cards):
        add_card(
            fig,
            (0.045 + index * 0.305, 0.650, 0.275, 0.155),
            title,
            body,
            value=value,
            accent=color,
            body_size=6.2,
        )
    table_rows = [
        ["solve_mask_rasterization", extraction["solve_mask_rasterization"]],
        ["contour_extrapolation_method", extraction["contour_extrapolation_method"]],
        [f"contour_extrapolation_halo_cells={extraction['contour_extrapolation_halo_cells']}", "suporte numérico fora da borda"],
        [
            f"contour_gradient_lsq_max_radius_cells={extraction['contour_gradient_lsq_max_radius_cells']}",
            (
                f"vizinhos≥{extraction['contour_gradient_lsq_minimum_neighbor_count']} · "
                f"rank={extraction['contour_gradient_lsq_required_rank']} · "
                f"conectividade={extraction['contour_gradient_component_connectivity']}"
            ),
        ],
        [
            f"contour_gradient_lsq_max_relative_residual={float(extraction['contour_gradient_lsq_max_relative_residual']):.2f}",
            "gate fail-closed",
        ],
        [
            f"contour_gradient_lsq_max_condition_number={float(extraction['contour_gradient_lsq_max_condition_number']):.1f}",
            "gate fail-closed",
        ],
        ["endpoint_extension_mode", extraction["endpoint_extension_mode"]],
        ["spline_representation_method", extraction["spline_representation_method"]],
        [
            f"spline_representation_tolerance_fraction={float(extraction['spline_representation_tolerance_fraction']):.2f}",
            "fração do desvio máximo",
        ],
        [
            f"spline_max_deviation_m={float(extraction['spline_max_deviation_m']):.2f}",
            "limite geométrico mantido, em metros",
        ],
        ["erro de corda máximo derivado", f"{chord_error_m:.3f} m"],
        [
            "min_radius_m",
            (
                "mínimo conservador: raio analítico da spline e raio da LineString "
                f"reamostrada a cada {format_number(extraction['curvature_sample_step_m'], 2, ' m')}"
            ),
        ],
    ]
    table_ax = fig.add_axes((0.045, 0.230, 0.91, 0.330))
    styled_table(
        table_ax,
        table_rows,
        ["Campo", "Valor declarado"],
        widths=[0.43, 0.57],
        font_size=5.8,
        scale_y=1.02,
    )
    fig.text(
        0.045,
        0.155,
        "Regra: erro de corda ≤ spline_max_deviation_m × spline_representation_tolerance_fraction = 0,12 m × 0,10 = 0,012 m. O refinamento não afrouxa o desvio máximo de 0,12 m.",
        fontsize=7.0,
        color=INK,
        weight="bold",
    )
    fig.text(
        0.045,
        0.095,
        "Todos os vértices originais entram no fit, que falha com ROW_SPLINE_FIT_FAILED se não cumprir o contrato. O raio publicado nunca é exclusivamente analítico: adota o menor dos dois estimadores.",
        fontsize=6.6,
        color=GRAY,
    )
    book.finish(fig, page)


def render_domain_exclusions(book: ReportBook, page: PageDefinition, data: ReportData) -> None:
    fig = book.new(page, kicker="AUDITORIA DO DOMÍNIO FÍSICO")
    domain = data.manifest["domain_assembly"]
    filter_record = domain["work_block_filter"]
    excluded_all = domain["excluded_components"]
    index = int(page.payload or "0")
    excluded_chunks = chunks(excluded_all, 12) or [[]]
    excluded = excluded_chunks[index]
    add_intro(
        fig,
        "Componentes sem suporte espacial suficiente são registrados no manifesto, não viram bloco de trabalho e não recebem família inventada.",
    )
    cards = [
        (
            "Área mínima efetiva",
            format_number(filter_record["effective_minimum_area_m2"], 2, " m²"),
            f"{filter_record['minimum_grid_cell_count']} células pela regra declarada",
        ),
        (
            "Largura mínima efetiva",
            format_number(filter_record["effective_minimum_width_m"], 2, " m"),
            f"{format_number(filter_record['minimum_width_row_spacing_factor'], 2)} × espaçamento de linha",
        ),
        (
            "Componentes excluídos",
            str(len(excluded_all)),
            "partição revalidada pelo verificador CF0",
        ),
    ]
    for card_index, (title, value, body) in enumerate(cards):
        add_card(
            fig,
            (0.045 + card_index * 0.305, 0.665, 0.275, 0.140),
            title,
            body,
            value=value,
            accent=[BLUE, YELLOW, RED][card_index],
            body_size=6.8,
        )
    if excluded:
        table_rows = [
            [
                record["field_id"],
                record["component_index"],
                format_number(record["area_m2"], 2),
                format_number(record["minimum_rotated_width_m"], 2),
                record["estimated_grid_cell_count"],
                "; ".join(record["reason_codes"]),
            ]
            for record in excluded
        ]
        table_ax = fig.add_axes((0.045, 0.180, 0.91, 0.390))
        styled_table(
            table_ax,
            table_rows,
            ["Talhão", "Comp.", "Área m²", "Largura m", "Células", "Motivo contratual"],
            widths=[0.10, 0.075, 0.11, 0.11, 0.09, 0.515],
            font_size=6.0,
            scale_y=1.23,
        )
    else:
        add_card(
            fig,
            (0.045, 0.300, 0.91, 0.210),
            "Nenhum componente excluído nesta execução",
            "A lista obrigatória excluded_components está presente e vazia. Isso não elimina os demais gates de domínio, geometria, frota ou hidráulica.",
            value="0",
            accent=GREEN,
        )
    fig.text(
        0.045,
        0.105,
        "A exclusão é um resultado de suporte de grade/largura, não uma recomendação agronômica. A geometria desses slivers não é publicada como sulcação.",
        fontsize=7.0,
        color=GRAY,
        weight="bold",
    )
    book.finish(fig, page)


def render_qa(book: ReportBook, page: PageDefinition, data: ReportData) -> None:
    fig = book.new(page)
    qa = data.manifest["qa"]
    add_intro(fig, "Contagens do manifesto confrontadas com geometrias, atributos e células raster pelo verificador independente.")
    cards = [
        ("Famílias avaliadas", qa["candidate_field_count"], BLUE),
        ("GEOMETRIC_PASS", qa["geometric_pass_count"], GREEN),
        ("Sem família viável", qa["no_feasible_family_count"], RED),
        ("Hidráulica pendente", qa["hydraulic_unconfirmed_count"], YELLOW),
    ]
    for index, (title, value, color) in enumerate(cards):
        add_card(fig, (0.045 + index * 0.23, 0.650, 0.205, 0.145), title, "Resultado de gate CF0.", value=str(value), accent=color)
    geometry_rows = [
        ["Geometria inválida", qa["invalid_geometry_count"], "0 exigido"],
        ["Geometria sem Z", qa["non_3d_geometry_count"], "0 exigido"],
        ["Coordenada não finita", qa["nonfinite_coordinate_count"], "0 exigido"],
        ["Ponta interna", qa["internal_endpoint_count"], "0 em linhas publicadas"],
        ["Interseção", qa["intersection_count"], "0 em linhas publicadas"],
        ["Raster inválido", qa["raster_invalid_count"], "0 exigido"],
        ["Raio não avaliado", qa["radius_not_evaluated_count"], "bloqueia uso operacional"],
    ]
    table_ax = fig.add_axes((0.045, 0.205, 0.48, 0.360))
    styled_table(table_ax, geometry_rows, ["Verificação", "Contagem", "Regra"], widths=[0.43, 0.19, 0.38], font_size=7.0, scale_y=1.45)
    gate_failures = qa.get("gate_failures", [])
    add_card(
        fig,
        (0.565, 0.330, 0.39, 0.235),
        "Blockers geométricos observados",
        wrap_text("; ".join(gate_failures) if gate_failures else "Nenhum blocker nas famílias publicadas.", 62),
        value=str(len(gate_failures)),
        accent=RED if gate_failures else GREEN,
        body_size=7.0,
    )
    verification = data.verification
    add_card(
        fig,
        (0.565, 0.205, 0.39, 0.075),
        "Verificação do pacote-fonte",
        f"{verification['status']} · {verification['schema_validation']}",
        accent=GREEN,
        body_size=7.0,
    )
    book.finish(fig, page)


def render_phase_audit(book: ReportBook, page: PageDefinition, data: ReportData) -> None:
    fig = book.new(page, kicker="AUDITORIA POR CANDIDATO · SEM GEOMETRIA INVENTADA")
    index = int(page.payload or "0")
    subset = chunks(data.manifest["candidates"], 8)[index]
    extraction = data.manifest["solver_parameters"]["extraction"]
    add_intro(
        fig,
        "Offsets, trials, máscara e halo abaixo são copiados do manifesto CF0; linhas rejeitadas permanecem somente diagnósticas.",
    )
    rows = []
    for candidate in subset:
        selection = candidate["phase_offset_selection"]
        trials = selection["attempted_offsets"]
        passing_trials = sum(
            trial["geometric_status"] == "GEOMETRIC_PASS" for trial in trials
        )
        halo = candidate["contour_extrapolation_qa"]
        mask_components = candidate["metrics"]["solver_gate_metrics"][
            "solve_mask_component_count"
        ]
        rows.append(
            [
                PROFILE_SHORT[candidate["candidate_id"]],
                candidate["work_block_id"],
                candidate["phase_offset_role"],
                format_number(candidate["phase_offset_fraction"], 2),
                format_number(candidate["phase_offset_m"], 3),
                selection["selection_status"],
                f"{len(trials)} / {passing_trials} PASS",
                "—" if mask_components is None else str(mask_components),
                halo["gradient_estimation_status"],
                f"{halo['supported_halo_cell_count']}/{halo['halo_cell_count']}",
                f"{candidate['row_count']}/{candidate['diagnostic_row_count']}",
                wrap_text(",".join(candidate["blocker_codes"]) or "nenhum", 30),
            ]
        )
    table_ax = fig.add_axes((0.025, 0.355, 0.95, 0.450))
    styled_table(
        table_ax,
        rows,
        [
            "Perfil",
            "Bloco",
            "Papel offset",
            "fração",
            "m",
            "Seleção",
            "trials / PASS",
            "comp. máscara",
            "halo",
            "halo sup./total",
            "aprov./diag.",
            "blockers",
        ],
        widths=[0.05, 0.13, 0.095, 0.045, 0.045, 0.13, 0.075, 0.065, 0.075, 0.075, 0.065, 0.15],
        font_size=4.8,
        scale_y=1.40,
    )
    lsq_text = (
        f"{extraction['contour_extrapolation_method']} · "
        f"contour_gradient_lsq_max_relative_residual={float(extraction['contour_gradient_lsq_max_relative_residual']):.2f} · "
        f"contour_gradient_lsq_max_condition_number={float(extraction['contour_gradient_lsq_max_condition_number']):.1f} · "
        f"raio≤{extraction['contour_gradient_lsq_max_radius_cells']} células · "
        f"vizinhos≥{extraction['contour_gradient_lsq_minimum_neighbor_count']} · "
        f"rank={extraction['contour_gradient_lsq_required_rank']} · "
        f"conectividade={extraction['contour_gradient_component_connectivity']}"
    )
    add_card(
        fig,
        (0.045, 0.205, 0.91, 0.105),
        "Contrato LSQ do halo",
        wrap_text(lsq_text, 145),
        accent=PURPLE,
        body_size=6.1,
    )
    fig.text(
        0.045,
        0.148,
        "Falhas contratuais: ROW_SPLINE_FIT_FAILED · PHASE_HALO_GRADIENT_UNESTIMABLE · WORK_BLOCK_SOLVE_MASK_DISCONNECTED.",
        fontsize=6.7,
        color=RED,
        weight="bold",
    )
    fig.text(
        0.045,
        0.105,
        "continuous_rows: LINHAS APROVADAS · diagnostic_rows: LINHAS DIAGNÓSTICAS, NÃO APROVADO e NOT_AUTHORIZED.",
        fontsize=6.7,
        color=INK,
        weight="bold",
    )
    book.finish(fig, page)


def render_block_map(book: ReportBook, page: PageDefinition, data: ReportData) -> None:
    assert page.payload is not None
    work_block_id = page.payload
    fig = book.new(page, kicker="CENÁRIOS GEOMÉTRICOS POR BLOCO")
    block = block_record(data.manifest, work_block_id)
    geometry = data.work_blocks[work_block_id]
    fields = fields_for_block(data, work_block_id)
    add_intro(
        fig,
        f"Área útil {format_number(block['usable_area_m2'] / 10000.0, 3, ' ha')} · componente {block['component_index']} · comparação na mesma escala.",
    )
    candidates = candidates_for_block(data.manifest, work_block_id)
    for index, candidate in enumerate(candidates):
        candidate_id = candidate["candidate_id"]
        color = PROFILE_COLORS[candidate_id]
        ax = fig.add_axes((0.045 + index * 0.315, 0.270, 0.285, 0.525))
        add_terrain(ax, data.terrain, alpha=0.50)
        add_geometry_outline(ax, fields.geometry, color="#6d7771", linewidth=0.7, linestyle="dashed", zorder=8)
        add_geometry_outline(ax, [geometry], color="white", linewidth=3.0, zorder=9)
        add_geometry_outline(ax, [geometry], color=BOUNDARY, linewidth=1.2, zorder=10)
        published = data.rows[
            (data.rows["candidate_id"] == candidate_id)
            & (data.rows["work_block_id"] == work_block_id)
        ]
        add_linework(ax, published.geometry, color=color, linewidth=0.85, alpha=0.98, zorder=12)
        diagnostics = data.diagnostic_rows
        if not diagnostics.empty:
            diagnostics = diagnostics[
                (diagnostics["candidate_id"] == candidate_id)
                & (diagnostics["work_block_id"] == work_block_id)
            ]
            add_linework(
                ax,
                diagnostics.geometry,
                color="#9d2f2a",
                linewidth=0.75,
                alpha=0.82,
                zorder=11,
                linestyle="dashed",
            )
        set_map_extent(ax, geometry.bounds, pad_fraction=0.035)
        if index == 0:
            add_north_arrow(ax)
        ax.set_title(
            f"{PROFILE_SHORT[candidate_id]} · {candidate['geometric_status']}",
            loc="left",
            color=color if candidate["geometric_status"] == "GEOMETRIC_PASS" else RED,
            fontsize=8.4,
            weight="bold",
        )
        metrics = candidate["metrics"]
        detail = (
            f"aprovadas {candidate['row_count']} · diagnósticas {candidate['diagnostic_row_count']}\n"
            f"comprimento aprovado {format_number(candidate['total_length_m'] / 1000.0, 2, ' km')}\n"
            f"spacing erro P95 {format_number(metrics['spacing_error_p95_m'], 3, ' m')}\n"
            f"raio {metrics['radius_status']} · rampa máx. {format_number(metrics['maximum_abs_grade_pct'], 2, '%')}"
        )
        ax.text(
            0.02,
            0.02,
            detail,
            transform=ax.transAxes,
            va="bottom",
            fontsize=5.9,
            color=INK,
            bbox={"facecolor": "white", "edgecolor": "#cfd6d1", "alpha": 0.88, "pad": 2.5},
            zorder=20,
        )
        if candidate["geometric_status"] != "GEOMETRIC_PASS":
            label = "LINHAS DIAGNÓSTICAS · NÃO APROVADO" if not diagnostics.empty else "SEM LINHAS PUBLICADAS · NÃO APROVADO"
            ax.text(
                0.5,
                0.52,
                label,
                transform=ax.transAxes,
                ha="center",
                va="center",
                fontsize=7.2,
                weight="bold",
                color="white",
                bbox={"facecolor": RED, "edgecolor": "none", "alpha": 0.88, "pad": 4.0},
                zorder=25,
            )
    blockers = sorted({code for item in candidates for code in item["blocker_codes"]})
    fig.text(0.045, 0.210, "BLOCKERS DO BLOCO", fontsize=6.8, color=RED if blockers else GREEN, weight="bold")
    fig.text(
        0.045,
        0.181,
        wrap_text("; ".join(blockers) if blockers else "Nenhum blocker geométrico nas famílias publicadas.", 145),
        fontsize=7.2,
        color=INK,
        va="top",
    )
    fig.text(
        0.045,
        0.105,
        "Traço contínuo colorido = LINHAS APROVADAS de continuous_rows. Vermelho tracejado = LINHAS DIAGNÓSTICAS de diagnostic_rows: NÃO APROVADO e NOT_AUTHORIZED.",
        fontsize=6.8,
        color=GRAY,
    )
    book.finish(fig, page)


def add_axial_traces(ax: plt.Axes, raster: RasterData) -> None:
    if len(raster.arrays) < 2:
        return
    theta = raster.arrays[0]
    coherence = raster.arrays[1]
    valid = raster.masks[0] & raster.masks[1]
    rows, columns = theta.shape
    row_step = max(1, rows // 13)
    column_step = max(1, columns // 16)
    length = min(abs(raster.transform[1]), abs(raster.transform[5])) * 2.3
    traces: list[np.ndarray] = []
    strengths: list[float] = []
    for row in range(row_step // 2, rows, row_step):
        for column in range(column_step // 2, columns, column_step):
            if not valid[row, column]:
                continue
            angle = math.radians(float(theta[row, column]))
            center_x = raster.transform[0] + (column + 0.5) * raster.transform[1]
            center_y = raster.transform[3] + (row + 0.5) * raster.transform[5]
            direction = np.asarray([math.cos(angle), math.sin(angle)], dtype=float)
            center = np.asarray([center_x, center_y], dtype=float)
            traces.append(np.vstack((center - direction * length, center + direction * length)))
            strengths.append(float(coherence[row, column]))
    if traces:
        ax.add_collection(
            mpl.collections.LineCollection(
                traces,
                colors=[(1, 1, 1, 0.30 + 0.60 * min(max(value, 0.0), 1.0)) for value in strengths],
                linewidths=0.55,
                zorder=9,
            )
        )


def render_block_rasters(book: ReportBook, page: PageDefinition, data: ReportData) -> None:
    assert page.payload is not None
    work_block_id = page.payload
    fig = book.new(page, kicker="DIAGNÓSTICO DO CAMPO CONTÍNUO")
    block = block_record(data.manifest, work_block_id)
    field_id = str(block["field_ids"][0])
    geometry = data.work_blocks[work_block_id]
    add_intro(fig, "À esquerda, fase escalar em metros; à direita, coerência [0,1] com traços da orientação axial [0°,180°).")
    candidates = candidates_for_block(data.manifest, work_block_id)
    for row_index, candidate in enumerate(candidates):
        candidate_id = candidate["candidate_id"]
        phase = data.rasters[(candidate_id, field_id, work_block_id, "phase")]
        coherence = data.rasters[(candidate_id, field_id, work_block_id, "orientation_coherence")]
        y = 0.645 - row_index * 0.245
        phase_ax = fig.add_axes((0.080, y, 0.365, 0.205))
        phase_image = phase_ax.imshow(masked_band(phase), extent=phase.extent, origin="upper", cmap="viridis", zorder=1)
        add_geometry_outline(phase_ax, [geometry], color="white", linewidth=0.8, zorder=8)
        set_map_extent(phase_ax, geometry.bounds, pad_fraction=0.01)
        phase_ax.set_title(f"{PROFILE_SHORT[candidate_id]} · fase (m)", loc="left", fontsize=7.4, weight="bold", color=PROFILE_COLORS[candidate_id])
        phase_ax.set_xlabel("")
        phase_ax.set_ylabel("")
        phase_ax.tick_params(labelsize=5.1)
        colorbar = fig.colorbar(phase_image, ax=phase_ax, fraction=0.027, pad=0.012)
        colorbar.ax.tick_params(labelsize=5.0)

        coherence_ax = fig.add_axes((0.555, y, 0.365, 0.205))
        coherence_image = coherence_ax.imshow(
            masked_band(coherence, 1),
            extent=coherence.extent,
            origin="upper",
            cmap="RdYlGn",
            vmin=0,
            vmax=1,
            zorder=1,
        )
        add_axial_traces(coherence_ax, coherence)
        add_geometry_outline(coherence_ax, [geometry], color="white", linewidth=0.8, zorder=10)
        set_map_extent(coherence_ax, geometry.bounds, pad_fraction=0.01)
        coherence_ax.set_title(f"{PROFILE_SHORT[candidate_id]} · coerência axial", loc="left", fontsize=7.4, weight="bold", color=PROFILE_COLORS[candidate_id])
        coherence_ax.set_xlabel("")
        coherence_ax.set_ylabel("")
        coherence_ax.tick_params(labelsize=5.1)
        colorbar = fig.colorbar(coherence_image, ax=coherence_ax, fraction=0.027, pad=0.012)
        colorbar.ax.tick_params(labelsize=5.0)
    fig.text(
        0.045,
        0.085,
        "A fase não é um modelo hidráulico. O residual eikonal é somente um gate numérico; não representa vazão, nível d'água ou capacidade de seção.",
        fontsize=7.0,
        color=RED,
        weight="bold",
    )
    book.finish(fig, page)


def render_gates(book: ReportBook, page: PageDefinition, data: ReportData) -> None:
    fig = book.new(page, kicker="COMPARAÇÃO QUANTITATIVA")
    index = int(page.payload or "0")
    subset = chunks(data.manifest["candidates"], 8)[index]
    add_intro(fig, "PASS geométrico exige todos os gates simultaneamente; vazio ou rejeitado não é promovido por ranking relativo.")
    rows = []
    for candidate in subset:
        metrics = candidate["metrics"]
        rows.append(
            [
                PROFILE_SHORT[candidate["candidate_id"]],
                candidate["work_block_id"],
                candidate["geometric_status"].replace("GEOMETRIC_", ""),
                candidate["row_count"],
                format_number(candidate["total_length_m"] / 1000.0, 2),
                format_number(metrics["spacing_error_p95_m"], 3),
                format_number(metrics["eikonal_residual_p95"], 4),
                format_number(metrics["integrability_residual_p95"], 4),
                format_number(metrics["orientation_misalignment_p95_deg"], 2),
                metrics["radius_status"],
                format_number(metrics["coverage_proxy_ratio"], 3),
            ]
        )
    ax = fig.add_axes((0.035, 0.315, 0.93, 0.490))
    table = styled_table(
        ax,
        rows,
        ["Perfil", "Bloco", "Status", "Linhas", "km", "Esp. P95 m", "Eikonal", "Integrab.", "Orient. °", "Raio", "Cob. proxy"],
        widths=[0.055, 0.155, 0.105, 0.055, 0.055, 0.085, 0.083, 0.083, 0.075, 0.095, 0.085],
        font_size=5.8,
        scale_y=1.55,
    )
    for row_index, candidate in enumerate(subset, start=1):
        status_cell = table[(row_index, 2)]
        status_cell.get_text().set_weight("bold")
        status_cell.get_text().set_color(GREEN if candidate["geometric_status"] == "GEOMETRIC_PASS" else RED)
    parameters = data.manifest["solver_parameters"]
    extraction = parameters["extraction"]
    phase = parameters["phase"]
    orientation = parameters["orientation"]
    geometry_threshold_text = (
        f"Limites: erro de espaçamento ≤ {format_number(parameters['extraction']['spacing_tolerance_m'], 3, ' m')} · "
        f"área fora da tolerância ≤ {percentage(extraction['maximum_spacing_outside_tolerance_fraction'])} · "
        f"coverage_proxy_ratio {format_number(extraction['coverage_proxy_minimum'], 2)}–{format_number(extraction['coverage_proxy_maximum'], 2)} · "
        "topologia sem pontas internas/interseções/loops."
    )
    residual_threshold_text = (
        f"Resíduos do manifesto: eikonal P95 ≤ {format_number(phase['eikonal_residual_tolerance'], 4)} · "
        f"integrabilidade P95 ≤ {format_number(phase['integrability_residual_tolerance'], 4)} e RMS ≤ {format_number(phase['maximum_integrability_residual_rms'], 4)} · "
        f"erro angular P95 ≤ {format_number(orientation['maximum_frustration_deg'], 2, '°')} · "
        f"baixa coerência ≤ {percentage(orientation['maximum_low_coherence_fraction'])} · "
        f"arestas abruptas ≤ {percentage(orientation['maximum_abrupt_edge_fraction'])} · "
        f"conflitos de ciclo ≤ {percentage(orientation['maximum_cycle_conflict_fraction'])} · "
        f"gradiente crítico ≤ {percentage(phase['maximum_critical_fraction'])} · cut locus ≤ {percentage(phase['maximum_cut_locus_fraction'])}."
    )
    add_card(fig, (0.045, 0.185, 0.91, 0.090), "Gates geométricos versionados", wrap_text(geometry_threshold_text, 145), accent=BLUE, body_size=6.7)
    add_card(fig, (0.045, 0.085, 0.91, 0.085), "Gates do campo e dos resíduos", wrap_text(residual_threshold_text, 150), accent=PURPLE, body_size=6.2)
    fig.text(
        0.045,
        0.062,
        "Raio só pode receber PASS quando o requisito da frota foi declarado; caso contrário permanece NOT_EVALUATED e impede uso operacional.",
        fontsize=6.4,
        color=RED,
        weight="bold",
    )
    book.finish(fig, page)


def hydraulic_row(data: ReportData, candidate: dict[str, Any]) -> pd.Series | None:
    frame = data.hydraulic
    matches = frame[
        (frame["candidate_id"] == candidate["candidate_id"])
        & (frame["field_id"].astype(str) == str(candidate["field_id"]))
        & (frame["work_block_id"] == candidate["work_block_id"])
    ]
    return None if matches.empty else matches.iloc[0]


def render_hydraulic(book: ReportBook, page: PageDefinition, data: ReportData) -> None:
    fig = book.new(page, kicker="PRÉ-CHECAGEM · SEM SOLVER HIDRÁULICO")
    index = int(page.payload or "0")
    candidates = chunks(data.manifest["candidates"], 8)[index]
    add_intro(
        fig,
        "Declividade ao longo das linhas é diagnóstico geométrico. Sem evento IDF, solo, bacia completa, receptor e seção não há vazão ou capacidade.",
    )
    table_rows = []
    for candidate in candidates:
        record = hydraulic_row(data, candidate)
        if record is None:
            table_rows.append([PROFILE_SHORT[candidate["candidate_id"]], candidate["work_block_id"], "AUSENTE", "—", "—", "—", "registro ausente"])
            continue
        table_rows.append(
            [
                PROFILE_SHORT[candidate["candidate_id"]],
                candidate["work_block_id"],
                record["hydraulic_status"],
                format_number(record.get("grade_p95_pct"), 2),
                format_number(record.get("grade_max_pct"), 2),
                int(record.get("reversal_count") or 0),
                wrap_text(str(record.get("missing_inputs") or "nenhum declarado"), 38),
            ]
        )
    ax = fig.add_axes((0.035, 0.375, 0.93, 0.430))
    styled_table(
        ax,
        table_rows,
        ["Perfil", "Bloco", "Status", "Rampa P95 %", "Rampa máx. %", "Reversões", "Insumos ausentes/não confirmados"],
        widths=[0.065, 0.17, 0.18, 0.10, 0.10, 0.075, 0.31],
        font_size=5.9,
        scale_y=1.52,
    )
    input_columns = [
        "idf_status",
        "soil_status",
        "contributing_area_status",
        "outlet_status",
        "receiver_status",
        "section_status",
        "roughness_status",
        "downstream_status",
    ]
    counts = []
    for column in input_columns:
        values = data.hydraulic[column].value_counts().to_dict() if column in data.hydraulic else {}
        counts.append(
            [
                column.replace("_status", "").replace("_", " "),
                int(values.get("PROVIDED", 0)),
                int(values.get("MISSING", 0)),
                int(values.get("UNCONFIRMED", 0)),
            ]
        )
    status_ax = fig.add_axes((0.045, 0.125, 0.47, 0.195))
    styled_table(status_ax, counts, ["Insumo", "PROVIDED", "MISSING", "UNCONFIRMED"], widths=[0.48, 0.17, 0.17, 0.18], font_size=5.7, scale_y=1.14)
    add_card(
        fig,
        (0.555, 0.125, 0.40, 0.195),
        "Gate hidráulico",
        "CF0_NO_HYDRAULIC_SOLVER permanece ativo mesmo se algum insumo constar como PROVIDED. Não foram calculadas vazão, velocidade, tensão de cisalhamento, lâmina, bordo livre ou capacidade.",
        value="HYDRAULIC_UNCONFIRMED",
        accent=RED,
        body_size=7.0,
    )
    book.finish(fig, page)


def limitation_description(code: str) -> str:
    descriptions = {
        "CF0_NOT_FOR_GUIDANCE": "Saída de triagem geométrica; exportação de guiamento é proibida.",
        "HYDRAULIC_UNCONFIRMED": "Não há dimensionamento hidráulico, erosivo ou de estruturas.",
        "FIELD_VALIDATION_REQUIRED": "MDT, limites, obstáculos e comportamento operacional exigem validação de campo.",
        "RADIUS_REQUIREMENT_NOT_PROVIDED": "Raio mínimo da frota não foi declarado; colheitabilidade não está aprovada.",
        "POWER_INVENTORY_NOT_REVIEWED": "Rede elétrica não revisada; continuidade entre áreas não pode ser liberada.",
        "OPERATIONAL_SURFACES_INCOMPLETE": "Carreadores, portais, cabeceiras e obstáculos ainda não formam inventário completo.",
    }
    return descriptions.get(code, "Limitação declarada pelo pacote CF0; revisar o manifesto e o blocker correspondente.")


def render_limitations(book: ReportBook, page: PageDefinition, data: ReportData) -> None:
    fig = book.new(page, kicker="LIMITES DE USO E RESPONSABILIDADE")
    limitations = data.manifest["release_limitations"]
    add_intro(fig, "Uma família geometricamente viável ainda não é um projeto agronômico, conservacionista, hidráulico ou executivo.")
    y = 0.820
    for index, code in enumerate(limitations):
        column = 0 if index < math.ceil(len(limitations) / 2) else 1
        local_index = index if column == 0 else index - math.ceil(len(limitations) / 2)
        x = 0.045 + column * 0.465
        local_y = y - local_index * 0.135
        add_card(
            fig,
            (x, local_y - 0.095, 0.425, 0.105),
            code,
            limitation_description(code),
            accent=RED if code in {"CF0_NOT_FOR_GUIDANCE", "HYDRAULIC_UNCONFIRMED"} else YELLOW,
            body_size=6.9,
        )
    bottom_y = max(0.120, 0.820 - max(math.ceil(len(limitations) / 2), 1) * 0.135 - 0.015)
    fig.add_artist(Rectangle((0.045, bottom_y), 0.91, 0.075, transform=fig.transFigure, facecolor=RED))
    fig.text(
        0.5,
        bottom_y + 0.0375,
        "SEM APROVAÇÃO HIDRÁULICA · SEM HOMOLOGAÇÃO AGRONÔMICA · SEM GUIAMENTO",
        fontsize=10,
        color="white",
        weight="bold",
        ha="center",
        va="center",
    )
    fig.text(
        0.045,
        0.080,
        "diagnostic_rows é uma das quatro camadas obrigatórias do CF0 1.2; suas feições permanecem NOT_APPROVED e NOT_AUTHORIZED.",
        fontsize=7.1,
        color=GRAY,
    )
    book.finish(fig, page)


def render_roadmap(book: ReportBook, page: PageDefinition, data: ReportData) -> None:
    del data
    fig = book.new(page, kicker="PRODUTOS CONSERVACIONISTAS POSTERIORES")
    add_intro(fig, "Os produtos C1, C2 e C3 usarão famílias CF0 viáveis como insumo, acrescentando dimensionamento físico e hidráulico.")
    scenarios = [
        (
            "C1",
            "CURVA EMBUTIDA",
            "Estrutura mais estreita/profunda; sulcação por faixas e travessias somente quando projetadas.",
            "Dimensionar seção, espaçamento, armazenamento/descarga, excedência, superfície proposta e manobras.",
            GREEN,
        ),
        (
            "C2",
            "BASE LARGA / PASSANTE",
            "Seção larga e suave; trânsito apenas em crossing nodes validados para a frota e o perfil 3D.",
            "Verificar seção nova/degradada, capacidade, taludes, envelope, raio e transferência hidráulica.",
            YELLOW,
        ),
        (
            "C3",
            "ESD",
            "Greide controlado distribui escoamento entre sulcos ligados a outlets e receptores seguros.",
            "Modelar contribuição externa, convergência, transferência, CEV quando necessário, excedência e falha.",
            BLUE,
        ),
    ]
    for index, (code, title, concept, needs, color) in enumerate(scenarios):
        x = 0.045 + index * 0.305
        fig.add_artist(
            FancyBboxPatch(
                (x, 0.225),
                0.275,
                0.565,
                boxstyle="round,pad=0.008,rounding_size=0.005",
                transform=fig.transFigure,
                facecolor=PALE,
                edgecolor="#cfd7d1",
                linewidth=0.8,
            )
        )
        fig.add_artist(Rectangle((x, 0.720), 0.275, 0.070, transform=fig.transFigure, facecolor=color))
        fig.text(x + 0.018, 0.755, code, fontsize=18, color="white", weight="bold", va="center")
        fig.text(x + 0.075, 0.755, title, fontsize=8.6, color="white", weight="bold", va="center")
        fig.text(x + 0.018, 0.670, "CONCEITO", fontsize=6.8, color=color, weight="bold")
        fig.text(x + 0.018, 0.635, wrap_text(concept, 42), fontsize=7.6, color=INK, va="top", linespacing=1.32)
        fig.text(x + 0.018, 0.505, "PARA GERAR", fontsize=6.8, color=color, weight="bold")
        fig.text(x + 0.018, 0.470, wrap_text(needs, 42), fontsize=7.5, color=INK, va="top", linespacing=1.32)
        fig.add_artist(Rectangle((x + 0.018, 0.265), 0.239, 0.072, transform=fig.transFigure, facecolor=RED))
        fig.text(x + 0.1375, 0.301, "NÃO GERADO", fontsize=11, color="white", weight="bold", ha="center", va="center")
    fig.text(
        0.045,
        0.145,
        "ESD não é uma linha, curva de nível ou ausência de terraço. Cada alcance precisa de área contribuinte, capacidade e destino estável calculados.",
        fontsize=7.8,
        color=RED,
        weight="bold",
    )
    fig.text(
        0.045,
        0.095,
        "O termo continuidade C2 da spline geométrica é matemático e não significa que o cenário C2 base larga/passante tenha sido produzido.",
        fontsize=7.0,
        color=GRAY,
    )
    book.finish(fig, page)


def source_trace_rows(data: ReportData) -> list[list[str]]:
    records: list[tuple[str, dict[str, Any]]] = [("Manifesto CF0", file_record(data.manifest_path))]
    for label, value in data.manifest["inputs"].items():
        if not isinstance(value, dict):
            continue
        if {"path", "size_bytes", "sha256"} <= set(value):
            records.append((label, value))
    records.append(("GeoPackage", data.manifest["outputs"]["geopackage"]))
    return [
        [label, Path(record["path"]).name, str(record["size_bytes"]), str(record["sha256"])[:18] + "…"]
        for label, record in records[:7]
    ]


def render_traceability(book: ReportBook, page: PageDefinition, data: ReportData) -> None:
    fig = book.new(page, kicker="ENCERRAMENTO E CADEIA DE EVIDÊNCIA")
    add_intro(fig, "Este PDF é uma vista derivada. O manifesto CF0, seus hashes, GeoPackage e rasters continuam sendo a evidência primária.")
    conclusions = [
        "O pacote CF0 foi validado integralmente antes da renderização deste caderno.",
        "CF0A, CF0B e CF0C são perfis de busca geométrica; não correspondem aos produtos C1, C2 e C3.",
        "A comparação usa componentes físicos conexos e não cria continuidade sobre gaps ou divisas não dissolvidas.",
        "Famílias rejeitadas não aparecem em continuous_rows; diagnostic_rows é obrigatório e sempre NÃO APROVADO.",
        "phase_level_index identifica um nível assinado; row_index é apenas uma sequência local e não uma rota operacional.",
        "Hidráulica permanece HYDRAULIC_UNCONFIRMED e nenhum arquivo é autorizado para guiamento.",
    ]
    y = 0.805
    for index, conclusion in enumerate(conclusions, start=1):
        fig.add_artist(
            FancyBboxPatch(
                (0.050, y - 0.034),
                0.030,
                0.030,
                boxstyle="round,pad=0.003,rounding_size=0.004",
                transform=fig.transFigure,
                facecolor=DEEP,
                edgecolor="none",
            )
        )
        fig.text(0.065, y - 0.019, str(index), color="white", fontsize=7, weight="bold", ha="center", va="center")
        fig.text(0.095, y - 0.007, wrap_text(conclusion, 68), fontsize=7.8, color=INK, va="top")
        y -= 0.093
    table_ax = fig.add_axes((0.565, 0.315, 0.390, 0.480))
    styled_table(
        table_ax,
        source_trace_rows(data),
        ["Fonte", "Arquivo", "bytes", "SHA-256"],
        widths=[0.22, 0.33, 0.16, 0.29],
        font_size=5.8,
        scale_y=1.38,
    )
    parameters = data.manifest["solver_parameters"]
    extraction = parameters["extraction"]
    phase = parameters["phase"]
    boundary_text = (
        f"gauge_method={phase['gauge_method']} · "
        f"solve_mask={phase['solve_mask_required_component_count']} componente/"
        f"{phase['solve_mask_component_connectivity']}-conexa · "
        f"contour_extrapolation_method={extraction['contour_extrapolation_method']} · "
        f"LSQ residual≤{float(extraction['contour_gradient_lsq_max_relative_residual']):.2f}/"
        f"condição≤{float(extraction['contour_gradient_lsq_max_condition_number']):.1f}"
    )
    add_card(
        fig,
        (0.565, 0.145, 0.39, 0.130),
        f"Contrato de fase e borda · {data.verification['status']}",
        wrap_text(boundary_text, 60),
        accent=GREEN,
        body_size=5.7,
    )
    fig.add_artist(Rectangle((0.045, 0.075), 0.91, 0.055, transform=fig.transFigure, facecolor=RED))
    fig.text(0.5, 0.1025, SEAL, fontsize=10.2, color="white", weight="bold", ha="center", va="center")
    book.finish(fig, page)


def render_page(book: ReportBook, page: PageDefinition, data: ReportData) -> None:
    renderers = {
        "cover": render_cover,
        "scope": render_scope,
        "method": render_method,
        "phase_contract": render_phase_contract,
        "domains": render_domains,
        "extraction_contract": render_extraction_contract,
        "domain_exclusions": render_domain_exclusions,
        "qa": render_qa,
        "phase_audit": render_phase_audit,
        "block_map": render_block_map,
        "block_rasters": render_block_rasters,
        "gates": render_gates,
        "hydraulic": render_hydraulic,
        "limitations": render_limitations,
        "roadmap": render_roadmap,
        "traceability": render_traceability,
    }
    renderer = renderers.get(page.kind)
    if renderer is None:
        raise RuntimeError(f"Tipo de página desconhecido: {page.kind}")
    renderer(book, page, data)


REQUIRED_GLOBAL_TOKENS = (
    "CF0A_CONSERVACAO",
    "CF0B_EQUILIBRIO",
    "CF0C_OPERACAO",
    "HYDRAULIC_UNCONFIRMED",
    "C1",
    "C2",
    "C3",
    "NÃO GERADO",
    "diagnostic_rows",
    "NÃO APROVADO",
    "ALL_TOUCHED_SUPERCOVER",
    "ZERO_AT_LEXICOGRAPHIC_FIRST_VALID_CELL_PER_COMPONENT",
    "phase_level_m = phase_level_index * row_spacing_m + phase_offset_m",
    "CANDIDATE_WORK_BLOCK_PHASE_LEVEL",
    "CF0_PHASE_OFFSET_QUARTER_SPACING_V1",
    "DISCRETE_CONFIGURED_OFFSETS_NOT_CONTINUOUS_GAUGE_INVARIANCE",
    "ROW_INDEX_IS_NOT_OPERATIONAL_ROUTE",
    "FIRST_ORDER_LOCAL_LSQ_GRADIENT_FAIL_CLOSED",
    "contour_extrapolation_halo_cells=1",
    "contour_gradient_lsq_max_relative_residual=0.30",
    "contour_gradient_lsq_max_condition_number=100.0",
    "TANGENT_ONLY_FAIL_CLOSED",
    "ROW_SPLINE_FIT_FAILED",
    "PHASE_HALO_GRADIENT_UNESTIMABLE",
    "WORK_BLOCK_SOLVE_MASK_DISCONNECTED",
    "ADAPTIVE_CHORD_ERROR_PRESERVE_VERTICES",
    "spline_representation_tolerance_fraction=0.10",
    "spline_max_deviation_m=0.12",
)


def preflight_report(
    pdf_path: Path,
    assets: Sequence[dict[str, Any]],
    pages: Sequence[PageDefinition],
) -> dict[str, Any]:
    reader = PdfReader(str(pdf_path))
    page_texts = [page.extract_text() or "" for page in reader.pages]
    combined_text = "\n".join(page_texts)
    media_boxes = []
    for index, pdf_page in enumerate(reader.pages, start=1):
        width = float(pdf_page.mediabox.width)
        height = float(pdf_page.mediabox.height)
        media_boxes.append(
            {
                "page": index,
                "width_pt": round(width, 3),
                "height_pt": round(height, 3),
                "a4_landscape": (
                    width > height
                    and abs(width - 841.68) <= 2.0
                    and abs(height - 595.44) <= 2.0
                ),
            }
        )
    seal_checks = [
        {
            "page": index,
            "release_seal": "CF0 PROTÓTIPO GEOMÉTRICO" in text_value,
            "guidance_warning": "NÃO USAR PARA GUIAMENTO" in text_value,
            "hydraulic_warning": "HYDRAULIC_UNCONFIRMED" in text_value,
        }
        for index, text_value in enumerate(page_texts, start=1)
    ]
    required_tokens = {token: token in combined_text for token in REQUIRED_GLOBAL_TOKENS}
    image_checks: list[dict[str, Any]] = []
    for asset in assets:
        path = Path(asset["staged_path"])
        with Image.open(path) as image:
            statistics = ImageStat.Stat(image.convert("L"))
            stddev = float(statistics.stddev[0])
            image_checks.append(
                {
                    "page": int(asset["page"]),
                    "path": str(path),
                    "width": image.width,
                    "height": image.height,
                    "stddev": round(stddev, 3),
                    "nonblank": stddev > 2.0,
                }
            )
    title_checks = {
        str(page.number): page.title in page_texts[page.number - 1]
        for page in pages
        if page.number <= len(page_texts)
    }
    status = (
        len(reader.pages) == len(pages)
        and len(assets) == len(pages)
        and all(item["a4_landscape"] for item in media_boxes)
        and all(all(value for key, value in item.items() if key != "page") for item in seal_checks)
        and all(required_tokens.values())
        and all(image["nonblank"] for image in image_checks)
        and all(title_checks.values())
    )
    result = {
        "status": "PASS" if status else "FAIL",
        "pdf_page_count": len(reader.pages),
        "expected_page_count": len(pages),
        "all_pages_a4_landscape": all(item["a4_landscape"] for item in media_boxes),
        "every_page_has_release_seal": all(item["release_seal"] and item["guidance_warning"] for item in seal_checks),
        "every_page_has_hydraulic_warning": all(item["hydraulic_warning"] for item in seal_checks),
        "required_tokens": required_tokens,
        "page_titles_searchable": title_checks,
        "all_page_assets_nonblank": all(image["nonblank"] for image in image_checks),
        "media_boxes": media_boxes,
        "seal_checks": seal_checks,
        "asset_checks": image_checks,
    }
    if not status:
        raise RuntimeError(
            "Preflight do relatório CF0 falhou: "
            f"pages={len(reader.pages)}/{len(pages)}, "
            f"a4={result['all_pages_a4_landscape']}, "
            f"seal={result['every_page_has_release_seal']}, "
            f"tokens={required_tokens}, assets={result['all_page_assets_nonblank']}."
        )
    return result


def publish_file(staged_path: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
    try:
        shutil.copyfile(staged_path, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_report_manifest(
    *,
    data: ReportData,
    schema_path: Path,
    pages: Sequence[PageDefinition],
    staged_pdf: Path,
    output_pdf: Path,
    staged_assets: Sequence[dict[str, Any]],
    output_assets_dir: Path,
    preflight: dict[str, Any],
) -> dict[str, Any]:
    assets = [
        {
            **file_record(
                Path(asset["staged_path"]),
                published_path=output_assets_dir / Path(asset["staged_path"]).name,
            ),
            "page": int(asset["page"]),
        }
        for asset in staged_assets
    ]
    sanitized_preflight = json.loads(json.dumps(preflight))
    for check, record in zip(sanitized_preflight["asset_checks"], assets):
        check["path"] = record["path"]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "manifest_type": "CF0_REVIEW_REPORT",
        "release": "CF0_GEOMETRIC_SCREENING_REVIEW",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "release_seal": SEAL,
        "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
        "guidance_authorized": False,
        "legacy_pdfs_read": False,
        "source_package": {
            "manifest": file_record(data.manifest_path),
            "schema": file_record(schema_path),
            "verification": data.verification,
            "source_release": data.manifest["release"],
            "source_stage_status": data.manifest["stage_status"],
        },
        "source_layers": list(EXPECTED_SOURCE_LAYERS),
        "phase_contract": phase_contract(data.manifest),
        "phase_offset_contract": phase_offset_contract(data.manifest),
        "boundary_contract": boundary_contract(data.manifest),
        "spline_representation_contract": spline_representation_contract(data.manifest),
        "blocker_contract": dict(EXPECTED_BLOCKER_CONTRACT),
        "candidate_phase_audit": candidate_phase_audit(data.manifest),
        "diagnostic_rows": {
            "layer_required": True,
            "status": "PRESENT_NOT_APPROVED",
            "review_status": "NOT_APPROVED",
            "guidance_status": "NOT_AUTHORIZED",
            "feature_count": int(len(data.diagnostic_rows)),
            "release_authorized": False,
        },
        "excluded_components": {
            "status": "AUDITED_FROM_CF0_MANIFEST",
            "component_count": len(data.manifest["domain_assembly"]["excluded_components"]),
            "total_area_m2": round(
                sum(
                    float(record["area_m2"])
                    for record in data.manifest["domain_assembly"]["excluded_components"]
                ),
                6,
            ),
            "filter": data.manifest["domain_assembly"]["work_block_filter"],
        },
        "roadmap": {
            "C1_CURVA_EMBUTIDA": "NOT_GENERATED",
            "C2_BASE_LARGA_PASSANTE": "NOT_GENERATED",
            "C3_ESD": "NOT_GENERATED",
        },
        "page_count": len(pages),
        "pages": [
            {
                "number": page.number,
                "title": page.title,
                "slug": page.slug,
                "kind": page.kind,
                "payload": page.payload,
            }
            for page in pages
        ],
        "output_integrity": {
            "pdf": file_record(staged_pdf, published_path=output_pdf),
            "assets": assets,
        },
        "preflight": sanitized_preflight,
        "generator": file_record(Path(__file__)),
    }


def generate_report(
    cf0_manifest_path: Path,
    cf0_schema_path: Path,
    output_pdf: Path,
    report_manifest_path: Path,
    assets_dir: Path,
) -> dict[str, Any]:
    data = load_report_data(cf0_manifest_path, cf0_schema_path)
    pages = build_page_definitions(data.manifest)
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".cf0_review_", dir=output_pdf.parent) as temporary_name:
        temporary_dir = Path(temporary_name)
        staged_pdf = temporary_dir / output_pdf.name
        staged_assets_dir = temporary_dir / "assets"
        book = ReportBook(staged_pdf, staged_assets_dir, pages)
        try:
            for page in pages:
                render_page(book, page, data)
        finally:
            book.close()
        preflight = preflight_report(staged_pdf, book.assets, pages)
        report_manifest = build_report_manifest(
            data=data,
            schema_path=cf0_schema_path,
            pages=pages,
            staged_pdf=staged_pdf,
            output_pdf=output_pdf,
            staged_assets=book.assets,
            output_assets_dir=assets_dir,
            preflight=preflight,
        )

        publish_file(staged_pdf, output_pdf)
        assets_dir.mkdir(parents=True, exist_ok=True)
        expected_names: set[str] = set()
        for asset in book.assets:
            staged_asset = Path(asset["staged_path"])
            expected_names.add(staged_asset.name)
            publish_file(staged_asset, assets_dir / staged_asset.name)
        for stale in assets_dir.glob("cf0_page_*.png"):
            if stale.name not in expected_names:
                stale.unlink()
        # The report manifest is the transaction marker and is published last.
        atomic_write_json(report_manifest_path, report_manifest)
    return report_manifest


def synthetic_report_data(directory: Path) -> ReportData:
    field_id = "1"
    work_block_id = "WB_1_C001"
    polygon = Polygon([(0, 0), (100, 0), (100, 100), (0, 100), (0, 0)])
    transform = (0.0, 5.0, 0.0, 100.0, 0.0, -5.0)
    y_grid, x_grid = np.mgrid[0:20, 0:20]
    elevation = 500.0 + x_grid * 0.4 + y_grid * 0.2
    mask = np.ones(elevation.shape, dtype=bool)
    terrain = RasterData(directory / "synthetic_dtm.tif", [elevation], [mask], (0, 100, 0, 100), transform)

    candidates: list[dict[str, Any]] = []
    row_records: list[dict[str, Any]] = []
    hydraulic_records: list[dict[str, Any]] = []
    rasters: dict[tuple[str, str, str, str], RasterData] = {}
    profiles = []
    weights = ((0.80, 0.20), (0.55, 0.45), (0.30, 0.70))
    for candidate_index, ((candidate_id, color), (contour, long_axis)) in enumerate(zip(PROFILE_COLORS.items(), weights)):
        del color
        profiles.append(
            {
                "candidate_id": candidate_id,
                "label": PROFILE_SHORT[candidate_id],
                "orientation_weights": {"contour": contour, "long_axis": long_axis, "boundary": 0.0},
            }
        )
        family_id = f"{candidate_id}:{work_block_id}"
        lines = [LineString([(2, 15 + row * 17 + candidate_index), (98, 15 + row * 17 + candidate_index)]) for row in range(5)]
        phase_offset_fraction = (0.0, 0.25, 0.5)[candidate_index]
        phase_offset_m = phase_offset_fraction * 1.5
        for row_index, geometry in enumerate(lines, start=1):
            phase_level_index = row_index - 3
            row_records.append(
                {
                    "candidate_id": candidate_id,
                    "field_id": field_id,
                    "work_block_id": work_block_id,
                    "family_id": family_id,
                    "row_index": row_index,
                    "phase_level_index": phase_level_index,
                    "phase_level_m": phase_level_index * 1.5 + phase_offset_m,
                    "phase_offset_m": phase_offset_m,
                    "phase_offset_fraction": phase_offset_fraction,
                    "geometry": geometry,
                }
            )
        phase_trials = [
            {
                "phase_offset_fraction": fraction,
                "phase_offset_m": fraction * 1.5,
                "geometric_status": "GEOMETRIC_PASS",
                "blocker_codes": [],
                "extracted_row_count": len(lines),
                "extracted_total_length_m": sum(line.length for line in lines),
                "minimum_radius_order_class": "FINITE",
                "minimum_radius_m": 250.0 - abs(fraction - phase_offset_fraction),
                "final_spacing_outside_tolerance_fraction": 0.02,
                "spacing_error_p95_m": 0.18 + abs(fraction - phase_offset_fraction),
                "coverage_proxy_ratio": 0.94,
                "internal_endpoint_count": 0,
                "intersection_count": 0,
                "self_intersection_count": 0,
                "loop_count": 0,
            }
            for fraction in (0.0, 0.25, 0.5, 0.75)
        ]
        phase_selection = {
            "revision": "CF0_PHASE_OFFSET_QUARTER_SPACING_V1",
            "selection_rule": (
                "PASS_ONLY_MAX_MIN_RADIUS_MIN_SPACING_OUTSIDE_MIN_SPACING_P95_"
                "MIN_COVERAGE_ERROR_MIN_OFFSET"
            ),
            "search_scope": "DISCRETE_CONFIGURED_OFFSETS_NOT_CONTINUOUS_GAUGE_INVARIANCE",
            "selection_status": "SELECTED_GEOMETRIC_PASS",
            "selection_key_order": [
                "MAXIMUM_MINIMUM_WORK_PATH_RADIUS_M",
                "MINIMUM_FINAL_SPACING_OUTSIDE_TOLERANCE_FRACTION",
                "MINIMUM_SPACING_ERROR_P95_M",
                "MINIMUM_ABSOLUTE_COVERAGE_PROXY_ERROR_FROM_1",
                "MINIMUM_PHASE_OFFSET_FRACTION",
            ],
            "coverage_proxy_target_ratio": 1.0,
            "phase_offset_role": "SELECTED_PASS",
            "selected_phase_offset_fraction": phase_offset_fraction,
            "selected_phase_offset_m": phase_offset_m,
            "diagnostic_phase_offset_fraction": None,
            "diagnostic_phase_offset_m": None,
            "diagnostic_selection_rule": "LOWEST_CONFIGURED_PHASE_OFFSET_V1",
            "attempted_offsets": phase_trials,
            "row_spacing_m": 1.5,
            "level_equation": "phase_level_m = phase_level_index * row_spacing_m + phase_offset_m",
            "phase_level_index_scope": "CANDIDATE_WORK_BLOCK_PHASE_LEVEL",
            "row_index_definition": "UNIQUE_NONNEGATIVE_OPERATIONAL_SEQUENCE_PER_CANDIDATE_WORK_BLOCK",
            "phase_gauge": {
                "method": "ZERO_AT_LEXICOGRAPHIC_FIRST_VALID_CELL_PER_COMPONENT",
                "anchor_phase_value_m": 0.0,
                "anchors": [
                    {"solver_row": 0, "solver_column": 0, "x_m": 2.5, "y_m": 97.5}
                ],
            },
        }
        contour_qa = {
            "method": "FIRST_ORDER_LOCAL_LSQ_GRADIENT_FAIL_CLOSED",
            "gradient_estimation_status": "PASS",
            "halo_cells": 1,
            "gradient_lsq_max_radius_cells": 2,
            "gradient_lsq_max_relative_residual": 0.30,
            "gradient_lsq_max_condition_number": 100.0,
            "gradient_lsq_minimum_neighbor_count": 3,
            "gradient_lsq_required_rank": 2,
            "gradient_component_connectivity": 4,
            "halo_cell_count": 20,
            "supported_halo_cell_count": 20,
            "unsupported_halo_cell_count": 0,
            "direct_gradient_source_count": 14,
            "lsq_gradient_source_count": 6,
            "unestimable_gradient_source_count": 0,
            "missing_gradient_component_count": 0,
            "gradient_lsq_max_radius_used_cells": 2,
            "gradient_lsq_residual_rms_max": 0.04,
            "gradient_lsq_relative_residual_max": 0.10,
            "gradient_lsq_condition_number_max": 12.0,
        }
        candidates.append(
            {
                "candidate_id": candidate_id,
                "field_id": field_id,
                "work_block_id": work_block_id,
                "family_id": family_id,
                "phase_offset_m": phase_offset_m,
                "phase_offset_fraction": phase_offset_fraction,
                "phase_offset_role": "SELECTED_PASS",
                "phase_offset_selection": phase_selection,
                "contour_extrapolation_qa": contour_qa,
                "geometric_status": "GEOMETRIC_PASS",
                "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
                "row_count": len(lines),
                "total_length_m": sum(line.length for line in lines),
                "diagnostic_row_count": 0,
                "diagnostic_total_length_m": 0.0,
                "metrics": {
                    "spacing_error_p95_m": 0.18 + candidate_index * 0.03,
                    "eikonal_residual_p95": 0.04 + candidate_index * 0.005,
                    "integrability_residual_p95": 0.03 + candidate_index * 0.005,
                    "orientation_misalignment_p95_deg": 5.0 + candidate_index,
                    "minimum_radius_m": 250.0,
                    "radius_status": "PASS",
                    "maximum_abs_grade_pct": 3.5 + candidate_index,
                    "internal_endpoint_count": 0,
                    "intersection_count": 0,
                    "self_intersection_count": 0,
                    "loop_count": 0,
                    "coverage_proxy_ratio": 0.94 + candidate_index * 0.02,
                    "solver_gate_metrics": {
                        "solve_mask_component_count": 1,
                    },
                },
                "blocker_codes": [],
            }
        )
        hydraulic_records.append(
            {
                "candidate_id": candidate_id,
                "field_id": field_id,
                "work_block_id": work_block_id,
                "family_id": family_id,
                "hydraulic_status": "HYDRAULIC_UNCONFIRMED",
                "idf_status": "MISSING",
                "soil_status": "MISSING",
                "contributing_area_status": "UNCONFIRMED",
                "outlet_status": "UNCONFIRMED",
                "receiver_status": "UNCONFIRMED",
                "section_status": "MISSING",
                "roughness_status": "MISSING",
                "downstream_status": "UNCONFIRMED",
                "grade_p95_pct": 2.4,
                "grade_max_pct": 3.5,
                "reversal_count": candidate_index,
                "missing_inputs": "RAIN_IDF_EVENT,SOIL_INFILTRATION,VERIFIED_RECEIVER_NETWORK",
                "blocker_codes": "CF0_NO_HYDRAULIC_SOLVER",
            }
        )
        phase = (x_grid * math.cos(candidate_index * 0.25) + y_grid * math.sin(candidate_index * 0.25)) * 1.5
        theta = np.full(elevation.shape, 12.0 + candidate_index * 28.0, dtype=float)
        coherence = np.clip(0.70 + 0.20 * np.sin((x_grid + y_grid) / 7.0), 0, 1)
        rasters[(candidate_id, field_id, work_block_id, "phase")] = RasterData(
            directory / f"{candidate_id}_phase.tif", [phase], [mask], (0, 100, 0, 100), transform
        )
        rasters[(candidate_id, field_id, work_block_id, "orientation_coherence")] = RasterData(
            directory / f"{candidate_id}_coherence.tif",
            [theta, coherence],
            [mask, mask],
            (0, 100, 0, 100),
            transform,
        )

    manifest = {
        "schema_version": "1.2.0",
        "release": "CF0_GEOMETRIC_SCREENING",
        "stage_status": "HYDRAULIC_UNCONFIRMED",
        "crs": {"code": 31982},
        "inputs": {},
        "constraints": {
            "power_inventory_status": "NOT_REVIEWED",
            "power_barrier_application": "NOT_APPLIED_NOT_REVIEWED",
            "operational_surfaces_status": "NOT_REVIEWED",
        },
        "release_limitations": [
            "CF0_NOT_FOR_GUIDANCE",
            "HYDRAULIC_UNCONFIRMED",
            "FIELD_VALIDATION_REQUIRED",
            "POWER_INVENTORY_NOT_REVIEWED",
        ],
        "domain_assembly": {
            "status": "CONNECTED_COMPONENTS_PER_FIELD",
            "geometry_method": "BUFFERED_FIELD_BOUNDARY_V1",
            "outer_headland_m": 12.0,
            "obstacle_clearance_m": 6.0,
            "work_block_filter": {
                "revision": "CF0_WORK_BLOCK_SUPPORT_V1",
                "grid_cell_estimate_method": "FLOOR_AREA_DIVIDED_BY_GRID_CELL_AREA",
                "minimum_grid_cell_count": 9,
                "effective_minimum_area_m2": 225.0,
                "width_method": "MINIMUM_ROTATED_RECTANGLE_SHORT_SIDE",
                "minimum_width_row_spacing_factor": 2.0,
                "effective_minimum_width_m": 5.0,
            },
            "work_blocks": [
                {
                    "work_block_id": work_block_id,
                    "field_ids": [field_id],
                    "component_index": 1,
                    "usable_area_m2": polygon.area,
                }
            ],
            "excluded_components": [
                {
                    "field_id": field_id,
                    "component_index": 2,
                    "area_m2": 75.0,
                    "minimum_rotated_width_m": 2.5,
                    "estimated_grid_cell_count": 3,
                    "reason_codes": [
                        "AREA_BELOW_CF0_WORK_BLOCK_MINIMUM",
                        "WIDTH_BELOW_CF0_WORK_BLOCK_MINIMUM",
                    ],
                }
            ],
        },
        "solver_parameters": {
            "solver_revision": "cf0-continuous-family-1.2.0",
            "grid_resolution_m": 5.0,
            "row_spacing_m": 1.5,
            "orientation": {
                "smoothing_radius_m": 15.0,
                "minimum_coherence": 0.45,
                "maximum_low_coherence_fraction": 0.10,
                "maximum_frustration_deg": 25.0,
                "maximum_abrupt_edge_fraction": 0.08,
                "maximum_cycle_conflict_fraction": 0.02,
            },
            "phase": {
                "gauge_method": "ZERO_AT_LEXICOGRAPHIC_FIRST_VALID_CELL_PER_COMPONENT",
                "gauge_anchor_value_m": 0.0,
                "eikonal_residual_tolerance": 0.20,
                "integrability_residual_tolerance": 0.20,
                "maximum_integrability_residual_rms": 0.18,
                "maximum_critical_fraction": 0.05,
                "maximum_cut_locus_fraction": 0.03,
                "solve_mask_required_component_count": 1,
                "solve_mask_component_connectivity": 4,
            },
            "extraction": {
                "phase_level_equation": "phase_level_m = phase_level_index * row_spacing_m + phase_offset_m",
                "phase_level_index_scope": "CANDIDATE_WORK_BLOCK_PHASE_LEVEL",
                "row_index_definition": "UNIQUE_NONNEGATIVE_OPERATIONAL_SEQUENCE_PER_CANDIDATE_WORK_BLOCK",
                "phase_offset_search_revision": "CF0_PHASE_OFFSET_QUARTER_SPACING_V1",
                "phase_offset_fractions": [0.0, 0.25, 0.5, 0.75],
                "phase_offset_selection_rule": (
                    "PASS_ONLY_MAX_MIN_RADIUS_MIN_SPACING_OUTSIDE_MIN_SPACING_P95_"
                    "MIN_COVERAGE_ERROR_MIN_OFFSET"
                ),
                "phase_offset_search_scope": "DISCRETE_CONFIGURED_OFFSETS_NOT_CONTINUOUS_GAUGE_INVARIANCE",
                "spacing_tolerance_m": 0.45,
                "maximum_spacing_outside_tolerance_fraction": 0.10,
                "coverage_proxy_minimum": 0.70,
                "coverage_proxy_maximum": 1.30,
                "curvature_sample_step_m": 2.0,
                "spline_method": "PARAMETRIC_BSPLINE_C2",
                "spline_max_deviation_m": 0.12,
                "spline_representation_method": "ADAPTIVE_CHORD_ERROR_PRESERVE_VERTICES",
                "spline_representation_tolerance_fraction": 0.10,
                "endpoint_snap_tolerance_m": 0.03,
                "solve_mask_rasterization": "ALL_TOUCHED_SUPERCOVER",
                "contour_extrapolation_method": "FIRST_ORDER_LOCAL_LSQ_GRADIENT_FAIL_CLOSED",
                "contour_extrapolation_halo_cells": 1,
                "contour_gradient_lsq_max_radius_cells": 2,
                "contour_gradient_lsq_max_relative_residual": 0.30,
                "contour_gradient_lsq_max_condition_number": 100.0,
                "contour_gradient_lsq_minimum_neighbor_count": 3,
                "contour_gradient_lsq_required_rank": 2,
                "contour_gradient_component_connectivity": 4,
                "endpoint_extension_mode": "TANGENT_ONLY_FAIL_CLOSED",
            },
            "candidate_profiles": profiles,
        },
        "candidates": candidates,
        "qa": {
            "candidate_field_count": 3,
            "geometric_pass_count": 3,
            "no_feasible_family_count": 0,
            "hydraulic_unconfirmed_count": 3,
            "radius_not_evaluated_count": 0,
            "invalid_geometry_count": 0,
            "non_3d_geometry_count": 0,
            "nonfinite_coordinate_count": 0,
            "internal_endpoint_count": 0,
            "intersection_count": 0,
            "raster_invalid_count": 0,
            "gate_failures": [],
        },
        "outputs": {
            "geopackage": {"path": "synthetic.gpkg", "size_bytes": 1, "sha256": "0" * 64}
        },
        "layer_counts": {
            "continuous_rows": len(row_records),
            "diagnostic_rows": 0,
            "family_summary": 3,
            "hydraulic_precheck": 3,
        },
    }
    manifest_path = directory / "synthetic_cf0_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    rows = gpd.GeoDataFrame(row_records, geometry="geometry", crs="EPSG:31982")
    diagnostics = gpd.GeoDataFrame(
        columns=[
            "candidate_id",
            "field_id",
            "work_block_id",
            "row_index",
            "phase_level_index",
            "phase_level_m",
            "phase_offset_m",
            "phase_offset_fraction",
            "diagnostic_status",
            "guidance_status",
            "geometry",
        ],
        geometry="geometry",
        crs="EPSG:31982",
    )
    fields = gpd.GeoDataFrame({"field_id": [field_id]}, geometry=[polygon], crs="EPSG:31982")
    return ReportData(
        manifest_path=manifest_path,
        manifest=manifest,
        verification={"status": "VERIFIED", "schema_validation": "SYNTHETIC"},
        rows=rows,
        diagnostic_rows=diagnostics,
        hydraulic=pd.DataFrame(hydraulic_records),
        fields=fields,
        field_id_column="field_id",
        work_blocks={work_block_id: polygon},
        terrain=terrain,
        rasters=rasters,
    )


def run_self_test() -> dict[str, Any]:
    fake_manifest = {
        "domain_assembly": {
            "excluded_components": [],
            "work_blocks": [
                {"work_block_id": "WB_1_C001", "field_ids": ["1"]},
                {"work_block_id": "WB_1_C002", "field_ids": ["1"]},
            ]
        },
        "candidates": [
            {"candidate_id": candidate_id, "work_block_id": block_id}
            for block_id in ("WB_1_C001", "WB_1_C002")
            for candidate_id in PROFILE_COLORS
        ],
    }
    pages = build_page_definitions(fake_manifest)
    if sum(page.kind == "block_map" for page in pages) != 2:
        raise RuntimeError("Synthetic page plan did not preserve work blocks.")
    if sum(page.kind == "block_rasters" for page in pages) != 2:
        raise RuntimeError("Synthetic page plan did not preserve raster reviews.")
    if sum(page.kind == "domain_exclusions" for page in pages) != 1:
        raise RuntimeError("Synthetic page plan did not preserve excluded-component audit.")
    if sum(page.kind == "extraction_contract" for page in pages) != 1:
        raise RuntimeError("Synthetic page plan did not preserve extraction-contract audit.")
    if sum(page.kind == "phase_contract" for page in pages) != 1:
        raise RuntimeError("Synthetic page plan did not preserve phase-contract audit.")
    if sum(page.kind == "phase_audit" for page in pages) != 1:
        raise RuntimeError("Synthetic page plan did not preserve candidate phase audit.")
    if format_number(None) != "—" or format_number(math.inf) != "∞":
        raise RuntimeError("Numeric formatting self-test failed.")
    if chunks(list(range(9)), 8) != [list(range(8)), [8]]:
        raise RuntimeError("Chunking self-test failed.")

    with tempfile.TemporaryDirectory(prefix="cf0_report_selftest_") as temporary_name:
        temporary = Path(temporary_name)
        synthetic_data = synthetic_report_data(temporary)
        synthetic_pages = build_page_definitions(synthetic_data.manifest)
        book = ReportBook(temporary / "synthetic.pdf", temporary / "assets", synthetic_pages)
        try:
            for synthetic_page in synthetic_pages:
                render_page(book, synthetic_page, synthetic_data)
        finally:
            book.close()
        checks = preflight_report(temporary / "synthetic.pdf", book.assets, synthetic_pages)
        synthetic_manifest = build_report_manifest(
            data=synthetic_data,
            schema_path=DEFAULT_CF0_SCHEMA,
            pages=synthetic_pages,
            staged_pdf=temporary / "synthetic.pdf",
            output_pdf=temporary / "published.pdf",
            staged_assets=book.assets,
            output_assets_dir=temporary / "published_assets",
            preflight=checks,
        )
        if synthetic_manifest["phase_contract"] != EXPECTED_PHASE_CONTRACT:
            raise RuntimeError("Synthetic report manifest lost the phase contract.")
        if synthetic_manifest["phase_offset_contract"] != EXPECTED_PHASE_OFFSET_CONTRACT:
            raise RuntimeError("Synthetic report manifest lost the phase-offset contract.")
        if synthetic_manifest["boundary_contract"] != EXPECTED_BOUNDARY_CONTRACT:
            raise RuntimeError("Synthetic report manifest lost the LSQ boundary contract.")
        if synthetic_manifest["candidate_phase_audit"] != candidate_phase_audit(
            synthetic_data.manifest
        ):
            raise RuntimeError("Synthetic report manifest lost candidate phase/halo traceability.")
    return {
        "status": "PASS",
        "checks": [
            "dynamic_work_block_pages",
            "excluded_component_audit_page",
            "extraction_contract_audit_page",
            "phase_contract_audit_page",
            "candidate_phase_audit_page",
            "candidate_and_hydraulic_chunking",
            "numeric_formatting",
            "all_report_page_renderers",
            "synthetic_a4_pdf",
            "every_page_release_seal",
            "searchable_required_tokens",
            "nonblank_page_assets",
            "cf0_12_manifest_contract_copy",
        ],
        "synthetic_preflight": checks["status"],
    }


def cli_path(value: Path) -> Path:
    return value.resolve() if value.is_absolute() else (ROOT / value).resolve()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cf0-manifest", type=Path, default=DEFAULT_CF0_MANIFEST, help="Manifesto do pacote CF0 verificado.")
    parser.add_argument("--cf0-schema", type=Path, default=DEFAULT_CF0_SCHEMA, help="Schema JSON do estágio CF0.")
    parser.add_argument("--output", type=Path, default=DEFAULT_PDF, help="PDF A4 paisagem de saída.")
    parser.add_argument("--report-manifest", type=Path, default=DEFAULT_REPORT_MANIFEST, help="Manifesto de integridade do relatório.")
    parser.add_argument("--assets-dir", type=Path, default=DEFAULT_ASSETS, help="Diretório das páginas PNG para revisão.")
    parser.add_argument("--preflight-only", action="store_true", help="Verifica um relatório já publicado sem regenerá-lo.")
    parser.add_argument("--self-test", action="store_true", help="Executa somente testes sintéticos leves.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.self_test:
        print(json.dumps(run_self_test(), ensure_ascii=False, indent=2))
        return 0
    report_manifest_path = cli_path(args.report_manifest)
    if args.preflight_only:
        try:
            from verify_cf0_review_report import verify_report
        except ModuleNotFoundError:
            from scripts.verify_cf0_review_report import verify_report
        result = verify_report(report_manifest_path, cli_path(args.cf0_schema))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    report = generate_report(
        cli_path(args.cf0_manifest),
        cli_path(args.cf0_schema),
        cli_path(args.output),
        report_manifest_path,
        cli_path(args.assets_dir),
    )
    pdf = report["output_integrity"]["pdf"]
    print(f"PASS: {report['page_count']} páginas A4 paisagem")
    print(f"PDF: {pdf['path']} ({pdf['size_bytes']} bytes, sha256={pdf['sha256']})")
    print(f"Manifesto: {relative_path(report_manifest_path)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"CF0_REVIEW_REPORT_FAILED: {error}")
        raise SystemExit(2)
