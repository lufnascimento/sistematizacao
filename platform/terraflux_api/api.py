from __future__ import annotations

import json
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .catalog import ENGINE_CATALOG, PRODUCTS, public_catalog
from .jobs import JobRunner
from .models import (
    ArtifactReviewCreate,
    GenerationRequestCreate,
    PresetSelectionCreate,
    ProjectConfiguration,
    ProjectCreate,
    ProjectUpdate,
    RunCreate,
    ScenarioSelectionCreate,
)
from .services import (
    build_readiness,
    canonical_sha256,
    new_id,
    scenario_review_selection_blocker,
    utc_now,
)
from .storage import LocalStore
from .uploads import UploadValidationError, store_upload


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA_ROOT = Path(os.getenv("TERRAFLUX_DATA_ROOT", Path(__file__).parent / "data"))
DEFAULT_MAX_UPLOAD_BYTES = int(os.getenv("TERRAFLUX_MAX_UPLOAD_BYTES", str(4 * 1024**3)))


def create_app(
    data_root: Path | str | None = None,
    workspace_root: Path | str | None = None,
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
) -> FastAPI:
    store = LocalStore(Path(data_root or DEFAULT_DATA_ROOT))
    workspace = Path(workspace_root or WORKSPACE_ROOT).resolve()
    runner = JobRunner(store, workspace)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        runner.start()
        yield
        runner.stop()

    app = FastAPI(
        title="TerraFlux API local",
        version="0.1.0",
        description="Projetos, uploads, configuracao, pedidos, rodadas e produtos de sistematizacao.",
        lifespan=lifespan,
    )
    app.state.store = store
    app.state.runner = runner
    app.state.workspace_root = workspace
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost", "http://127.0.0.1", "http://localhost:8000", "http://127.0.0.1:8000", "null"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    api = FastAPI(
        title="TerraFlux API",
        version=app.version,
        docs_url="/docs",
        redoc_url=None,
        openapi_url="/openapi.json",
    )

    @api.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "terraflux-local-api",
            "version": app.version,
            "engines": list(ENGINE_CATALOG),
            "client_data_execution": "E0_PIPELINE_AVAILABLE_WITH_READINESS_GATES",
            "client_data_products": [
                "TOPOGRAPHY_E0",
                "SULCATION_E0",
                "CF0_CONTINUOUS",
                "C1_EMBEDDED_SCREENING",
                "PCX1_RUNOFF_SCREENING",
                "PCX2_HYDROGRAPH_SCREENING",
                "PCX3_REACH_ROUTING_SCREENING",
                "PCX4_SECTION_CAPACITY_SCREENING",
            ],
        }

    @api.get("/catalog")
    def catalog() -> dict[str, Any]:
        return public_catalog()

    @api.get("/catalog/products")
    def product_catalog() -> dict[str, Any]:
        return {"items": public_catalog()["products"]}

    @api.get("/catalog/asset-types")
    def asset_type_catalog() -> dict[str, Any]:
        return {"items": public_catalog()["asset_types"]}

    @api.get("/catalog/presets")
    def preset_catalog() -> dict[str, Any]:
        path = workspace / "config" / "catalogo_presets_sistema.json"
        if not path.is_file():
            raise HTTPException(status_code=503, detail="system preset catalog is unavailable")
        return json.loads(path.read_text(encoding="utf-8"))

    @api.get("/catalog/hydraulic-stability-models")
    def hydraulic_stability_catalog() -> dict[str, Any]:
        path = workspace / "config" / "catalogo_limites_estabilidade_hidraulica.json"
        if not path.is_file():
            raise HTTPException(status_code=503, detail="hydraulic stability reference catalog is unavailable")
        return json.loads(path.read_text(encoding="utf-8"))

    @api.post("/projects", status_code=status.HTTP_201_CREATED)
    def create_project(payload: ProjectCreate) -> dict[str, Any]:
        now = utc_now()
        project = {
            "id": new_id("prj"),
            **payload.model_dump(),
            "status": "DRAFT",
            "configuration": ProjectConfiguration().model_dump(),
            "created_at": now,
            "updated_at": now,
        }
        return store.insert("projects", project)

    @api.get("/projects")
    def list_projects() -> dict[str, Any]:
        return {"items": store.list("projects")}

    @api.get("/projects/{project_id}")
    def get_project(project_id: str) -> dict[str, Any]:
        return _require(store, "projects", project_id, "project")

    @api.patch("/projects/{project_id}")
    def update_project(project_id: str, payload: ProjectUpdate) -> dict[str, Any]:
        _require(store, "projects", project_id, "project")
        values = payload.model_dump(exclude_unset=True)
        values["updated_at"] = utc_now()
        return store.update("projects", project_id, values) or {}

    @api.get("/projects/{project_id}/configuration")
    def get_configuration(project_id: str) -> dict[str, Any]:
        return _require(store, "projects", project_id, "project")["configuration"]

    @api.put("/projects/{project_id}/configuration")
    def put_configuration(project_id: str, payload: ProjectConfiguration) -> dict[str, Any]:
        _require(store, "projects", project_id, "project")
        unknown = sorted(set(payload.selected_product_ids) - set(PRODUCTS))
        if unknown:
            raise HTTPException(status_code=422, detail={"code": "UNKNOWN_PRODUCTS", "product_ids": unknown})
        values = {"configuration": payload.model_dump(), "updated_at": utc_now()}
        updated = store.update("projects", project_id, values)
        return updated["configuration"] if updated else {}

    @api.post("/projects/{project_id}/configuration")
    def post_configuration(project_id: str, payload: ProjectConfiguration) -> dict[str, Any]:
        return put_configuration(project_id, payload)

    @api.post("/projects/{project_id}/assets", status_code=status.HTTP_201_CREATED)
    async def upload_project_asset(
        project_id: str,
        role: Annotated[str, Form()],
        file: Annotated[UploadFile, File()],
        description: Annotated[str | None, Form(max_length=500)] = None,
    ) -> dict[str, Any]:
        _require(store, "projects", project_id, "project")
        asset_id = new_id("ast")
        upload_dir = store.project_dir(project_id) / "assets" / asset_id
        try:
            file_data = await store_upload(file, upload_dir, role, max_upload_bytes)
        except UploadValidationError as exc:
            raise HTTPException(status_code=422, detail={"code": "UPLOAD_REJECTED", "message": str(exc)}) from exc
        now = utc_now()
        asset = {
            "id": asset_id,
            "project_id": project_id,
            "role": role,
            "description": description,
            "status": "STORED",
            "spatial_qa_status": "PENDING",
            **file_data,
            "created_at": now,
            "updated_at": now,
        }
        store.insert("assets", asset)
        return _public_asset(asset)

    @api.get("/projects/{project_id}/assets")
    def list_assets(project_id: str) -> dict[str, Any]:
        _require(store, "projects", project_id, "project")
        return {"items": [_public_asset(item) for item in store.list("assets", lambda item: item["project_id"] == project_id)]}

    @api.delete("/projects/{project_id}/assets/{asset_id}")
    def delete_project_asset(project_id: str, asset_id: str) -> dict[str, Any]:
        _require(store, "projects", project_id, "project")
        asset = _require(store, "assets", asset_id, "asset")
        if asset["project_id"] != project_id:
            raise HTTPException(status_code=404, detail="asset not found")
        referenced = any(
            any(reference.get("asset_id") == asset_id for reference in item.get("asset_snapshot", []))
            for item in store.list("generation_requests", lambda item: item["project_id"] == project_id)
        )
        if referenced:
            raise HTTPException(status_code=409, detail={"code": "ASSET_FROZEN_IN_REQUEST"})
        path = Path(asset["path"]).resolve()
        asset_dir = path.parent
        project_root = store.project_dir(project_id).resolve()
        if project_root not in asset_dir.parents or asset_dir.parent.name != "assets":
            raise HTTPException(status_code=409, detail="asset storage path is invalid")
        store.delete("assets", asset_id)
        shutil.rmtree(asset_dir)
        return {"deleted": True, "asset_id": asset_id}

    @api.get("/projects/{project_id}/readiness")
    def readiness(project_id: str) -> dict[str, Any]:
        return build_readiness(store, _require(store, "projects", project_id, "project"))

    @api.get("/projects/{project_id}/presets")
    def list_presets(project_id: str, include_catalog: bool = Query(default=False)) -> dict[str, Any]:
        _require(store, "projects", project_id, "project")
        result: dict[str, Any] = {
            "items": store.list("preset_selections", lambda item: item["project_id"] == project_id),
            "catalog_url": "/api/catalog/presets",
        }
        if include_catalog:
            result["catalog"] = preset_catalog()
        return result

    @api.post("/projects/{project_id}/presets", status_code=status.HTTP_201_CREATED)
    def create_preset(project_id: str, payload: PresetSelectionCreate) -> dict[str, Any]:
        _require(store, "projects", project_id, "project")
        selection = {
            "id": new_id("preset"),
            "project_id": project_id,
            **payload.model_dump(),
            "created_at": utc_now(),
        }
        selection["sha256"] = canonical_sha256(selection)
        return store.insert("preset_selections", selection)

    @api.get("/projects/{project_id}/requests")
    def list_requests(project_id: str) -> dict[str, Any]:
        _require(store, "projects", project_id, "project")
        return {"items": store.list("generation_requests", lambda item: item["project_id"] == project_id)}

    @api.get("/requests/{request_id}")
    def get_request(request_id: str) -> dict[str, Any]:
        return _require(store, "generation_requests", request_id, "generation request")

    @api.post("/projects/{project_id}/requests", status_code=status.HTTP_201_CREATED)
    def create_request(project_id: str, payload: GenerationRequestCreate) -> dict[str, Any]:
        project = _require(store, "projects", project_id, "project")
        unknown = sorted(set(payload.product_ids) - set(PRODUCTS))
        if unknown:
            raise HTTPException(status_code=422, detail={"code": "UNKNOWN_PRODUCTS", "product_ids": unknown})
        _validate_product_dependencies(payload.product_ids)
        if payload.preset_selection_id:
            preset = _require(store, "preset_selections", payload.preset_selection_id, "preset selection")
            if preset["project_id"] != project_id:
                raise HTTPException(status_code=409, detail="preset selection belongs to another project")
        request = _request_snapshot(store, project, payload.model_dump())
        return store.insert("generation_requests", request)

    @api.post("/projects/{project_id}/runs", status_code=status.HTTP_202_ACCEPTED)
    def create_run(project_id: str, payload: RunCreate) -> dict[str, Any]:
        project = _require(store, "projects", project_id, "project")
        request = None
        if payload.request_id:
            request = _require(store, "generation_requests", payload.request_id, "generation request")
            if request["project_id"] != project_id:
                raise HTTPException(status_code=409, detail="generation request belongs to another project")
        if payload.engine_id in {"project_topography", "project_pipeline_e0", "project_hydrology_screening"} and request is None:
            raise HTTPException(
                status_code=409,
                detail={"code": "IMMUTABLE_REQUEST_REQUIRED", "message": "Crie um pedido imutavel antes de executar este motor."},
            )
        product_ids = payload.product_ids or (request or {}).get("product_ids", [])
        if (
            request is not None
            and payload.engine_id in {"project_topography", "project_pipeline_e0", "project_hydrology_screening"}
            and payload.product_ids
            and payload.product_ids != request.get("product_ids", [])
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "REQUEST_PRODUCT_SNAPSHOT_MISMATCH"},
            )
        if payload.engine_id == "demo_current_dataset" and not product_ids:
            product_ids = project["configuration"]["selected_product_ids"]
        _validate_run_products(payload.engine_id, product_ids)
        run = _new_run(project_id, payload.engine_id, product_ids, request["id"] if request else None)
        store.insert("runs", run)
        runner.submit(run["id"])
        return run

    @api.get("/projects/{project_id}/runs")
    def list_runs(project_id: str) -> dict[str, Any]:
        _require(store, "projects", project_id, "project")
        return {"items": store.list("runs", lambda item: item["project_id"] == project_id)}

    @api.get("/runs")
    def list_all_runs(project_id: str | None = Query(default=None)) -> dict[str, Any]:
        items = store.list("runs", (lambda item: item["project_id"] == project_id) if project_id else None)
        return {"items": [_public_run(item) for item in items]}

    @api.get("/projects/{project_id}/artifacts")
    def project_artifacts(project_id: str) -> dict[str, Any]:
        _require(store, "projects", project_id, "project")
        items = store.list("artifacts", lambda item: item["project_id"] == project_id)
        return {"items": [_public_artifact(item) for item in items]}

    @api.post("/requests/{request_id}/runs", status_code=status.HTTP_202_ACCEPTED)
    def run_request(request_id: str, payload: RunCreate = Body(default=RunCreate())) -> dict[str, Any]:
        request = _require(store, "generation_requests", request_id, "generation request")
        data = payload.model_copy(update={"request_id": request_id})
        return create_run(request["project_id"], data)

    @api.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        return _public_run(_require(store, "runs", run_id, "run"))

    @api.get("/runs/{run_id}/logs")
    def get_run_logs(run_id: str) -> dict[str, Any]:
        run = _require(store, "runs", run_id, "run")
        return {"run_id": run_id, "status": run["status"], "items": run.get("logs", [])}

    @api.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
    def cancel_run(run_id: str) -> dict[str, Any]:
        run = _require(store, "runs", run_id, "run")
        if run["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            raise HTTPException(status_code=409, detail="run is already terminal")
        return _public_run(store.update("runs", run_id, {"cancel_requested": True, "updated_at": utc_now()}) or run)

    @api.get("/runs/{run_id}/artifacts")
    def run_artifacts(run_id: str) -> dict[str, Any]:
        _require(store, "runs", run_id, "run")
        items = store.list("artifacts", lambda item: item["run_id"] == run_id)
        return {"items": [_public_artifact(item) for item in items]}

    @api.get("/runs/{run_id}/scenarios")
    def run_scenarios(run_id: str) -> dict[str, Any]:
        _require(store, "runs", run_id, "run")
        return {"items": store.list("scenarios", lambda item: item["run_id"] == run_id)}

    @api.get("/runs/{run_id}/scenario-selection")
    def active_scenario_selection(run_id: str) -> dict[str, Any]:
        _require(store, "runs", run_id, "run")
        active = store.list(
            "scenarios",
            lambda item: item["run_id"] == run_id and item.get("selected_for_review") is True,
        )
        history = store.list("scenario_selections", lambda item: item["run_id"] == run_id)
        return {
            "active": active[0] if active else None,
            "selection": history[0] if history else None,
            "history_count": len(history),
            "guidance_authorized": False,
        }

    @api.get("/runs/{run_id}/scenario-selections")
    def scenario_selection_history(run_id: str) -> dict[str, Any]:
        _require(store, "runs", run_id, "run")
        return {"items": store.list("scenario_selections", lambda item: item["run_id"] == run_id)}

    @api.post(
        "/runs/{run_id}/scenarios/{scenario_id}/selection",
        status_code=status.HTTP_201_CREATED,
    )
    def select_scenario_for_review(
        run_id: str,
        scenario_id: str,
        payload: ScenarioSelectionCreate,
    ) -> dict[str, Any]:
        run = _require(store, "runs", run_id, "run")
        if run.get("status") != "SUCCEEDED":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "RUN_NOT_SELECTABLE",
                    "message": "Scenario selection is available only after a successful run.",
                },
            )
        scenario = _require(store, "scenarios", scenario_id, "scenario")
        if scenario.get("run_id") != run_id or scenario.get("project_id") != run.get("project_id"):
            raise HTTPException(status_code=404, detail="scenario not found")
        if payload.selected_for_review:
            blocker = scenario_review_selection_blocker(scenario)
            if blocker:
                raise HTTPException(status_code=409, detail=blocker)

        selected_at = utc_now()
        selection_id = new_id("sel")
        technical_snapshot = {
            key: value
            for key, value in scenario.items()
            if key
            not in {
                "selected_for_review",
                "selected_at",
                "selection_scope",
                "reviewer_note",
                "selection_id",
                "deselected_at",
                "deselected_by_selection_id",
            }
        }

        def operation(state: dict[str, Any]) -> dict[str, Any]:
            current = state["scenarios"].get(scenario_id)
            if current is None or current.get("run_id") != run_id:
                raise HTTPException(status_code=404, detail="scenario not found")
            if payload.selected_for_review:
                current_blocker = scenario_review_selection_blocker(current)
                if current_blocker:
                    raise HTTPException(status_code=409, detail=current_blocker)
            elif current.get("selected_for_review") is not True:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "SCENARIO_NOT_SELECTED",
                        "message": "Only the active review representative can be deselected.",
                    },
                )

            superseded_ids: list[str] = []
            if payload.selected_for_review:
                for candidate in state["scenarios"].values():
                    if candidate.get("run_id") != run_id or candidate.get("id") == scenario_id:
                        continue
                    if candidate.get("selected_for_review") is not True:
                        continue
                    superseded_ids.append(str(candidate["id"]))
                    candidate["selected_for_review"] = False
                    candidate["selection_scope"] = None
                    candidate["reviewer_note"] = None
                    candidate["selection_id"] = None
                    candidate["deselected_at"] = selected_at
                    candidate["deselected_by_selection_id"] = selection_id

                current.update(
                    {
                        "selected_for_review": True,
                        "selected_at": selected_at,
                        "selection_scope": payload.selection_scope,
                        "reviewer_note": payload.reviewer_note or None,
                        "selection_id": selection_id,
                        "deselected_at": None,
                        "deselected_by_selection_id": None,
                        "guidance_authorized": False,
                    }
                )
            else:
                current.update(
                    {
                        "selected_for_review": False,
                        "selected_at": None,
                        "selection_scope": None,
                        "reviewer_note": None,
                        "selection_id": None,
                        "deselected_at": selected_at,
                        "deselected_by_selection_id": selection_id,
                        "guidance_authorized": False,
                    }
                )
            audit = {
                "id": selection_id,
                "project_id": run["project_id"],
                "run_id": run_id,
                "scenario_id": scenario_id,
                "scenario_code": current.get("code"),
                "event": "SELECTED" if payload.selected_for_review else "DESELECTED",
                "selected_for_review": payload.selected_for_review,
                "selected_at": selected_at if payload.selected_for_review else None,
                "deselected_at": None if payload.selected_for_review else selected_at,
                "selection_scope": payload.selection_scope if payload.selected_for_review else None,
                "reviewer_note": payload.reviewer_note or None,
                "superseded_scenario_ids": sorted(superseded_ids),
                "scenario_snapshot_sha256": canonical_sha256(technical_snapshot),
                "guidance_authorized": False,
                "authorization_boundary": "TECHNICAL_REVIEW_ONLY_NOT_MACHINE_GUIDANCE",
                "created_at": selected_at,
            }
            audit["sha256"] = canonical_sha256(audit)
            state.setdefault("scenario_selections", {})[selection_id] = audit
            return {"scenario": current, "selection": audit}

        return store.transaction(operation)

    @api.get("/artifacts/{artifact_id}")
    def get_artifact(artifact_id: str) -> dict[str, Any]:
        return _public_artifact(_require(store, "artifacts", artifact_id, "artifact"))

    @api.get("/artifacts/{artifact_id}/download")
    def download_artifact(artifact_id: str) -> FileResponse:
        artifact = _require(store, "artifacts", artifact_id, "artifact")
        path = Path(artifact["path"]).resolve()
        allowed_roots = [store.root.resolve(), (workspace / "dataset" / "derived").resolve()]
        if not any(root == path or root in path.parents for root in allowed_roots) or not path.is_file():
            raise HTTPException(status_code=404, detail="artifact file is unavailable")
        return FileResponse(path, media_type=artifact["media_type"], filename=artifact["filename"])

    @api.post("/artifacts/{artifact_id}/reviews", status_code=status.HTTP_201_CREATED)
    def review_artifact(artifact_id: str, payload: ArtifactReviewCreate) -> dict[str, Any]:
        artifact = _require(store, "artifacts", artifact_id, "artifact")
        review = {
            "id": new_id("rev"),
            "artifact_id": artifact_id,
            "run_id": artifact["run_id"],
            "project_id": artifact["project_id"],
            **payload.model_dump(),
            "created_at": utc_now(),
        }
        review["sha256"] = canonical_sha256(review)
        return store.insert("artifact_reviews", review)

    app.mount("/api", api)
    web_root = workspace / "platform" / "web"
    if web_root.is_dir():
        app.mount("/", StaticFiles(directory=web_root, html=True), name="web")
    return app


def _require(store: LocalStore, collection: str, record_id: str, label: str) -> dict[str, Any]:
    record = store.get(collection, record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return record


def _request_snapshot(store: LocalStore, project: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    assets = store.list("assets", lambda item: item["project_id"] == project["id"] and item["status"] == "STORED")
    request = {
        "id": new_id("req"),
        "project_id": project["id"],
        **payload,
        "configuration_snapshot": project["configuration"],
        "asset_snapshot": [
            {"asset_id": item["id"], "role": item["role"], "sha256": item["sha256"], "size_bytes": item["size_bytes"]}
            for item in sorted(assets, key=lambda item: item["id"])
        ],
        "created_at": utc_now(),
        "immutable": True,
    }
    request["sha256"] = canonical_sha256(request)
    return request


def _validate_run_products(engine_id: str, product_ids: list[str]) -> None:
    if engine_id not in ENGINE_CATALOG:
        raise HTTPException(status_code=422, detail="engine is not whitelisted")
    unknown = sorted(set(product_ids) - set(PRODUCTS))
    if unknown:
        raise HTTPException(status_code=422, detail={"code": "UNKNOWN_PRODUCTS", "product_ids": unknown})
    _validate_product_dependencies(product_ids)
    if engine_id == "demo_current_dataset":
        unavailable = [item for item in product_ids if not PRODUCTS[item]["implementation"].startswith("DEMO_AVAILABLE")]
        if unavailable:
            raise HTTPException(status_code=409, detail={"code": "PRODUCT_NOT_AVAILABLE_IN_DEMO", "product_ids": unavailable})
    if engine_id == "project_topography":
        if product_ids != ["TOPOGRAPHY_E0"]:
            raise HTTPException(
                status_code=409,
                detail={"code": "ENGINE_PRODUCT_MISMATCH", "required_product_ids": ["TOPOGRAPHY_E0"]},
            )
    if engine_id == "project_pipeline_e0":
        supported = {
            "TOPOGRAPHY_E0",
            "SULCATION_E0",
            "CF0_CONTINUOUS",
            "C1_EMBEDDED_SCREENING",
        }
        requested = set(product_ids)
        if not requested <= supported or not requested & {
            "SULCATION_E0",
            "CF0_CONTINUOUS",
            "C1_EMBEDDED_SCREENING",
        }:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ENGINE_PRODUCT_MISMATCH",
                    "supported_product_ids": sorted(supported),
                    "requires_one_of": [
                        "SULCATION_E0",
                        "CF0_CONTINUOUS",
                        "C1_EMBEDDED_SCREENING",
                    ],
                },
            )
    if engine_id == "project_hydrology_screening":
        requested = set(product_ids)
        combinations = (
            {"PCX1_RUNOFF_SCREENING"},
            {"PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING"},
            {"PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING", "PCX3_REACH_ROUTING_SCREENING"},
            {"PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING", "PCX3_REACH_ROUTING_SCREENING", "PCX4_SECTION_CAPACITY_SCREENING"},
        )
        if requested not in combinations:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ENGINE_PRODUCT_MISMATCH",
                    "supported_product_combinations": [
                        ["PCX1_RUNOFF_SCREENING"],
                        ["PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING"],
                        ["PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING", "PCX3_REACH_ROUTING_SCREENING"],
                        ["PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING", "PCX3_REACH_ROUTING_SCREENING", "PCX4_SECTION_CAPACITY_SCREENING"],
                    ],
                },
            )


def _validate_product_dependencies(product_ids: list[str]) -> None:
    requested = set(product_ids)
    if "C1_EMBEDDED_SCREENING" in requested and "CF0_CONTINUOUS" not in requested:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PRODUCT_DEPENDENCY_MISSING",
                "product_id": "C1_EMBEDDED_SCREENING",
                "required_product_ids": ["CF0_CONTINUOUS"],
                "message": "A triagem conceitual C1 exige o CF0 da mesma rodada imutavel.",
            },
        )
    if "PCX2_HYDROGRAPH_SCREENING" in requested and "PCX1_RUNOFF_SCREENING" not in requested:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PRODUCT_DEPENDENCY_MISSING",
                "product_id": "PCX2_HYDROGRAPH_SCREENING",
                "required_product_ids": ["PCX1_RUNOFF_SCREENING"],
                "message": "O hidrograma preliminar exige a chuva que vira escoamento na mesma rodada.",
            },
        )
    if "PCX3_REACH_ROUTING_SCREENING" in requested and not {
        "PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING"
    }.issubset(requested):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PRODUCT_DEPENDENCY_MISSING",
                "product_id": "PCX3_REACH_ROUTING_SCREENING",
                "required_product_ids": ["PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING"],
                "message": "A propagacao preliminar exige a chuva que escoa e o hidrograma na mesma rodada.",
            },
        )
    if "PCX4_SECTION_CAPACITY_SCREENING" in requested and not {
        "PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING", "PCX3_REACH_ROUTING_SCREENING"
    }.issubset(requested):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PRODUCT_DEPENDENCY_MISSING",
                "product_id": "PCX4_SECTION_CAPACITY_SCREENING",
                "required_product_ids": ["PCX1_RUNOFF_SCREENING", "PCX2_HYDROGRAPH_SCREENING", "PCX3_REACH_ROUTING_SCREENING"],
                "message": "A verificacao de capacidade exige a cadeia hidrologica e a rede na mesma rodada.",
            },
        )


def _new_run(project_id: str, engine_id: str, product_ids: list[str], request_id: str | None) -> dict[str, Any]:
    now = utc_now()
    return {
        "id": new_id("run"),
        "project_id": project_id,
        "request_id": request_id,
        "engine_id": engine_id,
        "product_ids": product_ids,
        "status": "QUEUED",
        "progress": 0,
        "logs": [],
        "cancel_requested": False,
        "created_at": now,
        "updated_at": now,
        "delivery_boundary": (
            "HYDROLOGY_SCREENING_ONLY_NOT_HYDRAULIC_DESIGN"
            if engine_id == "project_hydrology_screening"
            else "E0_TRIAGEM_NOT_GUIDANCE_AUTHORIZED"
        ),
    }


def _public_asset(asset: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in asset.items() if key != "path"}


def _public_run(run: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in run.items() if key != "logs"} | {"log_count": len(run.get("logs", []))}


def _public_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in artifact.items() if key != "path"}
    result["download_url"] = f"/api/artifacts/{artifact['id']}/download"
    if Path(artifact["filename"]).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        result["preview_url"] = result["download_url"]
    return result


app = create_app()
