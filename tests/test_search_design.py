"""S1 executable examples. All network I/O uses synthetic transports.

The archive, restore, annotations, exact lookup and Raindrop matching use repository code.
"""

import copy
from urllib.parse import unquote

import httpx
import pytest
from test_archive import includes, inventory, media_http, photo, post, storage, video  # noqa: F401
from test_raindrop import item, snapshot

from x_bookmark_migrator.archive import archive_batch, restore_batch
from x_bookmark_migrator.catalog import annotate, build, drive_link, lookup, read_catalog
from x_bookmark_migrator.core import Store
from x_bookmark_migrator.raindrop import inspect_inventory
from x_bookmark_migrator.source import raindrop_post_id

TAIL = "The last sluice opened at moonrise."
LONG_TEXT = "Winter runoff filled the model reservoir. " * 900 + TAIL
IMAGE = "Paper heron balanced on a red wheel."
VIDEO = "Paper heron turns a brass crank in a loop."
PHOTO_ID = "704:1:p"
VIDEO_ID = "702:1:video-1"


@pytest.fixture
def archive(storage, tmp_path):  # noqa: F811
    state, drive, _, _ = storage
    output = Store(tmp_path / "discovery")
    batches = []
    # The photo belongs to a quote. The saved primary and attachment owner differ.
    for name, posts, context in (
        (
            "early",
            [
                post(
                    "701",
                    note_post={"text": LONG_TEXT},
                    referenced_posts=[{"type": "quoted", "id": "704"}],
                ),
                post("702", text="Workshop animation", attachments={"media_keys": ["video-1"]}),
            ],
            includes(
                [photo("p"), video()],
                [post("704", text="A folded model", attachments={"media_keys": ["p"]})],
            ),
        ),
        ("later", [post("703", text="A copper rain gauge")], includes()),
    ):
        inv = inventory(state, posts, context)
        with httpx.Client(transport=httpx.MockTransport(media_http)) as media:
            summary = archive_batch(inv, drive, media, post_ids=[p["id"] for p in posts])
        receipt_path = state.path(summary["private_receipt"])
        receipt = state.read(summary["private_receipt"])
        restored = Store(tmp_path / name)
        manifest = receipt["manifest"]
        restore_batch(drive, manifest["file_id"], restored, manifest["sha256"])
        batches.append((restored.root, receipt_path))
    return output, batches


def record_text(record):
    """Minimal UTF-8 body for the proposed Drive probe, built from a catalog record."""
    lines = ["Saved post: " + record["source_post_url"]]
    if "category_override" in record:
        lines.append("Approved category: " + record["category_override"])
    if "raindrop_url" in record:
        lines.append(record["raindrop_url"])
    for version in record["versions"]:
        lines.extend(
            [
                "Snapshot: "
                + version["manifest"]["sha256"]
                + " / "
                + version["payload_receipt"]["sha256"],
                "Preserved payload: " + version["payload_url"],
                "Original category: " + version["category"],
                *version["blockers"],
            ]
        )
        for folder in version["folders"]:
            lines.extend(folder["names"])
        for p in version["posts"]:
            label = "Primary" if p["id"] == record["post_id"] else "Referenced post"
            lines.extend(
                [
                    label + ": " + p["source_post_url"],
                    p["text"] if p["text"] is not None else "(text unavailable)",
                ]
            )
        for asset in version["media"]:
            lines.extend(
                [
                    "Attachment: " + asset["asset_id"],
                    asset.get("logical_type", "unknown type"),
                    asset["attachment_post_url"],
                    asset["availability"],
                    asset["browse_visibility"],
                    asset["interpretation_status"] + ": " + asset["description"],
                ]
            )
            if "archive_url" in asset:
                lines.append(asset["archive_url"])
    return "\n".join(lines) + "\n"


def literal_hits(units, clue):
    return {pid for pid, body in units.items() if clue.casefold() in body.casefold()}


def test_search_units_and_incremental_rebuild(archive):
    output, batches = archive
    originals = {
        p: p.read_bytes()
        for root, receipt in batches
        for p in [receipt, *root.iterdir()]
        if p.is_file()
    }
    first = build(batches[:1], output)
    annotate(output, first, PHOTO_ID, description=IMAGE)
    annotate(output, read_catalog(output), VIDEO_ID, description=VIDEO, visibility="hidden")
    before = {r["post_id"]: record_text(r) for r in build(batches[:1], output)}
    assert set(before) == {"701", "702"}
    assert LONG_TEXT in before["701"]
    assert literal_hits(before, TAIL) == {"701"}
    assert literal_hits(before, "paper heron") == {"701", "702"}
    assert literal_hits(before, "brass crank") == {"702"}  # refine ambiguous clue
    assert literal_hits(before, "red wheel") == {"701"}
    assert literal_hits(before, "synthetic category") == {"701", "702"}
    assert literal_hits(before, "violet submarine") == set()
    # Frozen positive target: literal matching misses this paraphrase. S3 must measure retrieval.
    assert literal_hits(before, "origami bird riding a scarlet circle") == set()
    asset = lookup(first, "704", PHOTO_ID)[0]
    assert asset["source_post_id"] == "701" and asset["post_id"] == "704"
    assert asset["archive_url"] == drive_link(asset["storage"])
    assert asset["archive_url"] in before["701"]
    assert (output.root / unquote(asset["local_url"])).read_bytes().startswith(b"\x89PNG")

    notes = output.read("annotations.private.json")
    notes["category_overrides"] = {"702": "Kinetic sculpture"}
    output.write("annotations.private.json", notes)
    records = build(batches, output)
    changed = "A tin kingfisher spins a flywheel."
    annotate(output, records, VIDEO_ID, description=changed)
    saved_notes = output.read("annotations.private.json")
    annotate(output, read_catalog(output), VIDEO_ID, description=changed)
    assert output.read("annotations.private.json") == saved_notes
    after = {r["post_id"]: record_text(r) for r in read_catalog(output)}
    assert set(after) == {"701", "702", "703"}
    assert before["701"] == after["701"]
    assert literal_hits(after, "copper rain gauge") == {"703"}
    assert literal_hits(after, changed) == literal_hits(after, "kinetic sculpture") == {"702"}
    assert literal_hits(after, "brass crank") == set()
    assert literal_hits(after, "synthetic category") == set(after)
    assert {
        r["post_id"]: record_text(r) for r in build([*reversed(batches), batches[0]], output)
    } == after
    hidden = lookup(read_catalog(output), "702", VIDEO_ID)[0]
    assert hidden["browse_visibility"] == "hidden"
    assert hidden["archive_url"] in before["702"] and hidden["archive_url"] in after["702"]
    assert all(p.read_bytes() == original for p, original in originals.items())


def test_missing_bytes_and_raindrop_archive_return(archive):
    output, batches = archive
    records = build(batches, output)
    asset = lookup(records, "704", PHOTO_ID)[0]
    (output.root / unquote(asset["local_url"])).unlink()
    # Matching a description can establish a candidate even when opening local bytes fails.
    records = build(batches, output)
    annotate(output, records, PHOTO_ID, description=IMAGE)
    missing = lookup(read_catalog(output), "704", PHOTO_ID)[0]
    assert missing["availability"] == "missing_local" and "local_url" not in missing
    assert missing["description"] == IMAGE and missing["archive_url"] == asset["archive_url"]

    pointer = item(pid="701", rid=17, link="https://twitter.com/example/status/701", cid=4)
    saved = snapshot([pointer, item(pid="704", rid=18)])
    before = copy.deepcopy(saved)
    for _ in range(2):
        pid = raindrop_post_id(pointer["link"])
        assert inspect_inventory(saved, pid)["raindrop_ids"] == [17]
        record = next(r for r in records if r["source_post_id"] == pid)
        assert record["post_id"] == "701" and TAIL in record_text(record)
    assert saved == before
    duplicate = snapshot([pointer, item(pid="701", rid=19)])
    assert inspect_inventory(duplicate, "701")["state"] == "conflict"


@pytest.mark.parametrize(
    ("prior", "observed", "expected"),
    [
        (None, None, "create with reserved ID"),
        (None, b"revised", "checkpoint only"),  # lost create acknowledgement
        (b"old", b"old", "update same ID"),
        (b"old", b"revised", "checkpoint only"),  # lost update acknowledgement
        (b"revised", b"revised", "checkpoint only"),  # unchanged full rebuild
        (b"old", None, "hold"),  # previously verified file disappeared
        (b"old", b"external edit", "hold"),
        (None, b"unrelated", "hold"),
    ],
)
def test_derived_file_reconciliation_table(prior, observed, expected):
    """Byte-level S1 decision table after checking logical key, parent, ID and access.

    None is a confirmed absence; an indeterminate read cannot enter this table.
    The reserved file ID and desired bytes are already durable in every row.
    """
    if observed == b"revised":
        action = "checkpoint only"
    elif observed != prior:
        action = "hold"
    elif prior is None:
        action = "create with reserved ID"
    else:
        action = "update same ID"
    assert action == expected
