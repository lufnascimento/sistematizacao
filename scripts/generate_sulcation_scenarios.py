"""Generate topographic screening scenarios for sugarcane planting rows.

Run with the QGIS Python environment:

    & 'C:\\Program Files\\QGIS 3.32.1\\bin\\python-qgis.bat' `
      '.\\scripts\\generate_sulcation_scenarios.py'

The script deliberately produces *screening geometry*, not machine-ready lines.
It has no soil, rainfall, surveyed road drainage, verified receiver, machine
envelope or approved regional rule pack. Reference values are planning inputs
kept in the output metadata so they cannot be mistaken for universal limits.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap
from osgeo import gdal, ogr, osr
from scipy import ndimage
from shapely import wkb
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon, box
from shapely.ops import unary_union

try:
    from project_request import (
        ContractError,
        ResolvedProjectRequest,
        legacy_metrics_manifest,
        load_project_request,
    )
except ModuleNotFoundError:  # Allows ``python -m scripts...`` from the repo root.
    from scripts.project_request import (
        ContractError,
        ResolvedProjectRequest,
        legacy_metrics_manifest,
        load_project_request,
    )

try:
    from operational_continuity import ContinuityPolicy, analyze_operational_continuity
    from sulcation_phase import adaptive_phase_levels, physical_spacing_from_levels
except ModuleNotFoundError:  # Allows ``python -m scripts...`` from the repo root.
    from scripts.operational_continuity import ContinuityPolicy, analyze_operational_continuity
    from scripts.sulcation_phase import adaptive_phase_levels, physical_spacing_from_levels


gdal.UseExceptions()
ogr.UseExceptions()

REPO = Path(__file__).resolve().parents[1]
DATASET = REPO / "dataset"
DERIVED = DATASET / "derived"
DTM = DERIVED / "dtm_1m.tif"
BOUNDARY = DATASET / "Contorno.shp"

OUTPUT_GPKG = DERIVED / "sulcation_scenarios.gpkg"
OUTPUT_JSON = DERIVED / "sulcation_scenario_metrics.json"
OUTPUT_MAP = DERIVED / "sulcation_scenarios_map.png"


@dataclass(frozen=True)
class Parameters:
    row_spacing_m: float = 1.50
    outer_headland_m: float = 12.0
    obstacle_clearance_m: float = 3.0
    minimum_segment_m: float = 8.0
    profile_sample_m: float = 3.0
    terrain_smoothing_sigma_m: float = 4.0
    candidate_angle_step_deg: int = 5
    field_lambda_values: tuple[float, ...] = (-6.0, -3.0, 0.0, 3.0, 6.0)
    reference_target_grade_percent: float = 2.0
    reference_alert_grade_percent: float = 5.0
    reversal_deadband_percent: float = 0.20
    spacing_tolerance_fraction: float = 0.20
    assumed_work_speed_kmh: float = 5.0
    assumed_turn_seconds: float = 22.0
    assumed_lift_seconds: float = 8.0


PARAMS = Parameters()


@dataclass(frozen=True)
class CandidateGatePolicy:
    allow_legacy_fallback: bool = True
    maximum_furrow_grade_percent: float | None = None
    maximum_cross_slope_percent: float | None = None

    def describe(self) -> dict:
        return {
            "singular_percent_max": 0.5,
            "spacing_outside_tolerance_percent_max": 35.0,
            "maximum_furrow_grade_percent": self.maximum_furrow_grade_percent,
            "maximum_cross_slope_percent": self.maximum_cross_slope_percent,
            "allow_legacy_fallback": self.allow_legacy_fallback,
        }


class NoFeasibleCandidateError(RuntimeError):
    code = "NO_FEASIBLE_CANDIDATE"

    def __init__(self, scope: str, policy: CandidateGatePolicy):
        self.scope = scope
        self.policy = policy
        super().__init__(f"{self.code}: no candidate passed the configured gates for {scope}.")


SCENARIOS = [
    {
        "id": "E0A_GREIDE_MIN",
        "name": "Menor greide no campo global",
        "selection": "conservation",
        "description": "Baseline que minimiza o greide longitudinal topográfico em uma única família por talhão.",
    },
    {
        "id": "E0B_GREIDE_ALVO",
        "name": "Greide-alvo no campo global",
        "selection": "controlled_grade",
        "description": "Busca o greide de estudo configurado, sem receptor hidráulico; não é greide aprovado.",
    },
    {
        "id": "E0C_EQUILIBRIO",
        "name": "Equilíbrio no campo global",
        "selection": "balanced",
        "description": "Baseline de compromisso entre greide, inclinação transversal, espaçamento e comprimento.",
    },
    {
        "id": "E0D_RETAS_LONGAS",
        "name": "Retas com tiros longos",
        "selection": "performance",
        "description": "Baseline reto que prioriza comprimento e mantém o alerta topográfico como diagnóstico.",
    },
    {
        "id": "E0E_RETAS_COLHEITA",
        "name": "Retas com menor inclinação transversal",
        "selection": "harvestability",
        "description": "Baseline reto que reduz inclinação transversal sem simular ainda o envelope da frota.",
    },
    {
        "id": "E0F_EIXO_COMUM",
        "name": "Eixo reto comum aos talhões",
        "selection": "shared_axis",
        "description": "Usa a mesma direção reta nos dois talhões; as continuidades operacional e hidráulica permanecem separadas.",
    },
]


def rounded(value: float | int | None, digits: int = 4):
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    return round(value, digits)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO))
    except ValueError:
        return str(resolved)


def file_integrity(path: Path) -> dict:
    return {
        "path": display_path(path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def percentile(values: Iterable[float], level: float) -> float | None:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    if array.size == 0:
        return None
    return float(np.percentile(array, level))


def iter_polygons(geometry):
    if isinstance(geometry, Polygon):
        yield geometry
    elif isinstance(geometry, MultiPolygon):
        yield from geometry.geoms
    elif isinstance(geometry, GeometryCollection):
        for item in geometry.geoms:
            yield from iter_polygons(item)


def iter_lines(geometry):
    if geometry.is_empty:
        return
    if isinstance(geometry, LineString):
        yield geometry
    elif isinstance(geometry, MultiLineString):
        yield from geometry.geoms
    elif isinstance(geometry, GeometryCollection):
        for item in geometry.geoms:
            yield from iter_lines(item)


def make_usable_geometry(geometry, headland_m: float, obstacle_clearance_m: float):
    parts = []
    for polygon in iter_polygons(geometry):
        outer = Polygon(polygon.exterior).buffer(-headland_m, join_style=2)
        if outer.is_empty:
            continue
        holes = [Polygon(ring).buffer(obstacle_clearance_m) for ring in polygon.interiors]
        usable = outer.difference(unary_union(holes)) if holes else outer
        if not usable.is_empty:
            parts.extend(iter_polygons(usable))
    if not parts:
        raise RuntimeError("The configured headland and obstacle buffers removed the whole field.")
    return unary_union(parts)


def work_edge_geometries(geometry, headland_m: float, obstacle_clearance_m: float):
    """Return the physical edges that can terminate E0 work geometry.

    The outer edge represents the inner limit of the assumed external
    headland. Hole edges remain unclassified obstacles: they require a work
    break, but do not authorize a turn or maneuver.
    """

    headland_edges = []
    obstacle_edges = []
    for polygon in iter_polygons(geometry):
        outer = Polygon(polygon.exterior).buffer(-headland_m, join_style=2)
        if not outer.is_empty:
            headland_edges.append(outer.boundary)
        for ring in polygon.interiors:
            obstacle = Polygon(ring).buffer(obstacle_clearance_m)
            if not obstacle.is_empty:
                obstacle_edges.append(obstacle.boundary)
    return (
        unary_union(headland_edges) if headland_edges else None,
        unary_union(obstacle_edges) if obstacle_edges else None,
    )


def canonical_field_id(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None


def read_fields(
    boundary_path: Path = BOUNDARY,
    boundary_layer: str | None = None,
    target_field_ids: list[str] | None = None,
    field_id_column: str | None = None,
    return_metadata: bool = False,
):
    source = ogr.Open(str(boundary_path))
    if source is None:
        raise RuntimeError(f"Could not open field boundary dataset: {boundary_path}")
    if boundary_layer is not None:
        layer = source.GetLayerByName(boundary_layer)
        if layer is None:
            available = [source.GetLayerByIndex(index).GetName() for index in range(source.GetLayerCount())]
            raise RuntimeError(
                f"FIELD_BOUNDARY layer {boundary_layer!r} was not found; available layers: {available}."
            )
    else:
        if source.GetLayerCount() != 1:
            raise RuntimeError(
                f"FIELD_BOUNDARY must declare layer_name when its datasource contains "
                f"{source.GetLayerCount()} layers."
            )
        layer = source.GetLayer(0)
    spatial_ref = layer.GetSpatialRef()
    if spatial_ref is None:
        raise RuntimeError("FIELD_BOUNDARY has no declared CRS.")
    projection = spatial_ref.ExportToWkt()
    definition = layer.GetLayerDefn()
    layer_name = layer.GetName()
    field_names = [
        definition.GetFieldDefn(index).GetName()
        for index in range(definition.GetFieldCount())
    ]

    records = []
    for feature in layer:
        geometry_ref = feature.GetGeometryRef()
        if geometry_ref is None:
            raise RuntimeError(f"FIELD_BOUNDARY feature {feature.GetFID()} has no geometry.")
        geometry = wkb.loads(bytes(geometry_ref.ExportToWkb()))
        if geometry.is_empty or not isinstance(geometry, (Polygon, MultiPolygon)):
            raise RuntimeError(
                f"FIELD_BOUNDARY feature {feature.GetFID()} is not a polygonal geometry."
            )
        if not geometry.is_valid:
            raise RuntimeError(f"FIELD_BOUNDARY feature {feature.GetFID()} is geometrically invalid.")
        records.append(
            {
                "fid": feature.GetFID(),
                "attributes": {name: feature.GetField(name) for name in field_names},
                "geometry": geometry,
            }
        )
    source = None
    if not records:
        raise RuntimeError("No field polygons were found.")

    requested_ids = (
        [canonical_field_id(value) for value in target_field_ids]
        if target_field_ids is not None
        else None
    )
    if requested_ids is not None:
        if any(value is None for value in requested_ids):
            raise RuntimeError("scope.field_ids contains an empty identifier.")
        if len(requested_ids) != len(set(requested_ids)):
            raise RuntimeError("scope.field_ids contains duplicate identifiers after normalization.")
        requested_set = set(requested_ids)
        candidate_columns = []
        for name in field_names:
            values = {
                value
                for value in (
                    canonical_field_id(record["attributes"][name])
                    for record in records
                )
                if value is not None
            }
            if requested_set.issubset(values):
                candidate_columns.append(name)
        if field_id_column is not None:
            if field_id_column not in field_names:
                raise RuntimeError(
                    f"Unknown --field-id-column {field_id_column!r}; available columns: {', '.join(field_names)}"
                )
            if field_id_column not in candidate_columns:
                raise RuntimeError(
                    f"Column {field_id_column!r} does not contain every scope.field_ids value."
                )
            selected_column = field_id_column
            resolution_mode = "CLI_EXPLICIT"
        else:
            if not candidate_columns:
                raise RuntimeError(
                    "No FIELD_BOUNDARY attribute contains every scope.field_ids value; "
                    "provide a correct boundary or --field-id-column."
                )
            if len(candidate_columns) > 1:
                raise RuntimeError(
                    "FIELD_BOUNDARY identifier is ambiguous; candidate columns are "
                    f"{', '.join(candidate_columns)}. Use --field-id-column."
                )
            selected_column = candidate_columns[0]
            resolution_mode = "UNIQUE_SCOPE_COVERAGE"
        ordered_ids = requested_ids
    else:
        selected_column = field_id_column or "cd_upnivel"
        if selected_column not in field_names:
            raise RuntimeError(
                f"Legacy field id column {selected_column!r} is missing from {boundary_path}."
            )
        candidate_columns = [selected_column]
        resolution_mode = "LEGACY_FIXED_COLUMN" if field_id_column is None else "CLI_EXPLICIT"
        ordered_ids = []
        for record in records:
            field_id = canonical_field_id(record["attributes"][selected_column])
            if field_id is None:
                raise RuntimeError(
                    f"FIELD_BOUNDARY feature {record['fid']} has an empty {selected_column!r} value."
                )
            if field_id not in ordered_ids:
                ordered_ids.append(field_id)

    geometries_by_id: dict[str, list] = {field_id: [] for field_id in ordered_ids}
    for record in records:
        field_id = canonical_field_id(record["attributes"][selected_column])
        if field_id in geometries_by_id:
            geometries_by_id[field_id].append(record["geometry"])

    missing_ids = [field_id for field_id, geometries in geometries_by_id.items() if not geometries]
    if missing_ids:
        raise RuntimeError(f"FIELD_BOUNDARY is missing requested fields: {', '.join(missing_ids)}")

    fields = []
    for field_id in ordered_ids:
        geometry = unary_union(geometries_by_id[field_id])
        if geometry.is_empty or not geometry.is_valid:
            raise RuntimeError(f"Merged FIELD_BOUNDARY geometry is invalid for field {field_id}.")
        usable = make_usable_geometry(
            geometry,
            PARAMS.outer_headland_m,
            PARAMS.obstacle_clearance_m,
        )
        headland_work_edge, unclassified_obstacle_edge = work_edge_geometries(
            geometry,
            PARAMS.outer_headland_m,
            PARAMS.obstacle_clearance_m,
        )
        fields.append(
            {
                "code": field_id,
                "geometry": geometry,
                "usable": usable,
                "headland_work_edge": headland_work_edge,
                "unclassified_obstacle_edge": unclassified_obstacle_edge,
                "gross_area_ha": geometry.area / 10_000,
                "usable_area_ha": usable.area / 10_000,
            }
        )

    metadata = {
        "layer_name": layer_name,
        "available_attribute_columns": field_names,
        "candidate_field_id_columns": candidate_columns,
        "selected_field_id_column": selected_column,
        "field_id_resolution": resolution_mode,
        "requested_field_ids": requested_ids,
        "loaded_field_ids": [field["code"] for field in fields],
        "ignored_feature_count": len(records) - sum(len(items) for items in geometries_by_id.values()),
    }
    return (fields, projection, metadata) if return_metadata else (fields, projection)


def spatial_reference_from_wkt(wkt_text: str, label: str) -> osr.SpatialReference:
    if not wkt_text:
        raise RuntimeError(f"{label} has no declared CRS.")
    spatial_ref = osr.SpatialReference()
    if spatial_ref.ImportFromWkt(wkt_text) != ogr.OGRERR_NONE:
        raise RuntimeError(f"{label} has an invalid CRS definition.")
    spatial_ref.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return spatial_ref


def validate_spatial_references(
    boundary_projection: str,
    dtm_path: Path,
    requested_horizontal_crs: str | None,
) -> dict:
    boundary_srs = spatial_reference_from_wkt(boundary_projection, "FIELD_BOUNDARY")
    if not boundary_srs.IsProjected():
        raise RuntimeError("FIELD_BOUNDARY CRS must be projected.")
    linear_units = float(boundary_srs.GetLinearUnits())
    if not math.isclose(linear_units, 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise RuntimeError(
            "FIELD_BOUNDARY CRS must use metre as its linear unit; "
            f"found {boundary_srs.GetLinearUnitsName()} ({linear_units} m/unit)."
        )

    dtm_dataset = gdal.Open(str(dtm_path))
    if dtm_dataset is None:
        raise RuntimeError(f"Could not open terrain raster: {dtm_path}")
    dtm_projection = dtm_dataset.GetProjection()
    dtm_srs = spatial_reference_from_wkt(dtm_projection, "DTM")
    dtm_dataset = None
    if not dtm_srs.IsProjected():
        raise RuntimeError("DTM CRS must be projected.")
    if not math.isclose(float(dtm_srs.GetLinearUnits()), 1.0, rel_tol=0.0, abs_tol=1e-9):
        raise RuntimeError("DTM CRS must use metre as its linear unit.")
    if not boundary_srs.IsSame(dtm_srs):
        raise RuntimeError("DTM and FIELD_BOUNDARY must use the same projected metric CRS.")

    if requested_horizontal_crs is not None:
        request_srs = osr.SpatialReference()
        if request_srs.SetFromUserInput(requested_horizontal_crs) != ogr.OGRERR_NONE:
            raise RuntimeError(
                f"terrain.horizontal_crs is invalid: {requested_horizontal_crs!r}."
            )
        request_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        if not request_srs.IsSame(dtm_srs):
            raise RuntimeError(
                "terrain.horizontal_crs does not match the DTM and FIELD_BOUNDARY CRS."
            )

    boundary_srs.AutoIdentifyEPSG()
    authority_name = boundary_srs.GetAuthorityName(None)
    authority_code = boundary_srs.GetAuthorityCode(None)
    return {
        "projected": True,
        "linear_unit_name": boundary_srs.GetLinearUnitsName(),
        "linear_units_to_m": linear_units,
        "authority": (
            f"{authority_name}:{authority_code}"
            if authority_name is not None and authority_code is not None
            else None
        ),
        "dtm_boundary_same_crs": True,
        "request_crs_matches": None if requested_horizontal_crs is None else True,
    }


class Terrain:
    def __init__(self, path: Path):
        dataset = gdal.Open(str(path))
        if dataset is None:
            raise RuntimeError(f"Could not open terrain raster: {path}")
        self.transform = dataset.GetGeoTransform()
        if not math.isclose(self.transform[2], 0.0, abs_tol=1e-12) or not math.isclose(
            self.transform[4], 0.0, abs_tol=1e-12
        ):
            raise RuntimeError("Rotated DTM geotransforms are not supported by this E0 engine.")
        self.projection = dataset.GetProjection()
        self.width = dataset.RasterXSize
        self.height = dataset.RasterYSize
        self.resolution_x = abs(self.transform[1])
        self.resolution_y = abs(self.transform[5])
        band = dataset.GetRasterBand(1)
        raw = band.ReadAsArray().astype("float64")
        nodata = band.GetNoDataValue()
        valid = np.isfinite(raw)
        if nodata is not None:
            valid &= ~np.isclose(raw, nodata)

        sigma_y = PARAMS.terrain_smoothing_sigma_m / self.resolution_y
        sigma_x = PARAMS.terrain_smoothing_sigma_m / self.resolution_x
        weights = ndimage.gaussian_filter(valid.astype(float), (sigma_y, sigma_x), mode="nearest")
        values = ndimage.gaussian_filter(np.where(valid, raw, 0.0), (sigma_y, sigma_x), mode="nearest")
        self.elevation = np.divide(values, weights, out=np.full_like(values, np.nan), where=weights > 1e-6)
        self.valid = valid & np.isfinite(self.elevation)

        # Raster rows increase southward; reverse the row derivative for northing.
        self.gx = np.gradient(self.elevation, self.resolution_x, axis=1)
        self.gy = -np.gradient(self.elevation, self.resolution_y, axis=0)
        x0 = self.transform[0]
        x1 = x0 + self.width * self.transform[1]
        y0 = self.transform[3]
        y1 = y0 + self.height * self.transform[5]
        self.footprint = box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self.dataset = dataset

    def xy_grids(self, row_slice: slice, col_slice: slice) -> tuple[np.ndarray, np.ndarray]:
        columns = np.arange(col_slice.start, col_slice.stop)
        rows = np.arange(row_slice.start, row_slice.stop)
        xs = self.transform[0] + (columns + 0.5) * self.transform[1]
        ys = self.transform[3] + (rows + 0.5) * self.transform[5]
        return xs, ys

    def geometry_mask(self, geometry, *, all_touched: bool = False) -> np.ndarray:
        memory = gdal.GetDriverByName("MEM").Create("", self.width, self.height, 1, gdal.GDT_Byte)
        memory.SetGeoTransform(self.transform)
        memory.SetProjection(self.projection)
        spatial_ref = osr.SpatialReference()
        spatial_ref.ImportFromWkt(self.projection)
        datasource = ogr.GetDriverByName("Memory").CreateDataSource("")
        layer = datasource.CreateLayer("mask", srs=spatial_ref, geom_type=ogr.wkbUnknown)
        feature = ogr.Feature(layer.GetLayerDefn())
        feature.SetGeometry(ogr.CreateGeometryFromWkb(geometry.wkb))
        layer.CreateFeature(feature)
        options = ["ALL_TOUCHED=TRUE"] if all_touched else []
        gdal.RasterizeLayer(memory, [1], layer, burn_values=[1], options=options)
        return memory.GetRasterBand(1).ReadAsArray().astype(bool)

    def validate_geometry_coverage(self, geometry) -> dict:
        outside_area_m2 = float(geometry.difference(self.footprint).area)
        raw_mask = self.geometry_mask(geometry, all_touched=True)
        invalid_cells = int(np.count_nonzero(raw_mask & ~self.valid))
        covered_cells = int(np.count_nonzero(raw_mask))
        if outside_area_m2 > 1e-6 or covered_cells == 0 or invalid_cells:
            raise RuntimeError(
                "DTM does not completely cover the usable work geometry: "
                f"outside_area_m2={outside_area_m2:.6f}, "
                f"covered_cells={covered_cells}, invalid_or_nodata_cells={invalid_cells}."
            )
        return {
            "status": "PASS_COMPLETE_VALID_CELL_COVERAGE",
            "outside_area_m2": rounded(outside_area_m2, 6),
            "all_touched_cell_count": covered_cells,
            "invalid_or_nodata_cell_count": invalid_cells,
            "cell_area_m2": rounded(self.resolution_x * self.resolution_y, 6),
        }

    def raster_mask(self, geometry) -> np.ndarray:
        return self.geometry_mask(geometry) & self.valid

    def sample(self, array: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
        xs = np.asarray(xs, dtype=float)
        ys = np.asarray(ys, dtype=float)
        columns = (xs - self.transform[0]) / self.transform[1] - 0.5
        rows = (ys - self.transform[3]) / self.transform[5] - 0.5
        inside = (
            np.isfinite(columns)
            & np.isfinite(rows)
            & (columns >= -0.5)
            & (columns <= self.width - 0.5)
            & (rows >= -0.5)
            & (rows <= self.height - 0.5)
        )
        if not bool(np.all(inside)):
            raise RuntimeError("Terrain sample falls outside the DTM cell-center extent.")
        valid_samples = ndimage.map_coordinates(
            self.valid.astype(np.uint8),
            [rows, columns],
            order=0,
            mode="nearest",
            cval=0,
        )
        if not bool(np.all(valid_samples == 1)):
            raise RuntimeError("Terrain sample crosses DTM NoData or a non-finite source cell.")
        return ndimage.map_coordinates(
            array,
            [rows, columns],
            order=1,
            mode="nearest",
        )


def field_window(mask: np.ndarray, padding: int = 3) -> tuple[slice, slice]:
    rows, columns = np.where(mask)
    if rows.size == 0:
        raise RuntimeError("A usable field does not intersect the terrain raster.")
    return (
        slice(max(0, int(rows.min()) - padding), min(mask.shape[0], int(rows.max()) + padding + 1)),
        slice(max(0, int(columns.min()) - padding), min(mask.shape[1], int(columns.max()) + padding + 1)),
    )


def projection_width(geometry, normal_x: float, normal_y: float) -> float:
    hull = geometry.convex_hull
    coordinates = np.asarray(hull.exterior.coords)
    projection = coordinates[:, 0] * normal_x + coordinates[:, 1] * normal_y
    return float(projection.max() - projection.min())


def candidate_metrics(field: dict, terrain: Terrain, mask: np.ndarray) -> list[dict]:
    values = mask & terrain.valid
    rows, columns = np.where(values)
    x_coordinates = terrain.transform[0] + (columns + 0.5) * terrain.transform[1]
    y_coordinates = terrain.transform[3] + (rows + 0.5) * terrain.transform[5]
    elevation = terrain.elevation[values]
    gx = terrain.gx[values]
    gy = terrain.gy[values]
    candidates = []
    for theta_deg in range(0, 180, PARAMS.candidate_angle_step_deg):
        theta = math.radians(theta_deg)
        base_tx, base_ty = math.cos(theta), math.sin(theta)
        base_nx, base_ny = -base_ty, base_tx
        width = projection_width(field["usable"], base_nx, base_ny)
        estimated_mean_run = field["usable"].area / max(width, PARAMS.row_spacing_m)
        for lambda_value in PARAMS.field_lambda_values:
            phi_x = base_nx + lambda_value * gx
            phi_y = base_ny + lambda_value * gy
            magnitude = np.hypot(phi_x, phi_y)
            safe = magnitude > 1e-6
            tangent_x = np.divide(-phi_y, magnitude, out=np.zeros_like(phi_y), where=safe)
            tangent_y = np.divide(phi_x, magnitude, out=np.zeros_like(phi_x), where=safe)
            normal_x = np.divide(phi_x, magnitude, out=np.zeros_like(phi_x), where=safe)
            normal_y = np.divide(phi_y, magnitude, out=np.zeros_like(phi_y), where=safe)

            along = np.abs(gx * tangent_x + gy * tangent_y) * 100.0
            cross = np.abs(gx * normal_x + gy * normal_y) * 100.0
            phi = (
                base_nx * x_coordinates
                + base_ny * y_coordinates
                + lambda_value * elevation
            )
            phase_valid = np.ones(phi.shape, dtype=bool)
            phase_levels = adaptive_phase_levels(
                phi,
                magnitude,
                phase_valid,
                PARAMS.row_spacing_m,
            )
            actual_spacing = physical_spacing_from_levels(
                phi,
                magnitude,
                phase_valid,
                phase_levels,
            )
            alignment = np.clip(np.abs(tangent_x * base_tx + tangent_y * base_ty), 0.0, 1.0)
            deflection = np.degrees(np.arccos(alignment))
            spacing_deviation = np.abs(actual_spacing / PARAMS.row_spacing_m - 1.0)

            candidates.append(
                {
                    "field_code": field["code"],
                    "theta_deg": float(theta_deg),
                    "lambda": float(lambda_value),
                    "estimated_mean_run_m": estimated_mean_run,
                    "along_p50_percent": percentile(along, 50),
                    "along_p90_percent": percentile(along, 90),
                    "along_p95_percent": percentile(along, 95),
                    "along_max_percent": float(np.max(along)),
                    "along_above_target_percent": 100.0 * float(np.mean(along > PARAMS.reference_target_grade_percent)),
                    "along_above_alert_percent": 100.0 * float(np.mean(along > PARAMS.reference_alert_grade_percent)),
                    "cross_p95_percent": percentile(cross, 95),
                    "cross_max_percent": float(np.max(cross)),
                    "spacing_p05_m": percentile(actual_spacing, 5),
                    "spacing_p50_m": percentile(actual_spacing, 50),
                    "spacing_p95_m": percentile(actual_spacing, 95),
                    "spacing_outside_tolerance_percent": 100.0
                    * float(np.mean(spacing_deviation > PARAMS.spacing_tolerance_fraction)),
                    "singular_percent": 100.0 * float(np.mean(magnitude < 0.35)),
                    "deflection_p95_deg": percentile(deflection, 95),
                }
            )
    return candidates


def normalized(series: pd.Series, reverse: bool = False) -> pd.Series:
    values = series.astype(float)
    low = float(values.quantile(0.05))
    high = float(values.quantile(0.95))
    if high <= low + 1e-9:
        result = pd.Series(np.zeros(values.size), index=values.index)
    else:
        result = ((values - low) / (high - low)).clip(0.0, 1.0)
    return 1.0 - result if reverse else result


def add_scores(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    scored = []
    for _, group in frame.groupby("field_code", sort=False):
        group = group.copy()
        group["conservation_loss"] = (
            0.45 * normalized(group["along_p90_percent"])
            + 0.25 * normalized(group["along_p95_percent"])
            + 0.20 * normalized(group["along_above_alert_percent"])
            + 0.10 * normalized(group["singular_percent"])
        )
        group["performance_loss"] = normalized(group["estimated_mean_run_m"], reverse=True)
        group["harvestability_loss"] = (
            0.55 * normalized(group["cross_p95_percent"])
            + 0.25 * normalized(group["deflection_p95_deg"])
            + 0.20 * normalized(group["spacing_outside_tolerance_percent"])
        )
        group["spacing_loss"] = normalized(group["spacing_outside_tolerance_percent"])
        group["controlled_grade_loss"] = (
            np.abs(group["along_p50_percent"] - PARAMS.reference_target_grade_percent)
            / max(PARAMS.reference_target_grade_percent, 0.1)
            + 0.35
            * np.maximum(
                group["along_p95_percent"] - PARAMS.reference_alert_grade_percent,
                0.0,
            )
            / PARAMS.reference_alert_grade_percent
            + 0.20 * group["spacing_loss"]
            + 0.10 * group["performance_loss"]
        )
        group["balanced_loss"] = (
            0.40 * group["conservation_loss"]
            + 0.25 * group["harvestability_loss"]
            + 0.25 * group["performance_loss"]
            + 0.10 * group["spacing_loss"]
        )
        scored.append(group)
    return pd.concat(scored, ignore_index=True)


def eligible_candidates(
    group: pd.DataFrame,
    gate_policy: CandidateGatePolicy = CandidateGatePolicy(),
    scope: str = "candidate group",
) -> pd.DataFrame:
    valid = (
        (group["singular_percent"] <= 0.5)
        & (group["spacing_outside_tolerance_percent"] <= 35.0)
    )
    if gate_policy.maximum_furrow_grade_percent is not None:
        valid &= group["along_max_percent"] <= gate_policy.maximum_furrow_grade_percent
    if gate_policy.maximum_cross_slope_percent is not None:
        valid &= group["cross_max_percent"] <= gate_policy.maximum_cross_slope_percent
    eligible = group[valid]
    if eligible.empty and not gate_policy.allow_legacy_fallback:
        raise NoFeasibleCandidateError(scope, gate_policy)
    return eligible if not eligible.empty else group


def select_field_candidate(
    group: pd.DataFrame,
    strategy: str,
    gate_policy: CandidateGatePolicy = CandidateGatePolicy(),
) -> pd.Series:
    field_code = str(group["field_code"].iloc[0])
    eligible = eligible_candidates(group, gate_policy, scope=f"field {field_code} / {strategy}")
    if strategy == "conservation":
        score = eligible["conservation_loss"] + 0.15 * eligible["performance_loss"] + 0.15 * eligible["spacing_loss"]
    elif strategy == "controlled_grade":
        score = eligible["controlled_grade_loss"]
    elif strategy == "balanced":
        score = eligible["balanced_loss"]
    elif strategy == "performance":
        straight = eligible[np.isclose(eligible["lambda"], 0.0)]
        if not straight.empty:
            eligible = straight
        within_alert = eligible[eligible["along_p95_percent"] <= PARAMS.reference_alert_grade_percent]
        if not within_alert.empty:
            eligible = within_alert
        score = eligible["performance_loss"] + 0.20 * eligible["conservation_loss"]
    elif strategy == "harvestability":
        within_alert = eligible[eligible["along_p95_percent"] <= PARAMS.reference_alert_grade_percent]
        if not within_alert.empty:
            eligible = within_alert
        score = (
            0.55 * eligible["harvestability_loss"]
            + 0.30 * eligible["conservation_loss"]
            + 0.15 * eligible["performance_loss"]
        )
    else:
        raise ValueError(f"Unknown field strategy: {strategy}")
    return eligible.loc[score.idxmin()]


def select_parameters(
    candidates: pd.DataFrame,
    gate_policy: CandidateGatePolicy = CandidateGatePolicy(),
) -> dict[str, dict[str, pd.Series]]:
    selections: dict[str, dict[str, pd.Series]] = {}
    field_groups = {code: group for code, group in candidates.groupby("field_code", sort=False)}

    for scenario in SCENARIOS:
        scenario_id = scenario["id"]
        strategy = scenario["selection"]
        if strategy == "shared_axis":
            shared_candidates = candidates[np.isclose(candidates["lambda"], 0.0)]
            if shared_candidates.empty:
                raise NoFeasibleCandidateError("shared straight-axis aggregate", gate_policy)
            aggregate = (
                shared_candidates.groupby(["theta_deg", "lambda"], as_index=False)
                .agg(
                    balanced_loss=("balanced_loss", "mean"),
                    conservation_loss=("conservation_loss", "mean"),
                    harvestability_loss=("harvestability_loss", "mean"),
                    performance_loss=("performance_loss", "mean"),
                    spacing_loss=("spacing_loss", "mean"),
                    singular_percent=("singular_percent", "max"),
                    spacing_outside_tolerance_percent=("spacing_outside_tolerance_percent", "max"),
                    along_p95_percent=("along_p95_percent", "max"),
                    along_max_percent=("along_max_percent", "max"),
                    cross_p95_percent=("cross_p95_percent", "max"),
                    cross_max_percent=("cross_max_percent", "max"),
                )
            )
            eligible = eligible_candidates(
                aggregate,
                gate_policy,
                scope="shared-axis aggregate",
            )
            shared_score = (
                0.40 * eligible["balanced_loss"]
                + 0.20 * eligible["conservation_loss"]
                + 0.15 * eligible["harvestability_loss"]
                + 0.25 * eligible["performance_loss"]
            )
            chosen = eligible.loc[shared_score.idxmin()]
            selections[scenario_id] = {}
            for code, group in field_groups.items():
                row = group[
                    np.isclose(group["theta_deg"], chosen["theta_deg"])
                    & np.isclose(group["lambda"], chosen["lambda"])
                ].iloc[0]
                selections[scenario_id][code] = row
        else:
            selections[scenario_id] = {
                code: select_field_candidate(group, strategy, gate_policy)
                for code, group in field_groups.items()
            }
    return selections


def adaptive_mean_phase_levels(
    phi: np.ndarray,
    gradient_magnitude: np.ndarray,
    valid: np.ndarray,
    spacing: float,
) -> np.ndarray:
    """Compatibility wrapper for the explicit E0 phase-spacing heuristic."""

    return adaptive_phase_levels(phi, gradient_magnitude, valid, spacing)


def contour_lines(
    field: dict,
    terrain: Terrain,
    mask: np.ndarray,
    theta_deg: float,
    lambda_value: float,
) -> list[tuple[int, float, LineString]]:
    row_slice, col_slice = field_window(mask)
    xs, ys = terrain.xy_grids(row_slice, col_slice)
    x_grid, y_grid = np.meshgrid(xs, ys)
    theta = math.radians(theta_deg)
    normal_x, normal_y = -math.sin(theta), math.cos(theta)
    elevation = terrain.elevation[row_slice, col_slice]
    gx = terrain.gx[row_slice, col_slice]
    gy = terrain.gy[row_slice, col_slice]
    local_mask = mask[row_slice, col_slice] & np.isfinite(elevation)
    phi = normal_x * x_grid + normal_y * y_grid + lambda_value * elevation
    phi_x = normal_x + lambda_value * gx
    phi_y = normal_y + lambda_value * gy
    gradient_magnitude = np.hypot(phi_x, phi_y)
    masked_phi = np.ma.masked_where(~local_mask, phi)
    levels = adaptive_mean_phase_levels(phi, gradient_magnitude, local_mask, PARAMS.row_spacing_m)

    figure, axes = plt.subplots(figsize=(1, 1))
    contour_set = axes.contour(xs, ys, masked_phi, levels=levels)
    plt.close(figure)

    lines = []
    for local_level_id, (phase_level_m, segments) in enumerate(
        zip(contour_set.levels, contour_set.allsegs)
    ):
        for vertices in segments:
            if len(vertices) < 2:
                continue
            candidate = LineString(vertices).simplify(0.25, preserve_topology=False)
            clipped = candidate.intersection(field["usable"])
            for part in iter_lines(clipped):
                if part.length >= PARAMS.minimum_segment_m:
                    lines.append((local_level_id, float(phase_level_m), part))
    return lines


def resample_line(line: LineString, interval: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    count = max(2, int(math.ceil(line.length / interval)) + 1)
    distances = np.linspace(0.0, line.length, count)
    coordinates = np.asarray([line.interpolate(float(distance)).coords[0] for distance in distances])
    return distances, coordinates[:, 0], coordinates[:, 1]


def line_metrics(line: LineString, terrain: Terrain) -> tuple[LineString, dict]:
    distances, xs, ys = resample_line(line, PARAMS.profile_sample_m)
    zs = terrain.sample(terrain.elevation, xs, ys)
    if not np.isfinite(zs).all():
        raise RuntimeError("A generated row profile crosses terrain NoData or non-finite elevation.")
    if zs[0] < zs[-1]:
        line = LineString(list(line.coords)[::-1])
        distances, xs, ys = resample_line(line, PARAMS.profile_sample_m)
        zs = terrain.sample(terrain.elevation, xs, ys)

    smooth_sigma = max(0.75, 5.0 / max(PARAMS.profile_sample_m, 0.1))
    smooth_z = ndimage.gaussian_filter1d(zs, smooth_sigma, mode="nearest")
    delta_distance = np.diff(distances)
    signed_grade = np.divide(
        smooth_z[:-1] - smooth_z[1:],
        delta_distance,
        out=np.zeros_like(delta_distance),
        where=delta_distance > 0,
    ) * 100.0
    absolute_grade = np.abs(signed_grade)

    gx = terrain.sample(terrain.gx, xs, ys)
    gy = terrain.sample(terrain.gy, xs, ys)
    if not np.isfinite(gx).all() or not np.isfinite(gy).all():
        raise RuntimeError("A generated row profile crosses a non-finite terrain gradient.")
    tangent_x = np.gradient(xs, distances, edge_order=1)
    tangent_y = np.gradient(ys, distances, edge_order=1)
    tangent_norm = np.hypot(tangent_x, tangent_y)
    tangent_x = np.divide(tangent_x, tangent_norm, out=np.zeros_like(tangent_x), where=tangent_norm > 0)
    tangent_y = np.divide(tangent_y, tangent_norm, out=np.zeros_like(tangent_y), where=tangent_norm > 0)
    cross_grade = np.abs(gx * -tangent_y + gy * tangent_x) * 100.0

    angles = np.unwrap(np.arctan2(tangent_y, tangent_x))
    curvature = np.abs(np.gradient(angles, distances, edge_order=1))
    meaningful_curvature = curvature[curvature > 1e-5]
    radius_values = 1.0 / meaningful_curvature if meaningful_curvature.size else np.asarray([np.inf])

    signs = np.zeros_like(signed_grade, dtype=int)
    signs[signed_grade > PARAMS.reversal_deadband_percent] = 1
    signs[signed_grade < -PARAMS.reversal_deadband_percent] = -1
    nonzero = signs[signs != 0]
    reversals = int(np.count_nonzero(nonzero[1:] != nonzero[:-1])) if nonzero.size > 1 else 0

    metrics = {
        "length_m": float(line.length),
        "z_start_m": float(zs[0]),
        "z_end_m": float(zs[-1]),
        "endpoint_grade_percent": 100.0 * float(zs[0] - zs[-1]) / max(line.length, 1e-6),
        "absolute_grade_p50_percent": percentile(absolute_grade, 50),
        "absolute_grade_p95_percent": percentile(absolute_grade, 95),
        "absolute_grade_max_percent": float(absolute_grade.max()) if absolute_grade.size else 0.0,
        "above_target_length_percent": 100.0 * float(np.mean(absolute_grade > PARAMS.reference_target_grade_percent)),
        "above_alert_length_percent": 100.0 * float(np.mean(absolute_grade > PARAMS.reference_alert_grade_percent)),
        "adverse_length_percent": 100.0 * float(np.mean(signed_grade < -PARAMS.reversal_deadband_percent)),
        "reversal_count": reversals,
        "cross_grade_p95_percent": percentile(cross_grade, 95),
        "cross_grade_max_percent": float(np.max(cross_grade)),
        "minimum_radius_m": float(np.min(radius_values)),
        "closed": bool(line.coords[0][0] == line.coords[-1][0] and line.coords[0][1] == line.coords[-1][1]),
    }
    return line, metrics


def aggregate_lines(
    line_records: list[dict],
    field: dict,
    candidate: pd.Series,
    scenario: dict,
    zone_count: int = 1,
) -> dict:
    lengths = np.asarray([record["metrics"]["length_m"] for record in line_records], dtype=float)
    total_length = float(lengths.sum()) if lengths.size else 0.0
    weights = lengths / total_length if total_length > 0 else np.zeros_like(lengths)

    def weighted_mean(key: str) -> float | None:
        if not line_records or total_length <= 0:
            return None
        values = np.asarray([record["metrics"][key] for record in line_records], dtype=float)
        return float(np.sum(values * weights))

    guidance_count = len(set(record["level_id"] for record in line_records))
    extra_segments = max(0, len(line_records) - guidance_count)
    work_seconds = total_length / max(PARAMS.assumed_work_speed_kmh / 3.6, 1e-9)
    # Every E0 segment is an isolated run. A route solver may later reduce this
    # cost only through approved WORK/LIFT/CROSS/TURN connections.
    turn_seconds = max(0, len(line_records) - 1) * PARAMS.assumed_turn_seconds
    lift_seconds = extra_segments * PARAMS.assumed_lift_seconds
    efficiency = work_seconds / (work_seconds + turn_seconds + lift_seconds) if work_seconds > 0 else 0.0

    summary = {
        "scenario_id": scenario["id"],
        "scenario_name": scenario["name"],
        "selection_strategy": scenario["selection"],
        "field_code": field["code"],
        "release_level": "E0_topographic_screening",
        "hydraulic_status": "not_evaluated_missing_soil_rainfall_receivers_and_structures",
        "theta_deg": rounded(candidate["theta_deg"], 2),
        "lambda": rounded(candidate["lambda"], 2),
        "gross_area_ha": rounded(field["gross_area_ha"], 4),
        "usable_area_ha": rounded(field["usable_area_ha"], 4),
        "total_line_km": rounded(total_length / 1000.0, 3),
        "segment_count": int(lengths.size),
        "guidance_group_count": int(guidance_count),
        "fragmentation_count": int(extra_segments),
        "segment_length_mean_m": rounded(lengths.mean() if lengths.size else None, 2),
        "segment_length_p10_m": rounded(percentile(lengths, 10), 2),
        "segment_length_p50_m": rounded(percentile(lengths, 50), 2),
        "segment_length_p90_m": rounded(percentile(lengths, 90), 2),
        "segments_below_50m_percent": rounded(100.0 * float(np.mean(lengths < 50.0)) if lengths.size else None, 2),
        "segments_above_200m_percent": rounded(100.0 * float(np.mean(lengths >= 200.0)) if lengths.size else None, 2),
        "coverage_proxy_percent": rounded(
            100.0 * total_length * PARAMS.row_spacing_m / max(field["usable"].area, 1e-9),
            2,
        ),
        "absolute_grade_p95_weighted_percent": rounded(weighted_mean("absolute_grade_p95_percent"), 3),
        "above_target_length_weighted_percent": rounded(weighted_mean("above_target_length_percent"), 2),
        "above_alert_length_weighted_percent": rounded(weighted_mean("above_alert_length_percent"), 2),
        "adverse_length_weighted_percent": rounded(weighted_mean("adverse_length_percent"), 2),
        "reversals_per_km": rounded(
            sum(record["metrics"]["reversal_count"] for record in line_records) / max(total_length / 1000.0, 1e-9),
            3,
        ),
        "cross_grade_p95_weighted_percent": rounded(weighted_mean("cross_grade_p95_percent"), 3),
        "closed_line_count": int(sum(record["metrics"]["closed"] for record in line_records)),
        "planning_field_efficiency_percent": rounded(100.0 * efficiency, 2),
        "planning_efficiency_status": "E0_ISOLATED_SEGMENT_PENALTY_NO_ROUTE_CREDIT",
        "proxy_spacing_p05_m": rounded(candidate["spacing_p05_m"], 3),
        "proxy_spacing_p95_m": rounded(candidate["spacing_p95_m"], 3),
        "proxy_spacing_outside_tolerance_percent": rounded(candidate["spacing_outside_tolerance_percent"], 2),
        "candidate_conservation_loss": rounded(candidate["conservation_loss"], 4),
        "candidate_harvestability_loss": rounded(candidate["harvestability_loss"], 4),
        "candidate_performance_loss": rounded(candidate["performance_loss"], 4),
        "zone_count": int(zone_count),
    }
    return summary


def build_scenarios(
    fields: list[dict],
    terrain: Terrain,
    masks: dict[str, np.ndarray],
    selections: dict[str, dict[str, pd.Series]],
    gate_policy: CandidateGatePolicy = CandidateGatePolicy(),
) -> tuple[list[dict], list[dict]]:
    all_lines = []
    summaries = []
    for scenario in SCENARIOS:
        scenario_id = scenario["id"]
        for field in fields:
            candidate = selections[scenario_id][field["code"]]
            raw_lines = contour_lines(
                field,
                terrain,
                masks[field["code"]],
                float(candidate["theta_deg"]),
                float(candidate["lambda"]),
            )
            field_records = []
            level_segment_counts: dict[int, int] = {}
            for level_id, phase_level_m, raw_line in raw_lines:
                line, metrics = line_metrics(raw_line, terrain)
                segment_index = level_segment_counts.get(level_id, 0)
                level_segment_counts[level_id] = segment_index + 1
                record = {
                    "scenario": scenario,
                    "field": field,
                    "level_id": level_id,
                    "phase_level_m": phase_level_m,
                    "segment_index": segment_index,
                    "geometry": line,
                    "metrics": metrics,
                    "theta_deg": float(candidate["theta_deg"]),
                    "lambda": float(candidate["lambda"]),
                }
                field_records.append(record)
                all_lines.append(record)
            summary = aggregate_lines(field_records, field, candidate, scenario)
            summaries.append(summary)
    return all_lines, summaries


def assign_line_ids(line_records: list[dict]) -> None:
    """Assign stable identifiers before continuity analysis and persistence."""

    for record in line_records:
        scenario_id = record["scenario"]["id"]
        field_code = record["field"]["code"]
        shared_straight_lattice = scenario_id == "E0F_EIXO_COMUM" and math.isclose(
            record["lambda"], 0.0, abs_tol=1e-12
        )
        record["phase_scope"] = (
            "GLOBAL_SHARED_STRAIGHT_LATTICE"
            if shared_straight_lattice
            else "FIELD_LOCAL_ADAPTIVE_LEVELS"
        )
        record["row_global_id"] = (
            int(round(record["phase_level_m"] / PARAMS.row_spacing_m))
            if shared_straight_lattice
            else None
        )
        guidance_id = f"{scenario_id}:{field_code}:G{record['level_id']}"
        line_id = f"{guidance_id}:S{record['segment_index']}"
        record["guidance_id"] = guidance_id
        record["line_id"] = line_id


def continuity_policy(terrain: Terrain, required_minimum_radius_m: float | None) -> ContinuityPolicy:
    # Marching-squares vertices lie on the 1 m raster cell edges and generated
    # lines are simplified by 0.25 m. Keep this terminal tolerance separate
    # from any topology snap tolerance: it never joins neighboring rows.
    raster_half_diagonal = 0.5 * math.hypot(terrain.resolution_x, terrain.resolution_y)
    terminal_tolerance = raster_half_diagonal + 0.25 + 0.05
    return ContinuityPolicy(
        terminal_surface_tolerance_m=terminal_tolerance,
        row_spacing_m=PARAMS.row_spacing_m,
        spacing_tolerance_fraction=PARAMS.spacing_tolerance_fraction,
        required_minimum_radius_m=required_minimum_radius_m,
    )


def apply_continuity_diagnostics(
    line_records: list[dict],
    summaries: list[dict],
    fields: list[dict],
    terrain: Terrain,
    required_minimum_radius_m: float | None,
) -> dict:
    assign_line_ids(line_records)
    continuity = analyze_operational_continuity(
        line_records,
        fields,
        continuity_policy(terrain, required_minimum_radius_m),
    )
    for record in line_records:
        record["continuity"] = continuity["line_diagnostics"][record["line_id"]]
    for summary in summaries:
        summary.update(
            continuity["group_summaries"][(summary["scenario_id"], summary["field_code"])]
        )
    return continuity


def ogr_line_z(line: LineString, terrain: Terrain) -> ogr.Geometry:
    geometry = ogr.Geometry(ogr.wkbLineString25D)
    coordinates = np.asarray(line.coords)
    elevations = terrain.sample(terrain.elevation, coordinates[:, 0], coordinates[:, 1])
    if not np.isfinite(elevations).all():
        raise RuntimeError("A persisted row geometry crosses terrain NoData.")
    for (x, y), z in zip(coordinates, elevations):
        geometry.AddPoint(float(x), float(y), float(z))
    return geometry


def ogr_point_z(point: Point, terrain: Terrain) -> ogr.Geometry:
    x, y = point.coords[0][:2]
    z = float(terrain.sample(terrain.elevation, np.asarray([x]), np.asarray([y]))[0])
    if not math.isfinite(z):
        raise RuntimeError("An operational node crosses terrain NoData.")
    geometry = ogr.Geometry(ogr.wkbPoint25D)
    geometry.AddPoint(float(x), float(y), z)
    return geometry


def create_field(layer: ogr.Layer, name: str, field_type, width: int = 0, precision: int = 0):
    definition = ogr.FieldDefn(name, field_type)
    if width:
        definition.SetWidth(width)
    if precision:
        definition.SetPrecision(precision)
    layer.CreateField(definition)


def write_outputs(
    projection: str,
    terrain: Terrain,
    line_records: list[dict],
    summaries: list[dict],
    candidates: pd.DataFrame,
    continuity: dict,
    output_path: Path = OUTPUT_GPKG,
) -> None:
    if output_path.exists():
        output_path.unlink()
    driver = ogr.GetDriverByName("GPKG")
    datasource = driver.CreateDataSource(str(output_path))
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromWkt(projection)

    line_layer = datasource.CreateLayer("sulcation_lines", srs=spatial_ref, geom_type=ogr.wkbLineString25D)
    line_fields = [
        ("scenario_id", ogr.OFTString, 32, 0),
        ("scenario_name", ogr.OFTString, 80, 0),
        ("field_code", ogr.OFTString, 24, 0),
        ("line_id", ogr.OFTString, 80, 0),
        ("guidance_id", ogr.OFTString, 64, 0),
        ("row_global_id", ogr.OFTInteger64, 0, 0),
        ("phase_level_m", ogr.OFTReal, 0, 6),
        ("phase_scope", ogr.OFTString, 48, 0),
        ("operational_run_id", ogr.OFTString, 80, 0),
        ("run_state", ogr.OFTString, 32, 0),
        ("release", ogr.OFTString, 32, 0),
        ("theta_deg", ogr.OFTReal, 0, 3),
        ("lambda", ogr.OFTReal, 0, 3),
        ("length_m", ogr.OFTReal, 0, 3),
        ("z_start_m", ogr.OFTReal, 0, 3),
        ("z_end_m", ogr.OFTReal, 0, 3),
        ("end_grade", ogr.OFTReal, 0, 3),
        ("grade_p95", ogr.OFTReal, 0, 3),
        ("grade_max", ogr.OFTReal, 0, 3),
        ("above_ref", ogr.OFTReal, 0, 2),
        ("above_alert", ogr.OFTReal, 0, 2),
        ("adverse_pct", ogr.OFTReal, 0, 2),
        ("reversals", ogr.OFTInteger, 0, 0),
        ("cross_p95", ogr.OFTReal, 0, 3),
        ("cross_max", ogr.OFTReal, 0, 3),
        ("min_radius", ogr.OFTReal, 0, 2),
        ("is_closed", ogr.OFTInteger, 0, 0),
        ("start_surface", ogr.OFTString, 40, 0),
        ("end_surface", ogr.OFTString, 40, 0),
        ("start_term", ogr.OFTString, 64, 0),
        ("end_term", ogr.OFTString, 64, 0),
        ("start_maneuver", ogr.OFTString, 64, 0),
        ("end_maneuver", ogr.OFTString, 64, 0),
        ("topology_status", ogr.OFTString, 24, 0),
        ("radius_status", ogr.OFTString, 64, 0),
        ("radius_req", ogr.OFTReal, 0, 2),
        ("continuity_status", ogr.OFTString, 80, 0),
        ("blocker_codes", ogr.OFTString, 254, 0),
        ("warning_codes", ogr.OFTString, 254, 0),
    ]
    for args in line_fields:
        create_field(line_layer, *args)

    for record in line_records:
        metrics = record["metrics"]
        feature = ogr.Feature(line_layer.GetLayerDefn())
        scenario_id = record["scenario"]["id"]
        field_code = record["field"]["code"]
        guidance_id = record["guidance_id"]
        line_id = record["line_id"]
        continuity_line = record["continuity"]
        values = {
            "scenario_id": scenario_id,
            "scenario_name": record["scenario"]["name"],
            "field_code": field_code,
            "line_id": line_id,
            "guidance_id": guidance_id,
            "row_global_id": record["row_global_id"],
            "phase_level_m": record["phase_level_m"],
            "phase_scope": record["phase_scope"],
            "operational_run_id": line_id,
            "run_state": continuity_line["operational_run_state"],
            "release": "E0_SCREENING",
            "theta_deg": record["theta_deg"],
            "lambda": record["lambda"],
            "length_m": metrics["length_m"],
            "z_start_m": metrics["z_start_m"],
            "z_end_m": metrics["z_end_m"],
            "end_grade": metrics["endpoint_grade_percent"],
            "grade_p95": metrics["absolute_grade_p95_percent"],
            "grade_max": metrics["absolute_grade_max_percent"],
            "above_ref": metrics["above_target_length_percent"],
            "above_alert": metrics["above_alert_length_percent"],
            "adverse_pct": metrics["adverse_length_percent"],
            "reversals": metrics["reversal_count"],
            "cross_p95": metrics["cross_grade_p95_percent"],
            "cross_max": metrics["cross_grade_max_percent"],
            "min_radius": continuity_line["radius_min_observed_m"],
            "is_closed": int(metrics["closed"]),
            "start_surface": continuity_line["start"]["surface_type"],
            "end_surface": continuity_line["end"]["surface_type"],
            "start_term": continuity_line["start"]["termination_status"],
            "end_term": continuity_line["end"]["termination_status"],
            "start_maneuver": continuity_line["start"]["maneuver_status"],
            "end_maneuver": continuity_line["end"]["maneuver_status"],
            "topology_status": continuity_line["topology_status"],
            "radius_status": continuity_line["radius_status"],
            "radius_req": continuity_line["radius_required_m"],
            "continuity_status": continuity_line["operational_continuity_status"],
            "blocker_codes": ",".join(continuity_line["blocker_codes"]),
            "warning_codes": ",".join(continuity_line["warning_codes"]),
        }
        for key, value in values.items():
            if value is not None:
                feature.SetField(key, value)
        feature.SetGeometry(ogr_line_z(record["geometry"], terrain))
        line_layer.CreateFeature(feature)

    summary_layer = datasource.CreateLayer("scenario_summary", geom_type=ogr.wkbNone)
    if summaries:
        for key, value in summaries[0].items():
            if isinstance(value, int):
                create_field(summary_layer, key, ogr.OFTInteger)
            elif isinstance(value, float):
                create_field(summary_layer, key, ogr.OFTReal, precision=4)
            else:
                create_field(summary_layer, key, ogr.OFTString, width=160)
        for summary in summaries:
            feature = ogr.Feature(summary_layer.GetLayerDefn())
            for key, value in summary.items():
                if value is not None:
                    feature.SetField(key, value)
            summary_layer.CreateFeature(feature)

    node_layer = datasource.CreateLayer("operational_nodes", srs=spatial_ref, geom_type=ogr.wkbPoint25D)
    node_fields = [
        ("node_id", ogr.OFTString, 96, 0),
        ("line_id", ogr.OFTString, 80, 0),
        ("scenario_id", ogr.OFTString, 40, 0),
        ("field_code", ogr.OFTString, 32, 0),
        ("endpoint", ogr.OFTString, 8, 0),
        ("surface_type", ogr.OFTString, 40, 0),
        ("surface_dist", ogr.OFTReal, 0, 3),
        ("termination", ogr.OFTString, 64, 0),
        ("maneuver", ogr.OFTString, 64, 0),
        ("qa_status", ogr.OFTString, 24, 0),
        ("blockers", ogr.OFTString, 254, 0),
        ("warnings", ogr.OFTString, 254, 0),
    ]
    for args in node_fields:
        create_field(node_layer, *args)
    for node in continuity["nodes"]:
        feature = ogr.Feature(node_layer.GetLayerDefn())
        values = {
            "node_id": node["node_id"],
            "line_id": node["line_id"],
            "scenario_id": node["scenario_id"],
            "field_code": node["field_code"],
            "endpoint": node["endpoint"],
            "surface_type": node["surface_type"],
            "surface_dist": node["surface_distance_m"],
            "termination": node["termination_status"],
            "maneuver": node["maneuver_status"],
            "qa_status": node["qa_status"],
            "blockers": ",".join(node["blocker_codes"]),
            "warnings": ",".join(node["warning_codes"]),
        }
        for key, value in values.items():
            if value is not None:
                feature.SetField(key, value)
        feature.SetGeometry(ogr_point_z(node["geometry"], terrain))
        node_layer.CreateFeature(feature)

    candidate_layer = datasource.CreateLayer("candidate_parameters", geom_type=ogr.wkbNone)
    candidate_columns = [
        "field_code",
        "theta_deg",
        "lambda",
        "estimated_mean_run_m",
        "along_p50_percent",
        "along_p90_percent",
        "along_p95_percent",
        "along_max_percent",
        "along_above_target_percent",
        "along_above_alert_percent",
        "cross_p95_percent",
        "cross_max_percent",
        "spacing_p05_m",
        "spacing_p50_m",
        "spacing_p95_m",
        "spacing_outside_tolerance_percent",
        "singular_percent",
        "deflection_p95_deg",
        "conservation_loss",
        "performance_loss",
        "harvestability_loss",
        "controlled_grade_loss",
        "balanced_loss",
    ]
    for column in candidate_columns:
        if column == "field_code":
            create_field(candidate_layer, column, ogr.OFTString, width=24)
        else:
            create_field(candidate_layer, column, ogr.OFTReal, precision=6)
    for _, row in candidates[candidate_columns].iterrows():
        feature = ogr.Feature(candidate_layer.GetLayerDefn())
        for column in candidate_columns:
            feature.SetField(column, str(row[column]) if column == "field_code" else float(row[column]))
        candidate_layer.CreateFeature(feature)

    datasource = None


def draw_polygon(ax: plt.Axes, geometry, color: str, linewidth: float = 0.8):
    for polygon in iter_polygons(geometry):
        exterior = np.asarray(polygon.exterior.coords)
        ax.plot(exterior[:, 0], exterior[:, 1], color=color, linewidth=linewidth)
        for ring in polygon.interiors:
            coordinates = np.asarray(ring.coords)
            ax.plot(coordinates[:, 0], coordinates[:, 1], color=color, linewidth=linewidth * 0.7)


def render_map(
    fields: list[dict],
    terrain: Terrain,
    line_records: list[dict],
    summaries: list[dict],
    output_path: Path = OUTPUT_MAP,
) -> None:
    columns = 4
    rows = math.ceil(len(SCENARIOS) / columns)
    figure, axes = plt.subplots(rows, columns, figsize=(5.6 * columns, 5.6 * rows), dpi=140, facecolor="#eef0ed")
    cmap = LinearSegmentedColormap.from_list("terrain", ["#d5e4d2", "#b9c98f", "#d8c58b", "#bba58f", "#e6e4dc"])
    transform = terrain.transform
    extent = [
        transform[0],
        transform[0] + transform[1] * terrain.width,
        transform[3] + transform[5] * terrain.height,
        transform[3],
    ]
    scenario_lookup = {scenario["id"]: scenario for scenario in SCENARIOS}
    summary_lookup = {}
    for summary in summaries:
        summary_lookup.setdefault(summary["scenario_id"], []).append(summary)

    for ax, scenario in zip(axes.flat, SCENARIOS):
        ax.imshow(terrain.elevation, extent=extent, origin="upper", cmap=cmap, alpha=0.88)
        records = [record for record in line_records if record["scenario"]["id"] == scenario["id"]]
        grade_cmap = LinearSegmentedColormap.from_list("grade", ["#16866f", "#e0ac3d", "#bc4337"])
        for record in records:
            coordinates = np.asarray(record["geometry"].coords)
            grade = min(record["metrics"]["absolute_grade_p95_percent"] / max(PARAMS.reference_alert_grade_percent, 0.1), 1.0)
            ax.plot(coordinates[:, 0], coordinates[:, 1], color=grade_cmap(grade), linewidth=0.34, alpha=0.82)
        for field in fields:
            draw_polygon(ax, field["geometry"], "#25342f", 1.0)
            draw_polygon(ax, field["usable"], "#ffffff", 0.55)
        scenario_summaries = summary_lookup.get(scenario["id"], [])
        total_km = sum(item["total_line_km"] or 0 for item in scenario_summaries)
        p50 = np.mean([item["segment_length_p50_m"] for item in scenario_summaries if item["segment_length_p50_m"] is not None])
        grade = np.mean(
            [item["absolute_grade_p95_weighted_percent"] for item in scenario_summaries if item["absolute_grade_p95_weighted_percent"] is not None]
        )
        efficiency = np.mean(
            [item["planning_field_efficiency_percent"] for item in scenario_summaries if item["planning_field_efficiency_percent"] is not None]
        )
        ax.set_title(
            f"{scenario['id']}  {scenario_lookup[scenario['id']]['name']}\n"
            f"{total_km:.1f} km | tiro P50 {p50:.0f} m | greide P95 pond. {grade:.2f}% | eficiência {efficiency:.1f}%",
            loc="left",
            fontsize=9,
            fontweight="bold",
            color="#1f2925",
        )
        ax.set_aspect("equal")
        ax.axis("off")

    for ax in axes.flat[len(SCENARIOS):]:
        ax.axis("off")

    figure.suptitle(
        "Primitivas geométricas de sulcação | E0, não são os cenários C1-C4",
        fontsize=16,
        fontweight="bold",
        color="#1f2925",
    )
    figure.text(
        0.5,
        0.02,
        "Verde: menor greide local  |  amarelo/vermelho: maior greide  |  "
        "Sem solo, chuva, carreadores, receptores e regra regional aprovada",
        ha="center",
        fontsize=9,
        color="#46544e",
    )
    figure.tight_layout(rect=[0.015, 0.045, 0.985, 0.95])
    figure.savefig(output_path, facecolor="#eef0ed")
    plt.close(figure)


def verify_outputs(
    line_records: list[dict],
    summaries: list[dict],
    gate_policy: CandidateGatePolicy,
) -> None:
    expected = len(SCENARIOS) * len(set(record["field"]["code"] for record in line_records))
    if len(summaries) != expected:
        raise RuntimeError(f"Expected {expected} scenario summaries, found {len(summaries)}.")
    if not line_records:
        raise RuntimeError("No sulcation lines were generated.")
    if any(not record["geometry"].is_valid for record in line_records):
        raise RuntimeError("At least one generated line is geometrically invalid.")
    if any(record["geometry"].length < PARAMS.minimum_segment_m - 1e-6 for record in line_records):
        raise RuntimeError("A generated line is shorter than the configured minimum.")
    # A static-radius failure makes that alternative ineligible, but the E0
    # package still publishes it as diagnostic geometry for comparison.  Stops
    # away from a physical edge, intersections, overlaps and loops remain hard
    # publication failures because they indicate a structurally invalid family.
    diagnostic_only_blockers = {"MINIMUM_RADIUS_VIOLATION"}
    failed_records = [
        record
        for record in line_records
        if set(record["continuity"].get("blocker_codes", [])) - diagnostic_only_blockers
    ]
    failed = [record["line_id"] for record in failed_records]
    if failed:
        blocker_counts: dict[str, int] = {}
        for record in failed_records:
            for code in record["continuity"].get("blocker_codes", []):
                blocker_counts[code] = blocker_counts.get(code, 0) + 1
        raise RuntimeError(
            f"Operational-continuity geometry failed for {len(failed)} lines; "
            f"blockers: {dict(sorted(blocker_counts.items()))}; first: {failed[:5]}"
        )
    if gate_policy.maximum_furrow_grade_percent is not None:
        failures = [
            record["line_id"]
            for record in line_records
            if record["metrics"]["absolute_grade_max_percent"]
            > gate_policy.maximum_furrow_grade_percent + 1e-9
        ]
        if failures:
            raise NoFeasibleCandidateError(
                f"generated longitudinal-grade lines; first {failures[:5]}",
                gate_policy,
            )
    if gate_policy.maximum_cross_slope_percent is not None:
        failures = [
            record["line_id"]
            for record in line_records
            if record["metrics"]["cross_grade_max_percent"]
            > gate_policy.maximum_cross_slope_percent + 1e-9
        ]
        if failures:
            raise NoFeasibleCandidateError(
                f"generated cross-slope lines; first {failures[:5]}",
                gate_policy,
            )
    for summary in summaries:
        coverage = summary["coverage_proxy_percent"]
        if coverage is None or not 70.0 <= coverage <= 130.0:
            raise RuntimeError(
                f"Coverage proxy outside the screening envelope for {summary['scenario_id']} / "
                f"{summary['field_code']}: {coverage}"
            )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--request",
        type=Path,
        help="Optional validated project-generation request JSON.",
    )
    parser.add_argument(
        "--field-id-column",
        help=(
            "Explicit FIELD_BOUNDARY identifier column. Required only when more than one "
            "attribute contains every scope.field_ids value."
        ),
    )
    parser.add_argument("--output-gpkg", type=Path, default=OUTPUT_GPKG)
    parser.add_argument("--output-map", type=Path, default=OUTPUT_MAP)
    parser.add_argument("--output-metrics", type=Path, default=OUTPUT_JSON)
    return parser.parse_args(argv)


def serialized_parameters() -> dict:
    return {
        key: (list(value) if isinstance(value, tuple) else value)
        for key, value in PARAMS.__dict__.items()
    }


def output_path_map() -> dict[str, Path]:
    return {
        "geopackage": OUTPUT_GPKG,
        "map": OUTPUT_MAP,
        "metrics": OUTPUT_JSON,
    }


def configure_request(
    request_path: Path | None,
) -> tuple[ResolvedProjectRequest | None, Path, dict, Path, dict, CandidateGatePolicy]:
    global PARAMS
    PARAMS = Parameters()
    if request_path is None:
        return (
            None,
            DTM,
            {
                "resolved_path": str(DTM.relative_to(REPO)),
                "path_resolution": "LEGACY_FIXED_PATH",
            },
            BOUNDARY,
            {
                "resolved_path": str(BOUNDARY.relative_to(REPO)),
                "path_resolution": "LEGACY_FIXED_PATH",
            },
            CandidateGatePolicy(),
        )

    request_context = load_project_request(request_path)
    if not request_context.is_e0:
        raise ContractError(
            "This generator implements only E0_TRIAGEM geometry. E1/E2/E3 requests require the "
            "operational, conservation and executive stages instead of a successful partial result."
        )
    PARAMS = replace(PARAMS, **request_context.engine_parameter_overrides())
    dtm_path, dtm_resolution = request_context.dtm_path()
    boundary_path, boundary_resolution = request_context.field_boundary_path()
    strict = False
    gate_policy = CandidateGatePolicy(
        allow_legacy_fallback=not strict,
        maximum_furrow_grade_percent=(
            min(
                float(request_context.value("conservation.max_furrow_grade_pct")),
                float(
                    request_context.conservative_operation_limit(
                        "fleet.max_longitudinal_grade_pct", "min"
                    )
                ),
            )
            if strict
            else None
        ),
        maximum_cross_slope_percent=(
            request_context.conservative_operation_limit("fleet.max_cross_slope_pct", "min")
            if strict else None
        ),
    )
    return (
        request_context,
        dtm_path,
        dtm_resolution,
        boundary_path,
        boundary_resolution,
        gate_policy,
    )


def write_terminal_result(result: dict, exit_code: int) -> int:
    DERIVED.mkdir(parents=True, exist_ok=True)
    temporary_json = OUTPUT_JSON.with_name(f".{OUTPUT_JSON.stem}.terminal{OUTPUT_JSON.suffix}")
    temporary_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_json.replace(OUTPUT_JSON)
    print(
        json.dumps(
            {
                "status": result["status"],
                "exit_code": exit_code,
                "metrics": display_path(OUTPUT_JSON),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return exit_code


def main(argv: list[str] | None = None) -> int:
    global OUTPUT_GPKG, OUTPUT_MAP, OUTPUT_JSON
    args = parse_args(argv)
    OUTPUT_GPKG = args.output_gpkg.resolve()
    OUTPUT_MAP = args.output_map.resolve()
    OUTPUT_JSON = args.output_metrics.resolve()
    for output in (OUTPUT_GPKG, OUTPUT_MAP, OUTPUT_JSON):
        output.parent.mkdir(parents=True, exist_ok=True)
    DERIVED.mkdir(parents=True, exist_ok=True)
    try:
        (
            request_context,
            dtm_path,
            dtm_resolution,
            boundary_path,
            boundary_resolution,
            gate_policy,
        ) = configure_request(args.request)
    except (ContractError, OSError, KeyError, TypeError, ValueError) as exc:
        print(f"INVALID_PROJECT_REQUEST: {exc}", file=sys.stderr)
        return 2

    if request_context is None:
        request_manifest = legacy_metrics_manifest(
            serialized_parameters(),
            dtm_path=dtm_path,
            boundary_path=boundary_path,
            output_paths=output_path_map(),
        )
    else:
        request_manifest = request_context.metrics_manifest(
            dtm_resolution=dtm_resolution,
            boundary_resolution=boundary_resolution,
            output_paths=output_path_map(),
        )

    if request_context is not None and request_context.blocks_this_generator:
        return write_terminal_result(
            {
                "status": "POWER_BARRIER_STAGE_REQUIRED",
                "source_files_unchanged": True,
                "purpose": "block operational generation until the supplied power-line barrier is clipped",
                "generation_request": request_manifest,
                "candidate_gate_policy": gate_policy.describe(),
                "outputs": {},
                "next_required_stage": (
                    "Buffer power_line_axis with its resolved exclusion half-width and split every worked, "
                    "hydraulic and movement segment before rerunning an operational request."
                ),
            },
            exit_code=4,
        )

    target_field_ids = (
        request_context.request["scope"]["field_ids"]
        if request_context is not None
        else None
    )
    requested_horizontal_crs = (
        request_context.value("terrain.horizontal_crs")
        if request_context is not None
        else None
    )
    try:
        contract_field_id_column = (
            boundary_resolution.get("id_field") if request_context is not None else None
        )
        if (
            request_context is not None
            and args.field_id_column is not None
            and args.field_id_column != contract_field_id_column
        ):
            raise RuntimeError(
                "--field-id-column cannot override FIELD_BOUNDARY.id_field in a validated request."
            )
        fields, projection, field_selection = read_fields(
            boundary_path=boundary_path,
            boundary_layer=boundary_resolution.get("layer_name"),
            target_field_ids=target_field_ids,
            field_id_column=args.field_id_column or contract_field_id_column,
            return_metadata=True,
        )
        spatial_reference_qa = validate_spatial_references(
            projection,
            dtm_path,
            requested_horizontal_crs,
        )
    except (RuntimeError, ValueError, TypeError) as exc:
        print(f"INVALID_PROJECT_SPATIAL_INPUT: {exc}", file=sys.stderr)
        return 2
    request_manifest["path_resolution"]["boundary"]["field_selection"] = field_selection
    request_manifest["spatial_reference_qa"] = spatial_reference_qa
    try:
        terrain = Terrain(dtm_path)
        if max(terrain.resolution_x, terrain.resolution_y) > PARAMS.row_spacing_m + 1e-9:
            return write_terminal_result(
                {
                    "status": "TERRAIN_RESOLUTION_TOO_COARSE_FOR_ROWS",
                    "source_files_unchanged": True,
                    "purpose": "reject a terrain grid that cannot support the configured row spacing",
                    "generation_request": request_manifest,
                    "terrain_resolution_m": {
                        "x": rounded(terrain.resolution_x, 6),
                        "y": rounded(terrain.resolution_y, 6),
                    },
                    "row_spacing_m": rounded(PARAMS.row_spacing_m, 6),
                    "outputs": {},
                },
                exit_code=2,
            )
        terrain_coverage_qa = {
            field["code"]: terrain.validate_geometry_coverage(field["usable"])
            for field in fields
        }
    except RuntimeError as exc:
        return write_terminal_result(
            {
                "status": "INVALID_PROJECT_DTM_COVERAGE",
                "source_files_unchanged": True,
                "purpose": "reject terrain that does not fully cover every usable work geometry",
                "generation_request": request_manifest,
                "candidate_gate_policy": gate_policy.describe(),
                "failure": str(exc),
                "outputs": {},
                "stale_output_warning": (
                    "Any pre-existing GeoPackage or map belongs to another successful run and is not "
                    "an output of this failed request."
                ),
            },
            exit_code=2,
        )
    request_manifest["terrain_coverage_qa"] = {
        "status": "PASS_ALL_USABLE_FIELDS_FULL_DTM_COVERAGE",
        "fields": terrain_coverage_qa,
    }
    masks = {field["code"]: terrain.raster_mask(field["usable"]) for field in fields}

    candidate_rows = []
    for field in fields:
        candidate_rows.extend(candidate_metrics(field, terrain, masks[field["code"]]))
    candidates = add_scores(pd.DataFrame(candidate_rows))
    try:
        selections = select_parameters(candidates, gate_policy)
    except NoFeasibleCandidateError as exc:
        return write_terminal_result(
            {
                "status": exc.code,
                "source_files_unchanged": True,
                "purpose": "reject a request whose candidate set does not pass its hard gates",
                "generation_request": request_manifest,
                "candidate_gate_policy": gate_policy.describe(),
                "failed_scope": exc.scope,
                "outputs": {},
                "stale_output_warning": (
                    "Any pre-existing GeoPackage or map was produced by another run and is not an output "
                    "of this failed request."
                ),
            },
            exit_code=3,
        )

    line_records, summaries = build_scenarios(fields, terrain, masks, selections, gate_policy)
    required_minimum_radius_m = (
        request_context.conservative_operation_limit("fleet.minimum_work_path_radius_m", "max")
        if request_context is not None
        else None
    )
    continuity = apply_continuity_diagnostics(
        line_records,
        summaries,
        fields,
        terrain,
        required_minimum_radius_m,
    )
    verify_outputs(line_records, summaries, gate_policy)
    temporary_gpkg = OUTPUT_GPKG.with_name(f".{OUTPUT_GPKG.stem}.staging{OUTPUT_GPKG.suffix}")
    temporary_map = OUTPUT_MAP.with_name(f".{OUTPUT_MAP.stem}.staging{OUTPUT_MAP.suffix}")
    for temporary in (temporary_gpkg, temporary_map):
        if temporary.exists():
            temporary.unlink()
    write_outputs(
        projection,
        terrain,
        line_records,
        summaries,
        candidates,
        continuity,
        output_path=temporary_gpkg,
    )
    render_map(fields, terrain, line_records, summaries, output_path=temporary_map)
    temporary_gpkg.replace(OUTPUT_GPKG)
    temporary_map.replace(OUTPUT_MAP)
    output_integrity = {
        "geopackage": file_integrity(OUTPUT_GPKG),
        "map": file_integrity(OUTPUT_MAP),
    }

    result = {
        "status": "E0_topographic_sulcation_geometry_screening",
        "source_files_unchanged": True,
        "purpose": "compare internal row-geometry strategies that feed conservation macros C1-C4",
        "portfolio_role": {
            "artifact_type": "geometric_precursor",
            "client_macros": [
                "C1_CURVA_EMBUTIDA",
                "C2_BASE_LARGA_PASSANTE",
                "C3_ESD",
                "C4_MISTO_POR_ZONA",
            ],
            "warning": "these E0 strategies are not conservation-system scenarios",
        },
        "method": {
            "status": "global_scalar_field_baseline",
            "candidate_space": "orientation sweep combined with elevation deformation",
            "line_family": "isolines of phi = planar_normal + lambda * elevation",
            "selection": "six exploratory objective strategies, not the final product taxonomy",
            "phase_spacing": "adaptive mean-spacing heuristic; not a normalized distance field",
            "multifield_phase_identity": (
                "row_global_id is emitted only for the E0F shared straight lattice; field-local "
                "guidance_id values never prove cross-field continuity"
            ),
            "operational_continuity": (
                "geometric invariant checked; maneuver authorization and fleet envelope remain pending"
            ),
        },
        "generation_request": request_manifest,
        "candidate_gate_policy": gate_policy.describe(),
        "maximum_output_delivery_level": "E0_TRIAGEM",
        "requested_level_achieved": (
            request_context is None or request_context.requested_delivery_level == "E0_TRIAGEM"
        ),
        "not_authorized_for": [
            "machine guidance",
            "executive agronomic design",
            "hydraulic approval",
            "construction staking",
        ],
        "parameters": serialized_parameters(),
        "parameter_policy": {
            "resolution_mode": request_manifest["mode"],
            "reference_target_grade_percent": "illustrative study value, not a universal agronomic rule",
            "reference_alert_grade_percent": "screening alert, not an approved limit",
            "headland_and_machine_assumptions": "replace after the real fleet profile is supplied",
        },
        "known_limitations": [
            "one global direction field per field; no topographic zone decomposition",
            "not yet the recursive hybrid-contour algorithm described in the technical literature",
            "adaptive phase-level steps improve mean density but do not guarantee local spacing along each row",
            "all internal holes are conservatively treated as obstacles but remain unclassified",
            "turns and the swept envelopes of the actual fleet are not simulated",
            "hydraulic receivers and physical conservation systems are not generated at E0",
        ],
        "operational_continuity_policy": continuity["policy"],
        "operational_continuity_invariants": [
            "analytical zone boundaries never authorize a work stop",
            "each E0 operational_run_id is one isolated physical segment",
            "every endpoint must reach a physical work edge",
            "crossing, overlap, touch, self-intersection and closed loops fail geometry",
            "headland work edges support termination but do not authorize a maneuver without fleet review",
            "unclassified obstacle edges require a break and do not authorize a maneuver",
            "static radius violations remain diagnostic and make the affected scenario ineligible",
        ],
        "missing_for_hydraulic_evaluation": [
            "complete upstream basin and downstream condition",
            "surveyed roads, carriers, culverts, terraces, channels and stable receivers",
            "soil profile, infiltration, erodibility, compaction and antecedent moisture",
            "design rainfall IDF, duration, return period and hyetograph",
            "coverage and roughness by crop stage",
            "approved regional rule pack and professional review",
        ],
        "fields": [
            {
                "code": field["code"],
                "gross_area_ha": rounded(field["gross_area_ha"], 4),
                "usable_area_ha": rounded(field["usable_area_ha"], 4),
            }
            for field in fields
        ],
        "scenario_definitions": SCENARIOS,
        "selected_parameters": {
            scenario_id: {
                field_code: {
                    "theta_deg": rounded(row["theta_deg"], 3),
                    "lambda": rounded(row["lambda"], 3),
                }
                for field_code, row in by_field.items()
            }
            for scenario_id, by_field in selections.items()
        },
        "scenario_summary": summaries,
        "outputs": {
            "geopackage": display_path(OUTPUT_GPKG),
            "map": display_path(OUTPUT_MAP),
        },
        "output_integrity": output_integrity,
    }
    temporary_json = OUTPUT_JSON.with_name(f".{OUTPUT_JSON.stem}.staging{OUTPUT_JSON.suffix}")
    temporary_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary_json.replace(OUTPUT_JSON)
    print(
        json.dumps(
            {
                "status": result["status"],
                "exit_code": 0,
                "outputs": result["outputs"],
                "metrics": display_path(OUTPUT_JSON),
                "line_count": len(line_records),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
