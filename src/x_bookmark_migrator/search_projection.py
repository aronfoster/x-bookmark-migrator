"""Offline, versioned text/plain Drive search unit for one projected saved primary."""

from .drive import digest


def drive_text(record):
    pid = record["source_post_id"]
    lines = ["Saved post: " + record["source_post_url"]]
    if "category_override" in record:
        lines.append("Approved category: " + record["category_override"])
    if "raindrop_url" in record:
        lines.append("Raindrop: " + record["raindrop_url"])
    for version in record["versions"]:
        lines.extend(
            [
                "Snapshot: "
                + version["manifest"]["sha256"]
                + " / "
                + version["payload_receipt"]["sha256"],
                "Preserved payload: " + version["payload_url"],
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
            lines.append(label + ": " + post["source_post_url"])
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
                    "Containing post: " + asset["attachment_post_url"],
                    "Type: " + asset.get("logical_type", "unknown"),
                    "Availability: " + asset["availability"],
                    "Visibility: " + asset["browse_visibility"],
                ]
            )
            alt = (asset.get("raw") or {}).get("alt_text")
            if isinstance(alt, str) and alt:
                lines.append("Source alt text: " + alt)
            if "description_sha256" in asset:
                lines.append("Explicit description: " + asset["description"])
            if "storage" in asset:
                storage = asset["storage"]
                lines.extend(
                    [
                        "Archived media: " + asset["archive_url"],
                        f"Receipt: {storage['sha256']} ({storage['size_bytes']} bytes)",
                    ]
                )
    body = ("\n".join(lines) + "\n").replace("\r\n", "\n").replace("\r", "\n")
    return {
        "name": f"bookmark-{pid}.txt",
        "renderer_version": 1,
        "body": body,
        "sha256": digest(body.encode("utf-8")),
    }
