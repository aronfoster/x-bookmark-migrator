"""Opt-in, disk-backed module-main fixture. Run in a network-isolated Linux namespace."""

import argparse
import contextlib
import hashlib
import io
import json
import resource
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
from test_archive import (
    DriveServer,
    grant,
    includes,
    inventory,
    manifest,
    manifest_hash,
    post,
    video,
)

from x_bookmark_migrator import large_mp4
from x_bookmark_migrator.archive import archive_batch, restore_batch
from x_bookmark_migrator.core import Store
from x_bookmark_migrator.drive import Drive


def run(root, size):
    server = DriveServer()
    cdn_file = root / "synthetic.mp4"
    remote_file = root / "mock-drive.mp4"
    with cdn_file.open("wb") as out:
        out.write(b"\0\0\0\x08ftyp\0\0\0\x08moov" + (size - 16).to_bytes(4) + b"mdat")
        out.truncate(size)
    remote_id = None
    largest = {"upload": 0, "cdn": 0, "drive_read": 0}
    counts = dict.fromkeys(largest, 0)

    class Body(httpx.SyncByteStream):
        def __init__(self, path, label):
            self.path, self.label = path, label

        def __iter__(self):
            counts[self.label] += 1
            with self.path.open("rb") as stream:
                while chunk := stream.read(65536):
                    largest[self.label] = max(largest[self.label], len(chunk))
                    yield chunk

    class Service(httpx.BaseTransport):
        def handle_request(self, req):
            nonlocal remote_id
            assert req.url.host in {
                "oauth2.googleapis.com",
                "www.googleapis.com",
                "video.twimg.com",
            }
            if req.url.host == "video.twimg.com":
                assert req.url.path == "/high.mp4" and "authorization" not in req.headers
                return httpx.Response(
                    200, headers={"Content-Length": str(size)}, stream=Body(cdn_file, "cdn")
                )
            if req.method == "PUT" and req.headers["Content-Type"] == "video/mp4":
                assert req.headers["Content-Length"] == str(size)
                remote_id = req.url.params["upload_id"]
                counts["upload"] += 1
                with remote_file.open("xb") as output:
                    for chunk in req.stream:
                        largest["upload"] = max(largest["upload"], len(chunk))
                        output.write(chunk)
                assert remote_file.stat().st_size == size
                server.objects[remote_id] = {
                    **server.sessions[remote_id],
                    "size": str(size),
                    "trashed": False,
                }
                return httpx.Response(200)
            if (
                remote_id
                and req.url.path.endswith("/" + remote_id)
                and req.url.params.get("alt") == "media"
            ):
                return httpx.Response(
                    200,
                    headers={"Content-Length": str(size)},
                    stream=Body(remote_file, "drive_read"),
                )
            # The original fixture handles only metadata and normal-profile small objects.
            req.read()
            return server.handle(req)

    client = httpx.Client

    def synthetic(**kwargs):
        return client(transport=Service(), **kwargs)

    store = Store(root / "state")
    grant(store)
    inv = inventory(store, [post(attachments={"media_keys": ["video-1"]})], includes([video()]))
    with (
        synthetic() as http,
        client(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(200, headers={"Content-Length": str(size)})
            )
        ) as old,
    ):
        drive = Drive(store, http, "root")
        drive.preflight()
        original = archive_batch(inv, drive, old)
        source = Store(root / "source")
        restore_batch(drive, manifest(store, original), source, manifest_hash(store, original))
    args = [
        "large_mp4",
        "--batch",
        str(source.root),
        str(store.path(original["private_receipt"])),
        "--item",
        "1",
        "--exact-bytes",
        str(size),
        "--state-dir",
        str(store.root),
        "--drive-folder-id",
        "root",
        "--output-dir",
        str(root / "restored"),
    ]

    def invoke(argv):
        out = io.StringIO()
        with (
            patch.object(sys, "argv", argv),
            patch.object(large_mp4.httpx, "Client", synthetic),
            contextlib.redirect_stdout(out),
        ):
            assert large_mp4.main() == 0
        return json.loads(out.getvalue())

    preview = invoke(args)
    result = invoke([*args, "--apply-plan", preview["plan_sha256"]])
    completed = store.read(result["private_receipt"])
    assert (
        completed["exact_mp4_local_verified"]
        and completed["exact_plan_sha256"] == preview["plan_sha256"]
    )
    for path in (cdn_file, remote_file, next((root / "restored").glob("*.mp4"))):
        with path.open("rb") as stream:
            assert (
                path.stat().st_size == size
                and hashlib.file_digest(stream, "sha256").hexdigest()
                == completed["local_mp4_sha256"]
            )
    assert counts["upload"] == counts["cdn"] == 1 and counts["drive_read"] >= 2
    assert max(largest.values()) <= 65536
    return {
        "size": size,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "largest_chunk_bytes": largest,
        "transfers": counts,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("size", choices=(463785057, 615780736), type=int)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(dir=args.directory) as temp:
        print(json.dumps(run(Path(temp), args.size)))
