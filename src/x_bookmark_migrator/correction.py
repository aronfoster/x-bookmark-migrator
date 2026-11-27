"""One owner-reviewed excerpt edit. Callers use the protected writer identity."""

import json
import re
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from .catalog import (
    annotate,
    raindrop_link,
    read_annotations,
    read_catalog,
    save_annotations,
    validate_description,
    verify_description_bytes,
)
from .core import PilotError
from .inventory import now
from .raindrop import inspect_inventory
from .source import post_id, raindrop_post_id


def resolve(adapter, records, link):
    url = urlsplit(link)
    if url.scheme == "https" and url.netloc == "app.raindrop.io":
        path = re.fullmatch(r"/my/-?[0-9]+(?:/item/([1-9][0-9]*))?/?", url.path)
        params = parse_qs(url.query).get("item", [])
        ids = ([path[1]] if path and path[1] else []) + params
        if not path or len(ids) != 1 or not re.fullmatch(r"[1-9][0-9]*", ids[0]):
            raise PilotError("Expected one Raindrop item link.")
        return adapter.read_active_item(int(ids[0]))
    pid = post_id(link)
    rid = next((r.get("raindrop_id") for r in records if r["post_id"] == pid), None)
    if rid is None:
        match = inspect_inventory(adapter.enumerate_now(), pid)
        if match["state"] != "active":
            raise PilotError("Original link does not resolve to one active Raindrop item.")
        rid = match["raindrop_ids"][0]
    item = adapter.read_active_item(rid)
    if raindrop_post_id(item["link"]) != pid:
        raise PilotError("Raindrop primary link differs from requested post.")
    return item


def source_primary(link):
    # A directly addressed Raindrop can itself link to an X profile or short URL.
    try:
        return post_id(link)
    except PilotError:
        return None


def local_record(records, item):
    pid = source_primary(item["link"])
    matched = None
    for record in records:
        same_primary = record["post_id"] == pid
        rid = record.get("raindrop_id")
        if rid is not None and (rid == item["_id"]) != same_primary:
            raise PilotError("Catalog and Raindrop identities disagree.")
        if same_primary:
            if matched:
                raise PilotError("Multiple local saved primaries.")
            matched = record
    return matched


def attachments(record):
    return [a for v in record["versions"] for a in v["media"]] if record else []


def local_target(output, record, key, sha):
    media = attachments(record)
    if not media and key is None and sha is None:
        if record:
            return read_annotations(output).get("bookmark_descriptions", {}).get(record["post_id"])
        return None
    selected = [
        a for a in media if a["asset_id"] == key and a.get("storage", {}).get("sha256") == sha
    ]
    if not selected or not sha:
        raise PilotError("Select the exact attachment and digest from inspection.")
    verify_description_bytes(output, selected, sha)
    return read_annotations(output).get(key, {}).get("descriptions", {}).get(sha)


def snapshot(item):
    # Canonical JSON preserves type distinctions (e.g. true versus 1).
    return {k: json.dumps(v, sort_keys=True) for k, v in item.items() if k != "lastUpdate"}


def attachment_choices(media):
    choices = {}
    for asset in media:
        sha = asset.get("storage", {}).get("sha256")
        identity = (asset["asset_id"], sha)
        if identity not in choices or asset["availability"] == "local_hash_verified":
            choices[identity] = {
                "asset_id": asset["asset_id"],
                "sha256": sha,
                "type": asset["logical_type"],
                "availability": asset["availability"],
                "description": asset.get("description"),
            }
    return list(choices.values())


def receipt(edit):
    return {
        k: edit[k]
        for k in (
            "preview_id",
            "status",
            "raindrop_id",
            "raindrop_url",
            "source_post_id",
            "asset_id",
            "sha256",
            "description",
            "reconciles_preview_id",
            "changed_fields",
            "approved_at",
            "verified_at",
        )
        if k in edit
    }


def run(adapter, output, args):
    with output.locked():
        records = read_catalog(output)
        if args.action in ("inspect", "preview"):
            item = resolve(adapter, records, args.link)
            record = local_record(records, item)
            media = attachments(record)
            view = {
                "raindrop_id": item["_id"],
                "raindrop_url": raindrop_link(item["_id"]),
                "source_post_id": source_primary(item["link"]),
                "link": item["link"],
                "title": item.get("title"),
                "excerpt": item.get("excerpt"),
                "provider_media": item.get("media", []),
                "cover": item.get("cover"),
                "attachments": attachment_choices(media),
            }
            if args.action == "inspect":
                return view
            validate_description(args.description)
            prior = local_target(output, record, args.asset_id, args.sha256)
            if item.get("excerpt") == args.description:
                raise PilotError("Proposed excerpt already present.")
            edit = dict(
                preview_id=uuid4().hex,
                status="preview",
                catalog=str(output.root),
                raindrop_id=item["_id"],
                raindrop_url=view["raindrop_url"],
                source_post_id=view["source_post_id"],
                local=record is not None,
                before=item,
                local_before=prior,
                asset_id=args.asset_id,
                sha256=args.sha256,
                description=args.description,
            )
            adapter.store.create(f"correction-{edit['preview_id']}.private.json", edit)
            return {**view, **receipt(edit), "previous_local_description": prior}

        if not re.fullmatch(r"[a-f0-9]{32}", args.preview_id):
            raise PilotError("Invalid preview ID.")
        name = f"correction-{args.preview_id}.private.json"
        with adapter.store.locked():
            edit = adapter.store.read(name)
            if not edit or edit["catalog"] != str(output.root):
                raise PilotError("Unknown preview for this catalog.")
            if args.action == "decline":
                if edit["status"] not in ("preview", "declined"):
                    raise PilotError("This decision has already been approved.")
                edit["status"] = "declined"
                adapter.store.write(name, edit)
                return receipt(edit)
            return decide(
                adapter, output, records, name, edit, reconcile=args.action == "reconcile"
            )


def decide(adapter, output, records, name, edit, *, reconcile):
    if reconcile and (edit["status"] != "attempted" or "reconciles_preview_id" in edit):
        raise PilotError("Reconciliation needs the original attempted write.")
    if edit["status"] in ("verified", "reconciled"):
        return receipt(edit)
    if edit["status"] not in ("preview", "attempted"):
        raise PilotError("This preview is closed. Inspect the item before a new proposal.")
    rid = edit["raindrop_id"]
    current = adapter.read_active_item(rid)
    record = local_record(records, current)
    if (record is not None) != edit["local"]:
        raise PilotError("Local bookmark membership changed since preview.")
    key, sha, description = edit["asset_id"], edit["sha256"], edit["description"]
    prior = local_target(output, record, key, sha)
    expected = {**edit["before"], "excerpt": description}
    if reconcile:
        return reconciliation_preview(adapter.store, edit, current, prior)
    local_only = "reconciles_preview_id" in edit
    if edit["status"] == "preview":
        if snapshot(current) != snapshot(edit["before"]) or prior != edit["local_before"]:
            raise PilotError("Preview is stale. Inspect and preview again.")
        edit.update(status="attempted", approved_at=now())
        adapter.store.write(name, edit)  # Durable intent before the only PUT.
        if not local_only:
            try:
                adapter.put_excerpt(rid, description)
            except PilotError as exc:
                provider = (exc.summary or {}).get("provider") or {}
                status = str(provider.get("http_status", ""))
                if status.startswith("4") and status != "408":
                    edit["status"] = "rejected"
                    adapter.store.write(name, edit)
                    raise PilotError(
                        "Write rejected. Resolve the reported cause or wait for the rate limit, "
                        "then start a new preview for approval.",
                        summary=exc.summary,
                    ) from exc
                # The server may have applied it. Only a fresh GET can settle this.
            current = adapter.read_active_item(rid)
    if snapshot(current) != snapshot(expected):
        if not local_only and current.get("excerpt") == description:
            raise PilotError(
                "Other fields changed. Use reconcile to preview a local-only decision."
            )
        raise PilotError("Readback differs from approved state. No automatic retry. Inspect again.")
    if record:
        if prior not in (edit["local_before"], description):
            raise PilotError("Local description changed since preview. Reconcile the decision.")
        if key:
            annotate(
                output,
                records,
                key,
                sha256=sha,
                description=description,
                edit_id=edit.get("reconciles_preview_id", edit["preview_id"]),
            )
        else:
            notes = read_annotations(output)
            notes.setdefault("bookmark_descriptions", {})[record["post_id"]] = description
            save_annotations(output, records, notes)
    edit.update(status="reconciled" if local_only else "verified", verified_at=now())
    adapter.store.write(name, edit)
    return receipt(edit)


def reconciliation_preview(store, edit, current, prior):
    if current.get("excerpt") != edit["description"]:
        raise PilotError("Raindrop does not hold the exact approved wording.")
    expected = snapshot({**edit["before"], "excerpt": edit["description"]})
    observed = snapshot(current)
    changed = sorted(
        k for k in expected.keys() | observed.keys() if expected.get(k) != observed.get(k)
    )
    if not changed:
        raise PilotError("Readback matches. Resume the original approved preview.")
    if {"link", "user", "userId"} & set(changed) or prior != edit["local_before"]:
        raise PilotError("Source, owner or local description changed. Inspect the item again.")
    recovery = {
        **edit,
        "preview_id": uuid4().hex,
        "status": "preview",
        "before": current,
        "reconciles_preview_id": edit["preview_id"],
        "changed_fields": changed,
    }
    recovery.pop("approved_at")
    store.create(f"correction-{recovery['preview_id']}.private.json", recovery)
    return {**receipt(recovery), "excerpt": current["excerpt"], "previous_local_description": prior}
