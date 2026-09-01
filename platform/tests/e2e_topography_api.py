#!/usr/bin/env python3
"""Exercise the client-data topography flow against a running local API."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import requests


def request(session: requests.Session, method: str, url: str, **kwargs):
    response = session.request(method, url, timeout=1800, **kwargs)
    if not response.ok:
        raise RuntimeError(f"{method} {url} -> {response.status_code}: {response.text[:2000]}")
    return response


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def run(base_url: str, dataset: Path, elevation_source: str) -> dict:
    api = base_url.rstrip("/") + "/api"
    session = requests.Session()
    project = request(
        session,
        "POST",
        f"{api}/projects",
        json={
            "name": f"Ensaio E2E Topografia {elevation_source}",
            "farm_name": "Dataset de validacao",
            "client_name": "QA TerraFlux",
            "crs": "EPSG:31982",
        },
    ).json()
    project_id = project["id"]

    configuration = request(session, "GET", f"{api}/projects/{project_id}/configuration").json()
    configuration["topography"].update(
        {
            "resolution_m": 3.4,
            "contour_interval_m": 1.0,
            "field_id_column": "cd_upnivel",
            "elevation_source_preference": "POINT_CLOUD" if elevation_source == "point-cloud" else "DTM_DEM",
        }
    )
    configuration["constraints"]["power_network_state"] = "DECLARED_NONE"
    configuration["selected_product_ids"] = ["TOPOGRAPHY_E0"]
    request(session, "PUT", f"{api}/projects/{project_id}/configuration", json=configuration)

    uploaded = []
    for suffix in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        source = dataset / f"Contorno{suffix}"
        with source.open("rb") as stream:
            uploaded.append(
                request(
                    session,
                    "POST",
                    f"{api}/projects/{project_id}/assets",
                    data={"role": "FIELD_BOUNDARY"},
                    files={"file": (source.name, stream, "application/octet-stream")},
                ).json()
            )
    elevation = dataset / ("TERRENO_LIMPO.LAZ" if elevation_source == "point-cloud" else "DEM.tif")
    role = "POINT_CLOUD" if elevation_source == "point-cloud" else "DTM_DEM"
    media_type = "application/octet-stream" if elevation_source == "point-cloud" else "image/tiff"
    with elevation.open("rb") as stream:
        uploaded.append(
            request(
                session,
                "POST",
                f"{api}/projects/{project_id}/assets",
                data={"role": role},
                files={"file": (elevation.name, stream, media_type)},
            ).json()
        )

    readiness = request(session, "GET", f"{api}/projects/{project_id}/readiness").json()
    if not readiness["summary"]["minimum_inputs_present"] or readiness["summary"]["blocking_count"]:
        raise RuntimeError(f"unexpected readiness: {json.dumps(readiness, ensure_ascii=False)}")

    generation_request = request(
        session,
        "POST",
        f"{api}/projects/{project_id}/requests",
        json={"name": "Topografia E0 real", "product_ids": ["TOPOGRAPHY_E0"]},
    ).json()
    job = request(
        session,
        "POST",
        f"{api}/requests/{generation_request['id']}/runs",
        json={"engine_id": "project_topography", "product_ids": ["TOPOGRAPHY_E0"]},
    ).json()

    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        job = request(session, "GET", f"{api}/runs/{job['id']}").json()
        if job["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            break
        time.sleep(0.5)
    if job["status"] != "SUCCEEDED":
        logs = request(session, "GET", f"{api}/runs/{job['id']}/logs").json()
        raise RuntimeError(f"topography run failed: {json.dumps({'run': job, 'logs': logs}, ensure_ascii=False)}")

    artifacts = request(session, "GET", f"{api}/runs/{job['id']}/artifacts").json()["items"]
    for artifact in artifacts:
        content = request(session, "GET", f"{base_url.rstrip('/')}{artifact['download_url']}").content
        if sha256_bytes(content) != artifact["sha256"]:
            raise RuntimeError(f"download hash mismatch: {artifact['filename']}")
    filenames = {item["filename"] for item in artifacts}
    required = {
        "dtm.tif",
        "slope_percent.tif",
        "contours.gpkg",
        "topography_map.png",
        "topography_manifest.json",
        "Dossie_Tecnico_Rodada_E0_CF0.pdf",
        "dossier_manifest.json",
    }
    if elevation_source == "point-cloud":
        required.add("point_density_5m.tif")
    if not required <= filenames:
        raise RuntimeError(f"missing artifacts: {sorted(required - filenames)}")
    if (job.get("result_summary") or {}).get("artifact_count") != len(artifacts):
        raise RuntimeError("topography result summary differs from published artifacts")

    return {
        "status": "PASS",
        "elevation_source": elevation_source,
        "project_id": project_id,
        "request_id": generation_request["id"],
        "run_id": job["id"],
        "result": job.get("result"),
        "result_summary": job.get("result_summary"),
        "uploaded_asset_count": len(uploaded),
        "artifact_count": len(artifacts),
        "artifact_filenames": sorted(filenames),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--dataset", type=Path, default=Path(__file__).resolve().parents[2] / "dataset")
    parser.add_argument("--elevation-source", choices=["terrain", "point-cloud"], default="terrain")
    args = parser.parse_args()
    print(json.dumps(run(args.base_url, args.dataset.resolve(), args.elevation_source), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
