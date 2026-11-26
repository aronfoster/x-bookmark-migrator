"""Manual, offline selection. A saved positive is not a fresh removal check."""

import json
import re
import time
from collections import defaultdict
from datetime import datetime

import httpx

from . import large_mp4
from .archive import batch_receipt
from .catalog import (
    raindrop_link,
    read_annotations,
    read_catalog,
    restored_media_filename,
    restored_media_path,
)
from .core import PilotError, Store, exact_id, validate_page
from .drive import Drive, digest, encode
from .inventory import Inventory
from .publication import publication_bytes
from .raindrop import validate_receipt
from .unbookmark import make_plan, validate_plan
from .worker import JOURNAL, Deferral, GuardedHTTP, removal_history


def latest_window(store, owner, folder):
    endpoint = f"/2/users/{exact_id(owner)}/bookmarks/folders/{exact_id(folder)}"
    latest = []
    newest = None
    for path in store.root.glob("read-*.private.json"):
        if not re.fullmatch(r"read-[0-9a-f]{24}\.private\.json", path.name):
            continue
        receipt = store.read(path.name)
        if receipt["endpoint"] != endpoint or receipt["request"] != {"max_results": "100"}:
            continue
        when = datetime.fromisoformat(receipt["retrieved_at"])
        if when.tzinfo is None or receipt["private_file"] != path.name:
            raise PilotError("Invalid saved folder window provenance.")
        if newest is None or when > newest:
            newest, latest = when, [receipt]
        elif when == newest:
            latest.append(receipt)
    if len(latest) != 1 or latest[0]["status"] != 200:
        raise PilotError("A unique successful latest saved folder window is required.")
    receipt = latest[0]
    data, _, errors, _ = validate_page(receipt["response"], "folder", 100)
    ids = [row["id"] for row in data]
    if errors or len(ids) != len(set(ids)) or len(ids) > 10000:
        raise PilotError("Ambiguous saved folder window.")
    return receipt, ids


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


def archive_anchors(store, drive_root):
    anchors = defaultdict(list)
    for path in store.root.glob("archive-*.private.json"):
        if re.fullmatch(r"archive-[0-9a-f]{24}\.private\.json", path.name):
            manifest = store.read(path.name)["manifest"]
            anchors[manifest["sha256"]].append((manifest, path.name, None, None))
    # Completed, explicitly published repairs are manual inputs only. Check
    # the frozen rows here; a manifest intent alone is never a completion.
    for path in store.root.glob("repair-*.receipt.private.json"):
        match = re.fullmatch(r"repair-([0-9a-f]{64})\.receipt\.private\.json", path.name)
        if not match:
            continue
        receipt, rows = repair_anchor(store, match[1], drive_root)
        if receipt.get("reason") == "retained_author":
            continue  # Removal verification cannot replay the separate donor evidence yet.
        if receipt["max_asset_bytes"] > large_mp4.MAX_BYTES:
            continue  # A remote-only repair cannot stand in for an exact completion.
        manifest = receipt["manifest"]
        anchors[manifest["sha256"]].append((manifest, path.name, rows, None))
    for path in store.root.glob("exact-complete-*.private.json"):
        match = re.fullmatch(r"exact-complete-([0-9a-f]{64})\.private\.json", path.name)
        if match:
            manifest, rows, source = exact_anchor(store, path.name, match[1], drive_root)
            anchors[manifest["sha256"]].append((manifest, path.name, rows, source))
    return anchors


def require_complete_history(config):
    if config.get("gates", {}).get("removal_sources_complete") is not True:
        raise PilotError("Complete removal history must be confirmed before backlog selection.")


def ordinary_repair_payload(catalog, pid, version):
    """Old receipts have no kind. Classify only the restored, receipt-bound successor."""
    receipt = version["payload_receipt"]
    for asset in version["media"]:
        if not asset.get("local_url"):
            continue
        path = restored_media_path(catalog, asset["local_url"], restored_media_filename(pid, asset))
        try:
            data = publication_bytes(path.with_name(pid + ".json"))
        except FileNotFoundError:
            continue
        if len(data) == receipt["size_bytes"] and digest(data) == receipt["sha256"]:
            payload = json.loads(data)
            return payload.get("post_id") == pid and "retained_author_source" not in payload
    return False


def select_backlog(store, catalog, config, folder, catalog_sha256):
    """Caller holds state then catalog lock. Return private bindings in saved page order."""
    require_complete_history(config)
    if digest(publication_bytes(catalog.path("catalog.jsonl"))) != catalog_sha256:
        raise PilotError("Catalog differs from the verified digest.")
    records = read_catalog(catalog)
    index = {r["post_id"]: r for r in records}
    if len(index) != len(records):
        raise PilotError("Duplicate catalog primary.")
    links = read_annotations(catalog).get("raindrop_links", {})
    inventory = Inventory.load(store)
    owner = config["owner_id"]
    if inventory.state["owner_id"] != owner:
        raise PilotError("Active inventory owner differs.")
    receipt, ids = latest_window(store, owner, folder)
    held = set(removal_history(store, config["helper_descriptors"]))
    journal = store.read(JOURNAL)
    if journal is not None:
        if journal["schema_version"] != 1:
            raise PilotError("Unsupported worker checkpoint.")
        now = time.time()
        held.update(
            pid
            for pid, item in journal["items"].items()
            if item["state"] != "queued"
            or item.get("retry_at", 0) > now
            or item["checkpoint"] != inventory.name
        )
    # A pre-delete rejection never creates a removal intent.
    for path in store.root.glob("unbookmark-report-*.private.json"):
        for outcome in store.read(path.name)["outcomes"]:
            if outcome["state"] == "blocked":
                held.add(exact_id(outcome["post_id"]))
    anchors = archive_anchors(store, config["drive_folder_id"])
    destination = store.read("drive-destination.private.json")
    if (
        not isinstance(destination, dict)
        or destination.get("root") != config["drive_folder_id"]
        or not isinstance(destination.get("owner_permission_id"), str)
        or not destination["owner_permission_id"]
    ):
        raise PilotError("Existing pinned Drive destination required.")

    candidates = []
    read_digest = digest(encode(receipt))
    for pid in ids:
        record = index.get(pid)
        post = inventory.state["posts"].get(pid)
        if pid in held or not record or not post or post["folder_ids"] != [folder]:
            continue
        original = inventory.state["folders"][folder]
        if original["id"] != folder or len(original["names"]) != 1:
            continue
        rid = record.get("raindrop_id")
        if rid is None or pid not in links:
            continue
        validate_receipt(links[pid], pid)
        if links[pid]["raindrop_id"] != rid or record.get("raindrop_url") != raindrop_link(rid):
            raise PilotError("Catalog Raindrop identity differs from its receipt.")
        for version in record["versions"]:
            if (
                version["archive_status"] != "bytes_verified"
                or version["blockers"] != []
                or version["category"] != "folder-" + folder
                or version["folders"] != [original]
            ):
                continue
            manifest = version["manifest"]
            matches = []
            for saved, name, rows, source in anchors[manifest["sha256"]]:
                if saved != manifest:
                    continue
                if rows is not None:
                    row = rows.get(pid)
                    if (
                        not row
                        or row["payload"] != version["payload_receipt"]
                        or any(
                            row[k] != version[k]
                            for k in ("category", "folders", "blockers", "archive_status")
                        )
                    ):
                        continue
                    # Untagged legacy receipts may also name a donor-dependent
                    # successor. Only its original archived bytes distinguish it.
                    if source is None and not ordinary_repair_payload(catalog, pid, version):
                        continue
                if source is not None:
                    media = [
                        a
                        for a in version["media"]
                        if a["storage"]["size_bytes"] > large_mp4.MAX_BYTES
                    ]
                    if len(media) != 1 or any(
                        media[0]["storage"].get(k) != v for k, v in source.items()
                    ):
                        continue
                matches.append((name, source))
            if len(matches) != 1:
                continue
            name, source = matches[0]
            candidates.append(
                dict(
                    owner_id=owner,
                    post_id=pid,
                    inventory_checkpoint=inventory.name,
                    inventory_record_sha256=digest(encode(post)),
                    folder=original,
                    source_read=receipt["private_file"],
                    source_read_sha256=read_digest,
                    catalog_sha256=catalog_sha256,
                    archive_receipt=name,
                    archive_receipt_sha256=digest(encode(store.read(name))),
                    **({"exact_source": source} if source is not None else {}),
                    drive_destination=destination,
                    manifest=manifest,
                    payload=version["payload_receipt"],
                    raindrop_id=rid,
                )
            )
            break
    return candidates


def staged_one(store, catalog, config, selection_sha, envelope_sha):
    """Revalidate an explicitly pinned preparation while state and catalog are locked."""
    if not all(re.fullmatch("[0-9a-f]{64}", value) for value in (selection_sha, envelope_sha)):
        raise PilotError("Expected two SHA-256 digests for staged removal.")
    name = f"backlog-plan-{selection_sha}.private.json"
    saved = store.read(name)
    if not isinstance(saved, dict) or digest(encode(saved)) != envelope_sha:
        raise PilotError("Staged plan digest differs.")
    binding = saved["backlog_binding"]
    if (
        saved.get("kind") != "backlog_preparation"
        or saved.get("schema_version") != 1
        or digest(encode(binding)) != selection_sha
        or binding
        not in select_backlog(
            store, catalog, config, binding["folder"]["id"], binding["catalog_sha256"]
        )
    ):
        raise PilotError("Staged backlog binding changed or held.")
    plan = validate_plan(saved["verified_plan"], config["owner_id"])
    verify_binding(plan, binding, store.read(binding["archive_receipt"]))
    return name, plan, binding


def verify_binding(plan, candidate, receipt):
    items = plan["items"]
    if not (
        plan["owner_id"] == candidate["owner_id"]
        and plan["drive_destination"] == candidate["drive_destination"]
        and plan["inventory_checkpoint"] == candidate["inventory_checkpoint"]
        and plan["archive_receipt"] == receipt
        and digest(encode(receipt)) == candidate["archive_receipt_sha256"]
        and receipt["manifest"] == candidate["manifest"]
        and len(items) == 1
        and items[0]["state"] == "verified"
        and items[0]["post_id"] == candidate["post_id"]
        and items[0]["folder"] == candidate["folder"]
        and items[0]["payload"] == candidate["payload"]
        and (
            "exact_source" not in candidate
            or items[0]["media_files"] == receipt["restore"]["media_files"]
        )
    ):
        raise PilotError("Drive plan differs from the selected binding.")


def prepare_one(store, catalog, config, folder, catalog_sha, source_sha, selection_sha, attested):
    """Under both locks, stage one exact plan; never publish a launcher pointer."""
    if not attested:
        raise PilotError("Explicit owner archive acceptance is required.")
    candidates = select_backlog(store, catalog, config, folder, catalog_sha)
    matches = [
        c
        for c in candidates
        if c["source_read_sha256"] == source_sha and digest(encode(c)) == selection_sha
    ]
    if len(matches) != 1:
        raise PilotError("Saved source or selected backlog binding changed.")
    candidate = matches[0]
    name = f"backlog-plan-{selection_sha}.private.json"
    gate = Deferral(store)
    gate.check()
    receipt = store.read(candidate["archive_receipt"])

    def stage(plan):
        verify_binding(plan, candidate, receipt)
        # Helper removal evidence can live outside both locked directories.
        if candidate not in select_backlog(store, catalog, config, folder, catalog_sha):
            raise PilotError("Selected backlog binding changed during Drive verification.")
        store.create(
            name,
            {
                "kind": "backlog_preparation",
                "schema_version": 1,
                "backlog_binding": candidate,
                "verified_plan": plan,
            },
        )
        return name

    existing = store.path(name).exists()
    if not existing:
        with httpx.Client(timeout=120, trust_env=False, follow_redirects=False) as http:
            drive = Drive(
                store,
                GuardedHTTP(http, gate, "drive"),
                config["drive_folder_id"],
                config["max_asset_bytes"],
            )
            make_plan(
                store,
                drive,
                candidate["archive_receipt"],
                [candidate["post_id"]],
                candidate["owner_id"],
                acceptance_confirmed=attested,
                stage_plan=stage,
                exact_source=candidate.get("exact_source"),
            )
    saved = store.read(name)
    if not (
        isinstance(saved, dict)
        and saved.get("kind") == "backlog_preparation"
        and saved.get("schema_version") == 1
        and saved.get("backlog_binding") == candidate
    ):
        raise PilotError("Existing backlog plan has a conflicting binding.")
    verify_binding(
        validate_plan(saved.get("verified_plan"), candidate["owner_id"]), candidate, receipt
    )
    return {
        "mode": "prepare_backlog_one",
        "already_staged": existing,
        "verified": 1,
        "selection_sha256": selection_sha,
        "plan_sha256": digest(encode(saved)),
        "x_api_calls": 0,
        "deleted": 0,
    }
