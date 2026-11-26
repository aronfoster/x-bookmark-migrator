"""Create a successor to a hash-bound blocked archive using its captured evidence."""

import copy
import json
from pathlib import Path

from .archive import (
    batch_receipt,
    batch_summary,
    download_media,
    media_format,
    partial_source_response,
    plan_post,
    restore_batch,
    save_manifest,
)
from .catalog import asset_key, checked_bytes, private_bytes
from .core import PilotError, exact_id
from .drive import Drive, digest, encode
from .source import source_url


def select_repairs(batches, reason):
    selected = {}
    for directory, receipt_path in batches:
        root = Path(directory)
        receipt = json.loads(private_bytes(receipt_path))
        manifest = receipt["manifest"]
        rows = list(map(json.loads, checked_bytes(root / "manifest.jsonl", manifest).splitlines()))
        if (
            receipt["schema_version"] != 1
            or len(rows) != receipt["records"]
            or not 1 <= len(rows) <= 20
        ):
            raise PilotError("Unsupported repair source batch.")
        for row in rows:
            if row["blockers"] != ["blocked_" + reason]:
                continue
            pid = exact_id(row["post_id"])
            if pid in selected:
                raise PilotError("Multiple matching source versions; narrow the repair inputs.")
            payload = json.loads(checked_bytes(root / (pid + ".json"), row["payload"]))
            fields = (
                "schema_version",
                "post_id",
                "archive_status",
                "blockers",
                "category",
                "folders",
            )
            if (
                row["schema_version"] != 1
                or row["archive_status"] != "blocked"
                or any(row[k] != payload[k] for k in fields)
            ):
                raise PilotError("Manifest/payload association mismatch.")
            record = payload["inventory_record"]
            evidence = payload["source_evidence"]
            post = record["post"]
            if (
                evidence.get("status") != 200
                or post["id"] != pid
                or post not in evidence["response"]["data"]
            ):
                raise PilotError("Incomplete original primary read.")
            posts, assets, blockers = plan_post(
                post, evidence["response"].get("includes", {}), evidence
            )
            if partial_source_response(record, posts, evidence):
                raise PilotError("Incomplete original primary read.")
            if blockers or posts != payload["posts"]:
                raise PilotError("Captured post plan is incomplete or differs from archived posts.")
            prior = {asset_key(a): a for a in payload["media"]}
            if len(prior) != len(payload["media"]):
                raise PilotError("Ambiguous original attachment identity.")
            missing = 0
            for asset in assets:
                old = prior.pop(asset_key(asset), None)
                if old is None:
                    if reason != "media_count":
                        raise PilotError("Byte-limit archive lacks an original attachment entry.")
                else:
                    original = {
                        k: v for k, v in old.items() if k not in ("blocked", "storage", "container")
                    }
                    if asset != original:
                        raise PilotError("Captured media plan differs from original archive.")
                    if "storage" in old and "blocked" not in old:
                        asset.update(storage=old["storage"], container=old["container"])
                        continue
                    if reason != "byte_limit" or old.get("blocked") != reason or "storage" in old:
                        raise PilotError("Unexpected original media failure.")
                missing += 1
            if prior or not missing:
                raise PilotError("Repair would omit stored media or recover no attachments.")
            payload["media"] = assets
            for target in (payload, row):
                for key, value in (("source_post_id", pid), ("source_post_url", source_url(pid))):
                    if target.setdefault(key, value) != value:
                        raise PilotError("Archived source identity mismatch.")
            payload["repair_source"] = {"manifest": manifest, "payload": row["payload"]}
            selected[pid] = (row, payload)
    if not 1 <= len(selected) <= 20:
        raise PilotError("Select source batches containing 1–20 matching records.")
    return [selected[pid] for pid in sorted(selected)]


def preview_repairs(selected, max_bytes):
    return {
        "records": len(selected),
        "media_to_fetch": sum("storage" not in a for _, p in selected for a in p["media"]),
        "media_to_reverify": sum("storage" in a for _, p in selected for a in p["media"]),
        "plan_sha256": digest(encode([max_bytes, selected])),
        "x_api_calls": 0,
    }


def verify_author_sources(drive, payload):
    chains = (payload["repair_source"], payload["retained_author_source"])
    receipts = [chain[key] for chain in chains for key in ("manifest", "payload")]
    if any(not isinstance(asset.get("storage"), dict) for asset in payload["media"]):
        raise PilotError("Missing author repair media receipt.")
    for receipt in receipts + [asset["storage"] for asset in payload["media"]]:
        parents = drive.metadata(receipt["file_id"]).get("parents")
        if parents != [receipt["parent_id"]] or drive.metadata(parents[0]).get("parents") != [
            drive.root
        ]:
            raise PilotError("Author evidence is outside the pinned archive.")
    for chain in chains:
        rows = [json.loads(line) for line in drive.verify(chain["manifest"]).splitlines()]
        matches = [r for r in rows if r["post_id"] == chain["post_id"]]
        if len(matches) != 1 or matches[0]["payload"] != chain["payload"]:
            raise PilotError("Author evidence manifest/payload binding differs.")
        original = json.loads(drive.verify(chain["payload"]))
        if original["post_id"] != chain["post_id"] or (
            "envelope_sha256" in chain
            and digest(encode(original["source_evidence"])) != chain["envelope_sha256"]
        ):
            raise PilotError("Author evidence identity differs.")


def write_repairs(store, drive, media_http, selected, *, author_restore=None):
    plan = preview_repairs(selected, drive.max_bytes)["plan_sha256"]
    name = f"repair-{plan}.receipt.private.json"
    pending_name = f"repair-{plan}.manifest-intent.private.json"
    binding = {"plan_sha256": plan, "max_asset_bytes": drive.max_bytes, "drive_root": drive.root}
    if author_restore is not None:
        binding["reason"] = "retained_author"
    # Receipts can be copied between state directories. Check the actual remote
    # source batch belongs to this archive root before creating any successors.
    verified = set()
    for _, payload in selected:
        if "retained_author_source" in payload:
            if author_restore is None:
                raise PilotError("Author repairs require independent restore.")
            verify_author_sources(drive, payload)
            continue
        manifest = payload["repair_source"]["manifest"]
        identity = (manifest["file_id"], manifest["sha256"])
        if identity not in verified:
            folder = drive.metadata(manifest["file_id"]).get("parents", [])
            if len(folder) != 1 or drive.metadata(folder[0]).get("parents") != [drive.root]:
                raise PilotError("Repair source manifest is outside the selected Drive archive.")
            drive.verify(manifest)
            verified.add(identity)
    receipt, intent = store.read(name), store.read(pending_name)
    new_receipt = receipt is None
    if intent is None:
        if receipt is not None:
            raise PilotError("Repair receipt lacks its manifest intent.")
        rows = repair_rows(drive, media_http, selected)
        # Freeze the exact successor manifest BEFORE any manifest create. Even
        # if the CDN changes after a lost upload acknowledgement, reuse these rows.
        store.create(pending_name, {**binding, "rows": rows})
    else:
        rows = intent["rows"]
        if intent != {**binding, "rows": rows} or len(rows) != len(selected):
            raise PilotError("Repair manifest intent differs from the reviewed plan.")
        for row, (source, expected) in zip(rows, selected, strict=True):
            if row != successor_row(source, row["payload"]):
                raise PilotError("Successor row differs from the reviewed plan.")
            # Verify the stored payload belongs to this exact plan, including
            # its original receipts and all retained source fields.
            payload = json.loads(drive.verify(row["payload"]))
            assets = payload["media"]
            if len(assets) != len(expected["media"]):
                raise PilotError("Successor media count differs from the reviewed plan.")
            for asset, planned in zip(assets, expected["media"], strict=True):
                if receipt is None:
                    verify_stored_media(drive, asset)
                if "storage" not in planned:
                    del asset["storage"], asset["container"]
            if payload != dict(
                expected, archive_status="bytes_verified", blockers=[], deletion_eligible=False
            ):
                raise PilotError("Successor payload differs from the reviewed plan.")
    if receipt is None:
        manifest = save_manifest(drive, rows)
        receipt = {**batch_receipt(manifest, rows, plan), **binding}
    else:
        manifest = receipt["manifest"]
        expected_receipt = {**batch_receipt(manifest, rows, plan), **binding}
        if receipt != expected_receipt or drive.verify(manifest) != b"".join(map(encode, rows)):
            raise PilotError("Repair completion receipt differs from its manifest intent.")
    if author_restore is not None:
        fresh = Drive(store, drive.http, drive.root, drive.max_bytes)
        fresh.preflight()
        for _, payload in selected:
            verify_author_sources(fresh, payload)
        with author_restore.locked():
            restore_batch(
                fresh, manifest["file_id"], author_restore, manifest["sha256"], existing_ok=True
            )
    if new_receipt:
        store.create(name, receipt)
    return batch_summary(name, receipt)


def successor_row(row, saved):
    return dict(
        row, payload=saved, blockers=[], archive_status="bytes_verified", deletion_eligible=False
    )


def verify_stored_media(drive, asset):
    storage = asset["storage"]
    mime, extension = media_format(drive.verify(storage), asset["logical_type"])
    if extension != asset["container"] or mime != storage["mime_type"]:
        raise PilotError("Verified media format differs from its recorded container or MIME type.")


def repair_rows(drive, media_http, selected):
    rows = []
    for row, planned in selected:
        payload = copy.deepcopy(planned)
        for asset in payload["media"]:
            kind = asset["logical_type"]
            if "storage" in asset:
                verify_stored_media(drive, asset)
                continue
            data, mime, extension = download_media(
                media_http, asset["source_url"], kind, drive.max_bytes
            )
            name = f"{asset['post_id']}_{asset['index']:02d}.{extension}"
            asset.update(storage=drive.save(row["category"], name, data, mime), container=extension)
            del data
        payload.update(archive_status="bytes_verified", blockers=[], deletion_eligible=False)
        saved = drive.save(
            row["category"], row["post_id"] + ".json", encode(payload), "application/json"
        )
        rows.append(successor_row(row, saved))
    return rows
