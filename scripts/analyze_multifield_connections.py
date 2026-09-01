"""Screen E0 row connections between adjacent fields.

Run with the QGIS Python environment:

    $env:TERRAFLUX_FIELD_CODES = "FIELD-A,FIELD-B"
    $env:TERRAFLUX_TARGET_EPSG = "31982"
    & 'C:\\Program Files\\QGIS 3.32.1\\bin\\python-qgis.bat' `
      '.\\scripts\\analyze_multifield_connections.py'

The output is deliberately UNCONFIRMED. Field adjacency does not prove that an
internal boundary is a carrier, nor that machinery may cross it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np
import shapely
from osgeo import gdal, ogr, osr
from scipy import ndimage
from shapely import wkb
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import nearest_points, unary_union
from shapely.prepared import prep


gdal.UseExceptions()
ogr.UseExceptions()

REPO = Path(__file__).resolve().parents[1]
DATASET = REPO / "dataset"
DERIVED = DATASET / "derived"
BOUNDARY = DATASET / "Contorno.shp"
DTM = DERIVED / "dtm_1m.tif"
SCENARIO_GPKG = DERIVED / "sulcation_scenarios.gpkg"
SCENARIO_METRICS = DERIVED / "sulcation_scenario_metrics.json"
OUTPUT = DERIVED / "multifield_connection_screening.json"
OUTPUT_GPKG = DERIVED / "multifield_connection_screening.gpkg"

SCENARIO_ID = "E0F_EIXO_COMUM"
PORTAL_SCENARIO_ID = "E0G_PORTAL_NORMAL_PHASE_LOCKED"
FIELD_CODES = tuple(
    value.strip()
    for value in os.environ.get(
        "TERRAFLUX_FIELD_CODES", "TALHAO-DEMO-A,TALHAO-DEMO-B"
    ).split(",")
    if value.strip()
)
TARGET_EPSG = int(os.environ.get("TERRAFLUX_TARGET_EPSG", "31982"))
require_two_fields = len(FIELD_CODES) == 2 and FIELD_CODES[0] != FIELD_CODES[1]
if not require_two_fields:
    raise RuntimeError("TERRAFLUX_FIELD_CODES must contain two distinct field identifiers.")
ROW_SPACING_M = 1.50
HEADLAND_M = 12.0
OBSTACLE_CLEARANCE_M = 3.0
MINIMUM_ANALYTIC_SEGMENT_M = 8.0
MAX_INFERRED_CONNECTOR_M = 100.0
OTHER_FIELD_PROXIMITY_M = HEADLAND_M + 3.5
BOUNDARY_MATCH_TOLERANCE_M = 0.01
TERRAIN_SAMPLE_M = 1.0
TERRAIN_SMOOTHING_SIGMA_M = 4.0
ROW_PROFILE_SAMPLE_M = 3.0
REFERENCE_GRADE_ALERT_PERCENT = 5.0
ASSUMED_TURN_SECONDS = 22.0
TRAVEL_SPEEDS_KMH = (5.0, 8.0, 10.0, 12.0, 15.0)
MIN_SEGMENT_SENSITIVITY_M = (8.0, 15.0, 20.0, 30.0, 40.0, 50.0, 75.0, 100.0)
PORTAL_ORIENTATION_DELTAS_DEG = (-5.0, -2.5, 0.0, 2.5, 5.0)
PORTAL_PHASE_DELTAS_M = (0.0, 0.375, 0.75, 1.125)
FIXED_GPKG_LAST_CHANGE = "2000-01-01T00:00:00.000Z"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def rounded(value: float | int | None, digits: int = 3):
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(number, digits)


def percentile(values, level: float) -> float | None:
    array = np.asarray(list(values), dtype=float)
    array = array[np.isfinite(array)]
    if not array.size:
        return None
    return float(np.percentile(array, level))


def percentile_summary(values, digits: int = 3) -> dict:
    return {
        "minimum": rounded(percentile(values, 0), digits),
        "p05": rounded(percentile(values, 5), digits),
        "p50": rounded(percentile(values, 50), digits),
        "p95": rounded(percentile(values, 95), digits),
        "maximum": rounded(percentile(values, 100), digits),
    }


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def file_record(path: Path) -> dict:
    return {
        "path": path.relative_to(REPO).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def iter_polygons(geometry):
    if geometry.geom_type == "Polygon":
        yield geometry
    elif geometry.geom_type == "MultiPolygon":
        yield from geometry.geoms
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from iter_polygons(part)


def make_usable_geometry(geometry):
    parts = []
    for polygon in iter_polygons(geometry):
        outer = Polygon(polygon.exterior).buffer(-HEADLAND_M, join_style=2)
        holes = [Polygon(ring).buffer(OBSTACLE_CLEARANCE_M) for ring in polygon.interiors]
        usable = outer.difference(unary_union(holes)) if holes else outer
        parts.extend(part for part in iter_polygons(usable) if not part.is_empty)
    require(parts, "The E0 buffers removed all usable geometry from a field.")
    return unary_union(parts)


def read_fields() -> tuple[dict, str]:
    source = ogr.Open(str(BOUNDARY))
    require(source is not None, f"Could not open {BOUNDARY}")
    layer = source.GetLayer(0)
    spatial_ref = layer.GetSpatialRef()
    geometries_by_code = defaultdict(list)
    for feature in layer:
        code = feature.GetFieldAsString("cd_upnivel")
        geometry = wkb.loads(bytes(feature.GetGeometryRef().ExportToWkb()))
        geometries_by_code[code].append(geometry)
    fields = {}
    for code, geometries in geometries_by_code.items():
        geometry = unary_union(geometries)
        require(not geometry.is_empty and geometry.is_valid, f"Invalid merged field geometry: {code}")
        fields[code] = {"gross": geometry, "usable": make_usable_geometry(geometry)}
    require(set(FIELD_CODES).issubset(fields), f"Expected fields {FIELD_CODES}, found {sorted(fields)}")
    crs = spatial_ref.GetAuthorityCode(None) if spatial_ref is not None else None
    return fields, f"EPSG:{crs}" if crs else "unknown"


class Terrain:
    def __init__(self, path: Path):
        dataset = gdal.Open(str(path))
        require(dataset is not None, f"Could not open {path}")
        self.transform = dataset.GetGeoTransform()
        require(
            math.isclose(self.transform[2], 0.0, abs_tol=1e-12)
            and math.isclose(self.transform[4], 0.0, abs_tol=1e-12),
            "Rotated DTM geotransforms are not supported.",
        )
        self.width = dataset.RasterXSize
        self.height = dataset.RasterYSize
        band = dataset.GetRasterBand(1)
        raw = band.ReadAsArray().astype("float64")
        nodata = band.GetNoDataValue()
        valid = np.isfinite(raw)
        if nodata is not None:
            valid &= ~np.isclose(raw, nodata)
        sigma_y = TERRAIN_SMOOTHING_SIGMA_M / abs(self.transform[5])
        sigma_x = TERRAIN_SMOOTHING_SIGMA_M / abs(self.transform[1])
        weights = ndimage.gaussian_filter(valid.astype(float), (sigma_y, sigma_x), mode="nearest")
        values = ndimage.gaussian_filter(
            np.where(valid, raw, 0.0), (sigma_y, sigma_x), mode="nearest"
        )
        self.values = np.divide(
            values,
            weights,
            out=np.full_like(values, np.nan),
            where=weights > 1e-6,
        )
        self.valid = valid
        x0 = self.transform[0]
        x1 = x0 + self.width * self.transform[1]
        y0 = self.transform[3]
        y1 = y0 + self.height * self.transform[5]
        self.footprint = box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self.dataset = dataset

    def sample(self, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
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
        require(bool(np.all(inside)), "A terrain profile leaves the DTM footprint.")
        valid_samples = ndimage.map_coordinates(
            self.valid.astype(np.uint8), [rows, columns], order=0, mode="nearest"
        )
        require(bool(np.all(valid_samples == 1)), "A terrain profile crosses DTM nodata.")
        return ndimage.map_coordinates(self.values, [rows, columns], order=1, mode="nearest")

    def connector_metrics(self, start: Point, end: Point) -> dict:
        distance = start.distance(end)
        count = max(2, int(math.ceil(distance / TERRAIN_SAMPLE_M)) + 1)
        xs = np.linspace(start.x, end.x, count)
        ys = np.linspace(start.y, end.y, count)
        zs = self.sample(xs, ys)
        require(np.isfinite(zs).all(), "A connector crosses DTM nodata.")
        spacing = distance / (count - 1)
        smoothed = ndimage.gaussian_filter1d(zs, 2.0, mode="nearest")
        grades = np.abs(np.diff(smoothed)) / max(spacing, 1e-9) * 100.0
        return {
            "endpoint_absolute_grade_percent": rounded(abs(zs[-1] - zs[0]) / distance * 100.0),
            "local_absolute_grade_p95_percent": rounded(percentile(grades, 95)),
            "local_absolute_grade_max_percent": rounded(float(grades.max())),
            "elevation_range_m": rounded(float(np.ptp(zs))),
        }

    def row_metrics(self, line) -> dict:
        count = max(2, int(math.ceil(line.length / ROW_PROFILE_SAMPLE_M)) + 1)
        distances = np.linspace(0.0, line.length, count)
        coordinates = np.asarray(
            [line.interpolate(float(distance)).coords[0] for distance in distances],
            dtype=float,
        )
        zs = self.sample(coordinates[:, 0], coordinates[:, 1])
        require(np.isfinite(zs).all(), "A paired crop segment crosses DTM nodata.")
        smoothed = ndimage.gaussian_filter1d(
            zs,
            max(0.75, 5.0 / ROW_PROFILE_SAMPLE_M),
            mode="nearest",
        )
        spacing = line.length / (count - 1)
        grades = np.abs(np.diff(smoothed)) / max(spacing, 1e-9) * 100.0
        return {
            "endpoint_absolute_grade_percent": rounded(
                abs(zs[-1] - zs[0]) / max(line.length, 1e-9) * 100.0
            ),
            "local_absolute_grade_p95_percent": rounded(percentile(grades, 95)),
            "local_absolute_grade_max_percent": rounded(float(grades.max())),
        }


def endpoint_pair(line_a, line_b):
    candidates = []
    for a_index, a_coord in enumerate((line_a.coords[0], line_a.coords[-1])):
        point_a = Point(float(a_coord[0]), float(a_coord[1]))
        for b_index, b_coord in enumerate((line_b.coords[0], line_b.coords[-1])):
            point_b = Point(float(b_coord[0]), float(b_coord[1]))
            candidates.append((point_a.distance(point_b), a_index, point_a, b_index, point_b))
    return min(candidates, key=lambda item: item[0])


def read_e0f_lines() -> tuple[dict, dict]:
    source = ogr.Open(str(SCENARIO_GPKG))
    require(source is not None, f"Could not open {SCENARIO_GPKG}")
    layer = source.GetLayerByName("sulcation_lines")
    require(layer is not None, "Missing sulcation_lines layer.")
    layer.SetAttributeFilter(f"scenario_id='{SCENARIO_ID}'")
    grouped = defaultdict(lambda: defaultdict(list))
    feature_counts = defaultdict(int)
    shared_theta_values = []
    for feature in layer:
        field_code = feature.GetFieldAsString("field_code")
        record = {
            "line_id": feature.GetFieldAsString("line_id"),
            "guidance_id": feature.GetFieldAsString("guidance_id"),
            "geometry": wkb.loads(bytes(feature.GetGeometryRef().ExportToWkb())),
            "theta_deg": feature.GetFieldAsDouble("theta_deg"),
            "lambda": feature.GetFieldAsDouble("lambda"),
            "row_global_id": feature.GetField("row_global_id"),
            "phase_level_m": feature.GetFieldAsDouble("phase_level_m"),
            "phase_scope": feature.GetFieldAsString("phase_scope"),
        }
        require(
            record["phase_scope"] == "GLOBAL_SHARED_STRAIGHT_LATTICE",
            f"E0F line is not declared on a shared phase lattice: {record['line_id']}",
        )
        require(
            feature.GetFieldAsString("release") == "E0_SCREENING",
            f"E0F line has an unexpected release: {record['line_id']}",
        )
        require(
            feature.GetFieldAsString("topology_status") == "PASS"
            and not feature.GetFieldAsString("blocker_codes"),
            f"E0F line failed its source continuity contract: {record['line_id']}",
        )
        require(
            feature.GetFieldAsString("continuity_status").startswith("E0_GEOMETRY_ONLY"),
            f"E0F line has an unexpected continuity status: {record['line_id']}",
        )
        require(
            math.isclose(float(record["lambda"]), 0.0, abs_tol=1e-12),
            f"E0F line is not straight: {record['line_id']}",
        )
        require(
            record["row_global_id"] is not None,
            f"E0F line lacks row_global_id: {record['line_id']}",
        )
        require(
            abs(record["phase_level_m"] - int(record["row_global_id"]) * ROW_SPACING_M)
            <= 1e-6,
            f"E0F global row and phase disagree: {record['line_id']}",
        )
        grouped[field_code][int(record["row_global_id"])].append(record)
        feature_counts[field_code] += 1
        shared_theta_values.append(normalize_axis_angle(float(record["theta_deg"])))
    require(set(FIELD_CODES).issubset(grouped), "The E0F scenario does not cover both fields.")
    require(shared_theta_values, "The E0F source has no lines.")
    reference_theta = shared_theta_values[0]
    require(
        all(
            min(abs(theta - reference_theta), 180.0 - abs(theta - reference_theta)) <= 1e-6
            for theta in shared_theta_values
        ),
        "E0F features do not share one global axial orientation.",
    )

    summary_layer = source.GetLayerByName("scenario_summary")
    require(summary_layer is not None, "Missing scenario_summary layer.")
    summary_layer.SetAttributeFilter(f"scenario_id='{SCENARIO_ID}'")
    summaries = {}
    for feature in summary_layer:
        code = feature.GetFieldAsString("field_code")
        summaries[code] = {
            "theta_deg": feature.GetFieldAsDouble("theta_deg"),
            "lambda": feature.GetFieldAsDouble("lambda"),
            "segment_count": feature.GetFieldAsInteger("segment_count"),
            "guidance_group_count": feature.GetFieldAsInteger("guidance_group_count"),
            "total_line_km": feature.GetFieldAsDouble("total_line_km"),
            "planning_field_efficiency_percent": feature.GetFieldAsDouble(
                "planning_field_efficiency_percent"
            ),
        }
    require(set(FIELD_CODES).issubset(summaries), "Missing E0F field summaries.")
    for code in FIELD_CODES:
        require(feature_counts[code] == summaries[code]["segment_count"], f"Count mismatch for {code}")
    return grouped, summaries


def infer_interface(fields: dict) -> dict:
    field_a = fields[FIELD_CODES[0]]
    field_b = fields[FIELD_CODES[1]]
    gross_a = field_a["gross"]
    gross_b = field_b["gross"]
    usable_a = field_a["usable"]
    usable_b = field_b["usable"]
    point_a, point_b = nearest_points(usable_a, usable_b)
    dx = point_b.x - point_a.x
    dy = point_b.y - point_a.y
    crossing_direction_deg = math.degrees(math.atan2(dy, dx)) % 180.0
    boundary_tangent_deg = (crossing_direction_deg + 90.0) % 180.0
    near_a = gross_a.boundary.intersection(gross_b.boundary.buffer(BOUNDARY_MATCH_TOLERANCE_M)).length
    near_b = gross_b.boundary.intersection(gross_a.boundary.buffer(BOUNDARY_MATCH_TOLERANCE_M)).length
    return {
        "relationship": "ADJACENT_TOPOLOGY_UNCONFIRMED_AS_CARRIER",
        "gross_polygon_distance_m": rounded(gross_a.distance(gross_b), 6),
        "gross_overlap_area_m2": rounded(gross_a.intersection(gross_b).area, 6),
        "exact_common_boundary_length_m": rounded(gross_a.boundary.intersection(gross_b.boundary).length),
        "near_common_boundary": {
            "matching_tolerance_m": BOUNDARY_MATCH_TOLERANCE_M,
            "field_a_length_m": rounded(near_a),
            "field_b_length_m": rounded(near_b),
            "mean_length_m": rounded((near_a + near_b) / 2.0),
        },
        "e0_independent_field_buffer": {
            "headland_each_side_m": HEADLAND_M,
            "theoretical_normal_gap_m": 2.0 * HEADLAND_M,
            "measured_minimum_usable_gap_m": rounded(usable_a.distance(usable_b), 6),
            "warning": "The E0 buffer is applied to each field independently and is not a surveyed carrier width.",
        },
        "local_shortest_connection_reference": {
            "field_a_xy": [rounded(point_a.x), rounded(point_a.y)],
            "field_b_xy": [rounded(point_b.x), rounded(point_b.y)],
            "direction_from_east_counterclockwise_deg": rounded(crossing_direction_deg),
            "inferred_local_boundary_tangent_deg": rounded(boundary_tangent_deg),
            "status": "REFERENCE_ONLY_NOT_A_GENERATED_PORTAL",
        },
    }


def normalize_axis_angle(angle_deg: float) -> float:
    return angle_deg % 180.0


def analytic_straight_rows(
    geometry,
    extent,
    theta_deg: float,
    phase_delta_m: float,
    field_code: str,
) -> dict:
    """Clip a globally phased straight-row lattice without raster contouring."""
    theta = math.radians(theta_deg)
    tangent_x, tangent_y = math.cos(theta), math.sin(theta)
    normal_x, normal_y = -tangent_y, tangent_x
    envelope_coordinates = np.asarray(extent.envelope.exterior.coords, dtype=float)
    normal_projection = (
        envelope_coordinates[:, 0] * normal_x + envelope_coordinates[:, 1] * normal_y
    )
    tangent_projection = (
        envelope_coordinates[:, 0] * tangent_x + envelope_coordinates[:, 1] * tangent_y
    )
    first_row_id = math.floor((float(normal_projection.min()) - phase_delta_m) / ROW_SPACING_M)
    last_row_id = math.ceil((float(normal_projection.max()) - phase_delta_m) / ROW_SPACING_M)
    row_ids = np.arange(first_row_id, last_row_id + 1, dtype=np.int64)
    levels = row_ids.astype(float) * ROW_SPACING_M + phase_delta_m
    center_tangent = (float(tangent_projection.min()) + float(tangent_projection.max())) / 2.0
    half_span = (float(tangent_projection.max()) - float(tangent_projection.min())) / 2.0 + 50.0

    base_x = levels * normal_x + center_tangent * tangent_x
    base_y = levels * normal_y + center_tangent * tangent_y
    coordinates = np.empty((row_ids.size, 2, 2), dtype=float)
    coordinates[:, 0, 0] = base_x - half_span * tangent_x
    coordinates[:, 0, 1] = base_y - half_span * tangent_y
    coordinates[:, 1, 0] = base_x + half_span * tangent_x
    coordinates[:, 1, 1] = base_y + half_span * tangent_y

    lattice = shapely.linestrings(coordinates)
    clipped = shapely.intersection(lattice, geometry)
    parts, source_indexes = shapely.get_parts(clipped, return_index=True)
    type_ids = shapely.get_type_id(parts)
    lengths = shapely.length(parts)
    keep = (type_ids == 1) & (lengths >= MINIMUM_ANALYTIC_SEGMENT_M)

    grouped = defaultdict(list)
    segment_indexes = defaultdict(int)
    for part, source_index in zip(parts[keep], source_indexes[keep]):
        row_id = int(row_ids[int(source_index)])
        segment_index = segment_indexes[row_id]
        segment_indexes[row_id] += 1
        grouped[row_id].append(
            {
                "line_id": (
                    f"{PORTAL_SCENARIO_ID}:{field_code}:R{row_id}:S{segment_index}"
                ),
                "row_global_id": row_id,
                "geometry": part,
                "theta_deg": theta_deg,
                "lambda": 0.0,
            }
        )
    return dict(grouped)


def analytic_field_summaries(grouped: dict, theta_deg: float, phase_delta_m: float) -> dict:
    summaries = {}
    for code in FIELD_CODES:
        records = [record for rows in grouped[code].values() for record in rows]
        summaries[code] = {
            "theta_deg": rounded(theta_deg, 6),
            "lambda": 0.0,
            "row_spacing_m": ROW_SPACING_M,
            "global_phase_offset_m": phase_delta_m,
            "segment_count": len(records),
            "guidance_group_count": len(grouped[code]),
            "total_line_km": rounded(
                sum(record["geometry"].length for record in records) / 1000.0
            ),
        }
    return summaries


def infer_analytic_connections(
    fields: dict,
    grouped: dict,
    terrain: Terrain | None,
    scenario_id: str,
) -> list[dict]:
    code_a, code_b = FIELD_CODES
    gross_a = fields[code_a]["gross"]
    gross_b = fields[code_b]["gross"]
    gross_union = unary_union([gross_a, gross_b])
    prepared_union = prep(gross_union)
    prepared_a_proximity = prep(gross_a.buffer(OTHER_FIELD_PROXIMITY_M))
    prepared_b_proximity = prep(gross_b.buffer(OTHER_FIELD_PROXIMITY_M))
    shared_rows = sorted(set(grouped[code_a]) & set(grouped[code_b]))
    connections = []

    for row_global_id in shared_rows:
        best = None
        for row_a in grouped[code_a][row_global_id]:
            for row_b in grouped[code_b][row_global_id]:
                pair = endpoint_pair(row_a["geometry"], row_b["geometry"])
                if best is None or pair[0] < best[0]:
                    best = (*pair, row_a, row_b)
        require(best is not None, f"No line pair found for row {row_global_id}")
        gap_m, a_endpoint_index, point_a, b_endpoint_index, point_b, row_a, row_b = best
        connector = LineString([point_a, point_b])
        inside_union = prepared_union.covers(connector)
        faces_other_field = (
            prepared_b_proximity.covers(point_a)
            and prepared_a_proximity.covers(point_b)
        )
        within_screening_distance = gap_m <= MAX_INFERRED_CONNECTOR_M
        if not (inside_union and faces_other_field and within_screening_distance):
            continue

        terrain_metrics = terrain.connector_metrics(point_a, point_b) if terrain else None
        crossing_angle = math.degrees(math.asin(min(1.0, 2.0 * HEADLAND_M / max(gap_m, 1e-9))))
        connection = {
            "portal_id": f"UNCONFIRMED:{scenario_id}:R{row_global_id}",
            "status": "UNCONFIRMED",
            "row_global_id": row_global_id,
            "field_a": {
                "code": code_a,
                "line_id": row_a["line_id"],
                "endpoint_index": a_endpoint_index,
                "xy": [float(point_a.x), float(point_a.y)],
                "paired_crop_segment_length_m": rounded(row_a["geometry"].length),
            },
            "field_b": {
                "code": code_b,
                "line_id": row_b["line_id"],
                "endpoint_index": b_endpoint_index,
                "xy": [float(point_b.x), float(point_b.y)],
                "paired_crop_segment_length_m": rounded(row_b["geometry"].length),
            },
            "connector_gap_m": rounded(gap_m),
            "acute_crossing_angle_proxy_deg": rounded(crossing_angle),
        }
        if terrain_metrics is not None:
            connection["terrain"] = terrain_metrics
            connection["field_a"]["paired_crop_terrain"] = terrain.row_metrics(
                row_a["geometry"]
            )
            connection["field_b"]["paired_crop_terrain"] = terrain.row_metrics(
                row_b["geometry"]
            )
            connection["screening_grade_alert_exceeded"] = (
                terrain_metrics["local_absolute_grade_p95_percent"]
                > REFERENCE_GRADE_ALERT_PERCENT
            )
        connections.append(connection)
    return connections


def build_portal_normal_trial(fields: dict, interface: dict, terrain: Terrain) -> dict:
    extent = unary_union([fields[code]["usable"] for code in FIELD_CODES])
    local_normal_deg = interface["local_shortest_connection_reference"][
        "direction_from_east_counterclockwise_deg"
    ]
    sweep = []
    trial_cache = {}
    for angular_delta_deg in PORTAL_ORIENTATION_DELTAS_DEG:
        theta_deg = normalize_axis_angle(local_normal_deg + angular_delta_deg)
        for phase_delta_m in PORTAL_PHASE_DELTAS_M:
            grouped = {
                code: analytic_straight_rows(
                    fields[code]["usable"], extent, theta_deg, phase_delta_m, code
                )
                for code in FIELD_CODES
            }
            connections = infer_analytic_connections(
                fields,
                grouped,
                terrain=None,
                scenario_id=PORTAL_SCENARIO_ID,
            )
            gaps = [item["connector_gap_m"] for item in connections]
            paired_crop_km = sum(
                item["field_a"]["paired_crop_segment_length_m"]
                + item["field_b"]["paired_crop_segment_length_m"]
                for item in connections
            ) / 1000.0
            record = {
                "angular_delta_from_local_normal_deg": angular_delta_deg,
                "phase_delta_m": phase_delta_m,
                "theta_from_east_counterclockwise_deg": rounded(theta_deg, 6),
                "geometric_pair_count": len(connections),
                "gap_m": percentile_summary(gaps),
                "paired_crop_length_km": rounded(paired_crop_km),
                "field_segment_counts": {
                    code: sum(len(rows) for rows in grouped[code].values())
                    for code in FIELD_CODES
                },
                "field_guidance_group_counts": {
                    code: len(grouped[code]) for code in FIELD_CODES
                },
            }
            sweep.append(record)
            trial_cache[(angular_delta_deg, phase_delta_m)] = (
                theta_deg,
                grouped,
                connections,
                paired_crop_km,
            )

    def rank(record: dict):
        median_gap = record["gap_m"]["p50"]
        return (
            record["geometric_pair_count"],
            record["paired_crop_length_km"],
            -float(median_gap) if median_gap is not None else -math.inf,
            -abs(record["angular_delta_from_local_normal_deg"]),
            -record["phase_delta_m"],
        )

    selected_record = max(sweep, key=rank)
    selected_key = (
        selected_record["angular_delta_from_local_normal_deg"],
        selected_record["phase_delta_m"],
    )
    selected_theta, grouped, _, _ = trial_cache[selected_key]
    connections = infer_analytic_connections(
        fields,
        grouped,
        terrain=terrain,
        scenario_id=PORTAL_SCENARIO_ID,
    )
    summaries = analytic_field_summaries(
        grouped,
        selected_theta,
        selected_record["phase_delta_m"],
    )
    return {
        "scenario_id": PORTAL_SCENARIO_ID,
        "status": "UNCONFIRMED",
        "design_behavior": "LOCAL_PORTAL_NORMAL_AXIS_WITH_GLOBAL_1P50M_PHASE",
        "method": {
            "geometry": "analytic straight lines clipped with vector intersections",
            "scalar_field": "phi = -sin(theta) * x + cos(theta) * y",
            "row_global_id": (
                "integer level where phi = row_global_id * 1.50 m + selected phase delta"
            ),
            "global_phase_definition": (
                "one shared phase delta in [0, 1.50 m) for both fields; no per-field phase reset"
            ),
            "local_normal_reference_deg": local_normal_deg,
            "orientation_delta_candidates_deg": list(PORTAL_ORIENTATION_DELTAS_DEG),
            "phase_delta_candidates_m": list(PORTAL_PHASE_DELTAS_M),
            "selection_rule": (
                "rank only the declared narrow orientation/phase sample by geometric pair count, "
                "paired crop length and gap; this is not a global or operational optimum"
            ),
        },
        "orientation_sweep": sweep,
        "selected": selected_record,
        "field_summaries": summaries,
        "_selected_grouped_rows": grouped,
        "connections": connections,
    }


def infer_e0f_connections(fields: dict, grouped: dict, terrain: Terrain) -> list[dict]:
    code_a, code_b = FIELD_CODES
    gross_a = fields[code_a]["gross"]
    gross_b = fields[code_b]["gross"]
    gross_union = unary_union([gross_a, gross_b])
    prepared_union = prep(gross_union)
    prepared_a_proximity = prep(gross_a.buffer(OTHER_FIELD_PROXIMITY_M))
    prepared_b_proximity = prep(gross_b.buffer(OTHER_FIELD_PROXIMITY_M))
    shared_rows = sorted(set(grouped[code_a]) & set(grouped[code_b]))
    connections = []

    for row_global_id in shared_rows:
        best = None
        for row_a in grouped[code_a][row_global_id]:
            for row_b in grouped[code_b][row_global_id]:
                pair = endpoint_pair(row_a["geometry"], row_b["geometry"])
                if best is None or pair[0] < best[0]:
                    best = (*pair, row_a, row_b)
        require(best is not None, f"No line pair found for global row {row_global_id}")
        gap_m, a_endpoint_index, point_a, b_endpoint_index, point_b, row_a, row_b = best
        connector = LineString([point_a, point_b])
        inside_union = prepared_union.covers(connector)
        faces_other_field = (
            prepared_b_proximity.covers(point_a)
            and prepared_a_proximity.covers(point_b)
        )
        within_screening_distance = gap_m <= MAX_INFERRED_CONNECTOR_M
        if not (inside_union and faces_other_field and within_screening_distance):
            continue
        terrain_metrics = terrain.connector_metrics(point_a, point_b)
        crossing_angle = math.degrees(math.asin(min(1.0, 2.0 * HEADLAND_M / max(gap_m, 1e-9))))
        connections.append(
            {
                "portal_id": f"UNCONFIRMED:{SCENARIO_ID}:R{row_global_id}",
                "status": "UNCONFIRMED",
                "row_global_id": row_global_id,
                "field_a": {
                    "code": code_a,
                    "line_id": row_a["line_id"],
                    "endpoint_index": a_endpoint_index,
                    "xy": [float(point_a.x), float(point_a.y)],
                    "paired_crop_segment_length_m": rounded(row_a["geometry"].length),
                    "paired_crop_terrain": terrain.row_metrics(row_a["geometry"]),
                },
                "field_b": {
                    "code": code_b,
                    "line_id": row_b["line_id"],
                    "endpoint_index": b_endpoint_index,
                    "xy": [float(point_b.x), float(point_b.y)],
                    "paired_crop_segment_length_m": rounded(row_b["geometry"].length),
                    "paired_crop_terrain": terrain.row_metrics(row_b["geometry"]),
                },
                "connector_gap_m": rounded(gap_m),
                "acute_crossing_angle_proxy_deg": rounded(crossing_angle),
                "terrain": terrain_metrics,
                "screening_grade_alert_exceeded": (
                    terrain_metrics["local_absolute_grade_p95_percent"]
                    > REFERENCE_GRADE_ALERT_PERCENT
                ),
            }
        )
    return connections


def summarize_connections(connections: list[dict]) -> dict:
    if not connections:
        empty = percentile_summary([])
        return {
            "inferred_connection_pair_count": 0,
            "inferred_input_segment_count": 0,
            "gap_m": empty,
            "acute_crossing_angle_proxy_deg": empty,
            "terrain_endpoint_absolute_grade_percent": empty,
            "terrain_local_absolute_grade_p95_percent": empty,
            "terrain_local_absolute_grade_max_percent": empty,
            "connections_exceeding_reference_grade_alert": 0,
            "status_detail": "NO_GEOMETRIC_PAIR_WITHIN_SCREENING_GATES",
        }
    gap_values = [item["connector_gap_m"] for item in connections]
    endpoint_grades = [item["terrain"]["endpoint_absolute_grade_percent"] for item in connections]
    p95_grades = [item["terrain"]["local_absolute_grade_p95_percent"] for item in connections]
    max_grades = [item["terrain"]["local_absolute_grade_max_percent"] for item in connections]
    crossing_angles = [item["acute_crossing_angle_proxy_deg"] for item in connections]
    return {
        "inferred_connection_pair_count": len(connections),
        "inferred_input_segment_count": 2 * len(connections),
        "gap_m": percentile_summary(gap_values),
        "acute_crossing_angle_proxy_deg": percentile_summary(crossing_angles),
        "terrain_endpoint_absolute_grade_percent": percentile_summary(endpoint_grades),
        "terrain_local_absolute_grade_p95_percent": percentile_summary(p95_grades),
        "terrain_local_absolute_grade_max_percent": percentile_summary(max_grades),
        "connections_exceeding_reference_grade_alert": sum(
            item["screening_grade_alert_exceeded"] for item in connections
        ),
    }


def operational_screening(connections: list[dict], summaries: dict) -> dict:
    total_groups = sum(summary["guidance_group_count"] for summary in summaries.values())
    merge_count = len(connections)
    total_gap_m = sum(item["connector_gap_m"] for item in connections)
    gross_turn_seconds = merge_count * ASSUMED_TURN_SECONDS
    speed_cases = []
    for speed in TRAVEL_SPEEDS_KMH:
        connector_seconds = total_gap_m / (speed / 3.6)
        speed_cases.append(
            {
                "connector_travel_speed_kmh": speed,
                "connector_travel_minutes": rounded(connector_seconds / 60.0),
                "net_minutes_before_lift_safety_and_route_penalties": rounded(
                    (gross_turn_seconds - connector_seconds) / 60.0
                ),
                "positive_pair_count_before_other_penalties": sum(
                    ASSUMED_TURN_SECONDS > item["connector_gap_m"] / (speed / 3.6)
                    for item in connections
                ),
            }
        )

    sensitivity = []
    for minimum_length in MIN_SEGMENT_SENSITIVITY_M:
        eligible = [
            item
            for item in connections
            if item["field_a"]["paired_crop_segment_length_m"] >= minimum_length
            and item["field_b"]["paired_crop_segment_length_m"] >= minimum_length
        ]
        sensitivity.append(
            {
                "minimum_crop_segment_m": minimum_length,
                "connection_pair_count": len(eligible),
                "input_segment_count": 2 * len(eligible),
                "resulting_cross_field_guidance_group_count": len(eligible),
                "paired_crop_length_km": rounded(
                    sum(
                        item["field_a"]["paired_crop_segment_length_m"]
                        + item["field_b"]["paired_crop_segment_length_m"]
                        for item in eligible
                    )
                    / 1000.0
                ),
                "connector_gap_length_km": rounded(
                    sum(item["connector_gap_m"] for item in eligible) / 1000.0
                ),
            }
        )

    return {
        "status": "UNCONFIRMED",
        "current_guidance_group_count": total_groups,
        "maximum_inferred_group_merges": merge_count,
        "guidance_group_reduction_percent": rounded(100.0 * merge_count / total_groups),
        "groups_after_inferred_merges": total_groups - merge_count,
        "physical_crop_segments_remain_separate": True,
        "gross_turn_time_saved": {
            "assumed_seconds_per_avoided_maneuver": ASSUMED_TURN_SECONDS,
            "seconds": rounded(gross_turn_seconds),
            "minutes": rounded(gross_turn_seconds / 60.0),
        },
        "connector_gap_total_km": rounded(total_gap_m / 1000.0),
        "aggregate_break_even_speed_kmh_before_other_penalties": (
            rounded(total_gap_m / gross_turn_seconds * 3.6)
            if gross_turn_seconds > 0
            else None
        ),
        "travel_speed_sensitivity": speed_cases,
        "minimum_crop_segment_sensitivity": sensitivity,
        "warning": (
            "This is not a route simulation. Lift/drop, traffic, acceleration, road condition, "
            "headland sequence, crop damage and safety penalties are absent."
        ),
    }


def detect_source_carrier_layers() -> list[str]:
    terms = ("carreador", "estrada", "road", "caminho")
    extensions = {".shp", ".gpkg", ".geojson", ".kml"}
    return sorted(
        path.relative_to(REPO).as_posix()
        for path in DATASET.iterdir()
        if path.is_file()
        and path.suffix.lower() in extensions
        and any(term in path.stem.lower() for term in terms)
    )


def create_field(layer, name: str, field_type, width: int = 0, precision: int = 0) -> None:
    definition = ogr.FieldDefn(name, field_type)
    if width:
        definition.SetWidth(width)
    if precision:
        definition.SetPrecision(precision)
    require(layer.CreateField(definition) == ogr.OGRERR_NONE, f"Could not create field {name}")


def set_feature_fields(feature, values: dict) -> None:
    for key, value in values.items():
        feature.SetField(key, value)


def line_z_and_metrics(line, terrain: Terrain, interval_m: float, smooth_sigma: float):
    count = max(2, int(math.ceil(line.length / interval_m)) + 1)
    distances = np.linspace(0.0, line.length, count)
    coordinates = np.asarray(
        [line.interpolate(float(distance)).coords[0] for distance in distances],
        dtype=float,
    )
    elevations = terrain.sample(coordinates[:, 0], coordinates[:, 1])
    require(np.isfinite(elevations).all(), "An output geometry crosses DTM nodata.")
    actual_interval = line.length / (count - 1)
    smoothed = ndimage.gaussian_filter1d(elevations, smooth_sigma, mode="nearest")
    grades = np.abs(np.diff(smoothed)) / max(actual_interval, 1e-9) * 100.0

    geometry = ogr.Geometry(ogr.wkbLineString25D)
    for (x, y), z in zip(coordinates, elevations):
        geometry.AddPoint(float(x), float(y), float(z))
    metrics = {
        "length_m": float(line.length),
        "z_start_m": float(elevations[0]),
        "z_end_m": float(elevations[-1]),
        "endpoint_grade_percent": abs(float(elevations[-1] - elevations[0]))
        / max(line.length, 1e-9)
        * 100.0,
        "grade_p95_percent": float(percentile(grades, 95)),
        "grade_max_percent": float(grades.max()),
    }
    return geometry, metrics


def write_e0g_geopackage(
    grouped: dict,
    connections: list[dict],
    terrain: Terrain,
    theta_deg: float,
    phase_delta_m: float,
) -> dict:
    if OUTPUT_GPKG.exists():
        OUTPUT_GPKG.unlink()
    driver = ogr.GetDriverByName("GPKG")
    datasource = driver.CreateDataSource(str(OUTPUT_GPKG))
    require(datasource is not None, f"Could not create {OUTPUT_GPKG}")
    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromEPSG(TARGET_EPSG)

    work_layer = datasource.CreateLayer(
        "e0g_work_segments",
        srs=spatial_ref,
        geom_type=ogr.wkbLineString25D,
        options=["SPATIAL_INDEX=YES"],
    )
    work_fields = [
        ("status", ogr.OFTString, 20, 0),
        ("scenario_id", ogr.OFTString, 48, 0),
        ("field_code", ogr.OFTString, 24, 0),
        ("portal_id", ogr.OFTString, 96, 0),
        ("line_id", ogr.OFTString, 96, 0),
        ("row_global_id", ogr.OFTInteger64, 0, 0),
        ("theta_deg", ogr.OFTReal, 0, 6),
        ("phase_offset_m", ogr.OFTReal, 0, 6),
        ("phase_level_m", ogr.OFTReal, 0, 6),
        ("length_m", ogr.OFTReal, 0, 3),
        ("z_start_m", ogr.OFTReal, 0, 3),
        ("z_end_m", ogr.OFTReal, 0, 3),
        ("end_grade", ogr.OFTReal, 0, 3),
        ("grade_p95", ogr.OFTReal, 0, 3),
        ("grade_max", ogr.OFTReal, 0, 3),
        ("paired", ogr.OFTInteger, 0, 0),
    ]
    for args in work_fields:
        create_field(work_layer, *args)

    connector_layer = datasource.CreateLayer(
        "e0g_movement_connectors",
        srs=spatial_ref,
        geom_type=ogr.wkbLineString25D,
        options=["SPATIAL_INDEX=YES"],
    )
    connector_fields = [
        ("status", ogr.OFTString, 20, 0),
        ("scenario_id", ogr.OFTString, 48, 0),
        ("portal_id", ogr.OFTString, 96, 0),
        ("row_global_id", ogr.OFTInteger64, 0, 0),
        ("field_pair", ogr.OFTString, 56, 0),
        ("from_field", ogr.OFTString, 24, 0),
        ("to_field", ogr.OFTString, 24, 0),
        ("theta_deg", ogr.OFTReal, 0, 6),
        ("phase_offset_m", ogr.OFTReal, 0, 6),
        ("phase_level_m", ogr.OFTReal, 0, 6),
        ("length_m", ogr.OFTReal, 0, 3),
        ("z_start_m", ogr.OFTReal, 0, 3),
        ("z_end_m", ogr.OFTReal, 0, 3),
        ("end_grade", ogr.OFTReal, 0, 3),
        ("grade_p95", ogr.OFTReal, 0, 3),
        ("grade_max", ogr.OFTReal, 0, 3),
        ("grade_alert", ogr.OFTInteger, 0, 0),
    ]
    for args in connector_fields:
        create_field(connector_layer, *args)

    portal_by_line_id = {}
    for connection in connections:
        portal_by_line_id[connection["field_a"]["line_id"]] = connection["portal_id"]
        portal_by_line_id[connection["field_b"]["line_id"]] = connection["portal_id"]

    work_count = 0
    paired_work_count = 0
    for field_code in FIELD_CODES:
        for row_global_id in sorted(grouped[field_code]):
            rows = sorted(grouped[field_code][row_global_id], key=lambda item: item["line_id"])
            for row in rows:
                geometry, metrics = line_z_and_metrics(
                    row["geometry"],
                    terrain,
                    interval_m=ROW_PROFILE_SAMPLE_M,
                    smooth_sigma=max(0.75, 5.0 / ROW_PROFILE_SAMPLE_M),
                )
                portal_id = portal_by_line_id.get(row["line_id"], "UNPAIRED")
                paired = int(portal_id != "UNPAIRED")
                feature = ogr.Feature(work_layer.GetLayerDefn())
                set_feature_fields(
                    feature,
                    {
                        "status": "UNCONFIRMED",
                        "scenario_id": PORTAL_SCENARIO_ID,
                        "field_code": field_code,
                        "portal_id": portal_id,
                        "line_id": row["line_id"],
                        "row_global_id": row_global_id,
                        "theta_deg": theta_deg,
                        "phase_offset_m": phase_delta_m,
                        "phase_level_m": row_global_id * ROW_SPACING_M + phase_delta_m,
                        "length_m": metrics["length_m"],
                        "z_start_m": metrics["z_start_m"],
                        "z_end_m": metrics["z_end_m"],
                        "end_grade": metrics["endpoint_grade_percent"],
                        "grade_p95": metrics["grade_p95_percent"],
                        "grade_max": metrics["grade_max_percent"],
                        "paired": paired,
                    },
                )
                feature.SetGeometry(geometry)
                require(work_layer.CreateFeature(feature) == ogr.OGRERR_NONE, "Work write failed.")
                work_count += 1
                paired_work_count += paired

    connector_count = 0
    for connection in sorted(connections, key=lambda item: item["row_global_id"]):
        point_a = connection["field_a"]["xy"]
        point_b = connection["field_b"]["xy"]
        line = LineString([point_a, point_b])
        geometry, metrics = line_z_and_metrics(
            line,
            terrain,
            interval_m=TERRAIN_SAMPLE_M,
            smooth_sigma=2.0,
        )
        feature = ogr.Feature(connector_layer.GetLayerDefn())
        set_feature_fields(
            feature,
            {
                "status": "UNCONFIRMED",
                "scenario_id": PORTAL_SCENARIO_ID,
                "portal_id": connection["portal_id"],
                "row_global_id": connection["row_global_id"],
                "field_pair": f"{FIELD_CODES[0]}>{FIELD_CODES[1]}",
                "from_field": FIELD_CODES[0],
                "to_field": FIELD_CODES[1],
                "theta_deg": theta_deg,
                "phase_offset_m": phase_delta_m,
                "phase_level_m": connection["row_global_id"] * ROW_SPACING_M + phase_delta_m,
                "length_m": metrics["length_m"],
                "z_start_m": metrics["z_start_m"],
                "z_end_m": metrics["z_end_m"],
                "end_grade": metrics["endpoint_grade_percent"],
                "grade_p95": metrics["grade_p95_percent"],
                "grade_max": metrics["grade_max_percent"],
                "grade_alert": int(
                    metrics["grade_p95_percent"] > REFERENCE_GRADE_ALERT_PERCENT
                ),
            },
        )
        feature.SetGeometry(geometry)
        require(
            connector_layer.CreateFeature(feature) == ogr.OGRERR_NONE,
            "Connector write failed.",
        )
        connector_count += 1

    work_layer = None
    connector_layer = None
    datasource = None
    with sqlite3.connect(OUTPUT_GPKG) as database:
        database.execute(
            "UPDATE gpkg_contents SET last_change = ?",
            (FIXED_GPKG_LAST_CHANGE,),
        )
        database.commit()
        database.execute("VACUUM")

    return {
        "work_segment_count": work_count,
        "paired_work_segment_count": paired_work_count,
        "movement_connector_count": connector_count,
    }


def validate_e0g_geopackage(expected_counts: dict) -> dict:
    source = ogr.Open(str(OUTPUT_GPKG))
    require(source is not None, f"Could not reopen {OUTPUT_GPKG}")
    layer_names = [source.GetLayerByIndex(index).GetName() for index in range(source.GetLayerCount())]
    expected_layers = ["e0g_work_segments", "e0g_movement_connectors"]
    require(sorted(layer_names) == sorted(expected_layers), f"Unexpected layers: {layer_names}")

    signature = hashlib.sha256()
    validation = {}
    total_non_3d = 0
    total_non_finite_z = 0
    total_invalid = 0
    total_wrong_status = 0
    for layer_name in expected_layers:
        layer = source.GetLayerByName(layer_name)
        spatial_ref = layer.GetSpatialRef()
        authority = spatial_ref.GetAuthorityCode(None) if spatial_ref is not None else None
        require(authority == str(TARGET_EPSG), f"Unexpected CRS on {layer_name}: {authority}")
        feature_count = layer.GetFeatureCount()
        field_names = [
            layer.GetLayerDefn().GetFieldDefn(index).GetName()
            for index in range(layer.GetLayerDefn().GetFieldCount())
        ]
        layer_non_3d = 0
        layer_non_finite_z = 0
        layer_invalid = 0
        layer_wrong_status = 0
        signature.update(layer_name.encode("ascii"))
        for feature in layer:
            geometry = feature.GetGeometryRef()
            layer_non_3d += int(geometry.GetCoordinateDimension() != 3)
            layer_invalid += int(not geometry.IsValid())
            layer_wrong_status += int(feature.GetFieldAsString("status") != "UNCONFIRMED")
            for point_index in range(geometry.GetPointCount()):
                layer_non_finite_z += int(not math.isfinite(geometry.GetZ(point_index)))
            attributes = {name: feature.GetField(name) for name in field_names}
            signature.update(
                json.dumps(attributes, sort_keys=True, ensure_ascii=True).encode("utf-8")
            )
            signature.update(bytes(geometry.ExportToWkb()))
        validation[layer_name] = {
            "feature_count": feature_count,
            "crs": f"EPSG:{TARGET_EPSG}",
            "geometry_type": "LineStringZ",
            "non_3d_count": layer_non_3d,
            "non_finite_z_count": layer_non_finite_z,
            "invalid_geometry_count": layer_invalid,
            "wrong_status_count": layer_wrong_status,
        }
        total_non_3d += layer_non_3d
        total_non_finite_z += layer_non_finite_z
        total_invalid += layer_invalid
        total_wrong_status += layer_wrong_status

    require(
        validation["e0g_work_segments"]["feature_count"]
        == expected_counts["work_segment_count"],
        "Unexpected work-segment count.",
    )
    require(
        validation["e0g_movement_connectors"]["feature_count"]
        == expected_counts["movement_connector_count"],
        "Unexpected connector count.",
    )
    require(total_non_3d == 0, f"Non-3D geometries: {total_non_3d}")
    require(total_non_finite_z == 0, f"Non-finite Z coordinates: {total_non_finite_z}")
    require(total_invalid == 0, f"Invalid geometries: {total_invalid}")
    require(total_wrong_status == 0, f"Features without UNCONFIRMED status: {total_wrong_status}")
    return {
        "status": "verified",
        "layers": validation,
        "semantic_sha256": signature.hexdigest(),
        "fixed_gpkg_last_change": FIXED_GPKG_LAST_CHANGE,
        "deterministic_order": (
            "field code, row_global_id and line_id for work; row_global_id for connectors"
        ),
    }


def main() -> None:
    for path in (BOUNDARY, DTM, SCENARIO_GPKG, SCENARIO_METRICS):
        require(path.exists(), f"Missing source: {path}")
    scenario_manifest = json.loads(SCENARIO_METRICS.read_text(encoding="utf-8"))
    require(
        scenario_manifest.get("status") == "E0_topographic_sulcation_geometry_screening",
        "The source sulcation package is not a current E0 screening artifact.",
    )
    require(
        {item.get("id") for item in scenario_manifest.get("scenario_definitions", [])}
        >= {SCENARIO_ID},
        f"The source sulcation manifest does not declare {SCENARIO_ID}.",
    )
    output_integrity = scenario_manifest.get("output_integrity", {})
    gpkg_integrity = output_integrity.get("geopackage")
    require(gpkg_integrity is not None, "The source sulcation package has no GeoPackage integrity record.")
    require(
        int(gpkg_integrity["size_bytes"]) == SCENARIO_GPKG.stat().st_size
        and gpkg_integrity["sha256"] == sha256(SCENARIO_GPKG),
        "The source sulcation GeoPackage does not match its manifest integrity record.",
    )
    manifest_parameters = scenario_manifest["parameters"]
    require(
        math.isclose(float(manifest_parameters["row_spacing_m"]), ROW_SPACING_M),
        "This dataset-specific screening script does not match the generated row spacing.",
    )
    require(
        math.isclose(float(manifest_parameters["outer_headland_m"]), HEADLAND_M),
        "This dataset-specific screening script does not match the generated headland.",
    )
    require(
        scenario_manifest["generation_request"]["path_resolution"]["dtm"]["resolved_path"]
        == DTM.relative_to(REPO).as_posix(),
        "The multifield screening DTM does not match the sulcation manifest.",
    )
    require(
        scenario_manifest["generation_request"]["path_resolution"]["boundary"]["resolved_path"]
        == BOUNDARY.relative_to(REPO).as_posix(),
        "The multifield screening boundary does not match the sulcation manifest.",
    )
    selected_e0f = scenario_manifest["selected_parameters"][SCENARIO_ID]
    require(
        all(math.isclose(float(item["lambda"]), 0.0, abs_tol=1e-12) for item in selected_e0f.values()),
        "E0F is not a straight shared-phase lattice.",
    )

    fields, crs = read_fields()
    grouped, summaries = read_e0f_lines()
    terrain = Terrain(DTM)
    for code in FIELD_CODES:
        require(
            terrain.footprint.covers(fields[code]["gross"]),
            f"The DTM does not cover the full multifield geometry for {code}.",
        )
    interface = infer_interface(fields)
    e0f_connections = infer_e0f_connections(fields, grouped, terrain)
    e0f_connection_summary = summarize_connections(e0f_connections)
    e0f_operational = operational_screening(e0f_connections, summaries)

    portal_trial = build_portal_normal_trial(fields, interface, terrain)
    e0g_connections = portal_trial.pop("connections")
    e0g_grouped = portal_trial.pop("_selected_grouped_rows")
    require(e0g_connections, "No E0G inferred cross-field connection was found.")
    e0g_connection_summary = summarize_connections(e0g_connections)
    e0g_summaries = portal_trial["field_summaries"]
    e0g_operational = operational_screening(e0g_connections, e0g_summaries)
    selected_theta = portal_trial["selected"]["theta_from_east_counterclockwise_deg"]
    selected_phase = portal_trial["selected"]["phase_delta_m"]
    gpkg_counts = write_e0g_geopackage(
        e0g_grouped,
        e0g_connections,
        terrain,
        selected_theta,
        selected_phase,
    )
    require(
        gpkg_counts["paired_work_segment_count"] == 2 * len(e0g_connections),
        "Each movement connector must identify two paired work segments.",
    )
    gpkg_validation = validate_e0g_geopackage(gpkg_counts)
    source_carrier_layers = detect_source_carrier_layers()

    e0f_gap_median = e0f_connection_summary["gap_m"]["p50"]
    e0g_gap_median = e0g_connection_summary["gap_m"]["p50"]
    e0f_grade_median = e0f_connection_summary[
        "terrain_local_absolute_grade_p95_percent"
    ]["p50"]
    e0g_grade_median = e0g_connection_summary[
        "terrain_local_absolute_grade_p95_percent"
    ]["p50"]

    result = {
        "analysis_id": "multifield_connection_screening_v2",
        "status": "UNCONFIRMED",
        "release_level": "E0_TOPOGRAPHIC_SCREENING",
        "purpose": (
            "Compare common-axis and portal-normal geometric continuity between the two current "
            "field polygons."
        ),
        "portfolio_role": {
            "artifact_type": "geometric_multifield_connection_precursor",
            "warning": (
                "Neither E0F nor E0G selects a conservation system, validates a carrier, or "
                "produces machine-ready rows."
            ),
        },
        "not_authorized_for": [
            "machine guidance",
            "planting or harvest execution",
            "carrier crossing approval",
            "hydraulic or conservation-system approval",
            "selection of embedded, broad-base/passable, ESD or mixed conservation methods",
        ],
        "sources": {
            "crs": crs,
            "files": [
                file_record(path)
                for path in (BOUNDARY, DTM, SCENARIO_GPKG, SCENARIO_METRICS)
            ],
        },
        "artifacts": {
            "geopackage": {
                **file_record(OUTPUT_GPKG),
                "layers": {
                    "e0g_work_segments": gpkg_counts["work_segment_count"],
                    "e0g_movement_connectors": gpkg_counts["movement_connector_count"],
                },
                "status": "UNCONFIRMED",
            }
        },
        "parameters": {
            "scenario_ids": [SCENARIO_ID, PORTAL_SCENARIO_ID],
            "field_codes": list(FIELD_CODES),
            "row_spacing_m": ROW_SPACING_M,
            "headland_each_field_m": HEADLAND_M,
            "obstacle_clearance_m": OBSTACLE_CLEARANCE_M,
            "minimum_analytic_segment_m": MINIMUM_ANALYTIC_SEGMENT_M,
            "maximum_inferred_connector_m": MAX_INFERRED_CONNECTOR_M,
            "other_field_proximity_m": OTHER_FIELD_PROXIMITY_M,
            "terrain_smoothing_sigma_m": TERRAIN_SMOOTHING_SIGMA_M,
            "reference_grade_alert_percent": REFERENCE_GRADE_ALERT_PERCENT,
            "reference_grade_alert_policy": "Illustrative E0 alert, not an approved agronomic limit.",
        },
        "interface": interface,
        "current_e0f": {
            "scenario_id": SCENARIO_ID,
            "design_behavior": (
                "COMMON_STRAIGHT_AXIS_WITH_VERIFIED_GLOBAL_PHASE; ROWS REMAIN CLIPPED BY FIELD"
            ),
            "field_summaries": summaries,
            "total_segment_count": sum(item["segment_count"] for item in summaries.values()),
            "total_guidance_group_count": sum(
                item["guidance_group_count"] for item in summaries.values()
            ),
            "shared_global_row_count": len(
                set(grouped[FIELD_CODES[0]]) & set(grouped[FIELD_CODES[1]])
            ),
            "identity_policy": (
                "row_global_id comes from the persisted global phase level; field-local guidance_id "
                "is never used for cross-field pairing"
            ),
            **e0f_connection_summary,
        },
        "current_e0g": {
            **portal_trial,
            "total_segment_count": sum(
                item["segment_count"] for item in e0g_summaries.values()
            ),
            "total_guidance_group_count": sum(
                item["guidance_group_count"] for item in e0g_summaries.values()
            ),
            **e0g_connection_summary,
            "persisted_geopackage": {
                "path": OUTPUT_GPKG.relative_to(REPO).as_posix(),
                "work_segment_count": gpkg_counts["work_segment_count"],
                "paired_work_segment_count": gpkg_counts["paired_work_segment_count"],
                "movement_connector_count": gpkg_counts["movement_connector_count"],
            },
            "interpretation": (
                "The objective is portal continuity only. Longitudinal row grade, erosion, "
                "harvestability and hydraulic behavior were not optimized."
            ),
        },
        "scenario_comparison": {
            "status": "UNCONFIRMED",
            "e0g_minus_e0f_connection_pairs": (
                e0g_connection_summary["inferred_connection_pair_count"]
                - e0f_connection_summary["inferred_connection_pair_count"]
            ),
            "median_gap_reduction_m": (
                rounded(e0f_gap_median - e0g_gap_median)
                if e0f_gap_median is not None
                else None
            ),
            "median_gap_reduction_percent": (
                rounded(100.0 * (e0f_gap_median - e0g_gap_median) / e0f_gap_median)
                if e0f_gap_median not in (None, 0)
                else None
            ),
            "median_connector_local_p95_grade": {
                SCENARIO_ID: e0f_grade_median,
                PORTAL_SCENARIO_ID: e0g_grade_median,
                "e0g_minus_e0f_percentage_points": (
                    rounded(e0g_grade_median - e0f_grade_median)
                    if e0f_grade_median is not None
                    else None
                ),
            },
            "guidance_group_reduction_percent": {
                SCENARIO_ID: e0f_operational["guidance_group_reduction_percent"],
                PORTAL_SCENARIO_ID: e0g_operational["guidance_group_reduction_percent"],
            },
            "aggregate_break_even_speed_kmh_before_other_penalties": {
                SCENARIO_ID: e0f_operational[
                    "aggregate_break_even_speed_kmh_before_other_penalties"
                ],
                PORTAL_SCENARIO_ID: e0g_operational[
                    "aggregate_break_even_speed_kmh_before_other_penalties"
                ],
            },
            "warning": (
                "More portals or shorter gaps do not imply a safer, more conservative, or more "
                "productive complete row plan."
            ),
        },
        "operational_screening": {
            SCENARIO_ID: e0f_operational,
            PORTAL_SCENARIO_ID: e0g_operational,
        },
        "candidate_portals": {
            SCENARIO_ID: e0f_connections,
            PORTAL_SCENARIO_ID: e0g_connections,
        },
        "geopackage_validation": gpkg_validation,
        "blocking_inputs": {
            "surveyed_carrier_layer_detected": bool(source_carrier_layers),
            "detected_candidate_layers": source_carrier_layers,
            "missing": [
                "surveyed carrier polygons or centerlines and true widths",
                "authorized portal and no-cross zones",
                "carrier finished-grade profile and crossfall",
                "culverts, ditches, terraces, channels and stable receivers",
                "fences, gates, utilities, bridges and protected features",
                "farm or property ownership and operational permission",
                "machine width, turning radius, clearance, lift/drop and safe crossing speed",
                "validated route sequence for planting and harvest fleets",
            ],
            "critical_interpretation": (
                "A shared polygon boundary is not evidence of a carrier or a legal and safe crossing."
            ),
        },
        "decision": {
            "current_e0f_cross_field_continuity": (
                "NOT_RECOMMENDED_FROM_AVAILABLE_EVIDENCE"
                if e0f_connections
                else "NOT_EVALUATED_NO_GEOMETRIC_PAIR"
            ),
            "e0f_operational_reason": (
                "The common axis has no pair within the geometric screening gates."
                if not e0f_connections
                else "With the provisional assumption of 22 seconds per avoided maneuver, the common "
                "axis produces long gaps whose simplified travel cost is unfavorable at the "
                "screened normal travel speeds."
            ),
            "e0f_separate_grade_diagnostic": (
                "The connector-grade alert is an independent topographic diagnostic; it is not "
                "part of the provisional 22-second operational comparison."
            ),
            "current_e0g_cross_field_continuity": "UNCONFIRMED_RESEARCH_BASELINE_ONLY",
            "e0g_reason": (
                "E0G samples globally phased straight lattices near the local boundary normal. "
                "It has not passed carrier, machine, conservation, hydraulic or route gates and "
                "is not a global optimum."
            ),
            "required_next_analysis": (
                "Classify the internal boundary, generate a block-level phase field with approved "
                "portals, and compare perpendicular/local-curved alternatives with a fleet route model."
            ),
        },
    }

    OUTPUT.write_text(json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    verified = json.loads(OUTPUT.read_text(encoding="utf-8"))
    require(verified["status"] == "UNCONFIRMED", "Output status changed unexpectedly.")
    require(
        verified["current_e0f"]["inferred_connection_pair_count"] == len(e0f_connections),
        "E0F output connection count mismatch.",
    )
    require(
        verified["current_e0g"]["inferred_connection_pair_count"] == len(e0g_connections),
        "E0G output connection count mismatch.",
    )
    require(
        all(item["status"] == "UNCONFIRMED" for item in e0f_connections + e0g_connections),
        "Every inferred portal must remain UNCONFIRMED.",
    )
    require(
        verified["artifacts"]["geopackage"]["layers"]["e0g_work_segments"]
        == gpkg_validation["layers"]["e0g_work_segments"]["feature_count"],
        "JSON/GPKG work count mismatch.",
    )
    require(
        verified["artifacts"]["geopackage"]["layers"]["e0g_movement_connectors"]
        == gpkg_validation["layers"]["e0g_movement_connectors"]["feature_count"],
        "JSON/GPKG connector count mismatch.",
    )
    print(
        json.dumps(
            {
                "status": verified["status"],
                "output": OUTPUT.relative_to(REPO).as_posix(),
                "field_pair": list(FIELD_CODES),
                "gross_gap_m": verified["interface"]["gross_polygon_distance_m"],
                "e0_usable_gap_m": verified["interface"]["e0_independent_field_buffer"][
                    "measured_minimum_usable_gap_m"
                ],
                "e0f_inferred_connection_pairs": len(e0f_connections),
                "e0g_inferred_connection_pairs": len(e0g_connections),
                "e0g_selected_theta_deg": verified["current_e0g"]["selected"][
                    "theta_from_east_counterclockwise_deg"
                ],
                "e0g_selected_phase_delta_m": verified["current_e0g"]["selected"][
                    "phase_delta_m"
                ],
                "e0g_gpkg": OUTPUT_GPKG.relative_to(REPO).as_posix(),
                "e0g_work_segment_count": gpkg_counts["work_segment_count"],
                "e0g_movement_connector_count": gpkg_counts["movement_connector_count"],
                "e0g_gpkg_semantic_sha256": gpkg_validation["semantic_sha256"],
                "e0f_grade_alert_exceeded_count": verified["current_e0f"][
                    "connections_exceeding_reference_grade_alert"
                ],
                "e0g_grade_alert_exceeded_count": verified["current_e0g"][
                    "connections_exceeding_reference_grade_alert"
                ],
                "e0f_decision": verified["decision"]["current_e0f_cross_field_continuity"],
                "e0g_decision": verified["decision"]["current_e0g_cross_field_continuity"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
