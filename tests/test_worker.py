"""Offline fault injection through real archive, catalog, and deletion transactions."""

import copy
import json
import sys
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_archive import media_http
from test_unbookmark import migration as migration
from test_unbookmark import storage as storage

from x_bookmark_migrator import cli, worker_cli
from x_bookmark_migrator.catalog import annotate, read_catalog
from x_bookmark_migrator.core import WRITE_SCOPES, PilotError, Store
from x_bookmark_migrator.drive import digest, encode
from x_bookmark_migrator.inventory import Inventory
from x_bookmark_migrator.worker import (
    DEFER,
    GATES,
    JOURNAL,
    OPERATIONS,
    ROTATION,
    CumulativeBudget,
    Deferral,
    GuardedHTTP,
    Worker,
    WorkerClient,
    merge_publication,
    removal_history,
    snapshot,
)


def config(tmp_path):
    return {
        "schema_version": 1,
        "owner_id": "100",
        "max_items": 3,
        "max_calls": 50,
        "max_batches": 20,
        "catalog_dir": str(tmp_path / "catalog"),
        "restore_dir": str(tmp_path / "worker-restored"),
        "raindrop_ledger_dir": str(tmp_path / "raindrop"),
        "reservation_usd": dict.fromkeys(OPERATIONS, ".01"),
        "run_cap_usd": "2",
        "drive_folder_id": "root",
        "gates": dict.fromkeys(GATES, True),
    }


def make_worker(migration, tmp_path, **updates):
    store, drive, _, source, client, _, _, _ = migration
    cfg = config(tmp_path)
    cfg.update(updates)
    gate = Deferral(store)
    client.http = GuardedHTTP(
        client.http.http if isinstance(client.http, GuardedHTTP) else client.http, gate, "x"
    )
    budget = CumulativeBudget(store, cfg["reservation_usd"], cfg["run_cap_usd"], cfg["max_calls"])
    return Worker(
        store,
        source.catalog,
        tmp_path / "worker-restored",
        client,
        drive,
        httpx.Client(transport=httpx.MockTransport(media_http)),
        source.adapter,
        budget,
        cfg,
    )


def test_real_queue_restores_publishes_verifies_and_drains_bounded(migration, tmp_path):
    w = make_worker(migration, tmp_path, max_items=2)
    result = w.run(allow_delete=True)
    assert result["stop_reason"] is None
    assert result["run_counts"]["delete_attempts"] == 2
    assert result["confirmed_removed"] == 2
    assert len(migration[3].deletes) == 2
    assert w.state["items"]["103"]["state"] == "queued"
    # Same scheduled tick and restarted process perform no provider work.
    before = len(migration[3].calls), len(migration[2].calls)
    assert make_worker(migration, tmp_path).run(allow_delete=True)["not_due"]
    assert before == (len(migration[3].calls), len(migration[2].calls))
    restarted = make_worker(migration, tmp_path)
    restarted.state["next_daily"] = 0
    assert restarted.run(allow_delete=True)["confirmed_removed"] == 3
    assert len(migration[3].deletes) == 3


def test_archive_only_cannot_create_raindrop_or_delete(migration, tmp_path):
    w = make_worker(migration, tmp_path)
    w.raindrop = None
    result = w.run(allow_delete=False)
    assert not result["stop_reason"] and result["remaining_reachable_queued"] == 3
    assert not migration[3].calls


def test_uncertain_delete_and_resaved_confirmed_id_never_repeat(migration, tmp_path):
    source = migration[3]
    source.mutate_then_timeout = True
    first = make_worker(migration, tmp_path).run(allow_delete=True)
    assert first["outcome_unknown"] == 1
    assert len(source.deletes) == 1
    source.mutate_then_timeout = False
    source.ids.insert(0, "101")  # Could be a re-save; cannot reuse prior generation.
    migration[0].write(DEFER, {"until": 0})
    next_worker = make_worker(migration, tmp_path)
    next_worker.run(allow_delete=True)
    assert [r.url.path.rsplit("/", 1)[-1] for r in source.deletes].count("101") == 1


@pytest.mark.parametrize("method", ["GET", "DELETE", "POST"])
def test_rate_reset_is_durable_before_response_and_blocks_every_request(tmp_path, method):
    store = Store(tmp_path / "state")
    now = [1000]
    gate = Deferral(store, lambda: now[0])
    calls = []

    def handle(req):
        calls.append(req)
        return httpx.Response(429, json={}, headers={"x-rate-limit-reset": "1600"})

    transport = GuardedHTTP(httpx.Client(transport=httpx.MockTransport(handle)), gate, "x")
    transport.request(method, "https://api.x.com/2/test")
    assert store.read(DEFER)["until"] == 1601
    for clock in (1000, 1599, 1600):
        now[0] = clock
        with pytest.raises(PilotError, match="deferred"):
            transport.get("https://api.x.com/2/test")
    assert len(calls) == 1
    now[0] = 1601
    transport.get("https://api.x.com/2/test")
    assert len(calls) == 2


def test_429_after_success_never_redeletes_and_wakes_after_reset(migration, tmp_path):
    migration[3].after_failure = True
    result = make_worker(migration, tmp_path).run(allow_delete=True)
    assert result["confirmed_removed"] == 1
    assert result["deferred"]
    calls = len(migration[3].calls), len(migration[2].calls)
    assert make_worker(migration, tmp_path).run(allow_delete=True)["deferred"]
    assert calls == (len(migration[3].calls), len(migration[2].calls))
    assert len(migration[3].deletes) == 1


def test_all_months_and_actual_observation_remain_conservative(tmp_path):
    store = Store(tmp_path / "state")
    store.write("budget.private.json", {"2026-01": "14.15", "2026-02": "5.205"})
    budget = CumulativeBudget(store, dict.fromkeys(OPERATIONS, ".01"), "1", 10, actual=".63")
    budget.reserve_operation("delete")
    with pytest.raises(PilotError, match="cap"):
        budget.reserve_operation("main")
    assert budget.used == Decimal(".01")
    assert store.read("budget.private.json")["2026-01"] == "14.15"


@pytest.mark.parametrize("value", ["NaN", "-1", "Infinity", "", None])
def test_invalid_ledger_cannot_reset_budget(tmp_path, value):
    store = Store(tmp_path / "state")
    store.write("budget.private.json", {"2026-01": value})
    with pytest.raises(PilotError):
        CumulativeBudget(store, dict.fromkeys(OPERATIONS, ".01"), "1", 10)


@pytest.mark.parametrize("crash", ["before_send", "after_response", "after_token_write"])
def test_refresh_rotation_crashes_never_replay_old_token(tmp_path, monkeypatch, crash):
    store = Store(tmp_path / "state")
    prior = {
        "access_token": "old",
        "refresh_token": "old-refresh",
        "client_id": "app",
        "expires_at": 0,
        "scope": " ".join(WRITE_SCOPES),
    }
    store.write("tokens.private.json", prior)
    calls = []

    def handle(req):
        calls.append(req)
        if crash == "before_send":
            raise httpx.ReadTimeout("synthetic", request=req)
        return httpx.Response(
            200,
            json={
                "access_token": "new",
                "refresh_token": "new-refresh",
                "scope": prior["scope"],
                "expires_in": 7200,
                "token_type": "bearer",
            },
        )

    write = store.write

    def interrupted(name, value):
        if name == "tokens.private.json" and crash == "after_response":
            raise OSError("fault before atomic token write")
        if name == ROTATION and value is None and crash == "after_token_write":
            raise OSError("fault after atomic token write")
        write(name, value)

    monkeypatch.setattr(store, "write", interrupted)
    client = WorkerClient(store, httpx.Client(transport=httpx.MockTransport(handle)), "app")
    with pytest.raises((OSError, httpx.HTTPError)):
        client.access_token()
    monkeypatch.setattr(store, "write", write)
    if crash == "after_token_write":
        assert client.access_token() == "new"
    else:
        with pytest.raises(PilotError) as exc:
            client.access_token()
        assert exc.value.code == "owner_reconsent_needed"
    assert len(calls) == 1


def test_invalid_grant_stops_without_loop(tmp_path):
    store = Store(tmp_path / "state")
    store.write(
        "tokens.private.json",
        {
            "access_token": "old",
            "refresh_token": "old",
            "client_id": "app",
            "expires_at": 0,
            "scope": " ".join(WRITE_SCOPES),
        },
    )
    calls = []

    def reject(req):
        calls.append(req)
        return httpx.Response(400, json={"error": "invalid_grant"})

    client = WorkerClient(store, httpx.Client(transport=httpx.MockTransport(reject)), "app")
    for _ in range(2):
        with pytest.raises(PilotError) as exc:
            client.access_token()
        assert exc.value.code == "owner_reconsent_needed"
    assert len(calls) == 1


def test_shared_lock_rejects_manual_or_scheduler_overlap(migration, tmp_path):
    store = migration[0]
    cfg = config(tmp_path)
    cfgpath = tmp_path / "config.json"
    cfgpath.write_text(json.dumps(cfg))
    cfgpath.chmod(0o600)
    args = SimpleNamespace(
        dry_run=True,
        report_only=False,
        once=False,
        live=False,
        allow_delete=False,
        scheduled=False,
        config=cfgpath,
        state_dir=store.root,
    )
    with store.locked(), pytest.raises(PilotError, match="Another command"):
        worker_cli.run(args)


def test_offline_modes_never_construct_http_or_adapter(migration, tmp_path, monkeypatch):
    cfg = config(tmp_path)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg))
    path.chmod(0o600)

    def forbidden(*args, **kwargs):
        pytest.fail("offline mode constructed network client")

    monkeypatch.setattr(worker_cli.httpx, "Client", forbidden)
    monkeypatch.setattr(worker_cli, "RaindropAdapter", forbidden)
    for mode in ("--dry-run", "--report-only"):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "x-migrator",
                "--state-dir",
                str(migration[0].root),
                "worker",
                "--config",
                str(path),
                mode,
            ],
        )
        assert cli.main() == 0


@pytest.mark.parametrize("mode", ["--dry-run", "--report-only"])
def test_offline_live_conflicts_fail_before_calls(migration, tmp_path, monkeypatch, mode):
    monkeypatch.setattr(
        sys, "argv", ["x-migrator", "worker", "--config", "/missing", mode, "--live"]
    )
    assert cli.main() == 1


def test_catalog_merge_keeps_all_versions_and_owner_annotations(migration, tmp_path):
    catalog = migration[3].catalog
    records = read_catalog(catalog)
    asset = records[0]["versions"][0]["media"][0]["asset_id"]
    annotate(catalog, records, asset, visibility="hidden", description="Owner memory")
    before = catalog.path("annotations.private.json").read_bytes()
    incoming = copy.deepcopy(read_catalog(catalog)[:1])
    merge_publication(catalog, incoming)
    result = read_catalog(catalog)
    assert len(result) == 3
    assert catalog.path("annotations.private.json").read_bytes() == before
    assert result[0]["versions"][0]["media"][0]["browse_visibility"] == "hidden"
    w = make_worker(migration, tmp_path)
    assert w.run(allow_delete=True)["stop_reason"] is None
    assert catalog.path("annotations.private.json").read_bytes() == before


def test_corrupt_external_archive_prevents_every_delete(migration, tmp_path):
    receipt = migration[0].read(migration[5]["private_receipt"])
    migration[2].corrupt_id = receipt["manifest"]["file_id"]
    result = make_worker(migration, tmp_path).run(allow_delete=True)
    assert result["stop_reason"]
    assert not migration[3].deletes


def test_stalled_window_and_unknown_category_are_honest(migration, tmp_path):
    w = make_worker(migration, tmp_path)
    inv = Inventory.load(migration[0])
    for pid in inv.state["posts"]:
        w.state["items"][pid] = {"state": "blocked"}
    w.coverage(inv)
    folders = [s for k, s in w.state["streams"].items() if k.startswith("folder:")]
    assert all(s["window_exposure_status"] == "stalled_on_blocked_window" for s in folders)
    record = copy.deepcopy(next(iter(inv.state["posts"].values())))
    record["folder_ids"] = []
    assert inv.membership(record) == "membership_unknown"


def test_25_32_43_checkpoint_uses_all_helper_attempts_without_remote_calls(tmp_path):
    store = Store(tmp_path / "state")
    rows = [{"id": str(i), "result": "removed"} for i in range(100, 125)]
    source = store.path("helper.private.json")
    source.write_bytes(encode(rows))
    source.chmod(0o600)
    descriptor = {
        "schema_version": 1,
        "format_description": "Synthetic checkpoint receipts",
        "all_intents_included": True,
        "records_pointer": "",
        "post_id_pointer": "/id",
        "outcome_pointer": "/result",
        "outcome_map": {"removed": "confirmed_removed"},
        "files": [{"path": source.name, "sha256": digest(source.read_bytes()), "record_count": 25}],
    }
    store.write("descriptor.private.json", descriptor)
    store.write(
        JOURNAL,
        {
            "schema_version": 1,
            "batches": {},
            "streams": {},
            "items": {
                str(i): {
                    "state": "held_previous_attempt"
                    if i < 125
                    else "queued"
                    if i < 157
                    else "blocked"
                }
                for i in range(100, 200)
            },
        },
    )
    report = snapshot(store, [store.path("descriptor.private.json")])
    assert report["known_records"] == 100 and report["confirmed_removed"] == 25
    assert report["states"] == {"held_previous_attempt": 25, "queued": 32, "blocked": 43}
    assert len(removal_history(store, [store.path("descriptor.private.json")])) == 25
    assert "Synthetic" not in json.dumps(report)


@pytest.mark.parametrize("suffix", ["intent", "response", "result", "window"])
def test_crash_at_each_removal_receipt_boundary_never_replays(
    migration, tmp_path, monkeypatch, suffix
):
    store = migration[0]
    create = store.create

    def crash(name, value):
        if name.endswith("." + suffix + ".private.json"):
            # Crash after durable publication: receipt must remain authoritative.
            create(name, value)
            raise OSError("synthetic process interruption")
        return create(name, value)

    monkeypatch.setattr(store, "create", crash)
    assert (
        make_worker(migration, tmp_path, max_items=1).run(allow_delete=True)["stop_reason"]
        == "worker_failure"
    )
    monkeypatch.setattr(store, "create", create)
    before = len([r for r in migration[3].deletes if r.url.path.endswith("/101")])
    make_worker(migration, tmp_path, max_items=1).run(allow_delete=True)
    assert len([r for r in migration[3].deletes if r.url.path.endswith("/101")]) == before


def test_raindrop_429_saved_reset_stops_all_providers(migration, tmp_path, monkeypatch):
    from x_bookmark_migrator.raindrop import blocked

    w = make_worker(migration, tmp_path)
    reset = int(time.time()) + 7200

    def rate(*args, **kwargs):
        raise blocked(
            "provider_request_failed",
            retryable=True,
            context={"http_status": "429", "reset": str(reset)},
        )

    monkeypatch.setattr(w.raindrop, "ensure_raindrop", rate)
    result = w.run(allow_delete=True)
    assert result["stop_reason"] == "blocked_raindrop_retryable"
    assert migration[0].read(DEFER)["until"] == reset + 1
    before = len(migration[2].calls), len(migration[3].calls)
    assert make_worker(migration, tmp_path).run(allow_delete=True)["deferred"]
    assert before == (len(migration[2].calls), len(migration[3].calls))


def test_fresh_daily_inventory_archive_create_get_delete_end_to_end(migration, tmp_path):
    from test_archive import includes, post

    source = migration[3]
    original = source.handle

    def handle(req):
        if req.url.path == "/2/users/100/bookmarks":
            source.calls.append(req)
            return httpx.Response(
                200,
                json={"data": [post("104")], "includes": includes([]), "meta": {"result_count": 1}},
            )
        if req.url.path == "/2/tweets":
            source.calls.append(req)
            ids = req.url.params["ids"].split(",")
            return httpx.Response(
                200, json={"data": [post(pid) for pid in ids], "includes": includes([])}
            )
        return original(req)

    migration[4].http = httpx.Client(transport=httpx.MockTransport(handle))
    w = make_worker(migration, tmp_path, max_items=3)
    assert w.run(allow_delete=True)["confirmed_removed"] == 3
    fresh = make_worker(migration, tmp_path, max_items=1)
    fresh.state["next_daily"] = 0
    result = fresh.run(allow_delete=True)
    assert result["stop_reason"] == "no_new_ids_observed"
    assert result["confirmed_removed"] == 4
    assert len(read_catalog(source.catalog)) == 4
    assert len(source.deletes) == 4
    assert source.deletes[-1].url.path.endswith("/104")
    assert all("pagination_token" not in req.url.params for req in source.calls)


def test_permanent_blocked_ids_not_hydrated_again(migration, tmp_path):
    from test_archive import includes, post

    source = migration[3]
    original = source.handle

    def handle(req):
        if req.url.path == "/2/users/100/bookmarks":
            source.calls.append(req)
            return httpx.Response(
                200,
                json={"data": [post("101")], "includes": includes([]), "meta": {"result_count": 1}},
            )
        assert req.url.path != "/2/tweets", "known blockers must not be rehydrated"
        return original(req)

    migration[4].http = httpx.Client(transport=httpx.MockTransport(handle))
    w = make_worker(migration, tmp_path)
    w.bootstrap(Inventory.load(migration[0]))
    for pid in ("101", "102", "103"):
        w.state["items"][pid]["state"] = "blocked"
    w.save()
    result = w.run(allow_delete=True)
    assert result["stop_reason"] is None
    assert not source.deletes


def test_restored_file_corruption_is_not_overwritten(migration, tmp_path):
    w = make_worker(migration, tmp_path)
    inv = Inventory.load(migration[0])
    w.bootstrap(inv)
    item = w.state["items"]["101"]
    path = Path(item["restore_dir"]) / "101.json"
    path.write_text("tampered")
    with pytest.raises(PilotError, match="differ"):
        w.adopt(item["receipt"], inv)
    assert path.read_text() == "tampered"
    assert not migration[3].deletes


def test_definite_delete_429_retries_only_after_reset_with_new_verified_intent(migration, tmp_path):
    source = migration[3]
    source.status = 429
    w = make_worker(migration, tmp_path, max_items=1)
    assert w.run(allow_delete=True)["stop_reason"] == "unbookmark_rejected"
    assert len(source.deletes) == 1
    assert make_worker(migration, tmp_path).run(allow_delete=True)["deferred"]
    migration[0].write(DEFER, {"until": 0})  # Synthetic clock advance, not a rollout instruction.
    source.status = 200
    retry = make_worker(migration, tmp_path, max_items=1)
    retry.state["next_daily"] = 0  # Clock advance includes the saved due deadline.
    result = retry.run(allow_delete=True)
    assert result["confirmed_removed"] == 1
    assert len(source.deletes) == 2
    attempts = list(migration[0].root.glob("unbookmark-*.intent.private.json"))
    assert len(attempts) == 2
    assert all(migration[0].read(p.name)["discovery_gate"]["kind"] == "raindrop" for p in attempts)


def test_republishing_shared_batch_does_not_clear_permanent_item_hold(migration, tmp_path):
    w = make_worker(migration, tmp_path)
    inv = Inventory.load(migration[0])
    w.bootstrap(inv)
    w.state["items"]["101"].update(state="blocked", failure_reason="raindrop_conflict")
    w.adopt(migration[5]["private_receipt"], inv)
    assert w.state["items"]["101"]["state"] == "blocked"
    assert w.state["items"]["102"]["state"] == "queued"


@pytest.mark.parametrize(
    "failure",
    [
        OSError("private path"),
        ValueError("secret"),
        KeyError("token"),
        StopIteration(),
        PilotError("Drive grant revoked", code="drive_failure"),
    ],
)
def test_failure_after_paid_discovery_defers_every_scheduled_tick(
    migration, tmp_path, monkeypatch, failure
):
    w = make_worker(migration, tmp_path)
    w.bootstrap(Inventory.load(migration[0]))
    for item in w.state["items"].values():
        item["state"] = "blocked"
    w.save()
    requests = []

    def discover():
        w.budget.reserve_operation("main")
        requests.append("paid-read")
        raise failure

    monkeypatch.setattr(w, "discover", discover)
    before = time.time()
    result = w.run()
    assert result["stop_reason"] in ("worker_failure", "drive_failure")
    assert w.store.read(JOURNAL)["next_daily"] >= before + 86400
    assert "secret" not in json.dumps(result) and "private path" not in json.dumps(result)
    for offset in (300, 900, 3600, 86000):
        restarted = make_worker(migration, tmp_path)
        restarted.clock = lambda offset=offset: before + offset
        restarted.gate.clock = restarted.clock
        assert restarted.run()["deferred"]
    assert requests == ["paid-read"]


def test_hard_crash_and_full_state_after_paid_work_keep_prepaid_daily_deadline(
    migration, tmp_path, monkeypatch
):
    w = make_worker(migration, tmp_path)
    deadline = []

    def crash(*args):
        deadline.append(w.store.read(JOURNAL)["next_daily"])
        raise KeyboardInterrupt  # Simulates process death, intentionally not caught.

    monkeypatch.setattr(w, "bootstrap", crash)
    with pytest.raises(KeyboardInterrupt):
        w.run()
    assert deadline[0] > time.time() + 86000
    assert make_worker(migration, tmp_path).run()["not_due"]


def test_raindrop_retry_deadline_survives_shared_batch_and_skips_only_delayed_item(
    migration, tmp_path
):
    w = make_worker(migration, tmp_path, max_items=1)
    inv = Inventory.load(migration[0])
    w.bootstrap(inv)
    item = w.state["items"]["101"]
    item.update(state="queued", retry_at=time.time() + 86400)
    w.save()
    w.adopt(item["receipt"], inv)
    assert not w.queued("101", w.state["items"]["101"])
    w.drain(inv)
    assert migration[3].deletes[-1].url.path.endswith("/102")
    assert w.state["items"]["101"]["retry_at"] > time.time()


def test_reader_group_projection_preserves_private_state_and_real_delete_gate(migration, tmp_path):
    import os

    from x_bookmark_migrator.publication import PublicationStore

    w = make_worker(migration, tmp_path, max_items=1)
    w.catalog.root.chmod(0o2750)
    w.catalog = PublicationStore(w.catalog.root)
    w.restore_root.mkdir(mode=0o700)
    w.restore_root.chmod(0o2750)
    # Local fixture already contains a private catalog; publication replaces it with reader bytes.
    result = w.run(allow_delete=True)
    assert result["confirmed_removed"] == 1
    for name in ("index.html", "catalog.jsonl", "source-links.jsonl"):
        st = w.catalog.path(name).stat()
        assert st.st_mode & 0o777 == 0o640 and st.st_gid == os.getgid()
    assert w.store.root.stat().st_mode & 0o777 == 0o700
    assert w.store.path(JOURNAL).stat().st_mode & 0o777 == 0o600
    root = Path(w.state["items"]["101"]["restore_dir"])
    assert root.stat().st_mode & 0o7777 == 0o2750
    assert (root / "101.json").stat().st_mode & 0o777 == 0o640
    assert w.catalog.path("lock").stat().st_mode & 0o777 == 0o600
    assert len(read_catalog(w.catalog)) == 3


def test_reader_group_never_allows_group_write_or_private_store_access(tmp_path):
    from x_bookmark_migrator.publication import PublicationStore, publication_bytes

    root = tmp_path / "publications"
    root.mkdir(mode=0o700)
    root.chmod(0o2750)
    PublicationStore(root)
    with pytest.raises(PilotError):
        Store(root)
    path = root / "catalog.jsonl"
    path.write_text("synthetic")
    path.chmod(0o660)
    with pytest.raises(PilotError):
        publication_bytes(path)
    root.chmod(0o2770)
    with pytest.raises(PilotError):
        PublicationStore(root)


def test_pinned_adapter_rechecks_bytes_and_never_sources_home(tmp_path, monkeypatch):
    from x_bookmark_migrator.raindrop import RaindropAdapter

    script = tmp_path / "skill.sh"
    script.write_text("printf '{\"result\":true}\\n'\n")
    helper = tmp_path / "list_all.py"
    helper.write_text("# synthetic helper\n")
    adapter = RaindropAdapter(
        tmp_path / "ledger",
        script,
        script_sha256=digest(script.read_bytes()),
        helper_sha256=digest(helper.read_bytes()),
    )
    monkeypatch.delenv("RAINDROP_TOKEN", raising=False)
    with pytest.raises(PilotError, match="explicit_environment_token_required"):
        adapter._call("get", 1)
    monkeypatch.setenv("RAINDROP_TOKEN", "synthetic-token")
    poison = tmp_path / "poison.sh"
    marker = tmp_path / "executed"
    poison.write_text(f'touch "{marker}"\n')
    monkeypatch.setenv("BASH_ENV", str(poison))
    assert adapter._call("get", 1) == {"result": True}
    assert not marker.exists()
    script.write_text("exit 99\n")
    with pytest.raises(PilotError, match="script_pin_mismatch"):
        adapter._call("get", 1)
    assert not marker.exists()


def test_all_delayed_queue_preserves_inventory_without_new_discovery(
    migration, tmp_path, monkeypatch
):
    w = make_worker(migration, tmp_path)
    inv = Inventory.load(migration[0])
    w.bootstrap(inv)
    for item in w.state["items"].values():
        item["retry_at"] = time.time() + 86400
    w.save()
    monkeypatch.setattr(w, "discover", lambda: pytest.fail("must not invalidate queued inventory"))
    before = len(migration[2].calls), len(migration[3].calls)
    assert w.run(allow_delete=True)["not_due"]
    assert before == (len(migration[2].calls), len(migration[3].calls))
    assert Inventory.load(migration[0]).name == inv.name


def test_raindrop_missing_reset_waits_day_and_reset_sets_item_deadline(
    migration, tmp_path, monkeypatch
):
    from x_bookmark_migrator.raindrop import blocked

    w = make_worker(migration, tmp_path)
    before = time.time()

    def fail(*args, **kwargs):
        raise blocked("transport", retryable=True)

    monkeypatch.setattr(w.raindrop, "ensure_raindrop", fail)
    assert w.run(allow_delete=True)["stop_reason"] == "blocked_raindrop_retryable"
    assert w.state["items"]["101"]["retry_at"] >= before + 86400
    assert w.store.read(DEFER)["until"] >= before + 86400


def test_drive_failure_after_real_inventory_does_not_repeat_paid_reads(
    migration, tmp_path, monkeypatch
):
    from test_archive import includes, post

    from x_bookmark_migrator import worker

    source = migration[3]
    original = source.handle

    def handle(req):
        if req.url.path == "/2/users/100/bookmarks":
            source.calls.append(req)
            return httpx.Response(
                200,
                json={"data": [post("104")], "includes": includes([]), "meta": {"result_count": 1}},
            )
        if req.url.path == "/2/tweets":
            source.calls.append(req)
            return httpx.Response(
                200,
                json={
                    "data": [post(pid) for pid in req.url.params["ids"].split(",")],
                    "includes": includes([]),
                },
            )
        return original(req)

    migration[4].http = httpx.Client(transport=httpx.MockTransport(handle))
    w = make_worker(migration, tmp_path)
    w.bootstrap(Inventory.load(migration[0]))
    for item in w.state["items"].values():
        item["state"] = "blocked"
    w.save()

    def archive_failure(*args, **kwargs):
        assert source.calls  # Real inventory requests already happened.
        raise PilotError("Synthetic revoked Drive grant", code="drive_failure")

    monkeypatch.setattr(worker, "archive_batch", archive_failure)
    assert w.run()["stop_reason"] == "drive_failure"
    assert w.budget.used > 0
    before = len(source.calls), len(migration[2].calls)
    assert make_worker(migration, tmp_path).run()["deferred"]
    assert before == (len(source.calls), len(migration[2].calls))


@pytest.mark.parametrize("change", ["modify", "remove", "symlink", "missing_pin"])
def test_pinned_enumerator_change_blocks_before_any_subprocess(tmp_path, monkeypatch, change):
    from x_bookmark_migrator import raindrop

    script = tmp_path / "raindrop.sh"
    helper = tmp_path / "list_all.py"
    script.write_text("# synthetic shell\n")
    helper.write_text("# synthetic enumerator\n")
    adapter = raindrop.RaindropAdapter(
        tmp_path / "ledger",
        script,
        script_sha256=digest(script.read_bytes()),
        helper_sha256=digest(helper.read_bytes()),
    )
    monkeypatch.setenv("RAINDROP_TOKEN", "synthetic")
    monkeypatch.setattr(raindrop.subprocess, "run", lambda *a, **k: pytest.fail("must not execute"))
    if change == "modify":
        helper.write_text("# changed enumerator\n")
    elif change == "remove":
        helper.unlink()
    elif change == "symlink":
        helper.unlink()
        helper.symlink_to(script)
    else:
        adapter.helper_sha256 = None
    with pytest.raises(PilotError, match="script_pin_mismatch"):
        adapter._call("list-all")
