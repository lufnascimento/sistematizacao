"""Field outlines with optional sampled terrain heights for inspection."""

import hashlib
import json
import math
from functools import lru_cache

from osgeo import gdal, ogr, osr


def export_boundaries(boundary, terrain_path, output_path, max_vertices=200000):
    terrain = gdal.Open(str(terrain_path))
    if terrain is None:
        raise ValueError("Terrain could not be opened")
    failure = None
    try:
        result = _export(boundary, terrain, terrain_path, output_path, max_vertices)
    except ValueError as exc:
        failure = str(exc)
    finally:
        terrain = None
    if failure is not None:
        raise ValueError(failure)
    return result


def _export(boundary, terrain, terrain_path, output_path, max_vertices):
    reference = boundary["srs"].Clone()
    reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    terrain_reference = osr.SpatialReference(wkt=terrain.GetProjection())
    terrain_reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    if not reference.IsProjected() or not math.isclose(reference.GetLinearUnits(), 1.0) or not reference.IsSame(terrain_reference):
        raise ValueError("Boundary and terrain must share a projected metric CRS")
    geographic = osr.SpatialReference()
    geographic.ImportFromEPSG(4326)
    geographic.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(reference, geographic)
    gt = terrain.GetGeoTransform()
    inverse = gdal.InvGeoTransform(gt)
    if inverse is None:
        raise ValueError("Terrain transform is singular")
    step = min(math.hypot(gt[1], gt[4]), math.hypot(gt[2], gt[5])) / 2
    if step <= 0:
        raise ValueError("Invalid terrain pixel size")
    band = terrain.GetRasterBand(1)
    mask = band.GetMaskBand()

    @lru_cache(maxsize=32)
    def raster_row(y):
        return band.ReadAsArray(0, y, terrain.RasterXSize, 1)[0], mask.ReadAsArray(0, y, terrain.RasterXSize, 1)[0]

    features, count = [], 0
    for field_id, geometry in zip(boundary["field_ids"], boundary["geometries"]):
        polygons = [geometry] if ogr.GT_Flatten(geometry.GetGeometryType()) == ogr.wkbPolygon else [geometry.GetGeometryRef(i) for i in range(geometry.GetGeometryCount())]
        for polygon_index, polygon in enumerate(polygons):
            for ring_index in range(polygon.GetGeometryCount()):
                ring = polygon.GetGeometryRef(ring_index).Clone()
                if count + ring.GetPointCount() + math.ceil(ring.Length() / step) > max_vertices:
                    raise ValueError("Field outlines exceed inspection vertex limit")
                ring.Segmentize(step)
                coordinates, elevations = [], []
                for point in ring.GetPoints():
                    x, y = point[:2]
                    lon, lat, _ = transform.TransformPoint(x, y)
                    if not all(math.isfinite(v) for v in (lon, lat)) or abs(lon) > 180 or abs(lat) > 85:
                        raise ValueError("Boundary outside viewer coverage")
                    coordinates.append([lon, lat])
                    px, py = gdal.ApplyGeoTransform(inverse, x, y)
                    col, row = math.floor(px), math.floor(py)
                    z = None
                    if 0 <= col < terrain.RasterXSize and 0 <= row < terrain.RasterYSize:
                        values, valid = raster_row(row)
                        if valid[col] and math.isfinite(float(values[col])):
                            z = float(values[col])
                    elevations.append(z)
                count += len(coordinates)
                features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": coordinates},
                                 "properties": {"kind": "FIELD_BOUNDARY", "field_id": field_id,
                                                "id": f"{field_id}:{polygon_index}:{ring_index}",
                                                "interior_ring": ring_index > 0,
                                                "source_elevations_m": elevations,
                                                "height_coverage_complete": all(z is not None for z in elevations)}})
    digest = hashlib.sha256()
    with terrain_path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    result = {"type": "FeatureCollection", "features": features, "terrain_sha256": digest.hexdigest(),
              "vertical_reference": "UNSPECIFIED_SOURCE_DATUM", "sampling": "NEAREST_SOURCE_PIXEL",
              "inspection_only": True, "guidance_authorized": False}
    output_path.write_text(json.dumps(result, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    return result
