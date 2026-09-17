from __future__ import annotations

import json
import math
import os
import queue
import shutil
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
from .process_runner import EngineCancelled, run_engine


class JobRunner:
    """In-process worker whose callable registry is the complete execution whitelist."""

    def __init__(self, store: LocalStore, workspace_root: Path):
        self.store = store
        self.workspace_root = workspace_root.resolve()
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
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
        self._stopping.clear()
        self._queue = queue.Queue()
        self._thread = threading.Thread(target=self._work, name="terraflux-local-worker", daemon=True)
        self._thread.start()
        for run_id in queued_run_ids:
            self._queue.put(run_id)

    def stop(self) -> None:
        self._stopping.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=10)

    def submit(self, run_id: str) -> None:
        if not self._thread or not self._thread.is_alive():
            self.start()
            return  # start() recovered every persisted QUEUED run, including this one.
        self._queue.put(run_id)

    def _log(self, run_id: str, level: str, message: str) -> None:
        self.store.append_run_log(run_id, {"at": utc_now(), "level": level, "message": message[:2000]})

    def _set(self, run_id: str, **values: Any) -> None:
        values["updated_at"] = utc_now()
        self.store.update("runs", run_id, values)

    def _work(self) -> None:
        while True:
            run_id = self._queue.get()
            if run_id is None or self._stopping.is_set():
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
        except EngineCancelled as exc:
            self._discard_run_publications(run_id)
            self._set(run_id, status="CANCELLED", finished_at=utc_now())
            self._log(run_id, "WARN", str(exc))
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
        spatial_metadata: dict[str, Any] | None = None,
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
        if spatial_metadata is not None:
            artifact["spatial_metadata"] = spatial_metadata
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
        code_root = str(Path(__file__).resolve().parents[2])
        if code_root not in sys.path:
            sys.path.insert(0, code_root)
        from scripts.pcx1_runoff import (
            RainfallInterval,
            calculate_pcx1_rainfall_excess,
            validate_pcx1_release,
        )
        from scripts.pcx2_hydrograph import (
            calculate_pcx2_triangular_hydrograph,
            validate_pcx2_release,
        )
        from scripts.preliminary_reach_routing import (
            route_hydrographs_by_lag,
            validate_routing_release,
        )
        from scripts.preliminary_section_capacity import check_reach_capacities, validate_capacity_release
        from scripts.preliminary_backwater_profile import calculate_standard_step_profiles, validate_profile_release
        from scripts.preliminary_overflow_path_screening import screen_overflow_paths, validate_overflow_path_release
        from scripts.overflow_geojson import export_overflow_geojson

        project = self.store.get("projects", run["project_id"])
        if project is None:
            raise RuntimeError("project disappeared")
        request = self.store.get("generation_requests", run.get("request_id") or "")
        if request is None or request["project_id"] != project["id"]:
            raise ValueError("project_hydrology_screening requires an immutable request from the same project")
        self._verify_request_snapshot(request)
        requested_products = set(request.get("product_ids", []))
        if requested_products not in (
            {"PCX1_RUNOFF_SCREENING"},
            {"PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING"},
            {"PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING", "PCX3_REACH_ROUTING_SCREENING"},
            {"PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING", "PCX3_REACH_ROUTING_SCREENING", "PCX4_SECTION_CAPACITY_SCREENING"},
            {"PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING", "PCX3_REACH_ROUTING_SCREENING", "PCX4_SECTION_CAPACITY_SCREENING", "PCX5_WATER_SURFACE_PROFILE_SCREENING"},
            {"PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING", "PCX3_REACH_ROUTING_SCREENING", "PCX4_SECTION_CAPACITY_SCREENING", "PCX5_WATER_SURFACE_PROFILE_SCREENING", "PCX6_OVERFLOW_PATH_SCREENING"},
        ):
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
                    "PCX_HYDROGRAPH_NOT_INCLUDED_IN_THIS_PRODUCT",
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
        manifest_path = output_dir / "resultado_chuva_escoamento.json"
        manifest_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        csv_path = output_dir / "serie_chuva_escoamento.csv"
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
        hydrograph_result: dict[str, Any] | None = None
        routing_result: dict[str, Any] | None = None
        capacity_result: dict[str, Any] | None = None
        profile_result: dict[str, Any] | None = None
        overflow_path_result: dict[str, Any] | None = None
        if "PCX2_HYDROGRAPH_SCREENING" in requested_products:
            result["blocker_codes"] = [
                code for code in result["blocker_codes"]
                if code != "PCX_HYDROGRAPH_NOT_INCLUDED_IN_THIS_PRODUCT"
            ]
            if configuration.get("hydrograph_enabled") is not True:
                raise ValueError("HYDROGRAPH_CONFIGURATION_NOT_ENABLED")
            lag_minutes = configuration.get("catchment_lag_minutes")
            if lag_minutes is None:
                raise ValueError("CATCHMENT_LAG_REQUIRED")
            hydrograph_result = calculate_pcx2_triangular_hydrograph(
                result["intervals"],
                catchment_area_ha=result["catchment_area_ha"],
                lag_time_s=float(lag_minutes) * 60.0,
                output_step_s=float(configuration.get("hydrograph_step_minutes", 1.0)) * 60.0,
                base_to_peak_time_ratio=float(configuration.get("triangle_base_to_peak_ratio", 2.67)),
            )
            validate_pcx2_release(hydrograph_result)
            hydrograph_result.update(
                {
                    "schema_version": "1.0.0",
                    "manifest_type": "PCX2_PRELIMINARY_HYDROGRAPH_RESULT",
                    "project_id": project["id"],
                    "run_id": run["id"],
                    "request_ref": {"id": request["id"], "sha256": request["sha256"]},
                    "source_rainfall_excess_release": result["release"],
                    "stage_status": "PRELIMINARY_HYDROGRAPH_WITH_EXPLICIT_BLOCKERS",
                    "blocker_codes": [
                        "HYDROGRAPH_METHOD_PROJECT_APPROVAL_REQUIRED",
                        "CATCHMENT_LAG_PROJECT_EVIDENCE_REQUIRED",
                        "CHANNEL_ROUTING_NOT_IMPLEMENTED",
                        "STRUCTURE_ROUTING_NOT_IMPLEMENTED",
                        "HYDRAULIC_CAPACITY_NOT_EVALUATED",
                        "RECEIVER_NOT_APPROVED",
                        "GUIDANCE_NOT_AUTHORIZED",
                    ],
                }
            )
            if configuration.get("parameter_evidence_state") != "PROJECT_EVIDENCE":
                hydrograph_result["blocker_codes"].append("HYDROLOGY_PROJECT_EVIDENCE_INCOMPLETE")
            hydro_json = output_dir / "hidrograma_preliminar.json"
            hydro_json.write_text(json.dumps(hydrograph_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            hydro_csv = output_dir / "hidrograma_preliminar.csv"
            hydro_csv.write_text(
                "tempo_min,vazao_m3_s,volume_acumulado_m3\n"
                + "\n".join(
                    f"{row['time_s'] / 60.0},{row['flow_m3_s']},{row['cumulative_volume_m3']}"
                    for row in hydrograph_result["hydrograph"]
                )
                + "\n",
                encoding="utf-8",
            )
            plot_path = output_dir / "grafico_hidrograma_preliminar.png"
            self._plot_hydrograph(hydrograph_result, plot_path)
            for path in (hydro_json, hydro_csv, plot_path):
                self._artifact(run, path, "PCX2_HYDROGRAPH_SCREENING", "GENERATED_FROM_CLIENT_CONFIGURATION")
        if "PCX3_REACH_ROUTING_SCREENING" in requested_products:
            if hydrograph_result is None:
                raise ValueError("HYDROGRAPH_DEPENDENCY_REQUIRED")
            if configuration.get("routing_enabled") is not True:
                raise ValueError("ROUTING_CONFIGURATION_NOT_ENABLED")
            source_node = str(configuration.get("routing_source_node_id") or "").strip()
            reach_values = configuration.get("routing_reaches") or []
            if not source_node or not reach_values:
                raise ValueError("ROUTING_NETWORK_REQUIRED")
            reaches = [
                {
                    "id": item["id"],
                    "upstream_node_id": item["upstream_node_id"],
                    "downstream_node_id": item["downstream_node_id"],
                    "travel_time_s": float(item["travel_time_minutes"]) * 60.0,
                }
                for item in reach_values
            ]
            routing_result = route_hydrographs_by_lag(
                reaches,
                {source_node: hydrograph_result["hydrograph"]},
            )
            validate_routing_release(routing_result)
            routing_result.update(
                {
                    "schema_version": "1.0.0",
                    "manifest_type": "PRELIMINARY_REACH_ROUTING_RESULT",
                    "project_id": project["id"],
                    "run_id": run["id"],
                    "request_ref": {"id": request["id"], "sha256": request["sha256"]},
                    "source_hydrograph_release": hydrograph_result["release"],
                    "source_node_id": source_node,
                    "stage_status": "PRELIMINARY_ROUTING_WITH_EXPLICIT_BLOCKERS",
                    "blocker_codes": [
                        "TRAVEL_TIMES_REQUIRE_PROJECT_EVIDENCE",
                        "ATTENUATION_NOT_EVALUATED",
                        "BACKWATER_NOT_EVALUATED",
                        "HYDRAULIC_CAPACITY_NOT_EVALUATED",
                        "FAILURE_PATH_NOT_EVALUATED",
                        "RECEIVER_NOT_APPROVED",
                        "GUIDANCE_NOT_AUTHORIZED",
                    ],
                }
            )
            routing_dir = run_dir / "products" / "preliminary_reach_routing"
            routing_dir.mkdir(parents=True, exist_ok=False)
            routing_json = routing_dir / "propagacao_preliminar_rede.json"
            routing_json.write_text(json.dumps(routing_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            routing_csv = routing_dir / "picos_por_trecho.csv"
            routing_csv.write_text(
                "trecho,no_entrada,no_saida,tempo_viagem_min,vazao_maxima_m3_s,tempo_ate_pico_min,status\n"
                + "\n".join(
                    f"{item['id']},{item['upstream_node_id']},{item['downstream_node_id']},"
                    f"{item['travel_time_s'] / 60.0},{item['peak_flow_m3_s']},"
                    f"{item['time_to_peak_s'] / 60.0},{item['status']}"
                    for item in routing_result["reaches"]
                )
                + "\n",
                encoding="utf-8",
            )
            for path in (routing_json, routing_csv):
                self._artifact(run, path, "PCX3_REACH_ROUTING_SCREENING", "GENERATED_FROM_CLIENT_CONFIGURATION")
        if "PCX4_SECTION_CAPACITY_SCREENING" in requested_products:
            if routing_result is None:
                raise ValueError("ROUTING_DEPENDENCY_REQUIRED")
            if configuration.get("capacity_enabled") is not True:
                raise ValueError("SECTION_CONFIGURATION_REQUIRED")
            section_values = configuration.get("reach_sections") or []
            routed_ids = {item["id"] for item in routing_result["reaches"]}
            section_keys = {(item["id"], item.get("condition_state", "CURRENT")) for item in section_values}
            if len(section_keys) != len(section_values):
                raise ValueError("SECTION_STATES_MUST_BE_UNIQUE_PER_REACH")
            if {item["id"] for item in section_values} != routed_ids:
                raise ValueError("SECTION_IDS_MUST_MATCH_ROUTED_REACH_IDS")
            capacity_inputs = []
            for routed in routing_result["reaches"]:
                for section in (item for item in section_values if item["id"] == routed["id"]):
                    capacity_inputs.append({
                        "id": routed["id"],
                        "peak_flow_m3_s": routed["peak_flow_m3_s"],
                        **{key: section.get(key) for key in (
                            "condition_state", "bottom_width_m", "side_slope_h_to_v", "slope_m_m",
                            "manning_n", "maximum_flow_depth_m", "bankfull_depth_m", "required_freeboard_m",
                            "overflow_path_state", "overflow_receiver_id", "maximum_admissible_velocity_m_s",
                            "downstream_water_depth_m", "downstream_velocity_m_s", "transition_loss_coefficient",
                            "downstream_boundary_source_id", "downstream_boundary_evidence_state",
                            "maximum_admissible_shear_pa", "stability_limit_source_id",
                            "stability_limit_evidence_state",
                        )},
                    })
            capacity_result = check_reach_capacities(capacity_inputs)
            validate_capacity_release(capacity_result)
            result["blocker_codes"] = [
                code for code in result["blocker_codes"]
                if code != "PCX_SECTION_CAPACITY_NOT_EVALUATED"
            ]
            capacity_result.update({
                "schema_version": "1.0.0",
                "manifest_type": "PRELIMINARY_SECTION_CAPACITY_RESULT",
                "project_id": project["id"],
                "run_id": run["id"],
                "request_ref": {"id": request["id"], "sha256": request["sha256"]},
                "source_routing_release": routing_result["release"],
                "stage_status": "PRELIMINARY_CAPACITY_WITH_EXPLICIT_BLOCKERS",
                "blocker_codes": ["UNIFORM_FLOW_ASSUMPTION", "BACKWATER_PROFILE_NOT_EVALUATED", "TRANSITION_ENVELOPE_ONLY", "EROSION_SAFETY_NOT_APPROVED", "OVERFLOW_PATH_NOT_SIMULATED", "RECEIVER_NOT_APPROVED", "GUIDANCE_NOT_AUTHORIZED"],
            })
            capacity_dir = run_dir / "products" / "preliminary_section_capacity"
            capacity_dir.mkdir(parents=True, exist_ok=False)
            capacity_json = capacity_dir / "verificacao_preliminar_capacidade.json"
            capacity_json.write_text(json.dumps(capacity_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            capacity_csv = capacity_dir / "capacidade_por_trecho.csv"
            capacity_csv.write_text(
                "trecho,estado_secao,vazao_maxima_m3_s,capacidade_m3_s,ocupacao,profundidade_normal_m,profundidade_controle_m,lamina_jusante_m,perda_transicao_m,estado_controle_jusante,folga_profundidade_m,profundidade_margem_m,borda_livre_requerida_m,borda_livre_no_pico_m,estado_borda_livre,caminho_excedente,receptor_excedente,velocidade_m_s,limite_velocidade_m_s,tensao_pa,limite_tensao_pa,estado_capacidade,estado_estabilidade\n"
                + "\n".join(f"{item['id']},{item['condition_state']},{item['peak_flow_m3_s']},{item['capacity_m3_s']},{item['capacity_ratio']},{item['required_normal_depth_m']},{item['screening_control_depth_m']},{item['downstream_water_depth_m']},{item['transition_head_loss_m']},{item['preliminary_downstream_control_status']},{item['depth_margin_m']},{item['bankfull_depth_m']},{item['required_freeboard_m']},{item['actual_freeboard_at_peak_m']},{item['preliminary_freeboard_status']},{item['overflow_path_state']},{item['overflow_receiver_id']},{item['velocity_at_peak_m_s']},{item['maximum_admissible_velocity_m_s']},{item['boundary_shear_at_peak_pa']},{item['maximum_admissible_shear_pa']},{item['preliminary_capacity_status']},{item['preliminary_stability_status']}" for item in capacity_result["reaches"])
                + "\n",
                encoding="utf-8",
            )
            for path in (capacity_json, capacity_csv):
                self._artifact(run, path, "PCX4_SECTION_CAPACITY_SCREENING", "GENERATED_FROM_CLIENT_CONFIGURATION")
        if "PCX5_WATER_SURFACE_PROFILE_SCREENING" in requested_products:
            if capacity_result is None or routing_result is None:
                raise ValueError("SECTION_CAPACITY_DEPENDENCY_REQUIRED")
            if configuration.get("profile_enabled") is not True:
                raise ValueError("PROFILE_CONFIGURATION_NOT_ENABLED")
            routed_by_id = {item["id"]: item for item in routing_result["reaches"]}
            configured_reaches = {item["id"]: item for item in configuration.get("routing_reaches") or []}
            profile_inputs = []
            for section in configuration.get("reach_sections") or []:
                reach = configured_reaches.get(section["id"])
                routed = routed_by_id.get(section["id"])
                if not reach or reach.get("length_m") is None or not routed or section.get("downstream_water_depth_m") is None:
                    raise ValueError("PROFILE_LENGTH_AND_DOWNSTREAM_DEPTH_REQUIRED")
                profile_inputs.append({
                    "id": section["id"],
                    "condition_state": section.get("condition_state", "CURRENT"),
                    "peak_flow_m3_s": routed["peak_flow_m3_s"],
                    "length_m": reach["length_m"],
                    "bottom_width_m": section["bottom_width_m"],
                    "side_slope_h_to_v": section["side_slope_h_to_v"],
                    "slope_m_m": section["slope_m_m"],
                    "manning_n": section["manning_n"],
                    "downstream_water_depth_m": section["downstream_water_depth_m"],
                    "profile_step_count": configuration.get("profile_step_count", 20),
                })
            profile_result = calculate_standard_step_profiles(profile_inputs)
            validate_profile_release(profile_result)
            profile_result.update({
                "schema_version": "1.0.0",
                "manifest_type": "PRELIMINARY_WATER_SURFACE_PROFILE_RESULT",
                "project_id": project["id"],
                "run_id": run["id"],
                "request_ref": {"id": request["id"], "sha256": request["sha256"]},
                "source_capacity_release": capacity_result["release"],
                "stage_status": "PRELIMINARY_PROFILE_WITH_EXPLICIT_BLOCKERS",
                "blocker_codes": ["SUBCRITICAL_STEADY_ONLY", "PRISMATIC_SECTION_ONLY", "NO_STRUCTURES", "NO_UNSTEADY_FLOW", "RECEIVER_NOT_APPROVED", "GUIDANCE_NOT_AUTHORIZED"],
            })
            profile_dir = run_dir / "products" / "preliminary_water_surface_profile"
            profile_dir.mkdir(parents=True, exist_ok=False)
            profile_json = profile_dir / "perfil_preliminar_lamina.json"
            profile_json.write_text(json.dumps(profile_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            profile_csv = profile_dir / "perfil_preliminar_lamina.csv"
            profile_csv.write_text(
                "trecho,estado_secao,distancia_a_montante_m,cota_fundo_relativa_m,profundidade_m,cota_lamina_relativa_m,velocidade_m_s,froude,declividade_atrito_m_m\n"
                + "\n".join(
                    f"{profile['id']},{profile['condition_state']},{point['station_from_downstream_m']},{point['bed_elevation_relative_m']},{point['water_depth_m']},{point['water_surface_elevation_relative_m']},{point['velocity_m_s']},{point['froude_number']},{point['friction_slope_m_m']}"
                    for profile in profile_result["profiles"] for point in profile["profile"]
                ) + "\n",
                encoding="utf-8",
            )
            profile_plot = profile_dir / "grafico_perfil_preliminar_lamina.png"
            self._plot_water_profiles(profile_result, profile_plot)
            for path in (profile_json, profile_csv, profile_plot):
                self._artifact(run, path, "PCX5_WATER_SURFACE_PROFILE_SCREENING", "GENERATED_FROM_CLIENT_CONFIGURATION")
        if "PCX6_OVERFLOW_PATH_SCREENING" in requested_products:
            if profile_result is None or capacity_result is None:
                raise ValueError("WATER_PROFILE_DEPENDENCY_REQUIRED")
            if configuration.get("overflow_path_screening_enabled") is not True:
                raise ValueError("OVERFLOW_PATH_CONFIGURATION_NOT_ENABLED")
            paths = configuration.get("overflow_paths") or []
            receivers = configuration.get("spatial_receivers") or []
            if not paths or not receivers:
                raise ValueError("OVERFLOW_PATH_GEOMETRY_REQUIRED")
            declared_destinations = {
                (item["id"], item["overflow_receiver_id"])
                for item in capacity_result["reaches"] if item.get("overflow_receiver_id")
            }
            if any((item["reach_id"], item["receiver_id"]) not in declared_destinations for item in paths):
                raise ValueError("OVERFLOW_PATH_DESTINATION_MUST_MATCH_SECTION_DECLARATION")
            overflow_path_result = screen_overflow_paths(
                paths, receivers, configuration.get("spatial_barriers") or [],
                endpoint_tolerance_m=float(configuration.get("overflow_path_endpoint_tolerance_m", 2.0)),
                elevation_tolerance_m=float(configuration.get("overflow_path_elevation_tolerance_m", 0.05)),
            )
            validate_overflow_path_release(overflow_path_result)
            source_crs = request.get("spatial_reference_snapshot", {}).get("horizontal_crs")
            geographic_paths = export_overflow_geojson(overflow_path_result["paths"], source_crs)
            overflow_path_result.update({
                "source_horizontal_crs": source_crs,
                "source_vertical_reference": "UNSPECIFIED_SOURCE_DATUM",
                "schema_version": "1.0.0",
                "manifest_type": "PRELIMINARY_OVERFLOW_PATH_SCREENING_RESULT",
                "project_id": project["id"], "run_id": run["id"],
                "request_ref": {"id": request["id"], "sha256": request["sha256"]},
                "source_profile_release": profile_result["release"],
                "stage_status": "SPATIAL_SCREENING_WITH_EXPLICIT_BLOCKERS",
                "blocker_codes": ["DECLARED_GEOMETRY_ONLY", "OVERFLOW_NOT_ROUTED", "RECEIVER_CAPACITY_NOT_EVALUATED", "RECEIVER_NOT_APPROVED", "GUIDANCE_NOT_AUTHORIZED"],
            })
            overflow_dir = run_dir / "products" / "preliminary_overflow_paths"
            overflow_dir.mkdir(parents=True, exist_ok=False)
            overflow_json = overflow_dir / "verificacao_caminhos_extravasamento.json"
            overflow_json.write_text(json.dumps(overflow_path_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            overflow_csv = overflow_dir / "caminhos_extravasamento.csv"
            overflow_csv.write_text(
                "caminho,trecho,receptor,comprimento_m,queda_m,menor_declividade_m_m,maior_subida_m,conexao_receptor,conflitos_barreira,status\n"
                + "\n".join(
                    f"{item['id']},{item['reach_id']},{item['receiver_id']},{item['length_m']},{item['elevation_drop_m']},{item['minimum_segment_slope_m_m']},{item['maximum_adverse_rise_m']},{item['receiver_connection_status']},{len(item['crossed_barriers'])},{item['screening_status']}"
                    for item in overflow_path_result["paths"]
                ) + "\n", encoding="utf-8",
            )
            overflow_geojson = overflow_dir / "caminhos_extravasamento.geojson"
            overflow_geojson.write_text(json.dumps(geographic_paths, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            overflow_plot = overflow_dir / "mapa_caminhos_extravasamento.png"
            self._plot_overflow_paths(overflow_path_result, receivers, configuration.get("spatial_barriers") or [], overflow_plot)
            for path in (overflow_json, overflow_csv, overflow_geojson, overflow_plot):
                self._artifact(
                    run, path, "PCX6_OVERFLOW_PATH_SCREENING", "GENERATED_FROM_CLIENT_CONFIGURATION",
                    spatial_metadata={
                        "label": "Caminhos de extravasamento", "format": "RFC7946",
                        "horizontal_crs": "OGC:CRS84", "geometry_type": "LineString",
                        "geometry_dimensions": 2, "source_horizontal_crs": source_crs,
                        "vertical_reference": "UNSPECIFIED_SOURCE_DATUM",
                    } if path == overflow_geojson else None,
                )
        self._set(
            run["id"],
            progress=95,
            stage="PUBLISHED_PRELIMINARY_RAINFALL_RESPONSE",
            result="PASS_PRELIMINARY_RAINFALL_RESPONSE_NOT_DESIGN",
            result_summary={
                "total_rainfall_mm": result["total_rainfall_mm"],
                "total_rainfall_excess_mm": result["total_rainfall_excess_mm"],
                "total_rainfall_excess_volume_m3": result["total_rainfall_excess_volume_m3"],
                "runoff_coefficient_event": result["runoff_coefficient_event"],
                "blocker_codes": result["blocker_codes"],
                "peak_flow_m3_s": hydrograph_result["peak_flow_m3_s"] if hydrograph_result else None,
                "time_to_peak_minutes": hydrograph_result["time_to_peak_s"] / 60.0 if hydrograph_result else None,
                "hydrograph_mass_balance_status": hydrograph_result["mass_balance_status"] if hydrograph_result else None,
                "routing_mass_balance_status": routing_result["mass_balance_status"] if routing_result else None,
                "routed_reach_count": routing_result["reach_count"] if routing_result else None,
                "routing_outlet_count": len(routing_result["outlet_node_ids"]) if routing_result else None,
                "capacity_within_count": capacity_result["within_capacity_count"] if capacity_result else None,
                "capacity_exceeded_count": capacity_result["exceeded_capacity_count"] if capacity_result else None,
                "stability_evaluated_count": capacity_result["stability_evaluated_count"] if capacity_result else None,
                "stability_exceeded_count": capacity_result["stability_exceeded_count"] if capacity_result else None,
                "section_condition_counts": capacity_result["condition_counts"] if capacity_result else None,
                "freeboard_evaluated_count": capacity_result["freeboard_evaluated_count"] if capacity_result else None,
                "freeboard_shortfall_count": capacity_result["freeboard_shortfall_count"] if capacity_result else None,
                "overtopping_count": capacity_result["overtopping_count"] if capacity_result else None,
                "overflow_path_declared_count": capacity_result["overflow_path_declared_count"] if capacity_result else None,
                "downstream_evaluated_count": capacity_result["downstream_evaluated_count"] if capacity_result else None,
                "downstream_controlled_count": capacity_result["downstream_controlled_count"] if capacity_result else None,
                "water_profile_count": profile_result["profile_count"] if profile_result else None,
                "water_profile_maximum_depth_m": max((item["maximum_water_depth_m"] for item in profile_result["profiles"]), default=None) if profile_result else None,
                "overflow_path_screened_count": overflow_path_result["path_count"] if overflow_path_result else None,
                "overflow_path_clear_count": overflow_path_result["screened_clear_count"] if overflow_path_result else None,
                "overflow_path_barrier_conflict_count": overflow_path_result["barrier_conflict_count"] if overflow_path_result else None,
                "guidance_authorized": False,
            },
        )
        message = "Resposta da chuva publicada como estudo preliminar."
        if hydrograph_result is None:
            message += " Hidrograma nao solicitado."
        elif capacity_result is None:
            message += " Capacidade das secoes nao avaliada."
        else:
            message += " Capacidade das secoes verificada somente por escoamento uniforme."
        if profile_result is not None:
            message += " Perfil permanente subcritico calculado por passo padrao."
        if overflow_path_result is not None:
            message += " Caminhos de extravasamento verificados espacialmente."
        self._log(run["id"], "WARN", message)

    @staticmethod
    def _plot_hydrograph(result: dict[str, Any], output_path: Path) -> None:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        times = [row["time_s"] / 60.0 for row in result["hydrograph"]]
        flows = [row["flow_m3_s"] for row in result["hydrograph"]]
        figure, axis = plt.subplots(figsize=(10, 4.8), dpi=150)
        axis.plot(times, flows, color="#176b4d", linewidth=2.2)
        axis.fill_between(times, flows, color="#9dcdb5", alpha=0.45)
        axis.scatter(
            [result["time_to_peak_s"] / 60.0],
            [result["peak_flow_m3_s"]],
            color="#b45309",
            s=35,
            zorder=3,
        )
        axis.set_title("Hidrograma preliminar do evento")
        axis.set_xlabel("Tempo (min)")
        axis.set_ylabel("Vazao estimada (m3/s)")
        axis.grid(True, color="#d8ded9", linewidth=0.7)
        figure.tight_layout()
        figure.savefig(output_path, bbox_inches="tight")
        plt.close(figure)

    @staticmethod
    def _plot_water_profiles(result: dict[str, Any], output_path: Path) -> None:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        figure, axis = plt.subplots(figsize=(10, 5.2), dpi=150)
        condition_labels = {"NEW": "Nova", "CURRENT": "Atual", "DEGRADED": "Degradada"}
        plotted_beds = set()
        for profile in result["profiles"]:
            points = profile["profile"]
            distances = [point["station_from_downstream_m"] for point in points]
            water = [point["water_surface_elevation_relative_m"] for point in points]
            reach_label = profile["id"].replace("_", " ")
            axis.plot(distances, water, linewidth=2.0, label=f"{reach_label} - {condition_labels.get(profile['condition_state'], profile['condition_state'])}")
            if profile["id"] not in plotted_beds:
                axis.plot(
                    distances,
                    [point["bed_elevation_relative_m"] for point in points],
                    linewidth=1.2, linestyle="--", label=f"Fundo {reach_label}",
                )
                plotted_beds.add(profile["id"])
        axis.set_title("Perfil preliminar da lamina por passo padrao")
        axis.set_xlabel("Distancia a montante da saida (m)")
        axis.set_ylabel("Cota relativa (m)")
        axis.grid(True, color="#d8ded9", linewidth=0.7)
        axis.legend(fontsize=8)
        figure.tight_layout()
        figure.savefig(output_path, bbox_inches="tight")
        plt.close(figure)

    @staticmethod
    def _plot_overflow_paths(result: dict[str, Any], receivers: list[dict[str, Any]], barriers: list[dict[str, Any]], output_path: Path) -> None:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        figure, axis = plt.subplots(figsize=(9, 6), dpi=150)
        for barrier in barriers:
            coordinates = barrier["coordinates"]
            axis.plot([p[0] for p in coordinates], [p[1] for p in coordinates], color="#a61b1b", linewidth=2.2, label="Barreira" if "Barreira" not in axis.get_legend_handles_labels()[1] else None)
        for path in result["paths"]:
            coordinates = path["coordinates"]
            clear = path["screening_status"] == "SCREENED_CLEAR"
            axis.plot([p[0] for p in coordinates], [p[1] for p in coordinates], color="#19734a" if clear else "#d97706", linewidth=2.5, marker="o", markersize=3, label=f"{path['id']} - {'sem conflito detectado' if clear else 'revisar'}")
        for receiver in receivers:
            x, y, _ = receiver["coordinate"]
            axis.scatter([x], [y], marker="*", s=110, color="#1769aa", zorder=5)
            axis.annotate(f"Receptor {receiver['id']}", (x, y), xytext=(5, 5), textcoords="offset points", fontsize=8)
        axis.set_title("Triagem dos caminhos de extravasamento declarados")
        axis.set_xlabel("Coordenada X (m)"); axis.set_ylabel("Coordenada Y (m)")
        axis.set_aspect("equal", adjustable="datalim"); axis.grid(True, color="#d8ded9", linewidth=0.7)
        axis.legend(fontsize=8); figure.tight_layout(); figure.savefig(output_path, bbox_inches="tight"); plt.close(figure)

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
        reference = request.get("spatial_reference_snapshot", {"horizontal_crs": project.get("crs")})
        if reference.get("horizontal_crs"):
            command.extend(["--crs", reference["horizontal_crs"]])
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
            self._artifact(
                run, path, "TOPOGRAPHY_E0", "GENERATED_FROM_CLIENT_DATA",
                spatial_metadata={
                    "label": "Terreno da rodada", "format": "TERRAIN_INSPECTION_MESH",
                    "horizontal_crs": "OGC:CRS84", "geometry_type": "Mesh",
                    "geometry_dimensions": 3, "vertical_reference": "UNSPECIFIED_SOURCE_DATUM",
                    "inspection_only": True,
                } if path.name == "terrain_inspection_mesh.json" else {
                    "label": "Curvas de nivel do terreno", "format": "RFC7946",
                    "horizontal_crs": "OGC:CRS84", "geometry_type": "LineString",
                    "geometry_dimensions": 2, "vertical_reference": "UNSPECIFIED_SOURCE_DATUM",
                    "inspection_only": True, "elevation_policy": "SOURCE_CONTOUR_HEIGHTS",
                } if path.name == "contours_inspection.geojson" else {
                    "label": "Limites dos talhoes", "format": "RFC7946",
                    "horizontal_crs": "OGC:CRS84", "geometry_type": "LineString",
                    "geometry_dimensions": 2, "vertical_reference": "UNSPECIFIED_SOURCE_DATUM",
                    "inspection_only": True, "elevation_policy": "SOURCE_TERRAIN_SAMPLE",
                } if path.name == "field_boundaries_inspection.geojson" else None,
            )
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
            "--maneuver-time-s", str(sulcation.get("maneuver_time_s", 38.5)),
            "--max-cross-slope-pct", str(sulcation.get("max_cross_slope_pct", 12.0)),
            "--terrain-smoothing-sigma-m", str(sulcation.get("terrain_smoothing_sigma_m", 4.0)),
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
            published += self._publish_row_web(run, e0_gpkg, topography_dir / "dtm.tif", "SULCATION_E0")
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
            published += self._publish_row_web(run, cf0_gpkg, topography_dir / "dtm.tif", "CF0_CONTINUOUS")
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
        run_engine(
            command, self.workspace_root, label,
            cancelled=lambda: self._stopping.is_set() or bool((self.store.get("runs", run["id"]) or {}).get("cancel_requested")),
            log=lambda level, message: self._log(run["id"], level, message),
            timeout_seconds=float(os.getenv("TERRAFLUX_ENGINE_TIMEOUT_S", "21600")),
        )

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

    def _publish_row_web(self, run, source, terrain, product_id):
        output_dir = source.parent / "web"
        self._run_process(run, [str(self._qgis_python_launcher()),
                               str(self.workspace_root / "scripts" / "rows_web.py"),
                               "--source", str(source), "--terrain", str(terrain),
                               "--output-dir", str(output_dir)], "row inspection layers")
        manifest_path = output_dir / "rows_web_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._artifact(run, manifest_path, product_id, "GENERATED_FROM_CLIENT_DATA")
        if manifest.get("status") == "UNAVAILABLE":
            self._log(run["id"], "WARNING", f"Mapa de linhas indisponivel: {manifest.get('reason')}")
            return 1
        if manifest.get("status") != "AVAILABLE" or manifest.get("source_sha256") != file_sha256(source) or manifest.get("terrain_sha256") != file_sha256(terrain):
            raise RuntimeError("row inspection source lineage mismatch")
        for record in manifest["outputs"]:
            path = self._declared_path(record)
            self._verify_manifest_file(record, path, output_dir, "row inspection")
            diagnostic = record["inspection_status"] == "DIAGNOSTIC"
            label = "Linhas de diagnostico" if diagnostic else "Sulcacao preliminar"
            if record["part_count"] > 1:
                label += f" ({record['part']}/{record['part_count']})"
            self._artifact(run, path, product_id, "GENERATED_FROM_CLIENT_DATA", spatial_metadata={
                "label": label, "format": "RFC7946", "horizontal_crs": "OGC:CRS84",
                "geometry_type": "LineString", "geometry_dimensions": 2,
                "vertical_reference": "UNSPECIFIED_SOURCE_DATUM", "elevation_policy": "SOURCE_ROW_HEIGHTS",
                "scenario_key": record["scenario_key"], "scenario_name": record["scenario_name"],
                "inspection_only": True, "default_visible": not diagnostic,
                "color": "#c64e59" if diagnostic else "#147547",
            })
        return 1 + len(manifest["outputs"])

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
