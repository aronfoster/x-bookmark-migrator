"""Offline discovery projection. Never grants deletion eligibility or calls a provider."""

import copy
import html
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from uuid import uuid4

from .archive import media_format
from .core import PilotError, exact_id
from .drive import digest, drive_id, encode
from .publication import PublicationReader, PublicationStore, publication_bytes
from .source import post_id as post_id
from .source import raindrop_post_id, source_url

NO_DESCRIPTION = "Description not yet supplied"
SHA256 = re.compile(r"[0-9a-f]{64}")

VISIBILITIES = ("featured", "ordinary", "hidden")
ROLES = ("important", "illustrative", "incidental", "unknown")


def raindrop_matches(value, pages):
    """Read-only matching of saved `list 0 --json` pages, not proof of coverage."""
    pid = post_id(value)
    matches = {}
    for page in pages:
        if page.get("result") is not True or not isinstance(page.get("items"), list):
            raise PilotError("Raindrop list response failed; no absence inference is safe.")
        for item in page["items"]:
            match = raindrop_post_id(item.get("link")) == pid
            if match:
                rid = item.get("_id")
                if type(rid) is not int or rid <= 0:
                    raise PilotError("Invalid Raindrop item identity.")
                if rid in matches and matches[rid] != item:
                    raise PilotError("Conflicting Raindrop snapshots; refresh before enrichment.")
                matches[rid] = copy.deepcopy(item)
    return [matches[key] for key in sorted(matches)]


def private_bytes(path):
    path = Path(path)
    st = path.lstat()
    if path.is_symlink() or not path.is_file() or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise PilotError("Catalog input must be an owner-only regular file.")
    return path.read_bytes()


def checked_bytes(path, receipt):
    data = publication_bytes(path)
    if digest(data) != receipt["sha256"] or len(data) != receipt["size_bytes"]:
        raise PilotError("Catalog input differs from its archive receipt.")
    return data


def drive_link(receipt):
    return "https://drive.google.com/file/d/" + drive_id(receipt["file_id"]) + "/view"


def primary_post_text(posts, pid):
    exact_id(pid)
    matches = [p for p in posts if p.get("id") == pid]
    if len(matches) != 1 or not isinstance(matches[0].get("text"), str):
        raise PilotError("Exactly one saved primary post text is required.")
    return matches[0]["text"]


def asset_key(asset):
    pid = exact_id(asset["post_id"])
    index, key = asset["index"], asset["media_key"]
    if type(index) is not int or not 1 <= index <= 100 or not isinstance(key, str) or not key:
        raise PilotError("Invalid attachment identity.")
    return f"{pid}:{index}:{key}"


def build(batches, output):
    """Consume existing archive-restore directories plus independent batch receipts."""
    records = {}
    for directory, receipt_path in batches:
        root = Path(directory).resolve()
        receipt = json.loads(private_bytes(receipt_path))
        if receipt.get("schema_version") != 1:
            raise PilotError("Unsupported archive receipt version.")
        raw = checked_bytes(root / "manifest.jsonl", receipt["manifest"])
        rows = [json.loads(line) for line in raw.splitlines()]
        if not 1 <= len(rows) <= 20:
            raise PilotError("Unexpected archive batch size.")
        seen = set()
        for row in rows:
            pid = exact_id(row["post_id"])
            if row.get("schema_version") != 1 or pid in seen:
                raise PilotError("Unsupported or duplicate archive row.")
            seen.add(pid)
            payload = json.loads(checked_bytes(root / (pid + ".json"), row["payload"]))
            fields = (
                "schema_version",
                "post_id",
                "category",
                "folders",
                "blockers",
                "archive_status",
            )
            if any(payload.get(k) != row[k] for k in fields):
                raise PilotError("Manifest/payload association mismatch.")
            version = {
                "manifest": receipt["manifest"],
                "payload_receipt": row["payload"],
                "payload_url": drive_link(row["payload"]),
                "category": row["category"],
                "folders": row["folders"],
                "archive_status": row["archive_status"],
                "blockers": row["blockers"],
                "posts": payload["posts"],
                "media": [],
            }
            related = {exact_id(p["id"]) for p in payload["posts"]}
            keys = set()
            for asset in payload["media"]:
                key = asset_key(asset)
                if key in keys or asset["post_id"] not in related:
                    raise PilotError("Duplicate or unassociated attachment.")
                keys.add(key)
                media = {**asset, "asset_id": key, "availability": "not_archived"}
                if "storage" in asset:
                    # Derive filenames from verified bytes, never trust a stored path.
                    extension = asset.get("container")
                    if extension not in ("png", "jpg", "webp", "mp4"):
                        raise PilotError("Unsupported archived media container.")
                    filename = f"{pid}_{asset['post_id']}_{asset['index']:02d}.{extension}"
                    path = root / filename
                    media.update(archive_url=drive_link(asset["storage"]))
                    try:
                        data = checked_bytes(path, asset["storage"])
                    except FileNotFoundError:
                        media["availability"] = "missing_local"
                    else:
                        _, detected = media_format(data, asset["logical_type"])
                        if detected != extension:
                            raise PilotError("Archived media container mismatch.")
                        media.update(
                            availability="local_hash_verified",
                            local_url=quote(os.path.relpath(path, output.root), safe="/"),
                        )
                elif not payload["blockers"]:
                    raise PilotError("Unblocked archive has missing media receipt.")
                version["media"].append(media)
            record = records.setdefault(
                pid,
                {
                    "schema_version": 1,
                    "post_id": pid,
                    "canonical_url": source_url(pid),
                    "source_post_id": pid,
                    "source_post_url": source_url(pid),
                    "deletion_eligible": False,
                    "versions": [],
                },
            )
            # Keep distinct snapshots and category appearances; exact reruns collapse.
            identity = (receipt["manifest"]["sha256"], row["payload"]["sha256"])
            if not any(
                (v["manifest"]["sha256"], v["payload_receipt"]["sha256"]) == identity
                for v in record["versions"]
            ):
                record["versions"].append(version)
    result = [records[k] for k in sorted(records)]
    for record in result:
        record["versions"].sort(
            key=lambda v: (v["manifest"]["sha256"], v["payload_receipt"]["sha256"])
        )
    return apply_annotations(
        add_source_links(result), output.read("annotations.private.json") or {}
    )


def apply_annotations(records, annotations):
    if not isinstance(annotations, dict):
        raise PilotError("Invalid media annotations object.")
    links = annotations.get("raindrop_links", {})
    if not isinstance(links, dict):
        raise PilotError("Raindrop links must be an object.")
    from .raindrop import validate_receipt

    for pid, receipt in links.items():
        validate_receipt(receipt, pid)
    if len({r["raindrop_id"] for r in links.values()}) != len(links):
        raise PilotError("Raindrop bookmark is associated with multiple saved primaries.")
    overrides = annotations.get("category_overrides", {})
    if not isinstance(overrides, dict):
        raise PilotError("Category overrides must be an object.")
    for pid, override in overrides.items():
        if post_id(pid) != pid or pid not in {r["post_id"] for r in records}:
            raise PilotError("Category override refers to an absent saved primary record.")
        if not isinstance(override, str) or not 1 <= len(override.strip()) <= 1000:
            raise PilotError("Invalid category override.")
    for record in records:
        record.pop("raindrop_id", None)
        record.pop("raindrop_url", None)
        if record["post_id"] in links:
            rid = links[record["post_id"]]["raindrop_id"]
            record.update(raindrop_id=rid, raindrop_url=raindrop_link(rid))
        record.pop("category_override", None)
        override = overrides.get(record["post_id"])
        if override is not None:
            if not isinstance(override, str) or not 1 <= len(override) <= 1000:
                raise PilotError("Invalid category override.")
            record["category_override"] = override
        for version in record["versions"]:
            for asset in version["media"]:
                note = annotations.get(asset["asset_id"], {})
                role = note.get("semantic_role", "unknown")
                visibility = note.get(
                    "browse_visibility", "hidden" if role == "incidental" else "ordinary"
                )
                if role not in ROLES or visibility not in VISIBILITIES:
                    raise PilotError("Invalid media annotation.")
                sha = asset.get("storage", {}).get("sha256")
                descriptions = note.get("descriptions", {})
                if not isinstance(descriptions, dict):
                    raise PilotError("Invalid digest-bound descriptions.")
                description = (
                    descriptions.get(sha)
                    if isinstance(sha, str) and SHA256.fullmatch(sha)
                    else None
                )
                status = "unreviewed"
                if "semantic_role" in note or "browse_visibility" in note:
                    status = note.get("interpretation_status", status)
                asset.pop("description_sha256", None)
                if description is not None:
                    status = "explicit_annotation"
                    validate_description(description)
                    asset["description_sha256"] = sha
                asset.update(
                    semantic_role=role,
                    browse_visibility=visibility,
                    description=NO_DESCRIPTION if description is None else description,
                    interpretation_status=status,
                    preservation_state="archived_raw" if "storage" in asset else "not_archived",
                )
    return records


def raindrop_link(rid):
    if type(rid) is not int or rid <= 0:
        raise PilotError("Invalid Raindrop bookmark identity.")
    return f"https://app.raindrop.io/my/0/item/{rid}"


def link_raindrop(output, records, receipt):
    """Persist historical GET evidence for navigation, never live-access proof."""
    from .raindrop import validate_receipt

    pid = receipt.get("source_post_id")
    validate_receipt(receipt, pid)
    if pid not in {r["post_id"] for r in records}:
        raise PilotError("Raindrop link refers to an absent saved primary record.")
    notes = copy.deepcopy(output.read("annotations.private.json") or {})
    links = notes.setdefault("raindrop_links", {})
    if pid in links and links[pid]["raindrop_id"] != receipt["raindrop_id"]:
        raise PilotError("Conflicting Raindrop bookmark association.")
    links[pid] = receipt
    projected = apply_annotations(copy.deepcopy(records), notes)
    output.write("annotations.private.json", notes)
    publish(output, projected)


def add_source_links(records):
    """Upgrade only derived copies; raw payloads/manifests are never rewritten."""
    records = copy.deepcopy(records)
    for record in records:
        pid = exact_id(record["post_id"])
        record.update(source_post_id=pid, source_post_url=source_url(pid))
        for version in record["versions"]:
            seen = set()
            for post in version["posts"]:
                child = exact_id(post["id"])
                if child in seen:
                    raise PilotError("Duplicate archived post identity.")
                seen.add(child)
                if post.get("permalink") and post_id(post["permalink"]) != child:
                    raise PilotError("Archived permalink identity mismatch.")
                post.update(source_post_id=child, source_post_url=source_url(child))
            for asset in version["media"]:
                # Older publications contain a projected caption with no evidence of its bytes.
                binding = asset.get("description_sha256")
                if not (
                    isinstance(binding, str)
                    and SHA256.fullmatch(binding)
                    and binding == asset.get("storage", {}).get("sha256")
                ):
                    asset.pop("description_sha256", None)
                    asset["description"] = NO_DESCRIPTION
                asset.update(
                    source_post_id=pid,
                    source_post_url=source_url(pid),
                    attachment_post_url=source_url(exact_id(asset["post_id"])),
                )
    return records


def source_sidecar(records):
    return [
        {
            "schema_version": 1,
            "source_post_id": exact_id(r["post_id"]),
            "source_post_url": source_url(r["post_id"]),
            "archives": [
                {"manifest": v["manifest"], "payload_receipt": v["payload_receipt"]}
                for v in r["versions"]
            ],
        }
        for r in sorted(records, key=lambda r: r["post_id"])
    ]


def lookup(records, value, asset_id=None):
    pid = post_id(value)
    matches = [
        r
        for r in records
        if r["post_id"] == pid or any(p["id"] == pid for v in r["versions"] for p in v["posts"])
    ]
    if asset_id is not None:
        matches = [
            {
                **a,
                "manifest_sha256": v["manifest"]["sha256"],
                "payload_sha256": v["payload_receipt"]["sha256"],
            }
            for r in matches
            for v in r["versions"]
            for a in v["media"]
            if a["asset_id"] == asset_id and a["post_id"] == pid
        ]
    if not matches:
        raise PilotError("No exact catalog match.")
    return matches


def render(records):
    esc = lambda value: html.escape(str(value), quote=True)  # noqa: E731
    parts = [
        '<!doctype html><html lang="en"><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "img-src 'self'; media-src 'self'; style-src 'unsafe-inline'\">",
        "<title>Saved posts</title><style>body{font:18px system-ui;max-width:70rem;"
        "margin:2rem auto;padding:1rem}article{border-top:1px solid #aaa;padding:1rem 0}"
        "pre{white-space:pre-wrap}img,video{max-width:24rem;max-height:18rem}"
        "figure{display:inline-block;vertical-align:top;max-width:25rem;margin:1rem}</style>",
        "<h1>Saved posts</h1><p>Private archive catalog. Hidden attachments are omitted. "
        "Historical receipts do not establish current Drive availability.</p>",
    ]
    categories = {}
    for record in records:
        if record.get("category_override"):
            categories.setdefault(record["category_override"], set()).add(record["post_id"])
        for version in record["versions"]:
            labels = [f"{' / '.join(f['names'])} ({f['id']})" for f in version["folders"]] or [
                version["category"]
            ]
            for label in labels:
                categories.setdefault(label, set()).add(record["post_id"])
    parts.append('<nav aria-label="Original categories"><h2>Original categories</h2>')
    for label, ids in sorted(categories.items()):
        links = " · ".join(f'<a href="#post-{esc(pid)}">{esc(pid)}</a>' for pid in sorted(ids))
        parts.append(f"<details><summary>{esc(label)}</summary>{links}</details>")
    parts.append("</nav>")
    for record in records:
        parts.append(
            f'<article id="post-{esc(record["post_id"])}"><h2>Post {esc(record["post_id"])}</h2>'
        )
        parts.append(
            f'<p><a href="{esc(source_url(record["post_id"]))}" rel="noreferrer">'
            "Open original saved X post</a></p>"
        )
        if "raindrop_id" in record:
            url = raindrop_link(record["raindrop_id"])
            if record.get("raindrop_url") != url:
                raise PilotError("Published Raindrop link mismatch.")
            parts.append(
                f'<p><a href="{esc(url)}" rel="noreferrer">Open bookmark in Raindrop</a></p>'
            )
        if record.get("category_override"):
            parts.append(f"<p>Category override: {esc(record['category_override'])}</p>")
        for v in record["versions"]:
            categories = ", ".join(f"{' / '.join(f['names'])} ({f['id']})" for f in v["folders"])
            parts.append(f"<h3>{esc(categories or v['category'])}</h3>")
            parts.append(f"<p>Archive status: {esc(v['archive_status'])}; {esc(v['blockers'])}</p>")
            parts.append(
                f'<a href="{esc(v["payload_url"])}" rel="noreferrer">Archived JSON on Drive</a>'
            )
            for post in v["posts"]:
                label = (
                    "Saved primary post" if post["id"] == record["post_id"] else "Referenced post"
                )
                parts.append(
                    f"<details><summary>Full saved text · {esc(post['id'])}</summary>"
                    f'<p><a href="{esc(source_url(post["id"]))}" rel="noreferrer">'
                    f"{label}"
                    " on X</a></p>"
                    f"<pre>{esc(post.get('text') or 'Text unavailable')}</pre></details>"
                )
            for a in sorted(v["media"], key=lambda a: a["browse_visibility"] != "featured"):
                if a["browse_visibility"] == "hidden":
                    continue
                parts.append(
                    f"<figure><figcaption>{esc(a['description'])} · "
                    f"{esc(a['asset_id'])} · {esc(a['availability'])}</figcaption>"
                )
                if a["availability"] == "local_hash_verified":
                    url = esc(a["local_url"])
                    if a["logical_type"] == "photo":
                        parts.append(
                            f'<img src="{url}" alt="{esc(a["description"])}" loading="lazy">'
                        )
                    else:
                        parts.append(f'<video src="{url}" controls preload="none"></video>')
                if "archive_url" in a:
                    parts.append(
                        f'<p><a href="{esc(a["archive_url"])}" rel="noreferrer">'
                        "View original on Drive</a></p>"
                    )
                parts.append("</figure>")
        parts.append("</article>")
    return "\n".join(parts) + "\n</html>\n"


def atomic_bytes(output, name, data):
    fd, temporary = tempfile.mkstemp(dir=output.root)
    try:
        if isinstance(output, PublicationStore):
            output.prepare_public_fd(fd)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output.path(name))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def projection_bytes(records):
    records = add_source_links(records)
    return {
        "catalog.jsonl": b"".join(encode(r) for r in records),
        "index.html": render(records).encode(),
        "source-links.jsonl": b"".join(encode(r) for r in source_sidecar(records)),
    }


def publish(output, records):
    for name, data in projection_bytes(records).items():
        atomic_bytes(output, name, data)


def verify_publication(output, records, expected_records=None):
    """Check persisted bytes against a fresh archive/annotation build, without repair."""
    if expected_records is not None and (
        type(expected_records) is not int
        or expected_records < 1
        or len(records) != expected_records
    ):
        raise PilotError("Published archive population does not match the expected count.")
    for name, expected in projection_bytes(records).items():
        if publication_bytes(output.path(name)) != expected:
            raise PilotError("Published catalog projections differ from archives/annotations.")
    return {
        "verified": True,
        "records": len(records),
        "network_calls": 0,
        "reader_access": "not_tested",
        "deletion_eligible": False,
    }


def read_catalog(output):
    raw = (
        output.bytes("catalog.jsonl")
        if isinstance(output, PublicationReader)
        else publication_bytes(output.path("catalog.jsonl"))
    )
    records = [json.loads(line) for line in raw.splitlines()]
    if any(r.get("schema_version") != 1 for r in records):
        raise PilotError("Unsupported catalog version.")
    return add_source_links(records)


def check_publication(output, *, expected_records, expected_media=None, annotations=None):
    """Check persisted bytes and local links without upgrading or writing evidence."""
    if expected_records < 1 or (expected_media is not None and expected_media < 0):
        raise PilotError("Expected publication counts must be positive records/nonnegative media.")
    names = ("catalog.jsonl", "index.html", "source-links.jsonl")
    before = {name: output.bytes(name) for name in names}
    records = [json.loads(line) for line in before["catalog.jsonl"].splitlines()]
    if (
        len(records) != expected_records
        or len({r["post_id"] for r in records}) != len(records)
        or any(r.get("schema_version") != 1 for r in records)
        or add_source_links(records) != records
        or any(r.get("canonical_url") != source_url(r["post_id"]) for r in records)
    ):
        raise PilotError("Catalog population or persisted source identity mismatch.")
    if annotations is not None:
        projected = copy.deepcopy(records)
        for record in projected:
            record.pop("category_override", None)
            record.pop("raindrop_id", None)
            record.pop("raindrop_url", None)
        if apply_annotations(projected, annotations) != records:
            raise PilotError("Published curation differs from canonical annotations.")
    if before["index.html"] != render(records).encode() or before["source-links.jsonl"] != b"".join(
        encode(r) for r in source_sidecar(records)
    ):
        raise PilotError("Published gallery or source sidecar is inconsistent.")
    media_paths = set()
    for record in records:
        for version in record["versions"]:
            if version["payload_url"] != drive_link(version["payload_receipt"]):
                raise PilotError("Published payload link mismatch.")
            for asset in version["media"]:
                if "storage" not in asset:
                    continue
                if asset.get("archive_url") != drive_link(asset["storage"]):
                    raise PilotError("Published attachment archive link mismatch.")
                link = asset.get("local_url", "")
                parsed = urlsplit(link)
                if (
                    asset.get("availability") != "local_hash_verified"
                    or not link
                    or parsed.scheme
                    or parsed.netloc
                    or parsed.query
                    or parsed.fragment
                    or Path(unquote(link)).is_absolute()
                ):
                    raise PilotError("Published attachment lacks a relative local link.")
                path = output.root / unquote(link)
                media_reader = PublicationReader(path.parent)
                data = media_reader.bytes(path.name)
                receipt = asset["storage"]
                if digest(data) != receipt["sha256"] or len(data) != receipt["size_bytes"]:
                    raise PilotError("Published attachment hash mismatch.")
                media_paths.add(path.resolve())
    if expected_media is not None and len(media_paths) != expected_media:
        raise PilotError("Published media population mismatch.")
    if any(output.bytes(name) != before[name] for name in names):
        raise PilotError("Publication changed during reader verification; quiesce writers.")
    return {
        "records": len(records),
        "media_files": len(media_paths),
        "verified": True,
        "annotations_checked": annotations is not None,
        "network_calls": 0,
        "deletion_eligible": False,
    }


def validate_description(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 1000:
        raise PilotError("Description must contain 1–1000 characters.")


def verify_description_bytes(output, assets, sha256):
    """Re-open a matching occurrence under the writer lock before binding a decision."""
    for asset in assets:
        if asset.get("availability") != "local_hash_verified":
            continue
        if asset.get("storage", {}).get("sha256") != sha256:
            continue
        try:
            checked_bytes(output.root / unquote(asset["local_url"]), asset["storage"])
        except FileNotFoundError:
            continue  # A shared quote can have another verified local copy.
        return
    raise PilotError("No verified local bytes for the selected attachment and SHA-256.")


def annotate(
    output,
    records,
    key,
    *,
    visibility=None,
    role=None,
    description=None,
    sha256=None,
    confirm_legacy=False,
    edit_id=None,
):
    assets = [a for r in records for v in r["versions"] for a in v["media"] if a["asset_id"] == key]
    if not assets:
        raise PilotError("Unknown exact attachment ID.")
    notes = output.read("annotations.private.json") or {}
    note = notes.setdefault(key, {})
    if confirm_legacy:
        if description is not None or "description" not in note:
            raise PilotError("Legacy confirmation requires an existing private legacy description.")
        description = note["description"]
    if description is not None:
        validate_description(description)
        if not isinstance(sha256, str) or not SHA256.fullmatch(sha256):
            raise PilotError("Supply the SHA-256 of the reviewed attachment bytes.")
        if edit_id is not None and (not isinstance(edit_id, str) or not edit_id.strip()):
            raise PilotError("An edit ID must be nonempty.")
        edit = dict(
            asset_id=key,
            sha256=sha256,
            description=description,
            confirm_legacy=confirm_legacy,
            visibility=visibility,
            role=role,
        )
        history = notes.setdefault("description_edit_history", {})
        if edit_id in history:
            if history[edit_id]["edit"] != edit:
                raise PilotError("Conflicting description edit ID.")
            # Repair an interrupted publication from current decisions, even on an old replay.
            publish(output, apply_annotations(records, notes))
            return
        verify_description_bytes(output, assets, sha256)
        if confirm_legacy and note.get("legacy_confirmed_sha256", sha256) != sha256:
            raise PilotError("Legacy description already confirmed for another digest.")
        descriptions = note.setdefault("descriptions", {})
        previous = descriptions.get(sha256)
        if (
            edit_id is not None
            or previous != description
            or (confirm_legacy and "legacy_confirmed_sha256" not in note)
        ):
            history[edit_id or str(uuid4())] = {"edit": edit, "previous": previous}
        descriptions[sha256] = description
        if confirm_legacy:
            note["legacy_confirmed_sha256"] = sha256
    elif sha256 is not None or edit_id is not None:
        raise PilotError("A digest or edit ID requires a description edit.")
    if role is not None:
        if role not in ROLES:
            raise PilotError("Invalid semantic role.")
        note["semantic_role"] = role
        if role == "incidental" and visibility is None:
            note["browse_visibility"] = "hidden"
    if visibility is not None:
        if visibility not in VISIBILITIES:
            raise PilotError("Invalid browse visibility.")
        note["browse_visibility"] = visibility
    note["interpretation_status"] = "explicit_annotation"
    projected = apply_annotations(records, notes)
    output.write("annotations.private.json", notes)
    publish(output, projected)


COMMANDS = (
    "catalog-build",
    "catalog-verify",
    "catalog-lookup",
    "catalog-check",
    "catalog-annotate",
    "catalog-description-audit",
    "catalog-drive-text",
    "catalog-raindrop-match",
    "catalog-raindrop-link",
)


def add_commands(sub):
    for command in COMMANDS:
        p = sub.add_parser(command, help="Offline private archive discovery; no credentials needed")
        if command == "catalog-raindrop-match":
            p.add_argument("--post", required=True)
            p.add_argument("--page-file", action="append", required=True)
            continue
        p.add_argument("--catalog-dir", type=Path, required=True)
        if command in ("catalog-build", "catalog-verify"):
            p.add_argument(
                "--batch",
                nargs=2,
                action="append",
                required=True,
                metavar=("RESTORED_DIR", "BATCH_RECEIPT"),
            )
            if command == "catalog-verify":
                p.add_argument("--expected-records", type=int, required=True)
        elif command == "catalog-check":
            p.add_argument("--expected-records", type=int, required=True)
            p.add_argument("--expected-media", type=int)
            p.add_argument("--annotations-file", type=Path)
        elif command == "catalog-drive-text":
            p.add_argument("--post", required=True)
        elif command == "catalog-lookup":
            p.add_argument("--post", required=True)
            p.add_argument("--asset-id")
        elif command == "catalog-raindrop-link":
            p.add_argument("--receipt-file", type=Path, required=True)
        elif command == "catalog-annotate":
            p.add_argument("--asset-id", required=True)
            p.add_argument("--visibility", choices=VISIBILITIES)
            p.add_argument("--role", choices=ROLES)
            desc = p.add_mutually_exclusive_group()
            desc.add_argument("--description")
            desc.add_argument("--confirm-legacy-description", action="store_true")
            p.add_argument("--sha256", help="Digest of the reviewed attachment bytes")
            p.add_argument("--edit-id", help="Stable decision ID for retrying a description edit")


def run(args):
    if args.command == "catalog-raindrop-match":
        matches = raindrop_matches(
            args.post, [json.loads(private_bytes(p)) for p in args.page_file]
        )
        return {"matches": matches, "coverage": "not_established", "create_authorized": False}
    if args.command == "catalog-description-audit":
        # Count all retained legacy notes, including assets absent from the current catalog.
        if not args.catalog_dir.is_dir():
            raise PilotError("Catalog directory must exist.")
        notes = PublicationStore(args.catalog_dir).read("annotations.private.json") or {}
        legacy = [v for k, v in notes.items() if ":" in k and "description" in v]
        return {
            "legacy_descriptions": len(legacy),
            "awaiting_confirmation": sum("legacy_confirmed_sha256" not in v for v in legacy),
        }
    if args.command in ("catalog-lookup", "catalog-check", "catalog-drive-text"):
        output = PublicationReader(args.catalog_dir)
        if args.command == "catalog-drive-text":
            from .search_projection import drive_text

            pid = post_id(args.post)
            for record in read_catalog(output):
                if record["post_id"] == pid:
                    return drive_text(record)
            raise PilotError("No exact saved primary for the Drive projection.")
        if args.command == "catalog-lookup":
            return {
                "matches": lookup(read_catalog(output), args.post, args.asset_id),
                "network_calls": 0,
            }
        annotations = (
            json.loads(private_bytes(args.annotations_file)) if args.annotations_file else None
        )
        if args.annotations_file and not isinstance(annotations, dict):
            raise PilotError("Canonical annotations must be an object.")
        return check_publication(
            output,
            expected_records=args.expected_records,
            expected_media=args.expected_media,
            annotations=annotations,
        )
    if args.command == "catalog-verify" and not args.catalog_dir.is_dir():
        raise PilotError("Publication must exist before verification.")
    output = PublicationStore(args.catalog_dir)
    with output.locked():
        if args.command in ("catalog-build", "catalog-verify"):
            records = build(args.batch, output)
            if args.command == "catalog-verify":
                return verify_publication(output, records, args.expected_records)
            publish(output, records)
            return {"records": len(records), "network_calls": 0, "deletion_eligible": False}
        records = read_catalog(output)
        if args.command == "catalog-raindrop-link":
            receipt = json.loads(private_bytes(args.receipt_file))
            if not isinstance(receipt, dict):
                raise PilotError("Raindrop receipt must be an object.")
            link_raindrop(output, records, receipt)
            return {"raindrop_link_updated": True, "network_calls": 0}
        if not args.confirm_legacy_description and all(
            v is None for v in (args.visibility, args.role, args.description)
        ):
            raise PilotError("Supply a visibility, role, or description edit.")
        annotate(
            output,
            records,
            args.asset_id,
            visibility=args.visibility,
            role=args.role,
            description=args.description,
            sha256=args.sha256,
            confirm_legacy=args.confirm_legacy_description,
            edit_id=args.edit_id,
        )
        return {"annotation_updated": True, "network_calls": 0}
