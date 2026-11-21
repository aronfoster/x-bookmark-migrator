"""Synthetic archive -> restore -> catalog, never a live account or credential."""

import copy
import json
import os
import sys

import httpx
import pytest
from test_archive import includes, inventory, media_http, photo, post, storage, video  # noqa: F401

from x_bookmark_migrator import cli
from x_bookmark_migrator.archive import archive_batch, restore_batch
from x_bookmark_migrator.catalog import (
    annotate,
    build,
    link_raindrop,
    lookup,
    post_id,
    primary_post_text,
    publish,
    raindrop_matches,
    read_catalog,
    render,
)
from x_bookmark_migrator.core import PilotError, Store
from x_bookmark_migrator.drive import digest, encode


def test_primary_text_does_not_substitute_referenced_post():
    posts = [{"id": "102", "text": "Quoted text"}, {"id": "101", "text": "Primary text"}]
    assert primary_post_text(posts, "101") == "Primary text"
    for invalid in (posts[:1], posts + [posts[1]], [{"id": "101", "text": None}]):
        with pytest.raises(PilotError):
            primary_post_text(invalid, "101")


def test_raindrop_link_survives_rebuild_and_preserves_annotations(corpus):
    batch, output, restored, _ = corpus
    before = {p.name: p.read_bytes() for p in restored.root.iterdir()}
    records = build([batch], output)
    key = records[0]["versions"][0]["media"][0]["asset_id"]
    annotate(output, records, key, description="Keep this annotation")
    receipt = {
        "schema_version": 1,
        "source_post_id": "101",
        "source_post_url": "https://x.com/i/status/101",
        "primary_link": "https://x.com/i/status/101",
        "raindrop_id": 42,
        "collection_id": -1,
        "verified_at": "2026-01-01T00:00:00Z",
        "response_sha256": "a" * 64,
    }
    link_raindrop(output, read_catalog(output), receipt)
    rebuilt = build([batch], output)
    assert rebuilt == read_catalog(output)
    assert rebuilt[0]["raindrop_url"] == "https://app.raindrop.io/my/0/item/42"
    assert "Open bookmark in Raindrop" in render(rebuilt)
    assert "raindrop_url" not in rebuilt[1]
    assert output.read("annotations.private.json")[key]["description"] == "Keep this annotation"
    assert {p.name: p.read_bytes() for p in restored.root.iterdir()} == before
    for changed in (
        {
            "source_post_id": "104",
            "source_post_url": "https://x.com/i/status/104",
            "primary_link": "https://x.com/i/status/104",
        },
        {"primary_link": "https://x.com/i/status/102"},
        {"raindrop_id": True},
        {"raindrop_id": 43},
    ):
        with pytest.raises(PilotError):
            link_raindrop(output, rebuilt, {**receipt, **changed})
    assert rebuilt == read_catalog(output)


@pytest.fixture
def corpus(storage, tmp_path):  # noqa: F811
    state, drive, server, _ = storage
    inv = inventory(
        state,
        [
            post(
                "101",
                note_post={"text": "Synthetic long-form saved prose. " * 1200},
                attachments={"media_keys": ["photo-1", "video-1"]},
            ),
            post("102", text="🔥 #reaction", attachments={"media_keys": ["video-1"]}),
            post("103", referenced_posts=[{"type": "quoted", "id": "104"}]),
        ],
        includes([photo(), video("animated_gif")], [post("104", text="Synthetic quoted text")]),
    )
    with httpx.Client(transport=httpx.MockTransport(media_http)) as media:
        summary = archive_batch(inv, drive, media)
    receipt = state.read(summary["private_receipt"])
    restored = Store(tmp_path / "restored")
    restore_batch(drive, receipt["manifest"]["file_id"], restored, receipt["manifest"]["sha256"])
    output = Store(tmp_path / "catalog")
    batch = (restored.root, state.path(summary["private_receipt"]))
    return batch, output, restored, receipt


@pytest.mark.parametrize(
    "value",
    [
        "101",
        "https://twitter.com/old/status/101?s=20",
        "http://www.twitter.com/new/status/101/",
        "https://x.com/renamed/status/101",
        "https://x.com/i/status/101#fragment",
    ],
)
def test_exact_identity(value):
    assert post_id(value) == "101"


@pytest.mark.parametrize(
    "value",
    [
        101,
        "https://x.com/old/status/1010/extra",
        "x101",
        "https://x.com.evil.invalid/a/status/101",
        "https://evil.invalid/x.com/a/status/101",
        "https://x.com@evil.invalid/a/status/101",
        "https://x.com:443/a/status/101",
        "https://x.com/a/status/１０１",
    ],
)
def test_identity_rejects_ambiguous_or_foreign_links(value):
    with pytest.raises(PilotError):
        post_id(value)


def test_roundtrip_lookup_long_text_media_and_idempotent_rerun(corpus):
    batch, output, restored, receipt = corpus
    before = {p.name: p.read_bytes() for p in restored.root.iterdir()}
    records = build([batch, batch], output)
    publish(output, records)
    assert len(records) == 3
    result = lookup(read_catalog(output), "https://twitter.com/renamed/status/101?s=20")[0]
    assert len(result["versions"]) == 1
    version = result["versions"][0]
    assert len(version["posts"][0]["text"]) > 10000
    assert version["folders"][0]["names"] == ["Synthetic category"]
    assert version["manifest"] == receipt["manifest"]
    assert {a["logical_type"] for a in version["media"]} == {"photo", "animated_gif"}
    assert all(a["availability"] == "local_hash_verified" for a in version["media"])
    assert all(
        a["archive_url"].startswith("https://drive.google.com/file/d/") for a in version["media"]
    )
    assert all(r["deletion_eligible"] is False for r in records)
    original = output.path("catalog.jsonl").read_bytes()
    publish(output, build([batch], output))
    assert output.path("catalog.jsonl").read_bytes() == original
    assert {p.name: p.read_bytes() for p in restored.root.iterdir()} == before
    assert lookup(records, "104")[0]["post_id"] == "103"
    with pytest.raises(PilotError, match="No exact"):
        lookup(records, "10")


def test_per_attachment_hide_describe_rebuild_unhide(corpus):
    batch, output, restored, _ = corpus
    records = build([batch], output)
    annotate(
        output, records, "101:2:video-1", role="incidental", description="Synthetic researching GIF"
    )
    hidden = lookup(read_catalog(output), "101", "101:2:video-1")[0]
    assert hidden["browse_visibility"] == "hidden"
    assert hidden["description"] == "Synthetic researching GIF"
    assert hidden["availability"] == "local_hash_verified"
    assert restored.path("101_101_02.mp4").exists()
    page = output.path("index.html").read_text()
    assert "101_101_02.mp4" not in page
    assert "Synthetic researching GIF" not in page
    assert "101_101_01.png" in page and "102_102_01.mp4" in page
    assert "Synthetic long-form saved prose." in page
    publish(output, build([batch], output))
    assert lookup(read_catalog(output), "101", "101:2:video-1")[0]["browse_visibility"] == "hidden"
    annotate(output, read_catalog(output), "101:2:video-1", visibility="ordinary")
    assert "101_101_02.mp4" in output.path("index.html").read_text()
    with pytest.raises(PilotError, match="Unknown exact"):
        annotate(output, records, "101:9:missing", visibility="hidden")


def test_missing_media_keeps_description_and_historical_receipt(corpus):
    batch, output, restored, _ = corpus
    restored.path("101_101_02.mp4").unlink()
    records = build([batch], output)
    asset = lookup(records, "101", "101:2:video-1")[0]
    assert asset["availability"] == "missing_local"
    assert "archive_url" in asset and "local_url" not in asset
    assert records[0]["versions"][0]["archive_status"] == "bytes_verified"
    assert "101_101_02.mp4" not in render(records)


@pytest.mark.parametrize("filename", ["manifest.jsonl", "101.json", "101_101_01.png"])
def test_corrupt_restored_bytes_do_not_replace_catalog(corpus, filename):
    batch, output, restored, _ = corpus
    publish(output, build([batch], output))
    previous = output.path("catalog.jsonl").read_bytes()
    restored.path(filename).write_bytes(b"synthetic corruption")
    with pytest.raises(PilotError, match="differs"):
        build([batch], output)
    assert output.path("catalog.jsonl").read_bytes() == previous


def rewrite_snapshot(batch, restored, receipt, mutate):
    """New synthetic independently hashed version, not tampering with test expectations."""
    rows = [json.loads(line) for line in restored.path("manifest.jsonl").read_bytes().splitlines()]
    row = rows[0]
    payload = json.loads(restored.path("101.json").read_bytes())
    mutate(row, payload)
    raw_payload = encode(payload)
    restored.path("101.json").write_bytes(raw_payload)
    row["payload"].update(sha256=digest(raw_payload), size_bytes=len(raw_payload))
    raw = b"".join(encode(r) for r in rows)
    restored.path("manifest.jsonl").write_bytes(raw)
    receipt["manifest"].update(sha256=digest(raw), size_bytes=len(raw))
    batch[1].write_bytes(encode(receipt))


def test_conflicting_multiple_folder_names_are_preserved(corpus):
    batch, output, restored, receipt = corpus

    def mutate(row, payload):
        for obj in (row, payload):
            obj.update(
                folders=[
                    {"id": "10", "names": ["Old", "Renamed"]},
                    {"id": "11", "names": ["Another category"]},
                ],
                category="category-conflict",
                archive_status="blocked",
                blockers=["blocked_category_conflict"],
            )

    rewrite_snapshot(batch, restored, receipt, mutate)
    records = build([batch], output)
    assert records[0]["versions"][0]["folders"][1]["id"] == "11"
    assert "Old / Renamed" in render(records)
    assert "blocked_category_conflict" in render(records)


def test_association_mismatch_is_rejected_even_with_valid_hashes(corpus):
    batch, output, restored, receipt = corpus
    rewrite_snapshot(batch, restored, receipt, lambda r, p: p.update(category="different"))
    with pytest.raises(PilotError, match="association"):
        build([batch], output)


def test_html_escapes_source_and_annotation_text(corpus):
    batch, output, _, _ = corpus
    records = build([batch], output)
    records[0]["versions"][0]["posts"][0]["text"] = '<script>alert("fixture")</script>'
    records[0]["versions"][0]["folders"][0]["names"] = ['<img src=x onerror="fixture">']
    annotate(output, records, "101:1:photo-1", description="<script>synthetic</script>")
    page = output.path("index.html").read_text()
    assert "<script>" not in page and "<img src=x" not in page
    assert "&lt;script&gt;synthetic" in page
    assert "default-src 'none'" in page


def test_raindrop_alias_dedupe_preserves_all_metadata_and_rerun():
    existing = {
        "_id": 7,
        "link": "https://twitter.com/old/status/101?s=20",
        "note": "Synthetic owner note",
        "tags": ["keep"],
        "collection": {"$id": 9},
    }
    page = {"result": True, "items": [existing, {"_id": 8, "link": "https://x.com/i/status/1010"}]}
    original = copy.deepcopy(page)
    assert raindrop_matches("https://x.com/new/status/101", [page, page]) == [existing]
    assert raindrop_matches("101", [page]) == [existing]
    assert page == original
    assert raindrop_matches("102", [page]) == []
    with pytest.raises(PilotError):
        raindrop_matches("101", [{"result": False, "error": "synthetic_429"}])


def test_cli_needs_no_accounts_or_http(corpus, monkeypatch, capsys):
    batch, output, _, _ = corpus

    def forbidden(*args, **kwargs):
        pytest.fail("Offline catalog attempted to initialize an HTTP client")

    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.delenv("X_CLIENT_ID", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "x-migrator",
            "catalog-build",
            "--catalog-dir",
            str(output.root),
            "--batch",
            str(batch[0]),
            str(batch[1]),
        ],
    )
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["network_calls"] == 0
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "x-migrator",
            "catalog-lookup",
            "--catalog-dir",
            str(output.root),
            "--post",
            "https://twitter.com/example/status/101",
        ],
    )
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["matches"][0]["post_id"] == "101"
    for name in ("catalog.jsonl", "index.html"):
        assert os.stat(output.path(name)).st_mode & 0o077 == 0


def test_symlink_and_public_input_rejected(corpus, tmp_path):
    batch, output, restored, _ = corpus
    media = restored.path("101_101_01.png")
    media.chmod(0o644)
    with pytest.raises(PilotError, match="owner-only"):
        build([batch], output)
    media.chmod(0o600)
    other = tmp_path / "outside"
    media.rename(other)
    media.symlink_to(other)
    with pytest.raises(PilotError, match="owner-only"):
        build([batch], output)


def test_distinct_snapshots_remain_under_one_post_and_order_is_stable(corpus, tmp_path):
    import shutil

    batch, output, restored, receipt = corpus
    other = tmp_path / "other-restored"
    shutil.copytree(restored.root, other)
    other_receipt = tmp_path / "other-receipt.private.json"
    other_receipt.write_bytes(encode(receipt))
    other_receipt.chmod(0o600)
    second = (other, other_receipt)

    def mutate(row, payload):
        for obj in (row, payload):
            obj["folders"][0]["names"] = ["Later observed name"]

    rewrite_snapshot(second, Store(other), copy.deepcopy(receipt), mutate)
    records = build([batch, second], output)
    assert len(records) == 3
    assert len(records[0]["versions"]) == 2
    assert {v["folders"][0]["names"][0] for v in records[0]["versions"]} == {
        "Synthetic category",
        "Later observed name",
    }
    assert records == build([second, batch, second], output)


def test_not_archived_asset_is_describable_without_false_link(corpus):
    batch, output, restored, receipt = corpus

    def mutate(row, payload):
        payload["media"][0].pop("storage")
        payload["media"][0]["blocked"] = "synthetic_failure"
        for obj in (row, payload):
            obj.update(archive_status="blocked", blockers=["blocked_synthetic_failure"])

    rewrite_snapshot(batch, restored, receipt, mutate)
    records = build([batch], output)
    asset = lookup(records, "101", "101:1:photo-1")[0]
    assert asset["availability"] == "not_archived"
    assert "archive_url" not in asset
    assert "101_101_01.png" not in render(records)


def test_raindrop_conflicting_snapshot_is_not_silently_selected():
    item = {"_id": 1, "link": "https://twitter.com/example/status/101", "note": "first"}
    with pytest.raises(PilotError, match="Conflicting"):
        raindrop_matches(
            "101",
            [
                {"result": True, "items": [item]},
                {"result": True, "items": [{**item, "note": "second"}]},
            ],
        )


def test_annotation_and_raindrop_cli_paths(corpus, monkeypatch, capsys):
    batch, output, _, _ = corpus
    publish(output, build([batch], output))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "x-migrator",
            "catalog-annotate",
            "--catalog-dir",
            str(output.root),
            "--asset-id",
            "101:1:photo-1",
            "--visibility",
            "hidden",
        ],
    )
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["annotation_updated"]
    assert "101_101_01.png" not in output.path("index.html").read_text()
    output.write("raindrop.private.json", {"result": True, "items": []})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "x-migrator",
            "catalog-raindrop-match",
            "--post",
            "101",
            "--page-file",
            str(output.path("raindrop.private.json")),
        ],
    )
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "matches": [],
        "coverage": "not_established",
        "create_authorized": False,
    }


def test_source_sidecar_immutable_archives_and_distinct_reference_links(corpus):
    batch, output, restored, _ = corpus
    originals = {p.name: p.read_bytes() for p in restored.root.iterdir()}
    records = build([batch], output)
    publish(output, records)
    sidecar = output.path("source-links.jsonl").read_bytes()
    rows = [json.loads(line) for line in sidecar.splitlines()]
    assert [r["source_post_id"] for r in rows] == ["101", "102", "103"]
    assert all(
        r["source_post_url"] == f"https://x.com/i/status/{r['source_post_id']}" for r in rows
    )
    quote = lookup(read_catalog(output), "104")[0]
    assert quote["source_post_id"] == "103"
    assert quote["source_post_url"] == "https://x.com/i/status/103"
    child = next(p for p in quote["versions"][0]["posts"] if p["id"] == "104")
    assert child["source_post_url"] == "https://x.com/i/status/104"
    page = output.path("index.html").read_text()
    assert page.count("Open original saved X post</a>") == 3
    assert "Referenced post on X</a>" in page
    publish(output, build([batch], output))
    assert output.path("source-links.jsonl").read_bytes() == sidecar
    assert {p.name: p.read_bytes() for p in restored.root.iterdir()} == originals


def test_category_override_and_media_curation_survive_rebuild(corpus):
    batch, output, _, _ = corpus
    output.write(
        "annotations.private.json",
        {
            "category_overrides": {"101": "Synthetic owner override"},
            "101:1:photo-1": {"browse_visibility": "hidden", "description": "Keep me"},
        },
    )
    publish(output, build([batch], output))
    record = read_catalog(output)[0]
    assert record["category_override"] == "Synthetic owner override"
    assert record["versions"][0]["folders"][0]["names"] == ["Synthetic category"]
    assert record["versions"][0]["media"][0]["description"] == "Keep me"
    assert record["versions"][0]["media"][0]["browse_visibility"] == "hidden"
    assert "Synthetic owner override" in output.path("index.html").read_text()
    original = output.path("catalog.jsonl").read_bytes()
    publish(output, build([batch], output))
    assert output.path("catalog.jsonl").read_bytes() == original


def test_legacy_catalog_lookup_gets_source_links_without_rewriting(corpus):
    batch, output, _, _ = corpus
    records = build([batch], output)
    for r in records:
        r.pop("source_post_id")
        r.pop("source_post_url")
    raw = b"".join(encode(r) for r in records)
    output.path("catalog.jsonl").write_bytes(raw)
    output.path("catalog.jsonl").chmod(0o600)
    result = read_catalog(output)
    assert result[0]["source_post_id"] == "101"
    assert output.path("catalog.jsonl").read_bytes() == raw


def test_unsupported_x_link_cannot_be_skipped_in_saved_raindrop_matching():
    with pytest.raises(PilotError):
        raindrop_matches(
            "101",
            [{"result": True, "items": [{"_id": 8, "link": "https://mobile.x.com/unsupported"}]}],
        )


def test_reader_check_curation_sources_media_and_no_writes(corpus, monkeypatch):
    from x_bookmark_migrator.catalog import check_publication
    from x_bookmark_migrator.publication import PublicationReader, PublicationStore

    batch, output, restored, _ = corpus
    notes = {
        "category_overrides": {"101": "Kept category"},
        "101:1:photo-1": {"browse_visibility": "hidden", "description": "Kept caption"},
    }
    output.write("annotations.private.json", notes)
    output.root.chmod(0o2750)
    writer = PublicationStore(output.root)
    publish(writer, build([batch], writer))
    before = {p: p.read_bytes() for root in (output.root, restored.root) for p in root.iterdir()}
    # The reader path must not consult caller ownership or acquire the writer lock.
    monkeypatch.setattr(os, "getuid", lambda: 45678)
    monkeypatch.setattr(Store, "locked", lambda *_: pytest.fail("reader opened writer lock"))
    reader = PublicationReader(output.root)
    result = check_publication(reader, expected_records=3, expected_media=3, annotations=notes)
    assert result["verified"] and result["annotations_checked"]
    assert read_catalog(reader)[0]["category_override"] == "Kept category"
    assert before == {p: p.read_bytes() for p in before}
    assert not output.path("lock").exists()


@pytest.mark.parametrize(
    "fault", ["source", "sidecar", "html", "media", "category", "link", "count", "symlink"]
)
def test_reader_check_rejects_corrupt_or_mismatched_publication(corpus, fault):
    from x_bookmark_migrator.catalog import check_publication
    from x_bookmark_migrator.publication import PublicationReader

    batch, output, restored, _ = corpus
    records = build([batch], output)
    if fault == "category":
        records[0]["category_override"] = "Stale override"
    if fault == "link":
        records[0]["versions"][0]["media"][0]["archive_url"] = "https://example.invalid/"
    publish(output, records)
    if fault == "source":
        records[0]["source_post_url"] = "https://x.com/i/status/104"
        output.path("catalog.jsonl").write_bytes(b"".join(encode(r) for r in records))
    elif fault in ("sidecar", "html"):
        output.path("source-links.jsonl" if fault == "sidecar" else "index.html").write_bytes(
            b"stale"
        )
    elif fault == "media":
        restored.path("101_101_01.png").write_bytes(b"corrupt")
    elif fault == "symlink":
        path = output.path("source-links.jsonl")
        path.rename(output.path("old"))
        path.symlink_to(output.path("old"))
    with pytest.raises(PilotError):
        check_publication(
            PublicationReader(output.root),
            expected_records=2 if fault == "count" else 3,
            annotations={},
        )


def test_reader_cli_uses_no_lock_and_checks_actual_read_errors(corpus, monkeypatch, capsys):
    from x_bookmark_migrator.publication import PublicationReader

    batch, output, _, _ = corpus
    publish(output, build([batch], output))
    for extra in (
        ["catalog-check", "--expected-records", "3", "--expected-media", "3"],
        ["catalog-lookup", "--post", "101"],
    ):
        monkeypatch.setattr(sys, "argv", ["x-migrator", *extra, "--catalog-dir", str(output.root)])
        assert cli.main() == 0
        capsys.readouterr()
    assert not output.path("lock").exists()
    monkeypatch.setattr(
        PublicationReader, "bytes", lambda *_: (_ for _ in ()).throw(PermissionError())
    )
    assert cli.main() == 1


def test_reader_check_detects_generation_change(corpus, monkeypatch):
    from x_bookmark_migrator.catalog import check_publication
    from x_bookmark_migrator.publication import PublicationReader

    batch, output, _, _ = corpus
    publish(output, build([batch], output))
    reader = PublicationReader(output.root)
    read = reader.bytes
    calls = 0

    def changing(name):
        nonlocal calls
        calls += 1
        return b"changed" if calls == 4 else read(name)

    monkeypatch.setattr(reader, "bytes", changing)
    with pytest.raises(PilotError, match="changed during"):
        check_publication(reader, expected_records=3)


@pytest.mark.parametrize("mode", [0o660, 0o644, 0o666])
def test_reader_rejects_exposed_or_writable_projection(corpus, mode):
    from x_bookmark_migrator.publication import PublicationReader

    batch, output, _, _ = corpus
    publish(output, build([batch], output))
    output.path("catalog.jsonl").chmod(mode)
    with pytest.raises(PilotError):
        PublicationReader(output.root).bytes("catalog.jsonl")
