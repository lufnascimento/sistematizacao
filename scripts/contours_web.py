"""Publish inspection contours without mistaking source heights for WGS84 Z."""

import hashlib
import json
import math
from pathlib import Path

from osgeo import ogr, osr


def _read_features(layer, max_vertices, contours=True):
    reference = layer.GetSpatialRef()
    if reference is None or not reference.IsProjected() or not math.isclose(reference.GetLinearUnits(), 1.0):
        raise ValueError("Contours require a projected metric CRS")
    reference = reference.Clone()
    reference.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    target = osr.SpatialReference()
    target.ImportFromEPSG(4326)
    target.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    transform = osr.CoordinateTransformation(reference, target)
    features = []
    vertex_count = 0
    for feature in layer:
        geometry = feature.GetGeometryRef()
        if geometry is None or ogr.GT_Flatten(geometry.GetGeometryType()) != ogr.wkbLineString or not geometry.Is3D():
            raise ValueError("Contours must be 3D LineStrings")
        points = geometry.GetPoints()
        vertex_count += len(points)
        if len(points) < 2 or vertex_count > max_vertices:
            raise ValueError("Contour layer exceeds inspection limits or contains an empty line")
        coordinates, heights = [], []
        for x, y, z in points:
            if not all(math.isfinite(value) for value in (x, y, z)):
                raise ValueError("Non-finite source contour coordinate")
            lon, lat, _ = transform.TransformPoint(x, y)
            if not math.isfinite(lon) or not math.isfinite(lat) or abs(lon) > 180 or abs(lat) > 85:
                raise ValueError("Contour outside viewer coverage")
            coordinates.append([lon, lat])
            heights.append(z)
        if contours and any(not math.isclose(z, heights[0], rel_tol=0, abs_tol=1e-6) for z in heights):
            raise ValueError("Contour elevation must be constant")
        properties = {"id": str(feature.GetFID()), "kind": "CONTOUR", "elevation_m": heights[0]} if contours else {
            **feature.items(), "id": str(feature.GetFID()), "kind": "SULCATION_ROW",
            "guidance_authorized": False,
        }
        properties["source_elevations_m"] = heights
        if not contours:
            chainages = [0.0]
            for previous, current in zip(points, points[1:]):
                chainages.append(chainages[-1] + math.hypot(current[0] - previous[0], current[1] - previous[1]))
            properties["source_chainages_m"] = chainages
            properties["chainage_reference"] = "SOURCE_PROJECTED_METRIC_XY"
        features.append({
            "type": "Feature", "geometry": {"type": "LineString", "coordinates": coordinates},
            "properties": properties,
        })
    return features


def export_contours(source: Path, terrain: Path, output: Path, max_vertices=200000):
    dataset = ogr.Open(str(source))
    if dataset is None:
        raise ValueError("Could not open contour dataset")
    try:
        features = _read_features(dataset.GetLayer(0), max_vertices)
    finally:
        dataset = None
    digest = hashlib.sha256()
    with terrain.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    result = {
        "type": "FeatureCollection", "features": features,
        "terrain_sha256": digest.hexdigest(), "vertical_reference": "UNSPECIFIED_SOURCE_DATUM",
        "vertical_transformation_applied": False, "inspection_only": True,
        "guidance_authorized": False,
    }
    output.write_text(json.dumps(result, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    return result
