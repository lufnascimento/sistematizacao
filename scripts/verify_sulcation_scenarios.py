"""Verify the E0 sulcation scenario package against its declared contract.

Run with the QGIS Python environment:

    & 'C:\\Program Files\\QGIS 3.32.1\\bin\\python-qgis.bat' `
      '.\\scripts\\verify_sulcation_scenarios.py'
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path

from osgeo import ogr
from shapely import wkb

try:
    import generate_sulcation_scenarios as generator
except ModuleNotFoundError:  # pragma: no cover - package import in unit-test environments
    from scripts import generate_sulcation_scenarios as generator


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def resolved_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else generator.REPO / path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_output_integrity(declared: dict, artifacts: dict[str, Path]) -> None:
    integrity = declared.get("output_integrity")
    if integrity is None:
        return
    require(isinstance(integrity, dict), "output_integrity must be an object.")
    for artifact_name, artifact_path in artifacts.items():
        record = integrity.get(artifact_name)
        require(isinstance(record, dict), f"Missing output_integrity.{artifact_name} record.")
        require(
            resolved_path(record.get("path", "")).resolve() == artifact_path.resolve(),
            f"output_integrity.{artifact_name}.path disagrees with outputs.{artifact_name}.",
        )
        require(
            record.get("size_bytes") == artifact_path.stat().st_size,
            f"Size mismatch for {artifact_name}.",
        )
        expected_hash = record.get("sha256")
        require(
            isinstance(expected_hash, str) and len(expected_hash) == 64,
            f"Invalid SHA-256 declaration for {artifact_name}.",
        )
        require(
            sha256_file(artifact_path) == expected_hash.lower(),
            f"SHA-256 mismatch for {artifact_name}.",
        )


def axial_angle_difference_deg(first: float, second: float) -> float:
    """Return the smallest difference between unoriented axes in degrees."""

    return abs((first - second + 90.0) % 180.0 - 90.0)


def main() -> None:
    require(generator.OUTPUT_JSON.exists(), f"Missing output: {generator.OUTPUT_JSON}")
    declared = json.loads(generator.OUTPUT_JSON.read_text(encoding="utf-8"))
    parameters = generator.Parameters(**declared["parameters"])
    generator.PARAMS = parameters

    output_gpkg = resolved_path(declared["outputs"]["geopackage"])
    output_map = resolved_path(declared["outputs"]["map"])
    require(output_gpkg.exists(), f"Missing output: {output_gpkg}")
    require(output_map.exists() and output_map.stat().st_size > 100_000, f"Invalid map: {output_map}")
    validate_output_integrity(
        declared,
        {"geopackage": output_gpkg, "map": output_map},
    )

    request_manifest = declared["generation_request"]
    path_resolution = request_manifest["path_resolution"]
    boundary_record = path_resolution["boundary"]
    field_selection = boundary_record.get("field_selection") or {}
    target_field_ids = field_selection.get("loaded_field_ids")
    if target_field_ids is None and request_manifest.get("mode") != "LEGACY_E0_ASSUMPTIONS":
        target_field_ids = request_manifest.get("scope", {}).get("field_ids")
    field_id_column = (
        field_selection.get("selected_field_id_column")
        or boundary_record.get("id_field")
    )
    fields, boundary_projection = generator.read_fields(
        boundary_path=resolved_path(boundary_record["resolved_path"]),
        boundary_layer=boundary_record.get("layer_name"),
        target_field_ids=target_field_ids,
        field_id_column=field_id_column,
    )
    usable_by_code = {field["code"]: field["usable"] for field in fields}
    usable_buffer_by_code = {
        field_code: usable.buffer(0.05)
        for field_code, usable in usable_by_code.items()
    }
    scenario_ids = {scenario["id"] for scenario in declared["scenario_definitions"]}
    require("E0F_EIXO_COMUM" in scenario_ids, "The shared-axis E0F scenario is missing.")
    selected_e0f = declared.get("selected_parameters", {}).get("E0F_EIXO_COMUM", {})
    require(selected_e0f, "E0F selected parameters are missing.")
    require(
        all(
            math.isclose(float(item["lambda"]), 0.0, abs_tol=1e-12)
            for item in selected_e0f.values()
        ),
        "E0F must be a straight shared-axis lattice (lambda=0).",
    )
    selected_e0f_thetas = [float(item["theta_deg"]) for item in selected_e0f.values()]
    require(
        all(math.isfinite(theta) for theta in selected_e0f_thetas),
        "E0F selected theta must be finite.",
    )
    require(
        all(
            axial_angle_difference_deg(theta, selected_e0f_thetas[0]) <= 0.0011
            for theta in selected_e0f_thetas[1:]
        ),
        "E0F selected parameters do not declare one global axial theta.",
    )

    source = ogr.Open(str(output_gpkg))
    require(source is not None, "GeoPackage could not be opened.")
    layer_names = {source.GetLayer(index).GetName() for index in range(source.GetLayerCount())}
    expected_layers = {
        "sulcation_lines",
        "scenario_summary",
        "candidate_parameters",
        "operational_nodes",
    }
    require(layer_names == expected_layers, f"Unexpected layers: {sorted(layer_names)}")

    line_layer = source.GetLayerByName("sulcation_lines")
    spatial_ref = line_layer.GetSpatialRef()
    expected_srs = generator.spatial_reference_from_wkt(boundary_projection, "FIELD_BOUNDARY")
    require(
        spatial_ref is not None and bool(spatial_ref.IsSame(expected_srs)),
        "GeoPackage CRS does not match the declared FIELD_BOUNDARY CRS.",
    )

    pair_counts: Counter[tuple[str, str]] = Counter()
    line_ids: set[str] = set()
    line_contracts: dict[str, dict] = {}
    operational_run_ids: set[str] = set()
    invalid_count = 0
    non_3d_count = 0
    outside_length_m = 0.0
    metric_length_error_m = 0.0
    continuity_failure_count = 0
    internal_endpoint_count = 0
    shared_phase_line_count = 0
    maximum_shared_phase_error_m = 0.0
    shared_axis_theta_deg: float | None = None

    for feature in line_layer:
        scenario_id = feature.GetFieldAsString("scenario_id")
        field_code = feature.GetFieldAsString("field_code")
        line_id = feature.GetFieldAsString("line_id")
        guidance_id = feature.GetFieldAsString("guidance_id")
        operational_run_id = feature.GetFieldAsString("operational_run_id")
        geometry_ogr = feature.GetGeometryRef()
        require(geometry_ogr is not None, f"Missing line geometry: {line_id}")
        require(geometry_ogr.GetGeometryName().upper() == "LINESTRING", f"Not a LineString: {line_id}")
        geometry = wkb.loads(bytes(geometry_ogr.ExportToWkb()))

        require(scenario_id in scenario_ids, f"Unknown scenario: {scenario_id}")
        require(field_code in usable_by_code, f"Unknown field: {field_code}")
        require(line_id not in line_ids, f"Duplicate line_id: {line_id}")
        require(
            guidance_id.startswith(f"{scenario_id}:{field_code}:G"),
            f"guidance_id does not identify its field: {guidance_id}",
        )
        phase_scope = feature.GetFieldAsString("phase_scope")
        row_global_id = feature.GetField("row_global_id")
        phase_level_m = feature.GetFieldAsDouble("phase_level_m")
        require(math.isfinite(phase_level_m), f"Invalid phase level: {line_id}")
        if scenario_id == "E0F_EIXO_COMUM":
            require(
                phase_scope == "GLOBAL_SHARED_STRAIGHT_LATTICE",
                f"E0F lacks a shared phase declaration: {line_id}",
            )
            require(
                isinstance(row_global_id, int) and not isinstance(row_global_id, bool),
                f"E0F lacks an integer row_global_id: {line_id}",
            )
            require(
                abs(phase_level_m - int(row_global_id) * parameters.row_spacing_m) <= 1e-6,
                f"E0F phase and row_global_id disagree: {line_id}",
            )
            theta_deg = feature.GetFieldAsDouble("theta_deg")
            lambda_value = feature.GetFieldAsDouble("lambda")
            require(math.isfinite(theta_deg), f"E0F theta is not finite: {line_id}")
            require(
                math.isclose(lambda_value, 0.0, abs_tol=1e-12),
                f"E0F feature is not straight (lambda != 0): {line_id}",
            )
            if shared_axis_theta_deg is None:
                shared_axis_theta_deg = theta_deg
            else:
                require(
                    axial_angle_difference_deg(theta_deg, shared_axis_theta_deg) <= 0.0011,
                    f"E0F feature does not use the single global axial theta: {line_id}",
                )
            require(
                axial_angle_difference_deg(theta_deg, selected_e0f_thetas[0]) <= 0.0011,
                f"E0F feature theta disagrees with selected parameters: {line_id}",
            )
            theta = math.radians(theta_deg)
            normal_x, normal_y = -math.sin(theta), math.cos(theta)
            phase_errors = [
                abs(normal_x * coordinate[0] + normal_y * coordinate[1] - phase_level_m)
                for coordinate in geometry.coords
            ]
            maximum_shared_phase_error_m = max(
                maximum_shared_phase_error_m,
                max(phase_errors, default=0.0),
            )
            shared_phase_line_count += 1
        else:
            require(
                phase_scope == "FIELD_LOCAL_ADAPTIVE_LEVELS",
                f"Unexpected phase scope: {line_id}",
            )
            require(row_global_id is None, f"Local phase published as global: {line_id}")
        require(feature.GetFieldAsString("release") == "E0_SCREENING", f"Unexpected release on {line_id}")
        require(bool(operational_run_id), f"Missing operational_run_id: {line_id}")
        require(
            operational_run_id not in operational_run_ids,
            f"Duplicate operational_run_id: {operational_run_id}",
        )
        require(
            operational_run_id == line_id,
            "operational_run_id must equal line_id before any barrier split at E0: "
            f"{line_id} != {operational_run_id}",
        )
        require(
            feature.GetFieldAsString("run_state") == "ISOLATED_SEGMENT_E0",
            f"Unexpected E0 run state: {line_id}",
        )
        require(
            feature.GetFieldAsString("topology_status") == "PASS",
            f"Continuity topology failed: {line_id}",
        )
        require(
            not feature.GetFieldAsString("blocker_codes"),
            f"Continuity blockers remain: {line_id}",
        )
        continuity_failure_count += int(
            not feature.GetFieldAsString("continuity_status").startswith("E0_GEOMETRY_ONLY")
        )
        internal_endpoint_count += int(feature.GetFieldAsString("start_surface") == "INTERNAL_UNSUPPORTED")
        internal_endpoint_count += int(feature.GetFieldAsString("end_surface") == "INTERNAL_UNSUPPORTED")

        line_ids.add(line_id)
        operational_run_ids.add(operational_run_id)
        pair_counts[(scenario_id, field_code)] += 1
        invalid_count += int(not geometry.is_valid)
        non_3d_count += int(geometry_ogr.GetCoordinateDimension() != 3)
        require(geometry.length + 1e-6 >= parameters.minimum_segment_m, f"Short line: {line_id}")

        points = [geometry_ogr.GetPoint(index) for index in range(geometry_ogr.GetPointCount())]
        require(len(points) >= 2, f"Line has fewer than two vertices: {line_id}")
        require(
            all(
                len(point) >= 3 and all(math.isfinite(value) for value in point[:3])
                for point in points
            ),
            f"Line contains a non-finite XYZ vertex: {line_id}",
        )

        declared_length = feature.GetFieldAsDouble("length_m")
        metric_length_error_m = max(metric_length_error_m, abs(geometry.length - declared_length))
        outside_length_m += geometry.difference(usable_buffer_by_code[field_code]).length

        first, last = points[0], points[-1]
        line_contracts[line_id] = {
            "scenario_id": scenario_id,
            "field_code": field_code,
            "warning_codes": {
                code for code in feature.GetFieldAsString("warning_codes").split(",") if code
            },
            "START": {
                "point": first,
                "surface_type": feature.GetFieldAsString("start_surface"),
                "termination": feature.GetFieldAsString("start_term"),
                "maneuver": feature.GetFieldAsString("start_maneuver"),
            },
            "END": {
                "point": last,
                "surface_type": feature.GetFieldAsString("end_surface"),
                "termination": feature.GetFieldAsString("end_term"),
                "maneuver": feature.GetFieldAsString("end_maneuver"),
            },
        }

    summary_layer = source.GetLayerByName("scenario_summary")
    candidate_layer = source.GetLayerByName("candidate_parameters")
    node_layer = source.GetLayerByName("operational_nodes")
    expected_pairs = {(scenario_id, code) for scenario_id in scenario_ids for code in usable_by_code}
    require(set(pair_counts) == expected_pairs, "At least one scenario/field pair has no lines.")
    require(summary_layer.GetFeatureCount() == len(expected_pairs), "Unexpected summary row count.")
    expected_candidates = (
        len(fields)
        * (180 // parameters.candidate_angle_step_deg)
        * len(parameters.field_lambda_values)
    )
    require(candidate_layer.GetFeatureCount() == expected_candidates, "Unexpected candidate row count.")
    require(internal_endpoint_count == 0, f"Unsupported internal endpoints: {internal_endpoint_count}")
    require(continuity_failure_count == 0, f"Continuity status failures: {continuity_failure_count}")
    node_ids: set[str] = set()
    node_endpoint_counts: Counter[tuple[str, str]] = Counter()
    for node in node_layer:
        node_id = node.GetFieldAsString("node_id")
        line_id = node.GetFieldAsString("line_id")
        endpoint = node.GetFieldAsString("endpoint")
        require(node_id and node_id not in node_ids, f"Duplicate or empty node_id: {node_id!r}")
        require(line_id in line_contracts, f"Operational node references an unknown line: {node_id}")
        require(endpoint in {"START", "END"}, f"Invalid endpoint on operational node: {node_id}")
        require(node_id == f"{line_id}:{endpoint}", f"node_id disagrees with line/endpoint: {node_id}")
        node_ids.add(node_id)
        node_endpoint_counts[(line_id, endpoint)] += 1

        contract = line_contracts[line_id]
        endpoint_contract = contract[endpoint]
        require(
            node.GetFieldAsString("scenario_id") == contract["scenario_id"],
            f"Node scenario disagrees with its line: {node_id}",
        )
        require(
            node.GetFieldAsString("field_code") == contract["field_code"],
            f"Node field disagrees with its line: {node_id}",
        )
        require(
            node.GetFieldAsString("surface_type") == endpoint_contract["surface_type"],
            f"Node surface disagrees with its line endpoint: {node_id}",
        )
        require(
            node.GetFieldAsString("termination") == endpoint_contract["termination"],
            f"Node termination disagrees with its line endpoint: {node_id}",
        )
        require(
            node.GetFieldAsString("maneuver") == endpoint_contract["maneuver"],
            f"Node maneuver status disagrees with its line endpoint: {node_id}",
        )
        require(
            node.GetFieldAsString("surface_type") != "INTERNAL_UNSUPPORTED",
            f"Internal node persisted: {node_id}",
        )
        require(
            not node.GetFieldAsString("blockers"),
            f"Operational node has unresolved geometric blockers: {node_id}",
        )
        node_warnings = {
            code for code in node.GetFieldAsString("warnings").split(",") if code
        }
        require(
            node_warnings.issubset(contract["warning_codes"]),
            f"Node warnings are not represented on its line: {node_id}",
        )
        require(node.GetFieldAsString("qa_status") != "FAIL", f"Operational node failed QA: {node_id}")
        surface_distance = node.GetFieldAsDouble("surface_dist")
        require(
            math.isfinite(surface_distance) and surface_distance >= 0.0,
            f"Invalid terminal surface distance: {node_id}",
        )

        node_geometry = node.GetGeometryRef()
        require(node_geometry is not None, f"Operational node has no geometry: {node_id}")
        require(node_geometry.GetGeometryName().upper() == "POINT", f"Operational node is not PointZ: {node_id}")
        require(node_geometry.GetCoordinateDimension() == 3, f"Operational node is not PointZ: {node_id}")
        point = node_geometry.GetPoint(0)
        require(
            len(point) >= 3 and all(math.isfinite(value) for value in point[:3]),
            f"Operational node has non-finite XYZ: {node_id}",
        )
        line_point = endpoint_contract["point"]
        require(
            math.hypot(point[0] - line_point[0], point[1] - line_point[1]) <= 1e-6,
            f"Operational node XY does not coincide with its line endpoint: {node_id}",
        )
        require(
            abs(point[2] - line_point[2]) <= 1e-6,
            f"Operational node Z disagrees with its line endpoint: {node_id}",
        )

    require(
        node_layer.GetFeatureCount() == 2 * line_layer.GetFeatureCount(),
        "Each isolated E0 segment must have exactly two operational nodes.",
    )
    for line_id in line_ids:
        require(
            node_endpoint_counts[(line_id, "START")] == 1
            and node_endpoint_counts[(line_id, "END")] == 1,
            f"Line does not have exactly one START and one END node: {line_id}",
        )

    declared_counts = {
        (item["scenario_id"], str(item["field_code"])): int(item["segment_count"])
        for item in declared["scenario_summary"]
    }
    require(declared_counts == dict(pair_counts), "JSON and GeoPackage segment counts differ.")
    require(invalid_count == 0, f"Invalid line geometries: {invalid_count}")
    require(non_3d_count == 0, f"Non-3D line geometries: {non_3d_count}")
    require(outside_length_m <= 0.10, f"Lines outside usable polygons: {outside_length_m:.3f} m")
    require(metric_length_error_m <= 0.01, f"Maximum length attribute error: {metric_length_error_m:.3f} m")
    require(shared_phase_line_count > 0, "No shared-phase E0F lines were verified.")
    require(
        maximum_shared_phase_error_m <= 0.01,
        f"Shared-phase geometry error: {maximum_shared_phase_error_m:.6f} m",
    )
    authority_name = expected_srs.GetAuthorityName(None)
    authority_code = expected_srs.GetAuthorityCode(None)
    declared_crs = request_manifest.get("spatial_reference_qa", {}).get("authority")
    if not declared_crs:
        declared_crs = (
            f"{authority_name}:{authority_code}"
            if authority_name and authority_code
            else "FIELD_BOUNDARY_WKT"
        )

    result = {
        "status": "verified",
        "release_level": declared["status"],
        "layers": sorted(layer_names),
        "line_count": line_layer.GetFeatureCount(),
        "summary_count": summary_layer.GetFeatureCount(),
        "candidate_count": candidate_layer.GetFeatureCount(),
        "scenario_count": len(scenario_ids),
        "field_count": len(fields),
        "operational_run_id_count": len(operational_run_ids),
        "operational_node_count": node_layer.GetFeatureCount(),
        "internal_endpoint_count": internal_endpoint_count,
        "continuity_failure_count": continuity_failure_count,
        "invalid_geometry_count": invalid_count,
        "non_3d_geometry_count": non_3d_count,
        "outside_usable_length_m": round(outside_length_m, 6),
        "maximum_length_attribute_error_m": round(metric_length_error_m, 6),
        "shared_phase_line_count": shared_phase_line_count,
        "shared_axis_theta_deg": round(shared_axis_theta_deg, 6),
        "maximum_shared_phase_error_m": round(maximum_shared_phase_error_m, 9),
        "declared_crs": declared_crs,
        "declared_row_spacing_m": parameters.row_spacing_m,
        "boundary_path": str(resolved_path(boundary_record["resolved_path"])),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
