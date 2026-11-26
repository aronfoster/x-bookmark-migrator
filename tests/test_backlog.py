import argparse
import copy
import json
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from test_unbookmark import migration as migration
from test_unbookmark import storage as storage
from test_worker import config, make_worker

from x_bookmark_migrator import cli, worker_cli
from x_bookmark_migrator.archive import batch_receipt, save_manifest
from x_bookmark_migrator.backlog import select_backlog
from x_bookmark_migrator.billing import HIGH_WATER, cutoff, settle
from x_bookmark_migrator.catalog import publish, read_catalog
from x_bookmark_migrator.core import TOKEN_ROTATION, PilotError
from x_bookmark_migrator.drive import digest, encode
from x_bookmark_migrator.unbookmark import make_plan
from x_bookmark_migrator.worker import JOURNAL


def test_manual_backlog_reads_latest_window_and_exact_repaired_version(migration, tmp_path):
    store, _, server, source, client, archived, _, _ = migration
    cfg = dict(config(tmp_path), helper_descriptors=[])
    store.write("config.json", cfg)
    catalog = source.catalog
    records = read_catalog(catalog)
    good = records[0]["versions"][0]
    old = copy.deepcopy(good)
    old["manifest"]["sha256"] = "0" * 64
    old.update(archive_status="blocked", blockers=["missing_media"])
    records[0]["versions"].append(old)
    publish(catalog, records)
    sha = digest(catalog.path("catalog.jsonl").read_bytes())
    # Exercise the actual saved-receipt writer with a synthetic transport.
    endpoint = "/2/users/100/bookmarks/folders/10"
    source.ids = ["103"]
    client.get(endpoint, {"max_results": "100"}, lambda: None)
    source.ids = ["102", "101", "999"]
    client.get(endpoint, {"max_results": "100"}, lambda: None)
    store.write(JOURNAL, {"schema_version": 1, "items": {"102": {"state": "blocked"}}})
    before = len(source.calls), len(server.calls), len(source.provider.calls)
    (candidate,) = select_backlog(store, catalog, cfg, "10", sha)
    assert candidate["post_id"] == "101" and candidate["raindrop_id"] == 101
    assert candidate["manifest"] == good["manifest"]
    assert candidate["payload"] == good["payload_receipt"]
    assert candidate["archive_receipt"] == archived["private_receipt"]
    assert candidate["source_read"] == client.observations[-1]["private_file"]

    parser = argparse.ArgumentParser()
    worker_cli.add_commands(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        ["worker", "--config", str(store.path("config.json")), "--inspect-backlog", "10", sha]
    )
    args.state_dir = store.root
    assert worker_cli.run(args) == {
        "mode": "inspect_backlog",
        "candidates": 1,
        "api_requests": 0,
        "source_sha256": candidate["source_read_sha256"],
        "selection_sha256": [digest(encode(candidate))],
    }
    store.write(
        "unbookmark-report-test.private.json",
        {"outcomes": [{"post_id": "101", "state": "blocked"}]},
    )
    assert worker_cli.run(args)["candidates"] == 0
    args.live = True
    with pytest.raises(PilotError, match="offline"):
        worker_cli.run(args)
    with pytest.raises(PilotError, match="verified digest"):
        select_backlog(store, catalog, cfg, "10", "a" * 64)
    assert before == (len(source.calls), len(server.calls), len(source.provider.calls))

    source.partial_folder = True
    client.get(endpoint, {"max_results": "100"}, lambda: None)
    with pytest.raises(PilotError, match="Ambiguous"):
        select_backlog(store, catalog, cfg, "10", sha)


@pytest.mark.parametrize("repair", ["media", "tagged_author", "legacy_author"])
def test_completed_repair_requires_published_exact_payload_and_bound_plan(
    migration, tmp_path, repair
):
    store, drive, _, source, client, archived, _, _ = migration
    cfg = dict(config(tmp_path), helper_descriptors=[])
    catalog = source.catalog
    original = store.read(archived["private_receipt"])["manifest"]
    row, donor, *_ = map(json.loads, drive.verify(original).splitlines())
    if repair == "legacy_author":
        payload = json.loads(drive.verify(row["payload"]))
        evidence = payload["source_evidence"]
        evidence["response"]["includes"]["users"] = []
        payload["retained_author_source"] = {
            "manifest": original,
            "payload": donor["payload"],
            "post_id": donor["post_id"],
            "envelope_sha256": digest(
                encode(json.loads(drive.verify(donor["payload"]))["source_evidence"])
            ),
        }
        data = encode(payload)
        row["payload"] = drive.save(
            row["category"], row["post_id"] + ".json", data, "application/json"
        )
        (tmp_path / "restored" / (row["post_id"] + ".json")).write_bytes(data)
    manifest = save_manifest(drive, [row])
    plan = "e" * 64
    name = f"repair-{plan}.receipt.private.json"
    binding = dict(plan_sha256=plan, max_asset_bytes=drive.max_bytes, drive_root=drive.root)
    if repair == "tagged_author":
        binding["reason"] = "retained_author"
    receipt = {**batch_receipt(manifest, [row], plan), **binding}
    store.create(f"repair-{plan}.manifest-intent.private.json", {**binding, "rows": [row]})
    records = read_catalog(catalog)
    record = next(r for r in records if r["post_id"] == row["post_id"])
    (predecessor,) = record["versions"]
    successor = dict(predecessor, manifest=manifest, payload_receipt=row["payload"])
    predecessor.update(archive_status="blocked", blockers=["blocked_byte_limit"])
    record["versions"].append(successor)
    source.ids = [row["post_id"]]
    client.get("/2/users/100/bookmarks/folders/10", {"max_results": "100"}, lambda: None)

    def inspect():
        publish(catalog, records)
        sha = digest(catalog.path("catalog.jsonl").read_bytes())
        return select_backlog(store, catalog, cfg, "10", sha)

    assert inspect() == []  # Frozen manifest intent is not completion evidence.
    store.create(name, receipt)
    store.write("config.json", cfg)
    parser = argparse.ArgumentParser()
    worker_cli.add_commands(parser.add_subparsers(dest="command"))
    args = parser.parse_args(
        [
            "worker",
            "--config",
            str(store.path("config.json")),
            "--inspect-backlog",
            "10",
            digest(catalog.path("catalog.jsonl").read_bytes()),
        ]
    )
    args.state_dir = store.root
    assert worker_cli.run(args)["candidates"] == int(repair == "media")
    if repair != "media":
        assert inspect() == []  # The archive is complete but removal needs donor replay.
        return
    (candidate,) = inspect()
    assert candidate["archive_receipt"] == name and candidate["payload"] == row["payload"]
    assert candidate["manifest"] == manifest and candidate["raindrop_id"] == int(row["post_id"])
    # Confirm the downstream plan constructor accepts the real repair basename.
    result = make_plan(store, drive, name, [row["post_id"]], "100", acceptance_confirmed=True)
    assert result["verified"] == 1 and result["x_api_calls"] == result["deleted"] == 0
    # A stale media link must withhold just its repair, not abort a folder scan.
    source.ids = [row["post_id"], donor["post_id"]]
    client.get("/2/users/100/bookmarks/folders/10", {"max_results": "100"}, lambda: None)
    restore = tmp_path / "restored"
    moved = tmp_path / "moved"
    restore.rename(moved)
    try:
        assert worker_cli.run(args)["candidates"] == 1
        assert [c["post_id"] for c in inspect()] == [donor["post_id"]]
    finally:
        moved.rename(restore)
    source.ids = [row["post_id"]]
    client.get("/2/users/100/bookmarks/folders/10", {"max_results": "100"}, lambda: None)
    successor["payload_receipt"] = dict(row["payload"], sha256="1" * 64)
    assert inspect() == []
    store.write(name, dict(receipt, plan_sha256="0" * 64))
    with pytest.raises(PilotError, match="differs from its plan"):
        inspect()


def test_manual_preparation_pins_verifies_and_replays_without_provider_calls(
    migration, tmp_path, monkeypatch
):
    store, _, server, source, client, _, _, _ = migration
    cfg = dict(config(tmp_path), helper_descriptors=[])
    store.write("config.json", cfg)
    old_pointer = {"preserve": True}
    store.create("verified-one-plan.private.json", old_pointer)
    source.ids = ["101", "102"]
    endpoint = "/2/users/100/bookmarks/folders/10"
    client.get(endpoint, {"max_results": "100"}, lambda: None)
    sha = digest(source.catalog.path("catalog.jsonl").read_bytes())
    parser = argparse.ArgumentParser()
    worker_cli.add_commands(parser.add_subparsers(dest="command"))

    def run(*flags):
        args = parser.parse_args(["worker", "--config", str(store.path("config.json")), *flags])
        args.state_dir = store.root
        return worker_cli.run(args)

    inspected = run("--inspect-backlog", "10", sha)
    source_sha = inspected["source_sha256"]
    selection = inspected["selection_sha256"][1]
    flags = ("--prepare-backlog-one", "10", sha, source_sha, selection)
    before = len(server.calls)
    with pytest.raises(PilotError, match="attestation"):
        run(*flags)
    for denied in ("--scheduled", "--live", "--allow-delete", "--apply-verified-plan"):
        with pytest.raises(PilotError):
            run(*flags, "--attest-archive-acceptance", denied)
    with pytest.raises(PilotError, match="binding changed"):
        run(*flags[:-1], "f" * 64, "--attest-archive-acceptance")
    assert len(server.calls) == before
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(server.handle), **kw),
    )
    result = run(*flags, "--attest-archive-acceptance")
    assert not result["already_staged"] and len(server.calls) > before
    assert result["verified"] == 1 and result["deleted"] == result["x_api_calls"] == 0
    name = f"backlog-plan-{selection}.private.json"
    saved = store.read(name)
    (item,) = saved["verified_plan"]["items"]
    assert item["post_id"] == "102" and item["state"] == "verified"
    assert saved["backlog_binding"]["raindrop_id"] == 102
    assert digest(encode(saved)) == result["plan_sha256"]
    before = len(server.calls)
    assert run(*flags, "--attest-archive-acceptance") == {**result, "already_staged": True}
    store.write(JOURNAL, {"schema_version": 1, "items": {"102": {"state": "blocked"}}})
    with pytest.raises(PilotError, match="binding changed"):
        run(*flags, "--attest-archive-acceptance")
    store.write(JOURNAL, {"schema_version": 1, "items": {}})
    client.get(endpoint, {"max_results": "100"}, lambda: None)
    with pytest.raises(PilotError, match="binding changed"):
        run(*flags, "--attest-archive-acceptance")
    assert len(server.calls) == before
    assert len(source.calls) == 2 and not source.deletes and not source.provider.calls
    assert store.read(name) == saved and store.read("verified-one-plan.private.json") == old_pointer

    # A digest match alone does not prove the published payload receipt matches
    # the independently downloaded manifest's complete receipt.
    records = read_catalog(source.catalog)
    version = next(r for r in records if r["post_id"] == "102")["versions"][0]
    version["payload_receipt"] = dict(version["payload_receipt"], file_id="other")
    publish(source.catalog, records)
    sha = digest(source.catalog.path("catalog.jsonl").read_bytes())
    changed = run("--inspect-backlog", "10", sha)
    with pytest.raises(PilotError, match="Drive plan differs"):
        run(
            "--prepare-backlog-one",
            "10",
            sha,
            changed["source_sha256"],
            changed["selection_sha256"][1],
            "--attest-archive-acceptance",
        )
    assert list(store.root.glob("backlog-plan-*.private.json")) == [store.path(name)]


def test_real_cli_requires_complete_sources_and_rejects_staged_artifact_on_generic_apply(
    migration, tmp_path, monkeypatch, capsys
):
    store, _, server, source, client, _, _, _ = migration
    cfg = dict(config(tmp_path), helper_descriptors=[])
    store.write("config.json", cfg)
    source.ids = ["101"]
    client.get("/2/users/100/bookmarks/folders/10", {"max_results": "100"}, lambda: None)
    sha = digest(source.catalog.path("catalog.jsonl").read_bytes())
    worker = ["worker", "--config", str(store.path("config.json"))]

    def invoke(*args):
        monkeypatch.setattr(sys, "argv", ["x-migrator", "--state-dir", str(store.root), *args])
        status = cli.main()
        return status, capsys.readouterr()

    def files():
        return {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}

    inspect = [*worker, "--inspect-backlog", "10", sha]
    status, result = invoke(*inspect)
    assert status == 0
    inspected = json.loads(result.out)
    (selection,) = inspected["selection_sha256"]
    prepare = [
        *worker,
        "--prepare-backlog-one",
        "10",
        sha,
        inspected["source_sha256"],
        selection,
        "--attest-archive-acceptance",
    ]
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(server.handle), **kw),
    )
    for gate in (False, 1):
        cfg["gates"]["removal_sources_complete"] = gate
        store.write("config.json", cfg)
        before = files(), len(server.calls)
        for args in (inspect, prepare):
            status, result = invoke(*args)
            assert status == 1 and "Complete removal history" in result.err
            assert (files(), len(server.calls)) == before
    cfg["gates"]["removal_sources_complete"] = True
    store.write("config.json", cfg)
    assert invoke(*prepare)[0] == 0
    name = f"backlog-plan-{selection}.private.json"
    saved = store.read(name)
    assert saved["kind"] == "backlog_preparation" and "items" not in saved
    assert saved["verified_plan"]["items"][0]["state"] == "verified"

    store.write(JOURNAL, {"schema_version": 1, "items": {"101": {"state": "blocked"}}})
    before = files(), len(server.calls)
    monkeypatch.setenv("X_CLIENT_ID", "app")
    status, result = invoke(
        "unbookmark-apply",
        "--plan-file",
        name,
        "--app-owner-id",
        "100",
        "--drive-folder-id",
        "root",
        "--per-call-reserve-usd",
        ".01",
        "--catalog-dir",
        str(source.catalog.root),
        "--raindrop-ledger-dir",
        str(source.adapter.store.root),
        "--raindrop-script",
        str(source.adapter.script),
        "--legacy-removal-helper-retired",
        "--confirm-unbookmark",
        "--console-hard-cap-confirmed",
        "--auto-recharge-off-confirmed",
    )
    assert status == 1 and "not an executable unbookmark plan" in result.err
    assert (files(), len(server.calls)) == before
    assert len(source.calls) == 1 and not source.provider.calls
    assert not list(store.root.glob("*.intent.private.json"))


@pytest.mark.parametrize(
    "fault",
    [
        "none",
        "hold",
        "repinned",
        "missing_link",
        "missing_restore",
        "rate_limit",
        "half_settled",
        "identity_denied",
    ],
)
def test_staged_worker_dispatch(migration, tmp_path, monkeypatch, capsys, fault):
    store, _, server, source, client, _, _, _ = migration
    worker = make_worker(migration, tmp_path, max_items=1, helper_descriptors=[])
    cfg = dict(
        worker.config,
        raindrop_script="fixture",
        raindrop_script_sha256="1" * 64,
        raindrop_helper_sha256="2" * 64,
    )
    store.write("config.json", cfg)
    parser = argparse.ArgumentParser()
    worker_cli.add_commands(parser.add_subparsers(dest="command"))

    def run(*args):
        parsed = parser.parse_args(["worker", "--config", str(store.path("config.json")), *args])
        parsed.state_dir = store.root
        return worker_cli.run(parsed)

    source.ids = ["101", "102", "103", "104"]
    client.get("/2/users/100/bookmarks/folders/10", {"max_results": "100"}, lambda: None)
    sha = digest(source.catalog.path("catalog.jsonl").read_bytes())
    inspected = run("--inspect-backlog", "10", sha)
    selection = inspected["selection_sha256"][1]
    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kw: real_client(transport=httpx.MockTransport(server.handle), **kw),
    )
    staged = run(
        "--prepare-backlog-one",
        "10",
        sha,
        inspected["source_sha256"],
        selection,
        "--attest-archive-acceptance",
    )
    monkeypatch.setattr(worker_cli, "protected_config", lambda path: None)
    monkeypatch.setattr(
        worker_cli, "os", SimpleNamespace(getuid=lambda: 1000, environ=worker_cli.os.environ)
    )
    monkeypatch.setattr(worker_cli, "require_protected_helper", lambda path: None)
    monkeypatch.setattr(worker_cli, "RaindropAdapter", lambda *a, **kw: source.adapter)
    monkeypatch.setattr(worker_cli, "WorkerClient", lambda *a: client)
    monkeypatch.setattr(worker_cli, "Drive", lambda *a: migration[1])
    monkeypatch.setenv("X_CLIENT_ID", "fixture")
    monkeypatch.setenv("RAINDROP_TOKEN", "fixture")
    store.write(
        "worker-billing.private.json",
        {"actual_usd": "0", "observed_at": datetime.now(UTC).isoformat()},
    )
    store.write("budget.private.json", {datetime.now(UTC).strftime("%Y-%m"): "1.00"})
    envelope = staged["plan_sha256"]
    if fault == "hold":
        store.write(JOURNAL, {"schema_version": 1, "items": {"102": {"state": "blocked"}}})
    if fault == "repinned":
        envelope = "0" * 64
    if fault == "missing_link":
        source.provider.active.clear()
    if fault == "missing_restore":
        (tmp_path / "restored" / "102.json").unlink()
    if fault == "rate_limit":
        source.status = 429
    args = [
        "--once",
        "--live",
        "--allow-delete",
        "--apply-verified-plan",
        "--staged-backlog",
        selection,
        envelope,
        str(tmp_path / "restored"),
    ]
    before = len(server.calls), len(source.calls)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "x-migrator",
            "--state-dir",
            str(store.root),
            "worker",
            "--config",
            str(store.path("config.json")),
            *args,
        ],
    )
    assert cli.main() == 1
    assert "Reconcile the historical billing cutoff" in capsys.readouterr().err
    with pytest.raises(PilotError, match="billing cutoff"):
        run(*args)
    settle(
        store,
        expected=cutoff(store)["cutoff_sha256"],
        cumulative="0.50",
        through=datetime.now(UTC).isoformat(),
        evidence="3" * 64,
        attested=True,
    )
    if fault in ("hold", "repinned"):
        with pytest.raises(PilotError, match="held|digest differs"):
            run(*args)
        assert before == (len(server.calls), len(source.calls))
        assert not source.provider.calls and not source.deletes
    else:
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "x-migrator",
                "--state-dir",
                str(store.root),
                "worker",
                "--config",
                str(store.path("config.json")),
                *args,
            ],
        )
        if fault in ("half_settled", "identity_denied"):
            calls = []

            def deny(req):
                calls.append(req.url.path)
                return httpx.Response(403, json={"detail": "fixture"})

            client.http = real_client(transport=httpx.MockTransport(deny))
            if fault == "half_settled":
                store.path(HIGH_WATER).unlink()
                store.write(
                    "tokens.private.json", dict(store.read("tokens.private.json"), expires_at=0)
                )
            token_before = store.read("tokens.private.json")
            rotation_before = store.read(TOKEN_ROTATION)
            assert cli.main() == 1
            output = capsys.readouterr()
            assert calls == ([] if fault == "half_settled" else ["/2/users/me"])
            assert len(server.calls) == before[0] and not source.provider.calls
            assert not list(store.root.glob("unbookmark-*.intent.private.json"))
            assert store.read("tokens.private.json") == token_before
            assert store.read(TOKEN_ROTATION) == rotation_before
            if fault == "identity_denied":
                name = client.observations[-1]["private_file"]
                assert store.read(name)["status"] == 403
                assert name not in output.err + output.out and "http_403" in output.err
            return
        status = cli.main()
        targets = [r.url.path.rsplit("/", 1)[-1] for r in source.deletes]
        intents = len(list(store.root.glob("unbookmark-*.intent.private.json")))
        print(
            f"{fault}: exit={status}, Raindrop={source.provider.calls}, "
            f"intents={intents}, DELETE={targets}"
        )
        assert status == int(fault != "none")
        assert source.provider.calls == (
            [] if fault == "missing_restore" else [(("get", 102), None)]
        )
        assert targets == (["102"] if fault in ("none", "rate_limit") else [])
        assert intents == len(targets)
        if fault == "rate_limit":
            worker.refresh_history()
            assert "102" in worker.held
            assert cli.main() == 1  # Deferred manual run is not a successful removal.
            assert "Provider retry is deferred" in capsys.readouterr().err
        else:
            with pytest.raises(PilotError, match="binding changed"):
                run(*args)
        assert len(source.deletes) == len(targets)
