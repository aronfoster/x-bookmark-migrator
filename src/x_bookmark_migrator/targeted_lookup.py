"""Repair one archived author gap from an already saved official multi-post lookup."""

import json
import os
import stat
from pathlib import Path

import httpx

from .archive import object_map, partial_source_response, plan_post, restore_batch
from .archive_repair import preview_repairs, write_repairs
from .catalog import build, checked_bytes, private_bytes, read_catalog
from .core import PilotError, Store, exact_id, validate_page
from .drive import MAX_BYTES, Drive, digest, encode
from .publication import PublicationStore
from .retained_authors import replay_author_gap
from .source import source_url
from .unbookmark import archive_rows
from .worker import catalog_index, merge_publication, require_catalog_versions


def select(batch, lookup_path, pid):
    restored, receipt_path = batch
    receipt = json.loads(private_bytes(receipt_path))
    manifest = receipt["manifest"]
    rows = list(
        map(json.loads, checked_bytes(Path(restored) / "manifest.jsonl", manifest).splitlines())
    )
    by_id = {exact_id(row["post_id"]): row for row in rows}
    if (
        receipt["schema_version"] != 1
        or not 1 <= len(by_id) == len(rows) == receipt["records"] <= 20
        or exact_id(pid) not in by_id
    ):
        raise PilotError("Expected one matching primary in a receipt-bound archive.")
    row = by_id[pid]
    payload = json.loads(checked_bytes(Path(restored) / (pid + ".json"), row["payload"]))
    fields = ("schema_version", "post_id", "category", "folders", "archive_status", "blockers")
    if (
        any(row[k] != payload[k] for k in fields)
        or row["schema_version"] != 1
        or row["archive_status"] != "blocked"
        or row["blockers"] != ["blocked_missing_author"]
        or payload["inventory_record"]["id"] != pid
    ):
        raise PilotError("Expected an author-only blocked predecessor.")
    identity = {"source_post_id": pid, "source_post_url": source_url(pid)}
    for key, value in identity.items():
        if row.get(key, value) != value or payload.get(key, value) != value:
            raise PilotError("Predecessor source identity differs.")
    posts, evidence = lookup_authors(payload, lookup_path)
    payload.update(
        identity,
        posts=posts,
        targeted_lookup=evidence,
        repair_source={"manifest": manifest, "payload": row["payload"]},
    )
    return receipt, row, [(dict(row, **identity), payload)]


def lookup_authors(payload, lookup_path):
    raw = private_bytes(lookup_path)
    try:
        lookup = json.loads(raw)
    except (ValueError, UnicodeError):
        raise PilotError("Malformed protected lookup envelope.") from None
    if (
        not isinstance(lookup, dict)
        or lookup.get("status") != 200
        or lookup.get("endpoint") != "/2/tweets"
    ):
        raise PilotError("Expected a successful official multi-post lookup.")
    request = lookup.get("request")
    if (
        not isinstance(request, dict)
        or not isinstance(request.get("ids"), str)
        or not isinstance(request.get("expansions"), str)
        or not isinstance(lookup.get("private_file"), str)
        or not lookup["private_file"]
    ):
        raise PilotError("Malformed protected lookup request or provenance.")
    requested = [exact_id(i) for i in request["ids"].split(",")]
    response = lookup.get("response")
    data, token, errors, _ = validate_page(response, "hydrate", len(requested))
    supplied = object_map(response.get("includes", {}).get("users", []), "id")
    returned = object_map(data, "id")
    if (
        not 1 <= len(set(requested)) == len(requested) <= 100
        or len(returned) != len(data)
        or not returned.keys() <= set(requested)
        or token
        or "author_id" not in request["expansions"].split(",")
    ):
        raise PilotError("Ambiguous targeted lookup request or response.")

    record = payload["inventory_record"]
    source = payload["source_evidence"]
    posts, media, blockers = plan_post(
        record["post"], source["response"].get("includes", {}), source
    )
    related = {p["id"] for p in posts}
    for p in posts:
        post = p["raw"]
        related.update(
            r["id"] for r in post.get("referenced_posts", post.get("referenced_tweets", []))
        )
    source_errors = source["response"].get("errors", []) + source.get("reference_errors", [])
    if (
        blockers != ["blocked_missing_author"]
        or not related <= returned.keys()
        or any("withheld" in returned[i] for i in related)
        or any(returned[p["id"]].get("author_id") != p["raw"].get("author_id") for p in posts)
        or partial_source_response(record, posts, {"reference_errors": errors + source_errors})
    ):
        raise PilotError("Targeted lookup lacks complete, error-free evidence for this record.")
    candidate = replay_author_gap(payload, [lookup])
    if not candidate or any("withheld" in p["author"] for p in candidate["posts"] if p["author"]):
        raise PilotError("Targeted lookup did not resolve the archived author gap.")
    if media != [
        {k: v for k, v in a.items() if k not in ("storage", "container")} for a in payload["media"]
    ] or any("storage" not in a or "container" not in a for a in payload["media"]):
        raise PilotError("Original attachment evidence differs from the captured plan.")

    # Preserve the original response verbatim. Keep only the selected lookup evidence
    # in the successor; the digest binds it to the full protected multi-item envelope.
    return candidate["posts"], {
        "sha256": digest(raw),
        "endpoint": lookup["endpoint"],
        "returned": [{"id": i, "author_id": returned[i].get("author_id")} for i in sorted(related)],
        "authors": [supplied[uid] for uid in sorted(candidate["author_sources"])],
    }


def restore_output(path, catalog):
    path.mkdir(mode=0o700, exist_ok=True)
    # mkdir inherits setgid from its parent. A crash can therefore leave either
    # 0700 or 02700 here. Finish setup only for an owned, empty private directory.
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        mode = stat.S_IMODE(info.st_mode)
        desired = 0o700 if catalog.reader_gid is None else 0o2750
        if mode != desired and mode in (0o700, 0o2700) and info.st_uid == os.getuid():
            if os.listdir(fd) or any(x.startswith("system.posix_acl") for x in os.listxattr(fd)):
                raise PilotError("Cannot change permissions on an occupied or ACL-managed restore.")
            if catalog.reader_gid is not None:
                os.fchown(fd, -1, catalog.reader_gid)
            os.fchmod(fd, desired)
    finally:
        os.close(fd)
    output = PublicationStore(path)
    if output.reader_gid != catalog.reader_gid:
        raise PilotError("Successor restore must have the catalog's reader permissions.")
    return output


def run(args):
    receipt, row, selected = select(args.batch, args.lookup, args.post_id)
    if not args.drive_folder_id or not 1024 <= args.max_asset_bytes <= MAX_BYTES:
        raise PilotError("Supply the pinned Drive destination and a supported byte limit.")
    catalog_path = Path(args.catalog_dir).absolute()
    state_path = args.state_dir.absolute()
    if state_path.resolve() != state_path:
        raise PilotError("Supply the private state directory at its real path.")
    if not catalog_path.is_dir() or catalog_path.resolve() != catalog_path:
        raise PilotError("Supply the existing catalog directory at its real path.")
    source_dir = Path(args.batch[0]).resolve()
    # A sibling has the same traversal path as the already published catalog.
    preview = preview_repairs(selected, args.max_asset_bytes)
    plan = digest(encode([preview["plan_sha256"], args.drive_folder_id, str(catalog_path)]))
    restore = catalog_path.with_name("lookup-" + plan)
    for dest in (catalog_path, restore):
        if any(dest.is_relative_to(p) or p.is_relative_to(dest) for p in (state_path, source_dir)):
            raise PilotError("Catalog and successor restore must be separate from the inputs.")
    for parent in catalog_path.parents:
        st = parent.stat()
        if st.st_uid not in (0, os.getuid()) or (
            st.st_mode & 0o022 and not (st.st_uid == 0 and st.st_mode & 0o1000)
        ):
            raise PilotError("Unsafe catalog parent directory.")
    if args.apply_plan is None:
        return {**preview, "plan_sha256": plan}
    if args.apply_plan != plan:
        raise PilotError("Targeted lookup plan differs from reviewed preview.")
    catalog = PublicationStore(catalog_path)
    state = Store(state_path)
    with state.locked():
        pin = state.read("drive-destination.private.json")
        if not pin or pin.get("root") != args.drive_folder_id or not pin.get("owner_permission_id"):
            raise PilotError("Expected the existing pinned Drive destination.")
        chain = {
            "post_id": args.post_id,
            "manifest": receipt["manifest"],
            "payload": row["payload"],
        }
        with catalog.locked():
            require_catalog_versions(catalog_index(read_catalog(catalog)), [chain])
        # Ordinary removal anchors do not consume supplemental lookup evidence.
        journal = Store(state.root / "targeted-lookup-repairs")
        with httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as http:
            drive = Drive(state, http, args.drive_folder_id, args.max_asset_bytes)
            drive.preflight()
            if archive_rows(drive, receipt).get(args.post_id) != row:
                raise PilotError("Remote predecessor differs from the selected archive.")
            media = [a["storage"] for a in selected[0][1]["media"]]
            for item in [row["payload"], *media]:
                drive.confined(item["file_id"], item["parent_id"])
            drive.verify(row["payload"])
            result = write_repairs(journal, drive, None, selected)
        saved = journal.read(result["private_receipt"])
        with httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as http:
            fresh = Drive(state, http, args.drive_folder_id, args.max_asset_bytes)
            fresh.preflight()
            manifest = saved["manifest"]
            verified_rows = archive_rows(fresh, saved)
            if list(verified_rows) != [args.post_id]:
                raise PilotError("Successor manifest differs from the selected primary.")
            for item in [verified_rows[args.post_id]["payload"], *media]:
                fresh.confined(item["file_id"], item["parent_id"])
            output = restore_output(restore, catalog)
            with output.locked():
                restored = restore_batch(
                    fresh, manifest["file_id"], output, manifest["sha256"], existing_ok=True
                )
                if output.reader_gid is not None:
                    for path in restore.iterdir():
                        if path.name != "lock" and path.stat().st_mode & 0o040 == 0:
                            raise PilotError(
                                "Restored files must be readable by the catalog reader."
                            )
                incoming = build(
                    [(restore, journal.path(result["private_receipt"]))],
                    catalog,
                    apply_curation=False,
                )
                if restored["bytes_verified"] != 1 or any(
                    a["availability"] != "local_hash_verified"
                    for a in incoming[0]["versions"][0]["media"]
                ):
                    raise PilotError("Incomplete independent successor restore.")
                merge_publication(
                    catalog,
                    incoming,
                    required_versions=[chain],
                )
        return {**restored, "catalog_promoted": True}
