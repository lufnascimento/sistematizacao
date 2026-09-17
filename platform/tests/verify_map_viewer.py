"""Browser checks against a successful live run with one overflow layer."""

import argparse
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageChops
from playwright.sync_api import expect, sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", default="platform_runtime/browser_checks")
    parser.add_argument("--terrain", action="store_true")
    parser.add_argument("--contours", action="store_true")
    parser.add_argument("--boundaries", action="store_true")
    parser.add_argument("--scenarios", action="store_true")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", headless=True)
        for name, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
            page = browser.new_page(viewport={"width": width, "height": height})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(args.url, wait_until="networkidle", timeout=120000)
            toggle = page.get_by_role("checkbox").first
            toggle.wait_for()
            assert toggle.is_enabled()
            canvas = page.locator("canvas")
            boundary_toggle = None
            if args.scenarios:
                selector = page.get_by_label("Alternativa", exact=True)
                assert selector.is_visible()
                expect(selector).to_be_enabled(timeout=120000)
                layer_manifest = page.request.get(args.url.split("/map.html")[0] + "/api/runs/" + args.url.split("run=")[1].split("&")[0] + "/map-layers").json()
                requested = page.evaluate("performance.getEntriesByType('resource').map(item => item.name)")
                for layer in layer_manifest["layers"]:
                    if layer["status"] == "READY" and not layer["default_visible"]:
                        assert not any(url.endswith(layer["source_url"]) for url in requested), "Hidden diagnostic downloaded during initial load"
                options = selector.locator("option").evaluate_all("items => items.map(item => ({value: item.value, name: item.textContent}))")
                assert len(options) > 1
                selector.select_option("")
                baseline = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
                bounds = (0, 90, baseline.width, baseline.height - 40)
                for option in options[1:]:
                    selector.select_option(option["value"])
                    expect(selector).to_be_enabled(timeout=120000)
                    assert "CF0" not in option["name"] and "E0" not in option["name"], "Internal code used as scenario name"
                    # Diagnostics require an explicit choice and never imply approval.
                    for diagnostic in page.get_by_role("checkbox", name="Linhas de diagnostico").all():
                        if diagnostic.is_visible():
                            assert not diagnostic.is_checked()
                    alternative = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
                    assert ImageChops.difference(baseline.crop(bounds), alternative.crop(bounds)).getbbox(), f"Alternative has no visible rows: {option['name']}"
                selector.select_option("")
            if args.boundaries:
                boundary_toggle = page.get_by_role("checkbox", name="Limites dos talhoes", exact=True)
                for view in ("2D", "3D"):
                    page.get_by_role("button", name=view, exact=True).click()
                    with_boundary = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
                    boundary_toggle.uncheck()
                    without_boundary = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
                    bounds = (0, 90, with_boundary.width, with_boundary.height - 40)
                    assert ImageChops.difference(with_boundary.crop(bounds), without_boundary.crop(bounds)).getbbox(), f"Field outlines absent in {view}"
                    boundary_toggle.check()
                boundary_toggle.uncheck()
            contour_toggle = None
            if args.contours:
                contour_toggle = page.get_by_role("checkbox", name="Curvas de nivel do terreno", exact=True)
                assert contour_toggle.is_enabled()
                for view in ("2D", "3D"):
                    page.get_by_role("button", name=view, exact=True).click()
                    with_contours = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
                    contour_toggle.uncheck()
                    without_contours = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
                    assert ImageChops.difference(with_contours, without_contours).getbbox(), f"Contours absent in {view}"
                    contour_toggle.check()
                contour_toggle.uncheck()
            if args.terrain:
                page.get_by_role("button", name="3D", exact=True).click()
                assert page.get_by_role("button", name="3D", exact=True).get_attribute("aria-pressed") == "true"
            before = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
            toggle.uncheck()
            hidden = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
            diff = ImageChops.difference(before, hidden)
            # Exclude overlaid toolbar/footer and antialiasing-only edge pixels.
            mask = diff.convert("L").point(lambda value: 255 if value > 15 else 0)
            mask.paste(0, (0, 0, mask.width, 90))
            mask.paste(0, (0, mask.height - 40, mask.width, mask.height))
            diff = Image.composite(diff, Image.new("RGB", diff.size), mask)
            bbox = diff.getbbox()
            assert bbox, "Layer toggle did not change canvas pixels"
            toggle.check()
            page.get_by_role("button", name="Aproximar", exact=True).click()
            zoomed = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
            assert ImageChops.difference(before, zoomed).getbbox(), "Zoom did not change framing"
            page.get_by_role("button", name="Enquadrar camadas", exact=True).click()
            # Use a changed pixel to hit the rendered line without assuming its location.
            rows = sorted(range(bbox[1], bbox[3]), key=lambda y: abs(y - (bbox[1] + bbox[3]) / 2))
            columns = sorted(range(bbox[0], bbox[2]), key=lambda x: abs(x - (bbox[0] + bbox[2]) / 2))
            changed = next((x, y) for y in rows for x in columns if any(diff.getpixel((x, y))))
            canvas.click(position={"x": changed[0], "y": changed[1]})
            assert ("Cota interpolada" if args.terrain else "Comprimento original") in page.locator("#selection").inner_text()
            opacity = page.get_by_role("slider").first
            opacity.fill("0")
            transparent = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
            # Footer coordinates may change after picking; compare only the line's bounding box.
            assert not ImageChops.difference(transparent.crop(bbox), hidden.crop(bbox)).getbbox()
            opacity.fill("1")
            if args.terrain:
                before_rotation = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
                bounds = canvas.bounding_box()
                page.mouse.move(bounds["x"] + bounds["width"] * .5, bounds["y"] + bounds["height"] * .5)
                page.mouse.down()
                page.mouse.move(bounds["x"] + bounds["width"] * .6, bounds["y"] + bounds["height"] * .55, steps=10)
                page.mouse.up()
                after_rotation = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
                assert ImageChops.difference(before_rotation, after_rotation).getbbox(), "Orbit did not change canvas pixels"
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            assert not errors, errors
            if contour_toggle is not None:
                contour_toggle.check()
            if boundary_toggle is not None:
                boundary_toggle.check()
            if args.scenarios:
                page.get_by_label("Alternativa", exact=True).select_option(options[1]["value"])
            page.screenshot(path=str(output / f"map-viewer-{'terrain-' if args.terrain else ''}{name}.png"), full_page=True)
            print(name, "PASS: layer pixels, zoom, selection, opacity, layout")
            page.close()
        if args.contours:
            page = browser.new_page(viewport={"width": 1440, "height": 900})

            def mismatch_terrain(route):
                response = route.fetch()
                data = response.json()
                if data.get("type") == "FeatureCollection":
                    data["terrain_sha256"] = "different-terrain"
                route.fulfill(response=response, json=data)

            page.route("**/api/artifacts/*/download", mismatch_terrain)
            page.goto(args.url, wait_until="networkidle", timeout=120000)
            if args.scenarios:
                expect(page.get_by_label("Alternativa", exact=True)).to_be_enabled(timeout=120000)
                page.get_by_label("Alternativa", exact=True).select_option("")
            contour_toggle = page.get_by_role("checkbox", name="Curvas de nivel do terreno", exact=True)
            assert contour_toggle.is_enabled()
            assert "somente em planta" in page.locator("#layers").inner_text()
            page.get_by_role("button", name="3D", exact=True).click()
            canvas = page.locator("canvas")
            checked = Image.open(BytesIO(canvas.screenshot())).convert("RGB")
            scene_bounds = (0, 90, checked.width, checked.height - 40)
            checked = checked.crop(scene_bounds)
            contour_toggle.uncheck()
            unchecked = Image.open(BytesIO(canvas.screenshot())).convert("RGB").crop(scene_bounds)
            mismatch_diff = ImageChops.difference(checked, unchecked).convert("L").point(lambda value: 255 if value > 15 else 0)
            if mismatch_diff.getbbox():
                checked.save(output / "provenance-checked.png")
                unchecked.save(output / "provenance-unchecked.png")
            assert not mismatch_diff.getbbox(), "Mismatched terrain heights leaked into 3D"
            contour_toggle.check()
            rechecked = Image.open(BytesIO(canvas.screenshot())).convert("RGB").crop(scene_bounds)
            assert not ImageChops.difference(checked, rechecked).convert("L").point(lambda value: 255 if value > 15 else 0).getbbox(), "Toggle bypassed vertical reference gate"
            page.close()
            print("provenance PASS: mismatched terrain cannot position contours in 3D")
        page = browser.new_page()
        page.goto(args.url.split("?")[0], wait_until="networkidle")
        assert page.locator("#empty").is_visible()
        assert "Selecione" in page.locator("#empty").inner_text()
        browser.close()


if __name__ == "__main__":
    main()
