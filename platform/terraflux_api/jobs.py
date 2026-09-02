from __future__ import annotations

import json
import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import traceback
import zipfile
from pathlib import Path
from typing import Any, Callable

from .catalog import ENGINE_CATALOG, PRODUCTS, demo_artifact_path
from .services import build_readiness, canonical_sha256, file_sha256, new_id, utc_now
from .storage import LocalStore
from .uploads import safe_filename


class JobRunner:
    """In-process worker whose callable registry is the complete execution whitelist."""

    def __init__(self, store: LocalStore, workspace_root: Path):
        self.store = store
        self.workspace_root = workspace_root.resolve()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._engines: dict[str, Callable[[dict[str, Any]], None]] = {
            "validate_uploads": self._validate_uploads,
            "project_topography": self._project_topography,
            "project_pipeline_e0": self._project_pipeline_e0,
            "project_hydrology_screening": self._project_hydrology_screening,
            "demo_current_dataset": self._publish_demo,
        }
        if set(self._engines) != set(ENGINE_CATALOG):
            raise RuntimeError("engine implementation and public whitelist differ")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        queued_run_ids = self._recover_orphaned_runs()
        self._thread = threading.Thread(target=self._work, name="terraflux-local-worker", daemon=True)
        self._thread.start()
        for run_id in queued_run_ids:
            self._queue.put(run_id)

    def stop(self) -> None:
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=10)

    def submit(self, run_id: str) -> None:
        if not self._thread or not self._thread.is_alive():
            self.start()
        self._queue.put(run_id)

    def _log(self, run_id: str, level: str, message: str) -> None:
        self.store.append_run_log(run_id, {"at": utc_now(), "level": level, "message": message[:2000]})

    def _set(self, run_id: str, **values: Any) -> None:
        values["updated_at"] = utc_now()
        self.store.update("runs", run_id, values)

    def _work(self) -> None:
        while True:
            run_id = self._queue.get()
            if run_id is None:
                self._queue.task_done()
                return
            try:
                self._execute(run_id)
            except Exception as exc:
                try:
                    self._set(
                        run_id,
                        status="FAILED",
                        finished_at=utc_now(),
                        error_code=type(exc).__name__,
                        error_message=str(exc)[:2000],
                    )
                except Exception:
                    pass
            finally:
                self._queue.task_done()

    def _execute(self, run_id: str) -> None:
        run = self.store.get("runs", run_id)
        if run is None:
            return
        engine = self._engines.get(run["engine_id"])
        if engine is None:
            self._set(run_id, status="FAILED", finished_at=utc_now(), error_code="ENGINE_NOT_WHITELISTED")
            return
        if run.get("cancel_requested"):
            self._set(run_id, status="CANCELLED", finished_at=utc_now(), progress=0)
            return
        self._set(run_id, status="RUNNING", started_at=utc_now(), progress=5)
        self.store.update(
            "projects",
            run["project_id"],
            {"status": "PROCESSING", "updated_at": utc_now()},
        )
        self._log(run_id, "INFO", f"Motor permitido iniciado: {run['engine_id']}")
        try:
            engine(run)
            refreshed = self.store.get("runs", run_id) or {}
            if refreshed.get("cancel_requested"):
                self._discard_run_publications(run_id)
                self._set(run_id, status="CANCELLED", finished_at=utc_now())
                self._log(run_id, "WARN", "Execucao cancelada.")
            else:
                self._set(run_id, status="SUCCEEDED", finished_at=utc_now(), progress=100)
                self.store.update(
                    "projects",
                    run["project_id"],
                    {"status": "RESULTS_AVAILABLE", "updated_at": utc_now()},
                )
                self._log(run_id, "INFO", "Execucao concluida e artefatos registrados.")
        except Exception as exc:  # worker boundary: preserve failure and trace locally
            self._discard_run_publications(run_id)
            self._set(
                run_id,
                status="FAILED",
                finished_at=utc_now(),
                error_code=type(exc).__name__,
                error_message=str(exc)[:2000],
            )
            self._log(run_id, "ERROR", f"Falha: {type(exc).__name__}: {exc}")
            self._log(run_id, "DEBUG", traceback.format_exc(limit=8))

    def _discard_run_publications(self, run_id: str) -> None:
        """Do not expose a partially verified package from a failed/cancelled run."""

        for collection in ("artifacts", "scenarios"):
            for record in self.store.list(collection, lambda item: item.get("run_id") == run_id):
                self.store.delete(collection, record["id"])

    def _recover_orphaned_runs(self) -> list[str]:
        """Fail interrupted work and restore queued work after a process restart."""

        queued_run_ids: list[str] = []
        for run in self.store.list("runs"):
            status = run.get("status")
            if status == "QUEUED":
                queued_run_ids.append(run["id"])
                continue
            if status != "RUNNING":
                continue
            run_id = run["id"]
            self._discard_run_publications(run_id)
            self._set(
                run_id,
                status="FAILED",
                stage="INTERRUPTED_BY_WORKER_RESTART",
                finished_at=utc_now(),
                error_code="WORKER_PROCESS_INTERRUPTED",
                error_message=(
                    "A execucao foi interrompida pela parada do processo. "
                    "Nenhum pacote parcial foi publicado; inicie uma nova rodada."
                ),
                result="INTERRUPTED_NO_PUBLISHED_PACKAGE",
                result_summary={
                    "guidance_authorized": False,
                    "partial_publications_discarded": True,
                },
            )
            self._log(
                run_id,
                "ERROR",
                "Rodada interrompida por reinicio do worker; publicacoes parciais removidas.",
            )
        successful_project_ids = {
            item["project_id"]
            for item in self.store.list("runs")
            if item.get("status") == "SUCCEEDED"
        }
        for completed in self.store.list("runs"):
            if completed.get("status") != "SUCCEEDED" or completed.get("engine_id") != "project_pipeline_e0":
                continue
            selected = set(completed.get("product_ids", []))
            normalized: dict[str, Any] = {}
            if completed.get("stage") == "PUBLISHED_E0_SCENARIOS":
                normalized["stage"] = "PUBLISHED_SCENARIO_SCREENING"
            if (
                completed.get("result") == "PASS_E0_SCENARIO_SCREENING"
                and {"SULCATION_E0", "CF0_CONTINUOUS"} <= selected
            ):
                normalized["result"] = "PASS_E0_CF0_GEOMETRIC_SCREENING"
            if normalized:
                self._set(completed["id"], **normalized)
        for project_id in successful_project_ids:
            project = self.store.get("projects", project_id)
            if project and project.get("status") in {"DRAFT", "PROCESSING"}:
                self.store.update(
                    "projects",
                    project_id,
                    {"status": "RESULTS_AVAILABLE", "updated_at": utc_now()},
                )
        return sorted(queued_run_ids)

    def _artifact(
        self,
        run: dict[str, Any],
        path: Path,
        product_id: str,
        source: str,
        *,
        delivery_level: str = "E0_TRIAGEM",
    ) -> dict[str, Any]:
        artifact = {
            "id": new_id("art"),
            "run_id": run["id"],
            "project_id": run["project_id"],
            "product_id": product_id,
            "filename": path.name,
            "media_type": _media_type(path),
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
            "path": str(path.resolve()),
            "source": source,
            "created_at": utc_now(),
            "delivery_level": delivery_level,
            "guidance_authorized": False,
        }
        self.store.insert("artifacts", artifact)
        return artifact

    def _validate_uploads(self, run: dict[str, Any]) -> None:
        project = self.store.get("projects", run["project_id"])
        if project is None:
            raise RuntimeError("project disappeared")
        self._set(run["id"], progress=35)
        readiness = build_readiness(self.store, project)
        run_dir = self.store.project_dir(project["id"]) / "runs" / run["id"]
        run_dir.mkdir(parents=True, exist_ok=True)
        report_path = run_dir / "input_validation_report.json"
        report = {
            "schema_version": "1.0.0",
            "run_id": run["id"],
            "project_id": project["id"],
            "created_at": utc_now(),
            "readiness": readiness,
            "result": "PASS_MINIMUM_UPLOAD_PACKAGE_PENDING_SPATIAL_QA" if readiness["summary"]["minimum_inputs_present"] else "BLOCKED_INPUTS",
            "delivery_boundary": "VALIDATION_ONLY",
        }
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        self._set(run["id"], progress=80, result=report["result"])
        self._artifact(run, report_path, "INPUT_VALIDATION", "GENERATED_BY_LOCAL_API")
        self._log(run["id"], "INFO", f"Validacao: {report['result']}; {readiness['summary']['blocking_count']} bloqueios.")

    def _publish_demo(self, run: dict[str, Any]) -> None:
        if not run["product_ids"]:
            raise ValueError("demo_current_dataset requires product_ids")
        selected: list[tuple[str, Path]] = []
        for product_id in run["product_ids"]:
            product = PRODUCTS.get(product_id)
            if product is None or not product["implementation"].startswith("DEMO_AVAILABLE"):
                raise ValueError(f"product is not available in demo: {product_id}")
            for filename in product.get("demo_artifacts", []):
                path = demo_artifact_path(self.workspace_root, filename).resolve()
                allowed_root = (self.workspace_root / "dataset" / "derived").resolve()
                if allowed_root not in path.parents or not path.is_file():
                    raise FileNotFoundError(f"whitelisted demo artifact is missing: {filename}")
                selected.append((product_id, path))
        total = max(1, len(selected))
        for index, (product_id, path) in enumerate(selected, start=1):
            refreshed = self.store.get("runs", run["id"])
            if refreshed and refreshed.get("cancel_requested"):
                return
            self._artifact(run, path, product_id, "DEMO_CURRENT_DATASET_REFERENCE")
            self._set(run["id"], progress=10 + int(80 * index / total))
        self._log(run["id"], "INFO", f"{len(selected)} artefatos demonstrativos publicados.")

    def _project_hydrology_screening(self, run: dict[str, Any]) -> None:
        workspace_text = str(self.workspace_root)
        if workspace_text not in sys.path:
            sys.path.insert(0, workspace_text)
        from scripts.pcx1_runoff import (
            RainfallInterval,
            calculate_pcx1_rainfall_excess,
            validate_pcx1_release,
        )

        project = self.store.get("projects", run["project_id"])
        if project is None:
            raise RuntimeError("project disappeared")
        request = self.store.get("generation_requests", run.get("request_id") or "")
        if request is None or request["project_id"] != project["id"]:
            raise ValueError("project_hydrology_screening requires an immutable request from the same project")
        self._verify_request_snapshot(request)
        if request.get("product_ids") != ["PCX1_RUNOFF_SCREENING"]:
            raise ValueError("PCX1_REQUEST_PRODUCT_SNAPSHOT_MISMATCH")

        configuration = request["configuration_snapshot"].get("hydrology_screening", {})
        if configuration.get("enabled") is not True:
            raise ValueError("PCX1_CONFIGURATION_NOT_ENABLED")
        source_id = str(configuration.get("parameter_source_id") or "").strip()
        if not source_id:
            raise ValueError("PCX1_PARAMETER_SOURCE_REQUIRED")
        interval_values = configuration.get("rainfall_intervals")
        if not isinstance(interval_values, list) or not interval_values:
            raise ValueError("PCX1_RAINFALL_INTERVALS_REQUIRED")
        intervals = [RainfallInterval(**item) for item in interval_values]

        self._set(run["id"], progress=30, stage="CALCULATING_PCX1_RAINFALL_EXCESS")
        result = calculate_pcx1_rainfall_excess(
            intervals,
            catchment_area_ha=configuration.get("catchment_area_ha"),
            curve_number=configuration.get("curve_number"),
            initial_abstraction_ratio=configuration.get("initial_abstraction_ratio"),
        )
        validate_pcx1_release(result)
        result.update(
            {
                "schema_version": "1.0.0",
                "manifest_type": "PCX1_RAINFALL_EXCESS_SCREENING_RESULT",
                "project_id": project["id"],
                "run_id": run["id"],
                "request_ref": {"id": request["id"], "sha256": request["sha256"]},
                "parameter_source": {
                    "source_id": source_id,
                    "evidence_state": configuration.get("parameter_evidence_state"),
                },
                "stage_status": "SCREENING_ONLY_WITH_EXPLICIT_BLOCKERS",
                "blocker_codes": [
                    "PCX1_METHOD_PROJECT_APPROVAL_REQUIRED",
                    "PCX_HYDROGRAPH_NOT_IMPLEMENTED",
                    "PCX_HYDRAULIC_ROUTING_NOT_IMPLEMENTED",
                    "PCX_SECTION_CAPACITY_NOT_EVALUATED",
                    "PCX_RECEIVER_NOT_APPROVED",
                    "GUIDANCE_NOT_AUTHORIZED",
                ],
            }
        )
        if configuration.get("parameter_evidence_state") != "PROJECT_EVIDENCE":
            result["blocker_codes"].append("PCX1_PROJECT_EVIDENCE_INCOMPLETE")

        run_dir = self.store.project_dir(project["id"]) / "runs" / run["id"]
        output_dir = run_dir / "products" / "pcx1_runoff_screening"
        output_dir.mkdir(parents=True, exist_ok=False)
        manifest_path = output_dir / "pcx1_rainfall_excess.json"
        manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        csv_path = output_dir / "pcx1_rainfall_excess_intervals.csv"
        columns = (
            "index,duration_s,elapsed_s,rainfall_mm,rainfall_intensity_mm_h,"
            "cumulative_rainfall_mm,rainfall_excess_mm,cumulative_rainfall_excess_mm,incremental_loss_mm\n"
        )
        rows = [
            ",".join(str(item[key]) for key in (
                "index", "duration_s", "elapsed_s", "rainfall_mm",
                "rainfall_intensity_mm_h", "cumulative_rainfall_mm",
                "rainfall_excess_mm", "cumulative_rainfall_excess_mm", "incremental_loss_mm"
            ))
            for item in result["intervals"]
        ]
        csv_path.write_text(columns + "\n".join(rows) + "\n", encoding="utf-8")
        self._artifact(run, manifest_path, "PCX1_RUNOFF_SCREENING", "GENERATED_FROM_CLIENT_CONFIGURATION")
        self._artifact(run, csv_path, "PCX1_RUNOFF_SCREENING", "GENERATED_FROM_CLIENT_CONFIGURATION")
        self._set(
            run["id"],
            progress=95,
            stage="PUBLISHED_PCX1_SCREENING",
            result="PASS_PCX1_RAINFALL_EXCESS_SCREENING_NOT_DESIGN",
            result_summary={
                "total_rainfall_mm": result["total_rainfall_mm"],
                "total_rainfall_excess_mm": result["total_rainfall_excess_mm"],
                "total_rainfall_excess_volume_m3": result["total_rainfall_excess_volume_m3"],
                "runoff_coefficient_event": result["runoff_coefficient_event"],
                "blocker_codes": result["blocker_codes"],
                "guidance_authorized": False,
            },
        )
        self._log(run["id"], "WARN", "PCX1 publicado como chuva-excesso de triagem; hidrograma e capacidade nao avaliados.")

    def _project_topography(self, run: dict[str, Any]) -> None:
        project = self.store.get("projects", run["project_id"])
        if project is None:
            raise RuntimeError("project disappeared")
        request = self.store.get("generation_requests", run.get("request_id") or "")
        if request is None or request["project_id"] != project["id"]:
            raise ValueError("project_topography requires an immutable request from the same project")
        self._verify_request_snapshot(request)

        assets = self._snapshot_assets(project["id"], request)
        by_role: dict[str, list[dict[str, Any]]] = {}
        for asset in assets:
            by_role.setdefault(asset["role"], []).append(asset)
        if not by_role.get("FIELD_BOUNDARY"):
            raise ValueError("FIELD_BOUNDARY is required")
        if not (by_role.get("DTM_DEM") or by_role.get("POINT_CLOUD")):
            raise ValueError("DTM_DEM or POINT_CLOUD is required")

        run_dir = self.store.project_dir(project["id"]) / "runs" / run["id"]
        input_dir = run_dir / "inputs"
        output_dir = run_dir / "products" / "topography_e0"
        input_dir.mkdir(parents=True, exist_ok=False)
        output_dir.mkdir(parents=True, exist_ok=False)
        boundary = self._prepare_boundary(by_role["FIELD_BOUNDARY"], input_dir / "boundary")

        configuration = request["configuration_snapshot"]
        topography = configuration.get("topography", {})
        preference = topography.get("elevation_source_preference", "DTM_DEM")
        terrain, point_cloud = self._select_elevation_source(by_role, preference)

        script = (self.workspace_root / "scripts" / "run_project_topography.py").resolve()
        if not script.is_file():
            raise FileNotFoundError(f"topography engine script is missing: {script}")
        launcher = self._qgis_python_launcher()
        command = [
            str(launcher), str(script),
            "--project-id", project["id"],
            "--boundary", str(boundary),
            "--resolution-m", str(topography.get("resolution_m", 1.0)),
            "--contour-interval-m", str(topography.get("contour_interval_m", 1.0)),
            "--output-dir", str(output_dir),
        ]
        if project.get("crs"):
            command.extend(["--crs", project["crs"]])
        if topography.get("field_id_column"):
            command.extend(["--field-id-column", topography["field_id_column"]])
        if topography.get("boundary_layer"):
            command.extend(["--boundary-layer", topography["boundary_layer"]])
        if terrain:
            command.extend(["--terrain", terrain["path"]])
        elif point_cloud:
            command.extend(["--point-cloud", point_cloud["path"]])
        else:
            raise ValueError("configured elevation source is unavailable")

        self._set(run["id"], progress=20, stage="SPATIAL_QA_AND_TERRAIN")
        self._log(run["id"], "INFO", "Executando topografia E0 em diretorio isolado da rodada.")
        self._run_process(run, command, "topography")

        manifest_path = (output_dir / "topography_manifest.json").resolve()
        if not manifest_path.is_file():
            raise RuntimeError("topography engine did not publish its manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("project_id") != project["id"] or manifest.get("guidance_authorized") is not False:
            raise RuntimeError("topography manifest identity or release boundary is invalid")
        self._enforce_scenario_resolution_gate(run, manifest, configuration)
        self._set(run["id"], progress=82, stage="VERIFYING_AND_PUBLISHING")
        outputs = manifest.get("outputs")
        if not isinstance(outputs, dict) or not outputs:
            raise RuntimeError("topography manifest contains no outputs")
        observed_paths: set[Path] = set()
        for output in outputs.values():
            path = self._declared_path(output).resolve()
            if output_dir not in path.parents or not path.is_file():
                raise RuntimeError("topography manifest references an output outside this run")
            if path in observed_paths:
                raise RuntimeError(f"topography manifest repeats output path: {path.name}")
            observed_paths.add(path)
            if int(output.get("size_bytes", -1)) != path.stat().st_size:
                raise RuntimeError(f"topography output size mismatch: {path.name}")
            if file_sha256(path) != str(output.get("sha256", "")).lower():
                raise RuntimeError(f"topography output hash mismatch: {path.name}")
            self._artifact(run, path, "TOPOGRAPHY_E0", "GENERATED_FROM_CLIENT_DATA")
        self._artifact(run, manifest_path, "TOPOGRAPHY_E0", "GENERATED_FROM_CLIENT_DATA")
        if run.get("engine_id") == "project_topography":
            self._set(run["id"], progress=90, stage="GENERATING_REVIEW_DOSSIER")
            self._publish_run_dossier(run, project, request)
        artifact_count = len(
            self.store.list("artifacts", lambda item: item["run_id"] == run["id"])
        )
        self._set(
            run["id"],
            progress=95,
            stage="PUBLISHED_E0",
            result="PASS_E0_TOPOGRAPHY_SCREENING",
            result_summary={
                "field_count": manifest.get("scope", {}).get("field_count"),
                "area_ha": manifest.get("scope", {}).get("area_ha"),
                "artifact_count": artifact_count,
                "terrain_resolution": manifest.get("resolution_provenance", {}),
                "guidance_authorized": False,
            },
        )
        used_elevation_id = (terrain or point_cloud or {}).get("id")
        for asset in assets:
            if asset["role"] == "FIELD_BOUNDARY" or asset["id"] == used_elevation_id:
                self.store.update("assets", asset["id"], {"spatial_qa_status": "PASSED_E0_ENGINE", "updated_at": utc_now()})
        self._log(run["id"], "INFO", f"Topografia E0 publicada com {artifact_count} artefatos verificados.")

    def _project_pipeline_e0(self, run: dict[str, Any]) -> None:
        project = self.store.get("projects", run["project_id"])
        if project is None:
            raise RuntimeError("project disappeared")
        request = self.store.get("generation_requests", run.get("request_id") or "")
        if request is None or request["project_id"] != project["id"]:
            raise ValueError("project_pipeline_e0 requires an immutable request from the same project")

        selected = set(run.get("product_ids", []))
        scenario_products = {
            "SULCATION_E0",
            "CF0_CONTINUOUS",
            "C1_EMBEDDED_SCREENING",
        }
        if not selected & scenario_products:
            raise ValueError(
                "project_pipeline_e0 requires SULCATION_E0, CF0_CONTINUOUS or C1_EMBEDDED_SCREENING"
            )
        if "C1_EMBEDDED_SCREENING" in selected and "CF0_CONTINUOUS" not in selected:
            raise ValueError("C1_REQUIRES_CF0_CONTINUOUS_IN_THE_SAME_RUN")
        configuration = request["configuration_snapshot"]
        topography = configuration.get("topography", {})
        sulcation = configuration.get("sulcation", {})
        constraints = configuration.get("constraints", {})
        field_id_column = topography.get("field_id_column")
        if not field_id_column:
            raise ValueError("FIELD_ID_COLUMN_REQUIRED")
        power_state = constraints.get("power_network_state", "UNKNOWN")
        if power_state == "UPLOADED":
            raise ValueError("POWER_BARRIER_STAGE_REQUIRED")
        if power_state != "DECLARED_NONE":
            raise ValueError("POWER_NETWORK_UNRESOLVED")
        if sulcation.get("allow_cross_field"):
            raise ValueError("CROSS_FIELD_DOMAIN_REQUIRES_REVIEWED_OPERATIONAL_SURFACES")
        if sulcation.get("allow_cross_property"):
            raise ValueError("CROSS_PROPERTY_DOMAIN_REQUIRES_REVIEWED_PERMISSION_AND_SURFACES")
        terrain_resolution_m = float(topography.get("resolution_m", 1.0))
        row_spacing_m = float(sulcation.get("row_spacing_m", 1.5))
        if "SULCATION_E0" in selected and terrain_resolution_m > row_spacing_m:
            raise ValueError("TERRAIN_RESOLUTION_TOO_COARSE_FOR_ROWS")
        if selected & {"CF0_CONTINUOUS", "C1_EMBEDDED_SCREENING"} and terrain_resolution_m > row_spacing_m:
            raise ValueError("TERRAIN_RESOLUTION_TOO_COARSE_FOR_CF0")

        # The terrain package is generated in the same immutable run and becomes
        # the only input lineage accepted by the E0/CF0/C1 stages below.
        self._project_topography(run)
        refreshed = self.store.get("runs", run["id"])
        if refreshed and refreshed.get("cancel_requested"):
            return

        run_dir = self.store.project_dir(project["id"]) / "runs" / run["id"]
        topography_dir = run_dir / "products" / "topography_e0"
        topography_manifest = topography_dir / "topography_manifest.json"
        topography_result = json.loads(topography_manifest.read_text(encoding="utf-8"))
        output_grid_resolution_m = self._verified_resolution_value(
            topography_result.get("resolution_provenance", {}).get("output_grid_resolution_m"),
            "topography output grid resolution",
        )
        engine_dir = run_dir / "engine"
        engine_dir.mkdir(parents=True, exist_ok=False)
        engine_request = engine_dir / "project_generation_request.json"
        review_status = {
            "REVIEWED": "COMPLETE",
            "PARTIAL": "PARTIAL",
            "NOT_REVIEWED": "NOT_REVIEWED",
        }.get(constraints.get("general_review_status"), "NOT_REVIEWED")
        build_command = [
            sys.executable,
            str((self.workspace_root / "scripts" / "build_platform_engine_request.py").resolve()),
            "--project-id", project["id"],
            "--request-id", request["id"],
            "--topography-manifest", str(topography_manifest),
            "--property-id", project["id"],
            "--row-spacing-m", str(sulcation.get("row_spacing_m", 1.5)),
            "--headland-m", str(sulcation.get("headland_width_m", 18.0)),
            "--minimum-work-path-radius-m", str(sulcation.get("min_turn_radius_m", 12.0)),
            "--minimum-shot-length-m", str(sulcation.get("min_shot_length_m", 50.0)),
            "--nominal-speed-kmh", str(sulcation.get("nominal_speed_kmh", 5.0)),
            "--power-status", "DECLARED_NONE",
            "--constraint-review-status", review_status,
            "--no-cross-field",
            "--no-cross-property",
            "--cross-property-permission", "DENIED",
            "--user-name", project.get("client_name") or project["name"],
            "--user-organization", project.get("farm_name") or "client-organization",
            "--user-role", "project-configurator",
            "--output", str(engine_request),
        ]
        expected_yield = configuration.get("logistics", {}).get("yield_t_ha")
        if expected_yield is not None:
            build_command.extend(["--expected-yield-t-ha", str(expected_yield)])
        self._set(run["id"], progress=36, stage="MATERIALIZING_ENGINE_REQUEST")
        self._run_process(run, build_command, "request builder")
        built_request = json.loads(engine_request.read_text(encoding="utf-8"))
        if built_request.get("project_id") != project["id"] or built_request.get("request_id") != request["id"]:
            raise RuntimeError("engine request identity mismatch")
        engine_request_sha256 = file_sha256(engine_request)
        trace_product = "SULCATION_E0" if "SULCATION_E0" in selected else "CF0_CONTINUOUS"
        self._artifact(run, engine_request, trace_product, "GENERATED_REQUEST_LINEAGE")

        scenario_count = 0
        published = 1
        if "SULCATION_E0" in selected:
            self._set(run["id"], progress=44, stage="GENERATING_SULCATION_E0")
            e0_dir = run_dir / "products" / "sulcation_e0"
            e0_dir.mkdir(parents=True, exist_ok=False)
            e0_gpkg = e0_dir / "sulcation_scenarios.gpkg"
            e0_map = e0_dir / "sulcation_scenarios_map.png"
            e0_metrics = e0_dir / "sulcation_scenario_metrics.json"
            e0_command = [
                str(self._qgis_python_launcher()),
                str((self.workspace_root / "scripts" / "generate_sulcation_scenarios.py").resolve()),
                "--request", str(engine_request),
                "--output-gpkg", str(e0_gpkg),
                "--output-map", str(e0_map),
                "--output-metrics", str(e0_metrics),
            ]
            self._run_process(run, e0_command, "sulcation E0")
            metrics = json.loads(e0_metrics.read_text(encoding="utf-8"))
            if metrics.get("status") != "E0_topographic_sulcation_geometry_screening":
                raise RuntimeError(f"unexpected sulcation E0 status: {metrics.get('status')}")
            self._verify_known_outputs(
                e0_dir,
                {"geopackage": e0_gpkg, "map": e0_map},
                metrics.get("output_integrity", {}),
            )
            for path in (e0_gpkg, e0_map, e0_metrics):
                self._artifact(run, path, "SULCATION_E0", "GENERATED_FROM_CLIENT_DATA")
                published += 1
            scenario_count += self._publish_e0_scenarios(
                run, metrics, configuration, engine_request_sha256
            )

        cf0_pass_count = 0
        if selected & {"CF0_CONTINUOUS", "C1_EMBEDDED_SCREENING"}:
            self._set(run["id"], progress=68, stage="GENERATING_CF0_CONTINUOUS_FAMILY")
            cf0_dir = run_dir / "products" / "cf0_continuous"
            cf0_dir.mkdir(parents=True, exist_ok=False)
            cf0_gpkg = cf0_dir / "continuous_family_candidates.gpkg"
            cf0_map = cf0_dir / "continuous_family_map.png"
            cf0_manifest_path = cf0_dir / "continuous_family_manifest.json"
            cf0_rasters = cf0_dir / "rasters"
            cf0_command = [
                str(self._qgis_python_launcher()),
                str((self.workspace_root / "scripts" / "generate_continuous_family.py").resolve()),
                "--request", str(engine_request),
                "--field-id-column", field_id_column,
                "--grid-resolution-m", str(output_grid_resolution_m),
                "--output-gpkg", str(cf0_gpkg),
                "--output-manifest", str(cf0_manifest_path),
                "--output-map", str(cf0_map),
                "--output-raster-dir", str(cf0_rasters),
            ]
            self._run_process(run, cf0_command, "continuous family CF0")
            manifest = json.loads(cf0_manifest_path.read_text(encoding="utf-8"))
            if manifest.get("release") != "CF0_GEOMETRIC_SCREENING":
                raise RuntimeError("unexpected CF0 manifest release")
            request_ref = manifest.get("project_request_ref", {})
            if request_ref.get("id") != request["id"]:
                raise RuntimeError("CF0 manifest request identity mismatch")
            self._verify_cf0_outputs(cf0_dir, manifest, cf0_gpkg, cf0_map, cf0_rasters)
            for path in (cf0_gpkg, cf0_map, cf0_manifest_path, *sorted(cf0_rasters.glob("*.tif"))):
                self._artifact(run, path, "CF0_CONTINUOUS", "GENERATED_FROM_CLIENT_DATA")
                published += 1
            scenario_count += self._publish_cf0_scenarios(
                run, manifest, configuration, engine_request_sha256
            )

        if "C1_EMBEDDED_SCREENING" in selected:
            refreshed = self.store.get("runs", run["id"])
            if refreshed and refreshed.get("cancel_requested"):
                return
            self._set(run["id"], progress=84, stage="GENERATING_C1_CONCEPT_SCREENING")
            c1_request_path = engine_dir / "c1_screening_request.json"
            c1_screening_id = f"{run['id']}-c1-e0"
            c1_request_payload = self._c1_screening_request_payload(
                screening_id=c1_screening_id,
                project_request_path=engine_request,
                cf0_manifest_path=cf0_manifest_path,
                screening_request_path=c1_request_path,
            )
            self._write_runtime_json(c1_request_path, c1_request_payload)
            c1_request_sha256 = file_sha256(c1_request_path)

            c1_dir = run_dir / "products" / "c1_embedded_screening"
            c1_dir.mkdir(parents=True, exist_ok=False)
            c1_gpkg = c1_dir / "embedded_terrace_screening.gpkg"
            c1_map = c1_dir / "embedded_terrace_screening_map.png"
            c1_manifest_path = c1_dir / "embedded_terrace_screening_manifest.json"
            c1_command = [
                str(self._qgis_python_launcher()),
                str(
                    (
                        self.workspace_root
                        / "scripts"
                        / "generate_embedded_terrace_screening.py"
                    ).resolve()
                ),
                "--screening-request", str(c1_request_path),
                "--field-id-column", field_id_column,
                "--output-gpkg", str(c1_gpkg),
                "--output-manifest", str(c1_manifest_path),
                "--output-map", str(c1_map),
            ]
            self._run_process(run, c1_command, "embedded terrace C1 concept screening")

            from .scenario_results import c1_artifact_records, validate_c1_result

            c1_manifest = validate_c1_result(
                c1_manifest_path,
                expected_project_request_id=request["id"],
                expected_project_request_sha256=engine_request_sha256,
                expected_sensitivity_request_id=c1_screening_id,
                expected_sensitivity_request_sha256=c1_request_sha256,
                expected_cf0_manifest_sha256=file_sha256(cf0_manifest_path),
                expected_cf0_geopackage_sha256=file_sha256(cf0_gpkg),
            )
            if self._declared_path(c1_manifest["project_request_ref"]) != engine_request.resolve():
                raise RuntimeError("C1 project request path differs from the immutable dependency")
            if self._declared_path(c1_manifest["sensitivity_request_ref"]) != c1_request_path.resolve():
                raise RuntimeError("C1 sensitivity request path differs from the immutable dependency")
            for input_name, record in c1_manifest["inputs"].items():
                declared = self._declared_path(record)
                expected = {
                    "cf0_manifest": cf0_manifest_path.resolve(),
                    "cf0_geopackage": cf0_gpkg.resolve(),
                }.get(input_name, declared)
                self._verify_manifest_file(
                    record,
                    expected,
                    run_dir,
                    f"C1 input {input_name}",
                )
            self._verify_known_outputs(
                c1_dir,
                {"geopackage": c1_gpkg, "map": c1_map},
                c1_manifest["outputs"],
            )

            concept_artifacts = {
                self._declared_path(record): record
                for record in c1_artifact_records(c1_manifest)
            }
            for path in (c1_gpkg, c1_map):
                record = concept_artifacts.get(path.resolve())
                if record is None:
                    raise RuntimeError(f"C1 artifact adapter omitted {path.name}")
                artifact = self._artifact(
                    run,
                    path,
                    "C1_EMBEDDED_SCREENING",
                    "GENERATED_FROM_CLIENT_DATA",
                    delivery_level="CONCEPT_ONLY",
                )
                self.store.update(
                    "artifacts",
                    artifact["id"],
                    {
                        "artifact_type": record["artifact_type"],
                        "role": record["role"],
                        "previewable": record["previewable"],
                        "updated_at": utc_now(),
                    },
                )
                published += 1
            for path, source in (
                (c1_request_path, "GENERATED_REQUEST_LINEAGE"),
                (c1_manifest_path, "GENERATED_FROM_CLIENT_DATA"),
            ):
                self._artifact(
                    run,
                    path,
                    "C1_EMBEDDED_SCREENING",
                    source,
                    delivery_level="CONCEPT_ONLY",
                )
                published += 1
            scenario_count += self._publish_c1_scenarios(
                run,
                c1_manifest,
                c1_screening_id=c1_screening_id,
                c1_request_sha256=c1_request_sha256,
                engine_request_sha256=engine_request_sha256,
                cf0_manifest_sha256=file_sha256(cf0_manifest_path),
                cf0_geopackage_sha256=file_sha256(cf0_gpkg),
            )

        published_scenarios = self.store.list(
            "scenarios", lambda item: item["run_id"] == run["id"]
        )
        scenario_count = len(published_scenarios)
        eligible_e0_count = sum(
            item.get("family", "").startswith("E0") and item.get("geometry_eligible") is True
            for item in published_scenarios
        )
        diagnostic_e0_count = sum(
            item.get("family", "").startswith("E0") and item.get("geometry_eligible") is False
            for item in published_scenarios
        )
        cf0_pass_count = sum(
            int(item.get("metrics", {}).get("geometric_pass_count", 0))
            for item in published_scenarios
            if item.get("family", "").startswith("CF0")
        )
        c1_concept_count = sum(
            item.get("family", "").startswith("C1")
            for item in published_scenarios
        )
        c1_ti_count = sum(
            item.get("family", "").startswith("C1") and item.get("variant") == "EMBUTIDA_TI"
            for item in published_scenarios
        )
        c1_td_blocked_count = sum(
            item.get("family", "").startswith("C1") and item.get("variant") == "EMBUTIDA_TD"
            for item in published_scenarios
        )
        has_eligible_geometry = bool(eligible_e0_count or cf0_pass_count)
        if has_eligible_geometry:
            if "C1_EMBEDDED_SCREENING" in selected and "SULCATION_E0" in selected:
                run_result = "PASS_E0_CF0_WITH_C1_CONCEPT_SCREENING"
            elif "C1_EMBEDDED_SCREENING" in selected:
                run_result = "PASS_CF0_WITH_C1_CONCEPT_SCREENING"
            elif {"SULCATION_E0", "CF0_CONTINUOUS"} <= selected:
                run_result = "PASS_E0_CF0_GEOMETRIC_SCREENING"
            elif "CF0_CONTINUOUS" in selected:
                run_result = "PASS_CF0_GEOMETRIC_SCREENING"
            else:
                run_result = "PASS_E0_SCENARIO_SCREENING"
        else:
            run_result = "DIAGNOSTIC_PUBLISHED_NO_ELIGIBLE_SCENARIO"
        artifact_count_before_dossier = len(
            self.store.list("artifacts", lambda item: item["run_id"] == run["id"])
        )
        result_summary = {
            "artifact_count": artifact_count_before_dossier + 2,
            "scenario_count": scenario_count,
            "eligible_e0_scenario_count": eligible_e0_count,
            "diagnostic_e0_scenario_count": diagnostic_e0_count,
            "cf0_geometric_pass_count": cf0_pass_count,
            "c1_concept_scenario_count": c1_concept_count,
            "c1_ti_sensitivity_count": c1_ti_count,
            "c1_td_blocked_count": c1_td_blocked_count,
            "guidance_authorized": False,
            "hydraulic_status": "NOT_EVALUATED_OR_UNCONFIRMED",
        }
        # Materialize the release decision before generating the dossier so its
        # run context does not retain the intermediate topography-only result.
        self._set(
            run["id"],
            progress=93,
            stage="GENERATING_REVIEW_DOSSIER",
            result=run_result,
            result_summary=result_summary,
        )
        self._publish_run_dossier(run, project, request)
        artifact_count = len(
            self.store.list("artifacts", lambda item: item["run_id"] == run["id"])
        )
        result_summary["artifact_count"] = artifact_count
        self._set(
            run["id"],
            progress=96,
            stage="PUBLISHED_SCENARIO_SCREENING",
            result=run_result,
            result_summary=result_summary,
        )
        self._log(
            run["id"],
            "INFO",
            f"Pipeline de cenarios publicou {artifact_count} artefatos e {scenario_count} cenarios; "
            f"{eligible_e0_count} alternativas E0 elegiveis e {cf0_pass_count} blocos CF0 "
            f"com passe geometrico; {c1_concept_count} registros C1 apenas conceituais.",
        )

    def _run_process(self, run: dict[str, Any], command: list[str], label: str) -> None:
        completed = subprocess.run(
            command,
            cwd=self.workspace_root,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in (completed.stdout or "").splitlines():
            self._log(run["id"], "INFO", line)
        for line in (completed.stderr or "").splitlines()[-60:]:
            self._log(run["id"], "ENGINE", line)
        if completed.returncode != 0:
            raise RuntimeError(f"{label} engine exited with code {completed.returncode}")

    def _verify_manifest_file(
        self,
        record: Any,
        expected_path: Path,
        allowed_root: Path,
        label: str,
    ) -> None:
        resolved = expected_path.resolve()
        declared = self._declared_path(record)
        if declared != resolved:
            raise RuntimeError(f"{label} manifest path mismatch")
        if allowed_root.resolve() not in resolved.parents or not resolved.is_file():
            raise RuntimeError(f"{label} is missing or outside the run")
        if int(record.get("size_bytes", -1)) != resolved.stat().st_size:
            raise RuntimeError(f"{label} size mismatch")
        if file_sha256(resolved) != str(record.get("sha256", "")).lower():
            raise RuntimeError(f"{label} hash mismatch")

    def _verify_known_outputs(
        self,
        output_dir: Path,
        paths: dict[str, Path],
        integrity: dict[str, Any],
    ) -> None:
        if not isinstance(integrity, dict):
            raise RuntimeError("engine output integrity is missing")
        for key, path in paths.items():
            self._verify_manifest_file(
                integrity.get(key), path, output_dir, f"engine output {key}"
            )

    def _verify_cf0_outputs(
        self,
        output_dir: Path,
        manifest: dict[str, Any],
        gpkg: Path,
        map_path: Path,
        raster_dir: Path,
    ) -> None:
        outputs = manifest.get("outputs", {})
        if not isinstance(outputs, dict):
            raise RuntimeError("CF0 outputs are missing")
        self._verify_manifest_file(
            outputs.get("geopackage"), gpkg, output_dir, "CF0 GeoPackage"
        )
        self._verify_manifest_file(
            outputs.get("map"), map_path, output_dir, "CF0 map"
        )
        raster_records = outputs.get("rasters")
        if not isinstance(raster_records, list):
            raise RuntimeError("CF0 raster records are missing")
        declared_rasters: set[Path] = set()
        for index, record in enumerate(raster_records):
            declared = self._declared_path(record)
            if declared in declared_rasters:
                raise RuntimeError(f"CF0 raster path is duplicated: {declared.name}")
            declared_rasters.add(declared)
            self._verify_manifest_file(
                record, declared, raster_dir, f"CF0 raster {index}"
            )
        actual_rasters = {path.resolve() for path in raster_dir.glob("*.tif")}
        if declared_rasters != actual_rasters:
            raise RuntimeError("CF0 raster bundle differs from its signed manifest")

    def _publish_e0_scenarios(
        self,
        run: dict[str, Any],
        metrics: dict[str, Any],
        configuration: dict[str, Any],
        engine_request_sha256: str,
    ) -> int:
        from .scenario_results import aggregate_e0_scenarios

        objective_config = configuration.get("objectives", {})
        scenarios = aggregate_e0_scenarios(
            metrics,
            run_id=run["id"],
            project_id=run["project_id"],
            expected_request_id=run["request_id"],
            expected_request_sha256=engine_request_sha256,
            objective_weights={
                "conservation": objective_config.get("soil_conservation_weight", 45.0),
                "harvestability": objective_config.get("harvestability_weight", 35.0),
                "performance": objective_config.get("performance_weight", 20.0),
            },
        )
        for scenario in scenarios:
            scenario["created_at"] = utc_now()
            self.store.insert("scenarios", scenario)
        return len(scenarios)

    def _publish_run_dossier(
        self,
        run: dict[str, Any],
        project: dict[str, Any],
        request: dict[str, Any],
    ) -> int:
        run_dir = self.store.project_dir(project["id"]) / "runs" / run["id"]
        context_dir = run_dir / "dossier_context"
        output_dir = run_dir / "products" / "dossier"
        context_dir.mkdir(parents=True, exist_ok=False)
        output_dir.mkdir(parents=True, exist_ok=False)
        project_json = context_dir / "project.json"
        run_json = context_dir / "run.json"
        request_json = context_dir / "request.json"
        config_json = context_dir / "configuration.json"
        self._write_runtime_json(project_json, project)
        dossier_run = dict(self.store.get("runs", run["id"]) or run)
        scenario_products = {"SULCATION_E0", "CF0_CONTINUOUS", "C1_EMBEDDED_SCREENING"}
        dossier_run.update(
            {
                "status": "SUCCEEDED",
                "progress": 100,
                "stage": (
                    "PUBLISHED_SCENARIO_SCREENING"
                    if scenario_products.intersection(dossier_run.get("product_ids", []))
                    else "PUBLISHED_E0"
                ),
                "finished_at": utc_now(),
                "dossier_snapshot_role": "EXPECTED_TERMINAL_STATE_AFTER_PACKAGE_PREFLIGHT",
            }
        )
        if not dossier_run.get("result") and dossier_run["stage"] == "PUBLISHED_E0":
            dossier_run["result"] = "PASS_E0_TOPOGRAPHY_SCREENING"
        self._write_runtime_json(run_json, dossier_run)
        self._write_runtime_json(request_json, request)
        self._write_runtime_json(config_json, request["configuration_snapshot"])

        output_pdf = output_dir / (
            "Dossie_Tecnico_Rodada_E0_CF0_C1.pdf"
            if "C1_EMBEDDED_SCREENING" in set(run.get("product_ids", []))
            else "Dossie_Tecnico_Rodada_E0_CF0.pdf"
        )
        output_manifest = output_dir / "dossier_manifest.json"
        command = [
            sys.executable,
            str((self.workspace_root / "scripts" / "generate_platform_run_dossier.py").resolve()),
            "--project-json", str(project_json),
            "--run-json", str(run_json),
            "--request-json", str(request_json),
            "--config-json", str(config_json),
            "--output-pdf", str(output_pdf),
            "--output-manifest", str(output_manifest),
        ]
        optional_inputs = (
            ("--topography-manifest", run_dir / "products" / "topography_e0" / "topography_manifest.json"),
            ("--topography-map", run_dir / "products" / "topography_e0" / "topography_map.png"),
            ("--slope-map", run_dir / "products" / "topography_e0" / "slope_map.png"),
            ("--e0-metrics", run_dir / "products" / "sulcation_e0" / "sulcation_scenario_metrics.json"),
            ("--e0-map", run_dir / "products" / "sulcation_e0" / "sulcation_scenarios_map.png"),
            ("--cf0-manifest", run_dir / "products" / "cf0_continuous" / "continuous_family_manifest.json"),
            ("--cf0-map", run_dir / "products" / "cf0_continuous" / "continuous_family_map.png"),
            ("--c1-manifest", run_dir / "products" / "c1_embedded_screening" / "embedded_terrace_screening_manifest.json"),
            ("--c1-map", run_dir / "products" / "c1_embedded_screening" / "embedded_terrace_screening_map.png"),
        )
        for option, path in optional_inputs:
            if path.is_file():
                command.extend([option, str(path)])
        self._run_process(run, command, "review dossier")
        if not output_pdf.is_file() or not output_manifest.is_file():
            raise RuntimeError("review dossier did not publish PDF and manifest")
        manifest = json.loads(output_manifest.read_text(encoding="utf-8"))
        if (
            manifest.get("project_id") != project["id"]
            or manifest.get("run_id") != run["id"]
            or manifest.get("request_id") != request["id"]
            or manifest.get("guidance_authorized") is not False
        ):
            raise RuntimeError("review dossier identity or release boundary is invalid")
        pdf_record = manifest.get("pdf", {})
        if (
            int(manifest.get("page_count", 0)) < 1
            or pdf_record.get("sha256") != file_sha256(output_pdf)
            or int(pdf_record.get("size_bytes", -1)) != output_pdf.stat().st_size
        ):
            raise RuntimeError("review dossier PDF integrity is invalid")
        pdf_artifact = self._artifact(
            run, output_pdf, "COMPLETE_DOSSIER", "GENERATED_FROM_CLIENT_DATA"
        )
        self.store.update(
            "artifacts",
            pdf_artifact["id"],
            {"pages": int(manifest["page_count"]), "updated_at": utc_now()},
        )
        self._artifact(run, output_manifest, "COMPLETE_DOSSIER", "GENERATED_FROM_CLIENT_DATA")
        self._log(run["id"], "INFO", f"Dossie de revisao publicado com {manifest['page_count']} paginas.")
        return 2

    @staticmethod
    def _write_runtime_json(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    @staticmethod
    def _c1_screening_request_payload(
        *,
        screening_id: str,
        project_request_path: Path,
        cf0_manifest_path: Path,
        screening_request_path: Path,
    ) -> dict[str, Any]:
        """Build the fixed E0 sensitivity contract consumed by the C1 stage."""

        return {
            "schema_version": "1.0.0",
            "screening_id": screening_id,
            "project_request_ref": str(project_request_path.resolve()),
            "cf0_manifest_ref": str(cf0_manifest_path.resolve()),
            "requested_release": "C1_E0_CONCEPT_ALIGNMENT_NOT_DIMENSIONED",
            "assumption_contract": {
                "parameter_class": "E0_ASSUMPTION",
                "selection_meaning": "GEOMETRIC_SENSITIVITY_GRID_ONLY",
                "selection_rule": "DATASET_RELIEF_LEGIBILITY_GRID_V1",
                "interval_role": "ELEVATION_ISOLINE_SAMPLING_NOT_PCE_SPACING",
                "vertical_accuracy_status": "NOT_VALIDATED",
                "agronomic_default": False,
                "pce_spacing_claim": False,
                "pcx_dimensioning_claim": False,
                "ti_approval_claim": False,
                "td_approval_claim": False,
                "guidance_claim": False,
                "notes": (
                    "A grade 2/4/6 m e somente uma sensibilidade geometrica de isolinhas. "
                    "Nao representa espacamento PCE, secao PCX, dimensionamento TI/TD "
                    "ou recomendacao agronomica."
                ),
            },
            "screening_parameters": {
                "vertical_interval_candidates_m": [2.0, 4.0, 6.0],
                "offset_fractions": [0.0, 0.25, 0.5, 0.75],
                "topology_gap_half_width_m": 0.25,
                "minimum_axis_length_m": 15.0,
                "minimum_strip_area_m2": 50.0,
                "minimum_diagnostic_row_segment_m": 8.0,
                "profile_sample_step_m": 2.0,
                "map_offset_fraction": 0.0,
            },
            "variant_policy": {
                "EMBUTIDA_TI": "GENERATE_ISOLINE_SENSITIVITY_NOT_DIMENSIONED",
                "EMBUTIDA_TD": "NOT_GENERATED_WITHOUT_VERIFIED_RECEIVER_AND_GRADE_RULE",
                "receiver_dataset_ref": None,
                "td_longitudinal_grade_rule_ref": None,
            },
            "row_source_policy": {
                "source_candidate_id": "CF0C_OPERACAO",
                "method": "CLIP_EXISTING_CF0C_ROWS_BY_TOPOLOGY_ONLY_INTERTERRACE_STRIPS",
                "status": "DIAGNOSTIC_ONLY_NOT_RESOLVED_PER_STRIP",
                "limitation_code": "ROW_CANDIDATES_DERIVED_FROM_CF0C_NOT_RESOLVED_PER_STRIP",
            },
            "provenance": {
                "origin": "E0_ASSUMPTION",
                "source_ref": str(screening_request_path.resolve()),
                "captured_at": utc_now(),
                "responsible": {
                    "name": "terraflux-screening-engine",
                    "organization": "TerraFlux project",
                    "role": "geometric-screening",
                },
                "confidence": "LOW",
                "revision": "c1-e0-screening-1.0.0",
                "applicability": "SCENARIO",
            },
        }

    def _publish_cf0_scenarios(
        self,
        run: dict[str, Any],
        manifest: dict[str, Any],
        configuration: dict[str, Any],
        engine_request_sha256: str,
    ) -> int:
        from .scenario_results import summarize_cf0_candidates

        scenarios = summarize_cf0_candidates(
            manifest,
            run_id=run["id"],
            project_id=run["project_id"],
            expected_request_id=run["request_id"],
            expected_request_sha256=engine_request_sha256,
        )
        for scenario in scenarios:
            scenario["created_at"] = utc_now()
            self.store.insert("scenarios", scenario)
        return len(scenarios)

    def _publish_c1_scenarios(
        self,
        run: dict[str, Any],
        manifest: dict[str, Any],
        *,
        c1_screening_id: str,
        c1_request_sha256: str,
        engine_request_sha256: str,
        cf0_manifest_sha256: str,
        cf0_geopackage_sha256: str,
    ) -> int:
        from .scenario_results import summarize_c1_screening

        scenarios = summarize_c1_screening(
            manifest,
            run_id=run["id"],
            project_id=run["project_id"],
            expected_project_request_id=run["request_id"],
            expected_project_request_sha256=engine_request_sha256,
            expected_sensitivity_request_id=c1_screening_id,
            expected_sensitivity_request_sha256=c1_request_sha256,
            expected_cf0_manifest_sha256=cf0_manifest_sha256,
            expected_cf0_geopackage_sha256=cf0_geopackage_sha256,
        )
        for scenario in scenarios:
            scenario["created_at"] = utc_now()
            self.store.insert("scenarios", scenario)
        return len(scenarios)

    def _snapshot_assets(self, project_id: str, request: dict[str, Any]) -> list[dict[str, Any]]:
        assets: list[dict[str, Any]] = []
        for reference in request.get("asset_snapshot", []):
            asset = self.store.get("assets", reference["asset_id"])
            if asset is None or asset["project_id"] != project_id or asset.get("status") != "STORED":
                raise RuntimeError(f"request asset is unavailable: {reference['asset_id']}")
            path = Path(asset["path"]).resolve()
            project_root = self.store.project_dir(project_id).resolve()
            if project_root not in path.parents or not path.is_file():
                raise RuntimeError("request asset path is outside the project storage")
            if file_sha256(path) != reference["sha256"]:
                raise RuntimeError(f"request asset hash changed: {asset['id']}")
            asset["path"] = str(path)
            assets.append(asset)
        return assets

    @staticmethod
    def _verify_request_snapshot(request: dict[str, Any]) -> None:
        recorded = request.get("sha256")
        unsigned = {key: value for key, value in request.items() if key != "sha256"}
        if not isinstance(recorded, str) or canonical_sha256(unsigned) != recorded.lower():
            raise RuntimeError("immutable generation request hash mismatch")

    @staticmethod
    def _verified_resolution_value(value: Any, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError(f"{label} is not verified")
        result = float(value)
        if not math.isfinite(result) or result <= 0:
            raise RuntimeError(f"{label} is not verified")
        return result

    def _enforce_scenario_resolution_gate(
        self,
        run: dict[str, Any],
        manifest: dict[str, Any],
        configuration: dict[str, Any],
    ) -> None:
        selected = set(run.get("product_ids", []))
        if not selected & {"SULCATION_E0", "CF0_CONTINUOUS", "C1_EMBEDDED_SCREENING"}:
            return
        provenance = manifest.get("resolution_provenance")
        if not isinstance(provenance, dict):
            raise RuntimeError("TERRAIN_EFFECTIVE_RESOLUTION_UNVERIFIED")
        effective_resolution_m = self._verified_resolution_value(
            provenance.get("effective_resolution_m"),
            "effective terrain resolution",
        )
        self._verified_resolution_value(
            provenance.get("output_grid_resolution_m"),
            "topography output grid resolution",
        )
        if provenance.get("source_type") == "POINT_CLOUD" and provenance.get(
            "ground_classification_filter"
        ) != "Classification[2:2]":
            raise RuntimeError("POINT_CLOUD_GROUND_FILTER_UNVERIFIED")
        row_spacing_m = self._verified_resolution_value(
            configuration.get("sulcation", {}).get("row_spacing_m"),
            "row spacing",
        )
        if "SULCATION_E0" in selected and effective_resolution_m > row_spacing_m + 1e-9:
            raise ValueError("TERRAIN_RESOLUTION_TOO_COARSE_FOR_ROWS")
        if selected & {"CF0_CONTINUOUS", "C1_EMBEDDED_SCREENING"} and effective_resolution_m > row_spacing_m + 1e-9:
            raise ValueError("TERRAIN_RESOLUTION_TOO_COARSE_FOR_CF0")

    def _declared_path(self, record: Any) -> Path:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str):
            raise RuntimeError("manifest output record has no path")
        path = Path(record["path"])
        return path.resolve() if path.is_absolute() else (self.workspace_root / path).resolve()

    @staticmethod
    def _choose_asset(assets: list[dict[str, Any]]) -> dict[str, Any] | None:
        return max(assets, key=lambda item: (item.get("created_at", ""), item["id"])) if assets else None

    def _select_elevation_source(
        self,
        by_role: dict[str, list[dict[str, Any]]],
        preference: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        terrain = self._choose_asset(by_role.get("DTM_DEM", []))
        point_cloud = self._choose_asset(by_role.get("POINT_CLOUD", []))
        if preference == "POINT_CLOUD" and point_cloud is not None:
            return None, point_cloud
        if preference == "DTM_DEM" and terrain is not None:
            return terrain, None
        return (terrain, None) if terrain is not None else (None, point_cloud)

    def _prepare_boundary(self, assets: list[dict[str, Any]], destination: Path) -> Path:
        destination.mkdir(parents=True, exist_ok=False)
        direct = [item for item in assets if item["extension"] in {".gpkg", ".geojson", ".json"}]
        if direct:
            source = Path(self._choose_asset(direct)["path"])
            target = destination / safe_filename(self._choose_asset(direct)["original_filename"])
            shutil.copy2(source, target)
            return target

        archives = [item for item in assets if item["extension"] == ".zip"]
        if archives:
            source = Path(self._choose_asset(archives)["path"])
            extracted = destination / "archive"
            extracted.mkdir()
            with zipfile.ZipFile(source) as archive:
                for member in archive.infolist():
                    target = (extracted / member.filename).resolve()
                    if extracted.resolve() not in target.parents:
                        raise RuntimeError("boundary ZIP contains an unsafe path")
                archive.extractall(extracted)
            candidates = sorted(extracted.rglob("*.gpkg")) + sorted(extracted.rglob("*.geojson"))
            if not candidates:
                candidates = [
                    path for path in sorted(extracted.rglob("*.shp"))
                    if path.with_suffix(".shx").is_file() and path.with_suffix(".dbf").is_file()
                ]
            if len(candidates) != 1:
                raise ValueError("boundary ZIP must contain exactly one supported vector dataset")
            return candidates[0]

        by_stem: dict[str, list[dict[str, Any]]] = {}
        for asset in assets:
            stem = Path(asset.get("original_filename", "")).stem.casefold()
            by_stem.setdefault(stem, []).append(asset)
        complete = [
            group for group in by_stem.values()
            if {item["extension"] for item in group} >= {".shp", ".shx", ".dbf"}
        ]
        if len(complete) != 1:
            raise ValueError("upload one complete and unambiguous SHP+SHX+DBF boundary set")
        shapefile = None
        for asset in complete[0]:
            target = destination / safe_filename(asset["original_filename"])
            shutil.copy2(Path(asset["path"]), target)
            if asset["extension"] == ".shp":
                shapefile = target
        if shapefile is None:
            raise ValueError("boundary SHP is missing")
        return shapefile

    @staticmethod
    def _qgis_python_launcher() -> Path:
        configured = os.getenv("TERRAFLUX_QGIS_PYTHON")
        candidates = [
            Path(configured) if configured else None,
            Path(r"C:\Program Files\QGIS 3.32.1\bin\python-qgis.bat"),
        ]
        for candidate in candidates:
            if candidate and candidate.is_file():
                return candidate.resolve()
        discovered = shutil.which("python-qgis.bat") or shutil.which("python-qgis")
        if discovered:
            return Path(discovered).resolve()
        raise RuntimeError("QGIS Python launcher is unavailable; configure TERRAFLUX_QGIS_PYTHON")


def _media_type(path: Path) -> str:
    return {
        ".json": "application/json",
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".gpkg": "application/geopackage+sqlite3",
        ".csv": "text/csv",
    }.get(path.suffix.lower(), "application/octet-stream")
