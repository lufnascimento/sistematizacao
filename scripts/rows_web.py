"""Bounded per-alternative web derivatives of the original screening rows."""

import argparse
import hashlib
import json
from pathlib import Path

from osgeo import ogr

try:
    from scripts.contours_web import _read_features
except ModuleNotFoundError:
    from contours_web import _read_features


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_rows(source, terrain, output_dir, max_part_vertices=100000):
    if not 2 <= max_part_vertices <= 200000:
        raise ValueError("Invalid web part vertex limit")
    dataset = ogr.Open(str(source))
    if dataset is None:
        raise ValueError("Cannot open row dataset")
    failure = None
    try:
        source_layers = []
        for name, status in (("sulcation_lines", "SCREENING"), ("continuous_rows", "SCREENING"), ("diagnostic_rows", "DIAGNOSTIC")):
            layer = dataset.GetLayerByName(name)
            if layer is not None:
                source_layers.append((name, status, _read_features(layer, 2000000, contours=False)))
        if not source_layers:
            raise ValueError("No supported row layer")
    except ValueError as exc:
        failure = str(exc)
    finally:
        layer = None
        dataset = None
    if failure:
        raise ValueError(failure)
    groups = {}
    names = {"CF0A_CONSERVACAO": "Curvas - conservacao", "CF0B_EQUILIBRIO": "Curvas - equilibrio", "CF0C_OPERACAO": "Curvas - operacao"}
    for layer_name, status, features in source_layers:
        for feature in features:
            properties = feature["properties"]
            key = properties.get("scenario_id") or properties.get("candidate_id")
            if not isinstance(key, str) or not key:
                raise ValueError("Row lacks alternative identity")
            if len(feature["geometry"]["coordinates"]) > max_part_vertices:
                raise ValueError("A row exceeds the web part limit; original geometry was not truncated")
            properties["source_layer"] = layer_name
            properties["inspection_status"] = status
            groups.setdefault((key, status), []).append(feature)
    if len(groups) > 64:
        raise ValueError("Too many alternative groups for inspection")
    terrain_hash = checksum(terrain)
    outputs = []
    output_dir.mkdir(parents=True, exist_ok=True)
    for group_index, ((key, status), features) in enumerate(sorted(groups.items())):
        parts, part, count = [], [], 0
        for feature in features:
            size = len(feature["geometry"]["coordinates"])
            if part and count + size > max_part_vertices:
                parts.append(part)
                part, count = [], 0
            part.append(feature)
            count += size
        if part:
            parts.append(part)
        name = features[0]["properties"].get("scenario_name") or names.get(key, f"Alternativa {group_index + 1}")
        for index, part in enumerate(parts):
            path = output_dir / f"rows_{group_index + 1:02d}_{index + 1:03d}.geojson"
            payload = {"type": "FeatureCollection", "features": part, "terrain_sha256": terrain_hash,
                       "vertical_reference": "UNSPECIFIED_SOURCE_DATUM", "inspection_only": True,
                       "guidance_authorized": False}
            path.write_text(json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":")), encoding="utf-8")
            outputs.append({"path": str(path.resolve()), "sha256": checksum(path), "size_bytes": path.stat().st_size,
                            "scenario_key": key, "scenario_name": name, "inspection_status": status,
                            "part": index + 1, "part_count": len(parts), "feature_count": len(part)})
    return {"status": "AVAILABLE", "source_sha256": checksum(source), "terrain_sha256": terrain_hash,
            "outputs": outputs, "guidance_authorized": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--terrain", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = export_rows(args.source, args.terrain, args.output_dir)
    except ValueError as exc:
        result = {"status": "UNAVAILABLE", "reason": str(exc), "outputs": [], "guidance_authorized": False}
    (args.output_dir / "rows_web_manifest.json").write_text(json.dumps(result, allow_nan=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
