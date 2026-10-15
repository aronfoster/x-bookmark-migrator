"""Synthetic complete archive -> exact-ID DELETE -> new folder window, and faults."""

import json
import sys
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from test_archive import archive_batch, includes, inventory, media_http, photo, post
from test_archive import storage as storage

from x_bookmark_migrator import cli
from x_bookmark_migrator.core import SCOPES, WRITE_SCOPES, Budget, Client, PilotError, auth_start
from x_bookmark_migrator.drive import digest, encode
from x_bookmark_migrator.unbookmark import (
    apply_plan,
    latest_attempt,
    make_plan,
    reconcile_plan,
)


class XServer:
    def __init__(self, store):
        self.store = store
        self.ids = ["101", "102", "103", "104", "105", "106"]
        self.calls = []
        self.status = 200
        self.body = {"data": {"bookmarked": False}}
        self.folder_name = "Synthetic category"
        self.mutate_then_timeout = False
        self.after_failure = False
        self.stagnant = False
        self.owner = "100"
        self.partial_folder = False

    def handle(self, req):
        self.calls.append(req)
        assert req.url.host == "api.x.com"
        assert req.headers["Authorization"] == "Bearer SECRET"
        if req.url.path == "/2/users/me":
            return httpx.Response(200, json={"data": {"id": self.owner}})
        if req.url.path.endswith("/folders"):
            return httpx.Response(200, json={"data": [{"id": "10", "name": self.folder_name}]})
        if req.method == "GET":
            assert req.url.path.endswith("/folders/10")
            assert dict(req.url.params) == {"max_results": "100"}  # No old cursor.
            if self.after_failure and any(r.method == "DELETE" for r in self.calls):
                return httpx.Response(429, json={"detail": "PRIVATE"})
            return httpx.Response(
                200,
                json={
                    "data": [{"id": i} for i in self.ids[:3]],
                    **({"errors": [{"detail": "PRIVATE"}]} if self.partial_folder else {}),
                },
            )
        assert req.method == "DELETE" and not req.content and not req.url.query
        pid = req.url.path.rsplit("/", 1)[-1]
        base, _ = latest_attempt(self.store, "100", pid)
        assert base  # Must be durable before network.
        intent = self.store.read(base + ".intent.private.json")
        assert intent["state"] == "unbookmark_requested" and intent["post_id"] == pid
        if self.status == 200 and not self.stagnant:
            self.ids.remove(pid)
        if self.mutate_then_timeout:
            raise httpx.ReadTimeout("PRIVATE", request=req)
        if isinstance(self.body, str):
            return httpx.Response(self.status, text=self.body)
        return httpx.Response(self.status, json=self.body, headers={"x-rate-limit-remaining": "49"})

    @property
    def deletes(self):
        return [r for r in self.calls if r.method == "DELETE"]


@pytest.fixture
def migration(storage):
    store, drive, server, _ = storage
    inv = inventory(
        store,
        [post("101", attachments={"media_keys": ["photo-1"]}), post("102"), post("103")],
        includes([photo()]),
    )
    with httpx.Client(transport=httpx.MockTransport(media_http)) as media:
        archived = archive_batch(inv, drive, media)
    store.write(
        "tokens.private.json",
        {
            "access_token": "SECRET",
            "refresh_token": "SECRET",
            "client_id": "app",
            "scope": " ".join(WRITE_SCOPES),
            "expires_at": time.time() + 7200,
        },
    )
    source = XServer(store)
    http = httpx.Client(transport=httpx.MockTransport(source.handle))
    client = Client(store, http, "app")

    def plan(ids=None):
        return make_plan(
            store,
            drive,
            archived["private_receipt"],
            ids or ["101"],
            "100",
            acceptance_confirmed=True,
        )["private_plan"]

    def apply(name, **kwargs):
        return apply_plan(
            store,
            drive,
            client,
            Budget(store, ".1", "10", "100"),
            name,
            "100",
            confirmed=True,
            **kwargs,
        )

    yield store, drive, server, source, client, archived, plan, apply
    http.close()


def test_three_real_archives_preserve_categories_delete_and_reveal_new_ids(migration):
    store, _, _, source, _, _, plan, apply = migration
    name = plan(["101", "102", "103"])
    assert not source.calls  # Plan uses independently retrieved Drive data, no X.
    result = apply(name)
    assert result["states"] == {"unbookmarked": 3}
    assert result["new_window_observations"] == 3 and not result["blocked"]
    assert result["delete_attempts_this_run"] == 3
    assert [r.url.path.rsplit("/", 1)[-1] for r in source.deletes] == ["101", "102", "103"]
    for pid in ("101", "102", "103"):
        base, _ = latest_attempt(store, "100", pid)
        comparison = store.read(base + ".window.private.json")
        assert comparison["removed_id_absent"] and comparison["newly_observed_ids"]
        assert comparison["inventory_baseline_available"]
        assert comparison["not_previously_inventoried_ids"] == comparison["newly_observed_ids"]
        assert comparison["older_bookmarks_exposed"] == "unverified"
        intent = store.read(base + ".intent.private.json")
        assert intent["verified"]["folder"] == {"id": "10", "names": ["Synthetic category"]}
        assert store.read(base + ".result.private.json")["state"] == "unbookmarked"
    from x_bookmark_migrator.inventory import Inventory

    inv = Inventory.load(store)
    assert not inv.state["consistent"] and not inv.all_folders_exhausted()
    with pytest.raises(PilotError, match="fresh inventory"):
        Inventory(store, "100", resume=True, source_unchanged=True)
    before = len(source.deletes)
    apply(name)
    assert len(source.deletes) == before  # Re-running never removes a new save.
    assert "Synthetic" not in json.dumps(result) and "SECRET" not in json.dumps(result)
    for path in store.root.glob("unbookmark-*.json"):
        assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "fault", ["owner", "scope", "corrupt_media", "corrupt_manifest", "rename", "moved", "partial"]
)
def test_gates_leave_bookmarks_untouched(migration, fault):
    store, _, server, source, _, _, plan, apply = migration
    name = plan()
    data = store.read(name)
    if fault == "owner":
        source.owner = "999"
    elif fault == "scope":
        token = store.read("tokens.private.json")
        token["scope"] = " ".join(SCOPES)
        store.write("tokens.private.json", token)
    elif fault == "corrupt_manifest":
        server.corrupt_id = data["archive_receipt"]["manifest"]["file_id"]
    elif fault == "corrupt_media":
        payload = json.loads(server.bytes[data["items"][0]["payload"]["file_id"]])
        server.corrupt_id = payload["media"][0]["storage"]["file_id"]
    elif fault == "rename":
        source.folder_name = "Different category"
    elif fault == "moved":
        source.ids.remove("101")
    else:
        source.partial_folder = True
    try:
        result = apply(name)
        assert result["blocked"]
    except PilotError:
        pass
    assert not source.deletes
    assert not list(store.root.glob("*.intent.private.json"))


@pytest.mark.parametrize("ids", [[], ["101"] * 2, ["101", "102", "103", "104"], [101]])
def test_plan_bounds_before_remote_access(migration, ids):
    store, drive, server, _, _, archived, _, _ = migration
    count = len(server.calls)
    with pytest.raises(PilotError):
        make_plan(store, drive, archived["private_receipt"], ids, "100", acceptance_confirmed=True)
    assert len(server.calls) == count


def test_incomplete_item_does_not_prevent_other_verified_item(migration):
    store, drive, _, source, _, _, _, apply = migration
    inv = inventory(store, [post("101", article={"title": "not full body"}), post("102")])
    with httpx.Client(transport=httpx.MockTransport(media_http)) as media:
        archive = archive_batch(inv, drive, media)
    result = make_plan(
        store, drive, archive["private_receipt"], ["101", "102"], "100", acceptance_confirmed=True
    )
    assert result["blocked"] == 1 and result["verified"] == 1
    applied = apply(result["private_plan"])
    assert applied["states"] == {"blocked": 1, "unbookmarked": 1}
    assert len(source.deletes) == 1 and source.deletes[0].url.path.endswith("/102")


@pytest.mark.parametrize(
    "body,status",
    [
        ({"data": {"bookmarked": True}}, 200),
        ({"data": {"bookmarked": 0}}, 200),
        ({"data": {"bookmarked": False}, "errors": [{}]}, 200),
        ({"data": {"bookmarked": False}}, 500),
        ({}, 204),
        ("not json", 200),
        ([], 200),
    ],
)
def test_ambiguous_response_never_retried_or_inferred_from_absence(migration, body, status):
    store, _, _, source, client, _, plan, apply = migration
    name = plan(["101", "102"])
    source.body, source.status = body, status
    result = apply(name)
    assert result["stop_reason"] == "unbookmark_outcome_unknown"
    assert len(source.deletes) == 1
    source.body, source.status = {"data": {"bookmarked": False}}, 200
    recovered = reconcile_plan(store, client, Budget(store, ".1", "10", "100"), name, "100")
    assert recovered["states"]["unbookmark_outcome_unknown"] == 1
    assert len(source.deletes) == 1
    # A new plan for that ID cannot evade the durable attempt guard.
    another = plan(["101"])
    assert apply(another, retry_rejected=True)["states"] == {"unbookmark_outcome_unknown": 1}
    assert len(source.deletes) == 1


def test_timeout_after_success_stays_unknown_and_never_redeletes(migration):
    store, _, _, source, client, _, plan, apply = migration
    name = plan()
    source.mutate_then_timeout = True
    result = apply(name)
    assert result["stop_reason"] == "delete_transport_unknown"
    assert "101" not in source.ids
    source.mutate_then_timeout = False
    apply(name, retry_rejected=True)
    recovered = reconcile_plan(store, client, Budget(store, ".1", "10", "100"), name, "100")
    assert recovered["states"] == {"unbookmark_outcome_unknown": 1}
    assert len(source.deletes) == 1


@pytest.mark.parametrize("stage", ["intent", "response", "result"])
def test_disk_failure_at_each_transaction_boundary(migration, monkeypatch, stage):
    store, _, _, source, client, _, plan, apply = migration
    name = plan()
    original = store.create

    def fail(filename, value):
        if filename.endswith("." + stage + ".private.json"):
            raise OSError("disk unavailable")
        return original(filename, value)

    monkeypatch.setattr(store, "create", fail)
    with pytest.raises(OSError):
        apply(name)
    assert len(source.deletes) == (0 if stage == "intent" else 1)
    monkeypatch.setattr(store, "create", original)
    result = reconcile_plan(store, client, Budget(store, ".1", "10", "100"), name, "100")
    expected = {
        "intent": "not_attempted",
        "response": "unbookmark_outcome_unknown",
        "result": "unbookmarked",
    }[stage]
    assert result["states"] == {expected: 1}
    assert len(source.deletes) == (0 if stage == "intent" else 1)


def test_process_death_after_intent_before_request_is_conservatively_unknown(
    migration, monkeypatch
):
    store, _, _, source, _, _, plan, apply = migration
    name = plan()
    original = store.create

    def crash(filename, value):
        original(filename, value)
        if filename.endswith(".intent.private.json"):
            raise SystemExit("process death")

    monkeypatch.setattr(store, "create", crash)
    with pytest.raises(SystemExit):
        apply(name)
    monkeypatch.setattr(store, "create", original)
    assert apply(name)["states"] == {"unbookmark_outcome_unknown": 1}
    assert not source.deletes


@pytest.mark.parametrize("status", [400, 401, 403, 429])
def test_definite_rejection_needs_explicit_retry_and_new_validation(migration, status):
    store, _, _, source, _, _, plan, apply = migration
    name = plan()
    source.status = status
    assert apply(name)["states"] == {"unbookmark_rejected": 1}
    apply(name)
    assert len(source.deletes) == 1
    source.status = 200
    assert apply(name, retry_rejected=True)["states"] == {"unbookmarked": 1}
    assert len(source.deletes) == 2
    _, count = latest_attempt(store, "100", "101")
    assert count == 2 and len(list(store.root.glob("*.intent.private.json"))) == 2


@pytest.mark.parametrize("cap", [".3", ".4"])
def test_reserve_delete_and_followup_before_intent(migration, cap):
    store, drive, _, source, client, _, plan, _ = migration
    result = apply_plan(
        store, drive, client, Budget(store, ".1", cap, "100"), plan(), "100", confirmed=True
    )
    assert result["stop_reason"] == "spend_cap"
    assert not source.deletes and not list(store.root.glob("*.intent.private.json"))


def test_request_bound_protects_followup_before_delete(migration):
    store, _, _, source, _, _, plan, apply = migration
    result = apply(plan(), max_calls=4)
    assert result["stop_reason"] == "request_limit"
    assert not source.deletes and not list(store.root.glob("*.intent.private.json"))


@pytest.mark.parametrize("mode", ["stagnant", "empty", "after_failure"])
def test_no_more_deletion_when_window_does_not_prove_progress(migration, mode):
    store, _, _, source, client, _, plan, apply = migration
    name = plan(["101", "102"])
    if mode == "stagnant":
        source.stagnant = True
    elif mode == "empty":
        source.ids = ["101"]
    else:
        source.after_failure = True
    result = apply(name)
    assert result["states"] == {"unbookmarked": 1} and result["blocked"]
    assert len(source.deletes) == 1
    if mode == "after_failure":
        apply(name)
        assert len(source.deletes) == 1
        source.after_failure = False
        restored = reconcile_plan(store, client, Budget(store, ".1", "10", "100"), name, "100")
        assert restored["states"]["unbookmarked"] == 1
        assert len(source.deletes) == 1
    else:
        apply(name)
        assert len(source.deletes) == 1
        restored = reconcile_plan(store, client, Budget(store, ".1", "10", "100"), name, "100")
        assert restored["blocked"]


def test_immutable_record_refuses_replacement(migration):
    store = migration[0]
    store.create("immutable.private.json", {"value": 1})
    with pytest.raises(FileExistsError):
        store.create("immutable.private.json", {"value": 2})
    assert store.read("immutable.private.json") == {"value": 1}


def test_write_consent_is_explicit_and_refresh_preserves_exact_scope(migration):
    store = migration[0]
    assert "bookmark.write" not in auth_start(store, "app")
    url = auth_start(store, "app", allow_unbookmark=True)
    query = parse_qs(urlsplit(url).query)
    assert set(query["scope"][0].split()) == WRITE_SCOPES
    calls = []

    def handle(req):
        calls.append(req)
        return httpx.Response(
            200,
            json={
                "access_token": "NEW",
                "refresh_token": "ROTATED",
                "scope": " ".join(WRITE_SCOPES),
                "expires_in": 7200,
                "token_type": "bearer",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        client = Client(store, http, "app")
        client.finish("http://127.0.0.1:8765/callback?code=SECRET&state=" + query["state"][0])
        token = store.read("tokens.private.json")
        token["expires_at"] = 1
        store.write("tokens.private.json", token)
        assert client.access_token(write=True) == "NEW"
    assert (
        len(calls) == 2 and set(store.read("tokens.private.json")["scope"].split()) == WRITE_SCOPES
    )


def test_cli_apply_and_reconcile_with_env_defaults(migration, monkeypatch, capsys):
    store, _, server, source, _, _, plan, _ = migration
    name = plan()
    monkeypatch.setenv("X_APP_OWNER_ID", "100")
    monkeypatch.setenv("X_CLIENT_ID", "app")
    monkeypatch.setenv("X_ARCHIVE_DRIVE_FOLDER_ID", "root")
    monkeypatch.setenv("X_PER_CALL_RESERVE_USD", ".1")
    monkeypatch.setenv("X_RUN_CAP_USD", "10")
    monkeypatch.setenv("X_MONTH_CAP_USD", "100")
    actual = httpx.Client

    def route(req):
        return source.handle(req) if req.url.host == "api.x.com" else server.handle(req)

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: actual(transport=httpx.MockTransport(route), **kw)
    )
    base = ["x-migrator", "--state-dir", str(store.root)]
    flags = ["--plan-file", name, "--console-hard-cap-confirmed", "--auto-recharge-off-confirmed"]
    monkeypatch.setattr(sys, "argv", base + ["unbookmark-apply"] + flags)
    assert cli.main() == 1 and not source.calls
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", base + ["unbookmark-apply", "--confirm-unbookmark"] + flags)
    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["states"] == {"unbookmarked": 1}
    monkeypatch.setattr(sys, "argv", base + ["unbookmark-reconcile"] + flags)
    assert cli.main() == 0 and len(source.deletes) == 1


def test_manifest_hash_mismatch_cannot_be_promoted_by_success_label(migration):
    store, drive, server, _, _, archived, _, _ = migration
    receipt = store.read(archived["private_receipt"])
    server.bytes[receipt["manifest"]["file_id"]] = encode({"archive_status": "bytes_verified"})
    assert digest(server.bytes[receipt["manifest"]["file_id"]]) != receipt["manifest"]["sha256"]
    with pytest.raises(PilotError):
        make_plan(
            store, drive, archived["private_receipt"], ["101"], "100", acceptance_confirmed=True
        )


def test_missing_asset_stays_blocked_even_with_internally_consistent_hashes(migration):
    store, drive, server, source, _, archived, _, _ = migration
    receipt = store.read(archived["private_receipt"])
    anchor = receipt["manifest"]
    rows = [json.loads(line) for line in server.bytes[anchor["file_id"]].decode().splitlines()]
    saved = rows[0]["payload"]
    payload = json.loads(server.bytes[saved["file_id"]])
    payload["media"] = []
    raw = encode(payload)
    server.bytes[saved["file_id"]] = raw
    saved.update(sha256=digest(raw), size_bytes=len(raw))
    raw_manifest = b"".join(encode(row) for row in rows)
    server.bytes[anchor["file_id"]] = raw_manifest
    anchor.update(sha256=digest(raw_manifest), size_bytes=len(raw_manifest))
    store.write(archived["private_receipt"], receipt)
    with pytest.raises(PilotError, match="media missing"):
        make_plan(
            store, drive, archived["private_receipt"], ["101"], "100", acceptance_confirmed=True
        )
    assert not source.deletes


@pytest.mark.parametrize("category", ["conflict", "unknown"])
def test_category_problem_never_creates_eligible_plan(migration, category):
    store, drive, _, source, _, _, _, _ = migration
    inv = inventory(store)
    if category == "unknown":
        inv.state["posts"]["101"]["folder_ids"] = []
    else:
        inv.state["folders"]["20"] = {"id": "20", "names": ["Second synthetic folder"]}
        inv.state["posts"]["101"]["folder_ids"].append("20")
    inv.save()
    with httpx.Client(transport=httpx.MockTransport(media_http)) as media:
        archived = archive_batch(inv, drive, media)
    result = make_plan(
        store, drive, archived["private_receipt"], ["101"], "100", acceptance_confirmed=True
    )
    assert result["blocked"] == 1 and result["verified"] == 0
    assert not source.calls


def test_plan_requires_runtime_attestation_before_network(migration):
    store, drive, server, _, _, archived, _, _ = migration
    count = len(server.calls)
    with pytest.raises(PilotError, match="acceptance"):
        make_plan(store, drive, archived["private_receipt"], ["101"], "100")
    assert len(server.calls) == count


def test_read_consent_cannot_silently_install_write_grant(migration):
    store = migration[0]
    url = auth_start(store, "app")
    state = parse_qs(urlsplit(url).query)["state"][0]
    prior = store.read("tokens.private.json")
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                json={
                    "access_token": "NEW",
                    "refresh_token": "NEW",
                    "token_type": "bearer",
                    "expires_in": 7200,
                    "scope": " ".join(WRITE_SCOPES),
                },
            )
        )
    ) as http:
        with pytest.raises(PilotError, match="requested scopes"):
            Client(store, http, "app").finish(
                "http://127.0.0.1:8765/callback?code=SECRET&state=" + state
            )
    assert store.read("tokens.private.json") == prior


def test_comparison_partitions_observations_without_claiming_bookmark_age():
    from x_bookmark_migrator.unbookmark import compare_window

    before = {"ids": ["101", "102"]}
    after = {"ids": ["102", "103", "104"]}
    comparison = compare_window(before, after, "101", ["101", "102", "103"])
    assert comparison["newly_observed_ids"] == ["103", "104"]
    assert comparison["previously_inventoried_ids"] == ["103"]
    assert comparison["not_previously_inventoried_ids"] == ["104"]
    assert comparison["inventory_baseline_available"]
    assert comparison["older_bookmarks_exposed"] == "unverified"
    legacy = compare_window(before, after, "101")
    assert not legacy["inventory_baseline_available"]
    assert legacy["previously_inventoried_ids"] == []
    assert legacy["not_previously_inventoried_ids"] == ["103", "104"]


def test_delayed_reconcile_keeps_original_baseline_and_cannot_resume_deletion(migration):
    from x_bookmark_migrator.inventory import Inventory

    store, _, _, source, client, _, plan, apply = migration
    name = plan(["101", "102"])
    source.after_failure = True
    assert apply(name)["stop_reason"] == "after_window_unverified"
    base, _ = latest_attempt(store, "100", "101")
    assert store.read(base + ".intent.private.json")["inventory_post_ids"] == ["101", "102", "103"]
    # Simulate later inventory learning an ID and an unrelated save displacing the
    # original first window. Recovery must use the intent's frozen baseline.
    inv = Inventory.load(store)
    inv.state["posts"]["104"] = {"id": "104"}
    inv.save()
    source.ids = ["102", "104", "107"]
    source.after_failure = False
    result = reconcile_plan(store, client, Budget(store, ".1", "10", "100"), name, "100")
    assert result["states"] == {"unbookmarked": 1, "not_attempted": 1}
    comparison = store.read(base + ".window.private.json")
    assert comparison["reconciled_later"]
    assert comparison["previously_inventoried_ids"] == []
    assert comparison["not_previously_inventoried_ids"] == ["104", "107"]
    assert apply(name)["stop_reason"] == "reconciliation_required"
    assert len(source.deletes) == 1


@pytest.mark.parametrize("fault", ["partial", "schema", "transport"])
def test_reconcile_records_failed_window_and_continues_other_receipts(migration, fault):
    store, _, _, source, _, _, plan, apply = migration
    name = plan(["101", "102", "103"])
    assert not apply(name)["blocked"]
    for pid in ("102", "103"):
        base, _ = latest_attempt(store, "100", pid)
        store.path(base + ".result.private.json").unlink()
    reads = []

    def handle(req):
        if req.url.path.endswith("/folders/10"):
            reads.append(req)
            if len(reads) == 1:
                if fault == "transport":
                    raise httpx.ReadTimeout("PRIVATE", request=req)
                body = {"data": [{"id": "104"}], "errors": [{"detail": "PRIVATE"}]}
                if fault == "schema":
                    body = {"data": "PRIVATE"}
                return httpx.Response(200, json=body)
        return source.handle(req)

    with httpx.Client(transport=httpx.MockTransport(handle)) as http:
        result = reconcile_plan(
            store, Client(store, http, "app"), Budget(store, ".1", "10", "100"), name, "100"
        )
    assert result["states"] == {"unbookmarked": 3} and result["blocked"]
    report = store.read(result["private_report"])
    outcomes = report["outcomes"]
    assert outcomes[0]["window_status"] == "unverified"
    assert (
        outcomes[0]["window_error"]
        == {
            "partial": "blocked_folder_window",
            "schema": "schema_error",
            "transport": "transport_error",
        }[fault]
    )
    assert all(o["window_status"] == "new_ids_observed" for o in outcomes[1:])
    assert len(reads) == 3 and len(source.deletes) == 3
    for pid in ("102", "103"):
        base, _ = latest_attempt(store, "100", pid)
        assert store.read(base + ".result.private.json")["state"] == "unbookmarked"
    assert result["reservations_by_operation"] == {"identity": 1, "reconcile_window": 3}
    assert "PRIVATE" not in json.dumps(report) and "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("limit", ["requests", "spend", "rate"])
def test_reconcile_recovers_all_receipts_without_exceeding_read_limits(migration, limit):
    store, _, _, source, client, _, plan, apply = migration
    name = plan(["101", "102", "103"])
    apply(name)
    count = len(source.calls)
    if limit == "rate":
        source.after_failure = True
    result = reconcile_plan(
        store,
        client,
        Budget(store, ".1", ".1" if limit == "spend" else "10", "100"),
        name,
        "100",
        max_calls=1 if limit == "requests" else 20,
    )
    assert result["states"] == {"unbookmarked": 3}
    assert (
        result["stop_reason"]
        == {"requests": "request_limit", "spend": "spend_cap", "rate": "http_429"}[limit]
    )
    assert len(source.calls) - count == (2 if limit == "rate" else 1)
    assert all(
        o["window_status"] == "unverified" for o in store.read(result["private_report"])["outcomes"]
    )
    assert len(source.deletes) == 3


@pytest.mark.parametrize("data", [None, [], "PRIVATE", 7])
def test_malformed_identity_is_safe_error_before_mutation(migration, data):
    store, drive, _, source, _, _, plan, _ = migration
    name = plan()
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"data": data}))
    ) as http:
        with pytest.raises(PilotError, match="owner differs"):
            apply_plan(
                store,
                drive,
                Client(store, http, "app"),
                Budget(store, ".1", "10", "100"),
                name,
                "100",
                confirmed=True,
            )
    assert not source.deletes and not list(store.root.glob("*.intent.private.json"))


def test_interrupted_immutable_publication_leaves_private_temporary_file(migration, monkeypatch):
    import os

    store = migration[0]
    original = os.unlink

    def interrupted(path):
        if str(path).endswith(".private.tmp"):
            raise OSError("simulated process interruption before cleanup")
        return original(path)

    monkeypatch.setattr(os, "unlink", interrupted)
    with pytest.raises(OSError):
        store.create("receipt.private.json", {"synthetic": True})
    pending = list(store.root.glob("pending-*.private.tmp"))
    assert len(pending) == 1 and pending[0].stat().st_mode & 0o777 == 0o600
    assert store.read("receipt.private.json") == {"synthetic": True}
    monkeypatch.setattr(os, "unlink", original)
    pending[0].unlink()


@pytest.mark.parametrize("size", [101, 10001])
def test_folder_window_preserves_overreturn_with_defensive_ceiling(migration, size):
    from x_bookmark_migrator.unbookmark import Reads, window

    store = migration[0]
    body = {"data": [{"id": str(i + 1)} for i in range(size)]}
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    ) as http:
        reads = Reads(Client(store, http, "app"), Budget(store, ".1", "10", "100"), 1)
        if size > 10000:
            with pytest.raises(PilotError, match="Ambiguous folder window"):
                window(reads, "100", "10", "before_window")
        else:
            result = window(reads, "100", "10", "before_window")
            assert len(result["ids"]) == size and not result["all_history_complete"]
