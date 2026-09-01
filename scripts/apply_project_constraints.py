"""Apply the central project-request power constraint to generated work lines.

This adapter is intentionally narrow: the validated project request is the
only authority for the power-axis dataset and exclusion half width. The
geometry implementation remains in ``apply_constraint_barriers``.

Exit codes:
    0: applied successfully, not required by declaration, or self-test passed
    2: command-line usage error (argparse)
    3: blocked or invalid/incomplete mandatory input
    4: unexpected processing/output error
    5: synthetic self-test failure
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

import fiona
import geopandas as gpd
from shapely.geometry import LineString

try:
    from apply_constraint_barriers import (
        BarrierInputError,
        BarrierValidationError,
        RunParameters,
        run_pipeline,
    )
    from project_request import load_project_request
    from validate_project_inputs import ContractError
except ModuleNotFoundError:  # Allows ``python -m scripts...`` from the repo root.
    from scripts.apply_constraint_barriers import (
        BarrierInputError,
        BarrierValidationError,
        RunParameters,
        run_pipeline,
    )
    from scripts.project_request import load_project_request
    from scripts.validate_project_inputs import ContractError


REPO = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "project-power-constraint-stage-v1.0.0"
ENGINE_METADATA_SUFFIX = ".barrier-engine.json"

DEFAULT_WORK = REPO / "dataset" / "derived" / "sulcation_scenarios.gpkg"
DEFAULT_WORK_LAYER = "sulcation_lines"
DEFAULT_OUTPUT = REPO / "dataset" / "derived" / "constrained_sulcation.gpkg"
DEFAULT_METADATA_OUTPUT = REPO / "dataset" / "derived" / "constrained_sulcation.json"

POWER_STATUS_ID = "constraints.overhead_power_line_inventory_status"
POWER_AXIS_ID = "constraints.overhead_power_line_axis_dataset_ref"
POWER_WIDTH_ID = "constraints.overhead_power_line_exclusion_half_width_m"
POWER_WORK_ID = "constraints.overhead_power_line_work_behavior"
POWER_TRANSIT_ID = "constraints.overhead_power_line_transit_policy"
POWER_LAYER_TYPE = "OVERHEAD_POWER_LINE_AXIS"

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_BLOCKED = 3
EXIT_PROCESSING = 4
EXIT_SELF_TEST = 5


class ProjectConstraintError(RuntimeError):
    """Raised when the central contract cannot be applied unambiguously."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO).as_posix()
    except ValueError:
        return str(resolved)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.stem}.tmp.json")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _request_identity(project) -> dict[str, Any]:
    return {
        "request_id": project.request["request_id"],
        "project_id": project.request["project_id"],
        "request_path": _display_path(project.request_path),
        "request_sha256": _sha256_file(project.request_path),
        "created_at": project.request["created_at"],
        "requested_delivery_level": project.requested_delivery_level,
        "revisions": project.request["revisions"],
    }


def _parameter_record(project, parameter_id: str) -> dict[str, Any]:
    record = project.parameter(parameter_id)
    if record is None:
        raise ProjectConstraintError(f"Validated request is missing parameter {parameter_id}.")
    return {
        "parameter_id": parameter_id,
        "value": record["value"],
        "unit": record["unit"],
        "parameter_class": record["parameter_class"],
        "provenance": record["provenance"],
        **(
            {"applied_override": record["applied_override"]}
            if "applied_override" in record
            else {}
        ),
    }


def _base_manifest(project, status: str) -> dict[str, Any]:
    inventory = project.request["constraint_inventory"]["overhead_power_line"]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "stage": "OVERHEAD_POWER_LINE_BARRIER",
        "request": _request_identity(project),
        "inventory": {
            "status": project.power_inventory_status,
            "declared_at": inventory["declared_at"],
            "responsible": inventory["responsible"],
            "declaration_ref": inventory.get("declaration_ref"),
            "layer_ids": inventory.get("layer_ids", []),
        },
        "policy": {
            "barrier_state": "POWER_LINE_BARRIER",
            "work_behavior": "SPLIT_AND_EXCLUDE",
            "transit_policy": "PROHIBITED",
            "creates_connectors": False,
            "continuity_across_barrier": False,
        },
    }


def _resolve_power_axis(project) -> tuple[Path, str | None, dict[str, Any]]:
    dataset_id = project.value(POWER_AXIS_ID)
    if not isinstance(dataset_id, str) or not dataset_id:
        raise ProjectConstraintError(f"{POWER_AXIS_ID} must identify the supplied axis dataset.")

    inventory_ids = set(
        project.request["constraint_inventory"]["overhead_power_line"].get("layer_ids", [])
    )
    matching_layers = [
        layer
        for layer in project.request["constraint_layers"]
        if layer["type"] == POWER_LAYER_TYPE
        and layer["layer_id"] in inventory_ids
        and layer["dataset_ref"] == dataset_id
    ]
    if not matching_layers:
        raise ProjectConstraintError(
            "No declared OVERHEAD_POWER_LINE_AXIS layer matches the axis dataset parameter."
        )

    dataset_path, dataset_resolution = project.dataset_path(
        dataset_id,
        expected_role="CONSTRAINT_LAYER",
    )
    available_layers = list(fiona.listlayers(dataset_path))
    if not available_layers:
        raise ProjectConstraintError(f"Power axis datasource has no readable layers: {dataset_path}")

    requested_names = {
        value
        for value in [
            project.dataset(dataset_id).get("layer_name"),
            *[
                layer.get(key)
                for layer in matching_layers
                for key in ("source_layer", "layer_name")
            ],
        ]
        if isinstance(value, str) and value
    }
    if requested_names:
        if len(requested_names) != 1:
            raise ProjectConstraintError(
                f"Power constraint layers declare conflicting physical layer names: {sorted(requested_names)}"
            )
        physical_layer = next(iter(requested_names))
        if physical_layer not in available_layers:
            raise ProjectConstraintError(
                f"Declared physical layer {physical_layer!r} is absent from {dataset_path}; "
                f"found {available_layers}."
            )
    elif len(available_layers) == 1:
        physical_layer = available_layers[0]
    else:
        declared_ids = {layer["layer_id"] for layer in matching_layers}
        candidates = [name for name in available_layers if name in declared_ids]
        if len(candidates) != 1:
            raise ProjectConstraintError(
                "The power axis datasource has multiple physical layers and the V1 request "
                f"does not identify one unambiguously; found {available_layers}."
            )
        physical_layer = candidates[0]

    return dataset_path, physical_layer, {
        **dataset_resolution,
        "constraint_layer_ids": sorted(layer["layer_id"] for layer in matching_layers),
        "physical_layer": physical_layer,
        "available_physical_layers": available_layers,
    }


def _engine_metadata_path(output: Path) -> Path:
    return output.with_name(f"{output.stem}{ENGINE_METADATA_SUFFIX}")


def apply_project_constraints(
    *,
    request_path: Path,
    work_path: Path = DEFAULT_WORK,
    work_layer: str | None = DEFAULT_WORK_LAYER,
    output: Path = DEFAULT_OUTPUT,
    metadata_output: Path = DEFAULT_METADATA_OUTPUT,
    min_fragment_m: float = 0.0,
    target_crs: str | None = None,
    work_crs: str | None = None,
    barrier_crs: str | None = None,
    id_field: str | None = None,
    barrier_id_field: str | None = None,
) -> tuple[int, dict[str, Any]]:
    """Apply or gate the power stage according to a validated request."""

    project = load_project_request(request_path)
    status = project.power_inventory_status

    if status == "NOT_REVIEWED":
        manifest = _base_manifest(project, "BLOCKED")
        manifest.update(
            {
                "blockers": ["OVERHEAD_POWER_LINE_NOT_REVIEWED"],
                "reason": (
                    "The V1 safety gate cannot establish absence or geometry of the "
                    "overhead power-line barrier."
                ),
                "geometry_stage_executed": False,
                "outputs": {
                    "gpkg_written": False,
                    "gpkg": None,
                    "metadata_json": str(metadata_output.resolve()),
                },
            }
        )
        _write_json(metadata_output, manifest)
        return EXIT_BLOCKED, manifest

    if status == "DECLARED_NONE":
        manifest = _base_manifest(project, "NOT_REQUIRED")
        manifest.update(
            {
                "blockers": [],
                "reason": "The client declaration states that no overhead power line is present.",
                "geometry_stage_executed": False,
                "outputs": {
                    "gpkg_written": False,
                    "gpkg": None,
                    "metadata_json": str(metadata_output.resolve()),
                    "requested_gpkg_path_not_written": str(output.resolve()),
                },
            }
        )
        _write_json(metadata_output, manifest)
        return EXIT_OK, manifest

    if status != "PROVIDED":
        raise ProjectConstraintError(f"Unsupported power inventory status: {status!r}")

    width_record = _parameter_record(project, POWER_WIDTH_ID)
    half_width_m = width_record["value"]
    if not isinstance(half_width_m, (int, float)) or isinstance(half_width_m, bool):
        raise ProjectConstraintError(f"{POWER_WIDTH_ID} must be numeric.")
    half_width_m = float(half_width_m)
    if not math.isfinite(half_width_m) or half_width_m <= 0.0:
        raise ProjectConstraintError(f"{POWER_WIDTH_ID} must be finite and positive.")

    work_behavior = _parameter_record(project, POWER_WORK_ID)
    transit_policy = _parameter_record(project, POWER_TRANSIT_ID)
    if work_behavior["value"] != "SPLIT_AND_EXCLUDE":
        raise ProjectConstraintError("The V1 adapter only accepts SPLIT_AND_EXCLUDE.")
    if transit_policy["value"] != "PROHIBITED":
        raise ProjectConstraintError("The V1 adapter only accepts PROHIBITED transit.")

    barrier_path, barrier_layer, barrier_resolution = _resolve_power_axis(project)
    engine_metadata = _engine_metadata_path(output)
    engine_result = run_pipeline(
        work_path,
        work_layer,
        barrier_path,
        barrier_layer,
        output,
        engine_metadata,
        RunParameters(
            half_width_m=half_width_m,
            min_fragment_m=min_fragment_m,
            target_crs=target_crs,
            work_crs=work_crs,
            barrier_crs=barrier_crs,
            id_field=id_field,
            barrier_id_field=barrier_id_field,
        ),
    )

    manifest = _base_manifest(project, "APPLIED")
    manifest.update(
        {
            "blockers": [],
            "geometry_stage_executed": True,
            "resolved_inputs": {
                "work": {
                    "path": str(work_path.resolve()),
                    "layer": work_layer,
                    "sha256": _sha256_file(work_path),
                },
                "power_axis": barrier_resolution,
            },
            "consumed_parameters": {
                POWER_AXIS_ID: _parameter_record(project, POWER_AXIS_ID),
                POWER_WIDTH_ID: width_record,
                POWER_WORK_ID: work_behavior,
                POWER_TRANSIT_ID: transit_policy,
            },
            "engine": {
                "module": "apply_constraint_barriers",
                "run_id": engine_result["run_id"],
                "metadata_json": str(engine_metadata.resolve()),
                "metrics": engine_result["metrics"],
                "validation": engine_result["validation"],
            },
            "outputs": {
                "gpkg_written": True,
                "gpkg": str(output.resolve()),
                "metadata_json": str(metadata_output.resolve()),
                "engine_metadata_json": str(engine_metadata.resolve()),
                "layers": ["worked_segments", "power_barriers", "work_breaks"],
            },
        }
    )
    _write_json(metadata_output, manifest)
    return EXIT_OK, manifest


def _synthetic_parameter(
    parameter_id: str,
    parameter_class: str,
    value: Any,
    unit: str | None,
    origin: str,
) -> dict[str, Any]:
    return {
        "parameter_id": parameter_id,
        "parameter_class": parameter_class,
        "value": value,
        "unit": unit,
        "provenance": {
            "origin": origin,
            "source_ref": "apply_project_constraints-self-test",
            "captured_at": "2026-08-11T12:00:00-03:00",
            "responsible": {
                "name": "synthetic-self-test",
                "organization": "terraflux",
                "role": "automated-test",
                "identifier": None,
            },
            "confidence": "VERIFIED",
            "revision": "self-test-v1",
            "applicability": "PROJECT",
        },
    }


def _replace_parameter(request: dict[str, Any], parameter: dict[str, Any]) -> None:
    request["parameter_values"] = [
        item
        for item in request["parameter_values"]
        if item["parameter_id"] != parameter["parameter_id"]
    ]
    request["parameter_values"].append(parameter)


def _write_request(path: Path, request: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(request, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _make_request(
    template: dict[str, Any],
    directory: Path,
    status: str,
    barrier_path: Path | None = None,
) -> Path:
    request = copy.deepcopy(template)
    request["request_id"] = f"constraint-self-test-{status.lower()}"
    request["project_id"] = "constraint-adapter-self-test"
    request["constraint_inventory"]["general_review_status"] = (
        "COMPLETE" if status != "NOT_REVIEWED" else "NOT_REVIEWED"
    )
    inventory = request["constraint_inventory"]["overhead_power_line"]
    inventory["status"] = status
    inventory["layer_ids"] = []
    inventory["declaration_ref"] = None
    request["constraint_layers"] = []
    _replace_parameter(
        request,
        _synthetic_parameter(
            POWER_STATUS_ID,
            "USER_FACT",
            status,
            None,
            "DECLARED",
        ),
    )
    _replace_parameter(
        request,
        _synthetic_parameter(
            "constraints.inventory_review_status",
            "USER_FACT",
            request["constraint_inventory"]["general_review_status"],
            None,
            "DECLARED",
        ),
    )

    if status == "DECLARED_NONE":
        inventory["declaration_ref"] = "synthetic-client-declaration-no-power-lines"
    elif status == "PROVIDED":
        if barrier_path is None:
            raise AssertionError("PROVIDED self-test request requires a barrier datasource.")
        dataset_id = "synthetic-power-axis"
        request["input_datasets"].append(
            {
                "dataset_id": dataset_id,
                "role": "CONSTRAINT_LAYER",
                "format": "GPKG",
                "source_ref": str(barrier_path.resolve()),
                "revision": "self-test-v1",
                "captured_at": "2026-08-11T12:00:00-03:00",
                "responsible": {
                    "name": "synthetic-self-test",
                    "organization": "terraflux",
                    "role": "automated-test",
                    "identifier": None,
                },
                "crs": {
                    "horizontal": "EPSG:31982",
                    "vertical": None,
                    "horizontal_unit": "m",
                    "vertical_unit": None,
                },
                "geometry_type": "LineString",
                "checksum_sha256": _sha256_file(barrier_path),
                "qa_status": "VALIDATED",
            }
        )
        layer_id = "synthetic-power-layer"
        inventory["layer_ids"] = [layer_id]
        request["constraint_layers"] = [
            {
                "layer_id": layer_id,
                "type": POWER_LAYER_TYPE,
                "dataset_ref": dataset_id,
                "geometry_type": "LineString",
                "dimension": "2D",
                "geometry_semantics": "AXIS_THROUGH_POST_CENTERS",
                "review_status": "SURVEYED",
                "operation_behaviors": [
                    {"operation": "FURROW", "behavior": "SPLIT_WORK"},
                    {"operation": "PLANT", "behavior": "SPLIT_WORK"},
                    {"operation": "HARVEST", "behavior": "SPLIT_WORK"},
                    {"operation": "TRANSSHIPMENT", "behavior": "EXCLUDE"},
                    {"operation": "TRUCK", "behavior": "EXCLUDE"},
                    {"operation": "MAINTENANCE", "behavior": "EXCLUDE"},
                ],
                "barrier_policy": {
                    "mode": "HORIZONTAL_EXCLUSION_BUFFER",
                    "exclusion_half_width_parameter_id": POWER_WIDTH_ID,
                    "work_behavior": "SPLIT_AND_EXCLUDE",
                    "transit_behavior": "PROHIBITED",
                    "buffer_join_style": "ROUND",
                },
                "attributes_contract": ["power_id"],
                "notes": "Synthetic central-contract self-test.",
            }
        ]
        for parameter in (
            _synthetic_parameter(POWER_AXIS_ID, "USER_FACT", dataset_id, None, "UPLOADED"),
            _synthetic_parameter(POWER_WIDTH_ID, "RULE_PACK", 5.0, "m", "RULE_PACK"),
            _synthetic_parameter(POWER_WORK_ID, "RULE_PACK", "SPLIT_AND_EXCLUDE", None, "RULE_PACK"),
            _synthetic_parameter(POWER_TRANSIT_ID, "RULE_PACK", "PROHIBITED", None, "RULE_PACK"),
        ):
            _replace_parameter(request, parameter)

    request_path = directory / f"request_{status.lower()}.json"
    _write_request(request_path, request)
    return request_path


def _make_vectors(
    directory: Path,
    case: str,
) -> tuple[Path, Path]:
    work_path = directory / f"work_{case}.gpkg"
    barrier_path = directory / f"barrier_{case}.gpkg"
    gpd.GeoDataFrame(
        {"work_id": ["ROW-1"]},
        geometry=[LineString([(0.0, 0.0, 100.0), (100.0, 0.0, 110.0)])],
        crs="EPSG:31982",
    ).to_file(work_path, layer="sulcation_lines", driver="GPKG", index=False)

    if case == "crossing":
        geometry = LineString([(50.0, -20.0), (50.0, 20.0)])
    elif case == "no_intersection":
        geometry = LineString([(200.0, -20.0), (200.0, 20.0)])
    elif case == "full_removal":
        geometry = LineString([(0.0, 0.0), (100.0, 0.0)])
    else:
        raise AssertionError(f"Unknown synthetic case: {case}")
    gpd.GeoDataFrame(
        {"power_id": [f"POWER-{case}"]},
        geometry=[geometry],
        crs="EPSG:31982",
    ).to_file(barrier_path, layer="power_axis", driver="GPKG", index=False)
    return work_path, barrier_path


def run_self_test() -> dict[str, Any]:
    template = json.loads(
        (REPO / "config" / "exemplo_pedido_e0_dataset_atual.json").read_text(encoding="utf-8")
    )
    with tempfile.TemporaryDirectory(prefix="project-constraints-self-test-") as temporary:
        directory = Path(temporary)
        checks: dict[str, str] = {}

        not_reviewed_request = _make_request(template, directory, "NOT_REVIEWED")
        not_reviewed_code, not_reviewed = apply_project_constraints(
            request_path=not_reviewed_request,
            work_path=directory / "must-not-be-read.gpkg",
            output=directory / "must-not-be-written.gpkg",
            metadata_output=directory / "not_reviewed.json",
        )
        if not_reviewed_code != EXIT_BLOCKED or not_reviewed["status"] != "BLOCKED":
            raise ProjectConstraintError("NOT_REVIEWED did not fail closed with exit 3.")
        checks["not_reviewed_exit_3"] = "PASS"

        declared_request = _make_request(template, directory, "DECLARED_NONE")
        declared_output = directory / "declared_none.gpkg"
        declared_code, declared = apply_project_constraints(
            request_path=declared_request,
            work_path=directory / "must-not-be-read.gpkg",
            output=declared_output,
            metadata_output=directory / "declared_none.json",
        )
        if declared_code != EXIT_OK or declared["status"] != "NOT_REQUIRED":
            raise ProjectConstraintError("DECLARED_NONE did not publish NOT_REQUIRED.")
        if declared_output.exists():
            raise ProjectConstraintError("DECLARED_NONE fabricated a GeoPackage output.")
        checks["declared_none_manifest_only"] = "PASS"

        expected = {
            "crossing": (2, 1),
            "no_intersection": (1, 0),
            "full_removal": (0, 1),
        }
        for case, (worked_count, break_count) in expected.items():
            case_directory = directory / case
            case_directory.mkdir()
            work_path, barrier_path = _make_vectors(case_directory, case)
            request_path = _make_request(template, case_directory, "PROVIDED", barrier_path)
            output = case_directory / "constrained.gpkg"
            code, manifest = apply_project_constraints(
                request_path=request_path,
                work_path=work_path,
                work_layer="sulcation_lines",
                output=output,
                metadata_output=case_directory / "constrained.json",
                target_crs="EPSG:31982",
                id_field="work_id",
                barrier_id_field="power_id",
            )
            if code != EXIT_OK or manifest["status"] != "APPLIED":
                raise ProjectConstraintError(f"Synthetic {case} stage was not applied.")
            layers = list(fiona.listlayers(output))
            required_layers = ["worked_segments", "power_barriers", "work_breaks"]
            if layers[:3] != required_layers:
                raise ProjectConstraintError(f"Synthetic {case} omitted required layers: {layers}")
            metrics = manifest["engine"]["metrics"]
            if metrics["worked_segment_count"] != worked_count:
                raise ProjectConstraintError(f"Synthetic {case} worked count mismatch.")
            if metrics["work_break_count"] != break_count:
                raise ProjectConstraintError(f"Synthetic {case} break count mismatch.")
            checks[f"{case}_three_layers"] = "PASS"

        return {
            "status": "passed",
            "schema_version": SCHEMA_VERSION,
            "checks": checks,
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Apply the overhead-power constraint declared by a validated project "
            "request. Barrier width is accepted only from the request."
        )
    )
    parser.add_argument("--request", type=Path, help="Validated project generation request JSON.")
    parser.add_argument("--work", type=Path, default=DEFAULT_WORK, help="Input work-line datasource.")
    parser.add_argument("--work-layer", default=DEFAULT_WORK_LAYER, help="Input work layer (default: sulcation_lines).")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Constrained output GeoPackage.")
    parser.add_argument("--metadata-output", type=Path, default=DEFAULT_METADATA_OUTPUT, help="Central stage manifest JSON.")
    parser.add_argument("--min-fragment-m", type=float, default=0.0, help="Recorded minimum kept fragment length; default 0.")
    parser.add_argument("--target-crs", "--crs", dest="target_crs", help="Projected metric processing/output CRS.")
    parser.add_argument("--work-crs", help="CRS override only when work input has no CRS metadata.")
    parser.add_argument("--barrier-crs", help="CRS override only when barrier input has no CRS metadata.")
    parser.add_argument("--id-field", help="Optional unique work identifier field.")
    parser.add_argument("--barrier-id-field", help="Optional unique barrier identifier field.")
    parser.add_argument("--self-test", action="store_true", help="Run central-contract synthetic tests and exit.")
    return parser


def _validate_cli(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.self_test:
        return
    if args.request is None:
        parser.error("the following argument is required unless --self-test is used: --request")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_cli(parser, args)
    if args.self_test:
        try:
            print(json.dumps(run_self_test(), indent=2, sort_keys=True))
            return EXIT_OK
        except Exception as exc:
            print(_canonical_json({"status": "self_test_failed", "error": str(exc)}), file=sys.stderr)
            return EXIT_SELF_TEST

    try:
        code, manifest = apply_project_constraints(
            request_path=args.request,
            work_path=args.work,
            work_layer=args.work_layer,
            output=args.output,
            metadata_output=args.metadata_output,
            min_fragment_m=args.min_fragment_m,
            target_crs=args.target_crs,
            work_crs=args.work_crs,
            barrier_crs=args.barrier_crs,
            id_field=args.id_field,
            barrier_id_field=args.barrier_id_field,
        )
        stream = sys.stderr if code else sys.stdout
        print(json.dumps(manifest, indent=2, sort_keys=True), file=stream)
        return code
    except (ContractError, ProjectConstraintError, BarrierInputError, BarrierValidationError) as exc:
        print(_canonical_json({"status": "blocked", "error": str(exc)}), file=sys.stderr)
        return EXIT_BLOCKED
    except Exception as exc:
        print(_canonical_json({"status": "processing_error", "error": str(exc)}), file=sys.stderr)
        return EXIT_PROCESSING


if __name__ == "__main__":
    raise SystemExit(main())
