"""Validate the operational stop/maneuver surface manifest contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA = REPO / "schemas" / "operational-surface-layer.schema.json"
SURFACE_TYPES = {
    "HEADLAND",
    "CARRIER",
    "APPROVED_PORTAL",
    "TURN_AREA",
    "WORK_BOUNDARY",
    "POWER_BARRIER_EDGE",
    "TERRACE_EDGE",
    "UNCLASSIFIED_OBSTACLE_EDGE",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def validate_schema(schema: dict) -> None:
    require(schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema", "Wrong draft.")
    require(schema.get("additionalProperties") is False, "Root schema must be closed.")
    layer = schema["$defs"]["surfaceLayer"]
    require(layer.get("additionalProperties") is False, "Surface layer schema must be closed.")
    require(set(layer["properties"]["surface_type"]["enum"]) == SURFACE_TYPES, "Surface types changed.")


def validate_manifest(manifest: dict) -> dict:
    require(set(manifest) == {"schema_version", "surface_layers"}, "Unexpected root fields.")
    require(manifest["schema_version"] == "1.0.0", "Wrong manifest version.")
    require(isinstance(manifest["surface_layers"], list), "surface_layers must be an array.")
    ids = set()
    authorized_count = 0
    for index, layer in enumerate(manifest["surface_layers"]):
        required = {
            "surface_id",
            "surface_type",
            "dataset_ref",
            "geometry_type",
            "review_status",
            "termination_policy",
            "maneuver_policy",
            "operations",
        }
        require(required.issubset(layer), f"surface_layers[{index}] is incomplete.")
        require(layer["surface_id"] not in ids, f"Duplicate surface_id: {layer['surface_id']}")
        ids.add(layer["surface_id"])
        require(layer["surface_type"] in SURFACE_TYPES, f"Unknown surface type: {layer['surface_type']}")
        require(layer["geometry_type"] in {"LineString", "MultiLineString", "Polygon", "MultiPolygon"}, "Bad geometry type.")
        require(layer["operations"], f"{layer['surface_id']} needs operations.")
        if layer["maneuver_policy"] == "AUTHORIZED":
            require(layer["review_status"] == "APPROVED", "Authorized maneuver needs approved review.")
            require(bool(layer.get("fleet_profile_ref")), "Authorized maneuver needs fleet_profile_ref.")
            require(bool(layer.get("evidence_ref")), "Authorized maneuver needs evidence_ref.")
            authorized_count += 1
        if layer["surface_type"] in {"POWER_BARRIER_EDGE", "UNCLASSIFIED_OBSTACLE_EDGE"}:
            require(layer["termination_policy"] == "REQUIRED_BREAK", "Barrier must require a work break.")
            require(layer["maneuver_policy"] == "PROHIBITED", "Barrier cannot authorize a maneuver.")
    return {
        "status": "VALID",
        "surface_count": len(ids),
        "authorized_maneuver_surface_count": authorized_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    validate_schema(json.loads(args.schema.read_text(encoding="utf-8")))
    result = {"status": "VALID", "schema": str(args.schema)}
    if args.manifest:
        result["manifest"] = validate_manifest(json.loads(args.manifest.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
