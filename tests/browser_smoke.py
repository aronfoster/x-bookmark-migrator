"""Run explicitly in CI: real browser, synthetic PNG/H.264, no remote accounts."""

import base64
import os
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
            output.write("annotations.private.json", {"category_overrides": {"101": "Reading"}})
            records = build([batch], output)
            legacy = records[1]["versions"][0]
            legacy.pop("folders")
            legacy["category"] = "membership-unknown"
            legacy["posts"][0].pop("author")
            legacy["posts"][0]["raw"].pop("author_id")
            # The archived reference may appear before the saved primary in the catalog.
            quoted = records[2]["versions"][0]["posts"]
            quoted.reverse()
            assert quoted[0]["id"] == "104"
            quoted[0]["raw"]["author_id"] = "901"
            quoted[0]["author"] = {"id": "901", "name": "Quoted Writer", "username": "quoted"}
            publish(output, records)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(
                    executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
                )
                page = browser.new_page()
                remote = []
                page.route(
                    "http://**", lambda route: (remote.append(route.request.url), route.abort())
                )
                page.route(
                    "https://**", lambda route: (remote.append(route.request.url), route.abort())
                )
                page.goto(output.path("index.html").as_uri())
                for pid in ("101", "102", "103"):
                    source = page.locator(f"#post-{pid}").get_by_role(
                        "link", name="Open original saved X post", exact=True
                    )
                    expect(source).to_be_visible()
                    expect(source).to_have_attribute("href", f"https://x.com/i/status/{pid}")

                def check_field(pid, label, value):
                    term = page.locator(f"#post-{pid}").get_by_text(label, exact=True)
                    expect(term.locator("+ dd")).to_have_text(value)
                    expect(term.locator("+ dd")).to_be_visible()

                check_field("101", "Original folder", "Synthetic category (10)")
                check_field("101", "Original category", "folder-10")
                check_field("101", "Effective category (owner-approved override)", "Reading")
                check_field("102", "Original folder", "Not recorded")
                check_field("102", "Original and effective category", "membership-unknown")
                check_field("102", "Saved primary author", "Not recorded")
                check_field("103", "Saved primary author", "Invented Author (@example)")
                check_field("103", "Original and effective category", "folder-10")
                page.locator("img").first.scroll_into_view_if_needed()
                expect(page.locator("img").first).to_have_js_property("naturalWidth", 1)
                page.locator("video").first.evaluate("v => v.load()")
                # Locator assertions work under the catalog's no-script CSP;
                # wait_for_function's string eval does not.
                expect(page.locator("video").first).to_have_js_property("readyState", 4)
                assert page.locator("video").first.evaluate("v => v.videoWidth") == 160
                assert page.locator("video").first.evaluate("v => v.duration") == 1
                for label in (
                    "Original folder: Synthetic category (10)",
                    "Owner-approved category: Reading",
                ):
                    group = page.locator("nav details").filter(
                        has=page.get_by_text(label, exact=True)
                    )
                    group.locator("summary").click()
                    group.get_by_role("link", name="101", exact=True).click()
                    assert page.url.endswith("#post-101")
                page.locator("article details summary").first.click()
                assert "Synthetic long-form saved prose." in page.locator("pre").first.inner_text()
                annotate(output, records, "101:2:video-1", role="incidental")
                page.reload()
                check_field("101", "Effective category (owner-approved override)", "Reading")
                assert page.locator('video[src*="101_101_02"]').count() == 0
                assert page.locator('video[src*="102_102_01"]').count() == 1
                assert not remote, "Static catalog attempted an external request"
                browser.close()
        finally:
            storage.close()
    print(
        "Synthetic author, folder, category, navigation, text, media and curation checks passed; "
        "no remote requests."
    )


if __name__ == "__main__":
    main()
