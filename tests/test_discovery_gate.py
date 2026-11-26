"""Mandatory gate tests use real archive, catalog, adapter and transaction code."""

import json

import httpx
import pytest
from test_archive import archive_batch, includes, inventory, media_http, photo, post
from test_archive import storage as storage  # noqa: F401
from test_raindrop import item, snapshot
from test_unbookmark import migration as migration  # noqa: F401

from x_bookmark_migrator.archive import restore_batch
from x_bookmark_migrator.catalog import build, publish, read_catalog
from x_bookmark_migrator.core import PilotError, Store
from x_bookmark_migrator.discovery_gate import positive_media_only
from x_bookmark_migrator.inventory import Inventory
from x_bookmark_migrator.raindrop import blocked
from x_bookmark_migrator.unbookmark import make_plan


def assert_no_delete_or_intent(store, source):
    assert not source.deletes
    assert not list(store.root.glob("unbookmark-*.intent.private.json"))
    assert Inventory.load(store).state["consistent"]


def test_v1_plan_zero_calls(migration):
    store, _, _, source, _, _, plan, apply = migration
    name = plan()
    old = store.read(name)
    old["schema_version"] = 1
    store.write(name, old)
    with pytest.raises(PilotError, match="legacy"):
        apply(name)
    assert not source.calls and not source.provider.calls
    assert_no_delete_or_intent(store, source)


def test_legacy_helper_attestation_required(migration):
    store, _, _, source, _, _, plan, apply = migration
    with pytest.raises(PilotError, match="legacy removal helpers"):
        apply(plan(), legacy_helper_retired=False)
    assert not source.calls
    assert_no_delete_or_intent(store, source)


@pytest.mark.parametrize("fault", ["quote_only", "source", "gallery", "missing", "ambiguous"])
def test_catalog_faults_leave_x_untouched(migration, fault):
    store, _, _, source, _, _, plan, apply = migration
    name = plan()
    records = read_catalog(source.catalog)
    if fault == "quote_only":
        # 101 still appears in versions[].posts but is no longer a saved primary.
        records[0]["post_id"] = "999"
    elif fault == "source":
        records[0]["canonical_url"] = "https://x.com/i/status/999"
    elif fault == "missing":
        records = records[1:]
    elif fault == "ambiguous":
        source.provider.active = []
        source.catalog.write("media-only.private.json", {"101": {"classification": "media_only"}})
    publish(source.catalog, records)
    if fault == "gallery":
        source.catalog.path("index.html").write_text("<html>No source link</html>")
    result = apply(name)
    assert result["blocked"]
    assert_no_delete_or_intent(store, source)


@pytest.mark.parametrize("fault", ["trash", "removed", "false", "wrong_id", "401", "429"])
def test_provider_fault_zero_delete(migration, fault):
    store, _, _, source, _, _, plan, apply = migration
    name = plan(["101", "102"])
    if fault == "trash":
        source.provider.active = []
        source.provider.trash = [item(rid=101, cid=-99)]
    elif fault in ("401", "429"):
        original = source.adapter._call

        def failure(*args, **kwargs):
            if args[0] == "get":
                raise blocked(
                    "provider_request_failed", retryable=True, context={"http_status": fault}
                )
            return original(*args, **kwargs)

        source.adapter._call = failure
    else:
        body = {"result": True, "item": item(rid=101)}
        if fault == "removed":
            body["item"]["removed"] = True
        elif fault == "false":
            body["result"] = False
        else:
            body["item"]["link"] = "https://x.com/i/status/999"
        source.provider.bad_get = body
    result = apply(name)
    assert result["blocked"]
    assert_no_delete_or_intent(store, source)
    if fault in ("401", "429", "false"):
        report = store.read(result["private_report"])
        assert report["outcomes"][0]["retryable"]
        assert len(report["outcomes"]) == 1  # no requests for next post after provider failure


def test_fresh_get_last_and_evidence_in_intent(migration):
    store, _, _, source, _, _, plan, apply = migration
    name = plan()
    source.provider.active.append(item(rid=999))
    seen = []
    original_call = source.adapter._call

    def call(*args, **kwargs):
        seen.append(args[0])
        return original_call(*args, **kwargs)

    source.adapter._call = call
    # MockTransport already captured source.handle; use store.create as the exact
    # intent boundary to assert no cached GET proof was used.
    original_create = store.create

    def create(name, value):
        if name.endswith(".intent.private.json"):
            assert seen[-1] == "get"
            gate = value["discovery_gate"]
            assert gate["proof"]["source_post_id"] == value["post_id"]
            assert gate["kind"] == "raindrop"
        return original_create(name, value)

    store.create = create
    assert not apply(name)["blocked"]
    assert seen == ["get"]
    assert source.deletes


def test_missing_saved_id_blocks_without_library_lookup(migration):
    store, _, _, source, _, _, plan, apply = migration
    records = read_catalog(source.catalog)
    del records[0]["raindrop_id"]
    publish(source.catalog, records)
    result = apply(plan([records[0]["post_id"]]))
    assert result["blocked_reasons"] == {"blocked_discovery": 1}
    assert not source.provider.calls
    assert_no_delete_or_intent(store, source)


def test_media_only_positive_empty_text_end_to_end(migration, tmp_path):
    store, drive, _, source, _, _, _, apply = migration
    inv = inventory(
        store, [post("101", text="", attachments={"media_keys": ["photo-1"]})], includes([photo()])
    )
    with httpx.Client(transport=httpx.MockTransport(media_http)) as media:
        archived = archive_batch(inv, drive, media)
    receipt = store.read(archived["private_receipt"])
    restored = Store(tmp_path / "media-restored")
    restore_batch(drive, receipt["manifest"]["file_id"], restored, receipt["manifest"]["sha256"])
    records = build([(restored.root, store.path(archived["private_receipt"]))], source.catalog)
    publish(source.catalog, records)
    source.catalog.write(
        "media-only.private.json",
        {
            "101": {
                "schema_version": 1,
                "source_post_id": "101",
                "source_post_url": "https://x.com/i/status/101",
                "affirmative": True,
                "classification": "media_only",
                "reviewed_by": "owner",
                "payload_sha256": records[0]["versions"][0]["payload_receipt"]["sha256"],
            }
        },
    )
    name = make_plan(
        store, drive, archived["private_receipt"], ["101"], "100", acceptance_confirmed=True
    )["private_plan"]
    result = apply(name, raindrop=None)
    assert not result["blocked"] and len(source.deletes) == 1
    assert not source.provider.calls
    intent = json.loads(next(store.root.glob("unbookmark-*.intent.private.json")).read_text())
    assert intent["discovery_gate"]["kind"] == "media_only"
    assert "https://x.com/i/status/101" in source.catalog.path("index.html").read_text()


@pytest.mark.parametrize(
    "fault", ["prose", "gif_caption", "emoji", "unknown", "quote", "repost", "hash"]
)
def test_substantive_or_ambiguous_not_exempt(fault):
    payload = {
        "post_id": "101",
        "posts": [{"id": "101", "text": ""}],
        "inventory_record": {"post": {}},
        "media": [{"post_id": "101", "logical_type": "photo", "storage": {}}],
    }
    classification = {
        "schema_version": 1,
        "source_post_id": "101",
        "source_post_url": "https://x.com/i/status/101",
        "classification": "media_only",
        "affirmative": True,
        "reviewed_by": "owner",
        "payload_sha256": "a" * 64,
    }
    if fault in ("prose", "gif_caption", "emoji"):
        payload["posts"][0]["text"] = {
            "prose": "Substantive article",
            "gif_caption": "Look at this",
            "emoji": "🔥 #reaction",
        }[fault]
    elif fault == "unknown":
        payload["media"][0]["logical_type"] = "unknown"
    elif fault in ("quote", "repost"):
        payload["inventory_record"]["post"]["referenced_posts"] = [{"id": "999", "type": fault}]
    else:
        classification["payload_sha256"] = "b" * 64
    assert not positive_media_only(payload, classification, "a" * 64)


def test_25_32_43_checkpoint_is_not_new_gate_evidence():
    # 100 synthetic saved primaries: historical removals, queued complete, blocked
    # archives. None of these old labels is a Raindrop proof.
    from x_bookmark_migrator.raindrop import inspect_inventory

    groups = {"removed": range(1, 26), "queued": range(26, 58), "blocked": range(58, 101)}
    empty = snapshot()
    missing = {
        key: sum(inspect_inventory(empty, str(pid))["state"] == "absent" for pid in ids)
        for key, ids in groups.items()
    }
    assert missing == {"removed": 25, "queued": 32, "blocked": 43}


@pytest.mark.parametrize("pair", ["state_catalog", "state_ledger", "catalog_ledger"])
def test_overlapping_state_dirs_zero_external_calls(migration, pair):
    store, _, _, source, _, _, plan, apply = migration
    name = plan()
    catalog_dir = source.catalog.root
    if pair == "state_catalog":
        catalog_dir = store.root
    elif pair == "state_ledger":
        source.adapter.store = store
    else:
        source.adapter.store = source.catalog
    with pytest.raises(PilotError, match="distinct state directories"):
        apply(name, catalog_dir=catalog_dir)
    assert not source.calls and not source.provider.calls
    assert_no_delete_or_intent(store, source)


def test_malformed_catalog_version_is_reported_as_blocked(migration, monkeypatch):
    store, _, _, source, _, _, plan, apply = migration
    name = plan()
    records = read_catalog(source.catalog)
    del records[0]["versions"]
    monkeypatch.setattr("x_bookmark_migrator.discovery_gate.read_catalog", lambda _: records)
    assert apply(name)["blocked"]
    assert not source.provider.calls
    assert_no_delete_or_intent(store, source)
