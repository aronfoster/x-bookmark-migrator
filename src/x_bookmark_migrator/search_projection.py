"""Offline, versioned text/plain Drive search unit for one projected saved primary."""

from .catalog import drive_link, has_bound_description, raindrop_link
from .drive import digest
from .source import source_url

RENDERER_VERSION = 3


def drive_text(record):
    pid = record["post_id"]
    lines = ["Saved post: " + source_url(pid)]
    if "approved_description" in record:
        lines.append("Approved description: " + record["approved_description"])
    if "category_override" in record:
        lines.append("Approved category: " + record["category_override"])
    if "raindrop_id" in record:
        lines.append("Raindrop: " + raindrop_link(record["raindrop_id"]))
    for version in record["versions"]:
        lines.extend(
            [
                "Snapshot: "
                + version["manifest"]["sha256"]
                + " / "
                + version["payload_receipt"]["sha256"],
                "Preserved payload: " + drive_link(version["payload_receipt"]),
                "Original category: " + version["category"],
                *(
                    "Original folder: " + name
                    for folder in version["folders"]
                    for name in folder["names"]
                ),
                *("Blocker: " + blocker for blocker in version["blockers"]),
            ]
        )
        relations = {}
        for post in version["posts"]:
            raw = post["raw"]
            refs = raw.get("referenced_posts", raw.get("referenced_tweets"))
            if isinstance(refs, list):
                for ref in refs:
                    if isinstance(ref, dict) and ref.get("type") in ("quoted", "retweeted"):
                        relations.setdefault(ref["id"], ref["type"])
        for post in version["posts"]:
            label = "Primary" if post["id"] == pid else relations.get(post["id"], "Referenced")
            lines.append(label + ": " + source_url(post["id"]))
            author = post.get("author") or {}
            if isinstance(author.get("username"), str):
                lines.append("Author: " + author["username"])
            lines.append(post["text"] if isinstance(post["text"], str) else "(text unavailable)")
            article = post["raw"].get("article")
            if isinstance(article, dict):
                for field in ("title", "plain_text"):
                    if isinstance(article.get(field), str):
                        lines.append("Article " + field + ": " + article[field])
            elif isinstance(post["raw"].get("article_title"), str):
                lines.append("Article title: " + post["raw"]["article_title"])
        for asset in version["media"]:
            lines.extend(
                [
                    "Attachment: " + asset["asset_id"],
                    "Containing post: " + source_url(asset["post_id"]),
                    "Type: " + asset.get("logical_type", "unknown"),
                    "Availability: " + asset["availability"],
                    "Visibility: " + asset["browse_visibility"],
                ]
            )
            alt = (asset.get("raw") or {}).get("alt_text")
            if isinstance(alt, str) and alt:
                lines.append("Source alt text: " + alt)
            if has_bound_description(asset):
                lines.append("Explicit description: " + asset["description"])
            if "storage" in asset:
                storage = asset["storage"]
                lines.extend(
                    [
                        "Archived media: " + drive_link(storage),
                        f"Receipt: {storage['sha256']} ({storage['size_bytes']} bytes)",
                    ]
                )
    body = ("\n".join(lines) + "\n").replace("\r\n", "\n").replace("\r", "\n")
    return {
        "name": f"bookmark-{pid}.txt",
        "renderer_version": RENDERER_VERSION,
        "body": body,
        "sha256": digest(body.encode("utf-8")),
    }
