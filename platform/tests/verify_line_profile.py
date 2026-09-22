"""Exercise source profiles with intercepted QA data, never published farm data."""

from pathlib import Path

from playwright.sync_api import expect, sync_playwright


BASE = "http://127.0.0.1:8003"


def main():
    output = Path("platform_runtime/browser_checks")
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", headless=True
        )
        for name, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
            page = browser.new_page(viewport={"width": width, "height": height})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            layer = {"status": "READY", "name": "Linha QA", "size_bytes": 1000,
                     "source_url": "/qa-profile.json", "default_visible": True,
                     "spatial_metadata": {"format": "GEOJSON", "elevation_policy": "SOURCE_ROW_HEIGHTS",
                                          "scenario_key": "QA", "scenario_name": "Teste de perfil"}}
            payload = {"type": "FeatureCollection", "features": [{"type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[-48, -22], [-47.9999, -22], [-47.9997, -22]]},
                "properties": {"kind": "SULCATION_ROW", "id": "QA", "field_id": "TESTE",
                               "chainage_reference": "SOURCE_PROJECTED_METRIC_XY",
                               "source_chainages_m": [0, 10, 30], "source_elevations_m": [100, 101, 99]}}]}
            payload["features"][0]["properties"]["profile_reference"] = {
                "usage": "SCREENING_ALERT_ONLY", "parameter_id": "e0.reference_alert_grade_pct",
                "grade_alert_pct": 5, "request_id": "req-qa", "request_sha256": "a" * 64}
            payload["terrain_sha256"] = "b" * 64
            terrain_layer = {"status": "READY", "name": "Terreno QA", "size_bytes": 1000,
                             "source_url": "/qa-terrain.json", "default_visible": True,
                             "spatial_metadata": {"format": "TERRAIN_INSPECTION_MESH"}}
            terrain = {"type": "TerrainInspectionMesh", "source_sha256": "b" * 64,
                       "vertices": [[-48.0001, -22.0001, 100], [-47.9996, -22.0001, 100],
                                    [-47.9996, -21.9999, 100], [-48.0001, -21.9999, 100]],
                       "triangles": [[0, 1, 2], [0, 2, 3]]}
            page.route("**/api/runs/qa-profile/map-layers", lambda route: route.fulfill(json={
                "project_id": "qa", "ready_count": 2, "layers": [terrain_layer, layer]}))
            page.route("**/api/runs/qa-profile/scenarios", lambda route: route.fulfill(json={"items": []}))
            page.route("**/qa-profile.json", lambda route: route.fulfill(json=payload))
            page.route("**/qa-terrain.json", lambda route: route.fulfill(json=terrain))
            page.goto(f"{BASE}/map.html?run=qa-profile&scenario=QA", wait_until="networkidle")
            expect(page.locator("#scenario")).to_be_enabled()
            assert page.locator("#back").get_attribute("href").endswith("/results?run=qa-profile")
            canvas = page.locator("canvas")
            box = canvas.bounding_box()
            canvas.click(position={"x": box["width"] / 2, "y": box["height"] / 2})
            expect(page.locator("#line-profile")).to_be_visible()
            expect(page.locator("#selection")).to_contain_text("Maior greide entre vertices (%)")
            expect(page.locator("#selection")).to_contain_text("nao constitui validacao hidraulica")
            assert page.locator("#line-profile polyline").first.get_attribute("points") == "10,60 83.33333333333333,10 230,110"
            expect(page.locator(".profile-alert")).to_have_count(1)
            expect(page.locator("#selection")).to_contain_text("Referencia excedida")
            before = canvas.screenshot()
            page.locator("#profile-interval").select_option("0")
            assert canvas.screenshot() != before, "Selected alert must highlight the map"
            page.get_by_role("button", name="3D", exact=True).click()
            before_3d = canvas.screenshot()
            page.locator("#profile-interval").select_option("0")
            assert canvas.screenshot() != before_3d, "Selected alert must highlight the 3D mesh"
            page.screenshot(path=str(output / f"line-profile-3d-{name}.png"))
            page.get_by_role("button", name="2D", exact=True).click()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.locator("#line-profile").scroll_into_view_if_needed()
            page.screenshot(path=str(output / f"line-profile-{name}.png"))
            page.locator("#scenario").select_option("")
            expect(page.locator("#line-profile")).to_have_count(0)
            payload["features"][0]["properties"].pop("source_chainages_m")
            page.reload(wait_until="networkidle")
            page.locator("#scenario").select_option("QA")
            expect(page.locator("#scenario")).to_be_enabled()
            canvas.click(position={"x": box["width"] / 2, "y": box["height"] / 2})
            expect(page.locator("#selection")).to_contain_text("Indisponivel")
            expect(page.locator("#line-profile")).to_have_count(0)
            assert not errors, errors
            page.close()
        browser.close()
    print("Profile selection, history, legacy data and responsive layout passed (QA fixtures).")


if __name__ == "__main__":
    main()
