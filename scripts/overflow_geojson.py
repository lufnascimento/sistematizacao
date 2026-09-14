"""Export metric screening paths as RFC 7946 horizontal geometry."""

import math

from pyproj import CRS, Transformer


def export_overflow_geojson(paths, source_crs):
    if not source_crs:
        raise ValueError("OVERFLOW_SOURCE_CRS_REQUIRED")
    crs = CRS.from_user_input(source_crs)
    if not crs.is_projected or len(crs.axis_info) != 2 or any(
        axis.unit_conversion_factor != 1.0 for axis in crs.axis_info
    ):
        raise ValueError("OVERFLOW_SOURCE_CRS_MUST_BE_PROJECTED_METRES")
    transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True, allow_ballpark=False)
    features = []
    for path in paths:
        coordinates = []
        for x, y, z in path["coordinates"]:
            if not all(math.isfinite(value) for value in (x, y, z)):
                raise ValueError("OVERFLOW_COORDINATES_MUST_BE_FINITE")
            lon, lat = transformer.transform(x, y, errcheck=True)
            if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError("OVERFLOW_GEOGRAPHIC_COORDINATES_INVALID")
            coordinates.append([lon, lat])
        features.append({
            "type": "Feature",
            "properties": {
                **{key: path[key] for key in ("id", "reach_id", "receiver_id", "length_m", "elevation_drop_m", "screening_status")},
                "source_crs": crs.to_string(),
                "source_elevations_m": [point[2] for point in path["coordinates"]],
                "vertical_reference": "UNSPECIFIED_SOURCE_DATUM",
                "vertical_transformation_applied": False,
            },
            "geometry": {"type": "LineString", "coordinates": coordinates},
        })
    return {"type": "FeatureCollection", "features": features}
