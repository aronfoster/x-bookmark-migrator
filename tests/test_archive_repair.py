"""Successor archive rehearsal using synthetic CDN and Drive services."""

import argparse
import json
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from test_archive import (
    includes,
    inventory,
    manifest,
    manifest_hash,
    media_http,
    photo,
    post,
    storage,  # noqa: F401 -- pytest fixture
    video,
)

from x_bookmark_migrator import archive_cli, archive_repair
from x_bookmark_migrator.archive import archive_batch, plan_post, restore_batch
from x_bookmark_migrator.backlog import archive_anchors
from x_bookmark_migrator.catalog import build, publish, read_catalog, restored_media_filename
from x_bookmark_migrator.core import PilotError, Store
from x_bookmark_migrator.drive import Drive, digest, encode
from x_bookmark_migrator.publication import PublicationStore
from x_bookmark_migrator.worker import Worker


@pytest.mark.parametrize("reason", ["media_count", "byte_limit"])
def test_successor_from_restore_without_inventory(tmp_path, storage, monkeypatch, reason):  # noqa: F811
    store, drive, server, http = storage
    media = [photo(), video()] if reason == "byte_limit" else [photo(str(i)) for i in range(20)]
    keys = [m["media_key"] for m in media]
    if reason == "byte_limit":
        origin = post("102", attachments={"media_keys": keys})
        primary = post(attachments={"media_keys": keys, "media_source_tweet_id": ["102"]})
        inv = inventory(store, [primary], includes(media, [origin]))
    else:
        inv = inventory(store, [post(attachments={"media_keys": keys})], includes(media))
        evidence_name = inv.state["posts"]["101"]["expansions_ref"]
        evidence = store.read(evidence_name)
        evidence["reference_errors"] = [{"resource_type": "tweet", "resource_id": "999"}]
        store.write(evidence_name, evidence)

    def old_cdn(request):
        if request.url.host == "video.twimg.com":
            return httpx.Response(200, headers={"Content-Length": str(drive.max_bytes + 1)})
        return media_http(request)

    def old_plan(*args):
        posts, assets, blockers = plan_post(*args)
        return posts, assets[:16], ["blocked_media_count"]

    with (
        httpx.Client(transport=httpx.MockTransport(old_cdn)) as cdn,
        patch("x_bookmark_migrator.archive.plan_post", old_plan)
        if reason == "media_count"
        else nullcontext(),
    ):
        old = archive_batch(inv, drive, cdn)
    original = Store(tmp_path / "original")
    restore_batch(drive, manifest(store, old), original, manifest_hash(store, old))
    old_objects = dict(server.bytes)
    old_local = {p: p.read_bytes() for p in original.root.iterdir()}
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path)
    archive_cli.add_commands(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        [
            "--state-dir",
            str(store.root),
            "archive-repair",
            "--drive-folder-id",
            "root",
            "--batch",
            str(original.root),
            str(store.path(old["private_receipt"])),
            "--reason",
            reason,
        ]
    )
    with patch.object(archive_cli.httpx, "Client", side_effect=AssertionError):
        plan = archive_cli.run(args)
        args.max_asset_bytes += 1
        assert archive_cli.run(args)["plan_sha256"] != plan["plan_sha256"]
        args.max_asset_bytes -= 1
        args.apply_plan = "stale"
        with pytest.raises(PilotError, match="differs"):
            archive_cli.run(args)
    fetches = []

    def fetch(request):
        fetches.append(request.url.host)
        return media_http(request)

    args.apply_plan = plan["plan_sha256"]
    if reason == "byte_limit":
        with monkeypatch.context() as stopped:
            stopped.setattr(
                archive_cli.httpx,
                "Client",
                mock_clients(server, lambda request: httpx.Response(404)),
            )
            with pytest.raises(PilotError, match="Media download failed"):
                archive_cli.run(args)
        assert server.bytes == old_objects
        assert len(list(store.root.glob("archive-*.private.json"))) == 1
    real_create = Store.create

    def interrupt(instance, name, value):
        if name.endswith(".receipt.private.json") and reason == "byte_limit":
            raise OSError("before publication")
        real_create(instance, name, value)
        if name.endswith(".receipt.private.json"):
            raise OSError("after publication")

    with monkeypatch.context() as crash:
        crash.setattr(archive_cli.httpx, "Client", mock_clients(server, fetch))
        crash.setattr(Store, "create", interrupt)
        with pytest.raises(OSError, match="publication"):
            archive_cli.run(args)
    receipts = "repair-*.receipt.private.json"
    assert len(list(store.root.glob(receipts))) == (reason == "media_count")
    after_crash = dict(server.bytes)
    assert sum(server.objects[f]["mimeType"] == "application/x-ndjson" for f in after_crash) == 2
    if reason == "byte_limit":
        (video_id,) = (f for f in after_crash if server.objects[f]["mimeType"] == "video/mp4")
        assert video_id not in old_objects
        with monkeypatch.context() as corrupt:
            corrupt.setattr(server, "corrupt_id", video_id)
            corrupt.setattr(
                archive_cli.httpx,
                "Client",
                mock_clients(server, lambda req: pytest.fail("CDN replay")),
            )
            with pytest.raises(PilotError, match="do not match"):
                archive_cli.run(args)
        assert not list(store.root.glob(receipts))
    for _ in range(2):
        with monkeypatch.context() as retry:
            retry.setattr(
                archive_cli.httpx,
                "Client",
                mock_clients(server, lambda req: pytest.fail("CDN replay")),
            )
            repaired = archive_cli.run(args)
        assert server.bytes == after_crash
        assert [p.name for p in store.root.glob(receipts)] == [repaired["private_receipt"]]
    assert store.read(repaired["private_receipt"])["plan_sha256"] == plan["plan_sha256"]
    assert repaired["bytes_verified"] == 1 and not repaired["deletion_eligible"]
    assert len(fetches) == plan["media_to_fetch"] == (1 if reason == "byte_limit" else 4)
    assert plan["media_to_reverify"] == len(media) - len(fetches)
    restored = Store(tmp_path / "new")
    result = restore_batch(
        drive, manifest(store, repaired), restored, manifest_hash(store, repaired)
    )
    assert result["media_files"] == len(media)
    assert old_objects.items() <= server.bytes.items()
    assert old_local == {p: p.read_bytes() for p in original.root.iterdir()}
    old_payload = json.loads(original.path("101.json").read_bytes())
    new_payload = json.loads(restored.path("101.json").read_bytes())
    if reason == "byte_limit":
        for asset in new_payload["media"]:
            assert asset["media_origin"] == {"post": origin, "author": includes()["users"][0]}
    assert new_payload["repair_source"]["manifest"]["sha256"] == manifest_hash(store, old)
    for key in ("folders", "category", "posts", "source_evidence", "inventory_record"):
        assert old_payload[key] == new_payload[key]
    for old_asset, new_asset in zip(old_payload["media"], new_payload["media"], strict=False):
        if "storage" in old_asset:
            assert new_asset == old_asset
    worker = Worker(
        store,
        PublicationStore(tmp_path / "catalog"),
        tmp_path / "queue-restores",
        None,
        drive,
        None,
        None,
        None,
        {"max_batches": 20},
    )
    assert worker.bootstrap(inv)
    assert set(worker.state["batches"]) == {old["private_receipt"]}
    assert worker.state["items"]["101"]["state"] == "blocked"
    server.corrupt_id = manifest(store, repaired)
    with monkeypatch.context() as replay:
        replay.setattr(
            archive_cli.httpx, "Client", mock_clients(server, lambda req: pytest.fail("CDN replay"))
        )
        with pytest.raises(PilotError, match="do not match"):
            archive_cli.run(args)


@pytest.mark.parametrize(
    "error",
    [{"resource_type": "tweet", "resource_id": "102"}, {"title": "unknown"}],
)
def test_repair_rejects_related_or_unattributed_errors(tmp_path, storage, error):  # noqa: F811
    store, drive, _, _ = storage
    primary = post(
        referenced_posts=[{"id": "102", "type": "replied_to"}],
        attachments={"media_keys": ["video-1"]},
    )
    inv = inventory(store, [primary], includes([video()]))
    name = inv.state["posts"]["101"]["expansions_ref"]
    store.write(name, {**store.read(name), "reference_errors": [error]})
    # Deliberately mislabel an incomplete legacy archive as byte-limit-only.
    # Eligibility must be rechecked from captured evidence in repair selection.
    with (
        patch("x_bookmark_migrator.archive.partial_source_response", return_value=False),
        httpx.Client(
            transport=httpx.MockTransport(
                lambda req: httpx.Response(
                    200, headers={"Content-Length": str(drive.max_bytes + 1)}
                )
            )
        ) as cdn,
    ):
        old = archive_batch(inv, drive, cdn)
    original = Store(tmp_path / "original")
    restore_batch(drive, manifest(store, old), original, manifest_hash(store, old))
    with pytest.raises(PilotError, match="Incomplete original"):
        archive_repair.select_repairs(
            [(original.root, store.path(old["private_receipt"]))], "byte_limit"
        )


def mock_clients(server, fetch):
    client = httpx.Client
    handlers = iter([server.handle, fetch])
    return lambda **kw: client(transport=httpx.MockTransport(next(handlers)), **kw)


@pytest.mark.parametrize("fault", ["donor", "media", "mixed"])
def test_author_writer_waits_for_independent_restore(tmp_path, storage, monkeypatch, fault):  # noqa: F811
    store, drive, server, _ = storage
    batches, payloads = [], []
    for pid, media in [("101", [photo()]), ("102", [])]:
        inc = includes(media)
        raw_post = post(pid)
        if pid == "101":
            inc["users"] = []
            raw_post["attachments"] = {"media_keys": [media[0]["media_key"]]}
        mixed = fault == "mixed" and pid == "101"
        inv = inventory(store, [raw_post, post("103")] if mixed else [raw_post], inc)
        if mixed:
            inv.state["posts"]["103"]["expansions_ref"] = None
        name = f"read-{pid}.private.json"
        record = inv.state["posts"][pid]
        evidence = store.read(record["expansions_ref"])
        store.create(name, dict(evidence, endpoint="/2/tweets", private_file=name))
        record["expansions_ref"] = name
        with httpx.Client(transport=httpx.MockTransport(media_http)) as cdn:
            batch = archive_batch(inv, drive, cdn)
        restored = Store(tmp_path / ("batch-" + pid))
        restore_batch(drive, manifest(store, batch), restored, manifest_hash(store, batch))
        batches.append((restored.root, store.path(batch["private_receipt"])))
        payloads.append(json.loads(restored.path(pid + ".json").read_bytes()))
    old_objects = dict(server.bytes)
    catalog = PublicationStore(tmp_path / "catalog")
    notes = {"category_overrides": {"102": "Keep this donor category"}}
    catalog.write("annotations.private.json", notes)
    publish(catalog, build(batches, catalog))
    before = catalog.path("catalog.jsonl").read_bytes()
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path)
    archive_cli.add_commands(parser.add_subparsers(dest="command"))
    output = tmp_path / "successor"
    args = parser.parse_args(
        [
            "--state-dir",
            str(store.root),
            "archive-repair",
            "--reason",
            "retained_author",
            "--catalog-dir",
            str(catalog.root),
            "--drive-folder-id",
            "root",
            "--output-dir",
            str(output),
            *(str(v) for batch in batches for v in ("--batch", *batch)),
        ]
    )
    preview = archive_cli.run(args)
    assert preview["records"] == preview["media_to_reverify"] == 1
    assert preview["media_to_fetch"] == 0
    args.apply_plan = preview["plan_sha256"]
    donor_row = json.loads((batches[1][0] / "manifest.jsonl").read_bytes())
    src_row = next(
        r
        for r in map(json.loads, (batches[0][0] / "manifest.jsonl").read_bytes().splitlines())
        if r["post_id"] == "101"
    )
    target = donor_row["payload"] if fault == "donor" else payloads[0]["media"][0]["storage"]
    clients = []
    preflight = Drive.preflight

    def fail_fresh_read(instance):
        clients.append(instance)
        if len(clients) == 2:
            server.corrupt_id = target["file_id"]
        return preflight(instance)

    def run():
        with monkeypatch.context() as m:
            m.setattr(
                archive_cli.httpx,
                "Client",
                mock_clients(server, lambda req: pytest.fail("unexpected CDN")),
            )
            return archive_cli.run(args)

    unsafe = tmp_path / "unsafe"
    unsafe.mkdir()
    unsafe.chmod(0o777)
    args.output_dir = str(unsafe / "restore")
    with patch.object(archive_cli.httpx, "Client", side_effect=AssertionError):
        with pytest.raises(PilotError, match="Unsafe local archive directory"):
            archive_cli.run(args)
    assert not Path(args.output_dir).exists() and not list(
        store.root.glob("repair-*.receipt.private.json")
    )
    args.output_dir = str(output)
    (unsafe / "private").mkdir(mode=0o700)
    args.output_dir = str(unsafe / "private" / "restore")
    args.catalog_dir = None
    with patch.object(archive_cli.httpx, "Client", side_effect=AssertionError):
        with pytest.raises(PilotError, match="Unsafe local archive directory"):
            archive_cli.run(args)
    assert not Path(args.output_dir).exists()
    args.catalog_dir = str(catalog.root)
    if fault == "mixed":
        catalog.root.chmod(0o2750)
        intermediate = tmp_path / "owner-only"
        intermediate.mkdir(mode=0o700)
        nested = intermediate / "restore"
        nested.mkdir()
        nested.chmod(0o2750)
        args.output_dir = str(nested)
        with patch.object(archive_cli.httpx, "Client", side_effect=AssertionError):
            with pytest.raises(PilotError, match="direct sibling"):
                archive_cli.run(args)
        assert catalog.path("catalog.jsonl").read_bytes() == before and not list(nested.iterdir())
        output.mkdir()
        output.chmod(0o2750)
    args.output_dir = str(output)
    with monkeypatch.context() as m:
        m.setattr(Drive, "preflight", fail_fresh_read)
        with pytest.raises(PilotError, match="do not match"):
            run()
    assert len(clients) == 2 and clients[0] is not clients[1]
    assert not list(store.root.glob("repair-*.receipt.private.json"))
    assert catalog.path("catalog.jsonl").read_bytes() == before
    uploaded = dict(server.bytes)
    server.corrupt_id = None
    if fault == "mixed":
        name = restored_media_filename("101", payloads[0]["media"][0])
        seeded = output / name
        seeded.write_bytes((batches[0][0] / name).read_bytes())
        seeded.chmod(0o600)
        projections = ("catalog.jsonl", "index.html", "source-links.jsonl")
        snapshot = [catalog.path(n).read_bytes() for n in projections]
        with pytest.raises(PilotError, match="reader permissions"):
            run()
        assert seeded.stat().st_mode & 0o777 == 0o600
        assert snapshot == [catalog.path(n).read_bytes() for n in projections]
        assert not list(store.root.glob("repair-*.receipt.private.json"))
        seeded.unlink()
    if fault == "donor":
        full_catalog = read_catalog(catalog)
        absent_donor = [dict(r, versions=[]) if r["post_id"] == "102" else r for r in full_catalog]
        publish(catalog, absent_donor)
        projections = ("catalog.jsonl", "index.html", "source-links.jsonl")
        snapshot = [catalog.path(n).read_bytes() for n in projections]
        with pytest.raises(PilotError, match="lacks the bound predecessor or donor"):
            run()
        assert snapshot == [catalog.path(n).read_bytes() for n in projections]
        assert server.bytes == uploaded
        publish(catalog, full_catalog)
    for _ in range(2):
        complete = run()
        assert complete["bytes_verified"] == 1 and complete["deletion_eligible"] is False
        assert server.bytes == uploaded
        assert complete["catalog_merged"] is True
        receipt = store.read(complete["private_receipt"])
        assert receipt["reason"] == "retained_author"
        assert receipt["manifest"]["sha256"] not in archive_anchors(store, drive.root)
    items = read_catalog(catalog)
    assert [len(r["versions"]) for r in items] == ([2, 1, 1] if fault == "mixed" else [2, 1])
    if fault == "mixed":
        assert items[2]["versions"][0]["blockers"] == ["blocked_missing_provenance"]
        assert (output / "101.json").stat().st_mode & 0o777 == 0o640
    assert items[1]["category_override"] == notes["category_overrides"]["102"]
    assert catalog.read("annotations.private.json") == notes
    assert sorted(v["blockers"] for v in items[0]["versions"]) == [[], ["blocked_missing_author"]]
    repaired = json.loads((output / "101.json").read_bytes())
    for i, (key, row) in enumerate(
        [("repair_source", src_row), ("retained_author_source", donor_row)]
    ):
        assert repaired[key]["payload"] == row["payload"]
        assert repaired[key]["manifest"] == json.loads(batches[i][1].read_bytes())["manifest"]
    assert repaired["retained_author_source"]["envelope_sha256"] == digest(
        encode(payloads[1]["source_evidence"])
    )
    assert repaired["posts"] == [dict(payloads[0]["posts"][0], author=includes()["users"][0])]
    assert repaired["source_evidence"] == payloads[0]["source_evidence"]
    assert repaired["media"] == payloads[0]["media"]
    assert old_objects.items() <= server.bytes.items()
