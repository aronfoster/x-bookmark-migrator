"""Fresh external discovery gate, called only inside the removal transaction."""

from html.parser import HTMLParser
from pathlib import Path

from .catalog import read_catalog
from .core import PilotError
from .drive import digest
from .publication import PublicationStore, publication_bytes
from .raindrop import inspect_inventory, validate_receipt
from .source import source_url


def require(ok, reason):
    if not ok:
        raise PilotError("Discovery gate blocked: " + reason + ".", code="blocked_discovery")


class SourceLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.article = None
        self.links = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "article":
            self.article = attrs.get("id")
        if tag == "a" and self.article and attrs.get("href"):
            self.links.add((self.article, attrs["href"]))

    def handle_endtag(self, tag):
        if tag == "article":
            self.article = None


def positive_media_only(payload, classification, payload_sha256):
    """Deliberately narrow: explicit review AND unambiguously empty original text.

    Captions/emoji/hashtags/quotes/reposts remain Raindrop-required. No heuristic
    decides that substantive prose is unimportant, and no mutable label suffices.
    """
    inventory = payload.get("inventory_record")
    if not isinstance(inventory, dict) or not isinstance(inventory.get("post"), dict):
        return False
    pid = payload["post_id"]
    return (
        isinstance(classification, dict)
        and classification.get("schema_version") == 1
        and classification.get("source_post_id") == pid
        and classification.get("source_post_url") == source_url(pid)
        and classification.get("classification") == "media_only"
        and classification.get("affirmative") is True
        and classification.get("reviewed_by") == "owner"
        and classification.get("payload_sha256") == payload_sha256
        and len(payload.get("posts", [])) == 1
        and payload["posts"][0].get("id") == pid
        and isinstance(payload["posts"][0].get("text"), str)
        and not payload["posts"][0]["text"].strip()
        and not payload["inventory_record"]["post"].get("referenced_tweets")
        and not payload["inventory_record"]["post"].get("referenced_posts")
        and bool(payload.get("media"))
        and all(
            a.get("post_id") == pid
            and a.get("logical_type") in ("photo", "video", "animated_gif")
            and "storage" in a
            for a in payload["media"]
        )
    )


def verify_discovery_now(pid, row, payload, catalog_dir, adapter):
    try:
        return _verify_discovery_now(pid, row, payload, catalog_dir, adapter)
    except (KeyError, TypeError, AttributeError, UnicodeError):
        raise PilotError(
            "Discovery gate blocked: malformed discovery evidence.", code="blocked_discovery"
        ) from None


def _verify_discovery_now(pid, row, payload, catalog_dir, adapter):
    require(catalog_dir is not None, "catalog_required")
    output = PublicationStore(Path(catalog_dir))
    # Same lock used by catalog publication/curation: keep the selected snapshot
    # stable through the last provider GET. apply writes proof before DELETE.
    with output.locked():
        records = read_catalog(output)
        matches = [r for r in records if r.get("post_id") == pid]
        require(len(matches) == 1, "exact_primary_catalog_record_required")
        record = matches[0]
        canonical = source_url(pid)
        require(record.get("canonical_url") == canonical, "wrong_source_link")
        require(
            record.get("source_post_id") == pid and record.get("source_post_url") == canonical,
            "source_projection_required",
        )
        versions = [v for v in record["versions"] if v.get("payload_receipt") == row["payload"]]
        require(len(versions) == 1, "catalog_archive_mismatch")
        version = versions[0]
        require(
            [
                {k: v for k, v in post.items() if k not in ("source_post_id", "source_post_url")}
                for post in version.get("posts", [])
            ]
            == payload["posts"]
            and version.get("category") == row["category"]
            and version.get("folders") == row["folders"],
            "catalog_content_mismatch",
        )
        html = publication_bytes(output.root / "index.html")
        links = SourceLinks()
        links.feed(html.decode())
        require(("post-" + pid, canonical) in links.links, "visible_original_link_required")
        evidence = {
            "source_post_id": pid,
            "source_post_url": canonical,
            "payload_sha256": row["payload"]["sha256"],
            "gallery_sha256": digest(html),
        }
        classifications = output.read("media-only.private.json") or {}
        classification = classifications.get(pid)
        if positive_media_only(payload, classification, row["payload"]["sha256"]):
            return {**evidence, "kind": "media_only", "classification": classification}
        require(adapter is not None, "raindrop_required")
        # Fresh enumeration prevents duplicate/Trash/incomplete coverage from
        # satisfying the gate. The independent single GET is the final network call.
        snapshot = adapter.enumerate_now()
        match = inspect_inventory(snapshot, pid)
        require(match["state"] == "active", "unique_active_raindrop_required")
        proof = adapter.verify_raindrop_now(pid, match["raindrop_ids"][0])
        validate_receipt(proof, pid)
        return {**evidence, "kind": "raindrop", "proof": proof, "coverage": snapshot["coverage"]}
