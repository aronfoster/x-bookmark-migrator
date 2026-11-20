"""Synthetic skill boundary, durable recovery and shared FOS-216 contracts."""

import copy
import json
import subprocess
from pathlib import Path

import pytest

from x_bookmark_migrator.core import PilotError
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
