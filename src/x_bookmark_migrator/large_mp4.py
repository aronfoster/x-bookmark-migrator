"""Explicit single-record repair and private restore of a captured oversized MP4."""

import argparse
import hashlib
import json
import mmap
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import httpx

from .archive import restore_batch, source_url
from .archive_repair import select_repairs, write_repairs
from .core import PilotError, Store
from .drive import API, FOLDER, MAX_BYTES, Drive, checked, digest, drive_id, encode

CEILING = 640 << 20
CHUNK = 64 << 10


class UploadMap(mmap.mmap):
    """Read-only file buffer for Drive hashes, container inspection and HTTPX upload."""

    def __iter__(self):
        return iter(lambda: self.read(CHUNK), b"")

    def seek(self, offset, whence=os.SEEK_SET):
        super().seek(offset, whence)
        return self.tell()  # HTTPX uses the return value to set Content-Length.


def check_container(data):
    # Keep malformed input from making the general container validator retain
    # millions of distinct untrusted box tags. Scan only top-level headers.
    missing = {b"ftyp", b"moov", b"mdat"}
    offset = 0
    for _ in range(65536):
        if offset + 8 > len(data):
            break
        length = int.from_bytes(data[offset : offset + 4], "big")
        tag = data[offset + 4 : offset + 8]
        header = 8
        if length == 1:
            header = 16
            length = int.from_bytes(data[offset + 8 : offset + header], "big")
        elif length == 0:
            length = len(data) - offset
        if length < header or offset + length > len(data):
            break
        missing.discard(tag)
        offset += length
        if offset == len(data) and not missing:
            return
    raise PilotError("Incomplete or unsupported large MP4 box layout.")


def mapped_response(response, root, size, *, expected_hash=None):
    checked(response, (200,))
    length = response.headers.get("content-length")
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise PilotError("Encoded MP4 transfer is unsupported.")
    if length is not None and length != str(size):
        raise PilotError("MP4 Content-Length differs from the reviewed size.")
    with tempfile.TemporaryFile(dir=root) as stream:
        written = 0
        hasher = hashlib.sha256() if expected_hash is not None else None
        for chunk in response.iter_bytes(CHUNK):
            written += len(chunk)
            if written > size:
                raise PilotError("MP4 exceeds the reviewed size.")
            stream.write(chunk)
            if hasher is not None:
                hasher.update(chunk)
        if written != size:
            raise PilotError("MP4 transfer ended before the reviewed size.")
        if hasher is not None and hasher.hexdigest() != expected_hash:
            raise PilotError("Drive MP4 differs from source receipt.")
        stream.flush()
        data = UploadMap(stream.fileno(), size, access=mmap.ACCESS_READ)
        data.madvise(mmap.MADV_SEQUENTIAL)
        return data


class ExactDrive(Drive):
    def __init__(self, store, http, root, size):
        super().__init__(store, http, root, MAX_BYTES)
        if type(size) is not int or not MAX_BYTES < size <= CEILING:
            raise PilotError("Exact MP4 size must exceed the normal bound and be at most 640 MiB.")
        self.max_bytes = size

    def download(self, file_id, limit=None):
        self.confined(file_id)
        return super().download(file_id, limit)

    def verify(self, receipt):
        self.confined(receipt["file_id"], receipt["parent_id"])
        if type(receipt["size_bytes"]) is not int:
            raise PilotError("Receipt size must be an integer.")
        if not isinstance(receipt.get("sha256"), str) or not re.fullmatch(
            "[0-9a-f]{64}", receipt["sha256"]
        ):
            raise PilotError("Receipt lacks a valid SHA-256 digest.")
        if receipt["size_bytes"] == self.max_bytes and receipt["mime_type"] == "video/mp4":
            with self.http.stream(
                "GET",
                API + "/files/" + drive_id(receipt["file_id"]),
                params={"alt": "media"},
                headers=self.headers(),
            ) as response:
                data = mapped_response(
                    response, self.store.root, self.max_bytes, expected_hash=receipt["sha256"]
                )
            try:
                check_container(data)
            except PilotError:
                data.close()
                raise
            return data
        if receipt["size_bytes"] > MAX_BYTES:
            raise PilotError("Only the exact reviewed MP4 may exceed the normal bound.")
        return super().verify(receipt)


def check_space(path, needed):
    # Fail closed on unknown/volatile backing. Check the actual mount (incl. bind mounts).
    fd = os.open(path, os.O_DIRECTORY)
    try:
        lines = Path(f"/proc/self/fdinfo/{fd}").read_text().splitlines()
        mount = next(line.split()[1] for line in lines if line.startswith("mnt_id:"))
    finally:
        os.close(fd)
    mounts = Path("/proc/self/mountinfo").read_text().splitlines()
    fstype = next(line.split(" - ")[1].split()[0] for line in mounts if line.split()[0] == mount)
    if fstype not in ("ext4", "xfs", "btrfs", "zfs") or shutil.disk_usage(path).free < needed:
        raise PilotError("Large profile requires sufficient space on a recognized disk filesystem.")


def verify_preparation_mp4(drive, assets, source):
    """Re-read only the pinned oversized asset under the exact profile."""
    matches = [asset for asset in assets if asset["storage"]["size_bytes"] > MAX_BYTES]
    if len(matches) != 1:
        raise PilotError("Exact preparation requires one oversized MP4.")
    asset = matches[0]
    saved = asset["storage"]
    if (
        asset["logical_type"] not in ("video", "animated_gif")
        or asset["container"] != "mp4"
        or saved["mime_type"] != "video/mp4"
        or any(saved.get(k) != v for k, v in source.items())
    ):
        raise PilotError("Archive media differs from the pinned exact MP4.")
    exact = ExactDrive(drive.store, drive.http, drive.root, source["size_bytes"])
    exact.preflight()
    check_space(drive.store.root, exact.max_bytes)
    # ExactDrive streams, checks the hash and validates the mapped container.
    exact.verify(saved).close()
    return asset


def restore_exact(drive, output, manifest_id, manifest_hash):
    raw = drive.download(manifest_id, 8 << 20)
    if digest(raw) != manifest_hash or len(raw.splitlines()) != 1:
        raise PilotError("Exact restore requires the reviewed single-record manifest.")
    row = json.loads(raw)
    payload = json.loads(drive.verify(row["payload"]))
    if row["blockers"] or row["archive_status"] != "bytes_verified":
        raise PilotError("Exact restore requires a verified successor.")
    assets = payload["media"]
    matches = [asset for asset in assets if asset["storage"]["size_bytes"] > MAX_BYTES]
    if len(matches) != 1:
        raise PilotError("Expected exactly one oversized MP4.")
    asset = matches[0]
    saved = asset["storage"]
    if saved["size_bytes"] != drive.max_bytes or saved["mime_type"] != "video/mp4":
        raise PilotError("Stored MP4 does not match the selected exact profile.")
    needed = (
        sum(a["storage"]["size_bytes"] for a in assets) + len(raw) + row["payload"]["size_bytes"]
    )
    shared = drive.store.root.stat().st_dev == output.root.stat().st_dev
    check_space(output.root, needed + (drive.max_bytes if shared else 0))
    check_space(drive.store.root, drive.max_bytes)
    restored = restore_batch(drive, manifest_id, output, manifest_hash)
    path = output.path(f"{row['post_id']}_{asset['post_id']}_{asset['index']:02d}.mp4")
    with path.open("rb") as stream:
        if (
            os.fstat(stream.fileno()).st_size != drive.max_bytes
            or hashlib.file_digest(stream, "sha256").hexdigest() != saved["sha256"]
        ):
            raise PilotError("Reopened local MP4 differs from its Drive receipt.")
    return {"restore": restored, "exact_mp4_local_verified": True}


def save_exact_asset(state, drive, cdn, row, asset, plan):
    intent_name = "exact-mp4-source-" + plan + ".private.json"
    intent = state.read(intent_name)
    size = drive.max_bytes
    if intent is not None:
        if not isinstance(intent, dict) or intent["size_bytes"] != size:
            raise PilotError("Unrecognized or inconsistent exact MP4 transfer intent.")
        parent, source_hash = intent["parent_id"], intent["sha256"]
        token = digest(encode([drive.root, parent, "bytes:" + source_hash]))
        journal_id = drive.journal.get(token)
        if journal_id is not None and journal_id != intent["file_id"]:
            raise PilotError("Drive allocation differs from pinned transfer intent.")
        folder = drive.metadata(parent)
        if folder.get("mimeType") != FOLDER or folder.get("parents") != [drive.root]:
            raise PilotError("Pinned transfer folder is outside the selected archive root.")
        obj = drive.metadata(intent["file_id"], missing_ok=True)
        if obj is not None:
            if (
                obj.get("parents") != [parent]
                or obj.get("size") != str(size)
                or obj.get("mimeType") != "video/mp4"
                or obj.get("appProperties") != {"xm_key": token, "sha256": source_hash}
            ):
                raise PilotError("Existing Drive object differs from pinned transfer intent.")
            saved = {**intent, "mime_type": "video/mp4", "name": obj["name"]}
            drive.verify(saved).close()
            return saved
        if journal_id is None:
            raise PilotError(
                "Pinned transfer is missing from both Drive and its allocation journal."
            )
    with (
        cdn.stream(
            "GET", source_url(asset["source_url"]), headers={"Accept-Encoding": "identity"}
        ) as response,
        mapped_response(response, state.root, size) as data,
    ):
        check_container(data)
        source_hash = digest(data)
        if intent is None:
            parent = drive.folder(row["category"])
            file_id, _ = drive.allocate(parent, "bytes:" + source_hash)
            intent = dict(file_id=file_id, parent_id=parent, size_bytes=size, sha256=source_hash)
            state.create(intent_name, intent)
        if intent["sha256"] != source_hash:
            raise PilotError("CDN bytes differ from the original upload intent.")
        saved = drive.put(
            parent,
            "bytes:" + source_hash,
            f"{asset['post_id']}_{asset['index']:02d}.mp4",
            data,
            "video/mp4",
        )
        if any(saved[k] != v for k, v in intent.items()):
            raise PilotError("Upload receipt differs from pinned transfer intent.")
        return saved


def run(args):
    size = args.exact_bytes
    if not MAX_BYTES < size <= CEILING:
        raise PilotError("Unsupported exact MP4 size.")
    if args.manifest:
        if args.item is not None or args.apply_plan is not None:
            raise PilotError("Repair selectors cannot be used for a standalone restore.")
        state, output = Store(args.state_dir), Store(args.output_dir)
        if output.root == state.root or any(output.root.iterdir()):
            raise PilotError("Use a fresh, empty private restore directory.")
        with (
            state.locked(),
            output.locked(),
            httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as http,
        ):
            drive = ExactDrive(state, http, args.drive_folder_id, size)
            drive.preflight()
            return restore_exact(drive, output, *args.manifest)
    candidates = select_repairs([args.batch], "byte_limit")
    if args.item is None or not 1 <= args.item <= len(candidates):
        raise PilotError("Repair item outside the selected sources.")
    row, payload = candidates[args.item - 1]
    missing = [a for a in payload["media"] if "storage" not in a]
    if len(missing) != 1 or missing[0]["logical_type"] not in ("video", "animated_gif"):
        raise PilotError("Exact profile requires one missing MP4 on this record.")
    asset = missing[0]
    selected = [(row, payload)]
    plan = digest(encode(["exact-mp4-v1", args.drive_folder_id, size, selected]))
    if args.apply_plan is None:
        return {"plan_sha256": plan, "records": 1, "exact_bytes": size, "x_api_calls": 0}
    if args.apply_plan != plan:
        raise PilotError("Repair differs from the reviewed exact MP4 plan.")
    state, output = Store(args.state_dir), Store(args.output_dir)
    if output.root == state.root or any(output.root.iterdir()):
        raise PilotError("Use a fresh, empty private restore directory.")
    with (
        state.locked(),
        output.locked(),
        httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as http,
        httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as cdn,
    ):
        local_size = size + sum(
            a["storage"]["size_bytes"] for a in payload["media"] if "storage" in a
        )
        shared = output.root.stat().st_dev == state.root.stat().st_dev
        check_space(state.root, 2 * size + (local_size if shared else 0) + (16 << 20))
        check_space(output.root, local_size + (16 << 20))
        drive = ExactDrive(state, http, args.drive_folder_id, size)
        drive.preflight()
        manifest = payload["repair_source"]["manifest"]
        drive.verify(manifest)
        saved = save_exact_asset(state, drive, cdn, row, asset, plan)
        asset.update(storage=saved, container="mp4")
        # Delegate successor intent, provenance and crash recovery. No missing
        # assets remain; write_repairs verifies each seeded receipt from Drive.
        remote = Store(state.root / "exact-remote-only" / plan)
        result = write_repairs(remote, drive, cdn, selected)
        receipt = remote.read(result["private_receipt"])
        successor = receipt["manifest"]
        proof = restore_exact(drive, output, successor["file_id"], successor["sha256"])
        completed = {
            **receipt,
            "exact_plan_sha256": plan,
            "local_mp4_sha256": saved["sha256"],
            **proof,
        }
        name = "exact-complete-" + plan + ".private.json"
        prior = state.read(name)
        if prior is None:
            state.create(name, completed)
        elif prior != completed:
            raise PilotError("Prior exact completion differs from verified restore.")
        return {**result, **proof, "private_receipt": name}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--batch", nargs=2, metavar=("RESTORE_DIR", "RECEIPT"))
    mode.add_argument("--manifest", nargs=2, metavar=("DRIVE_ID", "SHA256"))
    parser.add_argument("--item", type=int)
    parser.add_argument("--exact-bytes", type=int, required=True)
    parser.add_argument("--drive-folder-id", required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--apply-plan")
    try:
        print(json.dumps(run(parser.parse_args())))
    except PilotError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (
        httpx.HTTPError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        StopIteration,
    ):
        print("Local state, filesystem or transport failed; no automatic retry.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
