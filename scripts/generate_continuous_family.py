"""Generate CF0 continuous curved-row families from a validated project request.

CF0 is an independent geometric screening stage.  It projects an axial local
orientation preference onto one scalar phase per physical work domain, extracts
the phase isolines at the declared row spacing, and rejects a family whenever
phase, topology, spacing, or a *provided* static work-radius requirement fails.

The resulting lines are not guidance lines and do not represent an embedded
terrace, broad-base/passable terrace, ESD, or hydraulically sized structure.

Run with the QGIS Python environment::

    & 'C:\\Program Files\\QGIS 3.32.1\\bin\\python-qgis.bat' `
      '.\\scripts\\generate_continuous_family.py' `
      --request '.\\config\\exemplo_pedido_e0_dataset_atual.json'
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
import time
import warnings
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LightSource
from osgeo import gdal, ogr, osr
from scipy import ndimage
from scipy.interpolate import splprep, splev
from shapely.geometry import GeometryCollection, LineString, MultiPoint, Point, Polygon
from shapely.ops import unary_union

try:
    import generate_sulcation_scenarios as e0
    from continuous_family import (
        ContinuousFamilyPolicy,
        evaluate_family_geometry,
        extrapolate_phase_halo,
        phase_contours,
        solve_continuous_phase,
    )
    from operational_continuity import ContinuityPolicy, analyze_operational_continuity
    from project_request import ContractError, ResolvedProjectRequest, load_project_request
except ModuleNotFoundError:  # Allows ``python -m scripts...`` from the repo root.
    from scripts import generate_sulcation_scenarios as e0
    from scripts.continuous_family import (
        ContinuousFamilyPolicy,
        evaluate_family_geometry,
        extrapolate_phase_halo,
        phase_contours,
        solve_continuous_phase,
    )
    from scripts.operational_continuity import ContinuityPolicy, analyze_operational_continuity
    from scripts.project_request import ContractError, ResolvedProjectRequest, load_project_request


gdal.UseExceptions()
ogr.UseExceptions()

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "dataset" / "derived"
DEFAULT_REQUEST = REPO / "config" / "exemplo_pedido_e0_dataset_atual.json"
DEFAULT_GPKG = DERIVED / "continuous_family_candidates.gpkg"
DEFAULT_MANIFEST = DERIVED / "continuous_family_manifest.json"
DEFAULT_MAP = DERIVED / "continuous_family_map.png"
DEFAULT_RASTER_DIR = DERIVED / "continuous_family_rasters"

NODATA = -9999.0
HYDRAULIC_STATUS = "HYDRAULIC_UNCONFIRMED"
PHASE_OFFSET_PRUNED_BLOCKER = "PHASE_OFFSET_PRUNED_BY_INVARIANT_GATE"


@dataclass(frozen=True)
class Cf0Policy:
    revision: str = "cf0-continuous-family-1.2.0"
    work_block_filter_revision: str = "CF0_WORK_BLOCK_SUPPORT_V1"
    phase_offset_search_revision: str = "CF0_PHASE_OFFSET_QUARTER_SPACING_V1"
    phase_offset_selection_rule: str = (
        "PASS_ONLY_MAX_MIN_RADIUS_MIN_SPACING_OUTSIDE_MIN_SPACING_P95_"
        "MIN_COVERAGE_ERROR_MIN_OFFSET"
    )
    phase_offset_fractions: tuple[float, ...] = (0.0, 0.25, 0.50, 0.75)
    grid_resolution_m: float = 2.0
    smoothing_radius_m: float = 10.0
    minimum_coherence: float = 0.12
    maximum_low_coherence_fraction: float = 0.05
    maximum_frustration_deg: float = 35.0
    maximum_abrupt_edge_fraction: float = 0.01
    maximum_cycle_conflict_fraction: float = 0.0
    minimum_terrain_gradient: float = 1e-9
    gradient_floor: float = 0.30
    eikonal_residual_tolerance: float = 0.20
    integrability_residual_tolerance: float = 0.20
    maximum_iterations: int = 8
    convergence_tolerance: float = 1e-4
    regularization_weight: float = 0.15
    boundary_weight: float = 0.0
    phase_scale_min: float = 0.40
    phase_scale_max: float = 2.50
    phase_scale_relaxation: float = 0.65
    lsqr_tolerance: float = 1e-9
    lsqr_iteration_limit: int = 4000
    maximum_critical_fraction: float = 0.01
    cut_locus_laplacian_threshold: float = 1.25
    maximum_cut_locus_fraction: float = 0.02
    minimum_row_length_m: float = 8.0
    spacing_tolerance_fraction: float = 0.20
    maximum_spacing_outside_tolerance_fraction: float = 0.35
    coverage_proxy_minimum: float = 0.70
    coverage_proxy_maximum: float = 1.30
    curvature_sample_step_m: float = 2.0
    spline_max_deviation_m: float = 0.12
    spline_representation_tolerance_fraction: float = 0.10
    endpoint_snap_tolerance_m: float = 0.03
    endpoint_extension_factor: float = 2.5
    contour_extrapolation_halo_cells: int = 1
    contour_gradient_lsq_max_radius_cells: int = 2
    contour_gradient_lsq_max_relative_residual: float = 0.30
    contour_gradient_lsq_max_condition_number: float = 100.0
    solve_mask_required_component_count: int = 1
    solve_mask_component_connectivity: int = 4
    minimum_work_block_grid_cells: int = 9
    minimum_work_block_width_row_spacing_factor: float = 2.0

    def solver_dict(self) -> dict[str, Any]:
        return {
            "orientation_smoothing_sigma_cells": self.smoothing_radius_m
            / self.grid_resolution_m,
            "minimum_coherence": self.minimum_coherence,
            "maximum_low_coherence_fraction": self.maximum_low_coherence_fraction,
            "maximum_frustration_deg": self.maximum_frustration_deg,
            "maximum_abrupt_edge_fraction": self.maximum_abrupt_edge_fraction,
            "maximum_cycle_conflict_fraction": self.maximum_cycle_conflict_fraction,
            "minimum_terrain_gradient": self.minimum_terrain_gradient,
            "gradient_floor": self.gradient_floor,
            "eikonal_residual_tolerance": self.eikonal_residual_tolerance,
            "integrability_residual_tolerance": self.integrability_residual_tolerance,
            "maximum_iterations": self.maximum_iterations,
            "convergence_tolerance": self.convergence_tolerance,
            "regularization_weight": self.regularization_weight,
            "boundary_weight": self.boundary_weight,
            "phase_scale_min": self.phase_scale_min,
            "phase_scale_max": self.phase_scale_max,
            "phase_scale_relaxation": self.phase_scale_relaxation,
            "lsqr_tolerance": self.lsqr_tolerance,
            "lsqr_iteration_limit": self.lsqr_iteration_limit,
            "maximum_critical_fraction": self.maximum_critical_fraction,
            "cut_locus_laplacian_threshold": self.cut_locus_laplacian_threshold,
            "maximum_cut_locus_fraction": self.maximum_cut_locus_fraction,
            "maximum_spacing_outside_tolerance_fraction": self.maximum_spacing_outside_tolerance_fraction,
            "coverage_proxy_minimum": self.coverage_proxy_minimum,
            "coverage_proxy_maximum": self.coverage_proxy_maximum,
        }


CANDIDATE_PROFILES: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "CF0A_CONSERVACAO",
        "label": "Preferencia topografica conservacionista",
        "orientation_weights": {"contour": 0.80, "long_axis": 0.20, "boundary": 0.0},
    },
    {
        "candidate_id": "CF0B_EQUILIBRIO",
        "label": "Compromisso entre topografia e eixo longo",
        "orientation_weights": {"contour": 0.55, "long_axis": 0.45, "boundary": 0.0},
    },
    {
        "candidate_id": "CF0C_OPERACAO",
        "label": "Preferencia operacional por tiros longos",
        "orientation_weights": {"contour": 0.30, "long_axis": 0.70, "boundary": 0.0},
    },
)


def rounded(value: Any, digits: int = 6) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, digits) if math.isfinite(number) else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO).as_posix()
    except ValueError:
        return str(resolved)


def file_record(path: Path, *, published_path: Path | None = None, **extra: Any) -> dict[str, Any]:
    record = {
        "path": display_path(published_path or path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    record.update(extra)
    return record


def input_dataset_record(resolution: dict[str, Any], **extra: Any) -> dict[str, Any]:
    record = {
        "path": str(resolution["resolved_path"]).replace("\\", "/"),
        "integrity_scope": "SOURCE_BUNDLE",
        "source_bundle_sha256": resolution["source_bundle_sha256"],
        "source_files": [
            {
                "path": str(item["path"]).replace("\\", "/"),
                "size_bytes": int(item["size_bytes"]),
                "sha256": item["sha256"],
            }
            for item in resolution["source_files"]
        ],
    }
    record.update(extra)
    return record


def percentile(values: Iterable[float], level: float) -> float | None:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    return float(np.percentile(array, level)) if array.size else None


def longest_axis_angle_deg(geometry) -> float:
    rectangle = geometry.minimum_rotated_rectangle
    coordinates = np.asarray(rectangle.exterior.coords, dtype=float)[:, :2]
    edges = coordinates[1:] - coordinates[:-1]
    lengths = np.linalg.norm(edges, axis=1)
    if not lengths.size or float(lengths.max()) <= 1e-9:
        raise RuntimeError("Cannot derive a long axis from an empty or degenerate work geometry.")
    edge = edges[int(np.argmax(lengths))]
    return float(math.degrees(math.atan2(edge[1], edge[0])) % 180.0)


def grid_for_geometry(
    geometry,
    resolution_m: float,
    *,
    padding_cells: int = 0,
) -> tuple[tuple[float, ...], int, int]:
    if isinstance(padding_cells, bool) or not isinstance(padding_cells, int) or padding_cells < 0:
        raise ValueError("padding_cells must be a non-negative integer")
    min_x, min_y, max_x, max_y = geometry.bounds
    origin_x = math.floor(min_x / resolution_m) * resolution_m - padding_cells * resolution_m
    origin_y = math.ceil(max_y / resolution_m) * resolution_m + padding_cells * resolution_m
    width = max(2, int(math.ceil((max_x - origin_x) / resolution_m)))
    height = max(2, int(math.ceil((origin_y - min_y) / resolution_m)))
    width += padding_cells
    height += padding_cells
    return (origin_x, resolution_m, 0.0, origin_y, 0.0, -resolution_m), width, height


def rasterize_mask(
    geometry,
    transform: tuple[float, ...],
    width: int,
    height: int,
    projection: str,
    *,
    all_touched: bool = False,
) -> np.ndarray:
    raster = gdal.GetDriverByName("MEM").Create("", width, height, 1, gdal.GDT_Byte)
    raster.SetGeoTransform(transform)
    raster.SetProjection(projection)
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromWkt(projection)
    datasource = ogr.GetDriverByName("Memory").CreateDataSource("")
    layer = datasource.CreateLayer("work", srs=spatial_ref, geom_type=ogr.wkbUnknown)
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(ogr.CreateGeometryFromWkb(geometry.wkb))
    layer.CreateFeature(feature)
    options = ["ALL_TOUCHED=TRUE"] if all_touched else []
    gdal.RasterizeLayer(raster, [1], layer, burn_values=[1], options=options)
    mask = raster.GetRasterBand(1).ReadAsArray().astype(bool)
    feature = layer = datasource = raster = None
    return mask


def minimum_rotated_width_m(geometry) -> float:
    rectangle = geometry.minimum_rotated_rectangle
    if rectangle.is_empty or not isinstance(rectangle, Polygon):
        return 0.0
    coordinates = list(rectangle.exterior.coords)
    side_lengths = [
        math.dist(coordinates[index], coordinates[index + 1])
        for index in range(len(coordinates) - 1)
    ]
    positive = [value for value in side_lengths if value > 1e-9]
    return min(positive) if positive else 0.0


def work_block_filter_contract(
    policy: Cf0Policy, row_spacing_m: float
) -> dict[str, Any]:
    effective_minimum_width_m = max(
        policy.grid_resolution_m,
        policy.minimum_work_block_width_row_spacing_factor * row_spacing_m,
    )
    return {
        "revision": policy.work_block_filter_revision,
        "grid_cell_estimate_method": "FLOOR_AREA_DIVIDED_BY_GRID_CELL_AREA",
        "minimum_grid_cell_count": policy.minimum_work_block_grid_cells,
        "effective_minimum_area_m2": policy.minimum_work_block_grid_cells
        * policy.grid_resolution_m**2,
        "width_method": "MINIMUM_ROTATED_RECTANGLE_SHORT_SIDE",
        "minimum_width_row_spacing_factor": policy.minimum_work_block_width_row_spacing_factor,
        "effective_minimum_width_m": effective_minimum_width_m,
    }


def assemble_connected_work_blocks(
    fields: list[dict[str, Any]],
    *,
    policy: Cf0Policy,
    row_spacing_m: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    work_blocks: list[dict[str, Any]] = []
    excluded_components: list[dict[str, Any]] = []
    filter_contract = work_block_filter_contract(policy, row_spacing_m)
    minimum_area_m2 = float(filter_contract["effective_minimum_area_m2"])
    minimum_width_m = float(filter_contract["effective_minimum_width_m"])
    for field in fields:
        components = sorted(
            e0.iter_polygons(field["usable"]),
            key=lambda polygon: (*polygon.bounds, polygon.area),
        )
        for component_index, component in enumerate(components, start=1):
            width_m = minimum_rotated_width_m(component)
            estimated_grid_cell_count = int(
                math.floor(component.area / policy.grid_resolution_m**2 + 1e-12)
            )
            reason_codes: list[str] = []
            if component.area + 1e-9 < minimum_area_m2:
                reason_codes.append("AREA_BELOW_CF0_WORK_BLOCK_MINIMUM")
            if width_m + 1e-9 < minimum_width_m:
                reason_codes.append("WIDTH_BELOW_CF0_WORK_BLOCK_MINIMUM")
            if reason_codes:
                excluded_components.append(
                    {
                        "field_id": str(field["code"]),
                        "component_index": component_index,
                        "area_m2": rounded(component.area, 6),
                        "minimum_rotated_width_m": rounded(width_m, 6),
                        "estimated_grid_cell_count": estimated_grid_cell_count,
                        "reason_codes": reason_codes,
                    }
                )
                continue
            work_blocks.append(
                {
                    **field,
                    "usable": component,
                    "usable_area_ha": component.area / 10_000.0,
                    "component_index": component_index,
                    "work_block_id": f"WB_{field['code']}_C{component_index:03d}",
                }
            )
    if not work_blocks:
        raise RuntimeError(
            "No connected usable work block satisfies CF0_WORK_BLOCK_SUPPORT_V1."
        )
    return work_blocks, excluded_components, filter_contract


def sample_terrain_grid(
    terrain: e0.Terrain,
    mask: np.ndarray,
    transform: tuple[float, ...],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rows, columns = np.where(mask)
    if not rows.size:
        raise RuntimeError("Usable work geometry contains no CF0 solver cells.")
    xs = transform[0] + (columns + 0.5) * transform[1]
    ys = transform[3] + (rows + 0.5) * transform[5]
    elevation = np.full(mask.shape, np.nan, dtype=float)
    gx = np.full(mask.shape, np.nan, dtype=float)
    gy = np.full(mask.shape, np.nan, dtype=float)
    elevation[rows, columns] = terrain.sample(terrain.elevation, xs, ys)
    gx[rows, columns] = terrain.sample(terrain.gx, xs, ys)
    gy[rows, columns] = terrain.sample(terrain.gy, xs, ys)
    return elevation, gx, gy


def north_up_to_solver_grid(*arrays: np.ndarray) -> tuple[np.ndarray, ...]:
    """Convert north-up raster arrays (row+ south) to solver arrays (row+ y+)."""

    return tuple(np.flipud(np.asarray(array)) for array in arrays)


def solver_to_north_up_grid(*arrays: np.ndarray) -> tuple[np.ndarray, ...]:
    """Convert solver y-increasing arrays back to north-up raster row order."""

    return tuple(np.flipud(np.asarray(array)) for array in arrays)


def finite_solver_gradients(
    mask: np.ndarray,
    gradient_x: np.ndarray,
    gradient_y: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Keep mask-exterior sentinels finite without hiding invalid work cells."""

    valid = np.asarray(mask, dtype=bool)
    gx = np.asarray(gradient_x, dtype=float)
    gy = np.asarray(gradient_y, dtype=float)
    if gx.shape != valid.shape or gy.shape != valid.shape:
        raise ValueError("Solver gradient arrays must match the work mask.")
    if np.any(~np.isfinite(gx[valid])) or np.any(~np.isfinite(gy[valid])):
        raise ValueError("Terrain gradients must be finite inside the work mask.")
    return np.where(valid, gx, 0.0), np.where(valid, gy, 0.0)


def solve_mask_connectivity_qa(
    mask: np.ndarray,
    *,
    required_component_count: int = 1,
    connectivity: int = 4,
) -> dict[str, Any]:
    """Fail closed when one vector work block becomes multiple raster islands."""

    valid = np.asarray(mask, dtype=bool)
    if valid.ndim != 2 or not np.any(valid):
        raise ValueError("solve mask must be a non-empty two-dimensional array")
    if required_component_count != 1:
        raise ValueError("CF0 requires exactly one solve-mask component")
    if connectivity != 4:
        raise ValueError("CF0 solve-mask connectivity must be four-neighbour")
    component_count = int(
        ndimage.label(
            valid,
            structure=np.asarray(
                [[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.uint8
            ),
        )[1]
    )
    passed = component_count == required_component_count
    return {
        "solve_mask_component_count": component_count,
        "solve_mask_required_component_count": required_component_count,
        "solve_mask_component_connectivity": connectivity,
        "status": "PASS" if passed else "FAIL_CLOSED",
        "blocker_codes": [] if passed else ["WORK_BLOCK_SOLVE_MASK_DISCONNECTED"],
    }


def _qa_value(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value: Any = payload
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value[part]
        result = rounded(value, 10)
        if result is not None:
            return result
    return None


def _array_from_result(result: dict[str, Any], *keys: str) -> np.ndarray:
    for key in keys:
        value = result.get(key)
        if value is not None:
            return np.asarray(value, dtype=float)
    raise RuntimeError(f"Continuous-phase solver did not return any of: {', '.join(keys)}")


def write_cf0_rasters(
    phase_path: Path,
    orientation_path: Path,
    *,
    phase: np.ndarray,
    normal_x: np.ndarray,
    normal_y: np.ndarray,
    coherence: np.ndarray,
    mask: np.ndarray,
    transform: tuple[float, ...],
    projection: str,
) -> None:
    phase_path.parent.mkdir(parents=True, exist_ok=True)
    driver = gdal.GetDriverByName("GTiff")
    creation_options = ["TILED=YES", "COMPRESS=DEFLATE", "PREDICTOR=3"]

    phase_dataset = driver.Create(
        str(phase_path), phase.shape[1], phase.shape[0], 1, gdal.GDT_Float32, creation_options
    )
    phase_dataset.SetGeoTransform(transform)
    phase_dataset.SetProjection(projection)
    phase_band = phase_dataset.GetRasterBand(1)
    phase_band.SetDescription("continuous_phase_m")
    phase_band.SetNoDataValue(NODATA)
    phase_band.WriteArray(np.where(mask & np.isfinite(phase), phase, NODATA).astype("float32"))
    phase_band.FlushCache()
    phase_dataset.FlushCache()
    phase_dataset = None

    # nx,ny are phase normals.  The published axial angle is the row tangent,
    # modulo 180 degrees, so reversing a row never changes the raster.
    axial_degrees = np.degrees(np.arctan2(normal_x, -normal_y)) % 180.0
    orientation_dataset = driver.Create(
        str(orientation_path),
        phase.shape[1],
        phase.shape[0],
        2,
        gdal.GDT_Float32,
        creation_options,
    )
    orientation_dataset.SetGeoTransform(transform)
    orientation_dataset.SetProjection(projection)
    for index, (name, array) in enumerate(
        (("row_axial_orientation_deg", axial_degrees), ("orientation_coherence", coherence)),
        start=1,
    ):
        band = orientation_dataset.GetRasterBand(index)
        band.SetDescription(name)
        band.SetNoDataValue(NODATA)
        band.WriteArray(np.where(mask & np.isfinite(array), array, NODATA).astype("float32"))
        band.FlushCache()
    orientation_dataset.FlushCache()
    orientation_dataset = None


def raster_record(
    staged_path: Path,
    published_path: Path,
    *,
    candidate_id: str,
    field_id: str,
    work_block_id: str,
    raster_role: str,
) -> dict[str, Any]:
    dataset = gdal.Open(str(staged_path))
    if dataset is None:
        raise RuntimeError(f"Could not reopen generated CF0 raster: {staged_path}")
    transform = [float(value) for value in dataset.GetGeoTransform()]
    nodata = dataset.GetRasterBand(1).GetNoDataValue()
    record = file_record(
        staged_path,
        published_path=published_path,
        candidate_id=candidate_id,
        field_id=field_id,
        work_block_id=work_block_id,
        raster_role=raster_role,
        width=dataset.RasterXSize,
        height=dataset.RasterYSize,
        band_count=dataset.RasterCount,
        geotransform=transform,
        nodata=nodata,
    )
    dataset = None
    return record


OGR_FIELDS: dict[str, tuple[tuple[str, int], ...]] = {
    "continuous_rows": (
        ("row_id", ogr.OFTString),
        ("family_id", ogr.OFTString),
        ("candidate_id", ogr.OFTString),
        ("field_id", ogr.OFTString),
        ("work_block_id", ogr.OFTString),
        ("row_index", ogr.OFTInteger),
        ("phase_level_index", ogr.OFTInteger),
        ("phase_level_m", ogr.OFTReal),
        ("phase_offset_m", ogr.OFTReal),
        ("phase_offset_fraction", ogr.OFTReal),
        ("length_m", ogr.OFTReal),
        ("min_radius_m", ogr.OFTReal),
        ("max_abs_grade_pct", ogr.OFTReal),
        ("grade_p95_pct", ogr.OFTReal),
        ("reversal_count", ogr.OFTInteger),
        ("start_surface", ogr.OFTString),
        ("end_surface", ogr.OFTString),
        ("geometry_status", ogr.OFTString),
        ("hydraulic_status", ogr.OFTString),
        ("topology_status", ogr.OFTString),
        ("blocker_codes", ogr.OFTString),
    ),
    "diagnostic_rows": (
        ("row_id", ogr.OFTString),
        ("family_id", ogr.OFTString),
        ("candidate_id", ogr.OFTString),
        ("field_id", ogr.OFTString),
        ("work_block_id", ogr.OFTString),
        ("row_index", ogr.OFTInteger),
        ("phase_level_index", ogr.OFTInteger),
        ("phase_level_m", ogr.OFTReal),
        ("phase_offset_m", ogr.OFTReal),
        ("phase_offset_fraction", ogr.OFTReal),
        ("length_m", ogr.OFTReal),
        ("min_radius_m", ogr.OFTReal),
        ("max_abs_grade_pct", ogr.OFTReal),
        ("grade_p95_pct", ogr.OFTReal),
        ("reversal_count", ogr.OFTInteger),
        ("start_surface", ogr.OFTString),
        ("end_surface", ogr.OFTString),
        ("geometry_status", ogr.OFTString),
        ("hydraulic_status", ogr.OFTString),
        ("topology_status", ogr.OFTString),
        ("diagnostic_status", ogr.OFTString),
        ("guidance_status", ogr.OFTString),
        ("blocker_codes", ogr.OFTString),
    ),
    "family_summary": (
        ("candidate_id", ogr.OFTString),
        ("field_id", ogr.OFTString),
        ("work_block_id", ogr.OFTString),
        ("family_id", ogr.OFTString),
        ("phase_offset_m", ogr.OFTReal),
        ("phase_offset_fraction", ogr.OFTReal),
        ("phase_offset_role", ogr.OFTString),
        ("phase_offset_selection_status", ogr.OFTString),
        ("geometry_status", ogr.OFTString),
        ("hydraulic_status", ogr.OFTString),
        ("row_count", ogr.OFTInteger),
        ("total_length_m", ogr.OFTReal),
        ("diagnostic_row_count", ogr.OFTInteger),
        ("diagnostic_total_length_m", ogr.OFTReal),
        ("spacing_p95_m", ogr.OFTReal),
        ("eikonal_p95", ogr.OFTReal),
        ("integrability_p95", ogr.OFTReal),
        ("orientation_p95_deg", ogr.OFTReal),
        ("min_radius_m", ogr.OFTReal),
        ("radius_status", ogr.OFTString),
        ("max_grade_pct", ogr.OFTReal),
        ("internal_endpoints", ogr.OFTInteger),
        ("intersections", ogr.OFTInteger),
        ("self_intersections", ogr.OFTInteger),
        ("loops", ogr.OFTInteger),
        ("blocker_codes", ogr.OFTString),
    ),
    "hydraulic_precheck": (
        ("candidate_id", ogr.OFTString),
        ("field_id", ogr.OFTString),
        ("work_block_id", ogr.OFTString),
        ("family_id", ogr.OFTString),
        ("hydraulic_status", ogr.OFTString),
        ("idf_status", ogr.OFTString),
        ("soil_status", ogr.OFTString),
        ("contributing_area_status", ogr.OFTString),
        ("outlet_status", ogr.OFTString),
        ("receiver_status", ogr.OFTString),
        ("section_status", ogr.OFTString),
        ("roughness_status", ogr.OFTString),
        ("downstream_status", ogr.OFTString),
        ("grade_p95_pct", ogr.OFTReal),
        ("grade_max_pct", ogr.OFTReal),
        ("reversal_count", ogr.OFTInteger),
        ("adverse_length_pct", ogr.OFTReal),
        ("missing_inputs", ogr.OFTString),
        ("blocker_codes", ogr.OFTString),
    ),
}


def _create_fields(layer: ogr.Layer, definitions: tuple[tuple[str, int], ...]) -> None:
    for name, field_type in definitions:
        definition = ogr.FieldDefn(name, field_type)
        if field_type == ogr.OFTString:
            definition.SetWidth(2048 if name in {"blocker_codes", "missing_inputs"} else 254)
        elif field_type == ogr.OFTReal:
            definition.SetWidth(24)
            definition.SetPrecision(8)
        if layer.CreateField(definition) != ogr.OGRERR_NONE:
            raise RuntimeError(f"Could not create {layer.GetName()}.{name}.")


def _set_feature_fields(feature: ogr.Feature, payload: dict[str, Any], fields) -> None:
    for name, _ in fields:
        value = payload.get(name)
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            value = ",".join(str(item) for item in value)
        feature.SetField(name, value)


def write_geopackage(
    path: Path,
    projection: str,
    rows: list[dict[str, Any]],
    diagnostic_rows: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    hydraulic: list[dict[str, Any]],
) -> None:
    driver = ogr.GetDriverByName("GPKG")
    if path.exists():
        driver.DeleteDataSource(str(path))
    datasource = driver.CreateDataSource(str(path))
    if datasource is None:
        raise RuntimeError(f"Could not create CF0 GeoPackage: {path}")
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromWkt(projection)
    spatial_ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

    row_layer = datasource.CreateLayer(
        "continuous_rows", srs=spatial_ref, geom_type=ogr.wkbLineString25D
    )
    diagnostic_layer = datasource.CreateLayer(
        "diagnostic_rows", srs=spatial_ref, geom_type=ogr.wkbLineString25D
    )
    summary_layer = datasource.CreateLayer("family_summary", geom_type=ogr.wkbNone)
    hydraulic_layer = datasource.CreateLayer("hydraulic_precheck", geom_type=ogr.wkbNone)
    if any(
        layer is None
        for layer in (row_layer, diagnostic_layer, summary_layer, hydraulic_layer)
    ):
        raise RuntimeError("Could not create all required CF0 GeoPackage layers.")
    _create_fields(row_layer, OGR_FIELDS["continuous_rows"])
    _create_fields(diagnostic_layer, OGR_FIELDS["diagnostic_rows"])
    _create_fields(summary_layer, OGR_FIELDS["family_summary"])
    _create_fields(hydraulic_layer, OGR_FIELDS["hydraulic_precheck"])

    for layer, records, layer_name in (
        (row_layer, rows, "continuous_rows"),
        (diagnostic_layer, diagnostic_rows, "diagnostic_rows"),
    ):
        for payload in records:
            feature = ogr.Feature(layer.GetLayerDefn())
            _set_feature_fields(feature, payload, OGR_FIELDS[layer_name])
            feature.SetGeometry(ogr.CreateGeometryFromWkb(payload["geometry"].wkb))
            if layer.CreateFeature(feature) != ogr.OGRERR_NONE:
                raise RuntimeError(
                    f"Could not write {layer_name} row {payload['row_id']}."
                )
            feature = None
    for layer, records, layer_name in (
        (summary_layer, summaries, "family_summary"),
        (hydraulic_layer, hydraulic, "hydraulic_precheck"),
    ):
        for payload in records:
            feature = ogr.Feature(layer.GetLayerDefn())
            _set_feature_fields(feature, payload, OGR_FIELDS[layer_name])
            if layer.CreateFeature(feature) != ogr.OGRERR_NONE:
                raise RuntimeError(f"Could not write {layer_name} record.")
            feature = None
    datasource.FlushCache()
    datasource = None


def line_with_z(line: LineString, terrain: e0.Terrain, interval_m: float) -> LineString:
    del interval_m  # XY sampling is fixed by the already validated C2 spline.
    xy = np.asarray([coordinate[:2] for coordinate in line.coords], dtype=float)
    zs = terrain.sample(terrain.elevation, xy[:, 0], xy[:, 1])
    if not np.isfinite(zs).all():
        raise RuntimeError("CF0 row crosses a non-finite terrain elevation.")
    return LineString([(float(x), float(y), float(z)) for (x, y), z in zip(xy, zs)])


def _plot_boundary(axis, geometry, **style: Any) -> None:
    for polygon in e0.iter_polygons(geometry):
        x, y = polygon.exterior.xy
        axis.plot(x, y, **style)
        for ring in polygon.interiors:
            x, y = ring.xy
            axis.plot(x, y, **style)


def render_map(
    path: Path,
    fields: list[dict[str, Any]],
    terrain: e0.Terrain,
    rows: list[dict[str, Any]],
    diagnostic_rows: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
) -> None:
    field_ids = [str(field["code"]) for field in fields]
    figure, axes = plt.subplots(
        len(CANDIDATE_PROFILES),
        len(field_ids),
        figsize=(7.2 * len(field_ids), 5.4 * len(CANDIDATE_PROFILES)),
        squeeze=False,
    )
    row_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        row_groups.setdefault((row["candidate_id"], row["field_id"]), []).append(row)
    diagnostic_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in diagnostic_rows:
        diagnostic_groups.setdefault(
            (row["candidate_id"], row["field_id"]), []
        ).append(row)
    summaries_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in summaries:
        summaries_by_key.setdefault(
            (record["candidate_id"], record["field_id"]), []
        ).append(record)
    colors = {
        "CF0A_CONSERVACAO": "#1b7f5a",
        "CF0B_EQUILIBRIO": "#df8f16",
        "CF0C_OPERACAO": "#2675b8",
    }

    for row_index, profile in enumerate(CANDIDATE_PROFILES):
        candidate_id = profile["candidate_id"]
        for column_index, field in enumerate(fields):
            axis = axes[row_index, column_index]
            field_id = str(field["code"])
            mask = terrain.raster_mask(field["geometry"])
            row_slice, column_slice = e0.field_window(mask, padding=4)
            elevation = terrain.elevation[row_slice, column_slice]
            valid = terrain.valid[row_slice, column_slice]
            extent = (
                terrain.transform[0] + column_slice.start * terrain.transform[1],
                terrain.transform[0] + column_slice.stop * terrain.transform[1],
                terrain.transform[3] + row_slice.stop * terrain.transform[5],
                terrain.transform[3] + row_slice.start * terrain.transform[5],
            )
            fill = np.where(valid, elevation, np.nan)
            if np.isfinite(fill).any():
                light = LightSource(azdeg=315, altdeg=38)
                shade_source = np.where(valid, elevation, float(np.nanmedian(elevation[valid])))
                shaded = light.shade(shade_source, cmap=plt.get_cmap("Greys"), blend_mode="soft")
                shaded[~valid, 3] = 0
                axis.imshow(shaded, extent=extent, origin="upper", alpha=0.78)
            _plot_boundary(axis, field["geometry"], color="#353535", linewidth=1.0)
            _plot_boundary(axis, field["usable"], color="#707070", linewidth=0.7, linestyle="--")
            for record in diagnostic_groups.get((candidate_id, field_id), []):
                coordinates = np.asarray(record["geometry"].coords)
                axis.plot(
                    coordinates[:, 0],
                    coordinates[:, 1],
                    color="#b64d3b",
                    linewidth=0.48,
                    linestyle=(0, (3, 2)),
                    alpha=0.58,
                )
            for record in row_groups.get((candidate_id, field_id), []):
                coordinates = np.asarray(record["geometry"].coords)
                axis.plot(
                    coordinates[:, 0],
                    coordinates[:, 1],
                    color=colors[candidate_id],
                    linewidth=0.55,
                    alpha=0.92,
                )
            panel_summaries = summaries_by_key.get((candidate_id, field_id), [])
            pass_blocks = sum(
                summary["geometry_status"] == "GEOMETRIC_PASS"
                for summary in panel_summaries
            )
            row_count = sum(summary["row_count"] for summary in panel_summaries)
            total_length_m = sum(summary["total_length_m"] for summary in panel_summaries)
            diagnostic_count = sum(
                summary["diagnostic_row_count"] for summary in panel_summaries
            )
            subtitle = (
                f"{field_id} | {pass_blocks}/{len(panel_summaries)} blocos PASS\n"
                f"{row_count} aprovadas | {diagnostic_count} diagnosticas NAO APROVADAS | "
                f"{total_length_m / 1000.0:.2f} km aprovados"
            )
            axis.set_title(f"{candidate_id}\n{subtitle}", fontsize=9)
            axis.set_aspect("equal")
            axis.set_axis_off()
    figure.suptitle(
        "CF0 - familias curvas continuas (triagem geometrica; hidraulica nao confirmada)",
        fontsize=14,
        fontweight="bold",
    )
    figure.tight_layout(rect=(0, 0, 1, 0.975))
    figure.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(figure)


def make_solver_policy(
    policy: Cf0Policy,
    row_spacing_m: float,
    contour_preference: float,
    minimum_radius_m: float | None,
) -> ContinuousFamilyPolicy:
    keyword_arguments = {
        "grid_resolution_m": policy.grid_resolution_m,
        "row_spacing_m": row_spacing_m,
        "orientation_smoothing_m": policy.smoothing_radius_m,
        "contour_preference": contour_preference,
        "minimum_orientation_coherence": policy.minimum_coherence,
        "maximum_low_coherence_fraction": policy.maximum_low_coherence_fraction,
        "maximum_axial_jump_deg": policy.maximum_frustration_deg,
        "maximum_abrupt_edge_fraction": policy.maximum_abrupt_edge_fraction,
        "maximum_cycle_conflict_fraction": policy.maximum_cycle_conflict_fraction,
        "integrability_iterations": policy.maximum_iterations,
        "phase_scale_min": policy.phase_scale_min,
        "phase_scale_max": policy.phase_scale_max,
        "phase_scale_regularization": max(policy.regularization_weight, 1e-6),
        "phase_scale_relaxation": policy.phase_scale_relaxation,
        "lsqr_tolerance": policy.lsqr_tolerance,
        "lsqr_iteration_limit": policy.lsqr_iteration_limit,
        "maximum_integrability_residual_rms": policy.integrability_residual_tolerance,
        "maximum_integrability_angular_error_p95_deg": policy.maximum_frustration_deg,
        "minimum_phase_gradient": policy.gradient_floor,
        "maximum_critical_fraction": policy.maximum_critical_fraction,
        "cut_locus_laplacian_threshold": policy.cut_locus_laplacian_threshold,
        "maximum_cut_locus_fraction": policy.maximum_cut_locus_fraction,
        "spacing_tolerance_fraction": policy.spacing_tolerance_fraction,
        "minimum_contour_length_m": policy.minimum_row_length_m,
        "minimum_work_path_radius_m": minimum_radius_m,
        "minimum_terrain_gradient": policy.minimum_terrain_gradient,
    }
    # Newer solver revisions expose the early-stop threshold explicitly.  The
    # compatibility check keeps the runner usable during the parallel CF0 API
    # rollout without silently passing an unknown policy field.
    if "integrability_convergence_tolerance" in ContinuousFamilyPolicy.__dataclass_fields__:
        keyword_arguments["integrability_convergence_tolerance"] = policy.convergence_tolerance
    if "maximum_eikonal_residual_p95" in ContinuousFamilyPolicy.__dataclass_fields__:
        keyword_arguments["maximum_eikonal_residual_p95"] = policy.eikonal_residual_tolerance
    return ContinuousFamilyPolicy(**keyword_arguments)


def phase_grid_metrics(
    phase: np.ndarray,
    mask: np.ndarray,
    resolution_m: float,
    row_spacing_m: float,
) -> dict[str, float | None]:
    filled = np.where(mask & np.isfinite(phase), phase, np.nan)
    # The solver's masked finite differences are mirrored here only for the
    # publication gates; invalid neighbours are excluded from the statistic.
    derivative_y, derivative_x = np.gradient(filled, resolution_m, resolution_m)
    magnitude = np.hypot(derivative_x, derivative_y)
    valid = mask & np.isfinite(magnitude) & (magnitude > 1e-9)
    if not np.any(valid):
        return {"eikonal_p95": None, "spacing_error_p95_m": None}
    eikonal = np.abs(magnitude[valid] - 1.0)
    spacing = row_spacing_m / magnitude[valid]
    return {
        "eikonal_p95": percentile(eikonal, 95),
        "spacing_error_p95_m": percentile(np.abs(spacing - row_spacing_m), 95),
    }


def _intersection_points(geometry) -> list[Point]:
    if geometry.is_empty:
        return []
    if isinstance(geometry, Point):
        return [geometry]
    if isinstance(geometry, MultiPoint):
        return list(geometry.geoms)
    if isinstance(geometry, LineString):
        coordinates = list(geometry.coords)
        return [Point(coordinates[0]), Point(coordinates[-1])]
    if isinstance(geometry, GeometryCollection) or hasattr(geometry, "geoms"):
        points: list[Point] = []
        for part in geometry.geoms:
            points.extend(_intersection_points(part))
        return points
    return []


def _extend_endpoint_to_boundary(
    coordinates: list[tuple[float, float]],
    *,
    at_start: bool,
    usable,
    maximum_extension_m: float,
    snap_tolerance_m: float,
) -> tuple[list[tuple[float, float]], bool]:
    endpoint = np.asarray(coordinates[0 if at_start else -1], dtype=float)[:2]
    neighbour = np.asarray(coordinates[1 if at_start else -2], dtype=float)[:2]
    outward = endpoint - neighbour
    norm = float(np.linalg.norm(outward))
    if norm <= 1e-9:
        return coordinates, False
    outward /= norm
    endpoint_point = Point(float(endpoint[0]), float(endpoint[1]))
    if endpoint_point.distance(usable.boundary) <= snap_tolerance_m:
        nearest_distance = usable.boundary.project(endpoint_point)
        snapped = usable.boundary.interpolate(nearest_distance)
        hit = (float(snapped.x), float(snapped.y))
    else:
        target = endpoint + outward * maximum_extension_m
        ray = LineString(
            [(float(endpoint[0]), float(endpoint[1])), (float(target[0]), float(target[1]))]
        )
        candidates: list[tuple[float, Point]] = []
        for point in _intersection_points(ray.intersection(usable.boundary)):
            delta = np.asarray([point.x, point.y], dtype=float) - endpoint
            distance_along = float(np.dot(delta, outward))
            lateral = abs(float(np.cross(outward, delta)))
            if distance_along > 1e-7 and lateral <= 1e-5:
                candidates.append((distance_along, point))
        if candidates:
            distance_along, point = min(candidates, key=lambda item: item[0])
            if distance_along > maximum_extension_m + 1e-7:
                return coordinates, False
        else:
            return coordinates, False
        extension = LineString([tuple(endpoint), (point.x, point.y)])
        if not extension.within(usable.buffer(max(snap_tolerance_m, 1e-7))):
            return coordinates, False
        hit = (float(point.x), float(point.y))

    endpoint_tuple = (float(endpoint[0]), float(endpoint[1]))
    if math.dist(hit, endpoint_tuple) <= 1e-8:
        replacement = [hit, *coordinates[1:]] if at_start else [*coordinates[:-1], hit]
    elif at_start:
        replacement = [hit, *coordinates]
    else:
        replacement = [*coordinates, hit]
    return replacement, True


def extend_line_to_physical_boundary(
    line: LineString,
    usable,
    *,
    maximum_extension_m: float,
    snap_tolerance_m: float,
) -> tuple[LineString, bool]:
    if line.is_ring:
        return line, False
    coordinates = [(float(x), float(y)) for x, y, *_ in line.coords]
    if len(coordinates) < 2:
        return line, False
    coordinates, start_ok = _extend_endpoint_to_boundary(
        coordinates,
        at_start=True,
        usable=usable,
        maximum_extension_m=maximum_extension_m,
        snap_tolerance_m=snap_tolerance_m,
    )
    coordinates, end_ok = _extend_endpoint_to_boundary(
        coordinates,
        at_start=False,
        usable=usable,
        maximum_extension_m=maximum_extension_m,
        snap_tolerance_m=snap_tolerance_m,
    )
    extended = LineString(coordinates)
    endpoints_supported = (
        start_ok
        and end_ok
        and Point(extended.coords[0]).distance(usable.boundary) <= snap_tolerance_m
        and Point(extended.coords[-1]).distance(usable.boundary) <= snap_tolerance_m
    )
    return extended, bool(endpoints_supported)


def resampled_polyline_minimum_radius(line: LineString, step_m: float) -> float | None:
    if line.length <= step_m * 2:
        coordinates = list(line.coords)
    else:
        distances = np.linspace(
            0.0,
            line.length,
            max(3, int(math.ceil(line.length / step_m)) + 1),
        )
        coordinates = [line.interpolate(float(distance)).coords[0] for distance in distances]
    minimum = math.inf
    for first, middle, last in zip(coordinates, coordinates[1:], coordinates[2:]):
        first_side = math.dist(first[:2], middle[:2])
        second_side = math.dist(middle[:2], last[:2])
        chord = math.dist(first[:2], last[:2])
        twice_area = abs(
            (middle[0] - first[0]) * (last[1] - middle[1])
            - (middle[1] - first[1]) * (last[0] - middle[0])
        )
        if (
            min(first_side, second_side, chord) <= 1e-9
            or twice_area <= 1e-10 * max(first_side * second_side, 1.0)
        ):
            continue
        radius = first_side * second_side * chord / (2.0 * twice_area)
        if math.isfinite(radius):
            minimum = min(minimum, radius)
    return None if math.isinf(minimum) else minimum


def smooth_line_c2(
    line: LineString,
    usable,
    *,
    sample_step_m: float,
    maximum_deviation_m: float,
    endpoint_tolerance_m: float,
    representation_tolerance_fraction: float = 0.10,
) -> tuple[LineString | None, dict[str, Any]]:
    if (
        not math.isfinite(representation_tolerance_fraction)
        or representation_tolerance_fraction <= 0
        or representation_tolerance_fraction >= 1
    ):
        raise ValueError("representation_tolerance_fraction must be between zero and one")
    input_step = min(sample_step_m, max(0.35, line.length / 80.0))
    input_count = max(6, int(math.ceil(line.length / input_step)) + 1)
    original = np.asarray(line.coords, dtype=float)[:, :2]
    original_chainage = np.concatenate(
        ([0.0], np.cumsum(np.linalg.norm(np.diff(original, axis=0), axis=1)))
    )
    distances = np.unique(
        np.concatenate((np.linspace(0.0, line.length, input_count), original_chainage))
    )
    coordinates = np.asarray(
        [line.interpolate(float(distance)).coords[0][:2] for distance in distances], dtype=float
    )
    keep = np.concatenate(([True], np.linalg.norm(np.diff(coordinates, axis=0), axis=1) > 1e-8))
    coordinates = coordinates[keep]
    if coordinates.shape[0] < 4:
        return None, {"status": "FAIL_TOO_FEW_SPLINE_POINTS", "blocker_code": "ROW_SPLINE_FIT_FAILED"}
    cumulative = np.concatenate(
        ([0.0], np.cumsum(np.linalg.norm(np.diff(coordinates, axis=0), axis=1)))
    )
    if cumulative[-1] <= 1e-9:
        return None, {"status": "FAIL_DEGENERATE_LINE", "blocker_code": "ROW_SPLINE_FIT_FAILED"}
    u = cumulative / cumulative[-1]
    weights = np.ones(coordinates.shape[0], dtype=float)
    weights[[0, -1]] = 1e6
    representation_tolerance_m = maximum_deviation_m * representation_tolerance_fraction
    smoothing_factors = (1.0, 0.50, 0.20, 0.05, 0.0)
    for factor in smoothing_factors:
        try:
            smoothing = float(coordinates.shape[0] * (maximum_deviation_m * factor) ** 2)
            with warnings.catch_warnings():
                warnings.simplefilter("error", RuntimeWarning)
                spline, _ = splprep(
                    [coordinates[:, 0], coordinates[:, 1]],
                    u=u,
                    w=weights,
                    s=smoothing,
                    k=3,
                )
            output_u = np.unique(np.concatenate(([0.0, 1.0], u)))
            representation_converged = False
            maximum_chord_error = math.inf
            for _ in range(18):
                x_nodes, y_nodes = splev(output_u, spline, der=0)
                nodes = np.column_stack((x_nodes, y_nodes))
                midpoint_u = (output_u[:-1] + output_u[1:]) * 0.5
                x_mid, y_mid = splev(midpoint_u, spline, der=0)
                midpoints = np.column_stack((x_mid, y_mid))
                segment = nodes[1:] - nodes[:-1]
                segment_length_squared = np.sum(segment * segment, axis=1)
                projection_fraction = np.divide(
                    np.sum((midpoints - nodes[:-1]) * segment, axis=1),
                    segment_length_squared,
                    out=np.zeros_like(segment_length_squared),
                    where=segment_length_squared > 1e-18,
                )
                projection_fraction = np.clip(projection_fraction, 0.0, 1.0)
                projected = nodes[:-1] + projection_fraction[:, None] * segment
                chord_error = np.linalg.norm(midpoints - projected, axis=1)
                chord_length = np.sqrt(segment_length_squared)
                maximum_chord_error = float(np.max(chord_error)) if chord_error.size else 0.0
                refine = (chord_error > representation_tolerance_m) | (
                    chord_length > sample_step_m
                )
                if not np.any(refine):
                    representation_converged = True
                    break
                output_u = np.sort(np.concatenate((output_u, midpoint_u[refine])))
            if not representation_converged:
                continue
            x_values, y_values = splev(output_u, spline, der=0)
            output = np.column_stack((x_values, y_values))
            output[0] = coordinates[0]
            output[-1] = coordinates[-1]
            smoothed = LineString(output)
            deviation = float(line.hausdorff_distance(smoothed))
            if not smoothed.is_simple or deviation > maximum_deviation_m + 1e-9:
                continue
            if not smoothed.within(usable.buffer(endpoint_tolerance_m)):
                continue
            if (
                Point(smoothed.coords[0]).distance(usable.boundary) > endpoint_tolerance_m
                or Point(smoothed.coords[-1]).distance(usable.boundary) > endpoint_tolerance_m
            ):
                continue
            first_x, first_y = splev(output_u, spline, der=1)
            second_x, second_y = splev(output_u, spline, der=2)
            first_x = np.asarray(first_x, dtype=float)
            first_y = np.asarray(first_y, dtype=float)
            numerator = np.abs(
                first_x * np.asarray(second_y, dtype=float)
                - first_y * np.asarray(second_x, dtype=float)
            )
            denominator = np.power(first_x * first_x + first_y * first_y, 1.5)
            curvature = np.divide(
                numerator,
                denominator,
                out=np.zeros_like(numerator),
                where=denominator > 1e-12,
            )
            maximum_curvature = float(np.max(curvature)) if curvature.size else 0.0
            analytic_minimum_radius = (
                1.0 / maximum_curvature if maximum_curvature > 1e-10 else math.inf
            )
            sampled_minimum_radius = resampled_polyline_minimum_radius(
                smoothed,
                sample_step_m,
            )
            minimum_radius = min(
                analytic_minimum_radius,
                sampled_minimum_radius
                if sampled_minimum_radius is not None
                else analytic_minimum_radius,
            )
            return smoothed, {
                "status": "PASS_C2_PARAMETRIC_SPLINE",
                "maximum_deviation_m": deviation,
                "minimum_radius_m": minimum_radius,
                "analytic_minimum_radius_m": analytic_minimum_radius,
                "sampled_minimum_radius_m": sampled_minimum_radius,
                "smoothing_factor": factor,
                "sample_count": int(output_u.size),
                "input_vertex_count": int(original.shape[0]),
                "input_sample_count": int(coordinates.shape[0]),
                "representation_tolerance_m": representation_tolerance_m,
                "representation_chord_error_m": maximum_chord_error,
                "blocker_code": None,
            }
        except (RuntimeWarning, TypeError, ValueError):
            continue
    return None, {
        "status": "FAIL_C2_SPLINE_TOLERANCE_OR_DOMAIN",
        "blocker_code": "ROW_SPLINE_FIT_FAILED",
    }


def extract_metric_lines(
    contour_records: list[dict[str, Any]],
    field: dict[str, Any],
    terrain: e0.Terrain,
    profile: dict[str, Any],
    family_id: str,
    policy: Cf0Policy,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    next_index = 1
    for contour in contour_records:
        clipped = contour["geometry"].intersection(field["usable"])
        for part in e0.iter_lines(clipped):
            maximum_extension = policy.grid_resolution_m * policy.endpoint_extension_factor
            extended, endpoints_supported = extend_line_to_physical_boundary(
                part,
                field["usable"],
                maximum_extension_m=maximum_extension,
                snap_tolerance_m=policy.endpoint_snap_tolerance_m,
            )
            if extended.length + 1e-9 < policy.minimum_row_length_m:
                continue
            smoothed, spline_qa = smooth_line_c2(
                extended,
                field["usable"],
                sample_step_m=policy.curvature_sample_step_m,
                maximum_deviation_m=policy.spline_max_deviation_m,
                endpoint_tolerance_m=policy.endpoint_snap_tolerance_m,
                representation_tolerance_fraction=policy.spline_representation_tolerance_fraction,
            )
            geometry = smoothed if smoothed is not None else extended
            oriented, metrics = e0.line_metrics(geometry, terrain)
            line_id = f"{family_id}:R{next_index:05d}"
            records.append(
                {
                    "line_id": line_id,
                    "level_id": int(contour["level_id"]),
                    "phase_level_m": float(contour["phase_level_m"]),
                    "geometry": oriented,
                    "metrics": metrics,
                    "scenario": {"id": profile["candidate_id"]},
                    "field": field,
                    "row_index": next_index,
                    # This signed integer k may repeat when one phase level
                    # has multiple disconnected segments.
                    "phase_level_index": int(contour["level_id"]),
                    "sequence_index": next_index,
                    "endpoints_extended_to_boundary": endpoints_supported,
                    "spline_qa": spline_qa,
                }
            )
            next_index += 1
    return records


def final_normal_ray_spacing(
    records: list[dict[str, Any]],
    row_spacing_m: float,
    tolerance_fraction: float,
) -> dict[str, Any]:
    by_level: dict[int, list[LineString]] = {}
    for record in records:
        by_level.setdefault(int(record["level_id"]), []).append(record["geometry"])
    level_geometry = {level: unary_union(lines) for level, lines in by_level.items()}
    maximum_ray_m = row_spacing_m * 4.0
    sample_interval_m = max(6.0, row_spacing_m * 4.0)
    endpoint_trim_m = max(3.0, row_spacing_m * 2.0)
    distances: list[float] = []
    unpaired = 0
    for record in records:
        line = record["geometry"]
        level = int(record["level_id"])
        adjacent = [
            level_geometry[candidate]
            for candidate in (level - 1, level + 1)
            if candidate in level_geometry
        ]
        start = min(endpoint_trim_m, line.length * 0.25)
        stop = line.length - start
        if stop <= start:
            positions = [line.length * 0.5]
        else:
            count = max(1, int(math.ceil((stop - start) / sample_interval_m)))
            positions = np.linspace(start, stop, count + 1)
        if not adjacent:
            unpaired += len(positions)
            continue
        target = unary_union(adjacent)
        for position in positions:
            position = float(position)
            epsilon = min(max(0.25, row_spacing_m * 0.25), max(line.length * 0.02, 0.25))
            first = line.interpolate(max(0.0, position - epsilon))
            second = line.interpolate(min(line.length, position + epsilon))
            tangent = np.asarray([second.x - first.x, second.y - first.y], dtype=float)
            tangent_norm = float(np.linalg.norm(tangent))
            if tangent_norm <= 1e-9:
                unpaired += 1
                continue
            tangent /= tangent_norm
            normal = np.asarray([-tangent[1], tangent[0]], dtype=float)
            point = line.interpolate(position)
            center = np.asarray([point.x, point.y], dtype=float)
            ray = LineString(
                [
                    tuple(center - normal * maximum_ray_m),
                    tuple(center + normal * maximum_ray_m),
                ]
            )
            intersection = ray.intersection(target)
            if intersection.is_empty:
                unpaired += 1
                continue
            distance = float(point.distance(intersection))
            if not math.isfinite(distance) or distance > maximum_ray_m + 1e-9:
                unpaired += 1
                continue
            distances.append(distance)
    values = np.asarray(distances, dtype=float)
    total = int(values.size) + unpaired
    lower = row_spacing_m * (1.0 - tolerance_fraction)
    upper = row_spacing_m * (1.0 + tolerance_fraction)
    paired_outside = int(np.count_nonzero((values < lower) | (values > upper)))
    error = np.abs(values - row_spacing_m)
    return {
        "method": "LOCAL_NORMAL_RAY_TO_ADJACENT_PHASE_LEVEL",
        "sample_count": int(values.size),
        "unpaired_sample_count": int(unpaired),
        "spacing_p05_m": percentile(values, 5),
        "spacing_p50_m": percentile(values, 50),
        "spacing_p95_m": percentile(values, 95),
        "spacing_error_p95_m": percentile(error, 95),
        "outside_tolerance_percent": (
            100.0 * (paired_outside + unpaired) / total if total else None
        ),
    }


def _minimum_finite(*values: Any) -> float | None:
    finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return min(finite) if finite else None


def build_hydraulic_record(
    request: ResolvedProjectRequest,
    *,
    candidate_id: str,
    field_id: str,
    work_block_id: str,
    family_id: str,
    metric_lines: list[dict[str, Any]],
) -> dict[str, Any]:
    rainfall = request.value("conservation.design_rainfall_ref")
    soil = request.value("agronomy.soil_zones_dataset_ref")
    conditioning = request.value("terrain.hydrologic_conditioning_status", "NOT_STARTED")
    receiver_inventory = request.value("conservation.receiver_inventory_status", "NOT_REVIEWED")
    receiver_dataset = request.value("conservation.receiver_dataset_ref")

    idf_status = "PROVIDED" if rainfall else "MISSING"
    soil_status = "PROVIDED" if soil else "MISSING"
    contributing_status = "PROVIDED" if conditioning == "APPROVED" else "UNCONFIRMED"
    receiver_status = (
        "PROVIDED"
        if receiver_inventory == "PROVIDED" and receiver_dataset
        else "MISSING" if receiver_inventory == "DECLARED_NONE" else "UNCONFIRMED"
    )
    outlet_status = receiver_status
    section_status = "MISSING"
    roughness_status = "MISSING"
    downstream_status = "UNCONFIRMED"

    status_to_code = (
        (idf_status, "RAIN_IDF_EVENT"),
        (soil_status, "SOIL_INFILTRATION"),
        (contributing_status, "COMPLETE_CONTRIBUTING_CATCHMENT"),
        (section_status, "ROW_OR_STRUCTURE_SECTION"),
        (roughness_status, "ROUGHNESS"),
        (receiver_status, "VERIFIED_RECEIVER_NETWORK"),
    )
    missing_inputs = [code for status, code in status_to_code if status != "PROVIDED"]
    lengths = np.asarray([item["metrics"]["length_m"] for item in metric_lines], dtype=float)
    total_length = float(lengths.sum()) if lengths.size else 0.0
    adverse = (
        float(
            np.sum(
                lengths
                * np.asarray(
                    [item["metrics"]["adverse_length_percent"] for item in metric_lines],
                    dtype=float,
                )
            )
            / total_length
        )
        if total_length > 0
        else None
    )
    return {
        "candidate_id": candidate_id,
        "field_id": field_id,
        "work_block_id": work_block_id,
        "family_id": family_id,
        "hydraulic_status": HYDRAULIC_STATUS,
        "idf_status": idf_status,
        "soil_status": soil_status,
        "contributing_area_status": contributing_status,
        "outlet_status": outlet_status,
        "receiver_status": receiver_status,
        "section_status": section_status,
        "roughness_status": roughness_status,
        "downstream_status": downstream_status,
        "grade_p95_pct": rounded(
            percentile(
                (item["metrics"]["absolute_grade_p95_percent"] for item in metric_lines), 95
            ),
            6,
        ),
        "grade_max_pct": rounded(
            max(
                (item["metrics"]["absolute_grade_max_percent"] for item in metric_lines),
                default=math.nan,
            ),
            6,
        ),
        "reversal_count": int(
            sum(item["metrics"]["reversal_count"] for item in metric_lines)
        ),
        "adverse_length_pct": rounded(adverse, 6),
        "missing_inputs": missing_inputs,
        "blocker_codes": [
            "CF0_NO_HYDRAULIC_SOLVER",
            *[f"HYDRAULIC_INPUT_{code}_UNCONFIRMED" for code in missing_inputs],
        ],
    }


def _validated_phase_offset_fractions(policy: Cf0Policy) -> tuple[float, ...]:
    fractions = tuple(float(value) for value in policy.phase_offset_fractions)
    if not fractions:
        raise ValueError("phase_offset_fractions must contain at least one value")
    if any(not math.isfinite(value) or value < 0 or value >= 1 for value in fractions):
        raise ValueError("phase_offset_fractions must be finite and in [0, 1)")
    if len(set(fractions)) != len(fractions):
        raise ValueError("phase_offset_fractions must not contain duplicates")
    if fractions != tuple(sorted(fractions)):
        raise ValueError("phase_offset_fractions must be in increasing order")
    if fractions[0] != 0.0:
        raise ValueError("phase_offset_fractions must start with diagnostic offset zero")
    return fractions


def phase_offset_invariant_blockers(
    solver_qa: dict[str, Any],
    extraction_qa: dict[str, Any],
    *,
    solver_spacing_error_m: float | None,
    solver_spacing_outside_fraction: float | None,
    eikonal_p95: float | None,
    integrability_p95: float | None,
    orientation_p95_deg: float | None,
    row_spacing_m: float,
    policy: Cf0Policy,
) -> list[str]:
    """Return gates that cannot change when only the phase offset changes."""

    blockers = {str(code) for code in solver_qa.get("blocker_codes", [])}
    if extraction_qa.get("gradient_estimation_status") != "PASS":
        blockers.add("PHASE_HALO_GRADIENT_UNESTIMABLE")
    spacing_tolerance_m = row_spacing_m * policy.spacing_tolerance_fraction
    if (
        solver_spacing_error_m is None
        or solver_spacing_error_m > spacing_tolerance_m + 1e-9
    ):
        blockers.add("SOLVER_NORMAL_SPACING_OUTSIDE_CF0_TOLERANCE")
    if (
        solver_spacing_outside_fraction is None
        or solver_spacing_outside_fraction
        > policy.maximum_spacing_outside_tolerance_fraction + 1e-9
    ):
        blockers.add("SOLVER_NORMAL_SPACING_AREA_EXCESS")
    if eikonal_p95 is None or eikonal_p95 > policy.eikonal_residual_tolerance + 1e-9:
        blockers.add("EIKONAL_P95_OUTSIDE_CF0_TOLERANCE")
    if (
        integrability_p95 is None
        or integrability_p95 > policy.integrability_residual_tolerance + 1e-9
    ):
        blockers.add("INTEGRABILITY_P95_OUTSIDE_CF0_TOLERANCE")
    if (
        orientation_p95_deg is None
        or orientation_p95_deg > policy.maximum_frustration_deg + 1e-9
    ):
        blockers.add("ORIENTATION_P95_OUTSIDE_CF0_TOLERANCE")
    return sorted(blockers)


def pruned_phase_offset_attempt(
    phase_offset_fraction: float,
    *,
    row_spacing_m: float,
    minimum_radius_m: float | None,
    invariant_blockers: Iterable[str],
    extraction_qa: dict[str, Any],
) -> dict[str, Any]:
    """Persist an honest fail-closed trial without materializing impossible rows."""

    blockers = {
        str(code) for code in invariant_blockers
    } | {
        PHASE_OFFSET_PRUNED_BLOCKER,
        "NO_EXTRACTED_ROWS",
        "SPACING_P95_OUTSIDE_CF0_TOLERANCE",
        "SPACING_OUTSIDE_TOLERANCE_AREA_EXCESS",
        "COVERAGE_RATIO_OUTSIDE_CF0_ENVELOPE",
    }
    radius_status = "NOT_EVALUATED"
    if minimum_radius_m is not None:
        radius_status = "FAIL"
        blockers.add("MINIMUM_WORK_PATH_RADIUS_VIOLATION")
    return {
        "phase_offset_fraction": float(phase_offset_fraction),
        "phase_offset_m": float(phase_offset_fraction) * row_spacing_m,
        "geometric_status": "NO_FEASIBLE_FAMILY",
        "blocker_codes": sorted(blockers),
        "metric_lines": [],
        "continuity": {"group_summaries": {}, "line_diagnostics": {}},
        "metrics": {
            "spacing_error_p95_m": None,
            "eikonal_residual_p95": None,
            "integrability_residual_p95": None,
            "orientation_misalignment_p95_deg": None,
            "minimum_radius_m": None,
            "radius_status": radius_status,
            "maximum_abs_grade_pct": None,
            "internal_endpoint_count": 0,
            "intersection_count": 0,
            "self_intersection_count": 0,
            "loop_count": 0,
            "coverage_proxy_ratio": 0.0,
            "solver_gate_metrics": {
                "final_spacing_outside_tolerance_fraction": None,
                "spline_failure_count": 0,
            },
        },
        "total_length_m": 0.0,
        "minimum_radius_order_value": None,
        "extraction_qa": extraction_qa,
    }


def complete_phase_offset_attempts(
    diagnostic_attempt: dict[str, Any],
    *,
    policy: Cf0Policy,
    row_spacing_m: float,
    minimum_radius_m: float | None,
    invariant_blockers: Iterable[str],
    extraction_qa: dict[str, Any],
    evaluator: Callable[[float], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Materialize recoverable offsets and audit-prune invariant failures."""

    if float(diagnostic_attempt.get("phase_offset_fraction", math.nan)) != 0.0:
        raise ValueError("diagnostic phase-offset attempt must use fraction zero")
    invariant = tuple(sorted({str(code) for code in invariant_blockers}))
    attempts = [diagnostic_attempt]
    for fraction in _validated_phase_offset_fractions(policy)[1:]:
        if invariant:
            attempts.append(
                pruned_phase_offset_attempt(
                    fraction,
                    row_spacing_m=row_spacing_m,
                    minimum_radius_m=minimum_radius_m,
                    invariant_blockers=invariant,
                    extraction_qa=extraction_qa,
                )
            )
        else:
            attempts.append(evaluator(fraction))
    return attempts


def evaluate_phase_offset_attempt(
    request: ResolvedProjectRequest,
    field: dict[str, Any],
    terrain: e0.Terrain,
    profile: dict[str, Any],
    policy: Cf0Policy,
    row_spacing_m: float,
    minimum_radius_m: float | None,
    family_id: str,
    solver_policy: ContinuousFamilyPolicy,
    solver_result: dict[str, Any],
    solver_phase: np.ndarray,
    solver_mask: np.ndarray,
    extraction_phase: np.ndarray,
    extraction_mask: np.ndarray,
    extraction_qa: dict[str, Any],
    x_coordinates: np.ndarray,
    y_coordinates: np.ndarray,
    phase_offset_fraction: float,
) -> dict[str, Any]:
    """Extract and apply every CF0 gate for one discrete phase offset."""

    phase_offset_m = float(phase_offset_fraction) * row_spacing_m
    contours = phase_contours(
        extraction_phase,
        extraction_mask,
        x_coordinates,
        y_coordinates,
        row_spacing_m,
        policy.minimum_row_length_m,
        level_range_mask=solver_mask,
        phase_offset_m=phase_offset_m,
    )
    metric_lines = extract_metric_lines(
        contours,
        field,
        terrain,
        profile,
        family_id,
        policy,
    )
    radius_neutral_solver_policy = replace(
        solver_policy, minimum_work_path_radius_m=None
    )
    geometry_evaluation = evaluate_family_geometry(
        metric_lines,
        radius_neutral_solver_policy,
        physical_boundary=field["usable"].boundary,
        endpoint_tolerance_m=policy.endpoint_snap_tolerance_m,
    )
    continuity = analyze_operational_continuity(
        metric_lines,
        [field],
        ContinuityPolicy(
            terminal_surface_tolerance_m=policy.endpoint_snap_tolerance_m,
            row_spacing_m=row_spacing_m,
            spacing_tolerance_fraction=policy.spacing_tolerance_fraction,
            spacing_sample_interval_m=max(row_spacing_m * 4.0, 6.0),
            spacing_endpoint_trim_m=max(row_spacing_m * 2.0, 3.0),
            required_minimum_radius_m=None,
        ),
    )
    candidate_id = str(profile["candidate_id"])
    field_id = str(field["code"])
    continuity_summary = continuity["group_summaries"].get(
        (candidate_id, field_id), {}
    )
    solver_qa = solver_result["qa"]
    grid_metrics = phase_grid_metrics(
        solver_phase, solver_mask, policy.grid_resolution_m, row_spacing_m
    )
    integrability_p95 = _qa_value(
        solver_qa, "integrability.relative_residual_p95", "integrability.residual_p95"
    )
    orientation_p95 = _qa_value(
        solver_qa,
        "integrability.angular_error_p95_deg",
        "orientation.misalignment_p95_deg",
    )
    final_spacing = final_normal_ray_spacing(
        metric_lines, row_spacing_m, policy.spacing_tolerance_fraction
    )
    spacing_error_p95 = rounded(final_spacing["spacing_error_p95_m"], 10)
    eikonal_p95 = _qa_value(solver_qa, "integrability.eikonal_residual_p95")
    if eikonal_p95 is None:
        eikonal_p95 = grid_metrics["eikonal_p95"]
    total_length = float(sum(item["metrics"]["length_m"] for item in metric_lines))
    coverage_ratio = total_length * row_spacing_m / max(field["usable"].area, 1e-9)
    intersection_count = int(
        geometry_evaluation["crossing_or_overlap_pair_count"]
        + geometry_evaluation["touching_pair_count"]
    )
    internal_endpoints = int(geometry_evaluation["unsupported_endpoint_count"])
    self_intersections = int(geometry_evaluation["self_intersection_count"])
    loops = int(sum(item["geometry"].is_ring for item in metric_lines))
    spline_failures = [
        item
        for item in metric_lines
        if item["spline_qa"].get("blocker_code") is not None
    ]
    endpoint_failures = [
        item for item in metric_lines if not item["endpoints_extended_to_boundary"]
    ]
    raw_spline_radii = [
        item["spline_qa"].get("minimum_radius_m")
        for item in metric_lines
        if item["spline_qa"].get("minimum_radius_m") is not None
    ]
    finite_spline_radii = [
        float(value) for value in raw_spline_radii if math.isfinite(float(value))
    ]
    minimum_radius_observed = (
        min(finite_spline_radii)
        if finite_spline_radii
        else math.inf
        if raw_spline_radii and not spline_failures
        else None
    )
    if minimum_radius_m is None:
        radius_status = "NOT_EVALUATED"
    elif spline_failures or minimum_radius_observed is None:
        radius_status = "FAIL"
    elif minimum_radius_observed + 1e-9 < minimum_radius_m:
        radius_status = "FAIL"
    else:
        radius_status = "PASS"

    blockers: list[str] = []
    blockers.extend(str(code) for code in solver_qa.get("blocker_codes", []))
    blockers.extend(str(code) for code in geometry_evaluation.get("blocker_codes", []))
    continuity_codes = continuity_summary.get("continuity_blocker_codes", "")
    blockers.extend(filter(None, str(continuity_codes).split(",")))
    if extraction_qa.get("gradient_estimation_status") != "PASS":
        blockers.append("PHASE_HALO_GRADIENT_UNESTIMABLE")
    if not metric_lines:
        blockers.append("NO_EXTRACTED_ROWS")
    if endpoint_failures:
        blockers.append("INTERNAL_UNSUPPORTED_ENDPOINT")
    if spline_failures:
        blockers.append("ROW_SPLINE_FIT_FAILED")
    if minimum_radius_m is not None and radius_status == "FAIL":
        blockers.append("MINIMUM_WORK_PATH_RADIUS_VIOLATION")
    spacing_tolerance_m = row_spacing_m * policy.spacing_tolerance_fraction
    if spacing_error_p95 is None or spacing_error_p95 > spacing_tolerance_m + 1e-9:
        blockers.append("SPACING_P95_OUTSIDE_CF0_TOLERANCE")
    outside_spacing_percent = rounded(final_spacing["outside_tolerance_percent"], 10)
    maximum_spacing_outside_percent = (
        100.0 * policy.maximum_spacing_outside_tolerance_fraction
    )
    if (
        outside_spacing_percent is None
        or outside_spacing_percent > maximum_spacing_outside_percent + 1e-9
    ):
        blockers.append("SPACING_OUTSIDE_TOLERANCE_AREA_EXCESS")
    solver_spacing = solver_qa.get("normal_spacing", {})
    solver_spacing_p05 = rounded(solver_spacing.get("spacing_p05_m"), 10)
    solver_spacing_p95 = rounded(solver_spacing.get("spacing_p95_m"), 10)
    solver_spacing_error = (
        max(
            abs(solver_spacing_p05 - row_spacing_m),
            abs(solver_spacing_p95 - row_spacing_m),
        )
        if solver_spacing_p05 is not None and solver_spacing_p95 is not None
        else None
    )
    if solver_spacing_error is None or solver_spacing_error > spacing_tolerance_m + 1e-9:
        blockers.append("SOLVER_NORMAL_SPACING_OUTSIDE_CF0_TOLERANCE")
    solver_spacing_outside = rounded(
        solver_spacing.get("outside_tolerance_fraction"), 10
    )
    if (
        solver_spacing_outside is None
        or solver_spacing_outside
        > policy.maximum_spacing_outside_tolerance_fraction + 1e-9
    ):
        blockers.append("SOLVER_NORMAL_SPACING_AREA_EXCESS")
    if eikonal_p95 is None or eikonal_p95 > policy.eikonal_residual_tolerance + 1e-9:
        blockers.append("EIKONAL_P95_OUTSIDE_CF0_TOLERANCE")
    if (
        integrability_p95 is None
        or integrability_p95 > policy.integrability_residual_tolerance + 1e-9
    ):
        blockers.append("INTEGRABILITY_P95_OUTSIDE_CF0_TOLERANCE")
    if (
        orientation_p95 is None
        or orientation_p95 > policy.maximum_frustration_deg + 1e-9
    ):
        blockers.append("ORIENTATION_P95_OUTSIDE_CF0_TOLERANCE")
    if not policy.coverage_proxy_minimum <= coverage_ratio <= policy.coverage_proxy_maximum:
        blockers.append("COVERAGE_RATIO_OUTSIDE_CF0_ENVELOPE")
    blockers = sorted(set(blockers))
    geometric_status = "GEOMETRIC_PASS" if not blockers else "NO_FEASIBLE_FAMILY"

    solver_gate_metrics = {
        "solve_mask_component_count": solver_qa.get("domain", {}).get(
            "solve_mask_component_count"
        ),
        "low_coherence_fraction": rounded(
            solver_qa.get("orientation", {}).get("combined_low_coherence_fraction"), 10
        ),
        "abrupt_edge_fraction": rounded(
            solver_qa.get("axial_lift", {}).get("abrupt_edge_fraction"), 10
        ),
        "cycle_conflict_fraction": rounded(
            solver_qa.get("axial_lift", {}).get("cycle_conflict_fraction"), 10
        ),
        "integrability_residual_rms": rounded(
            solver_qa.get("integrability", {}).get("integrability_residual_rms"), 10
        ),
        "critical_fraction": rounded(
            solver_qa.get("critical_indicators", {}).get("critical_fraction"), 10
        ),
        "singular_fraction": rounded(
            solver_qa.get("critical_indicators", {}).get("singular_fraction"), 10
        ),
        "cut_locus_fraction": rounded(
            solver_qa.get("critical_indicators", {}).get("cut_locus_fraction"), 10
        ),
        "solver_spacing_p05_m": solver_spacing_p05,
        "solver_spacing_p95_m": solver_spacing_p95,
        "solver_spacing_outside_tolerance_fraction": solver_spacing_outside,
        "final_spacing_outside_tolerance_fraction": (
            rounded(outside_spacing_percent / 100.0, 10)
            if outside_spacing_percent is not None
            else None
        ),
        "spline_failure_count": len(spline_failures),
    }
    metrics = {
        "spacing_error_p95_m": rounded(spacing_error_p95, 8),
        "eikonal_residual_p95": rounded(eikonal_p95, 8),
        "integrability_residual_p95": rounded(integrability_p95, 8),
        "orientation_misalignment_p95_deg": rounded(orientation_p95, 8),
        "minimum_radius_m": rounded(minimum_radius_observed, 8),
        "radius_status": radius_status,
        "maximum_abs_grade_pct": rounded(
            max(
                (item["metrics"]["absolute_grade_max_percent"] for item in metric_lines),
                default=math.nan,
            ),
            8,
        ),
        "internal_endpoint_count": internal_endpoints,
        "intersection_count": intersection_count,
        "self_intersection_count": self_intersections,
        "loop_count": loops,
        "coverage_proxy_ratio": rounded(coverage_ratio, 8),
        "solver_gate_metrics": solver_gate_metrics,
    }
    return {
        "phase_offset_fraction": float(phase_offset_fraction),
        "phase_offset_m": phase_offset_m,
        "geometric_status": geometric_status,
        "blocker_codes": blockers,
        "metric_lines": metric_lines,
        "continuity": continuity,
        "metrics": metrics,
        "total_length_m": total_length,
        "minimum_radius_order_value": minimum_radius_observed,
        "extraction_qa": extraction_qa,
    }


def _phase_offset_trial_record(attempt: dict[str, Any]) -> dict[str, Any]:
    metrics = attempt["metrics"]
    gate_metrics = metrics["solver_gate_metrics"]
    radius_order_value = attempt.get("minimum_radius_order_value")
    if radius_order_value is None:
        radius_order_class = "UNAVAILABLE"
    else:
        radius_order_number = float(radius_order_value)
        if math.isfinite(radius_order_number):
            radius_order_class = "FINITE"
        elif radius_order_number > 0:
            radius_order_class = "UNBOUNDED_STRAIGHT"
        else:
            radius_order_class = "UNAVAILABLE"
    return {
        "phase_offset_fraction": attempt["phase_offset_fraction"],
        "phase_offset_m": rounded(attempt["phase_offset_m"], 8),
        "geometric_status": attempt["geometric_status"],
        "blocker_codes": list(attempt["blocker_codes"]),
        "extracted_row_count": len(attempt["metric_lines"]),
        "extracted_total_length_m": rounded(attempt["total_length_m"], 8),
        "minimum_radius_m": metrics["minimum_radius_m"],
        "minimum_radius_order_class": radius_order_class,
        "radius_status": metrics["radius_status"],
        "spacing_error_p95_m": metrics["spacing_error_p95_m"],
        "final_spacing_outside_tolerance_fraction": gate_metrics[
            "final_spacing_outside_tolerance_fraction"
        ],
        "coverage_proxy_ratio": metrics["coverage_proxy_ratio"],
        "spline_failure_count": gate_metrics["spline_failure_count"],
        "internal_endpoint_count": metrics["internal_endpoint_count"],
        "intersection_count": metrics["intersection_count"],
        "self_intersection_count": metrics["self_intersection_count"],
        "loop_count": metrics["loop_count"],
    }


def select_phase_offset_attempt(
    attempts: list[dict[str, Any]], policy: Cf0Policy
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Select only among PASS attempts; offset zero is diagnostic otherwise."""

    if not attempts:
        raise ValueError("phase offset selection requires at least one attempt")
    passing = [
        attempt
        for attempt in attempts
        if attempt.get("geometric_status") == "GEOMETRIC_PASS"
        and not attempt.get("blocker_codes")
    ]

    def finite_or_infinity(value: Any) -> float:
        if value is None:
            return math.inf
        number = float(value)
        return number if math.isfinite(number) else math.inf

    def pass_key(attempt: dict[str, Any]) -> tuple[float, float, float, float, float]:
        radius = attempt.get("minimum_radius_order_value")
        radius_value = float(radius) if radius is not None else -math.inf
        radius_key = -radius_value if math.isfinite(radius_value) else (
            -math.inf if radius_value > 0 else math.inf
        )
        metrics = attempt["metrics"]
        gate_metrics = metrics["solver_gate_metrics"]
        coverage = metrics.get("coverage_proxy_ratio")
        coverage_error = (
            abs(float(coverage) - 1.0) if coverage is not None else math.inf
        )
        return (
            radius_key,
            finite_or_infinity(
                gate_metrics.get("final_spacing_outside_tolerance_fraction")
            ),
            finite_or_infinity(metrics.get("spacing_error_p95_m")),
            coverage_error,
            float(attempt["phase_offset_fraction"]),
        )

    if passing:
        materialized = min(passing, key=pass_key)
        selection_status = "SELECTED_GEOMETRIC_PASS"
        selected_fraction = materialized["phase_offset_fraction"]
        selected_offset_m = materialized["phase_offset_m"]
        diagnostic_fraction = None
        diagnostic_offset_m = None
        phase_offset_role = "SELECTED_PASS"
    else:
        materialized = min(attempts, key=lambda item: float(item["phase_offset_fraction"]))
        selection_status = "NO_FEASIBLE_PHASE_OFFSET"
        selected_fraction = None
        selected_offset_m = None
        diagnostic_fraction = materialized["phase_offset_fraction"]
        diagnostic_offset_m = materialized["phase_offset_m"]
        phase_offset_role = "DIAGNOSTIC_ONLY"

    selection = {
        "revision": policy.phase_offset_search_revision,
        "selection_rule": policy.phase_offset_selection_rule,
        "search_scope": "DISCRETE_CONFIGURED_OFFSETS_NOT_CONTINUOUS_GAUGE_INVARIANCE",
        "selection_status": selection_status,
        "selection_key_order": [
            "MAXIMUM_MINIMUM_WORK_PATH_RADIUS_M",
            "MINIMUM_FINAL_SPACING_OUTSIDE_TOLERANCE_FRACTION",
            "MINIMUM_SPACING_ERROR_P95_M",
            "MINIMUM_ABSOLUTE_COVERAGE_PROXY_ERROR_FROM_1",
            "MINIMUM_PHASE_OFFSET_FRACTION",
        ],
        "coverage_proxy_target_ratio": 1.0,
        "phase_offset_role": phase_offset_role,
        "selected_phase_offset_fraction": selected_fraction,
        "selected_phase_offset_m": rounded(selected_offset_m, 8),
        "diagnostic_phase_offset_fraction": diagnostic_fraction,
        "diagnostic_phase_offset_m": rounded(diagnostic_offset_m, 8),
        "diagnostic_selection_rule": "LOWEST_CONFIGURED_PHASE_OFFSET_V1",
        "attempted_offsets": [_phase_offset_trial_record(item) for item in attempts],
    }
    return materialized, selection


def solve_candidate_field(
    request: ResolvedProjectRequest,
    field: dict[str, Any],
    terrain: e0.Terrain,
    profile: dict[str, Any],
    policy: Cf0Policy,
    row_spacing_m: float,
    minimum_radius_m: float | None,
    staged_raster_dir: Path,
    published_raster_dir: Path,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
    list[dict[str, Any]],
]:
    candidate_id = profile["candidate_id"]
    field_id = str(field["code"])
    work_block_id = str(field["work_block_id"])
    family_id = f"{candidate_id}:{work_block_id}"
    transform, width, height = grid_for_geometry(
        field["usable"],
        policy.grid_resolution_m,
        padding_cells=policy.contour_extrapolation_halo_cells + 1,
    )
    mask = rasterize_mask(
        field["usable"],
        transform,
        width,
        height,
        terrain.projection,
        all_touched=True,
    )
    if not np.any(mask):
        centroid = field["usable"].representative_point()
        column = int((centroid.x - transform[0]) / transform[1])
        row = int((centroid.y - transform[3]) / transform[5])
        mask[
            min(max(row, 0), height - 1),
            min(max(column, 0), width - 1),
        ] = True
    base_angle = longest_axis_angle_deg(field["usable"])
    contour_preference = float(profile["orientation_weights"]["contour"])
    solver_policy = make_solver_policy(policy, row_spacing_m, contour_preference, minimum_radius_m)
    safe_field = "".join(character if character.isalnum() else "_" for character in field_id)
    safe_block = "".join(
        character if character.isalnum() else "_" for character in work_block_id
    )
    stem = f"{candidate_id.lower()}__{safe_field}__{safe_block.lower()}"
    phase_staged = staged_raster_dir / f"{stem}__phase.tif"
    orientation_staged = staged_raster_dir / f"{stem}__orientation_coherence.tif"
    phase_published = published_raster_dir / phase_staged.name
    orientation_published = published_raster_dir / orientation_staged.name
    if np.count_nonzero(mask) < policy.minimum_work_block_grid_cells:
        theta = math.radians(base_angle)
        tangent_x, tangent_y = math.cos(theta), math.sin(theta)
        normal_x_value, normal_y_value = -tangent_y, tangent_x
        rows_index, columns_index = np.indices(mask.shape)
        xs_grid = transform[0] + (columns_index + 0.5) * transform[1]
        ys_grid = transform[3] + (rows_index + 0.5) * transform[5]
        phase = normal_x_value * xs_grid + normal_y_value * ys_grid
        normal_x = np.full(mask.shape, normal_x_value, dtype=float)
        normal_y = np.full(mask.shape, normal_y_value, dtype=float)
        coherence = np.ones(mask.shape, dtype=float)
        write_cf0_rasters(
            phase_staged,
            orientation_staged,
            phase=phase,
            normal_x=normal_x,
            normal_y=normal_y,
            coherence=coherence,
            mask=mask,
            transform=transform,
            projection=terrain.projection,
        )
        raster_records = [
            raster_record(
                orientation_staged,
                orientation_published,
                candidate_id=candidate_id,
                field_id=field_id,
                work_block_id=work_block_id,
                raster_role="orientation_coherence",
            ),
            raster_record(
                phase_staged,
                phase_published,
                candidate_id=candidate_id,
                field_id=field_id,
                work_block_id=work_block_id,
                raster_role="phase",
            ),
        ]
        radius_status = "NOT_EVALUATED" if minimum_radius_m is None else "FAIL"
        blockers = ["WORK_BLOCK_BELOW_CF0_GRID_SUPPORT"]
        if minimum_radius_m is not None:
            blockers.append("MINIMUM_WORK_PATH_RADIUS_NOT_EVALUATED")
        metrics = {
            "spacing_error_p95_m": None,
            "eikonal_residual_p95": None,
            "integrability_residual_p95": None,
            "orientation_misalignment_p95_deg": None,
            "minimum_radius_m": None,
            "radius_status": radius_status,
            "maximum_abs_grade_pct": None,
            "internal_endpoint_count": 0,
            "intersection_count": 0,
            "self_intersection_count": 0,
            "loop_count": 0,
            "coverage_proxy_ratio": 0.0,
            "solver_gate_metrics": {
                "solve_mask_component_count": None,
                "low_coherence_fraction": None,
                "abrupt_edge_fraction": None,
                "cycle_conflict_fraction": None,
                "integrability_residual_rms": None,
                "critical_fraction": None,
                "singular_fraction": None,
                "cut_locus_fraction": None,
                "solver_spacing_p05_m": None,
                "solver_spacing_p95_m": None,
                "solver_spacing_outside_tolerance_fraction": None,
                "final_spacing_outside_tolerance_fraction": None,
                "spline_failure_count": 0,
            },
        }
        phase_offset_selection = {
            "revision": policy.phase_offset_search_revision,
            "selection_rule": policy.phase_offset_selection_rule,
            "search_scope": "DISCRETE_CONFIGURED_OFFSETS_NOT_CONTINUOUS_GAUGE_INVARIANCE",
            "selection_status": "NOT_EVALUATED_WORK_BLOCK_BELOW_GRID_SUPPORT",
            "selection_key_order": [
                "MAXIMUM_MINIMUM_WORK_PATH_RADIUS_M",
                "MINIMUM_FINAL_SPACING_OUTSIDE_TOLERANCE_FRACTION",
                "MINIMUM_SPACING_ERROR_P95_M",
                "MINIMUM_ABSOLUTE_COVERAGE_PROXY_ERROR_FROM_1",
                "MINIMUM_PHASE_OFFSET_FRACTION",
            ],
            "coverage_proxy_target_ratio": 1.0,
            "phase_offset_role": "NOT_EVALUATED",
            "selected_phase_offset_fraction": None,
            "selected_phase_offset_m": None,
            "diagnostic_phase_offset_fraction": None,
            "diagnostic_phase_offset_m": None,
            "diagnostic_selection_rule": "LOWEST_CONFIGURED_PHASE_OFFSET_V1",
            "attempted_offsets": [],
            "row_spacing_m": row_spacing_m,
            "level_equation": (
                "phase_level_m = phase_level_index * row_spacing_m + phase_offset_m"
            ),
            "phase_level_index_scope": "CANDIDATE_WORK_BLOCK_PHASE_LEVEL",
            "row_index_definition": (
                "UNIQUE_NONNEGATIVE_OPERATIONAL_SEQUENCE_PER_CANDIDATE_WORK_BLOCK"
            ),
            "phase_gauge": {
                "method": "NOT_APPLICABLE_WORK_BLOCK_BELOW_GRID_SUPPORT",
                "anchor_phase_value_m": None,
                "anchors": [],
            },
        }
        candidate = {
            "candidate_id": candidate_id,
            "field_id": field_id,
            "work_block_id": work_block_id,
            "family_id": family_id,
            "phase_offset_m": None,
            "phase_offset_fraction": None,
            "phase_offset_role": "NOT_EVALUATED",
            "phase_offset_selection": phase_offset_selection,
            "contour_extrapolation_qa": {
                "method": "FIRST_ORDER_LOCAL_LSQ_GRADIENT_FAIL_CLOSED",
                "gradient_estimation_status": "NOT_EVALUATED",
                "halo_cells": policy.contour_extrapolation_halo_cells,
                "gradient_lsq_max_radius_cells": (
                    policy.contour_gradient_lsq_max_radius_cells
                ),
                "gradient_lsq_max_relative_residual": (
                    policy.contour_gradient_lsq_max_relative_residual
                ),
                "gradient_lsq_max_condition_number": (
                    policy.contour_gradient_lsq_max_condition_number
                ),
                "gradient_lsq_minimum_neighbor_count": 3,
                "gradient_lsq_required_rank": 2,
                "gradient_component_connectivity": 4,
                "halo_cell_count": 0,
                "supported_halo_cell_count": 0,
                "unsupported_halo_cell_count": 0,
                "direct_gradient_source_count": 0,
                "lsq_gradient_source_count": 0,
                "unestimable_gradient_source_count": 0,
                "missing_gradient_component_count": 0,
                "gradient_lsq_max_radius_used_cells": 0,
                "gradient_lsq_residual_rms_max": None,
                "gradient_lsq_relative_residual_max": None,
                "gradient_lsq_condition_number_max": None,
            },
            "geometric_status": "NO_FEASIBLE_FAMILY",
            "hydraulic_status": HYDRAULIC_STATUS,
            "row_count": 0,
            "total_length_m": 0.0,
            "diagnostic_row_count": 0,
            "diagnostic_total_length_m": 0.0,
            "metrics": metrics,
            "blocker_codes": blockers,
        }
        summary = {
            "candidate_id": candidate_id,
            "field_id": field_id,
            "work_block_id": work_block_id,
            "family_id": family_id,
            "phase_offset_m": None,
            "phase_offset_fraction": None,
            "phase_offset_role": "NOT_EVALUATED",
            "phase_offset_selection_status": (
                "NOT_EVALUATED_WORK_BLOCK_BELOW_GRID_SUPPORT"
            ),
            "geometry_status": "NO_FEASIBLE_FAMILY",
            "hydraulic_status": HYDRAULIC_STATUS,
            "row_count": 0,
            "total_length_m": 0.0,
            "diagnostic_row_count": 0,
            "diagnostic_total_length_m": 0.0,
            "spacing_p95_m": None,
            "eikonal_p95": None,
            "integrability_p95": None,
            "orientation_p95_deg": None,
            "min_radius_m": None,
            "radius_status": radius_status,
            "max_grade_pct": None,
            "internal_endpoints": 0,
            "intersections": 0,
            "self_intersections": 0,
            "loops": 0,
            "blocker_codes": blockers,
        }
        hydraulic = build_hydraulic_record(
            request,
            candidate_id=candidate_id,
            field_id=field_id,
            work_block_id=work_block_id,
            family_id=family_id,
            metric_lines=[],
        )
        return candidate, summary, [], [], hydraulic, raster_records

    elevation, elevation_gx, elevation_gy = sample_terrain_grid(terrain, mask, transform)
    # The numerical stencil treats row+ as Cartesian y+.  GeoTIFF north-up
    # arrays use row+ toward the south, so solve on a vertical flip and undo it
    # only for publication.  gy already stores the world-north component and
    # must not be sign-negated.
    solver_mask, solver_gx, solver_gy = north_up_to_solver_grid(
        mask, elevation_gx, elevation_gy
    )
    solver_gx, solver_gy = finite_solver_gradients(
        solver_mask, solver_gx, solver_gy
    )
    result = solve_continuous_phase(
        solver_mask,
        solver_gx,
        solver_gy,
        policy.grid_resolution_m,
        base_angle,
        contour_preference,
        solver_policy,
    )
    solve_mask_qa = solve_mask_connectivity_qa(
        solver_mask,
        required_component_count=policy.solve_mask_required_component_count,
        connectivity=policy.solve_mask_component_connectivity,
    )
    solver_domain_qa = result["qa"].setdefault("domain", {})
    solver_domain_qa.update(
        {key: value for key, value in solve_mask_qa.items() if key != "blocker_codes"}
    )
    blocker_codes = result["qa"].setdefault("blocker_codes", [])
    for blocker_code in solve_mask_qa["blocker_codes"]:
        if blocker_code not in blocker_codes:
            blocker_codes.append(blocker_code)
    solver_phase = _array_from_result(result, "phase")
    solver_normal_x = _array_from_result(result, "nx", "normal_x")
    solver_normal_y = _array_from_result(result, "ny", "normal_y")
    solver_coherence = _array_from_result(result, "orientation_coherence", "coherence")
    if any(
        array.shape != solver_mask.shape
        for array in (solver_phase, solver_normal_x, solver_normal_y, solver_coherence)
    ):
        raise RuntimeError(f"Solver returned inconsistent array shapes for {family_id}.")
    phase, normal_x, normal_y, coherence = solver_to_north_up_grid(
        solver_phase, solver_normal_x, solver_normal_y, solver_coherence
    )

    write_cf0_rasters(
        phase_staged,
        orientation_staged,
        phase=phase,
        normal_x=normal_x,
        normal_y=normal_y,
        coherence=coherence,
        mask=mask,
        transform=transform,
        projection=terrain.projection,
    )
    raster_records = [
        raster_record(
            orientation_staged,
            orientation_published,
            candidate_id=candidate_id,
            field_id=field_id,
            work_block_id=work_block_id,
            raster_role="orientation_coherence",
        ),
        raster_record(
            phase_staged,
            phase_published,
            candidate_id=candidate_id,
            field_id=field_id,
            work_block_id=work_block_id,
            raster_role="phase",
        ),
    ]

    x_coordinates = transform[0] + (np.arange(width) + 0.5) * transform[1]
    decreasing_y = transform[3] + (np.arange(height) + 0.5) * transform[5]
    extraction_phase, extraction_mask, extraction_qa = extrapolate_phase_halo(
        solver_phase,
        solver_mask,
        policy.grid_resolution_m,
        policy.contour_extrapolation_halo_cells,
        policy.contour_gradient_lsq_max_radius_cells,
        policy.contour_gradient_lsq_max_relative_residual,
        policy.contour_gradient_lsq_max_condition_number,
    )
    contours = phase_contours(
        extraction_phase,
        extraction_mask,
        x_coordinates,
        decreasing_y[::-1],
        row_spacing_m,
        policy.minimum_row_length_m,
        level_range_mask=solver_mask,
        phase_offset_m=0.0,
    )
    metric_lines = extract_metric_lines(
        contours,
        field,
        terrain,
        profile,
        family_id,
        policy,
    )
    radius_neutral_solver_policy = replace(
        solver_policy, minimum_work_path_radius_m=None
    )
    geometry_evaluation = evaluate_family_geometry(
        metric_lines,
        radius_neutral_solver_policy,
        physical_boundary=field["usable"].boundary,
        endpoint_tolerance_m=policy.endpoint_snap_tolerance_m,
    )
    continuity = analyze_operational_continuity(
        metric_lines,
        [field],
        ContinuityPolicy(
            terminal_surface_tolerance_m=policy.endpoint_snap_tolerance_m,
            row_spacing_m=row_spacing_m,
            spacing_tolerance_fraction=policy.spacing_tolerance_fraction,
            spacing_sample_interval_m=max(row_spacing_m * 4.0, 6.0),
            spacing_endpoint_trim_m=max(row_spacing_m * 2.0, 3.0),
            required_minimum_radius_m=None,
        ),
    )
    group_key = (candidate_id, field_id)
    continuity_summary = continuity["group_summaries"].get(group_key, {})
    solver_qa = result["qa"]
    grid_metrics = phase_grid_metrics(
        solver_phase, solver_mask, policy.grid_resolution_m, row_spacing_m
    )
    integrability_p95 = _qa_value(
        solver_qa, "integrability.relative_residual_p95", "integrability.residual_p95"
    )
    orientation_p95 = _qa_value(
        solver_qa,
        "integrability.angular_error_p95_deg",
        "orientation.misalignment_p95_deg",
    )
    final_spacing = final_normal_ray_spacing(
        metric_lines, row_spacing_m, policy.spacing_tolerance_fraction
    )
    spacing_error_p95 = rounded(final_spacing["spacing_error_p95_m"], 10)
    eikonal_p95 = _qa_value(
        solver_qa, "integrability.eikonal_residual_p95"
    )
    if eikonal_p95 is None:
        eikonal_p95 = grid_metrics["eikonal_p95"]
    total_diagnostic_length = float(
        sum(item["metrics"]["length_m"] for item in metric_lines)
    )
    coverage_ratio = total_diagnostic_length * row_spacing_m / max(field["usable"].area, 1e-9)
    intersection_count = int(
        geometry_evaluation["crossing_or_overlap_pair_count"]
        + geometry_evaluation["touching_pair_count"]
    )
    internal_endpoints = int(geometry_evaluation["unsupported_endpoint_count"])
    self_intersections = int(geometry_evaluation["self_intersection_count"])
    loops = int(sum(item["geometry"].is_ring for item in metric_lines))
    spline_failures = [
        item for item in metric_lines if item["spline_qa"].get("blocker_code") is not None
    ]
    endpoint_failures = [
        item for item in metric_lines if not item["endpoints_extended_to_boundary"]
    ]
    raw_spline_radii = [
        item["spline_qa"].get("minimum_radius_m")
        for item in metric_lines
        if item["spline_qa"].get("minimum_radius_m") is not None
    ]
    finite_spline_radii = [
        float(value) for value in raw_spline_radii if math.isfinite(float(value))
    ]
    minimum_radius_observed = (
        min(finite_spline_radii)
        if finite_spline_radii
        else math.inf if raw_spline_radii and not spline_failures else None
    )
    if minimum_radius_m is None:
        radius_status = "NOT_EVALUATED"
    elif spline_failures or minimum_radius_observed is None:
        radius_status = "FAIL"
    elif minimum_radius_observed + 1e-9 < minimum_radius_m:
        radius_status = "FAIL"
    else:
        radius_status = "PASS"

    blockers: list[str] = []
    for code in solver_qa.get("blocker_codes", []):
        if code not in blockers:
            blockers.append(code)
    for code in geometry_evaluation.get("blocker_codes", []):
        if code not in blockers:
            blockers.append(code)
    continuity_codes = continuity_summary.get("continuity_blocker_codes", "")
    for code in filter(None, str(continuity_codes).split(",")):
        if code not in blockers:
            blockers.append(code)
    if extraction_qa.get("gradient_estimation_status") != "PASS":
        blockers.append("PHASE_HALO_GRADIENT_UNESTIMABLE")
    if not metric_lines:
        blockers.append("NO_EXTRACTED_ROWS")
    if endpoint_failures:
        blockers.append("INTERNAL_UNSUPPORTED_ENDPOINT")
    if spline_failures:
        blockers.append("ROW_SPLINE_FIT_FAILED")
    if minimum_radius_m is not None and radius_status == "FAIL":
        blockers.append("MINIMUM_WORK_PATH_RADIUS_VIOLATION")
    spacing_tolerance_m = row_spacing_m * policy.spacing_tolerance_fraction
    if spacing_error_p95 is None or spacing_error_p95 > spacing_tolerance_m + 1e-9:
        blockers.append("SPACING_P95_OUTSIDE_CF0_TOLERANCE")
    outside_spacing_percent = rounded(final_spacing["outside_tolerance_percent"], 10)
    maximum_spacing_outside_percent = (
        100.0 * policy.maximum_spacing_outside_tolerance_fraction
    )
    if (
        outside_spacing_percent is None
        or outside_spacing_percent > maximum_spacing_outside_percent + 1e-9
    ):
        blockers.append("SPACING_OUTSIDE_TOLERANCE_AREA_EXCESS")
    solver_spacing = solver_qa.get("normal_spacing", {})
    solver_spacing_p05 = rounded(solver_spacing.get("spacing_p05_m"), 10)
    solver_spacing_p95 = rounded(solver_spacing.get("spacing_p95_m"), 10)
    solver_spacing_error = (
        max(abs(solver_spacing_p05 - row_spacing_m), abs(solver_spacing_p95 - row_spacing_m))
        if solver_spacing_p05 is not None and solver_spacing_p95 is not None
        else None
    )
    if solver_spacing_error is None or solver_spacing_error > spacing_tolerance_m + 1e-9:
        blockers.append("SOLVER_NORMAL_SPACING_OUTSIDE_CF0_TOLERANCE")
    solver_spacing_outside = rounded(solver_spacing.get("outside_tolerance_fraction"), 10)
    if (
        solver_spacing_outside is None
        or solver_spacing_outside
        > policy.maximum_spacing_outside_tolerance_fraction + 1e-9
    ):
        blockers.append("SOLVER_NORMAL_SPACING_AREA_EXCESS")
    if eikonal_p95 is None or eikonal_p95 > policy.eikonal_residual_tolerance + 1e-9:
        blockers.append("EIKONAL_P95_OUTSIDE_CF0_TOLERANCE")
    if integrability_p95 is None or integrability_p95 > policy.integrability_residual_tolerance + 1e-9:
        blockers.append("INTEGRABILITY_P95_OUTSIDE_CF0_TOLERANCE")
    if orientation_p95 is None or orientation_p95 > policy.maximum_frustration_deg + 1e-9:
        blockers.append("ORIENTATION_P95_OUTSIDE_CF0_TOLERANCE")
    if not policy.coverage_proxy_minimum <= coverage_ratio <= policy.coverage_proxy_maximum:
        blockers.append("COVERAGE_RATIO_OUTSIDE_CF0_ENVELOPE")
    blockers = sorted(set(blockers))
    geometric_status = "GEOMETRIC_PASS" if not blockers else "NO_FEASIBLE_FAMILY"

    solver_gate_metrics = {
        "solve_mask_component_count": solver_qa.get("domain", {}).get(
            "solve_mask_component_count"
        ),
        "low_coherence_fraction": rounded(
            solver_qa.get("orientation", {}).get("combined_low_coherence_fraction"), 10
        ),
        "abrupt_edge_fraction": rounded(
            solver_qa.get("axial_lift", {}).get("abrupt_edge_fraction"), 10
        ),
        "cycle_conflict_fraction": rounded(
            solver_qa.get("axial_lift", {}).get("cycle_conflict_fraction"), 10
        ),
        "integrability_residual_rms": rounded(
            solver_qa.get("integrability", {}).get("integrability_residual_rms"), 10
        ),
        "critical_fraction": rounded(
            solver_qa.get("critical_indicators", {}).get("critical_fraction"), 10
        ),
        "singular_fraction": rounded(
            solver_qa.get("critical_indicators", {}).get("singular_fraction"), 10
        ),
        "cut_locus_fraction": rounded(
            solver_qa.get("critical_indicators", {}).get("cut_locus_fraction"), 10
        ),
        "solver_spacing_p05_m": solver_spacing_p05,
        "solver_spacing_p95_m": solver_spacing_p95,
        "solver_spacing_outside_tolerance_fraction": solver_spacing_outside,
        "final_spacing_outside_tolerance_fraction": (
            rounded(outside_spacing_percent / 100.0, 10)
            if outside_spacing_percent is not None
            else None
        ),
        "spline_failure_count": len(spline_failures),
    }
    metrics = {
        "spacing_error_p95_m": rounded(spacing_error_p95, 8),
        "eikonal_residual_p95": rounded(eikonal_p95, 8),
        "integrability_residual_p95": rounded(integrability_p95, 8),
        "orientation_misalignment_p95_deg": rounded(orientation_p95, 8),
        "minimum_radius_m": rounded(minimum_radius_observed, 8),
        "radius_status": radius_status,
        "maximum_abs_grade_pct": rounded(
            max(
                (item["metrics"]["absolute_grade_max_percent"] for item in metric_lines),
                default=math.nan,
            ),
            8,
        ),
        "internal_endpoint_count": internal_endpoints,
        "intersection_count": intersection_count,
        "self_intersection_count": self_intersections,
        "loop_count": loops,
        "coverage_proxy_ratio": rounded(coverage_ratio, 8),
        "solver_gate_metrics": solver_gate_metrics,
    }
    invariant_blockers = phase_offset_invariant_blockers(
        solver_qa,
        extraction_qa,
        solver_spacing_error_m=solver_spacing_error,
        solver_spacing_outside_fraction=solver_spacing_outside,
        eikonal_p95=eikonal_p95,
        integrability_p95=integrability_p95,
        orientation_p95_deg=orientation_p95,
        row_spacing_m=row_spacing_m,
        policy=policy,
    )
    diagnostic_attempt = {
        "phase_offset_fraction": 0.0,
        "phase_offset_m": 0.0,
        "geometric_status": geometric_status,
        "blocker_codes": blockers,
        "metric_lines": metric_lines,
        "continuity": continuity,
        "metrics": metrics,
        "total_length_m": total_diagnostic_length,
        "minimum_radius_order_value": minimum_radius_observed,
        "extraction_qa": extraction_qa,
    }
    offset_attempts = complete_phase_offset_attempts(
        diagnostic_attempt,
        policy=policy,
        row_spacing_m=row_spacing_m,
        minimum_radius_m=minimum_radius_m,
        invariant_blockers=invariant_blockers,
        extraction_qa=extraction_qa,
        evaluator=lambda phase_offset_fraction_value: evaluate_phase_offset_attempt(
            request,
            field,
            terrain,
            profile,
            policy,
            row_spacing_m,
            minimum_radius_m,
            family_id,
            solver_policy,
            result,
            solver_phase,
            solver_mask,
            extraction_phase,
            extraction_mask,
            extraction_qa,
            x_coordinates,
            decreasing_y[::-1],
            phase_offset_fraction_value,
        ),
    )
    selected_attempt, phase_offset_selection = select_phase_offset_attempt(
        offset_attempts, policy
    )
    phase_offset_fraction = selected_attempt["phase_offset_fraction"]
    phase_offset_m = selected_attempt["phase_offset_m"]
    phase_offset_role = phase_offset_selection["phase_offset_role"]
    metric_lines = selected_attempt["metric_lines"]
    continuity = selected_attempt["continuity"]
    metrics = selected_attempt["metrics"]
    blockers = selected_attempt["blocker_codes"]
    geometric_status = selected_attempt["geometric_status"]
    total_diagnostic_length = selected_attempt["total_length_m"]
    spacing_error_p95 = metrics["spacing_error_p95_m"]
    eikonal_p95 = metrics["eikonal_residual_p95"]
    integrability_p95 = metrics["integrability_residual_p95"]
    orientation_p95 = metrics["orientation_misalignment_p95_deg"]
    minimum_radius_observed = metrics["minimum_radius_m"]
    radius_status = metrics["radius_status"]
    internal_endpoints = metrics["internal_endpoint_count"]
    intersection_count = metrics["intersection_count"]
    self_intersections = metrics["self_intersection_count"]
    loops = metrics["loop_count"]
    published_count = len(metric_lines) if geometric_status == "GEOMETRIC_PASS" else 0
    published_length = total_diagnostic_length if geometric_status == "GEOMETRIC_PASS" else 0.0
    diagnostic_count = len(metric_lines) if geometric_status == "NO_FEASIBLE_FAMILY" else 0
    diagnostic_length = (
        total_diagnostic_length if geometric_status == "NO_FEASIBLE_FAMILY" else 0.0
    )
    gauge_qa = result["qa"].get("integrability", {}).get("phase_gauge", {})
    phase_gauge = {
        "method": gauge_qa.get(
            "method", "ZERO_AT_LEXICOGRAPHIC_FIRST_VALID_CELL_PER_COMPONENT"
        ),
        "anchor_phase_value_m": gauge_qa.get("anchor_phase_value_m", 0.0),
        "anchors": [],
    }
    for anchor in gauge_qa.get("anchor_grid_indices", []):
        anchor_row = int(anchor["row"])
        anchor_column = int(anchor["column"])
        phase_gauge["anchors"].append(
            {
                "solver_row": anchor_row,
                "solver_column": anchor_column,
                "x_m": rounded(x_coordinates[anchor_column], 8),
                "y_m": rounded(decreasing_y[::-1][anchor_row], 8),
            }
        )
    phase_offset_selection.update(
        {
            "row_spacing_m": row_spacing_m,
            "level_equation": (
                "phase_level_m = phase_level_index * row_spacing_m + phase_offset_m"
            ),
            "phase_level_index_scope": "CANDIDATE_WORK_BLOCK_PHASE_LEVEL",
            "row_index_definition": (
                "UNIQUE_NONNEGATIVE_OPERATIONAL_SEQUENCE_PER_CANDIDATE_WORK_BLOCK"
            ),
            "phase_gauge": phase_gauge,
        }
    )
    candidate = {
        "candidate_id": candidate_id,
        "field_id": field_id,
        "work_block_id": work_block_id,
        "family_id": family_id,
        "phase_offset_m": rounded(phase_offset_m, 8),
        "phase_offset_fraction": phase_offset_fraction,
        "phase_offset_role": phase_offset_role,
        "phase_offset_selection": phase_offset_selection,
        "contour_extrapolation_qa": extraction_qa,
        "geometric_status": geometric_status,
        "hydraulic_status": HYDRAULIC_STATUS,
        "row_count": published_count,
        "total_length_m": rounded(published_length, 8) or 0.0,
        "diagnostic_row_count": diagnostic_count,
        "diagnostic_total_length_m": rounded(diagnostic_length, 8) or 0.0,
        "metrics": metrics,
        "blocker_codes": blockers,
    }
    summary = {
        "candidate_id": candidate_id,
        "field_id": field_id,
        "work_block_id": work_block_id,
        "family_id": family_id,
        "phase_offset_m": rounded(phase_offset_m, 8),
        "phase_offset_fraction": phase_offset_fraction,
        "phase_offset_role": phase_offset_role,
        "phase_offset_selection_status": phase_offset_selection[
            "selection_status"
        ],
        "geometry_status": geometric_status,
        "hydraulic_status": HYDRAULIC_STATUS,
        "row_count": published_count,
        "total_length_m": rounded(published_length, 8) or 0.0,
        "diagnostic_row_count": diagnostic_count,
        "diagnostic_total_length_m": rounded(diagnostic_length, 8) or 0.0,
        "spacing_p95_m": rounded(spacing_error_p95, 8),
        "eikonal_p95": rounded(eikonal_p95, 8),
        "integrability_p95": rounded(integrability_p95, 8),
        "orientation_p95_deg": rounded(orientation_p95, 8),
        "min_radius_m": rounded(minimum_radius_observed, 8),
        "radius_status": radius_status,
        "max_grade_pct": metrics["maximum_abs_grade_pct"],
        "internal_endpoints": internal_endpoints,
        "intersections": intersection_count,
        "self_intersections": self_intersections,
        "loops": loops,
        "blocker_codes": blockers,
    }
    hydraulic = build_hydraulic_record(
        request,
        candidate_id=candidate_id,
        field_id=field_id,
        work_block_id=work_block_id,
        family_id=family_id,
        metric_lines=metric_lines,
    )

    published_rows: list[dict[str, Any]] = []
    diagnostic_rows: list[dict[str, Any]] = []
    for item in metric_lines:
        expected_phase_level = (
            item["phase_level_index"] * row_spacing_m + phase_offset_m
        )
        if abs(item["phase_level_m"] - expected_phase_level) > 1e-8:
            raise RuntimeError(
                f"Phase level/index contract failed for {item['line_id']}."
            )
        diagnostic = continuity["line_diagnostics"][item["line_id"]]
        geometry_3d = line_with_z(
            item["geometry"], terrain, policy.curvature_sample_step_m
        )
        coordinates_3d = np.asarray(geometry_3d.coords, dtype=float)
        if (
            geometry_3d.is_empty
            or not geometry_3d.is_valid
            or abs(geometry_3d.length - item["geometry"].length) > 1e-6
            or not np.isfinite(coordinates_3d).all()
        ):
            raise RuntimeError(
                f"Diagnostic 3D geometry changed or invalidated {item['line_id']}."
            )
        row_payload = {
            "row_id": item["line_id"],
            "family_id": family_id,
            "candidate_id": candidate_id,
            "field_id": field_id,
            "work_block_id": work_block_id,
            "row_index": item["row_index"],
            "phase_level_index": item["phase_level_index"],
            "phase_level_m": rounded(item["phase_level_m"], 8),
            "phase_offset_m": rounded(phase_offset_m, 8),
            "phase_offset_fraction": phase_offset_fraction,
            "length_m": rounded(item["metrics"]["length_m"], 8),
            "min_radius_m": rounded(item["spline_qa"].get("minimum_radius_m"), 8),
            "max_abs_grade_pct": rounded(
                item["metrics"]["absolute_grade_max_percent"], 8
            ),
            "grade_p95_pct": rounded(
                item["metrics"]["absolute_grade_p95_percent"], 8
            ),
            "reversal_count": int(item["metrics"]["reversal_count"]),
            "start_surface": diagnostic["start"]["surface_type"],
            "end_surface": diagnostic["end"]["surface_type"],
            "geometry_status": geometric_status,
            "hydraulic_status": HYDRAULIC_STATUS,
            "topology_status": diagnostic["topology_status"],
            "geometry": geometry_3d,
        }
        if geometric_status == "GEOMETRIC_PASS":
            if not geometry_3d.is_simple or diagnostic["blocker_codes"]:
                raise RuntimeError(
                    f"Approved 3D geometry retained blockers for {item['line_id']}."
                )
            published_rows.append(
                {
                    **row_payload,
                    "blocker_codes": [],
                }
            )
        else:
            row_blockers = set(blockers)
            row_blockers.update(diagnostic["blocker_codes"])
            spline_blocker = item["spline_qa"].get("blocker_code")
            if spline_blocker:
                row_blockers.add(str(spline_blocker))
            diagnostic_rows.append(
                {
                    **row_payload,
                    "diagnostic_status": "NOT_APPROVED",
                    "guidance_status": "NOT_AUTHORIZED",
                    "blocker_codes": sorted(row_blockers),
                }
            )
    return (
        candidate,
        summary,
        published_rows,
        diagnostic_rows,
        hydraulic,
        raster_records,
    )


def spatial_reference_manifest(projection: str) -> dict[str, Any]:
    spatial_ref = osr.SpatialReference()
    if spatial_ref.ImportFromWkt(projection) != ogr.OGRERR_NONE:
        raise RuntimeError("Generated CF0 projection WKT is invalid.")
    spatial_ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    spatial_ref.AutoIdentifyEPSG()
    authority = spatial_ref.GetAuthorityName(None)
    code = spatial_ref.GetAuthorityCode(None)
    if authority != "EPSG" or code is None:
        raise RuntimeError("CF0 requires a CRS with an identifiable EPSG code.")
    unit_name = str(spatial_ref.GetLinearUnitsName() or "").lower()
    if not unit_name.startswith("met"):
        raise RuntimeError(f"CF0 requires metre units; found {unit_name!r}.")
    if unit_name.startswith("meter"):
        normalized_unit = "meter"
    elif unit_name == "metre (international)":
        normalized_unit = unit_name
    else:
        normalized_unit = "metre"
    return {
        "authority": "EPSG",
        "code": int(code),
        "projected": True,
        "linear_unit": normalized_unit,
        "wkt_sha256": hashlib.sha256(projection.encode("utf-8")).hexdigest(),
    }


def validate_staged_geopackage(
    path: Path,
    expected_counts: dict[str, int],
) -> dict[str, int]:
    source = ogr.Open(str(path))
    if source is None:
        raise RuntimeError(f"Could not reopen staged CF0 GeoPackage: {path}")
    names = {source.GetLayerByIndex(index).GetName() for index in range(source.GetLayerCount())}
    if names != set(OGR_FIELDS):
        raise RuntimeError(f"Unexpected staged CF0 layers: {sorted(names)}")
    counts = {
        layer_name: source.GetLayerByName(layer_name).GetFeatureCount()
        for layer_name in OGR_FIELDS
    }
    if counts != expected_counts:
        raise RuntimeError(f"Staged CF0 layer counts disagree: {counts} != {expected_counts}")
    invalid = non_3d = nonfinite = 0
    for layer_name in ("continuous_rows", "diagnostic_rows"):
        layer = source.GetLayerByName(layer_name)
        if ogr.GT_HasZ(layer.GetGeomType()) != 1:
            raise RuntimeError(f"{layer_name} does not declare a Z dimension.")
        for feature in layer:
            geometry = feature.GetGeometryRef()
            if geometry is None or geometry.IsEmpty() or not geometry.IsValid():
                invalid += 1
                continue
            if geometry.GetCoordinateDimension() < 3:
                non_3d += 1
            coordinates = [
                geometry.GetPoint(index) for index in range(geometry.GetPointCount())
            ]
            nonfinite += sum(
                not all(math.isfinite(float(value)) for value in coordinate[:3])
                for coordinate in coordinates
            )
    source = None
    if invalid or non_3d or nonfinite:
        raise RuntimeError(
            "Staged CF0 GeoPackage failed XYZ QA: "
            f"invalid={invalid}, non_3d={non_3d}, nonfinite={nonfinite}."
        )
    return {
        "invalid_geometry_count": invalid,
        "non_3d_geometry_count": non_3d,
        "nonfinite_coordinate_count": nonfinite,
    }


def validate_staged_rasters(staged_raster_dir: Path, records: list[dict[str, Any]]) -> int:
    invalid_count = 0
    for record in records:
        path = staged_raster_dir / Path(record["path"]).name
        dataset = gdal.Open(str(path))
        if dataset is None:
            invalid_count += 1
            continue
        expected_bands = 2 if record["raster_role"] == "orientation_coherence" else 1
        valid = dataset.RasterCount == expected_bands
        arrays = []
        for band_index in range(1, dataset.RasterCount + 1):
            band = dataset.GetRasterBand(band_index)
            array = band.ReadAsArray().astype(float)
            nodata = band.GetNoDataValue()
            mask = np.isfinite(array)
            if nodata is not None:
                mask &= ~np.isclose(array, nodata)
            valid &= bool(np.any(mask))
            arrays.append((array, mask))
        if record["raster_role"] == "orientation_coherence" and len(arrays) == 2:
            angle, angle_mask = arrays[0]
            coherence, coherence_mask = arrays[1]
            valid &= bool(np.all((angle[angle_mask] >= 0.0) & (angle[angle_mask] < 180.0)))
            valid &= bool(
                np.all(
                    (coherence[coherence_mask] >= -1e-6)
                    & (coherence[coherence_mask] <= 1.0 + 1e-6)
                )
            )
        invalid_count += int(not valid)
        dataset = None
    if invalid_count:
        raise RuntimeError(f"{invalid_count} staged CF0 rasters failed QA.")
    return invalid_count


def release_limitations(
    request: ResolvedProjectRequest,
    minimum_radius_m: float | None,
    operational_surfaces_status: str,
) -> list[str]:
    limitations = [
        "CF0_NOT_FOR_GUIDANCE",
        "HYDRAULIC_UNCONFIRMED",
        "FIELD_VALIDATION_REQUIRED",
    ]
    if minimum_radius_m is None:
        limitations.append("RADIUS_REQUIREMENT_NOT_PROVIDED")
    if request.power_inventory_status == "NOT_REVIEWED":
        limitations.append("POWER_INVENTORY_NOT_REVIEWED")
    if operational_surfaces_status != "PROVIDED":
        limitations.append("OPERATIONAL_SURFACES_INCOMPLETE")
    return limitations


def operational_surfaces_status(request: ResolvedProjectRequest) -> str:
    if request.value("constraints.operational_surfaces_dataset_ref"):
        return "PROVIDED"
    inventory = request.value("constraints.inventory_review_status", "NOT_REVIEWED")
    return "NOT_REVIEWED" if inventory == "NOT_REVIEWED" else "PARTIAL"


def build_manifest(
    *,
    request: ResolvedProjectRequest,
    dtm_resolution: dict[str, Any],
    boundary_resolution: dict[str, Any],
    boundary_metadata: dict[str, Any],
    fields: list[dict[str, Any]],
    work_blocks: list[dict[str, Any]],
    excluded_components: list[dict[str, Any]],
    work_block_filter: dict[str, Any],
    projection: str,
    policy: Cf0Policy,
    row_spacing_m: float,
    minimum_radius_m: float | None,
    candidates: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    diagnostic_rows: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
    hydraulic: list[dict[str, Any]],
    raster_records: list[dict[str, Any]],
    staged_gpkg: Path,
    staged_map: Path,
    published_gpkg: Path,
    published_map: Path,
    geometric_qa: dict[str, int],
    raster_invalid_count: int,
) -> dict[str, Any]:
    operation_status = operational_surfaces_status(request)
    power_status = request.power_inventory_status
    if power_status == "PROVIDED":
        power_application = "APPLIED"
    elif power_status == "DECLARED_NONE":
        power_application = "NOT_APPLICABLE_DECLARED_NONE"
    else:
        power_application = "NOT_APPLIED_NOT_REVIEWED"
    candidate_blockers = sorted(
        {code for candidate in candidates for code in candidate["blocker_codes"]}
    )
    pass_count = sum(
        candidate["geometric_status"] == "GEOMETRIC_PASS" for candidate in candidates
    )
    request_sha256 = sha256_file(request.request_path)
    endpoint_extension_limit = policy.grid_resolution_m * policy.endpoint_extension_factor
    manifest = {
        "schema_version": "1.2.0",
        "manifest_type": "CONTINUOUS_FAMILY_STAGE_RESULT",
        "release": "CF0_GEOMETRIC_SCREENING",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "project_request_ref": {
            "id": request.request["request_id"],
            "sha256": request_sha256,
        },
        "stage_status": (
            "HYDRAULIC_UNCONFIRMED" if pass_count else "NO_FEASIBLE_FAMILY"
        ),
        "crs": spatial_reference_manifest(projection),
        "inputs": {
            "work_area": input_dataset_record(
                boundary_resolution,
                layer=boundary_metadata["layer_name"],
                role="FIELD_BOUNDARY",
                id_field=boundary_metadata["selected_field_id_column"],
                field_ids=[str(field["code"]) for field in fields],
            ),
            "terrain_dtm": input_dataset_record(dtm_resolution, role="DTM"),
            "project_request": file_record(
                request.request_path,
                published_path=request.request_path,
                role="PROJECT_GENERATION_REQUEST",
            ),
        },
        "constraints": {
            "power_inventory_status": power_status,
            "power_barrier_application": power_application,
            "operational_surfaces_status": operation_status,
            "absolute_barrier_count": 0,
        },
        "release_limitations": release_limitations(
            request, minimum_radius_m, operation_status
        ),
        "domain_assembly": {
            "status": "CONNECTED_COMPONENTS_PER_FIELD",
            "cross_field_requested": False,
            "cadastral_boundaries_used_as_work_edges": True,
            "geometry_method": "BUFFERED_FIELD_BOUNDARY_V1",
            "outer_headland_m": e0.PARAMS.outer_headland_m,
            "obstacle_clearance_m": e0.PARAMS.obstacle_clearance_m,
            "work_block_filter": work_block_filter,
            "work_blocks": [
                {
                    "work_block_id": str(work_block["work_block_id"]),
                    "field_ids": [str(work_block["code"])],
                    "component_index": int(work_block["component_index"]),
                    "usable_area_m2": rounded(work_block["usable"].area, 6),
                }
                for work_block in work_blocks
            ],
            "excluded_components": excluded_components,
            "blocker_codes": [],
        },
        "solver_parameters": {
            "solver_revision": policy.revision,
            "grid_resolution_m": policy.grid_resolution_m,
            "row_spacing_m": row_spacing_m,
            "minimum_work_path_radius_m": minimum_radius_m,
            "radius_requirement_status": (
                "DECLARED" if minimum_radius_m is not None else "NOT_PROVIDED"
            ),
            "orientation": {
                "representation": "AXIAL_DOUBLE_ANGLE",
                "smoothing_radius_m": policy.smoothing_radius_m,
                "minimum_coherence": policy.minimum_coherence,
                "maximum_low_coherence_fraction": policy.maximum_low_coherence_fraction,
                "maximum_frustration_deg": policy.maximum_frustration_deg,
                "maximum_abrupt_edge_fraction": policy.maximum_abrupt_edge_fraction,
                "maximum_cycle_conflict_fraction": policy.maximum_cycle_conflict_fraction,
                "minimum_terrain_gradient": policy.minimum_terrain_gradient,
                "gradient_floor": policy.gradient_floor,
            },
            "phase": {
                "method": "ALTERNATING_POSITIVE_SCALE_PROJECTION",
                "gauge_method": (
                    "ZERO_AT_LEXICOGRAPHIC_FIRST_VALID_CELL_PER_COMPONENT"
                ),
                "gauge_anchor_value_m": 0.0,
                "eikonal_role": "RESIDUAL_GATE_ONLY",
                "seed_method": "POISSON_PROJECTION",
                "target_gradient_norm": 1,
                "eikonal_residual_tolerance": policy.eikonal_residual_tolerance,
                "integrability_residual_tolerance": policy.integrability_residual_tolerance,
                "maximum_iterations": policy.maximum_iterations,
                "convergence_tolerance": policy.convergence_tolerance,
                "regularization_weight": policy.regularization_weight,
                "boundary_weight": policy.boundary_weight,
                "phase_scale_min": policy.phase_scale_min,
                "phase_scale_max": policy.phase_scale_max,
                "phase_scale_relaxation": policy.phase_scale_relaxation,
                "lsqr_tolerance": policy.lsqr_tolerance,
                "lsqr_iteration_limit": policy.lsqr_iteration_limit,
                "maximum_integrability_residual_rms": policy.integrability_residual_tolerance,
                "maximum_integrability_angular_error_p95_deg": policy.maximum_frustration_deg,
                "maximum_eikonal_residual_p95": policy.eikonal_residual_tolerance,
                "maximum_critical_fraction": policy.maximum_critical_fraction,
                "cut_locus_laplacian_threshold": policy.cut_locus_laplacian_threshold,
                "maximum_cut_locus_fraction": policy.maximum_cut_locus_fraction,
                "solve_mask_required_component_count": (
                    policy.solve_mask_required_component_count
                ),
                "solve_mask_component_connectivity": (
                    policy.solve_mask_component_connectivity
                ),
            },
            "extraction": {
                "level_interval_m": row_spacing_m,
                "phase_level_equation": (
                    "phase_level_m = phase_level_index * row_spacing_m + phase_offset_m"
                ),
                "phase_level_index_scope": "CANDIDATE_WORK_BLOCK_PHASE_LEVEL",
                "row_index_definition": (
                    "UNIQUE_NONNEGATIVE_OPERATIONAL_SEQUENCE_PER_CANDIDATE_WORK_BLOCK"
                ),
                "phase_offset_search_revision": policy.phase_offset_search_revision,
                "phase_offset_fractions": list(policy.phase_offset_fractions),
                "phase_offset_selection_rule": policy.phase_offset_selection_rule,
                "phase_offset_search_scope": (
                    "DISCRETE_CONFIGURED_OFFSETS_NOT_CONTINUOUS_GAUGE_INVARIANCE"
                ),
                "minimum_row_length_m": policy.minimum_row_length_m,
                "endpoint_tolerance_m": policy.endpoint_snap_tolerance_m,
                "spacing_tolerance_fraction": policy.spacing_tolerance_fraction,
                "spacing_tolerance_m": row_spacing_m * policy.spacing_tolerance_fraction,
                "maximum_spacing_outside_tolerance_fraction": policy.maximum_spacing_outside_tolerance_fraction,
                "coverage_proxy_minimum": policy.coverage_proxy_minimum,
                "coverage_proxy_maximum": policy.coverage_proxy_maximum,
                "curvature_sample_step_m": policy.curvature_sample_step_m,
                "spline_method": "PARAMETRIC_BSPLINE_C2",
                "spline_max_deviation_m": policy.spline_max_deviation_m,
                "spline_representation_method": "ADAPTIVE_CHORD_ERROR_PRESERVE_VERTICES",
                "spline_representation_tolerance_fraction": policy.spline_representation_tolerance_fraction,
                "endpoint_snap_tolerance_m": policy.endpoint_snap_tolerance_m,
                "endpoint_extension_limit_m": endpoint_extension_limit,
                "endpoint_extension_factor": policy.endpoint_extension_factor,
                "solve_mask_rasterization": "ALL_TOUCHED_SUPERCOVER",
                "contour_extrapolation_method": (
                    "FIRST_ORDER_LOCAL_LSQ_GRADIENT_FAIL_CLOSED"
                ),
                "contour_extrapolation_halo_cells": policy.contour_extrapolation_halo_cells,
                "contour_gradient_lsq_max_radius_cells": (
                    policy.contour_gradient_lsq_max_radius_cells
                ),
                "contour_gradient_lsq_max_relative_residual": (
                    policy.contour_gradient_lsq_max_relative_residual
                ),
                "contour_gradient_lsq_max_condition_number": (
                    policy.contour_gradient_lsq_max_condition_number
                ),
                "contour_gradient_lsq_minimum_neighbor_count": 3,
                "contour_gradient_lsq_required_rank": 2,
                "contour_gradient_component_connectivity": 4,
                "endpoint_extension_mode": "TANGENT_ONLY_FAIL_CLOSED",
            },
            "candidate_profiles": [dict(profile) for profile in CANDIDATE_PROFILES],
        },
        "candidates": candidates,
        "qa": {
            "candidate_field_count": len(candidates),
            "geometric_pass_count": pass_count,
            "no_feasible_family_count": len(candidates) - pass_count,
            "hydraulic_unconfirmed_count": len(candidates),
            "radius_not_evaluated_count": sum(
                candidate["metrics"]["radius_status"] == "NOT_EVALUATED"
                for candidate in candidates
            ),
            **geometric_qa,
            "internal_endpoint_count": sum(
                candidate["metrics"]["internal_endpoint_count"] for candidate in candidates
            ),
            "intersection_count": sum(
                candidate["metrics"]["intersection_count"] for candidate in candidates
            ),
            "raster_invalid_count": raster_invalid_count,
            "gate_failures": candidate_blockers,
        },
        "layer_counts": {
            "continuous_rows": len(rows),
            "diagnostic_rows": len(diagnostic_rows),
            "family_summary": len(summaries),
            "hydraulic_precheck": len(hydraulic),
        },
        "outputs": {
            "geopackage": file_record(
                staged_gpkg, published_path=published_gpkg, role="CF0_VECTOR_PACKAGE"
            ),
            "map": file_record(
                staged_map, published_path=published_map, role="CF0_COMPARATIVE_MAP"
            ),
            "rasters": raster_records,
        },
    }
    return manifest


def publish_outputs(
    *,
    staged_gpkg: Path,
    staged_map: Path,
    staged_manifest: Path,
    staged_raster_dir: Path,
    output_gpkg: Path,
    output_map: Path,
    output_manifest: Path,
    output_raster_dir: Path,
) -> None:
    for path in (output_gpkg, output_map, output_manifest):
        path.parent.mkdir(parents=True, exist_ok=True)
    output_raster_dir.mkdir(parents=True, exist_ok=True)
    os.replace(staged_gpkg, output_gpkg)
    os.replace(staged_map, output_map)
    staged_rasters = sorted(staged_raster_dir.glob("*.tif"))
    published_raster_names = {path.name for path in staged_rasters}
    for staged_raster in staged_rasters:
        os.replace(staged_raster, output_raster_dir / staged_raster.name)
    # This directory is a CF0-managed bundle.  Remove only prior CF0 products
    # that are no longer declared by this run (for example, an excluded sliver),
    # leaving unrelated rasters untouched.
    managed_patterns = ("cf0*__phase.tif", "cf0*__orientation_coherence.tif")
    for pattern in managed_patterns:
        for previous_raster in output_raster_dir.glob(pattern):
            if previous_raster.name not in published_raster_names:
                previous_raster.unlink()
    # The manifest is the transaction marker and is always published last.
    os.replace(staged_manifest, output_manifest)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, default=DEFAULT_REQUEST)
    parser.add_argument("--field-id-column")
    parser.add_argument("--grid-resolution-m", type=float)
    parser.add_argument("--output-gpkg", type=Path, default=DEFAULT_GPKG)
    parser.add_argument("--output-manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--output-raster-dir", type=Path, default=DEFAULT_RASTER_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.grid_resolution_m is not None and (
            not math.isfinite(args.grid_resolution_m) or args.grid_resolution_m <= 0
        ):
            raise ContractError("--grid-resolution-m must be finite and positive.")
        request = load_project_request(args.request)
        if request.power_inventory_status == "PROVIDED":
            raise ContractError(
                "POWER_BARRIER_STAGE_REQUIRED: supplied power axes must be buffered and applied "
                "before CF0 can assemble a work domain."
            )
        if request.value("constraints.operational_surfaces_dataset_ref"):
            raise ContractError(
                "OPERATIONAL_SURFACE_STAGE_REQUIRED: the supplied operational surfaces are "
                "not consumed by CF0 V1 and cannot be declared applied."
            )
        if request.request.get("constraint_layers"):
            raise ContractError(
                "CONSTRAINT_LAYER_STAGE_REQUIRED: CF0 V1 will not ignore supplied constraint "
                "geometries; classify and apply them before continuous-family generation."
            )
        cross_field_requested = bool(
            request.request["scope"].get("cross_field_generation", False)
            or request.value("scope.allow_cross_field_work", False)
        )
        if cross_field_requested:
            raise ContractError(
                "CROSS_FIELD_DOMAIN_REQUIRES_REVIEWED_OPERATIONAL_SURFACES: V1 will not turn "
                "a cadastral boundary into a row endpoint or dissolve it without physical-edge review."
            )
        row_spacing_m = float(request.value("agronomy.row_spacing_m"))
        grid_resolution_m = (
            float(args.grid_resolution_m)
            if args.grid_resolution_m is not None
            else min(Cf0Policy.grid_resolution_m, row_spacing_m)
        )
        if grid_resolution_m > row_spacing_m + 1e-9:
            raise ContractError(
                "CF0_GRID_RESOLUTION_TOO_COARSE_FOR_ROWS: grid resolution must be equal to or "
                "finer than the configured row spacing."
            )
        e0.PARAMS = replace(e0.Parameters(), **request.engine_parameter_overrides())
        policy = replace(Cf0Policy(), grid_resolution_m=grid_resolution_m)
        minimum_radius_m = request.conservative_operation_limit(
            "fleet.minimum_work_path_radius_m", "max"
        )
        dtm_path, dtm_resolution = request.dtm_path()
        boundary_path, boundary_resolution = request.field_boundary_path()
        boundary_dataset = next(
            item for item in request.request["input_datasets"] if item["role"] == "FIELD_BOUNDARY"
        )
        fields, projection, boundary_metadata = e0.read_fields(
            boundary_path=boundary_path,
            boundary_layer=boundary_dataset.get("layer_name"),
            target_field_ids=[str(item) for item in request.request["scope"]["field_ids"]],
            field_id_column=args.field_id_column or boundary_dataset.get("id_field"),
            return_metadata=True,
        )
        e0.validate_spatial_references(
            projection,
            dtm_path,
            request.value("terrain.horizontal_crs"),
        )
        terrain = e0.Terrain(dtm_path)
        terrain_resolution_m = max(terrain.resolution_x, terrain.resolution_y)
        if terrain_resolution_m > row_spacing_m + 1e-9:
            raise ContractError(
                "TERRAIN_RESOLUTION_TOO_COARSE_FOR_CF0: terrain resolution must be equal to or "
                "finer than the configured row spacing."
            )
        if grid_resolution_m + 1e-9 < terrain_resolution_m:
            raise ContractError(
                "CF0_GRID_FINER_THAN_SOURCE_TERRAIN: the solver grid cannot claim more spatial "
                "detail than the source terrain."
            )
        for field in fields:
            terrain.validate_geometry_coverage(field["usable"])
        (
            work_blocks,
            excluded_components,
            work_block_filter,
        ) = assemble_connected_work_blocks(
            fields,
            policy=policy,
            row_spacing_m=row_spacing_m,
        )
    except (ContractError, RuntimeError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"CF0_INPUT_BLOCKED: {exc}", file=sys.stderr)
        return 2

    DERIVED.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(tempfile.mkdtemp(prefix=".continuous-family-", dir=DERIVED))
    staged_gpkg = temporary_root / args.output_gpkg.name
    staged_map = temporary_root / args.output_map.name
    staged_manifest = temporary_root / args.output_manifest.name
    staged_raster_dir = temporary_root / "rasters"
    staged_raster_dir.mkdir(parents=True, exist_ok=True)
    candidates: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    diagnostic_rows: list[dict[str, Any]] = []
    hydraulic: list[dict[str, Any]] = []
    raster_records: list[dict[str, Any]] = []
    try:
        for profile in CANDIDATE_PROFILES:
            for field in work_blocks:
                candidate_started_at = time.perf_counter()
                (
                    candidate,
                    summary,
                    candidate_rows,
                    candidate_diagnostic_rows,
                    precheck,
                    rasters,
                ) = solve_candidate_field(
                    request,
                    field,
                    terrain,
                    profile,
                    policy,
                    row_spacing_m,
                    minimum_radius_m,
                    staged_raster_dir,
                    args.output_raster_dir,
                )
                candidates.append(candidate)
                summaries.append(summary)
                rows.extend(candidate_rows)
                diagnostic_rows.extend(candidate_diagnostic_rows)
                hydraulic.append(precheck)
                raster_records.extend(rasters)
                print(
                    json.dumps(
                        {
                            "event": "CF0_CANDIDATE_COMPLETE",
                            "candidate_id": candidate["candidate_id"],
                            "work_block_id": candidate["work_block_id"],
                            "geometric_status": candidate["geometric_status"],
                            "published_row_count": candidate["row_count"],
                            "diagnostic_row_count": candidate["diagnostic_row_count"],
                            "elapsed_s": round(time.perf_counter() - candidate_started_at, 3),
                        },
                        ensure_ascii=True,
                    ),
                    flush=True,
                )

        write_geopackage(
            staged_gpkg,
            projection,
            rows,
            diagnostic_rows,
            summaries,
            hydraulic,
        )
        render_map(staged_map, fields, terrain, rows, diagnostic_rows, summaries)
        layer_counts = {
            "continuous_rows": len(rows),
            "diagnostic_rows": len(diagnostic_rows),
            "family_summary": len(summaries),
            "hydraulic_precheck": len(hydraulic),
        }
        geometric_qa = validate_staged_geopackage(staged_gpkg, layer_counts)
        raster_invalid_count = validate_staged_rasters(staged_raster_dir, raster_records)
        manifest = build_manifest(
            request=request,
            dtm_resolution=dtm_resolution,
            boundary_resolution=boundary_resolution,
            boundary_metadata=boundary_metadata,
            fields=fields,
            work_blocks=work_blocks,
            excluded_components=excluded_components,
            work_block_filter=work_block_filter,
            projection=projection,
            policy=policy,
            row_spacing_m=row_spacing_m,
            minimum_radius_m=minimum_radius_m,
            candidates=candidates,
            rows=rows,
            diagnostic_rows=diagnostic_rows,
            summaries=summaries,
            hydraulic=hydraulic,
            raster_records=raster_records,
            staged_gpkg=staged_gpkg,
            staged_map=staged_map,
            published_gpkg=args.output_gpkg,
            published_map=args.output_map,
            geometric_qa=geometric_qa,
            raster_invalid_count=raster_invalid_count,
        )
        staged_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        publish_outputs(
            staged_gpkg=staged_gpkg,
            staged_map=staged_map,
            staged_manifest=staged_manifest,
            staged_raster_dir=staged_raster_dir,
            output_gpkg=args.output_gpkg,
            output_map=args.output_map,
            output_manifest=args.output_manifest,
            output_raster_dir=args.output_raster_dir,
        )
    except Exception as exc:
        print(f"CF0_GENERATION_FAILED: {exc}", file=sys.stderr)
        return 3
    finally:
        terrain.dataset = None
        shutil.rmtree(temporary_root, ignore_errors=True)

    print(
        json.dumps(
            {
                "status": manifest["stage_status"],
                "geometric_pass_count": manifest["qa"]["geometric_pass_count"],
                "candidate_field_count": manifest["qa"]["candidate_field_count"],
                "published_row_count": len(rows),
                "diagnostic_row_count": len(diagnostic_rows),
                "manifest": display_path(args.output_manifest),
                "geopackage": display_path(args.output_gpkg),
                "map": display_path(args.output_map),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
