"""Synthetic skill boundary, durable recovery and shared FOS-216 contracts."""

import copy
import json
import subprocess
from functools import partial
from pathlib import Path

import pytest

from x_bookmark_migrator.core import PilotError
from x_bookmark_migrator.drive import digest, encode
from x_bookmark_migrator.raindrop import (
    RaindropAdapter,
    inspect_inventory,
    validate_receipt,
)


def item(pid="101", rid=1, link=None, cid=-1):
    return {
        "_id": rid,
        "link": link or f"https://x.com/i/status/{pid}",
        "collection": {"$id": cid},
        "note": "Keep this note",
        "tags": ["keep"],
        "important": True,
        "title": "Existing title",
    }


def snapshot(active=(), trash=()):
    return {
        "schema_version": 1,
        "result": True,
        "active": {"items": list(active), "count": len(active), "pages": len(active) // 50 + 1},
        "trash": {"items": list(trash), "count": len(trash), "pages": len(trash) // 50 + 1},
        "coverage": {"complete": True, "stable_passes": 2, "perpage": 50},
        "observed_at": "2026-11-16T00:00:00+00:00",
    }


class SkillServer:
    def __init__(self, active=(), trash=()):
        self.active, self.trash = list(active), list(trash)
        self.calls = []
        self.crash_after_post = False
        self.bad_get = None
        self.bad_inventory = None
        self.adapter = None

    def call(self, *args, payload=None):
        self.calls.append((args, payload))
        if args == ("list-all",):
            return copy.deepcopy(self.bad_inventory or snapshot(self.active, self.trash))
        if args[0] == "get":
            if self.bad_get is not None:
                return copy.deepcopy(self.bad_get)
            matches = [r for r in self.active + self.trash if r["_id"] == args[1]]
            if not matches:
                raise PilotError("Synthetic missing item")
            return {"result": True, "item": copy.deepcopy(matches[0])}
        assert args == ("add-json",)
        assert self.adapter is not None
        pid = payload["link"].rsplit("/", 1)[1]
        intent = self.adapter.store.read(f"raindrop-{pid}.intent.private.json")
        assert intent and intent["attempts"] in (1, 2)
        # Prove exclusive intent is visible to another reader before remote side effect.
        created = {**copy.deepcopy(payload), "_id": 1000 + len(self.active)}
        self.active.append(created)
        if self.crash_after_post:
            self.crash_after_post = False
            raise RuntimeError("simulated process death after remote POST")
        return {"result": True, "item": created}

    @property
    def posts(self):
        return [c for c in self.calls if c[0] == ("add-json",)]


def adapter_for(tmp_path, server):
    adapter = RaindropAdapter(tmp_path / "raindrop", Path(__file__).resolve())
    adapter._call = server.call
    adapter.ensure_raindrop = partial(adapter.ensure_raindrop, post_text="Synthetic primary text")
    server.adapter = adapter
    return adapter


def test_homepages_preserved_without_blocking_exact_post_inventory(tmp_path):
    homepages = [
        item(rid=2, link="http://twitter.com/"),
        item(rid=3, link="https://x.com/"),
    ]
    server = SkillServer(
        active=[item(), *homepages], trash=[item(rid=4, link="https://x.com/", cid=-99)]
    )
    before = copy.deepcopy((server.active, server.trash))
    adapter = adapter_for(tmp_path, server)
    inventory = adapter.enumerate_now()
    assert inspect_inventory(inventory, "101")["state"] == "active"
    assert inspect_inventory(inventory, "102")["state"] == "absent"
    assert inventory["active"]["items"] == before[0]
    assert inventory["trash"]["items"] == before[1]
    assert adapter.store.read("raindrop-inventory.private.json") == inventory
    assert (server.active, server.trash) == before
    assert server.calls == [(("list-all",), None)]


@pytest.mark.parametrize(
    "link",
    [
        "https://twitter.com/old/status/101?s=20",
        "https://mobile.twitter.com/new/status/101/photo/1",
        "https://m.x.com/new/status/101/video/2",
        "https://x.com/i/web/status/101",
    ],
)
def test_existing_alias_metadata_untouched_zero_create(tmp_path, link):
    existing = item(link=link, cid=55)
    server = SkillServer([existing])
    adapter = adapter_for(tmp_path, server)
    for _ in range(2):
        receipt = adapter.ensure_raindrop(
            "101", "https://x.com/i/status/101", "https://archive.invalid"
        )
        assert receipt["disposition"] == "adopted"
        validate_receipt(receipt, "101")
    assert not server.posts and server.active == [existing]
    assert len([c for c in server.calls if c[0][0] == "get"]) == 2
    assert len([c for c in server.calls if c[0] == ("list-all",)]) == 2


def test_post_then_crash_recovers_exact_receipt_without_duplicate(tmp_path):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    server.crash_after_post = True
    with pytest.raises(RuntimeError):
        adapter.ensure_raindrop("101", "https://x.com/i/status/101", "https://archive.invalid")
    assert len(server.posts) == 1
    assert adapter.store.read("raindrop-101.intent.private.json")
    assert adapter.store.read("raindrop-101.receipt.private.json") is None
    fresh_adapter = adapter_for(tmp_path, server)
    result = fresh_adapter.ensure_raindrop(
        "101", "https://x.com/i/status/101", "https://archive.invalid"
    )
    assert result["raindrop_id"] == 1000 and result["disposition"] == "adopted"
    assert len(server.posts) == 1
    assert server.calls[-1][0] == ("get", 1000)


def test_uncertain_absence_allows_only_one_recovery_post(tmp_path):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    original = adapter._call

    def fail_before_post(*args, **kwargs):
        if args == ("add-json",):
            raise PilotError("uncertain transport")
        return original(*args, **kwargs)

    adapter._call = fail_before_post
    for _ in range(3):
        with pytest.raises(PilotError):
            adapter.ensure_raindrop("101", "https://x.com/i/status/101", "https://archive.invalid")
    assert adapter.store.read("raindrop-101.intent.private.json")["attempts"] == 2


@pytest.mark.parametrize(
    "fault", ["trash", "duplicates", "both", "unsupported", "count", "coverage"]
)
def test_dedupe_fault_zero_create(tmp_path, fault):
    server = SkillServer()
    if fault in ("trash", "both"):
        server.trash = [item(cid=-99)]
    if fault in ("duplicates", "both"):
        server.active = [item(rid=2), item(rid=3)] if fault == "duplicates" else [item(rid=2)]
    if fault == "unsupported":
        server.active = [item(link="https://mobile.x.com/unparsed/path")]
    if fault in ("count", "coverage"):
        server.bad_inventory = snapshot()
        if fault == "count":
            server.bad_inventory["active"]["count"] = 1
        else:
            server.bad_inventory["coverage"]["complete"] = False
    adapter = adapter_for(tmp_path, server)
    with pytest.raises(PilotError):
        adapter.ensure_raindrop("101", "https://x.com/i/status/101", "https://archive.invalid")
    assert not server.posts


@pytest.mark.parametrize("change", ["trash", "removed", "wrong", "id", "false"])
def test_fresh_readback_failure(tmp_path, change):
    server = SkillServer([item()])
    adapter = adapter_for(tmp_path, server)
    bad = {"result": True, "item": item()}
    if change == "trash":
        bad["item"]["collection"]["$id"] = -99
    elif change == "removed":
        bad["item"]["removed"] = True
    elif change == "wrong":
        bad["item"]["link"] = "https://x.com/i/status/999"
    elif change == "id":
        bad["item"]["_id"] = 9
    else:
        bad["result"] = False
    server.bad_get = bad
    with pytest.raises(PilotError):
        adapter.verify_raindrop_now("101", 1)


def test_receipt_deleted_item_not_recreated(tmp_path):
    server = SkillServer([item()])
    adapter = adapter_for(tmp_path, server)
    adapter.ensure_raindrop("101", "https://x.com/i/status/101", "https://archive.invalid")
    server.active.clear()
    with pytest.raises(PilotError, match="previous_receipt_conflicts"):
        adapter.ensure_raindrop("101", "https://x.com/i/status/101", "https://archive.invalid")
    assert not server.posts


def test_lock_and_private_permissions(tmp_path):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    with adapter.store.locked(), pytest.raises(PilotError, match="Another command"):
        adapter.ensure_raindrop("101", "https://x.com/i/status/101", "https://archive.invalid")
    assert not server.calls
    adapter.ensure_raindrop("101", "https://x.com/i/status/101", "https://archive.invalid")
    for path in adapter.store.root.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("status", ["401", "429", "500"])
def test_real_subprocess_boundary_redacts_failure(tmp_path, monkeypatch, status):
    adapter = RaindropAdapter(tmp_path / "ledger", Path(__file__).resolve())
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a,
            1,
            "PRIVATE_BODY",
            json.dumps({"http_status": status, "reset": "123456", "errorMessage": "SECRET"}),
        ),
    )
    with pytest.raises(PilotError) as exc:
        adapter.verify_raindrop_now("101", 1)
    assert exc.value.summary["retryable"]
    assert exc.value.summary["provider"]["http_status"] == status
    assert "SECRET" not in str(exc.value.summary)


def test_offline_inspector_never_claims_freshness():
    assert inspect_inventory(snapshot([item()]), "101")["state"] == "active"
    assert inspect_inventory(snapshot([], [item(cid=-99)]), "101")["state"] == "trash"
    assert inspect_inventory({}, "101")["state"] == "incomplete"


def test_archive_reference_cannot_be_post_body(tmp_path):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    with pytest.raises(PilotError, match="https_url"):
        adapter.ensure_raindrop("101", "https://x.com/i/status/101", "Synthetic post body")
    assert not server.calls


@pytest.mark.parametrize("command", [("list-all",), ("get", 1), ("add-json",)])
def test_adapter_paces_reads_and_writes(tmp_path, monkeypatch, command):
    adapter = RaindropAdapter(tmp_path / "ledger", Path(__file__).resolve())
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, '{"result":true}', "")

    monkeypatch.setattr(subprocess, "run", run)
    adapter._call(*command)
    assert calls[0][2:5] == ["--json", "--delay", "600"]


@pytest.mark.parametrize(
    "link",
    [
        "https://x.com/synthetic-profile",
        "https://twitter.com/i/lists/123",
        "https://x.com/i/spaces/synthetic",
        "https://x.com/u/status/101/analytics",
    ],
)
def test_unsupported_x_links_identify_blocking_item_without_exposing_url(tmp_path, link):
    server = SkillServer([item(rid=44, link=link)])
    adapter = adapter_for(tmp_path, server)
    with pytest.raises(PilotError) as exc:
        adapter.ensure_raindrop("101", "https://x.com/i/status/101", "https://archive.invalid")
    assert exc.value.summary["raindrop_id"] == 44
    assert link not in str(exc.value.summary) and not server.posts
    offline = inspect_inventory(snapshot(server.active), "101")
    assert offline["state"] == "incomplete"
    assert offline["reason"] == "unsupported_x_link"
    assert offline["blocking_raindrop_id"] == 44


def test_cli_rejects_symlinked_state_directory_before_adapter(tmp_path):
    from types import SimpleNamespace

    from x_bookmark_migrator.raindrop_cli import adapter_from_args

    target = tmp_path / "private"
    target.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(PilotError, match="distinct state directories"):
        adapter_from_args(
            SimpleNamespace(
                state_dir=target,
                catalog_dir=tmp_path / "catalog",
                raindrop_ledger_dir=alias,
                raindrop_script=Path(__file__).resolve(),
            )
        )


@pytest.mark.parametrize("alias", [False, True])
def test_ensure_cli_rejects_catalog_ledger_collision_before_adapter(tmp_path, monkeypatch, alias):
    from types import SimpleNamespace
    from unittest.mock import Mock

    from x_bookmark_migrator import raindrop_cli

    target = tmp_path / "private"
    target.mkdir()
    catalog = target
    if alias:
        catalog = tmp_path / "alias"
        catalog.symlink_to(target, target_is_directory=True)
    constructor = Mock(side_effect=AssertionError("Adapter must not be constructed"))
    monkeypatch.setattr(raindrop_cli, "RaindropAdapter", constructor)
    with pytest.raises(PilotError, match="distinct state directories"):
        raindrop_cli.run(
            SimpleNamespace(
                command="raindrop-ensure",
                catalog_dir=catalog,
                raindrop_ledger_dir=target,
                raindrop_script=Path(__file__).resolve(),
            )
        )
    constructor.assert_not_called()


@pytest.mark.parametrize(
    "body", [{"result": False}, {"result": True}, {"result": True, "item": []}, []]
)
@pytest.mark.parametrize("phase", ["before", "after"])
def test_note_update_rejects_invalid_get_envelope_and_retains_evidence(tmp_path, body, phase):
    from unittest.mock import Mock

    adapter = RaindropAdapter(tmp_path / "ledger", Path(__file__).resolve())
    responses = (
        [body]
        if phase == "before"
        else [
            {"result": True, "item": item()},
            {"result": True},
            body,
        ]
    )
    adapter._call = Mock(side_effect=responses)
    with pytest.raises(PilotError, match="response_failed"):
        adapter.replace_note("101", 1, "Updated text")
    assert adapter._call.call_count == (1 if phase == "before" else 3)
    retained = [
        json.loads(path.read_text())["response"]
        for path in adapter.store.root.glob("raindrop-101.get-*.private.json")
    ]
    assert body in retained


def test_explicit_note_update_is_primary_bound_and_note_only(tmp_path):
    from unittest.mock import Mock

    script = tmp_path / "skill.sh"
    script.write_text("synthetic")
    adapter = RaindropAdapter(tmp_path / "ledger", script)
    before = item(pid="101", rid=42)
    before.update(title="Keep title", note="Replace me", tags=["keep"])
    text = "🔥\n" * 200
    after = {**before, "note": text[:250]}
    adapter._call = Mock(
        side_effect=[
            {"result": True, "item": before},
            {"result": True},
            {"result": True, "item": after},
            {"result": True, "item": after},
        ]
    )
    receipt = adapter.replace_note("101", 42, text)
    assert receipt["raindrop_id"] == 42
    assert adapter._call.call_args_list[1].args == ("update-note-json", 42)
    assert adapter._call.call_args_list[1].kwargs == {"payload": {"note": text[:250]}}
    assert adapter._call.call_count == 3  # GET, note-only PUT, verified GET; no third GET.
    adapter._call = Mock(return_value={"result": True, "item": item(pid="102", rid=42)})
    with pytest.raises(PilotError):
        adapter.replace_note("101", 42, text)
    assert adapter._call.call_count == 1
    adapter._call = Mock(
        side_effect=[
            {"result": True, "item": before},
            {"result": True},
            {"result": True, "item": {**after, "tags": []}},
        ]
    )
    with pytest.raises(PilotError):
        adapter.replace_note("101", 42, text)


@pytest.mark.parametrize("text", ["Short post", "🔥\n" * 200])
def test_new_create_uses_only_primary_text_and_preserves_adopted_note(tmp_path, text):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    adapter.ensure_raindrop(
        "101",
        "https://x.com/i/status/101",
        "https://archive.invalid",
        category="123456789",
        post_text=text,
    )
    assert server.posts[0][1]["note"] == text[:250]
    server.active[0]["note"] = "Owner edited this"
    adapter.ensure_raindrop(
        "101", "https://x.com/i/status/101", "https://archive.invalid", post_text="Changed"
    )
    assert len(server.posts) == 1 and server.active[0]["note"] == "Owner edited this"


def test_missing_text_and_changed_recovery_note_never_post(tmp_path):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    with pytest.raises(PilotError):
        adapter.ensure_raindrop(
            "101", "https://x.com/i/status/101", "https://archive.invalid", post_text=None
        )
    assert not server.posts
    assert adapter.store.read("raindrop-101.intent.private.json") is None
    adapter.store.write(
        "raindrop-101.intent.private.json",
        {
            "schema_version": 1,
            "source_post_id": "101",
            "source_post_url": "https://x.com/i/status/101",
            "archive_ref": "https://archive.invalid",
            "attempts": 1,
            "note": "Different original note",
        },
    )
    with pytest.raises(PilotError):
        adapter.ensure_raindrop(
            "101", "https://x.com/i/status/101", "https://archive.invalid", post_text="New text"
        )
    assert not server.posts


def ensure_in_run(run, pid="101"):
    return run.ensure_raindrop(
        pid,
        f"https://x.com/i/status/{pid}",
        "https://archive.invalid",
        post_text="Synthetic primary text",
    )


def saved_gets(adapter, pid="101"):
    return [
        adapter.store.read(path.name)
        for path in sorted(adapter.store.root.glob(f"raindrop-{pid}.get-*.private.json"))
    ]


def test_bounded_inventory_one_baseline_and_fresh_get_per_operation(tmp_path):
    existing = item()
    server = SkillServer([existing])
    adapter = adapter_for(tmp_path, server)
    baseline = snapshot([existing])
    with adapter.bounded_inventory(max_operations=3) as run:
        receipts = [ensure_in_run(run, pid) for pid in ("101", "102", "102")]
        # The baseline remains the actual enumeration, not invented completeness.
        assert adapter.store.read("raindrop-inventory.private.json") == baseline
        assert run.match("102") == {"state": "active", "raindrop_ids": [1001]}
    assert [c[0][0] for c in server.calls] == ["list-all", "get", "add-json", "get", "get"]
    assert server.active[0] == existing
    for receipt in receipts:
        validate_receipt(receipt, receipt["source_post_id"])
        assert "coverage" not in receipt and "inventory_sha256" not in receipt
        assert receipt["baseline_coverage"] == baseline["coverage"]
        assert receipt["baseline_inventory_sha256"] == receipts[0]["baseline_inventory_sha256"]
    with pytest.raises(PilotError, match="inventory_run_closed"):
        ensure_in_run(run)


def test_bounded_crash_requires_new_inventory_then_adopts_without_post(tmp_path):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    server.crash_after_post = True
    with adapter.bounded_inventory(max_operations=3) as run:
        with pytest.raises(RuntimeError):
            ensure_in_run(run)
        assert run.match("101")["state"] == "absent"
        with pytest.raises(PilotError, match="inventory_run_closed"):
            ensure_in_run(run)
    assert adapter.store.read("raindrop-101.intent.private.json")["no_repost"] is True
    fresh = adapter_for(tmp_path, server)
    with fresh.bounded_inventory(max_operations=1) as resumed:
        receipt = ensure_in_run(resumed)
    assert receipt["disposition"] == "adopted"
    assert [c[0][0] for c in server.calls] == ["list-all", "add-json", "list-all", "get"]


@pytest.mark.parametrize("legacy_intent", [False, True])
def test_bounded_uncertain_absence_never_reposts_on_resume(tmp_path, legacy_intent):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    original = adapter._call
    post_attempts = []

    def uncertain(*args, **kwargs):
        if args == ("add-json",):
            post_attempts.append(args)
            raise PilotError("unknown outcome")
        return original(*args, **kwargs)

    adapter._call = uncertain
    if legacy_intent:
        with pytest.raises(PilotError):
            adapter.ensure_raindrop("101", "https://x.com/i/status/101", "https://archive.invalid")
    else:
        with adapter.bounded_inventory(max_operations=1) as run, pytest.raises(PilotError):
            ensure_in_run(run)
    for _ in range(2):
        with adapter.bounded_inventory(max_operations=1) as run:
            with pytest.raises(PilotError, match="uncertain_create_requires_reconciliation"):
                ensure_in_run(run)
    if not legacy_intent:
        with pytest.raises(PilotError, match="uncertain_create_requires_reconciliation"):
            adapter.ensure_raindrop("101", "https://x.com/i/status/101", "https://archive.invalid")
    assert len(post_attempts) == 1
    assert adapter.store.read("raindrop-101.intent.private.json")["attempts"] == 1


@pytest.mark.parametrize("fault", ["duplicates", "trash", "both"])
def test_bounded_baseline_conflicts_block_without_get_or_post(tmp_path, fault):
    server = SkillServer(
        active=[item(), item(rid=2)]
        if fault == "duplicates"
        else [item()]
        if fault == "both"
        else [],
        trash=[item(rid=3, cid=-99)] if fault in ("trash", "both") else [],
    )
    adapter = adapter_for(tmp_path, server)
    with adapter.bounded_inventory(max_operations=1) as run, pytest.raises(PilotError):
        ensure_in_run(run)
    assert server.calls == [(("list-all",), None)]


def test_bounded_previous_receipt_not_recreated(tmp_path):
    server = SkillServer([item()])
    adapter = adapter_for(tmp_path, server)
    with adapter.bounded_inventory(max_operations=1) as run:
        ensure_in_run(run)
    server.active.clear()
    with adapter.bounded_inventory(max_operations=1) as run:
        with pytest.raises(PilotError, match="previous_receipt_conflicts"):
            ensure_in_run(run)
    assert not server.posts


def test_bounded_expired_baseline_never_posts(tmp_path, monkeypatch):
    clock = [0]
    monkeypatch.setattr("x_bookmark_migrator.raindrop.time.monotonic", lambda: clock[0])
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    with adapter.bounded_inventory(max_operations=1, max_age_seconds=10) as run:
        clock[0] = 10
        with pytest.raises(PilotError, match="inventory_baseline_expired"):
            ensure_in_run(run)
    assert server.calls == [(("list-all",), None)]
    assert adapter.store.read("raindrop-101.intent.private.json") is None


def test_bounded_operation_limit_and_ledger_lock(tmp_path):
    server = SkillServer([item()])
    adapter = adapter_for(tmp_path, server)
    with adapter.bounded_inventory(max_operations=1) as run:
        with pytest.raises(PilotError, match="Another command"):
            adapter.ensure_raindrop("101", "https://x.com/i/status/101", "https://archive.invalid")
        ensure_in_run(run)
        with pytest.raises(PilotError, match="inventory_operation_bound_exhausted"):
            ensure_in_run(run)
    assert [c[0][0] for c in server.calls] == ["list-all", "get"]


def test_bounded_checks_age_again_after_durable_intent_before_post(tmp_path, monkeypatch):
    clock = [0]
    monkeypatch.setattr("x_bookmark_migrator.raindrop.time.monotonic", lambda: clock[0])
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    original = adapter.store.write

    def slow_write(name, value):
        original(name, value)
        if name.endswith(".intent.private.json"):
            clock[0] = 10

    monkeypatch.setattr(adapter.store, "write", slow_write)
    with adapter.bounded_inventory(max_operations=1, max_age_seconds=10) as run:
        with pytest.raises(PilotError, match="inventory_baseline_expired"):
            ensure_in_run(run)
    assert not server.posts
    assert adapter.store.read("raindrop-101.intent.private.json")["no_repost"] is True


def test_bounded_rejects_incomplete_baseline_before_yield(tmp_path):
    server = SkillServer()
    server.bad_inventory = snapshot()
    server.bad_inventory["coverage"]["complete"] = False
    adapter = adapter_for(tmp_path, server)
    with pytest.raises(PilotError, match="incomplete_enumeration"):
        with adapter.bounded_inventory(max_operations=1):
            pytest.fail("Incomplete provider inventory must never become a run")
    assert server.calls == [(("list-all",), None)]


def test_bounded_selection_is_frozen_and_rejects_unselected_post(tmp_path):
    server = SkillServer([item()])
    adapter = adapter_for(tmp_path, server)
    selected = ["101"]
    with adapter.bounded_inventory(max_operations=2, post_ids=selected) as run:
        selected.append("102")
        ensure_in_run(run)
        with pytest.raises(PilotError, match="post_outside_inventory_selection"):
            ensure_in_run(run, "102")
    assert [c[0][0] for c in server.calls] == ["list-all", "get"]


@pytest.mark.parametrize("selection", [[], "101", [101]])
def test_bounded_invalid_selection_never_enumerates(tmp_path, selection):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    with pytest.raises(PilotError):
        with adapter.bounded_inventory(max_operations=1, post_ids=selection):
            pytest.fail("Invalid selection must fail before enumeration")
    assert not server.calls


def test_repair_session_routes_existing_adapter_calls_and_reports_counts(tmp_path):
    server = SkillServer([item()])
    adapter = adapter_for(tmp_path, server)
    with adapter.repair_session(["101", "102"], max_seconds=60) as stats:
        for pid in ("101", "102"):
            adapter.ensure_raindrop(pid, f"https://x.com/i/status/{pid}", "https://archive.invalid")
        assert stats is adapter.repair_stats
    assert stats == {
        "inventory_operations": 1,
        "baseline_pages_per_pass": {"active": 1, "trash": 1},
        "baseline_stable_passes": 2,
        "get_attempts": 2,
        "post_attempts": 1,
    }
    assert [c[0][0] for c in server.calls] == ["list-all", "get", "add-json", "get"]
    adapter.ensure_raindrop("101", "https://x.com/i/status/101", "https://archive.invalid")
    assert [c[0][0] for c in server.calls[-2:]] == ["list-all", "get"]
    assert stats["get_attempts"] == 2


def test_repair_session_crash_stats_and_fresh_resume(tmp_path):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    server.crash_after_post = True
    with pytest.raises(RuntimeError):
        with adapter.repair_session(["101"]):
            adapter.ensure_raindrop("101", "https://x.com/i/status/101", "https://archive.invalid")
    assert adapter.repair_stats["post_attempts"] == 1
    assert adapter.repair_stats["get_attempts"] == 0
    with adapter.repair_session(["101"]):
        receipt = adapter.ensure_raindrop(
            "101", "https://x.com/i/status/101", "https://archive.invalid"
        )
    assert receipt["disposition"] == "adopted"
    assert adapter.repair_stats["post_attempts"] == 0
    assert adapter.repair_stats["get_attempts"] == 1
    assert len([c for c in server.calls if c[0] == ("list-all",)]) == 2


@pytest.mark.parametrize(
    "field,value,accepted",
    [
        ("note", "\n Synthetic primary text \t", True),
        ("note", "Synthetic  primary text", False),
        ("note", None, False),
        ("title", "Wrong title", False),
        ("tags", ["other"], False),
        ("link", "https://x.com/i/status/999", False),
    ],
)
def test_created_metadata_verified_from_saved_get_before_index_update(
    tmp_path, field, value, accepted
):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    original = adapter._call

    def changed_get(*args, **kwargs):
        response = original(*args, **kwargs)
        if args[0] == "get":
            response["item"][field] = value
        return response

    adapter._call = changed_get
    with adapter.bounded_inventory(max_operations=1) as run:
        if accepted:
            ensure_in_run(run)
            assert run.match("101")["state"] == "active"
        else:
            with pytest.raises(PilotError):
                ensure_in_run(run)
            assert run.match("101")["state"] == "absent"
            assert adapter.store.read("raindrop-101.receipt.private.json") is None
    saved = saved_gets(adapter)[-1]
    assert saved["response"]["item"][field] == value
    assert len(server.posts) == 1


@pytest.mark.parametrize("note,accepted", [(" \nnew\ntext \t", True), ("new text", False)])
def test_note_update_only_tolerates_edge_whitespace(tmp_path, note, accepted):
    from unittest.mock import Mock

    adapter = RaindropAdapter(tmp_path / "ledger", Path(__file__).resolve())
    before = item()
    after = {**before, "note": note}
    adapter._call = Mock(
        side_effect=[
            {"result": True, "item": before},
            {"result": True},
            {"result": True, "item": after},
            {"result": True, "item": after},
        ]
    )
    if accepted:
        adapter.replace_note("101", 1, "new\ntext")
    else:
        with pytest.raises(PilotError, match="note_update_readback_mismatch"):
            adapter.replace_note("101", 1, "new\ntext")
    assert any(saved["response"]["item"] == after for saved in saved_gets(adapter))


@pytest.mark.parametrize("selection", [["101", "101"], [str(i) for i in range(101)]])
def test_repair_session_rejects_duplicates_and_oversized_selection(tmp_path, selection):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    with pytest.raises(PilotError):
        with adapter.repair_session(selection):
            pytest.fail("Invalid selection must fail before inventory")
    assert not server.calls


def test_bounded_operation_cap_never_enumerates(tmp_path):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    with pytest.raises(PilotError):
        with adapter.bounded_inventory(max_operations=101):
            pytest.fail("Oversized operation bound must fail before inventory")
    assert not server.calls


def test_failed_metadata_evidence_survives_successful_resume_with_safe_diagnostics(tmp_path):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    original = adapter._call
    private_note = "Synthetic  primary text"

    def content_changed(*args, **kwargs):
        response = original(*args, **kwargs)
        if args[0] == "get":
            response["item"]["note"] = private_note
        return response

    adapter._call = content_changed
    with pytest.raises(PilotError) as exc:
        with adapter.repair_session(["101"]):
            ensure_in_run(adapter)
    diagnostics = exc.value.summary["metadata_mismatch"]
    assert diagnostics == {
        "note": True,
        "title": False,
        "tags": False,
        "note_actual_length": len(private_note),
        "note_expected_length": len("Synthetic primary text"),
        "note_equal_after_strip": False,
    }
    assert private_note not in json.dumps(exc.value.summary)
    prior = saved_gets(adapter)
    assert len(prior) == 1
    assert prior[0]["response"]["item"]["note"] == private_note
    adapter._call = original
    with adapter.repair_session(["101"]):
        receipt = ensure_in_run(adapter)
    retained = saved_gets(adapter)
    assert len(retained) == 2
    assert prior[0] in retained
    assert any(
        digest(encode(entry["response"])) == receipt["response_sha256"] for entry in retained
    )
    assert len(server.posts) == 1


@pytest.mark.parametrize("bounded", [False, True])
def test_own_unverified_create_resume_checks_metadata_until_receipt_then_preserves_edits(
    tmp_path, bounded
):
    from contextlib import nullcontext

    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    original = adapter._call

    def changed_create(*args, **kwargs):
        response = original(*args, **kwargs)
        if args == ("add-json",):
            server.active[0]["note"] = "Different content"
        return response

    adapter._call = changed_create
    for _ in range(2):
        with pytest.raises(PilotError, match="create_metadata_readback_mismatch"):
            with adapter.repair_session(["101"]) if bounded else nullcontext():
                ensure_in_run(adapter)
        assert adapter.store.read("raindrop-101.receipt.private.json") is None
    assert len(server.posts) == 1
    server.active[0]["note"] = " \nSynthetic primary text\t"
    with adapter.repair_session(["101"]) if bounded else nullcontext():
        receipt = ensure_in_run(adapter)
    validate_receipt(receipt, "101")
    server.active[0].update(note="Later owner edit", title="Owner title", tags=["owner"])
    with adapter.repair_session(["101"]) if bounded else nullcontext():
        receipt = ensure_in_run(adapter)
    validate_receipt(receipt, "101")
    assert server.active[0]["note"] == "Later owner edit"
    assert len(server.posts) == 1
    assert len(saved_gets(adapter)) == 4


@pytest.mark.parametrize("change", ["note", "identity", "archive"])
def test_own_pending_metadata_resume_requires_original_intent_binding(tmp_path, change):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    server.bad_get = {"result": True, "item": item(rid=1000)}
    with pytest.raises(PilotError, match="create_metadata_readback_mismatch"):
        with adapter.repair_session(["101"]):
            ensure_in_run(adapter)
    server.bad_get = None
    intent = adapter.store.read("raindrop-101.intent.private.json")
    key, value = {
        "note": ("note", "Different intended content"),
        "identity": ("source_post_id", "999"),
        "archive": ("archive_ref", "https://different.invalid"),
    }[change]
    intent[key] = value
    adapter.store.write("raindrop-101.intent.private.json", intent)
    with pytest.raises(PilotError, match="intent_conflict"):
        with adapter.repair_session(["101"]):
            ensure_in_run(adapter)
    assert len(server.posts) == 1


def test_existing_created_receipt_preserves_later_owner_edits(tmp_path):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    receipt = ensure_in_run(adapter)
    validate_receipt(receipt, "101")
    server.active[0]["note"] = "Different content"
    with adapter.repair_session(["101"]):
        receipt = ensure_in_run(adapter)
    validate_receipt(receipt, "101")
    assert server.active[0]["note"] == "Different content"
    assert len(server.posts) == 1


def test_explicit_partial_recheck_does_not_trust_old_identity_receipt(tmp_path):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    original = ensure_in_run(adapter)
    assert original["disposition"] == "created"
    saved_receipt = adapter.store.path("raindrop-101.receipt.private.json").read_bytes()
    server.active[0]["note"] = "Different content after identity-only receipt"
    for _ in range(2):
        with pytest.raises(PilotError, match="create_metadata_readback_mismatch"):
            with adapter.repair_session(["101"], verify_created_ids=["101"]):
                ensure_in_run(adapter)
        assert adapter.store.path("raindrop-101.receipt.private.json").read_bytes() == saved_receipt
    server.active[0]["note"] = "  Synthetic primary text\n"
    with adapter.repair_session(["101"], verify_created_ids=["101"]):
        proof = ensure_in_run(adapter)
    assert proof["disposition"] == "adopted"
    assert len(server.posts) == 1


@pytest.mark.parametrize("active", [False, True])
def test_explicit_partial_recheck_requires_saved_create_and_never_creates(tmp_path, active):
    server = SkillServer(active=[item()] if active else [])
    adapter = adapter_for(tmp_path, server)
    with pytest.raises(PilotError, match="create_result_required|created_item_missing"):
        with adapter.repair_session(["101"], verify_created_ids=["101"]):
            ensure_in_run(adapter)
    assert not server.posts


def test_partial_recheck_scope_must_be_selected_before_inventory(tmp_path):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    with pytest.raises(PilotError, match="metadata_check_outside_selection"):
        with adapter.repair_session(["101"], verify_created_ids=["102"]):
            pass
    assert not server.calls


def test_pending_create_result_must_match_selected_active_id(tmp_path):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    server.bad_get = {"result": True, "item": item(rid=1000)}
    with pytest.raises(PilotError, match="create_metadata_readback_mismatch"):
        with adapter.repair_session(["101"]):
            ensure_in_run(adapter)
    server.bad_get = None
    server.active[0]["_id"] = 2000
    with pytest.raises(PilotError, match="create_result_conflict"):
        with adapter.repair_session(["101"]):
            ensure_in_run(adapter)
    assert len(saved_gets(adapter)) == 1
    assert len(server.posts) == 1


def test_unknown_post_without_saved_response_adopts_identity_preserving_note(tmp_path):
    server = SkillServer()
    adapter = adapter_for(tmp_path, server)
    server.crash_after_post = True
    with pytest.raises(RuntimeError):
        with adapter.repair_session(["101"]):
            ensure_in_run(adapter)
    server.active[0]["note"] = "Owner content"
    with adapter.repair_session(["101"]):
        receipt = ensure_in_run(adapter)
    assert receipt["disposition"] == "adopted"
    assert server.active[0]["note"] == "Owner content"
    assert len(server.posts) == 1
