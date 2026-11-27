"""S1 executable examples. All network I/O uses synthetic transports.

The archive, restore, annotations, exact lookup and Raindrop matching use repository code.
"""

import base64
import json
from urllib.parse import unquote

import httpx
import pytest
from test_archive import includes, inventory, media_http, photo, post, storage, video  # noqa: F401
from test_raindrop import item, snapshot

from x_bookmark_migrator.archive import archive_batch, restore_batch
from x_bookmark_migrator.catalog import (
    annotate,
    build,
    drive_link,
    lookup,
    publish,
    read_catalog,
    render,
)
from x_bookmark_migrator.catalog_search import search
from x_bookmark_migrator.core import Store
from x_bookmark_migrator.discovery_gate import positive_media_only
from x_bookmark_migrator.raindrop import inspect_inventory
from x_bookmark_migrator.search_projection import drive_text
from x_bookmark_migrator.source import raindrop_post_id
from x_bookmark_migrator.worker import merge_publication

TAIL = "The last sluice opened at moonrise."
LONG_TEXT = "Winter runoff filled the model reservoir. " * 900 + TAIL
IMAGE = "Paper heron balanced on a red wheel."
VIDEO = "Paper heron turns a brass crank in a loop."
PHOTO_ID = "704:1:p"
VIDEO_ID = "702:1:video-1"


def restored_batch(root, drive, name, posts, context, handler=media_http):
    state = Store(root / (name + "-state"))
    inv = inventory(state, posts, context)
    with httpx.Client(transport=httpx.MockTransport(handler)) as media:
        summary = archive_batch(inv, drive, media, post_ids=[p["id"] for p in posts])
    receipt = state.read(summary["private_receipt"])
    restored = Store(root / name)
    manifest = receipt["manifest"]
    restore_batch(drive, manifest["file_id"], restored, manifest["sha256"])
    return restored.root, state.path(summary["private_receipt"])


@pytest.fixture
def archive(storage, tmp_path):  # noqa: F811
    _, drive, _, _ = storage
    output = Store(tmp_path / "discovery")
    quote = post("704", text="A folded model", attachments={"media_keys": ["p"]})
    batches = [
        restored_batch(tmp_path, drive, name, posts, context)
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
                    [quote],
                ),
            ),
            (
                "later",
                [
                    post(
                        "703",
                        text="A copper rain gauge",
                        referenced_posts=[{"type": "quoted", "id": "704"}],
                    ),
                    post("705", text="", attachments={"media_keys": ["solo"]}),
                    post("706", article={"title": "Waterworks ledger"}),
                ],
                includes([photo("p"), photo("solo")], [quote]),
            ),
        )
    ]
    return output, batches


def record_text(record):
    return drive_text(record)["body"]


def literal_hits(units, clue):
    return {pid for pid, body in units.items() if clue.casefold() in body.casefold()}


def category_edit(output, edit_id, pid, value):
    """S1 example of the S3 set/clear writer; use the real store lock and atomic write."""
    with output.locked():
        notes = output.read("annotations.private.json") or {}
        journal = notes.setdefault("category_edit_history", {})
        target = {"primary": pid, "value": value}
        if edit_id in journal:
            if target != journal[edit_id]["edit"]:
                raise ValueError("conflicting edit ID")
            return
        overrides = notes.setdefault("category_overrides", {})
        journal[edit_id] = {"edit": target, "previous": overrides.get(pid)}
        if value is None:
            overrides.pop(pid, None)
        else:
            overrides[pid] = value
        output.write("annotations.private.json", notes)


def test_search_units_and_incremental_rebuild(archive):
    output, batches = archive
    originals = {
        p: p.read_bytes()
        for root, receipt in batches
        for p in [receipt, *root.iterdir()]
        if p.is_file()
    }
    first = build(batches[:1], output)
    image_hash = lookup(first, "704", PHOTO_ID)[0]["storage"]["sha256"]
    video_hash = lookup(first, "702", VIDEO_ID)[0]["storage"]["sha256"]
    annotate(output, first, PHOTO_ID, description=IMAGE, sha256=image_hash)
    annotate(
        output,
        read_catalog(output),
        VIDEO_ID,
        description=VIDEO,
        visibility="hidden",
        sha256=video_hash,
    )
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

    category_edit(output, "first", "702", "Kinetic sculpture")
    records = build(batches, output)
    changed = "A tin kingfisher spins a flywheel."
    annotate(output, records, VIDEO_ID, description=changed, sha256=video_hash)
    saved_notes = output.read("annotations.private.json")
    annotate(output, read_catalog(output), VIDEO_ID, description=changed, sha256=video_hash)
    assert output.read("annotations.private.json") == saved_notes
    after = {r["post_id"]: record_text(r) for r in read_catalog(output)}
    assert set(after) == {"701", "702", "703", "705", "706"}
    assert "Article title: Waterworks ledger" in after["706"]
    assert (
        "blocked_incomplete_article" in after["706"] and "Article plain_text:" not in after["706"]
    )
    assert (
        "quoted: https://x.com/i/status/704" in after["701"] and "Author: example" in after["701"]
    )
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
    category_edit(output, "second", "702", None)
    reopened = Store(output.root)
    cleared_notes = reopened.read("annotations.private.json")
    assert cleared_notes["category_edit_history"]["second"]["previous"] == "Kinetic sculpture"
    category_edit(reopened, "first", "702", "Kinetic sculpture")  # stale identical replay
    with pytest.raises(ValueError, match="conflicting edit ID"):
        category_edit(reopened, "first", "703", "Kinetic sculpture")
    assert reopened.read("annotations.private.json") == cleared_notes
    cleared = {r["post_id"]: record_text(r) for r in build(batches, output)}
    assert literal_hits(cleared, "kinetic sculpture") == set()
    assert literal_hits(cleared, "synthetic category") == set(after)
    assert literal_hits(cleared, changed) == {"702"}
    assert all(p.read_bytes() == original for p, original in originals.items())


def test_missing_bytes_and_raindrop_archive_return(archive):
    output, batches = archive
    records = build(batches, output)
    asset = lookup(records, "704", PHOTO_ID)[0]
    annotate(output, records, PHOTO_ID, description=IMAGE, sha256=asset["storage"]["sha256"])
    (output.root / unquote(asset["local_url"])).unlink()
    # Matching a description can establish a candidate even when opening local bytes fails.
    records = build(batches, output)
    missing = lookup(records, "704", PHOTO_ID)[0]
    assert missing["availability"] == "missing_local" and "local_url" not in missing
    assert missing["description"] == IMAGE and missing["archive_url"] == asset["archive_url"]

    pointer = item(pid="701", rid=17, link="https://twitter.com/example/status/701", cid=4)
    saved = snapshot([pointer, item(pid="704", rid=18)])
    for _ in range(2):
        pid = raindrop_post_id(pointer["link"])
        assert inspect_inventory(saved, pid)["raindrop_ids"] == [17]
        record = next(r for r in records if r["source_post_id"] == pid)
        assert record["post_id"] == "701" and TAIL in record_text(record)
    duplicate = snapshot([pointer, item(pid="701", rid=19)])
    assert inspect_inventory(duplicate, "701")["state"] == "conflict"


def test_shared_quote_and_replaced_media(archive, storage, tmp_path):  # noqa: F811
    output, batches = archive
    records = build(batches, output)
    shared = lookup(records, "704", PHOTO_ID)
    assert [a["source_post_id"] for a in shared] == ["701", "703"]
    assert len({a["storage"]["sha256"] for a in shared}) == 1
    before = {r["post_id"]: record_text(r) for r in records}
    original_hash = shared[0]["storage"]["sha256"]
    annotate(output, records, PHOTO_ID, description=IMAGE, sha256=original_hash)
    rebuilt = build(batches, output)
    after = {r["post_id"]: record_text(r) for r in rebuilt}
    assert {pid for pid in after if after[pid] != before[pid]} == {"701", "703"}
    assert all(a["description"] == IMAGE for a in lookup(rebuilt, "704", PHOTO_ID))
    other = lookup(rebuilt, "705", "705:1:solo")[0]
    assert other["storage"]["sha256"] == original_hash and other["description"] != IMAGE

    # Another valid one-pixel PNG in a later capture of the same primary/quote.
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNg+M8AAAICAQB7CYF4AAAAAElFTkSuQmCC"
    )
    newer = restored_batch(
        tmp_path,
        storage[1],
        "changed",
        [post("701", referenced_posts=[{"type": "quoted", "id": "704"}])],
        includes([photo("p")], [post("704", attachments={"media_keys": ["p"]})]),
        handler=lambda request: httpx.Response(200, content=png),
    )
    merged = build([*batches, newer], output)
    primary = next(r for r in merged if r["post_id"] == "701")
    versions = primary["versions"]
    assert len(versions) == 2
    assert len({v["media"][0]["storage"]["sha256"] for v in versions}) == 2
    publish(output, merged)
    persisted = [
        json.loads(line) for line in output.path("catalog.jsonl").read_bytes().splitlines()
    ]
    assert persisted == merged
    rows = lookup(read_catalog(output), "704", PHOTO_ID)
    assert output.path("index.html").read_text().count(f'alt="{IMAGE}"') == 2
    assert (
        len({(a["source_post_id"], a["manifest_sha256"], a["payload_sha256"]) for a in rows}) == 3
    )
    for a in rows:
        assert (a["description"] == IMAGE) == (a["storage"]["sha256"] == original_hash)
    replacement = next(a for a in rows if a["storage"]["sha256"] != original_hash)
    for version in versions:
        asset = version["media"][0]
        matches = asset["storage"]["sha256"] == original_hash
        assert (asset.get("description_sha256") == original_hash) == matches
        unit = {**primary, "versions": [version]}
        assert (IMAGE in record_text(unit)) == matches
        assert (f'alt="{IMAGE}"' in render([unit])) == matches
        assert asset["archive_url"] in record_text(unit)
    annotate(
        output,
        merged,
        PHOTO_ID,
        description="Green replacement",
        sha256=replacement["storage"]["sha256"],
    )
    rebuilt = build([newer, *reversed(batches)], output)
    # Search keeps both byte variants and each saved context when coalescing repeated captures.
    candidates = search(rebuilt, [IMAGE, "Green replacement"])["matches"]
    assert {(h["source_post_id"], *h["snapshot"]) for h in candidates} == {
        (a["source_post_id"], a["manifest_sha256"], a["payload_sha256"])
        for a in lookup(rebuilt, "704", PHOTO_ID)
    }
    # Reproduce reversed arrival through the worker, then compare with the complete build.
    expected = next(r for r in rebuilt if r["post_id"] == "701")
    low, high = expected["versions"]
    publish(output, [{**expected, "versions": [high]}])
    merge_publication(output, [{**expected, "versions": [low]}])
    assert json.loads(output.path("catalog.jsonl").read_bytes()) == expected
    incremental = read_catalog(output)[0]
    assert drive_text(incremental) == drive_text(expected)
    assert incremental == expected
    for a in lookup(rebuilt, "704", PHOTO_ID):
        assert a["description"] == (
            IMAGE if a["storage"]["sha256"] == original_hash else "Green replacement"
        )


def test_media_only_surface_populations(archive):
    output, batches = archive
    records = build(batches, output)
    units = {r["post_id"]: record_text(r) for r in records}
    expected = {"701", "702", "703", "705", "706"}
    assert set(units) == expected
    solo = lookup(records, "705", "705:1:solo")[0]
    assert solo["archive_url"] in units["705"] and solo["availability"] == "local_hash_verified"
    payload = json.loads((batches[1][0] / "705.json").read_bytes())
    version = next(r for r in records if r["post_id"] == "705")["versions"][0]
    sha = version["payload_receipt"]["sha256"]
    decision = dict(
        schema_version=1,
        source_post_id="705",
        source_post_url=payload["source_post_url"],
        affirmative=True,
        classification="media_only",
        reviewed_by="owner",
        payload_sha256=sha,
    )
    for classification, required in [(decision, expected - {"705"}), (None, expected)]:
        exempt = {"705"} if positive_media_only(payload, classification, sha) else set()
        assert set(units) - exempt == required
        assert {r["post_id"] for r in records} == set(units) == expected


@pytest.mark.parametrize(
    ("prior", "observed", "desired", "current", "trashed", "expected"),
    [
        (None, None, b"new", True, False, "create with reserved ID"),
        (None, b"new", b"new", True, False, "checkpoint only"),
        (b"old", b"old", b"new", True, False, "update same ID"),
        (b"old", b"new", b"new", True, False, "checkpoint only"),
        (b"old", b"old", b"old", True, False, "checkpoint only"),  # edit reverted before write
        (b"old", b"old", b"new", False, False, "hold"),  # stale writer
        (b"old", b"new", b"new", False, False, "hold"),  # stale checkpoint
        (b"old", b"old", b"old", True, True, "hold"),
        (b"old", None, b"new", True, False, "hold"),
        (b"old", b"external", b"new", True, False, "hold"),
        (None, b"unrelated", b"new", True, False, "hold"),
    ],
)
def test_derived_file_reconciliation_table(prior, observed, desired, current, trashed, expected):
    """Executable S1 specification. S3 must run these rows against its fake Drive writer.

    None denotes confirmed absence. Identity, permissions and a durable reserved ID
    are preconditions. Indeterminate reads cannot enter the table.
    """
    if not current or trashed:
        action = "hold"
    elif observed == desired:
        action = "checkpoint only"
    elif observed != prior:
        action = "hold"
    elif prior is None:
        action = "create with reserved ID"
    else:
        action = "update same ID"
    assert action == expected
