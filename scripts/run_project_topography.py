#!/usr/bin/env python3
"""Generate a project-isolated E0 topography package from client assets.

Run through the QGIS Python launcher. A field boundary plus either a terrain
raster or a LAS/LAZ point cloud is required. The stage never modifies sources
and writes only below the explicit output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap
from osgeo import gdal, ogr, osr


gdal.UseExceptions()
ogr.UseExceptions()

SCHEMA_VERSION = "1.0.0"
RELEASE = "E0_TOPOGRAPHY_SCREENING"
GROUND_CLASSIFICATION_FILTER = "Classification[2:2]"


class TopographyError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise TopographyError(message)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def spatial_reference(value: str) -> osr.SpatialReference:
    reference = osr.SpatialReference()
    require(reference.SetFromUserInput(value) == 0, f"Invalid CRS: {value}")
    require(bool(reference.IsProjected()), "A projected CRS is required for metric processing.")
    units = float(reference.GetLinearUnits() or 0.0)
    require(math.isclose(units, 1.0, rel_tol=0.0, abs_tol=1e-9), "Projected CRS must use metres.")
    return reference


def open_boundary(path: Path, layer_name: str | None, id_field: str | None) -> dict[str, Any]:
    datasource = ogr.Open(str(path), 0)
    require(datasource is not None, f"Could not open boundary dataset: {path}")
    layer = datasource.GetLayerByName(layer_name) if layer_name else datasource.GetLayer(0)
    require(layer is not None, f"Boundary layer not found: {layer_name or 0}")
    reference = layer.GetSpatialRef()
    require(reference is not None and bool(reference.IsProjected()), "Boundary CRS must be projected.")
    require(math.isclose(float(reference.GetLinearUnits() or 0.0), 1.0, abs_tol=1e-9), "Boundary CRS must use metres.")
    definition = layer.GetLayerDefn()
    fields = [definition.GetFieldDefn(index).GetName() for index in range(definition.GetFieldCount())]
    if id_field:
        require(id_field in fields, f"Boundary id field does not exist: {id_field}")
    union = None
    ids: list[str] = []
    geometries: list[ogr.Geometry] = []
    for index, feature in enumerate(layer):
        geometry = feature.GetGeometryRef()
        require(geometry is not None and not geometry.IsEmpty() and bool(geometry.IsValid()), "Boundary contains invalid geometry.")
        flat = ogr.GT_Flatten(geometry.GetGeometryType())
        require(flat in {ogr.wkbPolygon, ogr.wkbMultiPolygon}, "Boundary must contain Polygon or MultiPolygon geometry.")
        clone = geometry.Clone()
        geometries.append(clone)
        union = clone.Clone() if union is None else union.Union(clone)
        ids.append(str(feature.GetField(id_field)) if id_field else str(index + 1))
    require(union is not None and geometries, "Boundary contains no features.")
    return {
        "datasource": datasource,
        "layer": layer,
        "layer_name": layer.GetName(),
        "srs": reference.Clone(),
        "wkt": reference.ExportToWkt(),
        "authority": reference.GetAuthorityName(None),
        "authority_code": reference.GetAuthorityCode(None),
        "union": union,
        "geometries": geometries,
        "field_ids": ids,
        "area_ha": float(union.GetArea()) / 10_000.0,
        "extent": union.GetEnvelope(),
    }


def pdal_command() -> str:
    executable = shutil.which("pdal")
    require(bool(executable), "PDAL is required when a point cloud is supplied.")
    return str(executable)


def run_ground_cloud_pipeline(point_cloud: Path, writer: dict[str, Any]) -> None:
    """Run a structured PDAL pipeline that never mixes canopy into the DTM."""

    pipeline = {
        "pipeline": [
            str(point_cloud),
            {"type": "filters.range", "limits": GROUND_CLASSIFICATION_FILTER},
            writer,
        ]
    }
    subprocess.run(
        [pdal_command(), "pipeline", "--stdin"],
        input=json.dumps(pipeline),
        text=True,
        check=True,
    )


def create_dtm_from_cloud(point_cloud: Path, output: Path, crs: str, resolution: float) -> None:
    run_ground_cloud_pipeline(
        point_cloud,
        {
            "type": "writers.gdal",
            "filename": str(output),
            "resolution": resolution,
            "output_type": "idw",
            "radius": resolution * 1.5,
            "window_size": 3,
            "override_srs": crs,
        },
    )


def create_density(point_cloud: Path, output: Path, crs: str, resolution: float = 5.0) -> None:
    run_ground_cloud_pipeline(
        point_cloud,
        {
            "type": "writers.gdal",
            "filename": str(output),
            "resolution": resolution,
            "output_type": "count",
            "radius": resolution / math.sqrt(2.0),
            "override_srs": crs,
        },
    )


def raster_resolution_m(path: Path) -> float | None:
    """Return the conservative larger pixel axis in metres when knowable."""

    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    require(dataset is not None, f"Could not open raster for resolution QA: {path}")
    transform = dataset.GetGeoTransform(can_return_null=True)
    projection = dataset.GetProjection()
    dataset = None
    if transform is None:
        return None
    x_size = math.hypot(float(transform[1]), float(transform[2]))
    y_size = math.hypot(float(transform[4]), float(transform[5]))
    if x_size <= 0 or y_size <= 0:
        return None
    reference = osr.SpatialReference()
    if not projection or reference.ImportFromWkt(projection) != 0 or not bool(reference.IsProjected()):
        return None
    unit_to_m = float(reference.GetLinearUnits() or 0.0)
    if unit_to_m <= 0:
        return None
    return max(x_size, y_size) * unit_to_m


def crop_terrain(source: Path, boundary: dict[str, Any], output: Path, resolution: float) -> None:
    xmin, xmax, ymin, ymax = boundary["extent"]
    target_srs = boundary["srs"].ExportToWkt()
    warped = gdal.Warp(
        str(output), str(source), format="GTiff", dstSRS=target_srs,
        outputBounds=(xmin, ymin, xmax, ymax), xRes=resolution, yRes=resolution,
        resampleAlg="bilinear", multithread=True,
        creationOptions=["TILED=YES", "COMPRESS=DEFLATE", "BIGTIFF=IF_SAFER"],
    )
    require(warped is not None, "Could not warp terrain raster to the project grid.")
    warped = None


def geometry_mask(dataset: gdal.Dataset, geometry: ogr.Geometry) -> np.ndarray:
    target = gdal.GetDriverByName("MEM").Create("", dataset.RasterXSize, dataset.RasterYSize, 1, gdal.GDT_Byte)
    target.SetGeoTransform(dataset.GetGeoTransform())
    target.SetProjection(dataset.GetProjection())
    reference = osr.SpatialReference()
    reference.ImportFromWkt(dataset.GetProjection())
    memory = ogr.GetDriverByName("Memory").CreateDataSource("")
    layer = memory.CreateLayer("mask", srs=reference, geom_type=ogr.wkbPolygon)
    feature = ogr.Feature(layer.GetLayerDefn())
    feature.SetGeometry(geometry)
    layer.CreateFeature(feature)
    gdal.RasterizeLayer(target, [1], layer, burn_values=[1])
    return target.GetRasterBand(1).ReadAsArray().astype(bool)


def raster_stats(path: Path, geometry: ogr.Geometry) -> dict[str, Any]:
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    require(dataset is not None, f"Could not open output raster: {path}")
    band = dataset.GetRasterBand(1)
    values = band.ReadAsArray().astype("float64")
    valid = np.isfinite(values) & geometry_mask(dataset, geometry)
    nodata = band.GetNoDataValue()
    if nodata is not None:
        valid &= ~np.isclose(values, nodata)
    selected = values[valid]
    require(selected.size > 0, f"Raster has no valid cells inside fields: {path.name}")
    percentiles = np.percentile(selected, [0, 5, 50, 95, 100])
    answer = {
        "valid_cell_count": int(selected.size),
        "min": round(float(percentiles[0]), 4),
        "p05": round(float(percentiles[1]), 4),
        "median": round(float(percentiles[2]), 4),
        "p95": round(float(percentiles[3]), 4),
        "max": round(float(percentiles[4]), 4),
        "mean": round(float(selected.mean()), 4),
    }
    dataset = None
    return answer


def create_contours(dtm: Path, output: Path, interval: float) -> int:
    driver = ogr.GetDriverByName("GPKG")
    if output.exists():
        driver.DeleteDataSource(str(output))
    executable = shutil.which("gdal_contour")
    require(bool(executable), "gdal_contour is required to create contour vectors.")
    subprocess.run(
        [
            str(executable), "-a", "elevation_m", "-i", str(interval),
            "-3d", "-f", "GPKG", str(dtm), str(output), "-nln", "contours",
        ],
        check=True,
    )
    datasource = ogr.Open(str(output), 0)
    require(datasource is not None, "Could not open generated contour package.")
    layer = datasource.GetLayerByName("contours")
    require(layer is not None, "Generated contour layer is missing.")
    count = layer.GetFeatureCount()
    datasource = None
    return int(count)


def read_raster_for_map(path: Path) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    dataset = gdal.Open(str(path), gdal.GA_ReadOnly)
    band = dataset.GetRasterBand(1)
    values = band.ReadAsArray().astype("float64")
    nodata = band.GetNoDataValue()
    if nodata is not None:
        values[np.isclose(values, nodata)] = np.nan
    transform = dataset.GetGeoTransform()
    extent = (
        transform[0], transform[0] + transform[1] * dataset.RasterXSize,
        transform[3] + transform[5] * dataset.RasterYSize, transform[3],
    )
    dataset = None
    return values, extent


def plot_boundaries(axis: Any, geometries: list[ogr.Geometry]) -> None:
    for geometry in geometries:
        flat = ogr.GT_Flatten(geometry.GetGeometryType())
        polygons = [geometry] if flat == ogr.wkbPolygon else [geometry.GetGeometryRef(index) for index in range(geometry.GetGeometryCount())]
        for polygon in polygons:
            ring = polygon.GetGeometryRef(0)
            points = ring.GetPoints()
            axis.plot([point[0] for point in points], [point[1] for point in points], color="#f3bd32", linewidth=1.5)


def render_map(raster: Path, output: Path, boundary: dict[str, Any], title: str, cmap: Any) -> None:
    values, extent = read_raster_for_map(raster)
    figure, axis = plt.subplots(figsize=(12, 8), dpi=150)
    image = axis.imshow(values, extent=extent, origin="upper", cmap=cmap)
    plot_boundaries(axis, boundary["geometries"])
    axis.set_title(title, fontsize=15, weight="bold")
    axis.set_aspect("equal")
    axis.set_axis_off()
    figure.colorbar(image, ax=axis, fraction=0.035, pad=0.02)
    figure.tight_layout()
    figure.savefig(output, bbox_inches="tight", facecolor="#f4f6f3")
    plt.close(figure)


def run(args: argparse.Namespace) -> dict[str, Any]:
    boundary_path = args.boundary.resolve()
    terrain_path = args.terrain.resolve() if args.terrain else None
    point_cloud_path = args.point_cloud.resolve() if args.point_cloud else None
    require(boundary_path.is_file(), f"Boundary file is missing: {boundary_path}")
    require(bool(terrain_path or point_cloud_path), "Supply --terrain or --point-cloud.")
    if terrain_path:
        require(terrain_path.is_file(), f"Terrain raster is missing: {terrain_path}")
    if point_cloud_path:
        require(point_cloud_path.is_file(), f"Point cloud is missing: {point_cloud_path}")
    require(args.resolution_m > 0, "Resolution must be positive.")
    require(args.contour_interval_m > 0, "Contour interval must be positive.")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    boundary = open_boundary(boundary_path, args.boundary_layer, args.field_id_column)
    detected_crs = (
        f"{boundary['authority']}:{boundary['authority_code']}"
        if boundary["authority"] and boundary["authority_code"] else boundary["wkt"]
    )
    target_crs = args.crs or detected_crs
    target_reference = spatial_reference(target_crs)
    require(
        bool(target_reference.IsSame(boundary["srs"])),
        f"Configured CRS {target_crs} does not match the boundary CRS.",
    )

    dtm = output_dir / "dtm.tif"
    source_native_resolution_m = None
    if terrain_path:
        source_native_resolution_m = raster_resolution_m(terrain_path)
        crop_terrain(terrain_path, boundary, dtm, args.resolution_m)
        dtm_source = "CLIENT_TERRAIN"
    else:
        raw_cloud_surface = output_dir / "_point_cloud_surface.tif"
        create_dtm_from_cloud(point_cloud_path, raw_cloud_surface, target_crs, args.resolution_m)
        crop_terrain(raw_cloud_surface, boundary, dtm, args.resolution_m)
        raw_cloud_surface.unlink(missing_ok=True)
        dtm_source = "POINT_CLOUD_IDW_SCREENING"

    output_grid_resolution_m = raster_resolution_m(dtm)
    require(output_grid_resolution_m is not None, "Generated DTM grid resolution could not be verified in metres.")
    if terrain_path:
        effective_resolution_m = (
            max(source_native_resolution_m, output_grid_resolution_m)
            if source_native_resolution_m is not None
            else None
        )
        upsampling_detected = (
            source_native_resolution_m is not None
            and output_grid_resolution_m + 1e-9 < source_native_resolution_m
        )
        resolution_gate_basis = "MAX_SOURCE_NATIVE_AND_OUTPUT_GRID"
    else:
        # A point cloud has no raster pixel size to preserve. At E0 the
        # effective grid is the class-2 ground-only interpolation grid.
        effective_resolution_m = output_grid_resolution_m
        upsampling_detected = False
        resolution_gate_basis = "GROUND_CLASS_2_POINT_CLOUD_OUTPUT_GRID"

    slope_percent = output_dir / "slope_percent.tif"
    slope_degrees = output_dir / "slope_degrees.tif"
    hillshade = output_dir / "hillshade.tif"
    gdal.DEMProcessing(str(slope_percent), str(dtm), "slope", slopeFormat="percent", computeEdges=True)
    gdal.DEMProcessing(str(slope_degrees), str(dtm), "slope", slopeFormat="degree", computeEdges=True)
    gdal.DEMProcessing(str(hillshade), str(dtm), "hillshade", computeEdges=True)
    contours = output_dir / "contours.gpkg"
    contour_count = create_contours(dtm, contours, args.contour_interval_m)

    density = None
    if point_cloud_path:
        density = output_dir / "point_density_5m.tif"
        create_density(point_cloud_path, density, target_crs)

    terrain_map = output_dir / "topography_map.png"
    slope_map = output_dir / "slope_map.png"
    render_map(
        dtm, terrain_map, boundary, "Topografia do projeto | triagem E0",
        LinearSegmentedColormap.from_list("terrain_project", ["#d9e7d1", "#8eb17a", "#466d51"]),
    )
    render_map(
        slope_percent, slope_map, boundary, "Declividade do projeto | porcentagem",
        LinearSegmentedColormap.from_list("slope_project", ["#e8efe3", "#8eb66f", "#efd061", "#d86e43", "#a33739"]),
    )

    outputs = {
        "dtm": artifact(dtm),
        "slope_percent": artifact(slope_percent),
        "slope_degrees": artifact(slope_degrees),
        "hillshade": artifact(hillshade),
        "contours": artifact(contours),
        "topography_map": artifact(terrain_map),
        "slope_map": artifact(slope_map),
    }
    if density:
        outputs["point_density"] = artifact(density)

    try:
        from scripts.terrain_web_mesh import generate_terrain_mesh
    except ModuleNotFoundError:
        from terrain_web_mesh import generate_terrain_mesh
    terrain_mesh_path = output_dir / "terrain_inspection_mesh.json"
    try:
        generate_terrain_mesh(dtm, terrain_mesh_path)
        outputs["terrain_inspection_mesh"] = artifact(terrain_mesh_path)
        terrain_mesh_status = {"status": "AVAILABLE", "inspection_only": True}
    except ValueError as exc:
        terrain_mesh_status = {"status": "UNAVAILABLE", "reason": str(exc), "inspection_only": True}

    manifest = {
        "terrain_mesh": terrain_mesh_status,
        "schema_version": SCHEMA_VERSION,
        "artifact_id": "terraflux-project-topography-package",
        "release": RELEASE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_id": args.project_id,
        "guidance_authorized": False,
        "hydraulic_status": "NOT_EVALUATED",
        "inputs": {
            "boundary": artifact(boundary_path),
            "terrain": artifact(terrain_path) if terrain_path else None,
            "point_cloud": (
                {
                    **artifact(point_cloud_path),
                    "classification_filter": GROUND_CLASSIFICATION_FILTER,
                    "classification_scope": "ASPRS_CLASS_2_GROUND_ONLY",
                }
                if point_cloud_path else None
            ),
        },
        "configuration": {
            "target_crs": target_crs,
            "resolution_m": output_grid_resolution_m,
            "requested_output_resolution_m": args.resolution_m,
            "source_native_resolution_m": source_native_resolution_m,
            "output_grid_resolution_m": output_grid_resolution_m,
            "effective_resolution_m": effective_resolution_m,
            "contour_interval_m": args.contour_interval_m,
            "field_id_column": args.field_id_column,
            "dtm_source": dtm_source,
            "point_cloud_classification_filter": (
                GROUND_CLASSIFICATION_FILTER if point_cloud_path else None
            ),
        },
        "resolution_provenance": {
            "source_type": "DTM_DEM" if terrain_path else "POINT_CLOUD",
            "source_native_resolution_m": source_native_resolution_m,
            "requested_output_resolution_m": args.resolution_m,
            "output_grid_resolution_m": output_grid_resolution_m,
            "effective_resolution_m": effective_resolution_m,
            "upsampling_detected": upsampling_detected,
            "gate_basis": resolution_gate_basis,
            "ground_classification_filter": (
                GROUND_CLASSIFICATION_FILTER if point_cloud_path else None
            ),
        },
        "scope": {
            "field_ids": boundary["field_ids"],
            "field_count": len(boundary["field_ids"]),
            "area_ha": round(boundary["area_ha"], 4),
        },
        "metrics": {
            "dtm": raster_stats(dtm, boundary["union"]),
            "slope_percent": raster_stats(slope_percent, boundary["union"]),
            "contour_feature_count": contour_count,
        },
        "outputs": outputs,
        "release_limitations": [
            "E0_SCREENING_ONLY",
            "VERTICAL_ACCURACY_NOT_VALIDATED",
            "HYDROLOGY_NOT_EVALUATED",
            "SOIL_AND_FLEET_NOT_EVALUATED",
            "NOT_AUTHORIZED_FOR_GUIDANCE",
            "POINT_CLOUD_GROUND_CLASSIFICATION_NOT_FIELD_VALIDATED" if point_cloud_path else "SOURCE_RASTER_VERTICAL_ACCURACY_NOT_VALIDATED",
        ],
    }
    manifest_path = output_dir / "topography_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {**manifest, "manifest": artifact(manifest_path)}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--project-id", required=True)
    value.add_argument("--boundary", type=Path, required=True)
    value.add_argument("--boundary-layer")
    value.add_argument("--field-id-column")
    value.add_argument("--terrain", type=Path)
    value.add_argument("--point-cloud", type=Path)
    value.add_argument("--crs")
    value.add_argument("--resolution-m", type=float, default=1.0)
    value.add_argument("--contour-interval-m", type=float, default=1.0)
    value.add_argument("--output-dir", type=Path, required=True)
    return value


def main() -> int:
    try:
        result = run(parser().parse_args())
    except (TopographyError, subprocess.CalledProcessError, RuntimeError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({
        "status": "PASS",
        "release": result["release"],
        "manifest": result["manifest"],
        "field_count": result["scope"]["field_count"],
        "area_ha": result["scope"]["area_ha"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
