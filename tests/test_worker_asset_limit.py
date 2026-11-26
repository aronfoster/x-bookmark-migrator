"""Worker config and Drive readback using disposable state and HTTP doubles."""

import json
from types import SimpleNamespace

import httpx
import pytest
from test_archive import MP4, includes, inventory, manifest, manifest_hash, post, storage, video  # noqa: F401
from test_worker import config

from x_bookmark_migrator import worker_cli
from x_bookmark_migrator.archive import archive_batch, restore_batch
from x_bookmark_migrator.archive_repair import select_repairs, write_repairs
from x_bookmark_migrator.catalog import build, read_catalog
from x_bookmark_migrator.core import PilotError, Store
from x_bookmark_migrator.drive import MAX_BYTES, Drive
from x_bookmark_migrator.worker import merge_publication


def worker_args(tmp_path, **settings):
    path = tmp_path / "worker.json"
    path.write_text(json.dumps({**config(tmp_path), **settings}))
    path.chmod(0o600)
    return SimpleNamespace(
        config=path,
        state_dir=tmp_path / "state",
        once=True,
        live=True,
        scheduled=False,
        allow_delete=False,
        apply_verified_plan=False,
        report_only=False,
        dry_run=False,
    )


@pytest.mark.parametrize("limit", [None, True, "1024", 1024.0, -1, 1023, MAX_BYTES + 1])
def test_invalid_asset_limit_stops_before_state_or_http(tmp_path, monkeypatch, limit):
    monkeypatch.setattr(httpx, "Client", lambda **_: pytest.fail("HTTP client constructed"))
    args = worker_args(tmp_path, max_asset_bytes=limit)
    with pytest.raises(PilotError, match="max_asset_bytes"):
        worker_cli.run(args)
    assert not args.state_dir.exists()


def test_worker_asset_limit_defaults_and_bounds(tmp_path):
    assert worker_cli.load_config(worker_args(tmp_path).config)["max_asset_bytes"] == 64 << 20
    for limit in (1024, MAX_BYTES):
        args = worker_args(tmp_path, max_asset_bytes=limit)
        assert worker_cli.load_config(args.config)["max_asset_bytes"] == limit


def test_worker_restores_repaired_large_video(tmp_path, storage, monkeypatch):  # noqa: F811
    store, drive, server, drive_http = storage
    inv = inventory(
        store, [post(attachments={"media_keys": ["video-1"]}), post("102")], includes([video()])
    )
    # One byte past the historical worker limit. Repair and restore the actual bytes.
    data = MP4.ljust((64 << 20) + 1, b"\0")
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=data))
    ) as cdn:
        old = archive_batch(inv, drive, cdn)
        original = Store(tmp_path / "original")
        restore_batch(drive, manifest(store, old), original, manifest_hash(store, old))
        selected = select_repairs(
            [(original.root, store.path(old["private_receipt"]))], "byte_limit"
        )
        large_drive = Drive(store, drive_http, "root", MAX_BYTES)
        repaired = write_repairs(store, large_drive, cdn, selected)
        repaired_root = Store(tmp_path / "repaired")
        restore_batch(
            large_drive, manifest(store, repaired), repaired_root, manifest_hash(store, repaired)
        )
        catalog = Store(tmp_path / "catalog")
        merge_publication(
            catalog, build([(repaired_root.root, store.path(repaired["private_receipt"]))], catalog)
        )
        assert (
            read_catalog(catalog)[0]["versions"][0]["media"][0]["availability"]
            == "local_hash_verified"
        )
        ordinary = archive_batch(inv, large_drive, cdn, post_ids=["101"])

    args = worker_args(tmp_path, max_asset_bytes=MAX_BYTES)
    store.write("worker-billing.private.json", {"observed_at": "fixture", "actual_usd": "0"})
    monkeypatch.setenv("X_CLIENT_ID", "fixture")
    monkeypatch.setattr(worker_cli, "protected_config", lambda path: path.read_bytes())
    monkeypatch.setattr(
        worker_cli, "os", SimpleNamespace(getuid=lambda: 1000, environ=worker_cli.os.environ)
    )
    client = httpx.Client

    def forbidden(request):
        pytest.fail(f"Unexpected {request.method} request outside Drive")

    handlers = iter((forbidden, server.handle, forbidden))
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: client(transport=httpx.MockTransport(next(handlers)), **kwargs),
    )
    result = worker_cli.run(args)
    assert result["stop_reason"] is None
    assert not (tmp_path / "worker-restored" / manifest_hash(store, repaired)).exists()
    restored = tmp_path / "worker-restored" / manifest_hash(store, ordinary)
    (video_file,) = restored.glob("*.mp4")
    assert video_file.read_bytes() == data
