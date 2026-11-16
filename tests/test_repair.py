"""Synthetic historical audit and shared-adapter orchestration; no live accounts."""

import copy
import json
import sys

import httpx
import pytest
import test_catalog
from test_archive import storage  # noqa: F401

from x_bookmark_migrator import cli
from x_bookmark_migrator.catalog import build
from x_bookmark_migrator.core import PilotError, Store
from x_bookmark_migrator.drive import digest, encode
from x_bookmark_migrator.repair import (
    backfill,
    classification,
    content_binding,
    reconcile,
    saved_raindrop_inspector,
    scan_helper,
    scan_native,
)


@pytest.fixture
def corpus(storage, tmp_path):  # noqa: F811
    return test_catalog.corpus.__wrapped__(storage, tmp_path)


def native(store, pid, status=None, body=None, number=0):
    base = f"unbookmark-synthetic-{pid}-{number:06d}"
    intent = {
        "schema_version": 1,
        "state": "unbookmark_requested",
        "post_id": pid,
        "owner_id": "900",
        "endpoint": f"/2/users/900/bookmarks/{pid}",
    }
    store.write(base + ".intent.private.json", intent)
    if status is not None:
        store.write(
            base + ".response.private.json",
            {
                "endpoint": intent["endpoint"],
                "intent_sha256": digest(encode(intent)),
                "status": status,
                "response": body,
            },
        )
    return base


def helper(store, rows):
    store.write("helper.private.json", {"attempts": rows})
    store.write(
        "helper-format.private.json",
        {
            "schema_version": 1,
            "format_description": "Synthetic helper: all DELETE intents with receipt outcomes.",
            "all_intents_included": True,
            "files": [
                {
                    "path": "helper.private.json",
                    "record_count": len(rows),
                    "sha256": digest(store.path("helper.private.json").read_bytes()),
                }
            ],
            "records_pointer": "/attempts",
            "post_id_pointer": "/id",
            "outcome_pointer": "/state",
            "outcome_map": {
                "removed": "confirmed_removed",
                "lost": "outcome_unknown",
                "denied": "rejected",
            },
        },
    )
    return store.path("helper-format.private.json")


@pytest.mark.parametrize(
    "status,body,outcome",
    [
        (200, {"data": {"bookmarked": False}}, "confirmed_removed"),
        (200, {"data": {"bookmarked": True}}, "outcome_unknown"),
        (
            200,
            {"data": {"bookmarked": False}, "errors": [{"message": "synthetic"}]},
            "outcome_unknown",
        ),
        (None, None, "outcome_unknown"),
        (500, {}, "outcome_unknown"),
        (401, {}, "rejected"),
        (429, {}, "rejected"),
    ],
)
def test_native_all_outcomes_without_writing_receipts(tmp_path, status, body, outcome):
    store = Store(tmp_path / "evidence")
    native(store, "101", status, body)
    before = {p.name: p.read_bytes() for p in store.root.iterdir()}
    result = scan_native(store.root)
    assert result[0]["outcome"] == outcome
    assert {p.name: p.read_bytes() for p in store.root.iterdir()} == before


def test_tampered_response_and_orphan_receipt(tmp_path):
    store = Store(tmp_path / "evidence")
    base = native(store, "101", 200, {"data": {"bookmarked": False}})
    response = store.read(base + ".response.private.json")
    response["intent_sha256"] = "0" * 64
    store.write(base + ".response.private.json", response)
    assert scan_native(store.root)[0]["outcome"] == "outcome_unknown"
    assert scan_native(store.root)[0]["blockers"] == ["response_intent_mismatch"]
    store.path(base + ".intent.private.json").unlink()
    with pytest.raises(PilotError, match="Orphan"):
        scan_native(store.root)


def test_helper_mapping_retains_every_intent_and_private_provenance(tmp_path):
    store = Store(tmp_path / "helper")
    spec = helper(
        store,
        [
            {"id": "101", "state": "removed"},
            {"id": "102", "state": "lost"},
            {"id": "103", "state": "denied"},
            {"id": "104", "state": "new_unknown_state"},
        ],
    )
    attempts = scan_helper(spec)
    assert [a["outcome"] for a in attempts] == [
        "confirmed_removed",
        "outcome_unknown",
        "rejected",
        "outcome_unknown",
    ]
    assert attempts[-1]["blockers"] == ["unmapped_helper_outcome"]
    assert attempts[0]["evidence"][0]["record_index"] == 0
    assert attempts[0]["descriptor"]["sha256"] == digest(spec.read_bytes())
    store.write("helper.private.json", {"attempts": []})
    with pytest.raises(PilotError, match="hash"):
        scan_helper(spec)


def test_helper_requires_documented_exhaustive_format(tmp_path):
    store = Store(tmp_path / "helper")
    spec = helper(store, [{"id": "101", "state": "removed"}])
    data = store.read(spec.name)
    data["all_intents_included"] = False
    store.write(spec.name, data)
    with pytest.raises(PilotError, match="Document"):
        scan_helper(spec)


def audit(corpus, tmp_path):
    batch, output, _, _ = corpus
    records = build([batch], output)
    state = Store(tmp_path / "removals")
    native(state, "101", 200, {"data": {"bookmarked": False}})
    native(state, "102")
    native(state, "103", 401, {})
    return records, reconcile(records, scan_native(state.root), coverage={"complete": True})


def test_primary_only_unknown_not_deleted_and_all_categories(corpus, tmp_path):
    records, report = audit(corpus, tmp_path)
    rows = {r["source_post_id"]: r for r in report["rows"]}
    assert rows["101"]["deletion_outcome"] == "confirmed_removed"
    assert rows["102"]["deletion_outcome"] == "outcome_unknown"
    assert rows["103"]["deletion_outcome"] == "rejected"
    assert rows["102"]["classification"] == "ambiguous"
    assert rows["102"]["raindrop_required"] is True  # emoji + GIF is not exempt
    assert rows["101"]["backfill_needed"] and rows["102"]["backfill_needed"]
    assert not rows["103"]["backfill_target"]
    assert "104" not in rows  # quote is not the saved primary
    extra = reconcile(records, [{"post_id": "104", "outcome": "outcome_unknown"}])
    orphan = next(r for r in extra["rows"] if r["source_post_id"] == "104")
    assert orphan["blockers"] == ["attempt_without_primary_archive", "raindrop_readback_unverified"]
    assert orphan["report_category"] == "exceptional_unrecoverable"


def test_attempt_aggregation_never_hides_unknown(corpus, tmp_path):
    records, _ = audit(corpus, tmp_path)
    attempts = [
        {"post_id": "101", "outcome": o}
        for o in ("confirmed_removed", "rejected", "outcome_unknown")
    ]
    report = reconcile(records, attempts)
    assert report["summary"]["removal_attempts"] == 3
    assert report["summary"]["attempted_primary_ids"] == 1
    assert report["rows"][0]["deletion_outcome"] == "outcome_unknown"


def test_positive_classification_bound_to_content(corpus, tmp_path):
    records, _ = audit(corpus, tmp_path)
    record = records[1]
    decisions = {
        "102": {
            "content_binding": content_binding(record),
            "classification": "media_only",
            "basis": "owner_review",
            "reason": "Synthetic reviewed reaction",
        }
    }
    assert classification(record, decisions)[0] == "media_only"
    record["versions"][0]["payload_receipt"]["sha256"] = "f" * 64
    with pytest.raises(PilotError, match="stale"):
        classification(record, decisions)


def test_empty_text_complete_media_is_affirmative_but_missing_media_is_not(corpus, tmp_path):
    records, _ = audit(corpus, tmp_path)
    record = records[1]
    record["versions"][0]["posts"][0]["text"] = ""
    assert classification(record, {})[0] == "media_only"
    record["versions"][0]["media"][0]["availability"] = "missing_local"
    assert classification(record, {})[0] == "ambiguous"


class Adapter:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def ensure_raindrop(self, pid, url, archive_ref):
        self.calls.append((pid, url, archive_ref))
        if self.error:
            raise PilotError("Synthetic provider error", code=self.error)
        return {"source_post_id": pid, "source_post_url": url, "raindrop_id": 7}


def test_backfill_only_calls_shared_ensure_for_removed_and_unknown(corpus, tmp_path):
    _, report = audit(corpus, tmp_path)
    adapter = Adapter()
    result = backfill(report, adapter, ["101", "102"], confirmed=True)
    assert [c[0] for c in adapter.calls] == ["101", "102"]
    assert result["summary"]["verified"] == 2
    assert result["summary"]["x_api_calls"] == 0
    assert adapter.calls[0][1] == "https://x.com/i/status/101"
    assert "payload" in adapter.calls[0][2]


@pytest.mark.parametrize("error", ["http_401", "http_429", "partial_inventory", "create_unknown"])
def test_backfill_no_retry_on_provider_error(corpus, tmp_path, error):
    _, report = audit(corpus, tmp_path)
    adapter = Adapter(error)
    result = backfill(report, adapter, ["101", "102"], confirmed=True)
    assert len(adapter.calls) == 1
    assert result["summary"]["blocked"]
    assert result["results"][0]["reason"] == error


@pytest.mark.parametrize("selection", [["104"], ["103"], ["101", "101"], ["101", "103"]])
def test_selection_preflight_makes_zero_calls(corpus, tmp_path, selection):
    _, report = audit(corpus, tmp_path)
    adapter = Adapter()
    with pytest.raises(PilotError):
        backfill(report, adapter, selection, confirmed=True)
    assert adapter.calls == []


def test_unconfirmed_or_incomplete_coverage_makes_zero_calls(corpus, tmp_path):
    _, report = audit(corpus, tmp_path)
    adapter = Adapter()
    with pytest.raises(PilotError):
        backfill(report, adapter, ["101"])
    report["coverage"]["complete"] = False
    with pytest.raises(PilotError):
        backfill(report, adapter, ["101"], confirmed=True)
    assert adapter.calls == []


def test_snapshot_evidence_uses_shared_validators_and_is_never_fresh():
    calls = []

    def inspect(snapshot, pid):
        calls.append((snapshot, pid))
        return {"state": "active", "raindrop_ids": [7], "reason": None}

    def validate(receipt, pid):
        calls.append((receipt, pid))
        return receipt

    inspector = saved_raindrop_inspector(
        {"synthetic": True}, {"101": {"raindrop_id": 7}}, inspect, validate
    )
    assert inspector("101")["state"] == "verified"
    assert inspector("101")["fresh"] is False
    assert inspector("102")["state"] == "active_unverified"
    assert len(calls) == 5


def test_synthetic_100_record_cross_tab_25_32_43(corpus, tmp_path):
    source, _ = audit(corpus, tmp_path)
    records, attempts = [], []
    for i in range(100):
        pid = str(1000 + i)
        r = copy.deepcopy(source[2])
        r.update(post_id=pid)
        r["versions"][0]["posts"] = [{"id": pid, "text": "Synthetic text"}]
        if i >= 57:
            r["versions"][0].update(
                archive_status="blocked", blockers=["synthetic_missing_content"]
            )
        records.append(r)
        if i < 25:
            attempts.append({"post_id": pid, "outcome": "confirmed_removed"})
    report = reconcile(records, attempts, coverage={"complete": True})
    summary = report["summary"]
    assert summary["catalog_records"] == summary["distinct_primary_ids"] == 100
    assert summary["deletion_outcomes"] == {"confirmed_removed": 25, "never_attempted": 75}
    assert sum(summary["cross_tab"]["confirmed_removed"].values()) == 25
    assert summary["cross_tab"]["never_attempted"]["ambiguous_classification"] == 32
    assert summary["cross_tab"]["never_attempted"]["exceptional_unrecoverable"] == 43
    assert not summary["offline_reconciliation_complete"]


def test_audit_cli_offline_redacted_and_requires_helper_coverage(
    corpus, tmp_path, monkeypatch, capsys
):
    batch, output, _, _ = corpus
    state = Store(tmp_path / "attempts")
    native(state, "101")

    def forbidden(*a, **kw):
        pytest.fail("Offline audit initialized HTTP")

    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "x-migrator",
            "repair-audit",
            "--catalog-dir",
            str(output.root),
            "--output-dir",
            str(tmp_path / "audit"),
            "--batch",
            str(batch[0]),
            str(batch[1]),
            "--removal-dir",
            str(state.root),
            "--expected-records",
            "3",
        ],
    )
    assert cli.main() == 1
    stdout = capsys.readouterr().out
    summary = json.loads(stdout)
    assert not summary["removal_coverage_complete"]
    assert summary["catalog_records"] == 3
    assert "101" not in stdout and "Synthetic category" not in stdout
    report = json.loads((tmp_path / "audit/repair-audit.private.json").read_text())
    assert "private_batch_helper_not_audited" in report["coverage"]["blockers"]


def test_real_adapter_backfill_create_crash_recover_and_rerun(corpus, tmp_path):
    from test_raindrop import SkillServer, adapter_for

    _, report = audit(corpus, tmp_path)
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    server.crash_after_post = True
    with pytest.raises(RuntimeError, match="process death"):
        backfill(report, adapter, ["101"], confirmed=True)
    assert len(server.posts) == 1
    assert adapter.store.read("raindrop-101.receipt.private.json") is None
    adapter = adapter_for(tmp_path, server)
    recovered = backfill(report, adapter, ["101"], confirmed=True)
    repeated = backfill(report, adapter, ["101"], confirmed=True)
    assert recovered["summary"]["verified"] == repeated["summary"]["verified"] == 1
    assert len(server.posts) == 1
    receipt = adapter.store.read("raindrop-101.receipt.private.json")
    assert recovered["results"][0]["receipt"]["raindrop_id"] == receipt["raindrop_id"]
    assert receipt["archive_ref"] == report["rows"][0]["repair_archive_ref"]
    assert server.posts[0][1]["note"].startswith("Archive: https://drive.google.com/file/d/")
    assert len(server.posts[0][1]["note"]) < 200


def test_real_adapter_adopts_three_pilot_aliases_preserving_metadata(corpus, tmp_path):
    from test_raindrop import SkillServer, adapter_for, item

    records, _ = audit(corpus, tmp_path)
    report = reconcile(
        records,
        [{"post_id": pid, "outcome": "confirmed_removed"} for pid in ("101", "102", "103")],
        coverage={"complete": True},
    )
    originals = [
        item("101", 7, "https://mobile.twitter.com/old/status/101/photo/1"),
        item("102", 8, "https://m.x.com/new/status/102/video/1"),
        item("103", 9, "https://x.com/i/web/status/103?s=20"),
    ]
    server = SkillServer(copy.deepcopy(originals))
    result = backfill(report, adapter_for(tmp_path, server), ["101", "102", "103"], confirmed=True)
    assert result["summary"]["verified"] == 3
    assert not server.posts
    assert server.active == originals


@pytest.mark.parametrize("fault", ["trash", "duplicates", "partial", "unsupported", "wrong_get"])
def test_real_adapter_failures_stay_blocked_without_create(corpus, tmp_path, fault):
    from test_raindrop import SkillServer, adapter_for, item, snapshot

    _, report = audit(corpus, tmp_path)
    server = SkillServer()
    if fault == "trash":
        server.trash = [item(cid=-99)]
    elif fault == "duplicates":
        server.active = [item(rid=1), item(rid=2)]
    elif fault == "partial":
        server.bad_inventory = snapshot()
        server.bad_inventory["active"]["count"] = 51
    elif fault == "unsupported":
        server.active = [item(link="https://mobile.x.com/home")]
    else:
        server.active = [item()]
        server.bad_get = {"result": True, "item": item(pid="104")}
    result = backfill(report, adapter_for(tmp_path, server), ["101"], confirmed=True)
    assert result["summary"]["blocked"]
    assert result["summary"]["verified"] == 0
    assert not server.posts


def test_real_adapter_saved_inventory_and_receipt_audit(corpus, tmp_path):
    from test_raindrop import SkillServer, adapter_for, item, snapshot

    from x_bookmark_migrator.raindrop import inspect_inventory, validate_receipt

    records, report = audit(corpus, tmp_path)
    server = SkillServer([item()])
    adapter = adapter_for(tmp_path, server)
    backfill(report, adapter, ["101"], confirmed=True)
    receipt = adapter.store.read("raindrop-101.receipt.private.json")
    inspector = saved_raindrop_inspector(
        snapshot(server.active), {"101": receipt}, inspect_inventory, validate_receipt
    )
    result = reconcile(
        records,
        [{"post_id": "101", "outcome": "confirmed_removed"}],
        coverage={"complete": True},
        inspect_raindrop=inspector,
    )
    assert result["rows"][0]["raindrop"]["state"] == "verified"
    assert result["rows"][0]["raindrop"]["fresh"] is False
    assert result["summary"]["live_acceptance"] == "not_established_by_offline_audit"


def test_removal_archive_mismatch_blocks_repair(corpus, tmp_path):
    records, _ = audit(corpus, tmp_path)
    result = reconcile(
        records,
        [
            {
                "post_id": "101",
                "outcome": "confirmed_removed",
                "archive_manifest": {"sha256": "0" * 64},
            }
        ],
        coverage={"complete": True},
    )
    assert "removal_archive_mismatch" in result["rows"][0]["blockers"]
    adapter = Adapter()
    with pytest.raises(PilotError):
        backfill(result, adapter, ["101"], confirmed=True)
    assert not adapter.calls
