"""Offline discovery projection. Never grants deletion eligibility or calls a provider."""

import copy
import html
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit

from .archive import media_format
from .core import PilotError, Store, exact_id
from .drive import digest, drive_id, encode

VISIBILITIES = ("featured", "ordinary", "hidden")
ROLES = ("important", "illustrative", "incidental", "unknown")


def post_id(value):
    """Exact numeric identity; never substring-search URLs or coerce JSON numbers."""
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value):
        return exact_id(value)
    if isinstance(value, str):
        url = urlsplit(value)
        match = re.fullmatch(r"/(?:[A-Za-z0-9_]+|i)/status/([0-9]{1,19})/?", url.path)
        if (
            url.scheme in ("http", "https")
            and url.netloc.lower() in ("x.com", "www.x.com", "twitter.com", "www.twitter.com")
            and match
        ):
            return exact_id(match[1])
    raise PilotError("Expected an exact numeric post ID or supported X status URL.")


def raindrop_matches(value, pages):
    """Read-only matching of saved `list 0 --json` pages, not proof of coverage."""
    pid = post_id(value)
    matches = {}
    for page in pages:
        if page.get("result") is not True or not isinstance(page.get("items"), list):
            raise PilotError("Raindrop list response failed; no absence inference is safe.")
        for item in page["items"]:
            try:
                match = post_id(item.get("link")) == pid
            except PilotError:
                continue
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
    data = private_bytes(path)
    if digest(data) != receipt["sha256"] or len(data) != receipt["size_bytes"]:
        raise PilotError("Catalog input differs from its archive receipt.")
    return data


def drive_link(receipt):
    return "https://drive.google.com/file/d/" + drive_id(receipt["file_id"]) + "/view"


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
                    "canonical_url": f"https://x.com/i/status/{pid}",
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
    return apply_annotations(result, output.read("annotations.private.json") or {})


def apply_annotations(records, annotations):
    for record in records:
        for version in record["versions"]:
            for asset in version["media"]:
                note = annotations.get(asset["asset_id"], {})
                role = note.get("semantic_role", "unknown")
                visibility = note.get(
                    "browse_visibility", "hidden" if role == "incidental" else "ordinary"
                )
                if role not in ROLES or visibility not in VISIBILITIES:
                    raise PilotError("Invalid media annotation.")
                asset.update(
                    semantic_role=role,
                    browse_visibility=visibility,
                    description=note.get("description", "Description not yet supplied"),
                    interpretation_status=note.get("interpretation_status", "unreviewed"),
                    preservation_state="archived_raw" if "storage" in asset else "not_archived",
                )
    return records


def lookup(records, value, asset_id=None):
    pid = post_id(value)
    matches = [
        r
        for r in records
        if r["post_id"] == pid or any(p["id"] == pid for v in r["versions"] for p in v["posts"])
    ]
    if asset_id is not None:
        matches = [
            a
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
        for v in record["versions"]:
            categories = ", ".join(f"{' / '.join(f['names'])} ({f['id']})" for f in v["folders"])
            parts.append(f"<h3>{esc(categories or v['category'])}</h3>")
            parts.append(f"<p>Archive status: {esc(v['archive_status'])}; {esc(v['blockers'])}</p>")
            parts.append(
                f'<a href="{esc(v["payload_url"])}" rel="noreferrer">Archived JSON on Drive</a>'
            )
            for post in v["posts"]:
                parts.append(
                    f"<details><summary>Full saved text · {esc(post['id'])}</summary>"
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
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output.path(name))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def publish(output, records):
    page = render(records).encode()
    atomic_bytes(output, "catalog.jsonl", b"".join(encode(r) for r in records))
    atomic_bytes(output, "index.html", page)


def read_catalog(output):
    records = [
        json.loads(line) for line in private_bytes(output.path("catalog.jsonl")).splitlines()
    ]
    if any(r.get("schema_version") != 1 for r in records):
        raise PilotError("Unsupported catalog version.")
    return records


def annotate(output, records, key, *, visibility=None, role=None, description=None):
    if not any(a["asset_id"] == key for r in records for v in r["versions"] for a in v["media"]):
        raise PilotError("Unknown exact attachment ID.")
    notes = output.read("annotations.private.json") or {}
    note = notes.setdefault(key, {})
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
    if description is not None:
        if not isinstance(description, str) or not 1 <= len(description) <= 1000:
            raise PilotError("Description must contain 1–1000 characters.")
        note["description"] = description
    note["interpretation_status"] = "explicit_annotation"
    output.write("annotations.private.json", notes)
    publish(output, apply_annotations(records, notes))


COMMANDS = ("catalog-build", "catalog-lookup", "catalog-annotate", "catalog-raindrop-match")


def add_commands(sub):
    for command in COMMANDS:
        p = sub.add_parser(command, help="Offline private archive discovery; no credentials needed")
        if command == "catalog-raindrop-match":
            p.add_argument("--post", required=True)
            p.add_argument("--page-file", action="append", required=True)
            continue
        p.add_argument("--catalog-dir", type=Path, required=True)
        if command == "catalog-build":
            p.add_argument(
                "--batch",
                nargs=2,
                action="append",
                required=True,
                metavar=("RESTORED_DIR", "BATCH_RECEIPT"),
            )
        elif command == "catalog-lookup":
            p.add_argument("--post", required=True)
            p.add_argument("--asset-id")
        else:
            p.add_argument("--asset-id", required=True)
            p.add_argument("--visibility", choices=VISIBILITIES)
            p.add_argument("--role", choices=ROLES)
            p.add_argument("--description")


def run(args):
    if args.command == "catalog-raindrop-match":
        matches = raindrop_matches(
            args.post, [json.loads(private_bytes(p)) for p in args.page_file]
        )
        return {"matches": matches, "coverage": "not_established", "create_authorized": False}
    output = Store(args.catalog_dir)
    with output.locked():
        if args.command == "catalog-build":
            records = build(args.batch, output)
            publish(output, records)
            return {"records": len(records), "network_calls": 0, "deletion_eligible": False}
        records = read_catalog(output)
        if args.command == "catalog-lookup":
            return {"matches": lookup(records, args.post, args.asset_id), "network_calls": 0}
        if all(v is None for v in (args.visibility, args.role, args.description)):
            raise PilotError("Supply a visibility, role, or description edit.")
        annotate(
            output,
            records,
            args.asset_id,
            visibility=args.visibility,
            role=args.role,
            description=args.description,
        )
        return {"annotation_updated": True, "network_calls": 0}
