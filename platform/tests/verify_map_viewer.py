"""Browser checks against a successful live run with one overflow layer."""

import argparse
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", default="platform_runtime/browser_checks")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", headless=True)
        for name, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
            page = browser.new_page(viewport={"width": width, "height": height})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(args.url, wait_until="networkidle")
            toggle = page.get_by_role("checkbox").first
            toggle.wait_for()
            assert toggle.is_enabled()
            canvas = page.locator("canvas")
            before = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
            toggle.uncheck()
            hidden = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
            diff = ImageChops.difference(before, hidden)
            bbox = diff.getbbox()
            assert bbox, "Layer toggle did not change canvas pixels"
            toggle.check()
            page.get_by_role("button", name="Aproximar", exact=True).click()
            zoomed = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
            assert ImageChops.difference(before, zoomed).getbbox(), "Zoom did not change framing"
            page.get_by_role("button", name="Enquadrar camadas", exact=True).click()
            # Use a changed pixel to hit the rendered line without assuming its location.
            changed = next((x, y) for y in range(bbox[1], bbox[3]) for x in range(bbox[0], bbox[2]) if any(diff.getpixel((x, y))))
            canvas.click(position={"x": changed[0], "y": changed[1]})
            assert "Comprimento original" in page.locator("#selection").inner_text()
            opacity = page.get_by_role("slider")
            opacity.fill("0")
            transparent = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
            # Footer coordinates may change after picking; compare only the line's bounding box.
            assert not ImageChops.difference(transparent.crop(bbox), hidden.crop(bbox)).getbbox()
            opacity.fill("1")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            assert not errors, errors
            page.screenshot(path=str(output / f"map-viewer-{name}.png"), full_page=True)
            print(name, "PASS: layer pixels, zoom, selection, opacity, layout")
            page.close()
        page = browser.new_page()
        page.goto(args.url.split("?")[0], wait_until="networkidle")
        assert page.locator("#empty").is_visible()
        assert "Selecione" in page.locator("#empty").inner_text()
        browser.close()


if __name__ == "__main__":
    main()
