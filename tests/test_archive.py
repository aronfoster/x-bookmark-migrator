"""Invented content/IDs only. HTTP doubles establish behavior, not live acceptance."""

import base64
import json
import re
import sys
import time

import httpx
import pytest

from x_bookmark_migrator.archive import (
    archive_batch,
    enrich_references,
    import_pilot,
    media_choice,
    media_format,
    plan_post,
    restore_batch,
    source_url,
)
from x_bookmark_migrator.core import SCOPES, Budget, Client, PilotError, Store, post_fields
from x_bookmark_migrator.drive import API, FOLDER, Drive, digest, encode
from x_bookmark_migrator.inventory import Inventory

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nWQAAAAASUVORK5CYII="
)


def box(kind, value):
    return (8 + len(value)).to_bytes(4, "big") + kind + value


# Synthetic ISO BMFF structure tests; deliberately NOT a claim of playable video.
MP4 = box(b"ftyp", b"isom0000") + box(b"mdat", b"sample") + box(b"moov", b"sample")


class DriveServer:
    def __init__(self):
        self.objects = {
            "root": {
                "id": "root",
                "name": "Archive",
                "mimeType": FOLDER,
                "ownedByMe": True,
                "trashed": False,
                "capabilities": {"canAddChildren": True},
            }
        }
        self.bytes = {}
        self.sessions = {}
        self.calls = []
        self.counter = 0
        self.interrupt_upload = False
        self.corrupt_id = None
        self.scope = "https://www.googleapis.com/auth/drive"

    def handle(self, request):
        self.calls.append(request)
        path = request.url.path
        assert request.method in ("POST", "PUT", "GET")
        if request.url.host == "oauth2.googleapis.com":
            assert request.method == "POST"
            return httpx.Response(
                200,
                json={
                    "access_token": "synthetic-secret",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                    "scope": self.scope,
                },
            )
        assert request.headers["Authorization"] == "Bearer synthetic-secret"
        if path.endswith("/about"):
            return httpx.Response(
                200,
                json={
                    "user": {"permissionId": "synthetic-owner"},
                    "storageQuota": {"limit": "1000000000", "usage": "0"},
                },
            )
        if path.endswith("/generateIds"):
            self.counter += 1
            return httpx.Response(200, json={"ids": [f"file-{self.counter}"]})
        if path == "/drive/v3/files" and request.method == "GET":
            token = re.search("value='([a-f0-9]+)'", request.url.params["q"])[1]
            files = [
                {"id": f["id"]}
                for f in self.objects.values()
                if f.get("appProperties", {}).get("xm_key") == token
            ]
            return httpx.Response(200, json={"files": files})
        if path == "/upload/drive/v3/files" and request.method == "POST":
            meta = json.loads(request.content)
            if meta["id"] in self.objects:
                return httpx.Response(409)
            self.sessions[meta["id"]] = meta
            return httpx.Response(
                200,
                headers={
                    "Location": "https://www.googleapis.com/upload/drive/v3/files?upload_id="
                    + meta["id"]
                },
            )
        if path == "/upload/drive/v3/files" and request.method == "PUT":
            file_id = request.url.params["upload_id"]
            self.objects[file_id] = {**self.sessions[file_id], "trashed": False}
            self.bytes[file_id] = request.content
            if self.interrupt_upload:
                self.interrupt_upload = False
                raise httpx.ReadError("synthetic dropped acknowledgement", request=request)
            return httpx.Response(200, json={"id": file_id})
        if path == "/drive/v3/files" and request.method == "POST":
            meta = json.loads(request.content)
            if meta["id"] in self.objects:
                return httpx.Response(409)
            self.objects[meta["id"]] = {**meta, "trashed": False}
            return httpx.Response(200, json=meta)
        file_id = path.rsplit("/", 1)[-1]
        if file_id not in self.objects:
            return httpx.Response(404)
        if request.url.params.get("alt") == "media":
            data = self.bytes[file_id]
            if file_id == self.corrupt_id:
                data = b"X" + data[1:]
            return httpx.Response(200, content=data)
        return httpx.Response(200, json=self.objects[file_id])


def grant(store):
    store.write(
        "drive-credentials.private.json",
        {
            "type": "authorized_user",
            "client_id": "fake",
            "client_secret": "fake",
            "refresh_token": "fake",
        },
    )


@pytest.fixture
def storage(tmp_path):
    store = Store(tmp_path / "state")
    grant(store)
    server = DriveServer()
    with httpx.Client(transport=httpx.MockTransport(server.handle)) as http:
        drive = Drive(store, http, "root")
        drive.preflight()
        yield store, drive, server, http


def post(pid="101", **extra):
    return {"id": pid, "author_id": "900", "text": "invented caption", **extra}


def includes(media=None, posts=None):
    return {
        "users": [{"id": "900", "name": "Invented Author", "username": "example"}],
        "media": media or [],
        "posts": posts or [],
    }


def photo(key="photo-1"):
    return {"media_key": key, "type": "photo", "url": "https://pbs.twimg.com/media/synthetic.png"}


def video(kind="video"):
    return {
        "media_key": "video-1",
        "type": kind,
        "preview_image_url": "https://pbs.twimg.com/p.jpg",
        "variants": [
            {"content_type": "application/x-mpegURL", "url": "https://video.twimg.com/a.m3u8"},
            {
                "content_type": "video/mp4",
                "bit_rate": 256,
                "url": "https://video.twimg.com/low.mp4",
            },
            {
                "content_type": "video/mp4",
                "bit_rate": 1024,
                "url": "https://video.twimg.com/high.mp4",
            },
        ],
    }


def inventory(store, posts=None, inc=None):
    inv = Inventory(store, "100")
    body = {"data": posts or [post()], "includes": inc or includes()}
    evidence = "read-fixture.private.json"
    store.write(
        evidence,
        {
            "status": 200,
            "endpoint": "/2/users/100/bookmarks",
            "request": post_fields(),
            "response": body,
        },
    )
    inv.accept_page("folders", {"data": [{"id": "10", "name": "Synthetic category"}]}, "folders")
    inv.accept_page("main", body, evidence)
    inv.accept_page("folder:10", {"data": [{"id": p["id"]} for p in body["data"]]}, "memberships")
    inv.save()
    return inv


def media_http(request):
    assert "authorization" not in request.headers
    assert request.url.host in ("pbs.twimg.com", "video.twimg.com")
    return httpx.Response(200, content=MP4 if request.url.path.endswith("mp4") else PNG)


def manifest(store, summary):
    return store.read(summary["private_receipt"])["manifest"]["file_id"]


def manifest_hash(store, summary):
    return store.read(summary["private_receipt"])["manifest"]["sha256"]


def test_five_posts_roundtrip_with_fresh_state_and_no_x(storage, tmp_path):
    store, drive, server, http = storage
    posts = [
        post(),
        post("102", note_post={"text": "long content " * 100}),
        post("103", attachments={"media_keys": ["photo-1", "photo-2"]}),
        post("104", attachments={"media_keys": ["video-1"]}),
        post("105", referenced_posts=[{"type": "quoted", "id": "106"}]),
    ]
    inc = includes(
        [photo(), photo("photo-2"), video("animated_gif")],
        [post("106", attachments={"media_keys": ["video-1"]})],
    )
    inv = inventory(store, posts, inc)
    with httpx.Client(transport=httpx.MockTransport(media_http)) as media:
        result = archive_batch(inv, drive, media)
        assert result["bytes_verified"] == 5 and result["blocked"] == 0
        object_count = len(server.bytes)
        again = archive_batch(inv, drive, media)
        # Content-addressed media, payloads and manifests do not duplicate on rerun.
        assert len(server.bytes) == object_count
        assert again["private_receipt"] != result["private_receipt"]
    fresh = Store(tmp_path / "fresh")
    grant(fresh)
    remote = Drive(fresh, http, "root")
    remote.preflight()
    output = Store(tmp_path / "restored")
    restored = restore_batch(remote, manifest(store, result), output, manifest_hash(store, result))
    assert restored == {
        "records": 5,
        "media_files": 4,
        "bytes_verified": 5,
        "blocked": 0,
        "deletion_eligible": False,
        "x_api_calls": 0,
    }
    payload = json.loads((output.root / "102.json").read_text())
    assert payload["posts"][0]["text"] == "long content " * 100
    assert payload["folders"] == [{"id": "10", "names": ["Synthetic category"]}]
    gif = json.loads((output.root / "104.json").read_text())["media"][0]
    assert gif["logical_type"] == "animated_gif" and gif["container"] == "mp4"
    assert gif["source_url"].endswith("high.mp4")
    assert gif["source_original_verified"] is False
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in output.root.iterdir())


@pytest.mark.parametrize(
    "extra,inc,reason",
    [
        ({"article": {"title": "Header"}}, includes(), "blocked_incomplete_article"),
        ({"article": {"text": "unproven schema"}}, includes(), "blocked_incomplete_article"),
        ({"note_post": {}}, includes(), "blocked_incomplete_long_text"),
        ({"attachments": {"media_keys": ["missing"]}}, includes(), "blocked_missing_media"),
        (
            {"attachments": {"media_keys": ["video-1"]}},
            includes([{**video(), "variants": []}]),
            "blocked_unsupported_media",
        ),
        (
            {"referenced_posts": [{"type": "quoted", "id": "999"}]},
            includes(),
            "blocked_missing_reference",
        ),
        ({}, {}, "blocked_missing_author"),
    ],
)
def test_incomplete_content_never_claimed_complete(extra, inc, reason):
    _, _, blockers = plan_post(post(**extra), inc)
    assert reason in blockers


def test_both_documented_long_text_dialects_and_conflict():
    assert plan_post(post(note_tweet={"text": "full"}), includes())[0][0]["text"] == "full"
    assert (
        "blocked_conflicting_long_text"
        in plan_post(post(note_tweet={"text": "a"}, note_post={"text": "b"}), includes())[2]
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://pbs.twimg.com/a",
        "https://localhost/a",
        "file:///x",
        "https://pbs.twimg.com.evil.example/a",
        "https://user@pbs.twimg.com/a",
    ],
)
def test_media_url_boundary(url):
    with pytest.raises(PilotError):
        source_url(url)


def test_media_validation():
    assert media_format(PNG, "photo") == ("image/png", "png")
    assert media_format(MP4, "video") == ("video/mp4", "mp4")
    assert media_choice(video())[0].endswith("high.mp4")
    for data, kind in [
        (b"<html>error</html>", "photo"),
        (PNG, "video"),
        (MP4[:-3], "video"),
        (PNG[:-5], "photo"),
        (b"#EXTM3U", "video"),
    ]:
        with pytest.raises(PilotError):
            media_format(data, kind)


def test_interrupted_successful_upload_recovers_without_duplicate(storage):
    store, drive, server, http = storage
    server.interrupt_upload = True
    with pytest.raises(httpx.ReadError):
        drive.save("folder-10", "101_01.png", PNG, "image/png")
    count = len(server.bytes)
    fresh = Drive(store, http, "root")
    result = fresh.save("folder-10", "101_01.png", PNG, "image/png")
    assert fresh.verify(result) == PNG
    assert len(server.bytes) == count == 1
    # Lost local journal: query appProperties and re-read bytes without re-upload.
    store.write("drive-journal.private.json", {})
    recovered = Drive(store, http, "root").save("folder-10", "101_01.png", PNG, "image/png")
    assert recovered["file_id"] == result["file_id"] and len(server.bytes) == count


def test_corrupt_readback_fails_and_cannot_be_replaced(storage):
    _, drive, server, _ = storage
    receipt = drive.save("folder-10", "101_01.png", PNG, "image/png")
    server.corrupt_id = receipt["file_id"]
    with pytest.raises(PilotError, match="match the receipt"):
        drive.save("folder-10", "101_01.png", PNG, "image/png")
    assert len(server.bytes) == 1


@pytest.mark.parametrize("cause", ["unknown", "conflict", "renamed", "api_partial", "provenance"])
def test_partial_records_are_saved_but_blocked(storage, cause):
    store, drive, _, _ = storage
    inv = inventory(store)
    record = inv.state["posts"]["101"]
    if cause == "unknown":
        record["folder_ids"] = []
    elif cause == "conflict":
        inv.state["folders"]["20"] = {"id": "20", "names": ["Other"]}
        record["folder_ids"].append("20")
    elif cause == "renamed":
        inv.state["folders"]["10"]["names"].append("Changed")
    elif cause == "api_partial":
        record["errors"] = [{"detail": "synthetic"}]
    else:
        record["expansions_ref"] = None
    with httpx.Client(transport=httpx.MockTransport(media_http)) as media:
        result = archive_batch(inv, drive, media)
    assert result["blocked"] == 1 and result["bytes_verified"] == 0


@pytest.mark.parametrize("status", [302, 401, 403, 404, 429, 500])
def test_media_http_failure_retains_blocked_record(storage, status):
    store, drive, _, _ = storage
    inv = inventory(store, [post(attachments={"media_keys": ["photo-1"]})], includes([photo()]))
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status))) as media:
        result = archive_batch(inv, drive, media)
    assert result["blocked"] == 1


def test_pilot_reuse_does_not_upload_old_media(storage):
    store, drive, server, _ = storage
    server.objects["legacy"] = {"id": "legacy", "trashed": False}
    server.bytes["legacy"] = PNG
    raw = encode(
        {
            "source_post_id": "101",
            "preserved_media": [
                {
                    "drive": {"id": "legacy", "parent_id": "old-folder"},
                    "sha256": digest(PNG),
                    "size_bytes": len(PNG),
                    "mime_type": "image/png",
                    "filename": "101_01.png",
                }
            ],
        }
    )
    server.objects["legacy-manifest"] = {"id": "legacy-manifest", "trashed": False}
    server.bytes["legacy-manifest"] = raw
    assert import_pilot(drive, store, "legacy-manifest")["adopted_assets"] == 1
    inv = inventory(store, [post(attachments={"media_keys": ["photo-1"]})], includes([photo()]))
    with httpx.Client(transport=httpx.MockTransport(media_http)) as media:
        result = archive_batch(inv, drive, media)
    assert result["bytes_verified"] == 1
    assert sum(data == PNG for data in server.bytes.values()) == 1


def test_explicit_reference_lookup_fills_media_gap(storage):
    store, drive, _, _ = storage
    inv = inventory(store, [post(referenced_posts=[{"type": "quoted", "id": "102"}])])
    store.write(
        "tokens.private.json",
        {
            "access_token": "fake",
            "refresh_token": "fake",
            "scope": " ".join(SCOPES),
            "client_id": "app",
            "expires_at": time.time() + 3600,
        },
    )
    calls = []

    def x(request):
        calls.append(request)
        if request.url.path == "/2/users/me":
            return httpx.Response(200, json={"data": {"id": "100"}})
        assert request.url.path == "/2/tweets" and request.url.params["ids"] == "102"
        assert request.url.params["post.fields"] == post_fields()["post.fields"]
        return httpx.Response(
            200,
            json={
                "data": [post("102", attachments={"media_keys": ["photo-1"]})],
                "includes": includes([photo()]),
            },
        )

    with httpx.Client(transport=httpx.MockTransport(x)) as http:
        enriched = enrich_references(
            inv, Client(store, http, "app"), Budget(store, ".01", ".10", "1"), "100"
        )
    assert enriched["x_api_calls"] == len(calls) == 2
    with httpx.Client(transport=httpx.MockTransport(media_http)) as media:
        result = archive_batch(Inventory.load(store), drive, media)
    assert result["bytes_verified"] == 1


def test_wrong_root_or_identity_refused(storage):
    _, drive, server, _ = storage
    server.objects["root"]["ownedByMe"] = False
    with pytest.raises(PilotError, match="owned"):
        drive.preflight()


def test_modified_manifest_association_refused(storage, tmp_path):
    store, drive, server, _ = storage
    inv = inventory(store)
    with httpx.Client(transport=httpx.MockTransport(media_http)) as media:
        result = archive_batch(inv, drive, media)
    file_id = manifest(store, result)
    row = json.loads(server.bytes[file_id])
    row["post_id"] = "999"
    server.bytes[file_id] = encode(row)
    with pytest.raises(PilotError, match="receipt hash"):
        restore_batch(drive, file_id, Store(tmp_path / "out"), manifest_hash(store, result))
    with pytest.raises(PilotError, match="association"):
        restore_batch(drive, file_id, Store(tmp_path / "out"), digest(server.bytes[file_id]))


def test_size_cap_blocks_media_without_upload(storage):
    store, drive, server, _ = storage
    drive.max_bytes = 10000
    inv = inventory(store, [post(attachments={"media_keys": ["photo-1"]})], includes([photo()]))
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"a" * 10001))
    ) as media:
        result = archive_batch(inv, drive, media)
    assert result["blocked"] == 1
    assert not any(data == b"a" * 10001 for data in server.bytes.values())


def test_no_storage_access_with_service_account(storage):
    store, _, server, http = storage
    store.write("drive-credentials.private.json", {"type": "service_account"})
    count = len(server.calls)
    with pytest.raises(PilotError, match="owner OAuth"):
        Drive(store, http, "root").preflight()
    assert len(server.calls) == count


def test_untrusted_upload_location_never_receives_credentials(storage):
    store, _, server, _ = storage

    def handler(request):
        if request.url.path == "/upload/drive/v3/files":
            return httpx.Response(200, headers={"Location": "https://example.invalid/upload"})
        return server.handle(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(PilotError, match="session URL"):
            Drive(store, http, "root").save("folder-10", "test.png", PNG, "image/png")
    assert not server.bytes


def test_ambiguous_search_refused(storage):
    store, _, server, _ = storage

    def handler(request):
        if str(request.url).startswith(API + "/files?"):
            return httpx.Response(200, json={"files": [{"id": "a"}, {"id": "b"}]})
        return server.handle(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(PilotError, match="Ambiguous"):
            Drive(store, http, "root").save("folder-10", "test.png", PNG, "image/png")


def test_restore_cannot_overwrite_existing_files(storage, tmp_path):
    store, drive, _, _ = storage
    with httpx.Client(transport=httpx.MockTransport(media_http)) as media:
        result = archive_batch(inventory(store), drive, media)
    output = Store(tmp_path / "out")
    restore_batch(drive, manifest(store, result), output, manifest_hash(store, result))
    before = (output.root / "101.json").read_bytes()
    with pytest.raises(FileExistsError):
        restore_batch(drive, manifest(store, result), output, manifest_hash(store, result))
    assert (output.root / "101.json").read_bytes() == before


def test_archive_cli_and_fresh_restore_no_x_configuration(storage, tmp_path, monkeypatch, capsys):
    from x_bookmark_migrator import cli

    store, _, server, _ = storage
    inventory(store, [post(attachments={"media_keys": ["photo-1"]})], includes([photo()]))
    monkeypatch.delenv("X_CLIENT_ID", raising=False)
    monkeypatch.setenv("X_ARCHIVE_DRIVE_FOLDER_ID", "root")
    real_client = httpx.Client

    def handler(request):
        if request.url.host == "pbs.twimg.com":
            return media_http(request)
        assert request.url.host != "api.x.com"
        return server.handle(request)

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )
    monkeypatch.setattr(sys, "argv", ["x-migrator", "--state-dir", str(store.root), "archive"])
    assert cli.main() == 0
    output = capsys.readouterr()
    summary = json.loads(output.out)
    assert "synthetic-secret" not in output.out and "Synthetic category" not in output.out
    fresh = Store(tmp_path / "fresh-cli")
    grant(fresh)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "x-migrator",
            "--state-dir",
            str(fresh.root),
            "archive-restore",
            "--manifest-id",
            manifest(store, summary),
            "--manifest-sha256",
            manifest_hash(store, summary),
            "--output-dir",
            str(tmp_path / "restore-cli"),
        ],
    )
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["media_files"] == 1


def test_probe_refreshes_twice_and_retrieves_same_bytes(storage, monkeypatch):
    from argparse import Namespace

    from x_bookmark_migrator import archive_cli

    store, _, server, _ = storage
    server.calls.clear()
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(server.handle), **kw),
    )
    result = archive_cli.run(
        Namespace(
            command="archive-probe",
            state_dir=store.root,
            drive_folder_id="root",
            max_asset_bytes=65536,
        )
    )
    assert result["storage_probe"] == "passed"
    assert sum(r.url.host == "oauth2.googleapis.com" for r in server.calls) == 2
    assert sum(r.url.params.get("alt") == "media" for r in server.calls) == 2


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_storage_errors_do_not_retry_or_emit_success(storage, status):
    store, _, server, _ = storage
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/upload/drive/v3/files":
            return httpx.Response(status, json={"error": {"message": "private_remote_body"}})
        return server.handle(request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(PilotError) as failure:
            Drive(store, http, "root").save("test", "synthetic.png", PNG, "image/png")
    assert "private_remote_body" not in str(failure.value)
    assert sum(r.url.path == "/upload/drive/v3/files" for r in calls) == 1
    assert not server.bytes
