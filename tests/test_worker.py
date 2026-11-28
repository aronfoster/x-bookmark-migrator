"""Offline fault injection through real archive, catalog, and deletion transactions."""

import copy
import hashlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_archive import media_http
from test_unbookmark import migration as migration
from test_unbookmark import storage as storage

from x_bookmark_migrator import cli, worker_cli
from x_bookmark_migrator.catalog import annotate, read_catalog
from x_bookmark_migrator.core import WRITE_SCOPES, PilotError, RequestSlots, Store
from x_bookmark_migrator.drive import digest, encode
from x_bookmark_migrator.inventory import Inventory
from x_bookmark_migrator.unbookmark import attempt_base, latest_attempt
from x_bookmark_migrator.worker import (
    DEFER,
    GATES,
    JOURNAL,
    OPERATIONS,
    ROTATION,
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
    budget = RequestSlots(cfg["max_calls"])
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


@pytest.mark.parametrize("initial_slots", [0, 45])
def test_scheduled_backlog_continues_without_repeating_daily_intake(
    migration, tmp_path, initial_slots
):
    worker = make_worker(
        migration, tmp_path, max_items=1, max_calls=45, continue_queued_history=True
    )
    now = time.time()
    worker.clock = lambda: now
    worker.state["next_intake"] = now + 86400
    for _ in range(initial_slots):
        worker.budget.reserve()
    result = worker.run(allow_delete=True, scheduled=True)
    assert result["stop_reason"] is None
    assert len(migration[3].deletes) == (0 if initial_slots else 1)
    assert worker.state["next_daily"] == now + 300
    restarted = make_worker(migration, tmp_path, max_items=20, max_calls=45)
    restarted.clock = lambda: now + 301
    assert restarted.run(allow_delete=True, scheduled=True)["confirmed_removed"] == 3
    assert len(set(migration[3].deletes)) == len(migration[3].deletes) == 3
    assert restarted.state["next_intake"] == now + 86400


def test_archive_only_cannot_create_raindrop_or_delete(migration, tmp_path):
    w = make_worker(migration, tmp_path)
    w.raindrop = None
    result = w.run(allow_delete=False)
    assert not result["stop_reason"] and result["remaining_reachable_queued"] == 3
    assert not migration[3].calls


def save_drive_proof(store, name):
    plan = store.read(name)
    (item,) = plan["items"]
    proof = dict(
        schema_version=1,
        inventory=plan["inventory_checkpoint"],
        post_id=item["post_id"],
        manifest_sha256=plan["archive_receipt"]["manifest"]["sha256"],
        payload_sha256=item["payload"]["sha256"],
    )
    key = hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest()
    store.create(f"one-drive-check-{key}.private.json", proof)
    return key


@pytest.mark.parametrize("linked", [True, False])
def test_verified_plan_removes_only_proved_id_without_raindrop_write(migration, tmp_path, linked):
    store, _, _, source, _, _, make_plan, _ = migration
    worker = make_worker(migration, tmp_path, max_items=1)
    worker.config["gates"]["live_authorized"] = False
    with pytest.raises(PilotError, match="live gate"):
        worker.apply_verified_plan()
    worker.config["gates"]["live_authorized"] = True
    name = make_plan(["102"])
    key = save_drive_proof(store, name)
    for ids in (["101"], ["101", "102"]):
        store.write(
            "verified-one-plan.private.json", {"drive_check_sha256": key, "plan": make_plan(ids)}
        )
        with pytest.raises(PilotError, match="differs"):
            worker.apply_verified_plan()
    assert not source.calls and not source.provider.calls
    store.write("verified-one-plan.private.json", {"drive_check_sha256": key, "plan": name})
    with pytest.raises(PilotError, match="pointer changed"):
        worker.apply_verified_plan(expected_pointer={"plan": "different"})
    worker.held.add("102")
    with pytest.raises(PilotError, match="worker hold"):
        worker.apply_verified_plan()
    assert not source.calls and not source.provider.calls
    worker.held.clear()
    if not linked:
        source.provider.active.clear()
        assert worker.apply_verified_plan()["states"] == {"blocked": 1}
        assert not source.deletes and not source.provider.posts
        return
    assert worker.apply_verified_plan()["states"] == {"unbookmarked": 1}
    assert source.provider.calls == [(("get", 102), None)]
    assert [r.url.path.split("/")[-1] for r in source.deletes] == ["102"]
    with pytest.raises(PilotError, match="previous attempt"):
        make_worker(migration, tmp_path, max_items=1).apply_verified_plan()


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "missing",
        "tampered",
        "unlinked",
        "local_missing",
        "local_tampered",
        "local_absolute",
        "local_traversal",
        "local_symlink",
        "local_manifest",
    ],
)
def test_verified_plan_rechecks_media_and_link_before_intent(
    migration, tmp_path, monkeypatch, fault
):
    store, _, server, source, _, _, make_plan, _ = migration
    name = make_plan(["101"])
    (item,) = store.read(name)["items"]
    assert item["media_files"] == 1
    pointer = {"plan": name, "drive_check_sha256": save_drive_proof(store, name)}
    store.write("verified-one-plan.private.json", pointer)
    payload = json.loads(server.bytes[item["payload"]["file_id"]])
    media_id = payload["media"][0]["storage"]["file_id"]
    if fault == "missing":
        del server.objects[media_id]
    elif fault == "tampered":
        server.corrupt_id = media_id
    elif fault == "unlinked":
        source.provider.active.clear()
        monkeypatch.setattr(
            "x_bookmark_migrator.discovery_gate.positive_media_only", lambda *_: True
        )
    elif fault and fault.startswith("local_"):
        records = read_catalog(source.catalog)
        version = next(r for r in records if r["post_id"] == "101")["versions"][0]
        (asset,) = version["media"]
        local = (source.catalog.root / asset["local_url"]).resolve()
        if fault == "local_missing":
            local.unlink()
        elif fault == "local_tampered":
            data = local.read_bytes()
            local.write_bytes(b"X" + data[1:])
        elif fault == "local_manifest":
            (local.parent / "manifest.jsonl").write_bytes(b"{}")
        else:
            if fault == "local_absolute":
                asset["local_url"] = str(local)
            elif fault == "local_symlink":
                alias = local.parent.with_name("alias")
                alias.symlink_to(local.parent, target_is_directory=True)
                asset["local_url"] = f"../alias/{local.name}"
            else:
                asset["local_url"] = f"%2E%2E/../{tmp_path.name}/restored/{local.name}"
            source.catalog.path("catalog.jsonl").write_bytes(b"".join(encode(r) for r in records))

    worker = make_worker(migration, tmp_path, max_items=1)
    result = worker.apply_verified_plan(expected_pointer=pointer)
    if fault:
        assert result["blocked"] and result["delete_attempts_this_run"] == 0
        assert not source.deletes and not list(store.root.glob("*.intent.private.json"))
        if fault.startswith("local_"):
            assert result["blocked_reasons"] == {"blocked_discovery": 1}
    else:
        assert result["states"] == {"unbookmarked": 1}
        assert result["new_window_observations"] == 1
        assert [r.url.path.rsplit("/", 1)[-1] for r in source.deletes] == ["101"]
    assert source.provider.calls == ([(("get", 101), None)] if fault in {None, "unlinked"} else [])


def test_cli_pins_entire_verified_pointer(migration, tmp_path, monkeypatch, capsys):
    store, _, server, source, _, _, make_plan, _ = migration
    name = make_plan(["102"])
    key = save_drive_proof(store, name)
    pointer = {"plan": name, "note": "café", "drive_check_sha256": key}
    canonical = f'{{"drive_check_sha256": "{key}", "note": "café", "plan": "{name}"}}\n'
    pin = hashlib.sha256(canonical.encode()).hexdigest()
    cfg = dict(
        config(tmp_path),
        max_items=1,
        raindrop_script="fixture",
        raindrop_script_sha256="0" * 64,
        raindrop_helper_sha256="1" * 64,
    )
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg))
    path.chmod(0o600)
    store.write("worker-billing.private.json", {"observed_at": "fixture", "actual_usd": "0"})
    monkeypatch.setenv("X_CLIENT_ID", "app")
    monkeypatch.setenv("RAINDROP_TOKEN", "fixture")
    monkeypatch.setattr(worker_cli, "protected_config", lambda p: p.read_bytes())
    monkeypatch.setattr(worker_cli, "require_protected_helper", lambda _: None)
    monkeypatch.setattr(
        worker_cli, "os", SimpleNamespace(getuid=lambda: 1000, environ=worker_cli.os.environ)
    )
    monkeypatch.setattr(worker_cli, "RaindropAdapter", lambda *a, **kw: source.adapter)
    client = httpx.Client

    def unexpected_media(request):
        pytest.fail("Exact-plan application requested media from the network.")

    handlers = iter((source.handle, server.handle, unexpected_media) * 2)
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(next(handlers)), **kw)
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "x-migrator",
            "--state-dir",
            str(store.root),
            "worker",
            "--config",
            str(path),
            "--once",
            "--live",
            "--allow-delete",
            "--apply-verified-plan",
            "--expected-pointer-sha256",
            pin,
        ],
    )
    store.write("verified-one-plan.private.json", {**pointer, "note": "changed"})
    server.calls.clear()
    assert cli.main() == 1
    assert "pointer changed" in capsys.readouterr().err
    assert not (server.calls or source.calls or source.provider.calls)
    assert not list(store.root.glob("*.intent.private.json"))
    store.write("verified-one-plan.private.json", pointer)
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["states"] == {"unbookmarked": 1}
    assert [r.url.path.rsplit("/", 1)[-1] for r in source.deletes] == ["102"]


@pytest.mark.parametrize(
    ("flags", "pin", "error"),
    [
        (["--dry-run"], "0" * 64, "--expected-pointer-sha256"),
        (["--once", "--live", "--allow-delete"], "0" * 64, "--expected-pointer-sha256"),
        (["--once", "--live", "--allow-delete", "--apply-verified-plan"], "A" * 64, "lowercase"),
        (
            ["--once", "--live", "--allow-delete", "--apply-verified-plan", "--scheduled"],
            "0" * 64,
            "manual",
        ),
    ],
)
def test_cli_rejects_invalid_pointer_pin_before_config(monkeypatch, capsys, flags, pin, error):
    monkeypatch.setattr(
        sys,
        "argv",
        ["x-migrator", "worker", "--config", "/missing", *flags, "--expected-pointer-sha256", pin],
    )
    assert cli.main() == 1
    assert error in capsys.readouterr().err


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
        apply_verified_plan=False,
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
    asset = records[0]["versions"][0]["media"][0]
    annotate(
        catalog,
        records,
        asset["asset_id"],
        visibility="hidden",
        description="Owner memory",
        sha256=asset["storage"]["sha256"],
    )
    before = catalog.path("annotations.private.json").read_bytes()
    incoming = copy.deepcopy(read_catalog(catalog)[:1])
    merge_publication(catalog, incoming)
    result = read_catalog(catalog)
    assert len(result) == 3
    assert catalog.path("annotations.private.json").read_bytes() == before
    assert result[0]["versions"][0]["media"][0]["browse_visibility"] == "hidden"
    w = make_worker(migration, tmp_path)
    assert w.run(allow_delete=True)["stop_reason"] is None
    annotations = catalog.read("annotations.private.json")
    assert annotations["raindrop_links"]
    assert annotations == json.loads(before)


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
    w.catalog.write("annotations.private.json", {})  # Exercise creation for an unlinked archive.
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
    w.catalog.write("annotations.private.json", {})
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
    assert w.budget.operations.total() > 0
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


@pytest.mark.parametrize(
    ("limit", "expected_lookups", "archived"),
    [(1, [], ["104"]), (2, [["105"]], ["104", "105"])],
)
def test_discovery_hydrates_only_the_selected_archive_batch(
    migration, tmp_path, limit, expected_lookups, archived
):
    from test_archive import includes, post

    store, _, _, source, client, *_ = migration
    lookups = []
    source.ids = ["101", "105", "106"]

    def handle(request):
        if request.url.path == "/2/users/100/bookmarks":
            return httpx.Response(
                200, json={"data": [post("104")], "includes": includes([]), "meta": {}}
            )
        if request.url.path == "/2/tweets":
            ids = request.url.params["ids"].split(",")
            lookups.append(ids)
            return httpx.Response(
                200, json={"data": [post(pid) for pid in ids], "includes": includes([])}
            )
        return source.handle(request)

    client.http = httpx.Client(transport=httpx.MockTransport(handle))
    worker = make_worker(migration, tmp_path, max_items=limit)
    worker.bootstrap(Inventory.load(store))
    for item in worker.state["items"].values():
        item["state"] = "blocked"
    worker.save()

    report = worker.run()
    assert report["stop_reason"] is None
    assert lookups == expected_lookups
    assert report["request_slots_by_operation"].get("hydrate", 0) == len(lookups)
    assert (
        sorted(pid for pid in worker.state["items"] if pid not in ("101", "102", "103")) == archived
    )
    inv = Inventory.load(store)
    assert inv.state["posts"]["106"]["folder_ids"] == ["10"]
    assert inv.state["posts"]["106"]["hydration_status"] == "pending"
    assert worker.state["items"]["101"]["state"] == "blocked"
    assert not source.deletes


def test_snapshot_separates_unattempted_complete_and_held_repairs(migration):
    from x_bookmark_migrator.archive import batch_receipt
    from x_bookmark_migrator.catalog import publish

    store, drive, _, source, _, _, _, _ = migration
    catalog = source.catalog
    records = read_catalog(catalog)
    records = records[:3]
    ordinary = copy.deepcopy(records[1]["versions"][0])
    for index, reason in enumerate(("retained_author", "targeted_lookup"), 1):
        version = records[index]["versions"][0]
        row = next(
            entry
            for entry in map(json.loads, drive.verify(version["manifest"]).splitlines())
            if entry["post_id"] == records[index]["post_id"]
        )
        raw = encode(row)
        manifest = {**version["manifest"], "sha256": digest(raw), "size_bytes": len(raw)}
        version["manifest"] = manifest
        plan = str(index) * 64
        binding = dict(plan_sha256=plan, max_asset_bytes=drive.max_bytes, drive_root=drive.root)
        journal = store
        if reason == "retained_author":
            binding["reason"] = reason
        else:
            journal = Store(store.root / "targeted-lookup-repairs")
        journal.create(
            f"repair-{plan}.receipt.private.json",
            {**batch_receipt(manifest, [row], plan), **binding},
        )
        journal.create(f"repair-{plan}.manifest-intent.private.json", {**binding, "rows": [row]})
    publish(catalog, records)
    store.path("repair-" + "f" * 64 + ".receipt.private.json").write_bytes(b"incomplete")
    report = snapshot(store, catalog=catalog)
    assert report["archived_records"] == 3
    assert report["complete_archives_unattempted"] == 1
    assert report["complete_archives_not_removable"] == 2
    assert report["blocked_archives_unattempted"] == 0
    records[1]["versions"].append(ordinary)
    publish(catalog, records)
    mixed = snapshot(store, catalog=catalog)
    assert mixed["complete_archives_unattempted"] == 2
    assert mixed["complete_archives_not_removable"] == 1


def scheduled_cli(tmp_path, monkeypatch, store, source, clock):
    cfg = dict(
        config(tmp_path),
        max_items=1,
        raindrop_script="fixture",
        raindrop_script_sha256="0" * 64,
        raindrop_helper_sha256="1" * 64,
    )
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg))
    path.chmod(0o600)
    store.write("worker-billing.private.json", {"observed_at": "fixture", "actual_usd": "0"})
    monkeypatch.setenv("X_CLIENT_ID", "app")
    monkeypatch.setenv("RAINDROP_TOKEN", "fixture")
    monkeypatch.setattr(worker_cli, "protected_config", lambda p: p.read_bytes())
    monkeypatch.setattr(worker_cli, "require_protected_helper", lambda _: None)
    monkeypatch.setattr(
        worker_cli, "os", SimpleNamespace(getuid=lambda: 1000, environ=worker_cli.os.environ)
    )
    monkeypatch.setattr(worker_cli, "Worker", lambda *args: Worker(*args, clock=clock))
    monkeypatch.setattr(worker_cli, "Deferral", lambda s: Deferral(s, clock))
    monkeypatch.setattr(worker_cli, "RaindropAdapter", lambda *a, **kw: source.adapter)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "x-migrator",
            "--state-dir",
            str(store.root),
            "worker",
            "--config",
            str(path),
            "--once",
            "--live",
            "--allow-delete",
            "--scheduled",
        ],
    )


@pytest.mark.parametrize(
    "history",
    [
        "eligible",
        "delayed",
        "legacy_delayed",
        "paused",
        "unknown_category",
        "no_progress",
        "observed_empty",
        "recover",
        "orphan_parent",
        "retry_parent",
    ],
)
def test_scheduled_service_discovers_later_save(migration, tmp_path, monkeypatch, capsys, history):
    from test_archive import includes, post

    store, _, server, source, *_ = migration
    now = [time.time()]

    def clock():
        return now[0]

    monkeypatch.setattr(time, "time", clock)
    token = store.read("tokens.private.json")
    store.write("tokens.private.json", {**token, "expires_at": clock() + 7 * 86400})
    old = Inventory.load(store)
    worker = make_worker(migration, tmp_path)
    worker.bootstrap(old)
    if history in {"no_progress", "observed_empty"}:
        source.ids = ["101", "102", "103"] if history == "no_progress" else ["101"]
        expected_stop = "no_new_ids_observed" if history == "no_progress" else "observed_empty"
        assert worker.run(allow_delete=True)["stop_reason"] == expected_stop
        now[0] += 86401
        source.calls.clear()
    if history in {"delayed", "legacy_delayed"}:
        for item in worker.state["items"].values():
            item["retry_at"] = clock() + 3 * 86400
        if history == "legacy_delayed":
            worker.state["next_daily"] = clock() + 3 * 86400
    elif history in {"paused", "unknown_category"}:
        old.state["streams"]["folder:20"] = {
            **old.state["streams"]["folder:10"],
            "folder_id": "20",
            "status": "paused",
            "next_token": "historical-cursor",
        }
        old.save()
        for item in worker.state["items"].values():
            item["state"] = "blocked"
    if history in {"orphan_parent", "retry_parent"}:
        for item in worker.state["items"].values():
            item["state"] = "blocked"
    worker.save()
    old_items = copy.deepcopy(worker.state["items"])
    scheduled_cli(tmp_path, monkeypatch, store, source, clock)
    if history == "paused":
        # A legacy removal with a valid identity can lack modern verified-folder metadata.
        from test_repair import native

        prior = native(store, "199", 200, {"data": {"bookmarked": False}})
        store.write(prior + ".window.private.json", {"window_status": "observed_empty"})
    source.ids = ["110", *source.ids, "111", "112", "113", "199"]
    main_ids = {"101", "110"}
    main_reads = []
    folder_reads = []
    folder_lists = []

    def handle(request):
        route = request.url.path
        assert "pagination_token" not in request.url.params
        if route == "/2/users/100/bookmarks" and request.method == "GET":
            main_reads.append(request)
            return httpx.Response(
                200,
                json={
                    "data": [post(pid) for pid in source.ids if pid in main_ids],
                    "includes": includes([]),
                    "meta": {"next_token": "unused"},
                },
            )
        if route == "/2/tweets":
            # After baseline replacement, the next due empty queue may resume history.
            return httpx.Response(
                200,
                json={
                    "data": [post(pid) for pid in request.url.params["ids"].split(",")],
                    "includes": includes([]),
                },
            )
        if route.endswith("/folders"):
            folder_lists.append(request)
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "10", "name": source.folder_name},
                        {"id": "20", "name": "Other"},
                    ]
                },
            )
        if route.endswith(("/folders/10", "/folders/20")):
            folder_reads.append(route)
            if history == "unknown_category":
                return httpx.Response(200, json={"data": [{"id": "199"}]})
            if route.endswith("/20"):
                return httpx.Response(
                    200,
                    json={
                        "data": [{"id": "199"}] if "199" in source.ids else [],
                        "meta": {"result_count": int("199" in source.ids)},
                    },
                )
        return source.handle(request)

    client = httpx.Client
    handlers = iter((handle, server.handle, media_http) * 6)
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(next(handlers)), **kw)
    )
    # A durable provider reset and an overlapping owner operation each exclude the whole run.
    gate = Deferral(store, clock)
    gate.save("x", seconds=300)
    assert cli.main() == 0
    assert not main_reads
    now[0] += 301
    with store.locked():
        assert cli.main() == 1
    assert not main_reads
    if history == "legacy_delayed":
        # Previous versions used the earliest delayed item as the entire service deadline.
        assert cli.main() == 0
        assert not main_reads
        assert store.read(JOURNAL)["next_intake"] == clock() + 86400
        now[0] += 86400
    if history in {"recover", "orphan_parent", "retry_parent"}:

        def interrupt_after_receipt(self, receipt, inventory):
            assert self.store.read(receipt)["daily_pass"] == inventory.state["pass_id"]
            raise KeyboardInterrupt

        def interrupt_after_queue(self, inventory, *, scheduled):
            assert scheduled
            self.state["items"]["110"]["retry_at"] = clock() + 2 * 86400
            self.save()
            raise KeyboardInterrupt

        with monkeypatch.context() as crash:
            if history == "retry_parent":
                crash.setattr(Worker, "drain", interrupt_after_queue)
            else:
                crash.setattr(Worker, "adopt", interrupt_after_receipt)
            with pytest.raises(KeyboardInterrupt):
                cli.main()
        assert not source.deletes and not source.provider.posts
        receipt_count = len(list(store.root.glob("archive-*.private.json")))
        if history != "recover":
            old_receipts = [store.read(p.name) for p in store.root.glob("archive-*.private.json")]
            (old_daily,) = [receipt for receipt in old_receipts if receipt.get("daily_pass")]
            old = Inventory(store, "100")
            with pytest.raises(PilotError, match="another active inventory"):
                Inventory.load(store, daily_pass=old_daily["daily_pass"])
        now[0] += 86401
    if history == "retry_parent":
        # A fresh baseline and a future item deadline must not strand unrelated new saves.
        main_ids.add("114")
        source.ids.insert(0, "114")
        retry_at = store.read(JOURNAL)["items"]["110"]["retry_at"]
        assert cli.main() == 0, capsys.readouterr()
        assert store.read("worker-report.private.json")["run_counts"]["superseded_queued"] == 1
        deferred = store.read(JOURNAL)["items"]["110"]
        assert deferred["state"] == "retryable" and deferred["retry_at"] == retry_at
        assert [r.url.path.split("/")[-1] for r in source.deletes] == ["114"]
        now[0] += 86401
    assert cli.main() == 0, capsys.readouterr()
    expected_reads = 2 if history in {"recover", "orphan_parent", "retry_parent"} else 1
    if history == "retry_parent":
        expected_reads += 1
    assert len(main_reads) == expected_reads
    if history == "recover":
        assert len(list(store.root.glob("archive-*.private.json"))) == receipt_count
    if history == "unknown_category":
        item = store.read(JOURNAL)["items"]["110"]
        assert item["state"] == "blocked" and "blocked_category_unknown" in item["blockers"]
        assert any(route.endswith("/20") for route in folder_reads)
        assert store.read("worker-report.private.json")["run_counts"]["daily_category_unknown"] == 1
        assert not source.deletes and not source.provider.posts
        return
    expected_removed = ["114", "110"] if history == "retry_parent" else ["110"]
    assert [r.url.path.split("/")[-1] for r in source.deletes] == expected_removed
    attempt, _ = latest_attempt(store, "100", "110")
    assert store.read(attempt + ".intent.private.json")["inventory_post_ids"] == sorted(
        old.state["posts"]
    )
    journal = store.read(JOURNAL)
    assert {pid: journal["items"][pid] for pid in old_items} == old_items
    assert Inventory.load(store).name == old.name
    assert "199" not in journal["items"]
    assert journal["items"]["110"]["discovery_state"] == "raindrop"
    if history in {"retry_parent", "orphan_parent"}:
        current = store.read(journal["items"]["110"]["receipt"])
        assert current["daily_pass"] != old_daily["daily_pass"]
        assert (
            Inventory.load(store, daily_pass=current["daily_pass"]).state["daily_parent"]
            == old.state["pass_id"]
        )
        assert store.read("archive-" + old_daily["receipt_id"] + ".private.json") == old_daily
    if history == "paused":
        assert any(route.endswith("/20") for route in folder_reads)
        assert Inventory.load(store).state["streams"]["folder:20"]["status"] == "paused"

    if history in {"no_progress", "observed_empty"}:
        assert any(route.endswith("/10") for route in folder_reads)
    assert (
        "unbookmark_attempt_requires_fresh_pass" in Inventory.load(store).state["continuity_notes"]
    )
    assert len(read_catalog(source.catalog)) == 3 + len(expected_removed)
    assert len(source.provider.posts) == len(expected_removed)
    calls = len(source.calls), len(server.calls), len(source.provider.calls)
    now[0] += 300
    assert cli.main() == 0
    assert len(main_reads) == expected_reads
    assert calls == (len(source.calls), len(server.calls), len(source.provider.calls))

    # A later due invocation may drain history. The held save never gets a second create or DELETE.
    now[0] += 86400
    old_lists = len(folder_lists)
    exit_status = cli.main()
    report = store.read("worker-report.private.json")
    assert report["stop_reason"] in {None, "no_new_ids_observed", "observed_empty"}
    assert exit_status == int(report["stop_reason"] is not None)
    assert len(main_reads) == expected_reads + 1
    assert sum(r.url.path.endswith("/110") for r in source.deletes) == 1
    if history in {"orphan_parent", "retry_parent"}:
        assert source.deletes[-1].url.path.endswith("/199")
        assert len(source.provider.posts) == len(expected_removed) + 1
    else:
        assert len(source.provider.posts) == len(expected_removed)
    if history in {"delayed", "paused"}:
        assert len(folder_lists) == old_lists


@pytest.mark.parametrize(
    "history",
    [
        "eligible",
        "paused",
        "blocked_window",
        "delayed",
        "request_limit",
        "stalled_comparison",
        "baseline_settled",
        "baseline_unfinished",
        "missing_comparison",
        "unattributed_intents",
    ],
)
def test_empty_queue_scheduled_folder_history(migration, tmp_path, monkeypatch, history):
    from test_archive import includes, post

    store, _, server, source, *_ = migration

    now = [time.time()]

    def clock():
        return now[0]

    monkeypatch.setattr(time, "time", clock)
    token = store.read("tokens.private.json")
    store.write("tokens.private.json", {**token, "expires_at": clock() + 7 * 86400})
    scheduled_cli(tmp_path, monkeypatch, store, source, clock)
    baseline = Inventory.load(store)
    worker = make_worker(migration, tmp_path)
    worker.bootstrap(baseline)
    for item in worker.state["items"].values():
        item["state"] = "blocked"
    if history in {
        "stalled_comparison",
        "baseline_settled",
        "baseline_unfinished",
        "missing_comparison",
    }:
        # Use the real transaction writer and its frozen baseline, including for legacy replay.
        worker.state["items"]["101"]["state"] = "queued"
        source.ids = (
            ["101", "102", "103"]
            if history == "stalled_comparison"
            else ["101", "102", "104", "103"]
        )
        worker.save()
        result = worker.run(allow_delete=True)
        assert result["stop_reason"] == (
            "no_new_ids_observed" if history == "stalled_comparison" else None
        )
        attempt, _ = latest_attempt(store, "100", "101")
        assert store.read(attempt + ".intent.private.json")["inventory_folder_post_ids"] == [
            "101",
            "102",
            "103",
        ]
        if history == "missing_comparison":
            store.path(attempt + ".window.private.json").unlink()
        if history == "baseline_unfinished":
            del worker.state["items"]["103"]
        now[0] += 86401
        worker.state["streams"].clear()
        source.calls.clear()
    if history == "unattributed_intents":
        for pid, metadata in zip(
            ("201", "202", "203"),
            ({}, {"verified": None}, {"verified": {"folder": None}}),
            strict=True,
        ):
            store.create(
                attempt_base("100", pid) + "-000000.intent.private.json",
                {
                    "schema_version": 1,
                    "state": "unbookmark_requested",
                    "post_id": pid,
                    "owner_id": "100",
                    "endpoint": f"/2/users/100/bookmarks/{pid}",
                    **metadata,
                },
            )
    # A known, unfinished baseline member keeps this folder eligible for a historical read.
    if history != "blocked_window":
        stream = baseline.state["streams"]["folder:10"]
        stream["pages"].clear()
        baseline.accept_page(
            "folder:10",
            {"data": [{"id": pid} for pid in ("101", "102", "103", "109")]},
            "baseline-window",
        )
    if history == "paused":
        baseline.state["streams"]["folder:10"].update(status="paused", next_token="held-cursor")
    if history == "delayed":
        worker.state["items"]["101"].update(state="queued", retry_at=clock() + 86400)
    if history == "request_limit":
        path = tmp_path / "config.json"
        path.write_text(json.dumps(dict(json.loads(path.read_text()), max_calls=2)))
    baseline.save()
    worker.save()
    source.ids.insert(0, "110")
    paths = []

    def handle(req):
        path = req.url.path
        paths.append(path)
        assert "pagination_token" not in req.url.params
        if path == "/2/users/100/bookmarks":
            return httpx.Response(
                200,
                json={"data": [post("101")], "includes": includes([]), "meta": {"result_count": 1}},
            )
        if path == "/2/tweets":
            assert req.url.params["ids"] == "110"
            return httpx.Response(200, json={"data": [post("110")], "includes": includes([])})
        return source.handle(req)

    client = httpx.Client
    handlers = iter((handle, server.handle, media_http) * 2)
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(next(handlers)), **kw)
    )
    assert cli.main() == int(history == "request_limit")
    assert paths.count("/2/users/100/bookmarks") == 1
    journal = store.read(JOURNAL)
    assert Inventory.load(store).name == baseline.name
    if history in {"eligible", "baseline_unfinished"}:
        assert [req.url.path.rsplit("/", 1)[1] for req in source.deletes] == ["110"]
        assert len(source.provider.posts) == 1
        assert journal["streams"]["folder:10"]["confirmed_removed"] == 1
        assert journal["items"]["110"]["discovery_state"] == "raindrop"
    else:
        assert len(paths) == 2
        assert "110" not in journal["items"]
        assert not source.provider.posts and not source.deletes
        if history == "request_limit":
            assert store.read("worker-report.private.json")["stop_reason"] == "request_limit"
    if history == "unattributed_intents":
        report = store.read("worker-report.private.json")
        assert report["stop_reason"] is None
        assert report["run_counts"]["historical_stops_unattributed"] == 3
    calls = len(paths)
    assert cli.main() == 0 and len(paths) == calls
