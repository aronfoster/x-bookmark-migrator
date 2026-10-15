"""Synthetic official API responses only; no account-derived fixtures."""

import json
import time
from decimal import Decimal

import httpx
import pytest

from x_bookmark_migrator.core import SCOPES, Budget, Client, PilotError, Store
from x_bookmark_migrator.inventory import Inventory

OWNER = "1000000000000000001"
A, B, C, D = [str(1900000000000000000 + i) for i in range(1, 5)]
BASE = f"/2/users/{OWNER}/bookmarks"


def page(data, token=None, **extra):
    return {
        "data": data,
        "meta": {"result_count": len(data), **({"next_token": token} if token else {})},
        **extra,
    }


def post(pid):
    return {
        "id": pid,
        "text": "synthetic caption",
        "created_at": "2020-01-01T00:00:00Z",
        "note_post": {"text": "synthetic long content " * 30},
    }


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / "private")
    store.write(
        "tokens.private.json",
        dict(
            access_token="SECRET",
            refresh_token="SECRET",
            scope=" ".join(SCOPES),
            client_id="app",
            expires_at=time.time() + 3600,
        ),
    )
    calls = []

    def run(inventory, handler, max_calls=50, cap="50", **kwargs):
        def handle(req):
            calls.append(req)
            assert req.method == "GET" and req.url.host == "api.x.com"
            if req.url.path == "/2/users/me":
                return httpx.Response(200, json={"data": {"id": OWNER}})
            result = handler(req)
            return (
                result if isinstance(result, httpx.Response) else httpx.Response(200, json=result)
            )

        with httpx.Client(transport=httpx.MockTransport(handle)) as http:
            return inventory.run(
                Client(store, http, "app"),
                Budget(store, ".1", cap, "100"),
                max_calls=max_calls,
                **kwargs,
            )

    return store, calls, run


def example(req):
    path, token = req.url.path, req.url.params.get("pagination_token")
    if path == BASE + "/folders":
        return page([{"id": "10", "name": "Malfoid"}, {"id": "20", "name": "Culture War"}])
    if path == BASE:
        return page([post(A), post(D)], "main-next") if not token else page([post(B)])
    if path == BASE + "/folders/10":
        assert set(req.url.params) <= {"max_results", "pagination_token"}
        return page([{"id": A}], "folder-next") if not token else page([{"id": C}])
    if path == BASE + "/folders/20":
        return page([{"id": A}, {"id": B}])
    assert path == "/2/tweets"
    assert "max_results" not in req.url.params and "note_post" in req.url.params["post.fields"]
    return {
        "data": [post(pid) for pid in req.url.params["ids"].split(",")],
        "includes": {"media": [{"media_key": "synthetic"}]},
    }


def read_export(store, summary):
    return [
        json.loads(line)
        for line in (store.root / summary["private_export"]).read_text().splitlines()
    ]


def associations(rows):
    return {
        r["id"]: (r["folder_ids"], r["membership_status"], r["post"])
        for r in rows
        if r["type"] == "post"
    }


def test_two_page_restart_named_folders_and_portable_export(setup):
    store, calls, run = setup
    first = Inventory(store, OWNER)
    bounded = run(first, example, max_calls=3)
    assert bounded["stop_reason"] == "request_limit"
    assert bounded["membership_counts"]["membership_unknown"] == 2
    assert first.state["streams"]["main"]["next_token"] == "main-next"
    resumed = Inventory(store, OWNER, resume=True, source_unchanged=True)
    summary = run(resumed, example)
    rows = read_export(store, summary)
    assert summary["inventory_status"] == "observed_end"
    assert summary["records"] == 4 and summary["folders"] == 2
    assert summary["membership_counts"] == {
        "filed": 3,
        "confirmed_unfiled": 1,
        "membership_unknown": 0,
    }
    records = {r["id"]: r for r in rows if r["type"] == "post"}
    assert records[A]["folders"] == [
        {"id": "10", "names": ["Malfoid"]},
        {"id": "20", "names": ["Culture War"]},
    ]
    assert records[C]["includes"]["media"][0]["media_key"] == "synthetic"
    assert records[C]["bookmark_time"] is None
    assert all(not r["deletion_eligible"] for r in records.values())
    assert all(isinstance(r["id"], str) for r in records.values())
    assert (
        len([r for r in calls if r.url.path == BASE and not r.url.params.get("pagination_token")])
        == 1
    )
    full = run(Inventory(store, OWNER), example)
    assert associations(rows) == associations(read_export(store, full))
    assert not summary["all_history_complete"]
    assert (store.root / summary["private_export"]).stat().st_mode & 0o777 == 0o600
    assert (store.root / summary["private_checkpoint"]).stat().st_mode & 0o777 == 0o600
    stream_row = next(r for r in rows if r["type"] == "stream" and r["key"] == "main")
    assert stream_row["raw_count"] == stream_row["unique_count"] == 3
    assert stream_row["pages"][1]["request_token"] == "main-next"


@pytest.mark.parametrize("failure", [403, 429, "partial", "no_meta", "bad_id"])
def test_failed_folder_never_proves_unfiled(setup, failure):
    store, calls, run = setup

    def handle(req):
        if req.url.path.endswith("/folders/10"):
            if isinstance(failure, int):
                return httpx.Response(failure, text="PRIVATE")
            if failure == "partial":
                return page([{"id": A}], errors=[{"detail": "PRIVATE"}])
            if failure == "bad_id":
                return page([{"id": 1900000000000000001}])
            return {"data": [{"id": A}]}
        return example(req)

    inv = Inventory(store, OWNER)
    summary = run(inv, handle)
    rows = read_export(store, summary)
    assert summary["inventory_status"] == "partial"
    assert not summary["all_folders_exhausted_in_pass"]
    assert summary["membership_counts"]["confirmed_unfiled"] == 0
    assert (
        next(r for r in rows if r["type"] == "post" and r["id"] == D)["membership_status"]
        == "membership_unknown"
    )
    assert "PRIVATE" not in json.dumps(summary)
    assert all(not r["category_evidence_complete"] for r in rows if r["type"] == "post")
    if failure == 403:
        assert any(r.url.path.endswith("/folders/20") for r in calls)
    if failure == 429:
        assert calls[-1].url.path.endswith("/folders/10")


def test_resume_requires_attestation_and_new_pass_drops_stale_cursor(setup):
    store, calls, run = setup
    inv = Inventory(store, OWNER)
    run(inv, example, max_calls=3)
    with pytest.raises(PilotError, match="source-unchanged"):
        Inventory(store, OWNER, resume=True)
    with pytest.raises(PilotError, match="owner"):
        Inventory(store, "2", resume=True, source_unchanged=True)
    fresh = Inventory(store, OWNER)
    assert fresh.state["pass_id"] != inv.state["pass_id"]
    assert fresh.state["posts"] == {} and fresh.state["streams"]["main"]["next_token"] is None
    assert store.read(inv.name)["streams"]["main"]["next_token"] == "main-next"


def test_shifted_pages_deduped_and_consistency_not_claimed(setup):
    store, _, run = setup

    def shifted(req):
        if req.url.path == BASE and req.url.params.get("pagination_token"):
            return page([post(A), post(B)])  # a newly inserted save shifted the page boundary
        return example(req)

    inv = Inventory(store, OWNER)
    summary = run(inv, shifted)
    assert len(inv.state["posts"]) == 4
    assert summary["inventory_status"] == "partial"
    assert not summary["all_folders_exhausted_in_pass"]
    assert inv.state["streams"]["main"]["pages"][1]["duplicate_count"] == 1
    assert inv.membership(inv.state["posts"][D]) == "membership_unknown"


def test_pagination_cycle_bounded_and_short_page_follows_token(setup):
    store, calls, run = setup

    def cyclic(req):
        if req.url.path.endswith("/folders/10"):
            return page([{"id": A}], "same")
        return example(req)

    inv = Inventory(store, OWNER)
    summary = run(inv, cyclic)
    assert inv.state["streams"]["folder:10"]["reason"] == "pagination_cycle"
    assert len([r for r in calls if r.url.path.endswith("/folders/10")]) == 2
    assert summary["inventory_status"] == "partial"


def test_hydration_tombstone_and_unknown_do_not_repeat_on_resume(setup):
    store, calls, run = setup

    def unavailable(req):
        if req.url.path == "/2/tweets":
            return {
                "errors": [
                    {
                        "type": "https://api.x.com/2/problems/resource-not-found",
                        "resource_type": "tweet",
                        "resource_id": C,
                        "detail": "synthetic unavailable post",
                    }
                ]
            }
        return example(req)

    inv = Inventory(store, OWNER)
    summary = run(inv, unavailable)
    assert summary["hydration_counts"]["blocked_unavailable_source"] == 1
    assert inv.state["posts"][C]["errors"][0]["resource_id"] == C
    run(Inventory(store, OWNER, resume=True, source_unchanged=True), unavailable)
    assert len([r for r in calls if r.url.path == "/2/tweets"]) == 1
    assert not inv.state["posts"][C]["post"]


def test_lookup_missing_ids_and_unknown_errors_stay_unresolved(setup):
    store, _, run = setup

    def missing(req):
        if req.url.path == "/2/tweets":
            return {"data": [], "errors": [{"resource_id": C, "type": "unknown"}]}
        return example(req)

    inv = Inventory(store, OWNER)
    summary = run(inv, missing)
    assert inv.state["posts"][C]["hydration_status"] == "unresolved"
    assert summary["inventory_status"] == "partial"


@pytest.mark.parametrize("kind", ["spend", "pages", "records"])
def test_caps_preserve_checkpoint_and_prevent_false_completion(setup, kind):
    store, calls, run = setup
    inv = Inventory(
        store,
        OWNER,
        max_pages=1 if kind == "pages" else 100,
        max_records=1 if kind == "records" else 10000,
    )
    summary = run(inv, example, cap=".25" if kind == "spend" else "50")
    assert summary["inventory_status"] == "partial"
    assert not summary["all_history_complete"]
    if kind == "spend":
        assert len(calls) == 2 and Decimal(summary["estimated_reserved_usd"]) == Decimal(".2")
        assert summary["stop_reason"] == "spend_cap"
    if kind == "pages":
        assert inv.state["streams"]["main"]["reason"] == "page_limit"
        assert inv.state["streams"]["main"]["next_token"] == "main-next"
    if kind == "records":
        assert len(inv.state["posts"]) <= 1
        assert inv.state["streams"]["main"]["reason"] == "record_limit"


def test_interrupted_request_never_proves_negative_membership(setup):
    store, _, run = setup
    inv = Inventory(store, OWNER)
    run(inv, example, max_calls=3)
    inv.state["in_flight"] = True  # simulate process death between GET and checkpoint commit
    inv.save()
    resumed = Inventory(store, OWNER, resume=True, source_unchanged=True)
    summary = run(resumed, example)
    assert summary["inventory_status"] == "partial"
    assert not summary["all_folders_exhausted_in_pass"]
    assert resumed.state["continuity_notes"]


def test_http_failure_is_explicitly_resumable_without_losing_cursor(setup):
    store, _, run = setup

    def limited(req):
        if req.url.path == BASE and req.url.params.get("pagination_token"):
            return httpx.Response(429, json={"detail": "PRIVATE"})
        return example(req)

    inv = Inventory(store, OWNER)
    summary = run(inv, limited)
    assert summary["stop_reason"] == "http_429"
    resumed = Inventory(store, OWNER, resume=True, source_unchanged=True)
    assert resumed.state["streams"]["main"]["next_token"] == "main-next"
    assert run(resumed, example)["records"] == 4


def test_export_offline_create_only_and_checkpoint_unchanged(setup):
    store, calls, run = setup
    summary = run(Inventory(store, OWNER), example)
    before_calls = len(calls)
    before = (store.root / summary["private_checkpoint"]).read_bytes()
    offline = Inventory.load(store)
    first, second = offline.export(), offline.export()
    assert first != second
    assert (store.root / first).read_bytes() == (store.root / second).read_bytes()
    assert (store.root / summary["private_checkpoint"]).read_bytes() == before
    assert len(calls) == before_calls


def test_unavailable_cache_crosses_fresh_passes_with_explicit_retry(setup):
    store, calls, run = setup

    def gone(req):
        if req.url.path == "/2/tweets":
            return {
                "errors": [
                    {
                        "type": "https://api.x.com/2/problems/resource-not-found",
                        "resource_type": "tweet",
                        "resource_id": C,
                    }
                ]
            }
        return example(req)

    run(Inventory(store, OWNER), gone)
    fresh = Inventory(store, OWNER)
    summary = run(fresh, example)
    assert summary["hydration_counts"]["blocked_unavailable_source"] == 1
    assert len([r for r in calls if r.url.path == "/2/tweets"]) == 1
    retried = Inventory(store, OWNER, retry_unavailable=True)
    assert run(retried, example)["hydration_counts"] == {"read": 4}
    assert len([r for r in calls if r.url.path == "/2/tweets"]) == 2


def test_folder_list_paginates_and_preserves_renames_and_unicode(setup):
    store, _, run = setup

    def folders(req):
        if req.url.path == BASE + "/folders":
            if not req.url.params.get("pagination_token"):
                return page([{"id": "10", "name": "原名/../Malfoid"}], "folders-next")
            return page([{"id": "10", "name": "Renamed"}, {"id": "20", "name": "Culture War"}])
        return example(req)

    inv = Inventory(store, OWNER)
    summary = run(inv, folders)
    assert inv.state["folders"]["10"]["names"] == ["原名/../Malfoid", "Renamed"]
    assert summary["inventory_status"] == "partial"
    assert summary["membership_counts"]["confirmed_unfiled"] == 0
    assert "原名" not in json.dumps(summary, ensure_ascii=False)


def test_lookup_batches_capped_and_successes_survive_unavailable_peer(setup):
    store, calls, run = setup

    def mixed(req):
        if req.url.path == BASE:
            return page([post(D)])
        if req.url.path == "/2/tweets":
            ids = req.url.params["ids"].split(",")
            return {
                "data": [post(pid) for pid in ids if pid != B],
                "errors": [
                    {
                        "type": "https://api.x.com/2/problems/not-authorized-for-resource",
                        "resource_type": "tweet",
                        "value": B,
                    }
                ]
                if B in ids
                else [],
            }
        return example(req)

    inv = Inventory(store, OWNER)
    summary = run(inv, mixed, hydrate_batch_size=2)
    assert summary["hydration_counts"] == {"read": 3, "blocked_unavailable_source": 1}
    lookups = [r for r in calls if r.url.path == "/2/tweets"]
    assert len(lookups) == 2
    assert all(len(r.url.params["ids"].split(",")) <= 2 for r in lookups)
    assert summary["reservations_by_operation"]["hydrate"]["requests"] == 2


def test_export_does_not_overwrite_or_delete_existing_file(setup, monkeypatch):
    store, _, run = setup
    inv = Inventory(store, OWNER)
    run(inv, example)
    monkeypatch.setattr("x_bookmark_migrator.inventory.secrets.token_hex", lambda n: "collision")
    name = inv.export()
    before = (store.root / name).read_bytes()
    with pytest.raises(FileExistsError):
        inv.export()
    assert (store.root / name).read_bytes() == before


def test_identity_failure_on_completed_resume_is_not_success(setup):
    store, _, run = setup
    run(Inventory(store, OWNER), example)
    inv = Inventory(store, OWNER, resume=True, source_unchanged=True)
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"data": {"id": "2"}}))
    ) as http:
        summary = inv.run(Client(store, http, "app"), Budget(store, ".1", "1", "100"))
    assert summary["inventory_status"] == "partial"
    assert summary["stop_reason"] == "identity"


def test_transport_failure_preserves_evidence_and_blocks_negative_claims(setup):
    store, _, run = setup

    def broken(req):
        if req.url.path == BASE and req.url.params.get("pagination_token"):
            raise httpx.ReadTimeout("PRIVATE transport detail", request=req)
        return example(req)

    inv = Inventory(store, OWNER)
    summary = run(inv, broken)
    assert summary["stop_reason"] == "transport_error"
    assert "PRIVATE" not in json.dumps(summary)
    assert inv.state["streams"]["main"]["next_token"] == "main-next"
    resumed = run(Inventory(store, OWNER, resume=True, source_unchanged=True), example)
    assert resumed["records"] == 4 and not resumed["all_folders_exhausted_in_pass"]
