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
from x_bookmark_migrator.core import PilotError, Store
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
