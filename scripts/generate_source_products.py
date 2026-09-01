"""Generate and summarize topographic products from the source dataset.

Run with the QGIS Python environment:

    & 'C:\\Program Files\\QGIS 3.32.1\\bin\\python-qgis.bat' `
      '.\\scripts\\generate_source_products.py'

Every output is calculated from the LAZ, DEM and field boundaries. Presentation
PDFs are not read and do not provide thresholds, styles or acceptance criteria.
Source files are never modified.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap
from matplotlib.lines import Line2D
from osgeo import gdal, ogr, osr


gdal.UseExceptions()
ogr.UseExceptions()

REPO = Path(__file__).resolve().parents[1]
DATASET = REPO / "dataset"
DERIVED = DATASET / "derived"
DEM = DATASET / "DEM.tif"
BOUNDARY = DATASET / "Contorno.shp"
LAZ = DATASET / "TERRENO_LIMPO.LAZ"
DENSITY_COUNT = DERIVED / "density_5m.tif"
SLOPE_DEGREES = DERIVED / "slope_dem_degrees.tif"
DENSITY_KM2 = DERIVED / "density_points_per_km2_5m.tif"
CONTOURS = DERIVED / "contours_1m.gpkg"
BASINS = DERIVED / "flow_basins.tif"
FLOW_PATHS = DERIVED / "candidate_flow_paths_1ha.tif"
CONTRIBUTING_AREA = DERIVED / "contributing_area_ha.tif"
HILLSHADE = DERIVED / "hillshade_dem.tif"
OUTPUT_JSON = DERIVED / "source_product_metrics.json"
FLOW_SCREENING_THRESHOLDS_HA = [0.10, 0.25, 0.50, 1.00, 2.00, 5.00]


def rounded(value: float | None, digits: int = 4) -> float | None:
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), digits)


def quantiles(values: np.ndarray) -> dict[str, float | int] | None:
    values = values[np.isfinite(values)]
    if values.size == 0:
        return None
    levels = np.percentile(values, [0, 5, 25, 50, 75, 90, 95, 99, 100])
    return {
        "count": int(values.size),
        "min": rounded(levels[0]),
        "p05": rounded(levels[1]),
        "p25": rounded(levels[2]),
        "median": rounded(levels[3]),
        "p75": rounded(levels[4]),
        "p90": rounded(levels[5]),
        "p95": rounded(levels[6]),
        "p99": rounded(levels[7]),
        "max": rounded(levels[8]),
        "mean": rounded(values.mean()),
        "stddev": rounded(values.std()),
    }


def read_boundary() -> tuple[ogr.Geometry, list[ogr.Geometry]]:
    source = ogr.Open(str(BOUNDARY))
    layer = source.GetLayer(0)
    union = None
    geometries = []
    for feature in layer:
        geometry = feature.GetGeometryRef().Clone()
        geometries.append(geometry)
        union = geometry if union is None else union.Union(geometry)
    if union is None:
        raise RuntimeError("Boundary layer has no features.")
    return union, geometries


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


def raster_values(path: Path, geometry: ogr.Geometry) -> tuple[gdal.Dataset, np.ndarray, np.ndarray]:
    dataset = gdal.Open(str(path))
    band = dataset.GetRasterBand(1)
    array = band.ReadAsArray().astype("float64")
    valid = np.isfinite(array)
    nodata = band.GetNoDataValue()
    if nodata is not None:
        valid &= ~np.isclose(array, nodata)
    mask = geometry_mask(dataset, geometry)
    return dataset, array, valid & mask


def ensure_derivatives() -> None:
    DERIVED.mkdir(parents=True, exist_ok=True)
    if not SLOPE_DEGREES.exists():
        gdal.DEMProcessing(
            str(SLOPE_DEGREES),
            str(DEM),
            "slope",
            slopeFormat="degree",
            computeEdges=True,
        )

    if not DENSITY_KM2.exists():
        source = gdal.Open(str(DENSITY_COUNT))
        source_band = source.GetRasterBand(1)
        count = source_band.ReadAsArray().astype("float32")
        pixel_area = abs(source.GetGeoTransform()[1] * source.GetGeoTransform()[5])
        points_per_km2 = count * (1_000_000.0 / pixel_area)
        target = gdal.GetDriverByName("GTiff").Create(
            str(DENSITY_KM2),
            source.RasterXSize,
            source.RasterYSize,
            1,
            gdal.GDT_Float32,
            options=["COMPRESS=DEFLATE", "TILED=YES"],
        )
        target.SetGeoTransform(source.GetGeoTransform())
        target.SetProjection(source.GetProjection())
        target_band = target.GetRasterBand(1)
        target_band.WriteArray(points_per_km2)
        target_band.SetNoDataValue(-9999)
        target_band.FlushCache()
        target = None

def percentage_bands(values: np.ndarray, breaks: list[float]) -> dict[str, float]:
    result = {}
    for lower, upper in zip(breaks, breaks[1:]):
        label = f"{lower:g}-{upper:g}"
        result[label] = rounded(
            100 * np.count_nonzero((values >= lower) & (values < upper)) / values.size,
            2,
        )
    result[f">={breaks[-1]:g}"] = rounded(
        100 * np.count_nonzero(values >= breaks[-1]) / values.size,
        2,
    )
    return result


def contour_parts(geometry: ogr.Geometry):
    kind = ogr.GT_Flatten(geometry.GetGeometryType())
    if kind == ogr.wkbLineString:
        yield geometry
    elif kind == ogr.wkbMultiLineString:
        for index in range(geometry.GetGeometryCount()):
            yield geometry.GetGeometryRef(index)


def contour_summary(union: ogr.Geometry) -> tuple[dict, list[tuple[ogr.Geometry, float]]]:
    source = ogr.Open(str(CONTOURS))
    layer = source.GetLayer(0)
    features_total = 0
    features_intersecting = 0
    length_total = 0.0
    length_inside = 0.0
    elevations = []
    drawable = []
    for feature in layer:
        geometry = feature.GetGeometryRef().Clone()
        elevation = float(feature.GetField("elev"))
        features_total += 1
        length_total += geometry.Length()
        if geometry.Intersects(union):
            clipped = geometry.Intersection(union)
            if clipped and not clipped.IsEmpty():
                features_intersecting += 1
                length_inside += clipped.Length()
                elevations.append(elevation)
                drawable.append((clipped, elevation))
    distinct_levels = sorted(set(elevations))
    return {
        "interval_m": 1.0,
        "feature_count_total": features_total,
        "feature_count_intersecting_fields": features_intersecting,
        "distinct_levels_inside_fields": len(distinct_levels),
        "elevation_min_inside_fields_m": rounded(min(distinct_levels) if distinct_levels else None),
        "elevation_max_inside_fields_m": rounded(max(distinct_levels) if distinct_levels else None),
        "length_total_km": rounded(length_total / 1000, 3),
        "length_inside_fields_km": rounded(length_inside / 1000, 3),
    }, drawable


def raster_extent(dataset: gdal.Dataset) -> list[float]:
    transform = dataset.GetGeoTransform()
    return [
        transform[0],
        transform[0] + transform[1] * dataset.RasterXSize,
        transform[3] + transform[5] * dataset.RasterYSize,
        transform[3],
    ]


def draw_boundary(ax: plt.Axes, geometry: ogr.Geometry, color: str = "#f5d04c") -> None:
    kind = ogr.GT_Flatten(geometry.GetGeometryType())
    if kind == ogr.wkbMultiPolygon:
        for index in range(geometry.GetGeometryCount()):
            draw_boundary(ax, geometry.GetGeometryRef(index), color)
        return
    if kind != ogr.wkbPolygon:
        return
    for index in range(geometry.GetGeometryCount()):
        points = np.asarray(geometry.GetGeometryRef(index).GetPoints())
        ax.plot(points[:, 0], points[:, 1], color="#17231d", linewidth=2.8, alpha=0.75)
        ax.plot(points[:, 0], points[:, 1], color=color, linewidth=1.35)


def figure_axes() -> tuple[plt.Figure, plt.Axes]:
    figure = plt.figure(figsize=(16, 10), dpi=120, facecolor="#e8ece7")
    axes = figure.add_axes([0, 0, 1, 1])
    axes.set_facecolor("#e8ece7")
    return figure, axes


def finish_map(
    figure: plt.Figure,
    axes: plt.Axes,
    union: ogr.Geometry,
    image,
    label: str,
    output: Path,
) -> None:
    envelope = union.Buffer(100).GetEnvelope()
    axes.set_xlim(envelope[0], envelope[1])
    axes.set_ylim(envelope[2], envelope[3])
    axes.set_aspect("equal")
    axes.axis("off")
    colorbar_axes = figure.add_axes([0.055, 0.075, 0.24, 0.023])
    colorbar = figure.colorbar(image, cax=colorbar_axes, orientation="horizontal")
    colorbar.ax.tick_params(labelsize=9, length=2)
    colorbar.set_label(label, size=10, weight="bold")
    figure.savefig(output, dpi=120, facecolor="#e8ece7")
    plt.close(figure)


def render_slope_map(union: ogr.Geometry) -> None:
    slope_ds = gdal.Open(str(SLOPE_DEGREES))
    shade_ds = gdal.Open(str(HILLSHADE))
    slope = slope_ds.GetRasterBand(1).ReadAsArray().astype(float)
    shade = shade_ds.GetRasterBand(1).ReadAsArray().astype(float)
    nodata = slope_ds.GetRasterBand(1).GetNoDataValue()
    if nodata is not None:
        slope = np.ma.masked_where(np.isclose(slope, nodata), slope)
    display_max = float(np.percentile(slope.compressed(), 99.5))
    cmap = LinearSegmentedColormap.from_list(
        "slope_degrees", ["#d8e7cf", "#83ad67", "#f0c953", "#e47a3e", "#a93636"]
    )
    figure, axes = figure_axes()
    image = axes.imshow(
        slope,
        extent=raster_extent(slope_ds),
        origin="upper",
        cmap=cmap,
        vmin=0,
        vmax=display_max,
        interpolation="bilinear",
    )
    axes.imshow(shade, extent=raster_extent(shade_ds), origin="upper", cmap="gray", alpha=0.26)
    draw_boundary(axes, union)
    finish_map(figure, axes, union, image, "Declividade (graus)", DERIVED / "slope_degrees_map.png")


def render_density_map(union: ogr.Geometry) -> None:
    density_ds = gdal.Open(str(DENSITY_KM2))
    density = density_ds.GetRasterBand(1).ReadAsArray().astype(float)
    nodata = density_ds.GetRasterBand(1).GetNoDataValue()
    density = np.ma.masked_where(np.isclose(density, nodata), density)
    display_max = float(np.percentile(density.compressed(), 99.5))
    cmap = LinearSegmentedColormap.from_list(
        "density", ["#edf0eb", "#b9d8a3", "#f1cf57", "#d8633b", "#8f2f35"]
    )
    figure, axes = figure_axes()
    image = axes.imshow(
        density,
        extent=raster_extent(density_ds),
        origin="upper",
        cmap=cmap,
        vmin=0,
        vmax=display_max,
        interpolation="nearest",
    )
    draw_boundary(axes, union)
    finish_map(
        figure,
        axes,
        union,
        image,
        "Densidade (pontos/km2)",
        DERIVED / "density_map.png",
    )


def render_contour_map(union: ogr.Geometry, contours: list[tuple[ogr.Geometry, float]]) -> None:
    dem_ds = gdal.Open(str(DEM))
    shade_ds = gdal.Open(str(HILLSHADE))
    dem = dem_ds.GetRasterBand(1).ReadAsArray().astype(float)
    shade = shade_ds.GetRasterBand(1).ReadAsArray().astype(float)
    nodata = dem_ds.GetRasterBand(1).GetNoDataValue()
    if nodata is not None:
        dem = np.ma.masked_where(np.isclose(dem, nodata), dem)
    figure, axes = figure_axes()
    image = axes.imshow(
        dem,
        extent=raster_extent(dem_ds),
        origin="upper",
        cmap="terrain",
        vmin=418,
        vmax=479,
        interpolation="bilinear",
    )
    axes.imshow(shade, extent=raster_extent(shade_ds), origin="upper", cmap="gray", alpha=0.22)
    for geometry, elevation in contours:
        major = int(round(elevation)) % 5 == 0
        for part in contour_parts(geometry):
            points = np.asarray(part.GetPoints())
            if len(points) > 1:
                axes.plot(
                    points[:, 0],
                    points[:, 1],
                    color="#f7f9f3" if not major else "#21362b",
                    linewidth=0.42 if not major else 0.85,
                    alpha=0.78 if not major else 0.9,
                )
    draw_boundary(axes, union)
    finish_map(figure, axes, union, image, "Elevacao (m), curvas de 1 m", DERIVED / "contours_map.png")


def render_flow_sensitivity_map(union: ogr.Geometry) -> None:
    if not CONTRIBUTING_AREA.exists():
        return
    dem_ds = gdal.Open(str(DEM))
    shade_ds = gdal.Open(str(HILLSHADE))
    area_ds = gdal.Open(str(CONTRIBUTING_AREA))
    dem = dem_ds.GetRasterBand(1).ReadAsArray().astype(float)
    shade = shade_ds.GetRasterBand(1).ReadAsArray().astype(float)
    area = area_ds.GetRasterBand(1).ReadAsArray().astype(float)
    nodata = dem_ds.GetRasterBand(1).GetNoDataValue()
    if nodata is not None:
        dem = np.ma.masked_where(np.isclose(dem, nodata), dem)
    colors = ["#8fe2dc", "#5bc9c5", "#2ca9b6", "#187f9e", "#135f83", "#123d64"]
    figure, axes = figure_axes()
    axes.imshow(
        dem,
        extent=raster_extent(dem_ds),
        origin="upper",
        cmap="terrain",
        vmin=418,
        vmax=479,
        interpolation="bilinear",
        alpha=0.62,
    )
    axes.imshow(shade, extent=raster_extent(shade_ds), origin="upper", cmap="gray", alpha=0.28)
    legend = []
    for threshold, color in zip(FLOW_SCREENING_THRESHOLDS_HA, colors):
        visible = np.ma.masked_where(area < threshold, np.ones(area.shape))
        cmap = LinearSegmentedColormap.from_list(f"stream_{threshold}", [color, color])
        axes.imshow(
            visible,
            extent=raster_extent(area_ds),
            origin="upper",
            cmap=cmap,
            vmin=0,
            vmax=1,
            alpha=0.92,
            interpolation="nearest",
        )
        legend.append(Line2D([0], [0], color=color, lw=2.5, label=f">= {threshold:g} ha"))
    draw_boundary(axes, union)
    envelope = union.Buffer(100).GetEnvelope()
    axes.set_xlim(envelope[0], envelope[1])
    axes.set_ylim(envelope[2], envelope[3])
    axes.set_aspect("equal")
    axes.axis("off")
    axes.legend(
        handles=legend,
        loc="lower left",
        bbox_to_anchor=(0.04, 0.05),
        frameon=True,
        framealpha=0.92,
        fontsize=8,
        title="Area contribuinte aparente",
        title_fontsize=9,
    )
    axes.text(
        envelope[0] + 35,
        envelope[3] - 38,
        "Sensibilidade de rede topografica",
        fontsize=12,
        weight="bold",
        color="#173745",
        bbox={"boxstyle": "round,pad=0.35", "fc": "white", "ec": "#cfdadd", "alpha": 0.92},
    )
    axes.text(
        envelope[0] + 35,
        envelope[3] - 70,
        "Corredores por limiar; bacia externa ainda incompleta",
        fontsize=9,
        color="#40555d",
        bbox={"boxstyle": "round,pad=0.3", "fc": "white", "ec": "#d8e0e1", "alpha": 0.9},
    )
    figure.savefig(DERIVED / "flow_sensitivity_map.png", dpi=120, facecolor="#e8ece7")
    plt.close(figure)


def flow_sensitivity_summary(union: ogr.Geometry) -> dict | None:
    if not CONTRIBUTING_AREA.exists():
        return None
    dataset = gdal.Open(str(CONTRIBUTING_AREA))
    band = dataset.GetRasterBand(1)
    area = band.ReadAsArray().astype("float64")
    valid = np.isfinite(area)
    nodata = band.GetNoDataValue()
    if nodata is not None:
        valid &= ~np.isclose(area, nodata)
    field_mask = geometry_mask(dataset, union)
    pixel_size = abs(dataset.GetGeoTransform()[1])
    rows = []
    for threshold in FLOW_SCREENING_THRESHOLDS_HA:
        active = valid & (area >= threshold)
        inside = active & field_mask
        rows.append(
            {
                "threshold_ha": threshold,
                "convergence_pixels_full_raster": int(np.count_nonzero(active)),
                "convergence_pixels_inside_fields": int(np.count_nonzero(inside)),
                "centerline_upper_bound_full_raster_km": rounded(
                    np.count_nonzero(active) * pixel_size / 1000, 3
                ),
                "centerline_upper_bound_inside_fields_km": rounded(
                    np.count_nonzero(inside) * pixel_size / 1000, 3
                ),
            }
        )
    if not rows:
        return None
    return {
        "method": "Thresholds over absolute signed GRASS r.watershed MFD contributing area",
        "thresholds": rows,
        "status": "topographic_convergence_sensitivity_not_vector_streams",
        "warning": (
            "Most field cells are marked as possibly influenced by flow outside the raster. "
            "The threshold masks are visual corridors, not discharge, vector streams or valid basins."
        ),
    }


def basin_summary(union: ogr.Geometry) -> dict | None:
    if not BASINS.exists():
        return None
    dataset = gdal.Open(str(BASINS))
    band = dataset.GetRasterBand(1)
    array = band.ReadAsArray().astype("float64")
    nodata = band.GetNoDataValue()
    valid = np.isfinite(array)
    if nodata is not None:
        valid &= ~np.isclose(array, nodata)
    boundary_pixels = geometry_mask(dataset, union)
    positive_total = array[valid & (array > 0)]
    positive_inside = array[valid & boundary_pixels & (array > 0)]
    return {
        "method": "GRASS r.watershed MFD on unconditioned DEM",
        "positive_basin_ids_full_raster": int(np.unique(positive_total).size),
        "positive_basin_ids_inside_fields": int(np.unique(positive_inside).size),
        "classified_pixels_full_raster": int(positive_total.size),
        "classified_pixels_inside_fields": int(positive_inside.size),
        "classified_coverage_inside_fields_percent": rounded(
            100 * positive_inside.size / np.count_nonzero(boundary_pixels), 2
        ),
        "status": "screening_only_incomplete_external_context",
    }


def flow_path_summary(union: ogr.Geometry) -> dict | None:
    if not FLOW_PATHS.exists():
        return None
    dataset = gdal.Open(str(FLOW_PATHS))
    band = dataset.GetRasterBand(1)
    array = band.ReadAsArray().astype("float64")
    nodata = band.GetNoDataValue()
    valid = np.isfinite(array)
    if nodata is not None:
        valid &= ~np.isclose(array, nodata)
    boundary_pixels = geometry_mask(dataset, union)
    active_total = valid & (array > 0)
    active_inside = active_total & boundary_pixels
    pixel_size = abs(dataset.GetGeoTransform()[1])
    return {
        "threshold_contributing_area_ha": 1.0,
        "raster_path_pixels_full_raster": int(np.count_nonzero(active_total)),
        "raster_path_pixels_inside_fields": int(np.count_nonzero(active_inside)),
        "centerline_length_proxy_full_raster_km": rounded(
            np.count_nonzero(active_total) * pixel_size / 1000, 3
        ),
        "centerline_length_proxy_inside_fields_km": rounded(
            np.count_nonzero(active_inside) * pixel_size / 1000, 3
        ),
        "status": "topographic_convergence_not_discharge",
    }


def product_matrix() -> list[dict]:
    return [
        {
            "product": "Inventario e QA LAS/LAZ",
            "source": LAZ.name,
            "release_level": "L0_derived",
            "status": "available_with_quality_gate",
            "qualification": "Ground-classified cloud with RGB; absolute accuracy needs independent checkpoints and a declared vertical datum.",
        },
        {
            "product": "Modelo digital do terreno, relevo e hillshade",
            "source": "DEM.tif e dtm_1m.tif derivado do LAZ",
            "release_level": "L0_derived",
            "status": "available_preliminary",
            "qualification": "Review classification, edges, voids and breaklines; internal agreement is not absolute vertical accuracy.",
        },
        {
            "product": "Curvas de nivel",
            "source": CONTOURS.name,
            "release_level": "L0_derived",
            "status": "available_preliminary",
            "qualification": "Executive contour interval depends on verified vertical accuracy and artifact review.",
        },
        {
            "product": "Declividade e derivados do terreno",
            "source": SLOPE_DEGREES.name,
            "release_level": "L0_derived",
            "status": "available",
            "qualification": "Scale, analysis window and smoothing must accompany every terrain derivative.",
        },
        {
            "product": "Densidade, cobertura e lacunas",
            "source": DENSITY_KM2.name,
            "release_level": "L0_derived",
            "status": "available",
            "qualification": "Report points per square metre, percentiles and spatial coverage without a saturated legend.",
        },
        {
            "product": "Bacias hidrograficas",
            "source": BASINS.name,
            "release_level": "L1_screening",
            "status": "blocked_incomplete_context",
            "qualification": "Requires the complete contributing area, verified outlets and a conditioned terrain model.",
        },
        {
            "product": "Direcao de fluxo e area contribuinte",
            "source": "contributing_area_ha.tif",
            "release_level": "L1_screening",
            "status": "available_topographic_screening",
            "qualification": "Sensitivity corridors are not discharge, watercourses or validated drainage basins.",
        },
        {
            "product": "Chuva-vazao e inundacao hidraulica",
            "source": None,
            "release_level": "L2_conditioned_to_L3_validated",
            "status": "blocked_missing_hydrologic_and_hydraulic_inputs",
            "qualification": "Requires rainfall, soil, infiltration, full basin, structures, roughness, boundary conditions and calibration.",
        },
    ]


def main() -> None:
    ensure_derivatives()
    union, _ = read_boundary()
    slope_ds, slope, slope_inside = raster_values(SLOPE_DEGREES, union)
    density_ds, density, density_inside = raster_values(DENSITY_KM2, union)
    dem_ds, dem, dem_inside = raster_values(DEM, union)
    contours, drawable_contours = contour_summary(union)

    slope_values = slope[slope_inside]
    density_values = density[density_inside]
    dem_values = dem[dem_inside]
    result = {
        "status": "source_driven_topographic_product_generation",
        "source_files_unchanged": True,
        "reference_policy": "Presentation PDFs are visual examples only and are not read, calibrated against or reproduced.",
        "dem": {
            "global_statistics_m": {
                "min": rounded(np.nanmin(dem[dem != dem_ds.GetRasterBand(1).GetNoDataValue()])),
                "max": rounded(np.nanmax(dem[dem != dem_ds.GetRasterBand(1).GetNoDataValue()])),
            },
            "inside_fields_statistics_m": quantiles(dem_values),
        },
        "slope_degrees": {
            "inside_fields_statistics": quantiles(slope_values),
            "bands_percent": percentage_bands(slope_values, [0, 2.5, 5, 7.5, 10, 12.5, 15]),
            "above_15_degrees_percent": rounded(
                100 * np.count_nonzero(slope_values > 15) / slope_values.size, 3
            ),
        },
        "point_density": {
            "unit": "points_per_square_kilometre",
            "inside_fields_statistics": quantiles(density_values),
            "equivalent_median_points_per_m2": rounded(np.median(density_values) / 1_000_000, 3),
        },
        "contours": contours,
        "basins": basin_summary(union),
        "flow_paths": flow_path_summary(union),
        "flow_network_sensitivity": flow_sensitivity_summary(union),
        "hydrologic_and_hydraulic_simulation": {
            "status": "blocked_missing_inputs",
            "required_inputs": [
                "complete contributing basin and downstream receptors",
                "verified outlets, roads, culverts, channels and conservation structures",
                "design rainfall IDF, return period and hyetograph",
                "soil profile, infiltration and initial moisture",
                "roughness, boundary conditions and calibration evidence",
            ],
            "warning": "A static elevation mask is not a rainfall-runoff or hydraulic inundation simulation.",
        },
        "products": product_matrix(),
    }
    with OUTPUT_JSON.open("w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")

    render_slope_map(union)
    render_density_map(union)
    render_contour_map(union, drawable_contours)
    render_flow_sensitivity_map(union)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
