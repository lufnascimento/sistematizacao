#!/usr/bin/env python3
"""Verify the live TerraFlux browser workflow against a running API."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[2]
BASE_URL = "http://127.0.0.1:8000"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument(
        "--project-id",
        default=os.environ.get("TERRAFLUX_FRONTEND_PROJECT_ID"),
        help="Reuse a project that already has a successful run instead of generating topography.",
    )
    parser.add_argument(
        "--expect-scenarios",
        action="store_true",
        help="Require and verify E0/CF0 scenario semantics on the reused project.",
    )
    return parser.parse_args()


def geometry_eligible(scenario: dict[str, object]) -> bool:
    explicit = scenario.get("geometry_eligible")
    if isinstance(explicit, bool):
        return explicit
    status = str(scenario.get("status") or "").upper()
    if status in {"CF0_NO_FEASIBLE_FAMILY", "CF0_PARTIAL_GEOMETRIC_SCREENING", "CONCEPT_ONLY", "BLOCKED"}:
        return False
    return "INFEASIBLE" not in status and "DIAGNOSTIC_ONLY" not in status


def api_items(page, path: str) -> list[dict[str, object]]:
    response = page.request.get(f"{BASE_URL}{path}")
    assert response.ok, (response.status, response.text())
    payload = response.json()
    return payload.get("items") or payload.get("runs") or payload.get("scenarios") or []


def verify_scenarios(page, project_id: str) -> dict[str, int]:
    runs = api_items(page, f"/api/runs?project_id={project_id}")
    run = next((item for item in runs if item.get("status") == "SUCCEEDED"), None)
    assert run, "The reused project has no successful run"
    scenarios = api_items(page, f"/api/runs/{run['id']}/scenarios")
    assert scenarios, "No scenarios were published"
    assert any("E0" in str(item.get("family", "")).upper() for item in scenarios), scenarios
    assert any("CF0" in str(item.get("family", "")).upper() for item in scenarios), scenarios

    recommended = [item for item in scenarios if item.get("recommended") is True]
    assert len(recommended) <= 1, recommended
    assert all(geometry_eligible(item) for item in recommended), recommended
    assert page.locator("[data-scenario-tab]").count() == len(scenarios)

    if recommended:
        selected = page.locator(".scenario-tab.is-active").get_attribute("data-scenario-tab")
        assert selected == recommended[0]["id"], (selected, recommended[0]["id"])
        assert recommended[0]["name"] in page.locator(".comparison-table thead").inner_text()

    ineligible_count = 0
    cf0_count = 0
    for scenario in scenarios:
        tab = page.locator(f'[data-scenario-tab="{scenario["id"]}"]')
        tab.click()
        page.locator("[data-scenario-decision]").wait_for(timeout=10_000)
        family = str(scenario.get("family") or "").upper()
        eligible = geometry_eligible(scenario)
        if not eligible:
            ineligible_count += 1
            assert page.locator('[data-scenario-decision="ineligible"]').count() == 1
            assert page.locator("[data-select-scenario]").count() == 0
        else:
            assert page.locator("[data-select-scenario]").count() == 1
        if "CF0" in family:
            cf0_count += 1
            assert page.locator('[data-map-product="continuous_family_map.png"]').count() == 1
        else:
            assert page.locator('[data-map-product="sulcation_scenarios_map.png"]').count() == 1

    assert page.locator('td.is-best[data-scenario-eligible="false"]').count() == 0
    length_row = page.locator(".comparison-table tbody tr").filter(has_text="Comprimento publicado (km)")
    assert length_row.count() == 1
    assert "Não calculado" not in length_row.inner_text(), length_row.inner_text()
    assert length_row.locator(".is-best").count() == 0
    return {"scenario_count": len(scenarios), "ineligible_count": ineligible_count, "cf0_count": cf0_count}


def main() -> None:
    global BASE_URL
    args = parse_args()
    BASE_URL = args.base_url.rstrip("/")
    boundary_zip = ROOT / "platform_runtime" / "browser_inputs" / "Contorno.zip"
    terrain = ROOT / "dataset" / "DEM.tif"
    if not EDGE.is_file() or (not args.project_id and (not boundary_zip.is_file() or not terrain.is_file())):
        raise FileNotFoundError("Browser or E2E input fixture is unavailable")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(EDGE), headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        console_errors: list[str] = []
        page_errors: list[str] = []
        failed_requests: list[str] = []
        page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on("requestfailed", lambda request: failed_requests.append(f"{request.method} {request.url}: {request.failure}"))

        if args.project_id:
            project_id = args.project_id
            page.goto(f"{BASE_URL}/#/projects/{project_id}/results", wait_until="networkidle")
        else:
            page.goto(BASE_URL, wait_until="networkidle")
            page.locator("#new-project").wait_for(timeout=20_000)
            assert "Projetos de sistematização" in page.locator("main").inner_text()
            page.locator("#new-project").click()
            suffix = str(int(time.time()))[-6:]
            page.locator("#project-name").fill(f"QA Plataforma {suffix}")
            page.locator("#project-code").fill(f"QA-{suffix}")
            page.locator("#farm-name").fill("Fazenda de validacao")
            page.locator("#municipality").fill("Ribeirao Preto")
            page.locator("#state").select_option("SP")
            page.locator("#crs").fill("EPSG:31982")
            page.locator("#create-project-form [type=submit]").click()
            page.locator("#file-input").wait_for(timeout=20_000)
            project_id = page.url.split("/projects/", 1)[1].split("/", 1)[0]

            page.locator("#asset-kind").select_option("FIELD_BOUNDARIES")
            page.locator("#file-input").set_input_files(str(boundary_zip))
            page.locator("#start-upload").click()
            page.get_by_text("Contorno.zip", exact=True).last.wait_for(timeout=30_000)

            page.locator("#asset-kind").select_option("DEM")
            page.locator("#file-input").set_input_files(str(terrain))
            page.locator("#start-upload").click()
            page.get_by_text("DEM.tif", exact=True).last.wait_for(timeout=30_000)
            page.wait_for_timeout(1_000)
            page.locator("#declare-no-power").check(force=True)
            page.wait_for_timeout(800)

            page.locator('[data-go-step="configure"]').first.click()
            page.locator('[data-parameter-id="topography.field_id_column"]').wait_for(timeout=20_000)
            page.locator('[data-parameter-id="topography.field_id_column"]').fill("cd_upnivel")
            page.locator('[data-parameter-id="topography.resolution_m"]').fill("3.4")
            page.locator("#save-configuration").click()
            page.locator("#continue-products").wait_for(timeout=20_000)

            topography = page.locator('[data-product-id="TOPOGRAPHY_E0"]')
            assert "is-disabled" not in (topography.get_attribute("class") or "")
            page.locator("#continue-products").click()
            page.locator("#start-run").wait_for(timeout=20_000)
            page.locator("#start-run").click()
            page.locator("#open-run-results").wait_for(timeout=180_000)
            page.locator("#open-run-results").click()

        page.locator(".artifact-card").first.wait_for(timeout=30_000)
        artifact_count = page.locator(".artifact-card").count()
        assert artifact_count >= 10, artifact_count
        assert page.locator('.artifact-card[data-product-id="COMPLETE_DOSSIER"][data-artifact-format="PDF"]').count() == 1
        assert page.get_by_text("Dossie_Tecnico_Rodada_E0_CF0.pdf", exact=True).count() == 1
        assert page.locator('.artifact-card img[src*="/api/artifacts/"]').count() >= 2
        assert page.locator(".artifact-card a[download]").count() == artifact_count
        scenario_summary = verify_scenarios(page, project_id) if args.expect_scenarios else {}
        if args.expect_scenarios:
            output = ROOT / "platform_runtime" / "browser_checks"
            output.mkdir(parents=True, exist_ok=True)
            page.locator("#comparison-table").screenshot(path=str(output / "comparison-table-desktop.png"))
        desktop_geometry = page.evaluate(
            """() => ({width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
            cards: document.querySelectorAll('.artifact-card').length})"""
        )
        assert desktop_geometry["scrollWidth"] <= desktop_geometry["width"] + 1, desktop_geometry
        shot_variant = "scenarios" if args.expect_scenarios else "topography"
        desktop_shot = ROOT / "platform" / "web" / f"verified-live-{shot_variant}-desktop.png"
        page.screenshot(path=str(desktop_shot), full_page=True)

        mobile = browser.new_page(viewport={"width": 390, "height": 844})
        mobile_errors: list[str] = []
        mobile.on("pageerror", lambda error: mobile_errors.append(str(error)))
        mobile.goto(f"{BASE_URL}/#/projects/{project_id}/results", wait_until="networkidle")
        mobile.locator(".artifact-card").first.wait_for(timeout=30_000)
        mobile_geometry = mobile.evaluate(
            """() => ({width: innerWidth, scrollWidth: document.documentElement.scrollWidth,
            cards: document.querySelectorAll('.artifact-card').length})"""
        )
        assert mobile_geometry["scrollWidth"] <= mobile_geometry["width"] + 1, mobile_geometry
        if args.expect_scenarios:
            assert mobile.locator("[data-scenario-tab]").count() == scenario_summary["scenario_count"]
        mobile_shot = ROOT / "platform" / "web" / f"verified-live-{shot_variant}-mobile.png"
        mobile.screenshot(path=str(mobile_shot), full_page=True)
        mobile.close()
        browser.close()

    network_failures = [item for item in failed_requests if "fonts.googleapis.com" not in item and "fonts.gstatic.com" not in item]
    assert not page_errors, page_errors
    assert not mobile_errors, mobile_errors
    assert not console_errors, console_errors
    assert not network_failures, network_failures
    print(
        json.dumps(
            {
                "status": "PASS",
                "project_id": project_id,
                "artifact_count": artifact_count,
                **scenario_summary,
                "desktop": desktop_geometry,
                "mobile": mobile_geometry,
                "screenshots": [str(desktop_shot), str(mobile_shot)],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
