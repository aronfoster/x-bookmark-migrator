"""Search published fields and open one selected occurrence as the local reader."""

import json
import re
from pathlib import Path

from .archive import media_format
from .catalog import (
    SHA256,
    asset_key,
    drive_link,
    has_bound_description,
    lookup,
    restored_media_filename,
)
from .core import PilotError
from .drive import digest
from .publication import PublicationReader
from .source import post_id, source_url


def search_fields(record, version):
    pid = record["post_id"]
    yield pid, None, "category", version["category"]
    yield pid, None, "approved_category", record.get("category_override")
    for folder in version["folders"]:
        for name in folder["names"]:
            yield pid, None, "folder", name
    for post in version["posts"]:
        child = post["id"]
        yield child, None, "text", post["text"]
        yield child, None, "author", (post.get("author") or {}).get("username")
        article = post["raw"].get("article")
        if isinstance(article, dict):
            yield child, None, "article_title", article.get("title")
            yield child, None, "article_body", article.get("plain_text")
        else:
            yield child, None, "article_title", post["raw"].get("article_title")
    for asset in version["media"]:
        child = asset["post_id"]
        yield child, asset, "alt_text", (asset.get("raw") or {}).get("alt_text")
        if has_bound_description(asset):
            yield child, asset, "description", asset["description"]


def match_clue(value, clues):
    # Keep offsets in the original string even when case folding changes a word's length.
    offsets = {}
    for token in re.finditer(r"\w+", value):
        offsets.setdefault(token[0].casefold(), token.start())
    for order, (terms, phrase) in enumerate(clues):
        if offsets.keys() >= terms:
            adjacent = phrase.search(value)
            start = adjacent.start() if adjacent else min(offsets[word] for word in terms)
            return (order, adjacent is None), value[max(0, start - 35) : start + 140]
    return None


def search(records, queries):
    """One hit per occurrence. Prefer the original clue, then adjacent terms."""
    clues = []
    for query in queries:
        words = re.findall(r"\w+", query)
        if not words:
            raise PilotError("Supply a clue with at least one word.")
        phrase = re.compile(r"(?<!\w)" + r"\W+".join(map(re.escape, words)) + r"(?!\w)", re.I)
        clues.append(({w.casefold() for w in words}, phrase))
    hits = {}
    for record in records:
        pid = record["post_id"]
        for version in record["versions"]:
            snapshot = [version["manifest"]["sha256"], version["payload_receipt"]["sha256"]]
            primary = next((p for p in version["posts"] if p["id"] == pid), {})
            for child, asset, field, value in search_fields(record, version):
                match = match_clue(value, clues) if isinstance(value, str) else None
                if match is None:
                    continue
                rank, snippet = match
                aid = asset["asset_id"] if asset else ""
                key = (pid, *snapshot, child, aid)
                if key in hits and hits[key][0] <= rank:
                    continue
                hit = {
                    "source_post_id": pid,
                    "source_post_url": source_url(pid),
                    "primary_excerpt": (primary.get("text") or "")[:140],
                    "containing_post_id": child,
                    "snapshot": snapshot,
                    "matched_field": field,
                    "matched_query": queries[rank[0]],
                    "snippet": snippet,
                }
                if asset:
                    hit.update(
                        asset_id=aid,
                        visibility=asset["browse_visibility"],
                        media_type=asset["logical_type"],
                        availability=asset["availability"],
                    )
                hits[key] = rank, hit
    ordered = sorted(hits, key=lambda key: (hits[key][0], key))
    return {"matches": [hits[k][1] for k in ordered[:3]], "total": len(hits), "network_calls": 0}


def read_verified(reader, filename, receipt):
    data = reader.bytes(filename)
    if len(data) != receipt["size_bytes"] or digest(data) != receipt["sha256"]:
        raise PilotError("Selected local file differs from its receipt.")
    return data


def restored_snapshot(root, pid, version):
    """The worker names batches by manifest hash. Older restores have operator-chosen names."""
    root = Path(root).absolute()
    if root.resolve(strict=True) != root or not root.is_dir():
        raise PilotError("Use the mounted restore directory.")
    manifest = version["manifest"]
    worker_batch = root / manifest["sha256"]
    if worker_batch.is_dir():
        directories = (worker_batch,)
    else:
        directories = (p.parent for p in root.rglob(pid + ".json"))
    for directory in directories:
        reader = PublicationReader(directory)
        try:
            raw = reader.bytes("manifest.jsonl")
        except FileNotFoundError:
            continue
        if digest(raw) != manifest["sha256"]:
            continue
        rows = [json.loads(line) for line in raw.splitlines()]
        receipts = [row["payload"] for row in rows if row["post_id"] == pid]
        if len(raw) != manifest["size_bytes"] or receipts != [version["payload_receipt"]]:
            raise PilotError("Selected manifest does not bind the saved primary to this payload.")
        payload = json.loads(read_verified(reader, pid + ".json", receipts[0]))
        if payload["post_id"] != pid:
            raise PilotError("Restored payload has a different saved primary.")
        return reader, payload
    raise PilotError("Selected snapshot is unavailable under the mounted restore directory.")


def open_selection(records, primary, snapshot, restore_root, asset_id):
    pid = post_id(primary)
    if not all(SHA256.fullmatch(s) for s in snapshot):
        raise PilotError("Expected manifest and payload SHA-256 digests.")
    versions = [
        v
        for r in lookup(records, pid)
        if r["post_id"] == pid
        for v in r["versions"]
        if [v["manifest"]["sha256"], v["payload_receipt"]["sha256"]] == snapshot
    ]
    if len(versions) != 1:
        raise PilotError("Selected saved primary and snapshot no longer resolve uniquely.")
    version = versions[0]
    reader, payload = restored_snapshot(restore_root, pid, version)
    result = {
        "source_post_id": pid,
        "source_post_url": source_url(pid),
        "snapshot": snapshot,
        "posts": payload["posts"],
        "folders": payload["folders"],
        "category": payload["category"],
        "blockers": payload["blockers"],
        "payload_url": drive_link(version["payload_receipt"]),
        "network_calls": 0,
    }
    if asset_id is not None:
        assets = [a for a in payload["media"] if asset_key(a) == asset_id]
        projected = [a for a in version["media"] if a["asset_id"] == asset_id]
        if len(assets) != 1 or len(projected) != 1:
            raise PilotError("Attachment is absent or ambiguous in selected snapshot.")
        asset = assets[0]
        if "storage" not in asset:
            raise PilotError("Selected attachment was not archived.")
        if projected[0].get("storage") != asset["storage"]:
            raise PilotError("Published attachment differs from the selected payload receipt.")
        filename = restored_media_filename(pid, asset)
        try:
            data = read_verified(reader, filename, asset["storage"])
        except FileNotFoundError as exc:
            raise PilotError("Selected attachment is missing locally.") from exc
        if media_format(data, asset["logical_type"])[1] != asset["container"]:
            raise PilotError("Selected media container mismatch.")
        result["attachment"] = {
            "asset_id": asset_id,
            "containing_post_id": asset["post_id"],
            "path": str(reader.path(filename)),
            "sha256": asset["storage"]["sha256"],
            "archive_url": drive_link(asset["storage"]),
            "visibility": projected[0]["browse_visibility"],
            "media_type": asset["logical_type"],
        }
    return result
