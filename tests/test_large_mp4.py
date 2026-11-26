from argparse import Namespace

import httpx
import pytest
from test_archive import (
    MP4,
    box,
    includes,
    inventory,
    manifest,
    manifest_hash,
    post,
    storage,  # noqa: F401
    video,
)
from test_archive_repair import mock_clients
from test_raindrop import SkillServer, adapter_for, item
from test_worker import config

from x_bookmark_migrator import archive
from x_bookmark_migrator import large_mp4 as large
from x_bookmark_migrator.archive import archive_batch, restore_batch
from x_bookmark_migrator.backlog import prepare_one, select_backlog
from x_bookmark_migrator.catalog import build, link_raindrop
from x_bookmark_migrator.core import PilotError, Store
from x_bookmark_migrator.drive import MAX_BYTES, Drive, digest, encode
from x_bookmark_migrator.unbookmark import load_plan


def test_profile_boundaries(storage):  # noqa: F811
    store, _, _, http = storage
    for size in (463785057, 615780736, large.CEILING):
        assert large.ExactDrive(store, http, "root", size).max_bytes == size
        with pytest.raises(PilotError):
            Drive(store, http, "root", size)
    for size in (MAX_BYTES, large.CEILING + 1, True):
        with pytest.raises(PilotError):
            large.ExactDrive(store, http, "root", size)
    assert Drive(store, http, "root").max_bytes == 64 << 20


@pytest.mark.parametrize(
    ("headers", "chunks"),
    [
        ({"Content-Length": "9"}, [b"1234"]),
        ({"Content-Encoding": "gzip"}, []),
        ({}, [b"123"]),
        ({}, [b"1234", b"5"]),
    ],
)
def test_strict_stream_bound(tmp_path, headers, chunks):
    with pytest.raises(PilotError):
        large.mapped_response(
            httpx.Response(200, headers=headers, stream=httpx.ByteStream(b"".join(chunks))),
            tmp_path,
            4,
        )
    assert not list(tmp_path.iterdir())


@pytest.fixture
def blocked_video(tmp_path, storage, monkeypatch):  # noqa: F811
    store, drive, server, _ = storage
    inv = inventory(store, [post(attachments={"media_keys": ["video-1"]})], includes([video()]))
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, headers={"Content-Length": str(drive.max_bytes + 1)})
        )
    ) as cdn:
        original = archive_batch(inv, drive, cdn)
    source = Store(tmp_path / "source")
    restore_batch(drive, manifest(store, original), source, manifest_hash(store, original))
    data = MP4 + box(b"free", bytes(2 * large.CHUNK))
    monkeypatch.setattr(large, "MAX_BYTES", large.CHUNK)
    monkeypatch.setattr(large, "check_space", lambda *args: None)  # tmpfs synthetic fixture
    handle = server.handle

    def with_sizes(request):
        try:
            return handle(request)
        finally:
            for file_id, value in server.bytes.items():
                server.objects[file_id]["size"] = str(len(value))

    monkeypatch.setattr(server, "handle", with_sizes)
    args = Namespace(
        batch=[source.root, store.path(original["private_receipt"])],
        manifest=None,
        item=1,
        state_dir=store.root,
        drive_folder_id="root",
        output_dir=tmp_path / "out",
        exact_bytes=len(data),
        apply_plan=None,
    )
    args.apply_plan = large.run(args)["plan_sha256"]
    assert not args.output_dir.exists()
    return store, server, data, args


def test_repair_restore_and_resume(tmp_path, blocked_video, monkeypatch):
    store, server, data, args = blocked_video
    calls = []

    def cdn(request):
        assert request.url.path == "/high.mp4" and "authorization" not in request.headers
        calls.append(request)
        return httpx.Response(200, content=data)

    with monkeypatch.context() as m:
        m.setattr(large.httpx, "Client", mock_clients(server, cdn))
        result = large.run(args)
    assert result["exact_mp4_local_verified"] and not result["deletion_eligible"]
    assert next(args.output_dir.glob("*.mp4")).read_bytes() == data
    upload = next(r for r in server.calls if r.method == "PUT" and r.content == data)
    assert upload.headers["Content-Length"] == str(len(data))
    args.output_dir = tmp_path / "second"
    with monkeypatch.context() as m:
        m.setattr(large.httpx, "Client", mock_clients(server, cdn))
        assert large.run(args)["private_receipt"] == result["private_receipt"]
    assert len(calls) == 1
    args.manifest = manifest(store, result), manifest_hash(store, result)
    args.item = args.apply_plan = None
    args.output_dir = tmp_path / "third"
    with monkeypatch.context() as m:
        m.setattr(large.httpx, "Client", mock_clients(server, cdn))
        assert large.run(args)["exact_mp4_local_verified"]
    args.output_dir = tmp_path / "corrupt"
    server.corrupt_id = next(k for k, v in server.bytes.items() if v == data)
    with monkeypatch.context() as m, pytest.raises(PilotError, match="differs from source"):
        m.setattr(large.httpx, "Client", mock_clients(server, cdn))
        large.run(args)
    assert not list(args.output_dir.glob("*.mp4"))


@pytest.mark.parametrize("damage", [None, "size", "mimeType", "parents", "appProperties", "bytes"])
def test_interrupted_upload_recovers_original_before_cdn(
    tmp_path, blocked_video, monkeypatch, damage
):
    store, server, data, args = blocked_video
    server.interrupt_upload = True
    with monkeypatch.context() as m, pytest.raises(httpx.ReadError):
        m.setattr(
            large.httpx, "Client", mock_clients(server, lambda r: httpx.Response(200, content=data))
        )
        large.run(args)
    original = next(k for k, v in server.bytes.items() if v == data)
    if damage == "bytes":
        server.corrupt_id = original
    elif damage:
        server.objects[original][damage] = "mismatch"
        if damage == "size":
            # This fixture recomputes the metadata size after every request.
            server.bytes[original] += b"x"
    args.output_dir = tmp_path / "retry"
    with monkeypatch.context() as m:
        m.setattr(large.httpx, "Client", mock_clients(server, lambda r: pytest.fail("CDN replay")))
        if damage:
            with pytest.raises(PilotError):
                large.run(args)
            assert not list(store.root.glob("exact-complete-*"))
        else:
            assert large.run(args)["exact_mp4_local_verified"]
            assert next(args.output_dir.glob("*.mp4")).read_bytes() == data
    assert sum(r.method == "PUT" and r.content == data for r in server.calls) == 1


def test_local_failure_does_not_publish_completion(tmp_path, blocked_video, monkeypatch):
    store, server, data, args = blocked_video
    write = archive.create_local

    def damaged(output, name, content, **kwargs):
        write(output, name, content, **kwargs)
        if name.endswith(".mp4"):
            with output.path(name).open("r+b") as local:
                local.write(b"x")

    with monkeypatch.context() as m, pytest.raises(PilotError, match="Reopened"):
        m.setattr(archive, "create_local", damaged)
        m.setattr(
            large.httpx, "Client", mock_clients(server, lambda r: httpx.Response(200, content=data))
        )
        large.run(args)
    assert not list(store.root.glob("*receipt.private.json"))
    assert not list(store.root.glob("exact-complete-*"))
    assert list((store.root / "exact-remote-only").rglob("*.receipt.private.json"))
    args.output_dir = tmp_path / "fresh"
    with monkeypatch.context() as m:
        m.setattr(large.httpx, "Client", mock_clients(server, lambda r: pytest.fail("CDN replay")))
        assert large.run(args)["exact_mp4_local_verified"]
    assert len(list(store.root.glob("exact-complete-*"))) == 1
    assert sum(r.method == "PUT" and r.content == data for r in server.calls) == 1


@pytest.mark.parametrize("target", ["manifest", "payload", "mp4"])
def test_standalone_restore_confined_before_read(tmp_path, blocked_video, monkeypatch, target):
    import json

    store, server, data, args = blocked_video
    with monkeypatch.context() as m:
        m.setattr(
            large.httpx, "Client", mock_clients(server, lambda r: httpx.Response(200, content=data))
        )
        result = large.run(args)
    args.manifest = manifest(store, result), manifest_hash(store, result)
    args.item = args.apply_plan = None
    args.output_dir = tmp_path / "standalone"
    row = json.loads(server.bytes[args.manifest[0]])
    moved = {
        "manifest": args.manifest[0],
        "payload": row["payload"]["file_id"],
        "mp4": next(k for k, v in server.bytes.items() if v == data),
    }[target]
    server.objects["root2"] = {**server.objects["root"], "id": "root2"}
    server.objects["folder2"] = {**server.objects["root"], "id": "folder2", "parents": ["root2"]}
    server.objects[moved]["parents"] = ["folder2"]
    server.calls.clear()
    with monkeypatch.context() as m, pytest.raises(PilotError, match="archive"):
        m.setattr(large.httpx, "Client", mock_clients(server, lambda r: pytest.fail("CDN replay")))
        large.run(args)
    assert not any(
        r.url.path.endswith("/" + moved) and r.url.params.get("alt") == "media"
        for r in server.calls
    )


def test_large_container_bound():
    large.check_container(MP4)
    for malformed in (MP4[:-1], box(b"junk", b"x"), box(b"free", b"") * 65536 + MP4):
        with pytest.raises(PilotError):
            large.check_container(malformed)


def test_published_completion_prepares_only_the_pinned_exact_mp4(
    tmp_path, blocked_video, monkeypatch
):
    store, server, data, args = blocked_video
    with monkeypatch.context() as m:
        m.setattr(
            large.httpx, "Client", mock_clients(server, lambda r: httpx.Response(200, content=data))
        )
        result = large.run(args)
    name = result["private_receipt"]
    receipt = store.read(name)
    assert receipt["exact_plan_sha256"] != receipt["plan_sha256"]
    # Publish a successor alongside its blocked predecessor as the launcher does.
    catalog = Store(tmp_path / "catalog")
    records = build([args.batch, [args.output_dir, store.path(name)]], catalog)
    adapter = adapter_for(tmp_path, SkillServer([item("101", 101)]))
    link_raindrop(catalog, records, adapter.verify_raindrop_now("101", 101))
    window = "read-" + "c" * 24 + ".private.json"
    store.create(
        window,
        dict(
            private_file=window,
            endpoint="/2/users/100/bookmarks/folders/10",
            request={"max_results": "100"},
            retrieved_at="2026-01-01T00:00:00Z",
            status=200,
            response={"data": [{"id": "101"}]},
        ),
    )
    sha = digest(catalog.path("catalog.jsonl").read_bytes())
    cfg = dict(config(tmp_path), helper_descriptors=[], max_asset_bytes=large.CHUNK)

    def select():
        return select_backlog(store, catalog, cfg, "10", sha)

    # The nested remote completion, even with an exact source intent, isn't sufficient.
    store.path(name).unlink()
    assert select() == []
    store.create(name, dict(receipt, local_mp4_sha256="0" * 64))
    with pytest.raises(PilotError, match="Exact completion"):
        select()
    store.write(name, receipt)
    remote = Store(store.root / "exact-remote-only" / receipt["exact_plan_sha256"])
    frozen_name = f"repair-{receipt['plan_sha256']}.manifest-intent.private.json"
    frozen = remote.read(frozen_name)
    remote.write(frozen_name, dict(frozen, drive_root="other"))
    with pytest.raises(PilotError, match="frozen manifest"):
        select()
    remote.write(frozen_name, frozen)
    (candidate,) = select()
    assert candidate["archive_receipt"] == name
    selection = digest(encode(candidate))

    def prepare():
        return prepare_one(
            store, catalog, cfg, "10", sha, candidate["source_read_sha256"], selection, True
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(server.handle), **kw),
    )
    server.calls.clear()
    server.corrupt_id = candidate["exact_source"]["file_id"]
    with pytest.raises(PilotError, match="differs from source"):
        prepare()
    assert not list(store.root.glob("backlog-plan-*"))
    server.corrupt_id = None
    cfg["max_asset_bytes"] = 1024
    with pytest.raises(PilotError, match="storage byte limit"):
        prepare()  # The JSON payload still uses the configured normal cap.
    cfg["max_asset_bytes"] = large.CHUNK
    staged = prepare()
    assert staged["verified"] == 1 and staged["deleted"] == staged["x_api_calls"] == 0
    with pytest.raises(PilotError, match="not an executable"):
        load_plan(store, f"backlog-plan-{selection}.private.json", "100")
    assert not store.path("verified-one-plan.private.json").exists()
    assert not list(store.root.glob("unbookmark-plan-*"))
    assert all(r.method == "GET" or r.url.host == "oauth2.googleapis.com" for r in server.calls)
