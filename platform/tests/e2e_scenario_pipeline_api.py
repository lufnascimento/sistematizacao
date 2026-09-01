#!/usr/bin/env python3
"""Exercise the client-data E0/CF0 scenario pipeline against a running API."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any

import requests


PRODUCT_IDS = ["TOPOGRAPHY_E0", "SULCATION_E0", "CF0_CONTINUOUS"]
TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELLED"}
E0_STATUSES = {
    "E0_SCREENING_ONLY_NOT_AUTHORIZED",
    "E0_INFEASIBLE_GEOMETRY_DIAGNOSTIC_ONLY",
}
CF0_STATUSES = {
    "CF0_GEOMETRIC_PASS_HYDRAULIC_UNCONFIRMED",
    "CF0_PARTIAL_GEOMETRIC_SCREENING",
    "CF0_NO_FEASIBLE_FAMILY",
}


def request(session: requests.Session, method: str, url: str, **kwargs: Any) -> requests.Response:
    response = session.request(method, url, timeout=1800, **kwargs)
    if not response.ok:
        raise RuntimeError(f"{method} {url} -> {response.status_code}: {response.text[:4000]}")
    return response


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def contorno_zip(dataset: Path) -> bytes:
    required_suffixes = (".shp", ".shx", ".dbf", ".prj")
    missing = [f"Contorno{suffix}" for suffix in required_suffixes if not (dataset / f"Contorno{suffix}").is_file()]
    if missing:
        raise FileNotFoundError(f"incomplete Contorno shapefile: {', '.join(missing)}")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for suffix in (*required_suffixes, ".cpg"):
            source = dataset / f"Contorno{suffix}"
            if source.is_file():
                archive.write(source, arcname=source.name)
    return buffer.getvalue()


def assert_conservative_scenarios(
    scenarios: list[dict[str, Any]], *, run_id: str, request_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not scenarios:
        raise RuntimeError("pipeline published no scenarios")

    e0 = [item for item in scenarios if str(item.get("family", "")).startswith("E0")]
    cf0 = [item for item in scenarios if str(item.get("family", "")).startswith("CF0")]
    if not e0 or not cf0:
        raise RuntimeError(f"expected both E0 and CF0 scenarios, got E0={len(e0)} CF0={len(cf0)}")

    for item in scenarios:
        if item.get("run_id") != run_id or not str(item.get("id", "")).startswith(f"{run_id}:"):
            raise RuntimeError(f"scenario has invalid run lineage: {item.get('id')}")
        if item.get("guidance_authorized") is not False:
            raise RuntimeError(f"scenario improperly authorizes guidance: {item.get('id')}")
        lineage = item.get("source_lineage") or {}
        if lineage.get("request_id") != request_id or len(str(lineage.get("request_sha256", ""))) != 64:
            raise RuntimeError(f"scenario has invalid immutable request lineage: {item.get('id')}")

    for item in e0:
        if item.get("status") not in E0_STATUSES:
            raise RuntimeError(f"E0 scenario has unsafe status: {item.get('status')}")
        if item.get("release_level") != "E0_topographic_screening":
            raise RuntimeError(f"E0 scenario exceeded its release boundary: {item.get('id')}")
        if item.get("recommendation_scope") != "E0_COMPARISON_REPRESENTATIVE_ONLY":
            raise RuntimeError(f"E0 recommendation scope changed: {item.get('id')}")
        hydraulic = item.get("hydraulic_statuses") or []
        if not hydraulic or any(not str(status).lower().startswith("not_evaluated") for status in hydraulic):
            raise RuntimeError(f"E0 scenario contains a hydraulic approval claim: {item.get('id')}")
        metrics = item.get("metrics") or {}
        if float(metrics.get("total_line_km", -1)) < 0 or float(metrics.get("comparison_score", -1)) < 0:
            raise RuntimeError(f"E0 scenario has invalid comparison metrics: {item.get('id')}")

    recommended = [item for item in e0 if item.get("recommended") is True]
    if len(recommended) > 1:
        raise RuntimeError("more than one E0 comparison representative was marked recommended")

    for item in cf0:
        if item.get("status") not in CF0_STATUSES:
            raise RuntimeError(f"CF0 scenario has unsafe status: {item.get('status')}")
        if item.get("hydraulic_status") != "HYDRAULIC_UNCONFIRMED":
            raise RuntimeError(f"CF0 scenario contains a hydraulic approval claim: {item.get('id')}")
        if item.get("recommended") is not False:
            raise RuntimeError(f"CF0 scenario cannot be recommended at screening stage: {item.get('id')}")
        metrics = item.get("metrics") or {}
        if int(metrics.get("work_block_count", 0)) < 1 or float(metrics.get("total_line_km", -1)) < 0:
            raise RuntimeError(f"CF0 scenario has invalid screening metrics: {item.get('id')}")

    return e0, cf0


def assert_artifacts(
    session: requests.Session, base_url: str, artifacts: list[dict[str, Any]]
) -> set[str]:
    if not artifacts:
        raise RuntimeError("pipeline published no artifacts")

    filenames = {item["filename"] for item in artifacts}
    required = {
        "dtm.tif",
        "slope_percent.tif",
        "point_density_5m.tif",
        "contours.gpkg",
        "topography_map.png",
        "topography_manifest.json",
        "project_generation_request.json",
        "sulcation_scenarios.gpkg",
        "sulcation_scenarios_map.png",
        "sulcation_scenario_metrics.json",
        "continuous_family_candidates.gpkg",
        "continuous_family_map.png",
        "continuous_family_manifest.json",
        "Dossie_Tecnico_Rodada_E0_CF0.pdf",
        "dossier_manifest.json",
    }
    if not required <= filenames:
        raise RuntimeError(f"missing pipeline artifacts: {sorted(required - filenames)}")

    for artifact in artifacts:
        if artifact.get("guidance_authorized") is not False or artifact.get("delivery_level") != "E0_TRIAGEM":
            raise RuntimeError(f"artifact exceeded E0 delivery boundary: {artifact.get('filename')}")
        content = request(
            session,
            "GET",
            f"{base_url.rstrip('/')}{artifact['download_url']}",
        ).content
        if len(content) != artifact.get("size_bytes"):
            raise RuntimeError(f"download size mismatch: {artifact['filename']}")
        if sha256_bytes(content) != artifact.get("sha256"):
            raise RuntimeError(f"download hash mismatch: {artifact['filename']}")

    required_maps = {
        "topography_map.png",
        "sulcation_scenarios_map.png",
        "continuous_family_map.png",
    }
    by_name = {item["filename"]: item for item in artifacts}
    for filename in required_maps:
        artifact = by_name[filename]
        if artifact.get("media_type") != "image/png" or artifact.get("preview_url") != artifact.get("download_url"):
            raise RuntimeError(f"map is not exposed as a PNG preview: {filename}")
    dossier_pdf = by_name["Dossie_Tecnico_Rodada_E0_CF0.pdf"]
    if dossier_pdf.get("media_type") != "application/pdf" or int(dossier_pdf.get("pages", 0)) < 1:
        raise RuntimeError("technical dossier is not exposed as a validated multipage PDF")
    return filenames


def run(base_url: str, dataset: Path, timeout_seconds: float) -> dict[str, Any]:
    api = base_url.rstrip("/") + "/api"
    session = requests.Session()
    project = request(
        session,
        "POST",
        f"{api}/projects",
        json={
            "name": "Ensaio E2E Pipeline E0 CF0",
            "farm_name": "Dataset de validacao",
            "client_name": "QA TerraFlux",
            "crs": "EPSG:31982",
        },
    ).json()
    project_id = project["id"]

    configuration = request(session, "GET", f"{api}/projects/{project_id}/configuration").json()
    configuration["topography"].update(
        {
            "resolution_m": 1.0,
            "contour_interval_m": 1.0,
            "field_id_column": "cd_upnivel",
            "elevation_source_preference": "POINT_CLOUD",
        }
    )
    configuration["constraints"]["power_network_state"] = "DECLARED_NONE"
    configuration["selected_product_ids"] = PRODUCT_IDS
    request(session, "PUT", f"{api}/projects/{project_id}/configuration", json=configuration)

    boundary_zip = contorno_zip(dataset)
    uploaded = [
        request(
            session,
            "POST",
            f"{api}/projects/{project_id}/assets",
            data={"role": "FIELD_BOUNDARY"},
            files={"file": ("Contorno.zip", boundary_zip, "application/zip")},
        ).json()
    ]
    elevation = dataset / "TERRENO_LIMPO.LAZ"
    if not elevation.is_file():
        raise FileNotFoundError(f"ground point-cloud fixture does not exist: {elevation}")
    with elevation.open("rb") as stream:
        uploaded.append(
            request(
                session,
                "POST",
                f"{api}/projects/{project_id}/assets",
                data={"role": "POINT_CLOUD"},
                files={"file": (elevation.name, stream, "application/octet-stream")},
            ).json()
        )

    readiness = request(session, "GET", f"{api}/projects/{project_id}/readiness").json()
    available = set(readiness["summary"].get("available_product_ids", []))
    if (
        not readiness["summary"].get("minimum_inputs_present")
        or readiness["summary"].get("blocking_count")
        or not set(PRODUCT_IDS) <= available
    ):
        raise RuntimeError(f"unexpected pipeline readiness: {json.dumps(readiness, ensure_ascii=False)}")

    generation_request = request(
        session,
        "POST",
        f"{api}/projects/{project_id}/requests",
        json={"name": "Cenarios reais E0 e CF0", "product_ids": PRODUCT_IDS},
    ).json()
    job = request(
        session,
        "POST",
        f"{api}/requests/{generation_request['id']}/runs",
        json={"engine_id": "project_pipeline_e0", "product_ids": PRODUCT_IDS},
    ).json()

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        job = request(session, "GET", f"{api}/runs/{job['id']}").json()
        if job["status"] in TERMINAL_STATUSES:
            break
        time.sleep(1.0)
    if job["status"] != "SUCCEEDED":
        logs = request(session, "GET", f"{api}/runs/{job['id']}/logs").json()
        raise RuntimeError(
            f"scenario pipeline failed or timed out: "
            f"{json.dumps({'run': job, 'logs': logs}, ensure_ascii=False)}"
        )

    result_summary = job.get("result_summary") or {}
    if (
        job.get("result") != "PASS_E0_CF0_GEOMETRIC_SCREENING"
        or result_summary.get("guidance_authorized") is not False
        or result_summary.get("hydraulic_status") != "NOT_EVALUATED_OR_UNCONFIRMED"
    ):
        raise RuntimeError(f"unsafe pipeline release summary: {json.dumps(result_summary, ensure_ascii=False)}")

    scenarios = request(session, "GET", f"{api}/runs/{job['id']}/scenarios").json()["items"]
    e0, cf0 = assert_conservative_scenarios(
        scenarios,
        run_id=job["id"],
        request_id=generation_request["id"],
    )
    artifacts = request(session, "GET", f"{api}/runs/{job['id']}/artifacts").json()["items"]
    filenames = assert_artifacts(session, base_url, artifacts)

    if result_summary.get("scenario_count") != len(scenarios):
        raise RuntimeError("result summary scenario count differs from published scenarios")
    if result_summary.get("artifact_count") != len(artifacts):
        raise RuntimeError("result summary artifact count differs from published artifacts")

    return {
        "status": "PASS",
        "project_id": project_id,
        "request_id": generation_request["id"],
        "run_id": job["id"],
        "result": job.get("result"),
        "result_summary": result_summary,
        "uploaded_asset_count": len(uploaded),
        "scenario_count": len(scenarios),
        "e0_scenario_count": len(e0),
        "cf0_scenario_count": len(cf0),
        "recommended_e0_codes": [item["code"] for item in e0 if item.get("recommended")],
        "artifact_count": len(artifacts),
        "artifact_filenames": sorted(filenames),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[2] / "dataset")
    parser.add_argument("--timeout-seconds", type=float, default=14400.0)
    args = parser.parse_args()
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    print(
        json.dumps(
            run(args.base_url, args.dataset.resolve(), args.timeout_seconds),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
