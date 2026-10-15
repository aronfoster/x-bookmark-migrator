"""Bounded, non-destructive archival from the existing official-API inventory."""

import json
import os
import re
import secrets
from urllib.parse import urlsplit

import httpx

from .core import PilotError, exact_id, post_fields, validate_page
from .drive import digest, encode, read_bounded


def source_url(value):
    if not isinstance(value, str):
        raise PilotError("Media URL is missing.", code="missing_media_url")
    url = urlsplit(value)
    # Only fetch exact API-returned CDN URLs, never invent originals or scrape pages.
    if (
        url.scheme != "https"
        or url.netloc not in ("pbs.twimg.com", "video.twimg.com")
        or url.fragment
    ):
        raise PilotError("Unsupported media URL.", code="unsupported_media_url")
    return value


def media_choice(media):
    kind = media.get("type")
    if kind == "photo":
        return source_url(media.get("url")), "photo", "api_returned_photo"
    if kind in ("video", "animated_gif"):
        variants = media.get("variants", [])
        if not isinstance(variants, list):
            raise PilotError("Malformed media variants.", code="invalid_media")
        choices = [
            v
            for v in variants
            if isinstance(v, dict)
            and v.get("content_type") == "video/mp4"
            and isinstance(v.get("url"), str)
            and type(v.get("bit_rate", 0)) is int
            and v.get("bit_rate", 0) >= 0
        ]
        if choices:
            chosen = max(choices, key=lambda v: (v.get("bit_rate", 0), v["url"]))
            return source_url(chosen["url"]), kind, "highest_bitrate_observed_mp4"
    raise PilotError("No supported full media rendition.", code="unsupported_media")


def media_format(data, kind):
    # Reject posters/HTML/playlists and obvious truncation. Full decoder validation
    # remains part of the representative live acceptance, not a claimed guarantee.
    if kind == "photo":
        if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
            return "image/jpeg", "jpg"
        if data.startswith(b"\x89PNG\r\n\x1a\n") and data[-12:] == b"\0\0\0\0IEND\xaeB`\x82":
            return "image/png", "png"
        if (
            len(data) >= 20
            and data[:4] == b"RIFF"
            and data[8:12] == b"WEBP"
            and int.from_bytes(data[4:8], "little") + 8 == len(data)
        ):
            return "image/webp", "webp"
    elif kind in ("video", "animated_gif"):
        # Check complete top-level ISO BMFF boxes, including data and movie metadata.
        offset, boxes = 0, set()
        while offset + 8 <= len(data):
            size = int.from_bytes(data[offset : offset + 4], "big")
            box = data[offset + 4 : offset + 8]
            header = 8
            if size == 1:
                if offset + 16 > len(data):
                    break
                size = int.from_bytes(data[offset + 8 : offset + 16], "big")
                header = 16
            elif size == 0:
                size = len(data) - offset
            if size < header or offset + size > len(data):
                break
            boxes.add(box)
            offset += size
        if offset == len(data) and {b"ftyp", b"moov", b"mdat"} <= boxes:
            return "video/mp4", "mp4"
    raise PilotError(
        "Downloaded bytes are not a supported complete container.", code="invalid_media"
    )


def download_media(http, url, kind, max_bytes):
    # A separate credential-free client is mandatory. Redirects stay disabled.
    with http.stream("GET", source_url(url), headers={"Accept-Encoding": "identity"}) as response:
        if response.status_code != 200:
            raise PilotError("Media download failed; bookmark stays blocked.", code="media_http")
        data = read_bounded(response, max_bytes)
    mime, extension = media_format(data, kind)
    return data, mime, extension


def object_map(values, key):
    result = {}
    if not isinstance(values, list):
        raise PilotError("Malformed expansion list.")
    for item in values:
        if not isinstance(item, dict) or not isinstance(item.get(key), str):
            raise PilotError("Malformed expansion object.")
        if item[key] in result and result[item[key]] != item:
            raise PilotError("Conflicting expansion objects.")
        result[item[key]] = item
    return result


def category_for(inventory, record):
    folders = [inventory.state["folders"][i] for i in record["folder_ids"]]
    if len(folders) == 1 and len(folders[0]["names"]) == 1:
        return "folder-" + exact_id(folders[0]["id"]), folders, []
    if folders:
        return "category-conflict", folders, ["blocked_category_conflict"]
    if inventory.membership(record) == "confirmed_unfiled":
        return "unfiled", [], []
    return "membership-unknown", [], ["blocked_category_unknown"]


def evidence_for(store, record):
    evidence = store.read(record["expansions_ref"]) if record.get("expansions_ref") else None
    if not evidence or evidence.get("status") != 200:
        raise PilotError("Missing official read evidence.", code="missing_provenance")
    request = evidence.get("request", {})
    dialect = "post" if "post.fields" in request else "tweet"
    required = {"attachments", "entities", "article", "note_" + dialect}
    fields = set(request.get(dialect + ".fields", "").split(","))
    expansions = set(request.get("expansions", "").split(","))
    if (
        not required <= fields
        or not {"url", "type", "variants"} <= set(request.get("media.fields", "").split(","))
        or not {"author_id", "attachments.media_keys"} <= expansions
        or not ({"referenced_posts", "referenced_tweets.id"} & expansions)
    ):
        raise PilotError("Source read did not request archive fields.", code="missing_provenance")
    body = evidence.get("response", {})
    if record["post"] not in body.get("data", []):
        raise PilotError("Inventory differs from saved API evidence.", code="missing_provenance")
    return evidence


def enrich_references(inventory, client, budget, owner_id, post_ids=None, limit=5):
    """Explicit paid hydration for direct quotes/reposts; never an automatic retry."""
    if not 1 <= limit <= 20:
        raise PilotError("Reference enrichment limit must be 1–20 posts.")
    ids = post_ids or list(inventory.state["posts"])[:limit]
    if not ids or len(ids) > limit or len(set(ids)) != len(ids):
        raise PilotError("Select distinct inventory posts within the enrichment limit.")
    if owner_id != inventory.state["owner_id"]:
        raise PilotError("Configured owner differs from the inventory.")
    work = []
    for pid in ids:
        record = inventory.state["posts"].get(exact_id(pid))
        if not record or not record.get("post"):
            raise PilotError("Enrichment requires an existing primary post body.")
        evidence_for(inventory.store, record)
        post = record["post"]
        refs = post.get("referenced_posts", post.get("referenced_tweets", []))
        needed = sorted(
            {exact_id(r["id"]) for r in refs if r.get("type") in ("quoted", "retweeted")}
        )
        if len(needed) > 20:
            raise PilotError("Too many references for bounded enrichment.")
        if needed:
            work.append((pid, record, needed))
    if not work:
        return {"enriched_posts": 0, "x_api_calls": 0}
    me = client.get("/2/users/me", {}, budget.reserve)
    if me.get("errors") or me.get("data", {}).get("id") != owner_id:
        raise PilotError("Authenticated X user differs from configured owner.")
    for pid, record, needed in work:
        params = {"ids": ",".join(needed), **post_fields(inventory.state["dialect"])}
        body = client.get("/2/tweets", params, budget.reserve)
        data, _, _, _ = validate_page(body, "hydrate", len(needed))
        returned = [p["id"] for p in data]
        if len(set(returned)) != len(returned) or any(p not in needed for p in returned):
            raise PilotError("Unexpected reference hydration IDs.")
        inventory.state.setdefault("archive_reference_reads", {})[pid] = {
            "source": record["expansions_ref"],
            "evidence": client.observations[-1]["private_file"],
        }
        inventory.save()
    return {
        "enriched_posts": len(work),
        "x_api_calls": len(work) + 1,
        "estimated_reserved_usd": str(budget.used),
        "observed_charge": "unverified",
    }


def archive_evidence(inventory, record):
    evidence = evidence_for(inventory.store, record)
    # Work on a JSON copy; do not mutate the original saved response.
    combined = json.loads(json.dumps(evidence))
    link = inventory.state.get("archive_reference_reads", {}).get(record["id"])
    if not link or link["source"] != record["expansions_ref"]:
        return combined
    extra = inventory.store.read(link["evidence"])
    if not extra or extra.get("status") != 200 or extra.get("endpoint") != "/2/tweets":
        raise PilotError("Missing official reference evidence.", code="missing_provenance")
    body = extra["response"]
    combined["original_response"] = json.loads(json.dumps(combined["response"]))
    combined["reference_evidence"] = extra
    combined["reference_errors"] = body.get("errors", [])
    includes = combined["response"].setdefault("includes", {})
    # More recent direct lookup is authoritative for referenced objects. Keep both
    # original responses in the payload so provenance and changes remain reviewable.
    for kind, key in (("users", "id"), ("media", "media_key"), ("posts", "id")):
        prior = list(includes.get(kind, []))
        newer = list(body.get("includes", {}).get(kind, []))
        if kind == "posts":
            prior += includes.pop("tweets", [])
            newer += body.get("includes", {}).get("tweets", []) + body.get("data", [])
        merged = object_map(prior, key)
        merged.update(object_map(newer, key))
        includes[kind] = list(merged.values())
    return combined


def plan_post(post, includes):
    """Keep unknown/incomplete shapes blocked, and still retain all returned evidence."""
    blockers, posts, assets = [], [], []
    users = object_map(includes.get("users", []), "id")
    media = object_map(includes.get("media", []), "media_key")
    references = object_map(includes.get("posts", []) + includes.get("tweets", []), "id")
    seen = set()

    def visit(item, depth=0):
        pid = exact_id(item["id"])
        if pid in seen:
            return
        if len(seen) >= 20:
            blockers.append("blocked_reference_count")
            return
        seen.add(pid)
        author = users.get(item.get("author_id"))
        if not author or not isinstance(author.get("username"), str):
            blockers.append("blocked_missing_author")
        text = item.get("text")
        text_field = "text"
        notes = [k for k in ("note_post", "note_tweet") if k in item]
        for key in notes:
            note = item[key]
            if not isinstance(note, dict) or not isinstance(note.get("text"), str):
                blockers.append("blocked_incomplete_long_text")
            elif text_field != "text" and text != note["text"]:
                blockers.append("blocked_conflicting_long_text")
            else:
                text, text_field = note["text"], key + ".text"
        if not isinstance(text, str):
            blockers.append("blocked_missing_text")
        if "article" in item or "article_title" in item:
            blockers.append("blocked_incomplete_article")
        # Article links can also be returned without an article object.
        for entity in item.get("entities", {}).get("urls", []):
            for key in ("expanded_url", "unwound_url"):
                url = urlsplit(entity.get(key) or "")
                if url.hostname in ("x.com", "twitter.com") and "/article/" in url.path:
                    blockers.append("blocked_incomplete_article")
        posts.append(
            {
                "id": pid,
                "permalink": f"https://x.com/i/status/{pid}",
                "text": text,
                "text_field": text_field,
                "author": author,
                "raw": item,
            }
        )
        attachments = item.get("attachments", {})
        if not isinstance(attachments, dict):
            blockers.append("blocked_invalid_attachments")
            attachments = {}
        keys = attachments.get("media_keys", [])
        if not isinstance(keys, list) or any(not isinstance(k, str) for k in keys):
            blockers.append("blocked_invalid_attachments")
            keys = []
        if len(keys) > 16:
            blockers.append("blocked_media_count")
            keys = keys[:16]
        if attachments.get("poll_ids"):
            blockers.append("blocked_unsupported_poll")
        for index, key in enumerate(keys, 1):
            entry = {"post_id": pid, "index": index, "media_key": key, "raw": media.get(key)}
            if key not in media:
                blockers.append("blocked_missing_media")
                entry["blocked"] = "missing_media"
            else:
                try:
                    url, kind, quality = media_choice(media[key])
                    entry.update(
                        source_url=url,
                        logical_type=kind,
                        quality=quality,
                        source_original_verified=False,
                        derivative=False,
                    )
                except PilotError as exc:
                    entry["blocked"] = exc.code
                    blockers.append("blocked_" + exc.code)
            assets.append(entry)
        refs = item.get("referenced_posts", item.get("referenced_tweets", []))
        if not isinstance(refs, list):
            blockers.append("blocked_invalid_references")
            return
        for ref in refs:
            rid = exact_id(ref["id"])
            # Reply context is retained by ID, not a promise to archive the whole thread.
            if ref.get("type") == "replied_to":
                continue
            if ref.get("type") not in ("quoted", "retweeted"):
                blockers.append("blocked_unknown_reference_type")
            elif rid not in references:
                blockers.append("blocked_missing_reference")
            elif depth >= 2:
                blockers.append("blocked_reference_depth")
            else:
                visit(references[rid], depth + 1)

    visit(post)
    return posts, assets, sorted(set(blockers))


def import_pilot(drive, store, manifest_id):
    """Reuse the existing Oct 14 JSONL schema; never promote its DOM text to API evidence."""
    raw = drive.download(manifest_id, 8 * 1024 * 1024)
    entries = store.read("archive-adoptions.private.json") or {}
    adopted = 0
    for line in raw.decode().splitlines():
        row = json.loads(line)
        exact_id(row["source_post_id"])
        for media in row.get("preserved_media", []):
            receipt = {
                "file_id": media["drive"]["id"],
                "sha256": media["sha256"],
                "size_bytes": media["size_bytes"],
                "mime_type": media["mime_type"],
                "name": media["filename"],
                "parent_id": media["drive"]["parent_id"],
            }
            data = drive.verify(receipt)
            if digest(data) != media["sha256"]:
                raise PilotError("Pilot media hash mismatch.")
            entries[media["sha256"]] = {
                **receipt,
                "legacy_reuse": True,
                "legacy_manifest_id": manifest_id,
            }
            adopted += 1
    store.write("archive-adoptions.private.json", entries)
    return {"adopted_assets": adopted, "x_api_calls": 0, "uploaded_assets": 0}


def archive_batch(inventory, drive, media_http, *, post_ids=None, limit=5):
    if type(limit) is not int or not 1 <= limit <= 20:
        raise PilotError("Archive batch limit must be 1–20 posts.")
    ids = post_ids or list(inventory.state["posts"])[:limit]
    if not ids or len(ids) > limit or len(set(ids)) != len(ids):
        raise PilotError("Select 1–limit distinct inventory posts.")
    if any(exact_id(pid) not in inventory.state["posts"] for pid in ids):
        raise PilotError("Selected post is absent from this inventory.")
    rows = []
    adoptions = inventory.store.read("archive-adoptions.private.json") or {}
    for pid in ids:
        record = inventory.state["posts"][pid]
        category, folders, blockers = category_for(inventory, record)
        payload = {
            "schema_version": 1,
            "post_id": pid,
            "category": category,
            "folders": folders,
            "inventory_record": record,
            "posts": [],
            "media": [],
            "deletion_eligible": False,
        }
        try:
            evidence = archive_evidence(inventory, record)
            payload["source_evidence"] = evidence
            posts, assets, content_blockers = plan_post(
                record["post"], evidence["response"].get("includes", {})
            )
            payload.update(posts=posts, media=assets)
            blockers.extend(content_blockers)
            if (
                record.get("errors")
                or record.get("hydration_status") != "read"
                or evidence.get("reference_errors")
            ):
                blockers.append("blocked_partial_api_response")
            for asset in assets:
                if "blocked" in asset:
                    continue
                try:
                    data, mime, extension = download_media(
                        media_http, asset["source_url"], asset["logical_type"], drive.max_bytes
                    )
                    name = f"{asset['post_id']}_{asset['index']:02d}.{extension}"
                    prior = adoptions.get(digest(data))
                    if prior:
                        drive.verify(prior)
                        saved = prior
                    else:
                        saved = drive.save(category, name, data, mime)
                    asset.update(storage=saved, container=extension)
                except (PilotError, httpx.HTTPError) as exc:
                    asset["blocked"] = (
                        exc.code if isinstance(exc, PilotError) else "media_transport"
                    )
                    blockers.append("blocked_" + asset["blocked"])
        except PilotError as exc:
            blockers.append("blocked_" + exc.code)
        payload["blockers"] = sorted(set(blockers))
        payload["archive_status"] = "blocked" if blockers else "bytes_verified"
        saved = drive.save(category, pid + ".json", encode(payload), "application/json")
        rows.append(
            {
                "type": "post",
                "schema_version": 1,
                "post_id": pid,
                "category": category,
                "folders": folders,
                "payload": saved,
                "archive_status": payload["archive_status"],
                "blockers": payload["blockers"],
                "deletion_eligible": False,
            }
        )
    batch_id = secrets.token_hex(12)
    manifest = drive.save(
        "batches", batch_id + ".jsonl", b"".join(encode(r) for r in rows), "application/x-ndjson"
    )
    receipt = {
        "schema_version": 1,
        "batch_id": batch_id,
        "manifest": manifest,
        "records": len(rows),
        "bytes_verified": sum(not r["blockers"] for r in rows),
        "blocked": sum(bool(r["blockers"]) for r in rows),
        "deletion_eligible": False,
    }
    name = "archive-" + batch_id + ".private.json"
    # Store.write replaces atomically; a random new name plus explicit existence guard
    # keeps batch receipts immutable under the enclosing state lock.
    if inventory.store.path(name).exists():
        raise PilotError("Archive receipt collision.")
    inventory.store.write(name, receipt)
    return {
        "records": receipt["records"],
        "bytes_verified": receipt["bytes_verified"],
        "blocked": receipt["blocked"],
        "private_receipt": name,
        "x_api_calls": 0,
        "deletion_eligible": False,
    }


def restore_batch(drive, manifest_id, output, expected_sha256):
    """Fresh remote reads, no X, source browser, local inventory or upload journal."""
    raw = drive.download(manifest_id, 8 * 1024 * 1024)
    if (
        not isinstance(expected_sha256, str)
        or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256)
        or digest(raw) != expected_sha256
    ):
        raise PilotError("Manifest does not match the original batch receipt hash.")
    rows = [json.loads(line) for line in raw.decode().splitlines()]
    if not 1 <= len(rows) <= 20:
        raise PilotError("Unexpected archive batch size.")
    files, verified, blocked = 0, 0, 0
    seen = set()
    for row in rows:
        pid = exact_id(row["post_id"])
        if row.get("schema_version") != 1 or pid in seen:
            raise PilotError("Unsupported or duplicate archive record.")
        seen.add(pid)
        data = drive.verify(row["payload"])
        payload = json.loads(data)
        if (
            payload.get("schema_version") != 1
            or payload.get("post_id") != pid
            or payload.get("category") != row["category"]
            or payload.get("folders") != row["folders"]
            or payload.get("blockers") != row["blockers"]
            or payload.get("archive_status") != row["archive_status"]
        ):
            raise PilotError("Manifest/payload association mismatch.")
        create_local(output, pid + ".json", data)
        for asset in payload["media"]:
            if "storage" not in asset:
                if not payload["blockers"]:
                    raise PilotError("Unblocked archive has missing media.")
                continue
            data = drive.verify(asset["storage"])
            _, extension = media_format(data, asset["logical_type"])
            asset_id = exact_id(asset["post_id"])
            index = asset["index"]
            if type(index) is not int or not 1 <= index <= 100:
                raise PilotError("Invalid media index.")
            create_local(output, f"{pid}_{asset_id}_{index:02d}.{extension}", data)
            files += 1
        blocked += bool(payload["blockers"])
        verified += not payload["blockers"]
    create_local(output, "manifest.jsonl", raw)
    return {
        "records": len(rows),
        "media_files": files,
        "bytes_verified": verified,
        "blocked": blocked,
        "deletion_eligible": False,
        "x_api_calls": 0,
    }


def create_local(store, name, data):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise PilotError("Unsafe reconstruction filename.")
    fd = os.open(store.path(name), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
