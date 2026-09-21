"""Verify that API failures never silently replace client data with demo data."""

import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8003")
    base = parser.parse_args().base_url.rstrip("/")
    with sync_playwright() as playwright:
        edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
        browser = playwright.chromium.launch(**({"executable_path": str(edge)} if edge.exists() else {}))
        try:
            page = browser.new_page()
            page.route("**/api/**", lambda route: route.abort())
            page.goto(base)
            page.get_by_role("heading", name="Conexão com a API indisponível").wait_for()
            assert page.locator("#new-project").count() == 0
            assert page.locator("#connection-banner").is_hidden()
            assert page.evaluate("async () => (await import('/api.js')).api.mode") == "unavailable"
            page.unroute("**/api/**")
            page.locator("#retry-api").click()
            page.locator("#new-project").wait_for()
            assert page.evaluate("async () => (await import('/api.js')).api.mode") == "live"
            page.close()

            denied = browser.new_page()
            denied.route("**/api/**", lambda route: route.fulfill(status=401, json={"detail": "Unauthorized"}))
            denied.goto(base)
            denied.get_by_role("heading", name="Conexão com a API indisponível").wait_for()
            assert denied.evaluate("async () => (await import('/api.js')).api.mode") == "unavailable"
            denied.close()

            demo = browser.new_page()
            demo.add_init_script("window.__TERRAFLUX_DEMO__ = true")
            demo.route("**/api/**", lambda route: route.abort())
            demo.goto(base)
            demo.locator("#new-project").wait_for()
            expect(demo.locator("#connection-banner")).to_be_visible()
            assert demo.evaluate("async () => (await import('/api.js')).api.mode") == "mock"
            print("PASS: network failure, denied access, reconnection and explicit demo")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
