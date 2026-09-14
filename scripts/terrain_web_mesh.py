"""Bounded terrain mesh for inspection; no triangles across source NoData."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
from osgeo import gdal, osr


def generate_terrain_mesh(source_path: Path, output_path: Path, max_axis_vertices: int = 129):
    if not 3 <= max_axis_vertices <= 257:
        raise ValueError("mesh dimension must be between 3 and 257")
    source = gdal.Open(str(source_path))
    if source is None or source.RasterXSize < 2 or source.RasterYSize < 2:
        raise ValueError("terrain requires at least two rows and columns")
    crs = osr.SpatialReference(wkt=source.GetProjection())
    if not crs.IsProjected() or not math.isclose(crs.GetLinearUnits(), 1.0, abs_tol=1e-9):
        raise ValueError("terrain requires a projected metric CRS")
    crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    geographic = osr.SpatialReference()
    geographic.ImportFromEPSG(4326)
    geographic.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(crs, geographic)
    xs = np.unique(np.linspace(0, source.RasterXSize - 1, min(max_axis_vertices, source.RasterXSize), dtype=int))
    ys = np.unique(np.linspace(0, source.RasterYSize - 1, min(max_axis_vertices, source.RasterYSize), dtype=int))
    band = source.GetRasterBand(1)
    mask = band.GetMaskBand()
    gt = source.GetGeoTransform()
    vertices, indexes = [], {}
    for row, y in enumerate(ys):
        values = band.ReadAsArray(0, int(y), source.RasterXSize, 1)[0]
        valid = mask.ReadAsArray(0, int(y), source.RasterXSize, 1)[0]
        for col, x in enumerate(xs):
            if not valid[x] or not math.isfinite(float(values[x])):
                continue
            px = gt[0] + (float(x) + .5) * gt[1] + (float(y) + .5) * gt[2]
            py = gt[3] + (float(x) + .5) * gt[4] + (float(y) + .5) * gt[5]
            lon, lat, _ = transform.TransformPoint(px, py)
            if not math.isfinite(lon) or not math.isfinite(lat) or abs(lat) > 85 or abs(lon) > 180:
                raise ValueError("terrain outside supported web coverage")
            indexes[(row, col)] = len(vertices)
            vertices.append([lon, lat, float(values[x])])
    triangles = []
    omitted = 0
    for row in range(len(ys) - 1):
        start, end = int(ys[row]), int(ys[row + 1])
        strip = band.ReadAsArray(0, start, source.RasterXSize, end - start + 1)
        valid = (mask.ReadAsArray(0, start, source.RasterXSize, end - start + 1) != 0) & np.isfinite(strip)
        for col in range(len(xs) - 1):
            corners = [(row, col), (row, col + 1), (row + 1, col), (row + 1, col + 1)]
            if not all(key in indexes for key in corners) or not valid[:, int(xs[col]):int(xs[col + 1]) + 1].all():
                omitted += 1
                continue
            a, b, c, d = [indexes[key] for key in corners]
            triangles.extend([[a, c, b], [b, c, d]])
    if not triangles:
        raise ValueError("no valid terrain triangles remain")
    # Stream the source checksum to avoid loading a large raster into memory.
    digest = hashlib.sha256()
    with source_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    result = {
        "schema_version": "1.0.0", "type": "TerrainInspectionMesh",
        "horizontal_crs": "OGC:CRS84", "source_crs_wkt": crs.ExportToWkt(),
        "vertical_reference": "UNSPECIFIED_SOURCE_DATUM", "vertical_unit": "m",
        "source_sha256": digest.hexdigest(), "source_size": [source.RasterXSize, source.RasterYSize],
        "sampled_grid_size": [len(xs), len(ys)], "vertices": vertices, "triangles": triangles,
        "omitted_quad_count": omitted, "nodata_policy": "OMIT_QUAD_IF_ANY_SOURCE_PIXEL_INVALID",
        "sampling": "SOURCE_PIXEL_CENTRES_NEAREST_GRID", "inspection_only": True,
        "guidance_authorized": False,
    }
    output_path.write_text(json.dumps(result, allow_nan=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return result
