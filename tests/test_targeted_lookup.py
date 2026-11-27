"""One targeted lookup supplies authors for an existing primary and its quote."""

import json
import sys
from copy import deepcopy

import httpx
import pytest
from test_archive import (  # noqa: F401
    includes,
    inventory,
    manifest,
    manifest_hash,
    media_http,
    photo,
    post,
    storage,
)

from x_bookmark_migrator import cli
from x_bookmark_migrator.archive import archive_batch, restore_batch
from x_bookmark_migrator.backlog import archive_anchors
from x_bookmark_migrator.catalog import annotate, build, publish, read_catalog
from x_bookmark_migrator.core import Store, post_fields
from x_bookmark_migrator.drive import digest
from x_bookmark_migrator.publication import PublicationStore


@pytest.fixture
def repair(tmp_path, storage, monkeypatch, capsys):  # noqa: F811
    state, drive, server, _ = storage
    ref = post("102", author_id="901")
    primary = post(
        referenced_tweets=[{"id": "102", "type": "quoted"}],
        attachments={"media_keys": ["photo-1"]},
    )
    other = post("103")
    inv = inventory(state, [primary, other], includes([photo()], [ref]))
    name = inv.state["posts"]["101"]["expansions_ref"]
    state.write(name, {**state.read(name), "private_file": name})
    with httpx.Client(transport=httpx.MockTransport(media_http)) as cdn:
        old = archive_batch(inv, drive, cdn)
    original = Store(tmp_path / "original")
    restore_batch(drive, manifest(state, old), original, manifest_hash(state, old))
    batch = (original.root, state.path(old["private_receipt"]))
    catalog = PublicationStore(tmp_path / "catalog")
    records = build([batch], catalog)
    publish(catalog, records)
    asset = records[0]["versions"][0]["media"][0]
    annotate(
        catalog,
        records,
        asset["asset_id"],
        description="owner caption",
        sha256=asset["storage"]["sha256"],
    )
    notes = catalog.read("annotations.private.json")
    catalog.write(
        "annotations.private.json", {**notes, "category_overrides": {"103": "owner folder"}}
    )
    publish(catalog, build([batch], catalog))

    lookup = {
        "endpoint": "/2/tweets",
        "status": 200,
        "private_file": "synthetic",
        "request": {"ids": "101,102,777,888", **post_fields("tweet")},
        "response": {
            "data": [primary, ref, post("777", text="unrelated lookup content")],
            "includes": {"users": [{"id": "901", "username": "quoted_author"}]},
            "errors": [{"resource_type": "tweet", "resource_id": "888"}],
        },
    }
    path = state.path("synthetic")
    state.write(path.name, lookup)
    argv = [
        "x-migrator",
        "--state-dir",
        str(state.root),
        "archive-repair-lookup",
        "--batch",
        *map(str, batch),
        "--post-id",
        "101",
        "--lookup",
        str(path),
        "--drive-folder-id",
        "root",
        "--catalog-dir",
        str(catalog.root),
    ]
    sessions = []
    client = httpx.Client

    def fake_client(**kw):
        sessions.append(len(sessions))
        return client(transport=httpx.MockTransport(server.handle), **kw)

    monkeypatch.setattr(httpx, "Client", fake_client)

    def run(*args, success=True):
        monkeypatch.setattr(sys, "argv", argv + list(args))
        assert cli.main() == (0 if success else 1)
        output = capsys.readouterr()
        return json.loads(output.out) if success else output.err

    return state, server, original, catalog, path, lookup, sessions, run


def test_targeted_lookup_restore_promotion_and_retry(repair, monkeypatch):
    state, server, original, catalog, path, lookup, sessions, run = repair
    before = {p.name: p.read_bytes() for p in original.root.iterdir()}
    prior, unrelated = read_catalog(catalog)
    annotations = catalog.path("annotations.private.json").read_bytes()
    plan = run()
    assert plan["media_to_fetch"] == 0 and plan["media_to_reverify"] == 1 and not sessions
    args = ("--apply-plan", plan["plan_sha256"])
    # Break readback in the second Drive session, after the writer has returned.
    media_id = prior["versions"][0]["media"][0]["storage"]["file_id"]
    handle = server.handle

    def corrupt(request):
        if (
            len(sessions) == 2
            and request.url.path.endswith("/" + media_id)
            and request.url.params.get("alt") == "media"
        ):
            return httpx.Response(200, content=b"damaged")
        return handle(request)

    with monkeypatch.context() as patch:
        patch.setattr(server, "handle", corrupt)
        run(*args, success=False)
    assert read_catalog(catalog) == [prior, unrelated]
    objects = dict(server.bytes)
    for _ in range(2):
        assert run(*args) == dict(
            records=1,
            media_files=1,
            bytes_verified=1,
            blocked=0,
            deletion_eligible=False,
            x_api_calls=0,
            catalog_promoted=True,
        )
        record, still_unrelated = read_catalog(catalog)
        assert still_unrelated == unrelated
        assert len(record["versions"]) == 2 and prior["versions"][0] in record["versions"]
        version = next(v for v in record["versions"] if not v["blockers"])
        assert version["posts"][1]["author"] == lookup["response"]["includes"]["users"][0]
        assert version["media"][0]["description"] == "owner caption"
        assert version["media"][0]["storage"] == prior["versions"][0]["media"][0]["storage"]
        assert record["source_post_url"] == prior["source_post_url"]
        assert catalog.path("annotations.private.json").read_bytes() == annotations
        assert server.bytes == objects
        assert version["manifest"]["sha256"] not in archive_anchors(state, "root")
    successor = next(catalog.root.parent.glob("lookup-*/101.json"))
    payload = json.loads(successor.read_bytes())
    predecessor = json.loads(before["101.json"])
    for key in (
        "source_post_id",
        "source_post_url",
        "source_evidence",
        "inventory_record",
        "media",
        "folders",
        "category",
    ):
        assert payload[key] == predecessor[key]
    assert payload["targeted_lookup"]["sha256"] == digest(path.read_bytes())
    assert b"unrelated lookup content" not in successor.read_bytes()
    assert {p.name: p.read_bytes() for p in original.root.iterdir()} == before
    assert sessions == list(range(6))


@pytest.mark.parametrize(
    "fault",
    [
        "missing_primary",
        "missing_ref",
        "scoped",
        "ambiguous",
        "wrong_author",
        "wrong_endpoint",
        "not_requested",
        "http_failure",
        "missing_user",
    ],
)
def test_partial_lookup_stays_incomplete(repair, fault):
    state, server, _, catalog, path, lookup, sessions, run = repair
    plan = run()
    evidence = deepcopy(lookup)
    body = evidence["response"]
    if fault == "missing_primary":
        body["data"].pop(0)
    elif fault == "missing_ref":
        body["data"].pop(1)
    elif fault == "scoped":
        body["errors"].append({"resource_type": "tweet", "resource_id": "102"})
    elif fault == "ambiguous":
        body["errors"].append({"title": "unattributed"})
    elif fault == "wrong_author":
        body["data"][1]["author_id"] = "999"
    elif fault == "wrong_endpoint":
        evidence["endpoint"] = "/2/users/100/bookmarks"
    elif fault == "not_requested":
        evidence["request"]["ids"] = "101,777,888"
    elif fault == "http_failure":
        evidence["status"] = 503
    else:
        body["includes"]["users"].clear()
    state.write(path.name, evidence)
    objects = dict(server.bytes)
    run(success=False)
    run("--apply-plan", plan["plan_sha256"], success=False)
    assert not sessions and server.bytes == objects
    (version,) = read_catalog(catalog)[0]["versions"]
    assert version["blockers"] == ["blocked_missing_author"]


def test_catalog_requires_exact_predecessor(repair):
    _, _, _, catalog, _, _, sessions, run = repair
    records = read_catalog(catalog)
    publish(catalog, records[1:])
    plan = run()
    assert "predecessor" in run("--apply-plan", plan["plan_sha256"], success=False)
    assert not sessions and read_catalog(catalog) == records[1:]


def test_moved_media_cannot_be_promoted_even_if_bytes_still_match(repair):
    _, server, _, catalog, _, _, _, run = repair
    records = read_catalog(catalog)
    media = records[0]["versions"][0]["media"][0]["storage"]
    server.objects[media["file_id"]]["parents"] = ["elsewhere"]
    objects = dict(server.bytes)
    plan = run()
    assert "parent" in run("--apply-plan", plan["plan_sha256"], success=False)
    assert server.bytes == objects and read_catalog(catalog) == records
