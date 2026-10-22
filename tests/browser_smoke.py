"""Run explicitly in CI: real browser, synthetic PNG/H.264, no remote accounts."""

import base64
import tempfile
from pathlib import Path

import test_archive
from playwright.sync_api import expect, sync_playwright
from test_catalog import corpus

from x_bookmark_migrator.catalog import annotate, build, publish


def main():
    # Generated with ffmpeg -f lavfi -i color=c=blue:s=160x120:d=1
    # -c:v libx264 -pix_fmt yuv420p. One second of synthetic blue, no audio.
    test_archive.MP4 = base64.b64decode(Path(__file__).with_name("catalog_video.b64").read_bytes())
    with tempfile.TemporaryDirectory(prefix="catalog-browser-") as temporary:
        root = Path(temporary)
        storage = test_archive.storage.__wrapped__(root)
        try:
            batch, output, _, _ = corpus.__wrapped__(next(storage), root)
            records = build([batch], output)
            publish(output, records)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page()
                remote = []
                page.route(
                    "http://**", lambda route: (remote.append(route.request.url), route.abort())
                )
                page.route(
                    "https://**", lambda route: (remote.append(route.request.url), route.abort())
                )
                page.goto(output.path("index.html").as_uri())
                page.locator("img").first.scroll_into_view_if_needed()
                expect(page.locator("img").first).to_have_js_property("naturalWidth", 1)
                page.locator("video").first.evaluate("v => v.load()")
                # Locator assertions work under the catalog's no-script CSP;
                # wait_for_function's string eval does not.
                expect(page.locator("video").first).to_have_js_property("readyState", 4)
                assert page.locator("video").first.evaluate("v => v.videoWidth") == 160
                assert page.locator("video").first.evaluate("v => v.duration") == 1
                page.locator("nav summary").click()
                page.locator('nav a[href="#post-101"]').click()
                page.locator("article details summary").first.click()
                assert "Synthetic long-form saved prose." in page.locator("pre").first.inner_text()
                annotate(output, records, "101:2:video-1", role="incidental")
                page.reload()
                assert page.locator('video[src*="101_101_02"]').count() == 0
                assert page.locator('video[src*="102_102_01"]').count() == 1
                assert not remote, "Static catalog attempted an external request"
                browser.close()
        finally:
            storage.close()
    print(
        "Synthetic browser image/video, text, category and hide checks passed; no remote requests."
    )


if __name__ == "__main__":
    main()
