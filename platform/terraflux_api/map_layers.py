"""Map layer discovery from explicit publication metadata."""

from pathlib import Path


LAYER_NAMES = {
    "contours.gpkg": "Curvas de nivel", "hillshade.tif": "Relevo sombreado",
    "slope_degrees.tif": "Declividade em graus", "slope_percent.tif": "Declividade percentual",
    "dtm.tif": "MDT original",
}


def build_map_layers(run, artifacts):
    layers = []
    for artifact in artifacts:
        if artifact.get("run_id") != run["id"] or artifact.get("project_id") != run["project_id"]:
            continue
        extension = Path(artifact["filename"]).suffix.lower()
        spatial = artifact.get("spatial_metadata") or {}
        terrain = extension == ".json" and spatial.get("format") == "TERRAIN_INSPECTION_MESH" and spatial.get("geometry_type") == "Mesh"
        if extension not in {".geojson", ".gpkg", ".tif", ".tiff", ".las", ".laz"} and not terrain:
            continue
        ready = (
            run.get("status") == "SUCCEEDED"
            and spatial.get("horizontal_crs") == "OGC:CRS84"
            and (terrain or (extension == ".geojson" and spatial.get("format") == "RFC7946" and spatial.get("geometry_type") == "LineString"))
        )
        reason = None if ready else (
            "RUN_NOT_SUCCEEDED" if run.get("status") != "SUCCEEDED" else
            "SPATIAL_METADATA_MISSING" if not spatial else "WEB_CONVERSION_REQUIRED"
        )
        layers.append({
            "id": artifact["id"], "artifact_id": artifact["id"],
            "name": spatial.get("label", LAYER_NAMES.get(artifact["filename"], artifact["filename"])),
            "product_id": artifact["product_id"], "sha256": artifact["sha256"],
            "size_bytes": artifact["size_bytes"], "format": extension[1:].upper(),
            "status": "READY" if ready else "UNAVAILABLE", "reason": reason,
            "source_url": f"/api/artifacts/{artifact['id']}/download" if ready else None,
            "spatial_metadata": spatial,
            "default_visible": ready, "default_opacity": 1.0,
        })
    return {
        "schema_version": "1.0.0", "run_id": run["id"], "project_id": run["project_id"],
        "layers": layers, "ready_count": sum(layer["status"] == "READY" for layer in layers),
        "terrain_mesh_available": any(layer["status"] == "READY" and layer["spatial_metadata"].get("format") == "TERRAIN_INSPECTION_MESH" for layer in layers), "guidance_authorized": False,
    }
