"""Receipt and restored-payload evidence shared by offline reporting and staging."""

import json
import re
from collections import defaultdict
from functools import cached_property
from pathlib import Path

from . import large_mp4
from .archive import batch_receipt
from .catalog import checked_bytes, restored_media_filename, restored_media_path
from .core import PilotError, Store, exact_id
from .drive import digest, encode
from .publication import publication_bytes


def repair_anchor(store, plan, drive_root):
    receipt = store.read(f"repair-{plan}.receipt.private.json")
    intent = store.read(f"repair-{plan}.manifest-intent.private.json")
    if not intent or not receipt or receipt.get("plan_sha256") != plan:
        raise PilotError("Repair receipt differs from its plan.")
    manifest, rows = receipt["manifest"], intent["rows"]
    binding = dict(
        plan_sha256=plan, max_asset_bytes=intent["max_asset_bytes"], drive_root=drive_root
    )
    if receipt.get("reason") == "retained_author":
        binding["reason"] = "retained_author"
    by_id = {exact_id(row["post_id"]): row for row in rows}
    if (
        intent != {**binding, "rows": rows}
        or receipt != {**batch_receipt(manifest, rows, plan), **binding}
        or digest(b"".join(map(encode, rows))) != manifest["sha256"]
        or not 1 <= len(by_id) == len(rows) <= 20
    ):
        raise PilotError("Repair completion does not match its frozen manifest.")
    return receipt, by_id


def exact_anchor(store, name, plan, drive_root):
    completed = store.read(name)
    inner = completed["plan_sha256"]
    parent = store.root / "exact-remote-only"
    journal = parent / plan
    if (
        not re.fullmatch("[0-9a-f]{64}", inner)
        or parent.is_symlink()
        or journal.is_symlink()
        or not journal.is_dir()
    ):
        raise PilotError("Missing or invalid exact repair journal.")
    receipt, rows = repair_anchor(Store(journal), inner, drive_root)
    source = store.read(f"exact-mp4-source-{plan}.private.json")
    size = receipt["max_asset_bytes"]
    media_files = completed["restore"]["media_files"]
    if (
        not isinstance(source, dict)
        or source.keys() != {"file_id", "parent_id", "size_bytes", "sha256"}
        or type(size) is not int
        or not large_mp4.MAX_BYTES < size <= large_mp4.CEILING
        or type(source["size_bytes"]) is not int
        or source["size_bytes"] != size
        or not re.fullmatch("[0-9a-f]{64}", source["sha256"])
        or len(rows) != 1
        or receipt["bytes_verified"] != 1
        or type(media_files) is not int
        or media_files < 1
    ):
        raise PilotError("Exact completion differs from its repair or local restore.")
    restore = dict(
        records=1,
        bytes_verified=1,
        blocked=0,
        deletion_eligible=False,
        media_files=media_files,
        x_api_calls=0,
    )
    expected = dict(
        receipt,
        exact_plan_sha256=plan,
        local_mp4_sha256=source["sha256"],
        exact_mp4_local_verified=True,
        restore=restore,
    )
    if encode(completed) != encode(expected):
        raise PilotError("Exact completion differs from its repair or local restore.")
    return receipt["manifest"], rows, source


def read_anchor(store, name, drive_root):
    if re.fullmatch(r"archive-[0-9a-f]{24}\.private\.json", name):
        return store.read(name)["manifest"], name, None, None
    if match := re.fullmatch(r"repair-([0-9a-f]{64})\.receipt\.private\.json", name):
        receipt, rows = repair_anchor(store, match[1], drive_root)
        if (
            receipt.get("reason") != "retained_author"
            and receipt["max_asset_bytes"] <= large_mp4.MAX_BYTES
        ):
            return receipt["manifest"], name, rows, None
    if match := re.fullmatch(r"exact-complete-([0-9a-f]{64})\.private\.json", name):
        manifest, rows, source = exact_anchor(store, name, match[1], drive_root)
        return manifest, name, rows, source
    return None


def archive_anchors(store, drive_root, *, report_only=False):
    # Only top-level completion receipts can support ordinary removal. The
    # targeted-lookup journal is separate; a manifest intent is never a receipt.
    anchors = defaultdict(list)
    for path in store.root.glob("*.private.json"):
        try:
            anchor = read_anchor(store, path.name, drive_root)
            if anchor is not None:
                anchors[anchor[0]["sha256"]].append(anchor)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, PilotError):
            if not report_only:
                raise
            # No removable classification from unreadable or inconsistent evidence.
    return anchors


class RestoredRepairs:
    """Classify legacy untagged successors from verified bytes; index each restore root once."""

    def __init__(self, catalog, restore_dir=None):
        self.catalog = catalog
        self.roots = {catalog.root.parent}
        if restore_dir is not None:
            self.roots.add(Path(restore_dir))

    @cached_property
    def directories(self):
        by_hash = defaultdict(list)
        for root in self.roots:
            if any(part.is_symlink() for part in (root, *root.parents)) or not root.is_dir():
                continue
            try:
                children = list(root.iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_symlink() or not child.is_dir():
                    continue
                try:
                    raw = publication_bytes(child / "manifest.jsonl")
                except (OSError, PilotError):
                    continue
                by_hash[digest(raw)].append(child)
        return by_hash

    def paths(self, pid, version):
        for asset in version["media"]:
            if asset.get("local_url"):
                try:
                    media = restored_media_path(
                        self.catalog, asset["local_url"], restored_media_filename(pid, asset)
                    )
                except (OSError, PilotError):
                    continue
                yield media.with_name(pid + ".json")
        for directory in self.directories.get(version["manifest"]["sha256"], ()):
            yield directory / (pid + ".json")

    def kind(self, pid, version):
        seen = set()
        for path in self.paths(exact_id(pid), version):
            if path in seen or any(parent.is_symlink() for parent in path.parents):
                continue
            seen.add(path)
            try:
                payload = json.loads(checked_bytes(path, version["payload_receipt"]))
            except (OSError, ValueError, PilotError, TypeError, KeyError):
                continue
            if isinstance(payload, dict) and payload.get("post_id") == pid:
                if "retained_author_source" in payload or "targeted_lookup" in payload:
                    return "supplemental"
                return "ordinary"
        return None


def removable_version(restores, pid, version, anchors):
    for manifest, _, rows, source in anchors.get(version["manifest"]["sha256"], ()):
        if manifest != version["manifest"]:
            continue
        if rows is None:
            return True
        if rows.get(pid, {}).get("payload") != version["payload_receipt"]:
            continue
        if source is not None or restores.kind(pid, version) == "ordinary":
            return True
    return False
