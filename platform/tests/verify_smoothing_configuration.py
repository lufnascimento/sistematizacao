"""Verify UI save/reload and immutable sigma requests on a dedicated QA project."""

import argparse
import json
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8003")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    output = Path(__file__).resolve().parents[2] / "platform_runtime" / "browser_checks"
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
        browser = playwright.chromium.launch(**({"executable_path": str(edge)} if edge.exists() else {}))
        try:
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            created = page.request.post(f"{base}/api/projects", data={
                "name": "QA - suavizacao configuravel", "crs": "EPSG:31982",
            })
            assert created.status == 201, created.text()
            project_id = created.json()["id"]
            endpoint = f"{base}/api/projects/{project_id}/configuration"
            route = f"{base}/#/projects/{project_id}/configure"
            selector = '[data-parameter-id="terrain.smoothing_sigma_m"]'
            page.goto(route)
            expect(page.locator(selector)).to_have_value("4")
            assert page.locator('[data-parameter-id="terrain.smoothing_radius_m"]').count() == 0
            request_ids = []
            for sigma in (0, 2.5):
                page.locator(selector).fill(str(sigma))
                with page.expect_response(lambda response: response.url == endpoint and response.request.method == "PUT") as saved:
                    page.locator("#save-configuration").click()
                assert saved.value.ok, saved.value.text()
                assert saved.value.json()["sulcation"]["terrain_smoothing_sigma_m"] == sigma
                page.locator("#continue-products").wait_for()
                request = page.request.post(f"{base}/api/projects/{project_id}/requests", data={
                    "name": f"QA sigma {sigma}", "product_ids": ["SULCATION_E0", "CF0_CONTINUOUS"],
                })
                assert request.status == 201, request.text()
                assert request.json()["configuration_snapshot"]["sulcation"]["terrain_smoothing_sigma_m"] == sigma
                request_ids.append(request.json()["id"])
                page.goto(route)
                expect(page.locator(selector)).to_have_value(str(sigma))
            for viewport, name in (({"width": 1440, "height": 1000}, "desktop"), ({"width": 390, "height": 844}, "mobile")):
                page.set_viewport_size(viewport)
                page.locator(selector).scroll_into_view_if_needed()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), name
                page.screenshot(path=str(output / f"configuration-sigma-{name}.png"))
            assert not errors, errors
            print(json.dumps({"status": "PASS", "project_id": project_id, "requests": request_ids,
                              "verified_sigma_m": [0, 2.5], "engine_runs_started": 0}))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
