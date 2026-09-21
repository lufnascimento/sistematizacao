"""Exercise two real QA runs, history navigation and ZIP delivery in the browser."""

import argparse
import hashlib
import json
import time
import zipfile
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
            response = page.request.post(f"{base}/api/projects", data={"name": "QA - historico e entrega"})
            assert response.status == 201, response.text()
            project_id = response.json()["id"]
            runs = []
            for _ in range(2):
                response = page.request.post(f"{base}/api/projects/{project_id}/runs", data={"engine_id": "validate_uploads"})
                assert response.status == 202, response.text()
                run_id = response.json()["id"]
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    run = page.request.get(f"{base}/api/runs/{run_id}").json()
                    if run["status"] == "SUCCEEDED":
                        break
                    assert run["status"] in {"QUEUED", "RUNNING"}, run
                    time.sleep(.2)
                assert run["status"] == "SUCCEEDED", run
                runs.append(run_id)
            page.goto(f"{base}/#/runs/{runs[0]}")
            page.locator("#open-run-results").click()
            expect(page.locator("#result-run")).to_have_value(runs[0])
            assert f"run={runs[0]}" in page.url
            expect(page.locator("#download-delivery")).to_have_attribute("href", f"/api/runs/{runs[0]}/delivery")
            with page.expect_download() as downloaded:
                page.locator("#download-delivery").click()
            archive_path = output / "qa-result-delivery.zip"
            downloaded.value.save_as(archive_path)
            with zipfile.ZipFile(archive_path) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                assert manifest["run_id"] == runs[0]
                assert not manifest["guidance_authorized"]
                for entry in manifest["artifacts"]:
                    assert hashlib.sha256(archive.read(entry["archive_path"])).hexdigest() == entry["sha256"]
            page.locator("#result-run").select_option(runs[1])
            expect(page.locator("#download-delivery")).to_have_attribute("href", f"/api/runs/{runs[1]}/delivery")
            page.reload()
            expect(page.locator("#result-run")).to_have_value(runs[1])
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            page.screenshot(path=str(output / "result-delivery-mobile.png"), full_page=True)
            page.goto(f"{base}/#/projects/{project_id}/results?run=another-project-run")
            page.get_by_role("heading", name="Rodada indisponível neste projeto").wait_for()
            assert page.locator("#download-delivery").count() == 0
            assert page.locator(".artifact-card").count() == 0
            assert not errors, errors
            print(json.dumps({"status": "PASS", "project_id": project_id, "runs": runs,
                              "delivery_artifact_count": manifest["artifact_count"]}))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
