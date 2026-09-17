"""Exercise a new client project through the public API, without editing storage."""

import argparse
import json
import time
from pathlib import Path

import httpx


def wait_for_run(client, run):
    previous = None
    while run["status"] in {"QUEUED", "RUNNING"}:
        current = (run["status"], run.get("stage"), run.get("progress"))
        if current != previous:
            print(current, flush=True)
            previous = current
        time.sleep(5)
        for attempt in range(3):
            try:
                response = client.get(f"/api/runs/{run['id']}")
                response.raise_for_status()
                run = response.json()
                break
            except httpx.TransportError:
                if attempt == 2:
                    raise
                time.sleep(2)
    print(json.dumps(run, ensure_ascii=True, indent=2), flush=True)
    if run["status"] != "SUCCEEDED":
        raise RuntimeError("Project smoke run failed; retained inputs and logs for diagnosis")
    response = client.get(f"/api/runs/{run['id']}/map-layers")
    response.raise_for_status()
    print("ready_layers", response.json()["ready_count"], flush=True)
    print(f"{str(client.base_url).rstrip('/')}/map.html?run={run['id']}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8003")
    parser.add_argument("--resume-run")
    parser.add_argument("--boundary", type=Path)
    parser.add_argument("--terrain", type=Path)
    parser.add_argument("--crs")
    parser.add_argument("--field-id-column")
    parser.add_argument("--resolution-m", type=float)
    parser.add_argument("--name", default="Fazenda - cenarios preliminares para revisao")
    parser.add_argument("--products", nargs="+", default=["TOPOGRAPHY_E0", "SULCATION_E0", "CF0_CONTINUOUS"])
    parser.add_argument("--power-network-declared-none", action="store_true")
    args = parser.parse_args()
    if args.resume_run:
        with httpx.Client(base_url=args.url, timeout=120) as client:
            response = client.get(f"/api/runs/{args.resume_run}")
            response.raise_for_status()
            wait_for_run(client, response.json())
        return
    if not all((args.boundary, args.terrain, args.crs, args.field_id_column, args.resolution_m, args.power_network_declared_none)):
        parser.error("New projects require boundary, terrain, CRS, field ID column, resolution and explicit absence of power lines")
    sources = [args.boundary]
    if args.boundary.suffix.lower() == ".shp":
        sources += [args.boundary.with_suffix(extension) for extension in (".shx", ".dbf", ".prj")]
    if not all(path.is_file() for path in [*sources, args.terrain]):
        raise ValueError("Missing required source file")
    with httpx.Client(base_url=args.url, timeout=120) as client:
        def request(method, url, **kwargs):
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()

        project = request("POST", "/api/projects", json={"name": args.name, "crs": args.crs,
                          "description": "Estudo preliminar com dados reais. Parametros de referencia, sem validacao de campo ou autorizacao de implantacao."})
        project_url = f"/api/projects/{project['id']}"
        print("project", project["id"], flush=True)
        for path, role in [*((path, "FIELD_BOUNDARY") for path in sources), (args.terrain, "DTM_DEM")]:
            with path.open("rb") as stream:
                request("POST", project_url + "/assets", data={"role": role, "description": "Fonte de estudo; qualidade vertical nao validada independentemente."}, files={"file": (path.name, stream)})
            print("uploaded", path.name, flush=True)
        configuration = request("GET", project_url + "/configuration")
        configuration["topography"].update(resolution_m=args.resolution_m, field_id_column=args.field_id_column)
        configuration["constraints"]["power_network_state"] = "DECLARED_NONE"
        configuration["selected_product_ids"] = args.products
        request("PUT", project_url + "/configuration", json=configuration)
        frozen = request("POST", project_url + "/requests", json={"name": "Cenarios para inspecao espacial", "product_ids": args.products,
                         "notes": "Rede eletrica declarada ausente pelo usuario. Demais parametros sao referencias de estudo, nao medidas de campo."})
        run = request("POST", project_url + "/runs", json={"engine_id": "project_pipeline_e0", "request_id": frozen["id"]})
        print("run", run["id"], flush=True)
        wait_for_run(client, run)


if __name__ == "__main__":
    main()
