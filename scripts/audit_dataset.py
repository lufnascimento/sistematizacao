"""Audit the farm point cloud, boundary and elevation model.

Run with the QGIS Python environment on Windows:

    "C:\\Program Files\\QGIS 3.32.1\\bin\\python-qgis.bat" scripts/audit_dataset.py

The script keeps source data untouched and writes every output to
``dataset/derived``.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, LogNorm
from osgeo import gdal, ogr, osr
from scipy import ndimage


ogr.UseExceptions()
gdal.UseExceptions()

REPO = Path(__file__).resolve().parents[1]
DATASET = REPO / "dataset"
DERIVED = DATASET / "derived"
POINT_CLOUD = DATASET / "TERRENO_LIMPO.LAZ"
BOUNDARY = DATASET / "Contorno.shp"
DELIVERED_DEM = DATASET / "DEM.tif"

DTM_1M = DERIVED / "dtm_1m.tif"
DENSITY_5M = DERIVED / "density_5m.tif"
SLOPE_1M = DERIVED / "slope_percent_1m.tif"
SLOPE_DEM = DERIVED / "slope_dem_percent.tif"
HILLSHADE_DEM = DERIVED / "hillshade_dem.tif"
CONTOURS = DERIVED / "contours_1m.gpkg"
METRICS = DERIVED / "audit_metrics.json"
FLOW_ACCUMULATION = DERIVED / "flow_accumulation_cells.tif"
CONTRIBUTING_AREA = DERIVED / "contributing_area_ha.tif"
CANDIDATE_PATHS = DERIVED / "candidate_flow_paths_1ha.tif"


def command(name: str) -> str:
    executable = shutil.which(name)
    if not executable:
        raise RuntimeError(
            f"{name} was not found. Run this script through the QGIS Python launcher."
        )
    return executable


def ensure_derivatives() -> None:
    DERIVED.mkdir(parents=True, exist_ok=True)

    if not DTM_1M.exists():
        subprocess.run(
            [
                command("pdal"),
                "translate",
                str(POINT_CLOUD),
                str(DTM_1M),
                "--writer=writers.gdal",
                "--writers.gdal.resolution=1",
                "--writers.gdal.output_type=idw",
                "--writers.gdal.radius=1.5",
                "--writers.gdal.window_size=3",
                "--writers.gdal.override_srs=EPSG:31982",
            ],
            check=True,
        )

    if not DENSITY_5M.exists():
        subprocess.run(
            [
                command("pdal"),
                "translate",
                str(POINT_CLOUD),
                str(DENSITY_5M),
                "--writer=writers.gdal",
                "--writers.gdal.resolution=5",
                "--writers.gdal.output_type=count",
                "--writers.gdal.radius=3.5356",
                "--writers.gdal.override_srs=EPSG:31982",
            ],
            check=True,
        )

    if not SLOPE_1M.exists():
        gdal.DEMProcessing(str(SLOPE_1M), str(DTM_1M), "slope", slopeFormat="percent")
    if not SLOPE_DEM.exists():
        gdal.DEMProcessing(
            str(SLOPE_DEM), str(DELIVERED_DEM), "slope", slopeFormat="percent", computeEdges=True
        )
    if not HILLSHADE_DEM.exists():
        gdal.DEMProcessing(
            str(HILLSHADE_DEM), str(DELIVERED_DEM), "hillshade", multidirectional=True, computeEdges=True
        )
    if not CONTOURS.exists():
        subprocess.run(
            [
                command("gdal_contour"),
                "-a",
                "elev",
                "-i",
                "1",
                "-f",
                "GPKG",
                str(DELIVERED_DEM),
                str(CONTOURS),
            ],
            check=True,
        )


def round_number(value: float | None, digits: int = 4) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def quantiles(values: np.ndarray) -> dict[str, float | int] | None:
    if values.size == 0:
        return None
    q = np.percentile(values, [0, 5, 25, 50, 75, 90, 95, 99, 100])
    return {
        "count": int(values.size),
        "min": round_number(q[0]),
        "p05": round_number(q[1]),
        "p25": round_number(q[2]),
        "median": round_number(q[3]),
        "p75": round_number(q[4]),
        "p90": round_number(q[5]),
        "p95": round_number(q[6]),
        "p99": round_number(q[7]),
        "max": round_number(q[8]),
        "mean": round_number(values.mean()),
        "stddev": round_number(values.std()),
    }


def polygon_and_hole_count(geometry: ogr.Geometry) -> tuple[int, int]:
    geometry_type = ogr.GT_Flatten(geometry.GetGeometryType())
    if geometry_type == ogr.wkbPolygon:
        return 1, max(0, geometry.GetGeometryCount() - 1)
    if geometry_type == ogr.wkbMultiPolygon:
        counts = [polygon_and_hole_count(geometry.GetGeometryRef(i)) for i in range(geometry.GetGeometryCount())]
        return sum(item[0] for item in counts), sum(item[1] for item in counts)
    return 0, 0


def read_boundaries() -> tuple[ogr.Geometry, list[dict], list[tuple[ogr.Geometry, str, float]]]:
    source = ogr.Open(str(BOUNDARY))
    layer = source.GetLayer(0)
    union = None
    features = []
    map_features = []

    for feature in layer:
        geometry = feature.GetGeometryRef().Clone()
        union = geometry.Clone() if union is None else union.Union(geometry)
        polygons, holes = polygon_and_hole_count(geometry)
        code = str(feature.GetField("cd_upnivel"))
        area_ha = geometry.GetArea() / 10_000
        features.append(
            {
                "fid": feature.GetFID(),
                "code": code,
                "reported_area_ha": round_number(feature.GetField("area")),
                "geometry_area_ha": round_number(area_ha),
                "perimeter_m": round_number(geometry.Boundary().Length(), 2),
                "parts": polygons,
                "excluded_islands": holes,
                "valid_geometry": bool(geometry.IsValid()),
            }
        )
        map_features.append((geometry, code, area_ha))

    if union is None:
        raise RuntimeError("The boundary layer has no features.")
    return union, features, map_features


def geometry_mask(dataset: gdal.Dataset, geometry: ogr.Geometry) -> np.ndarray:
    target = gdal.GetDriverByName("MEM").Create(
        "", dataset.RasterXSize, dataset.RasterYSize, 1, gdal.GDT_Byte
    )
    target.SetGeoTransform(dataset.GetGeoTransform())
    target.SetProjection(dataset.GetProjection())

    spatial_ref = osr.SpatialReference()
    spatial_ref.ImportFromWkt(dataset.GetProjection())
    memory = ogr.GetDriverByName("Memory").CreateDataSource("")
    layer = memory.CreateLayer("mask", srs=spatial_ref, geom_type=ogr.wkbUnknown)
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(geometry)
    layer.CreateFeature(feature)
    gdal.RasterizeLayer(target, [1], layer, burn_values=[1])
    return target.GetRasterBand(1).ReadAsArray().astype(bool)


def read_raster(path: Path, geometry: ogr.Geometry) -> dict:
    dataset = gdal.Open(str(path))
    band = dataset.GetRasterBand(1)
    array = band.ReadAsArray().astype("float64")
    nodata = band.GetNoDataValue()
    valid = np.isfinite(array)
    if nodata is not None:
        valid &= ~np.isclose(array, nodata)
    mask = geometry_mask(dataset, geometry)
    values = array[valid & mask]
    return {
        "dataset": dataset,
        "array": array,
        "valid": valid,
        "mask": mask,
        "values": values,
        "pixel_area": abs(dataset.GetGeoTransform()[1] * dataset.GetGeoTransform()[5]),
    }


def slope_bands(values: np.ndarray) -> dict[str, float]:
    bands = [(0, 3), (3, 6), (6, 12), (12, 20), (20, 45), (45, float("inf"))]
    return {
        (f"{low}-{high}" if math.isfinite(high) else ">=45"): round_number(
            100 * np.count_nonzero((values >= low) & (values < high)) / values.size, 2
        )
        for low, high in bands
    }


def compare_surfaces(dtm: dict, dem_path: Path) -> dict[str, float | int]:
    dataset = dtm["dataset"]
    gt = dataset.GetGeoTransform()
    xmin, ymax = gt[0], gt[3]
    xmax = xmin + dataset.RasterXSize * gt[1]
    ymin = ymax + dataset.RasterYSize * gt[5]
    warped = gdal.Warp(
        "",
        str(dem_path),
        format="MEM",
        outputBounds=(xmin, ymin, xmax, ymax),
        width=dataset.RasterXSize,
        height=dataset.RasterYSize,
        dstSRS=dataset.GetProjection(),
        resampleAlg="bilinear",
        srcNodata=-32767,
        dstNodata=-9999,
    )
    delivered = warped.GetRasterBand(1).ReadAsArray().astype("float64")
    common = dtm["valid"] & (~np.isclose(delivered, -9999)) & dtm["mask"]
    difference = dtm["array"][common] - delivered[common]
    absolute = np.abs(difference)
    return {
        "common_pixels": int(difference.size),
        "signed_mean_m": round_number(difference.mean()),
        "signed_median_m": round_number(np.median(difference)),
        "rmse_m": round_number(np.sqrt(np.mean(difference**2))),
        "absolute_median_m": round_number(np.median(absolute)),
        "absolute_p95_m": round_number(np.percentile(absolute, 95)),
        "absolute_p99_m": round_number(np.percentile(absolute, 99)),
        "absolute_max_m": round_number(absolute.max()),
    }


def las_summary() -> dict:
    process = subprocess.run(
        [
            command("pdal"),
            "info",
            "--stats",
            "--dimensions",
            "X,Y,Z,Classification,ReturnNumber,NumberOfReturns,GpsTime,Red,Green,Blue",
            str(POINT_CLOUD),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(process.stdout)
    stats = {item["name"]: item for item in data["stats"]["statistic"]}
    return {
        "file_size_bytes": data["file_size"],
        "point_count": stats["X"]["count"],
        "bounds": {
            axis.lower(): [round_number(stats[axis]["minimum"]), round_number(stats[axis]["maximum"])]
            for axis in ("X", "Y", "Z")
        },
        "classification": {
            "minimum": stats["Classification"]["minimum"],
            "maximum": stats["Classification"]["maximum"],
        },
        "return_number": {
            "minimum": stats["ReturnNumber"]["minimum"],
            "maximum": stats["ReturnNumber"]["maximum"],
            "mean": round_number(stats["ReturnNumber"]["average"]),
        },
        "gps_time": [round_number(stats["GpsTime"]["minimum"]), round_number(stats["GpsTime"]["maximum"])],
        "rgb_present": all(axis in stats for axis in ("Red", "Green", "Blue")),
    }


def draw_geometry(ax: plt.Axes, geometry: ogr.Geometry) -> None:
    geometry_type = ogr.GT_Flatten(geometry.GetGeometryType())
    if geometry_type == ogr.wkbMultiPolygon:
        for i in range(geometry.GetGeometryCount()):
            draw_geometry(ax, geometry.GetGeometryRef(i))
        return
    if geometry_type != ogr.wkbPolygon:
        return
    for index in range(geometry.GetGeometryCount()):
        ring = geometry.GetGeometryRef(index)
        points = np.asarray(ring.GetPoints())
        ax.plot(points[:, 0], points[:, 1], color="#111b16", linewidth=3.2, alpha=0.75)
        ax.plot(points[:, 0], points[:, 1], color="#f2c94c", linewidth=1.5)


def add_map_furniture(ax: plt.Axes, envelope: tuple[float, float, float, float]) -> None:
    min_x, max_x, min_y, max_y = envelope
    bar_x = min_x + 38
    bar_y = min_y + 35
    ax.plot([bar_x, bar_x + 200], [bar_y, bar_y], color="white", linewidth=5, solid_capstyle="butt")
    ax.plot([bar_x, bar_x + 200], [bar_y, bar_y], color="#17231d", linewidth=2, solid_capstyle="butt")
    ax.text(bar_x + 100, bar_y + 15, "200 m", ha="center", va="bottom", fontsize=11, weight="bold", color="#17231d")
    north_x = max_x - 55
    north_y = max_y - 45
    ax.annotate("N", (north_x, north_y), xytext=(north_x, north_y - 65), ha="center", va="center",
                fontsize=12, weight="bold", color="#17231d",
                arrowprops={"arrowstyle": "-|>", "color": "#17231d", "lw": 2})


def render_map(
    output: Path,
    raster_path: Path,
    hillshade_path: Path,
    map_features: list[tuple[ogr.Geometry, str, float]],
    union: ogr.Geometry,
    kind: str,
) -> None:
    raster_ds = gdal.Open(str(raster_path))
    hillshade_ds = gdal.Open(str(hillshade_path))
    raster = raster_ds.GetRasterBand(1).ReadAsArray().astype(float)
    shade = hillshade_ds.GetRasterBand(1).ReadAsArray().astype(float)
    nodata = raster_ds.GetRasterBand(1).GetNoDataValue()
    if nodata is not None:
        raster = np.ma.masked_where(np.isclose(raster, nodata), raster)

    gt = raster_ds.GetGeoTransform()
    extent = [
        gt[0],
        gt[0] + gt[1] * raster_ds.RasterXSize,
        gt[3] + gt[5] * raster_ds.RasterYSize,
        gt[3],
    ]
    envelope = union.Buffer(100).GetEnvelope()

    figure = plt.figure(figsize=(16, 10), dpi=120, facecolor="#e8ece7")
    ax = figure.add_axes([0, 0, 1, 1])
    ax.set_facecolor("#e8ece7")

    if kind == "elevation":
        values = raster.compressed()
        image = ax.imshow(
            raster,
            extent=extent,
            origin="upper",
            cmap="terrain",
            vmin=np.percentile(values, 2),
            vmax=np.percentile(values, 98),
            interpolation="bilinear",
        )
        label = "Elevacao (m)"
    else:
        cmap = LinearSegmentedColormap.from_list(
            "slope", ["#d8e7cf", "#79a965", "#f1c44f", "#e47d3d", "#ad3d35"]
        )
        image = ax.imshow(raster, extent=extent, origin="upper", cmap=cmap, vmin=0, vmax=20, interpolation="bilinear")
        label = "Declividade (%)"

    ax.imshow(shade, extent=extent, origin="upper", cmap="gray", alpha=0.27, interpolation="bilinear")
    for geometry, code, area_ha in map_features:
        draw_geometry(ax, geometry)
        point = geometry.PointOnSurface()
        ax.text(
            point.GetX(),
            point.GetY(),
            f"{code}\n{area_ha:.2f} ha",
            ha="center",
            va="center",
            fontsize=11,
            weight="bold",
            color="#17231d",
            bbox={"boxstyle": "round,pad=0.35", "fc": "white", "ec": "#d6ddd7", "alpha": 0.9},
        )

    ax.set_xlim(envelope[0], envelope[1])
    ax.set_ylim(envelope[2], envelope[3])
    ax.set_aspect("equal")
    ax.axis("off")
    add_map_furniture(ax, envelope)

    colorbar_ax = figure.add_axes([0.055, 0.075, 0.22, 0.023])
    colorbar = figure.colorbar(image, cax=colorbar_ax, orientation="horizontal")
    colorbar.ax.tick_params(labelsize=9, length=2)
    colorbar.set_label(label, size=10, weight="bold")
    figure.savefig(output, dpi=120, facecolor="#e8ece7")
    plt.close(figure)


def preliminary_flow_summary(
    union: ogr.Geometry, map_features: list[tuple[ogr.Geometry, str, float]]
) -> tuple[dict, list[dict]]:
    accumulation = read_raster(FLOW_ACCUMULATION, union)
    contributing = read_raster(CONTRIBUTING_AREA, union)
    inside = accumulation["valid"] & accumulation["mask"]
    signed = accumulation["array"]
    area = contributing["array"]

    boundary = inside & ~ndimage.binary_erosion(accumulation["mask"], structure=np.ones((3, 3)))
    local_maximum = area == ndimage.maximum_filter(area, size=7, mode="nearest")
    candidates = np.argwhere(boundary & local_maximum & (area >= 0.5))
    candidates = sorted(candidates, key=lambda rc: area[tuple(rc)], reverse=True)

    gt = accumulation["dataset"].GetGeoTransform()
    selected = []
    for row, column in candidates:
        x = gt[0] + (column + 0.5) * gt[1]
        y = gt[3] + (row + 0.5) * gt[5]
        if any((x - item["x"]) ** 2 + (y - item["y"]) ** 2 <= 40**2 for item in selected):
            continue
        point = ogr.Geometry(ogr.wkbPoint)
        point.AddPoint(float(x), float(y))
        field_code = next(
            (code for geometry, code, _ in map_features if geometry.Buffer(2).Contains(point)),
            None,
        )
        selected.append(
            {
                "id": f"P{len(selected) + 1}",
                "x": round_number(x, 3),
                "y": round_number(y, 3),
                "field_code": field_code,
                "contributing_area_ha": round_number(area[row, column], 3),
                "possibly_affected_by_external_flow": bool(signed[row, column] < 0),
            }
        )
        if len(selected) == 8:
            break

    values = area[inside]
    summary = {
        "status": "unconditioned_topographic_screening",
        "method": "GRASS r.watershed MFD, convergence 5",
        "candidate_path_threshold_ha": 1.0,
        "possibly_affected_by_external_flow_percent": round_number(
            100 * np.count_nonzero((signed < 0) & inside) / np.count_nonzero(inside), 2
        ),
        "maximum_contributing_area_inside_fields_ha": round_number(values.max(), 3),
        "contributing_area_statistics_ha": quantiles(values),
        "boundary_crossing_candidates": selected,
        "limitations": [
            "The terrain was not conditioned with surveyed roads, culverts, channels or verified depressions.",
            "Negative accumulation cells may receive runoff from outside the elevation raster and cannot be quantified accurately.",
            "Candidate paths indicate topographic convergence, not design discharge or hydraulic structures.",
        ],
    }
    return summary, selected


def render_flow_map(
    output: Path,
    map_features: list[tuple[ogr.Geometry, str, float]],
    union: ogr.Geometry,
    crossings: list[dict],
) -> None:
    dem_ds = gdal.Open(str(DELIVERED_DEM))
    shade_ds = gdal.Open(str(HILLSHADE_DEM))
    area_ds = gdal.Open(str(CONTRIBUTING_AREA))
    paths_ds = gdal.Open(str(CANDIDATE_PATHS))
    dem = dem_ds.GetRasterBand(1).ReadAsArray().astype(float)
    shade = shade_ds.GetRasterBand(1).ReadAsArray().astype(float)
    area = area_ds.GetRasterBand(1).ReadAsArray().astype(float)
    paths = paths_ds.GetRasterBand(1).ReadAsArray()
    dem_nodata = dem_ds.GetRasterBand(1).GetNoDataValue()
    area_nodata = area_ds.GetRasterBand(1).GetNoDataValue()
    if dem_nodata is not None:
        dem = np.ma.masked_where(np.isclose(dem, dem_nodata), dem)
    flow = np.ma.masked_where((area < 0.02) | np.isclose(area, area_nodata), area)
    streams = np.ma.masked_where(paths <= 0, paths)

    gt = dem_ds.GetGeoTransform()
    extent = [
        gt[0],
        gt[0] + gt[1] * dem_ds.RasterXSize,
        gt[3] + gt[5] * dem_ds.RasterYSize,
        gt[3],
    ]
    envelope = union.Buffer(100).GetEnvelope()
    figure = plt.figure(figsize=(16, 10), dpi=120, facecolor="#e8ece7")
    ax = figure.add_axes([0, 0, 1, 1])
    ax.set_facecolor("#e8ece7")
    ax.imshow(dem, extent=extent, origin="upper", cmap="terrain", alpha=0.58, interpolation="bilinear")
    ax.imshow(shade, extent=extent, origin="upper", cmap="gray", alpha=0.32, interpolation="bilinear")
    flow_cmap = plt.get_cmap("Blues").copy()
    flow_cmap.set_bad((0, 0, 0, 0))
    image = ax.imshow(
        flow,
        extent=extent,
        origin="upper",
        cmap=flow_cmap,
        norm=LogNorm(vmin=0.02, vmax=max(1, float(flow.max()))),
        alpha=0.82,
        interpolation="bilinear",
    )
    stream_cmap = plt.get_cmap("winter").copy()
    stream_cmap.set_bad((0, 0, 0, 0))
    ax.imshow(streams, extent=extent, origin="upper", cmap=stream_cmap, alpha=0.9, interpolation="nearest")

    for geometry, _, _ in map_features:
        draw_geometry(ax, geometry)
    label_offsets = [(15, -26), (15, 22), (15, 12), (15, 16), (15, -22)]
    for crossing, (offset_x, offset_y) in zip(crossings[:5], label_offsets):
        ax.scatter(crossing["x"], crossing["y"], s=70, c="#d14e3f", edgecolors="white", linewidths=1.5, zorder=8)
        ax.text(
            crossing["x"] + offset_x,
            crossing["y"] + offset_y,
            f'{crossing["id"]}  {crossing["contributing_area_ha"]:.2f} ha',
            fontsize=9,
            weight="bold",
            color="#17231d",
            bbox={"boxstyle": "round,pad=0.25", "fc": "white", "ec": "#d6ddd7", "alpha": 0.9},
            zorder=9,
        )

    ax.set_xlim(envelope[0], envelope[1])
    ax.set_ylim(envelope[2], envelope[3])
    ax.set_aspect("equal")
    ax.axis("off")
    add_map_furniture(ax, envelope)
    colorbar_ax = figure.add_axes([0.055, 0.075, 0.22, 0.023])
    colorbar = figure.colorbar(image, cax=colorbar_ax, orientation="horizontal")
    colorbar.ax.tick_params(labelsize=9, length=2)
    colorbar.set_label("Area contribuinte preliminar (ha)", size=10, weight="bold")
    figure.savefig(output, dpi=120, facecolor="#e8ece7")
    plt.close(figure)


def main() -> None:
    ensure_derivatives()
    union, features, map_features = read_boundaries()
    paths = {
        "delivered_dem": DELIVERED_DEM,
        "derived_dtm_1m": DTM_1M,
        "delivered_dem_slope": SLOPE_DEM,
        "derived_dtm_1m_slope": SLOPE_1M,
        "point_density_5m": DENSITY_5M,
    }
    rasters = {name: read_raster(path, union) for name, path in paths.items()}

    raster_metrics = {}
    for name, item in rasters.items():
        dataset = item["dataset"]
        gt = dataset.GetGeoTransform()
        raster_metrics[name] = {
            "resolution_m": [round_number(abs(gt[1]), 4), round_number(abs(gt[5]), 4)],
            "valid_coverage_percent": round_number(
                100 * np.count_nonzero(item["valid"] & item["mask"]) / np.count_nonzero(item["mask"]), 2
            ),
            "statistics": quantiles(item["values"]),
        }
    for name in ("delivered_dem_slope", "derived_dtm_1m_slope"):
        raster_metrics[name]["descriptive_bands_percent"] = slope_bands(rasters[name]["values"])

    density = rasters["point_density_5m"]
    density_cells = density["array"][density["mask"] & np.isfinite(density["array"])]
    context = {}
    for distance in (25, 50, 100):
        ring = union.Buffer(distance).Difference(union)
        ring_mask = geometry_mask(density["dataset"], ring)
        cells = density["array"][ring_mask & np.isfinite(density["array"])]
        context[str(distance)] = {
            "ring_area_ha": round_number(ring.GetArea() / 10_000),
            "cells_with_points_percent": round_number(100 * np.count_nonzero(cells > 0) / cells.size, 2),
        }

    metrics = {
        "status": "preliminary_topographic_audit",
        "source_files_unchanged": True,
        "point_cloud": las_summary(),
        "boundaries": {
            "features": features,
            "feature_count": len(features),
            "total_area_ha": round_number(union.GetArea() / 10_000),
            "total_perimeter_m": round_number(union.Boundary().Length(), 2),
            "valid_geometry": bool(union.IsValid()),
            "extent_xy": [round_number(value, 3) for value in union.GetEnvelope()],
        },
        "rasters_inside_boundaries": raster_metrics,
        "density_inside_boundaries": {
            "approximate_points_from_cell_sum": int(round(density_cells.sum())),
            "approximate_points_per_m2": round_number(density_cells.sum() / union.GetArea(), 3),
            "cells_with_points_percent": round_number(100 * np.count_nonzero(density_cells > 0) / density_cells.size, 2),
        },
        "external_context_coverage": context,
        "surface_comparison": compare_surfaces(rasters["derived_dtm_1m"], DELIVERED_DEM),
        "warnings": [
            "Horizontal CRS is EPSG:31982, but the LAZ WKT is encoded as a local engineering CRS.",
            "The LAZ header has no creation day/year and no vertical datum.",
            "All points are class 2; original rejected/non-ground points are unavailable for reclassification audit.",
            "Slope at 1 m contains terrain microrelief and edge artifacts; use the delivered DEM slope for initial macro reading.",
            "Immediate point coverage does not prove that the full upstream basin and downstream outlet are covered.",
        ],
    }

    if all(path.exists() for path in (FLOW_ACCUMULATION, CONTRIBUTING_AREA, CANDIDATE_PATHS)):
        flow_summary, crossings = preliminary_flow_summary(union, map_features)
        metrics["preliminary_flow"] = flow_summary
        render_flow_map(DERIVED / "preliminary_flow_map.png", map_features, union, crossings)

    with METRICS.open("w", encoding="utf-8") as output:
        json.dump(metrics, output, ensure_ascii=False, indent=2)
        output.write("\n")

    render_map(DERIVED / "topography_map.png", DELIVERED_DEM, HILLSHADE_DEM, map_features, union, "elevation")
    render_map(DERIVED / "slope_map.png", SLOPE_DEM, HILLSHADE_DEM, map_features, union, "slope")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
