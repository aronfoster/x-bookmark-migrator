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
    expansions = {r["evidence"]: r["includes"] for r in rows if r["type"] == "expansions"}
    assert expansions[records[C]["expansions_ref"]]["media"][0]["media_key"] == "synthetic"
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


def expansion_error(pid="99", parameter="referenced_posts"):
    return {
        "type": "https://api.x.com/2/problems/resource-not-found",
        "resource_id": pid,
        "resource_type": "tweet",
        "parameter": parameter,
    }


@pytest.mark.parametrize(
    "field,parameter",
    [("referenced_posts", "referenced_posts"), ("referenced_tweets", "referenced_tweets.id")],
)
def test_missing_reference_only_marks_affected_post_and_main_keeps_paging(setup, field, parameter):
    store, calls, run = setup

    def quoted(req):
        if req.url.path == BASE and not req.url.params.get("pagination_token"):
            return page(
                [{**post(A), field: [{"id": "99", "type": "quoted"}]}, post(D)],
                "main-next",
                errors=[expansion_error(parameter=parameter)],
            )
        return example(req)

    inv = Inventory(store, OWNER)
    summary = run(inv, quoted)
    assert inv.state["streams"]["main"]["status"] == "exhausted"
    assert any(r.url.params.get("pagination_token") == "main-next" for r in calls)
    assert summary["records"] == 4
    assert inv.state["posts"][A]["hydration_status"] == "partial"
    assert inv.state["posts"][D]["hydration_status"] == "read"
    assert inv.state["posts"][D]["errors"] == []
    assert inv.state["posts"][B]["hydration_status"] == "read"
    assert summary["all_folders_exhausted_in_pass"]  # content failure is not a category gap
    assert summary["inventory_status"] == "partial"  # affected content still needs work


@pytest.mark.parametrize(
    "error",
    [
        expansion_error("98"),  # no observed reference association
        {**expansion_error(), "parameter": "ids"},
        {**expansion_error(), "type": "unknown"},
        expansion_error(A),
    ],
)
def test_ambiguous_or_top_level_errors_still_block_main(setup, error):
    store, calls, run = setup

    def ambiguous(req):
        if req.url.path == BASE:
            return page(
                [{**post(A), "referenced_posts": [{"id": "99"}]}], "main-next", errors=[error]
            )
        return example(req)

    inv = Inventory(store, OWNER)
    run(inv, ambiguous)
    assert inv.state["streams"]["main"]["reason"] == "partial_errors"
    assert not any(r.url.params.get("pagination_token") == "main-next" for r in calls)


def test_hydration_scopes_nested_reference_failure_and_keeps_unaffected_records(setup):
    store, _, run = setup

    def mixed(req):
        if req.url.path == BASE:
            return page([post(D)])
        if req.url.path == "/2/tweets":
            ids = req.url.params["ids"].split(",")
            return {
                "data": [
                    {**post(pid), **({"referenced_posts": [{"id": "98"}]} if pid == A else {})}
                    for pid in ids
                ],
                "includes": {"posts": [{"id": "98", "referenced_posts": [{"id": "99"}]}]},
                "errors": [expansion_error()],
            }
        return example(req)

    inv = Inventory(store, OWNER)
    summary = run(inv, mixed)
    assert summary["hydration_counts"] == {"read": 3, "partial": 1}
    assert inv.state["posts"][A]["errors"] == [expansion_error()]
    assert inv.state["posts"][B]["errors"] == inv.state["posts"][C]["errors"] == []
    assert inv.state["hydration_batches"][0]["errors"] == [expansion_error()]


def test_expansions_stored_once_per_response_in_checkpoint_and_export(setup):
    store, _, run = setup
    marker = "SYNTHETIC_LARGE_MEDIA_PAYLOAD" * 1000
    ids = [str(1800000000000000000 + i) for i in range(100)]

    def large(req):
        if req.url.path == BASE + "/folders":
            return page([])
        assert req.url.path == BASE
        return page([post(pid) for pid in ids], includes={"media": [{"media_key": marker}]})

    inv = Inventory(store, OWNER)
    summary = run(inv, large)
    rows = read_export(store, summary)
    checkpoint = (store.root / inv.name).read_text()
    exported = (store.root / summary["private_export"]).read_text()
    assert checkpoint.count(marker) == exported.count(marker) == 1
    expansion_rows = [r for r in rows if r["type"] == "expansions"]
    assert len(expansion_rows) == 1
    assert all(
        p["expansions_ref"] == expansion_rows[0]["evidence"] for p in rows if p["type"] == "post"
    )
    assert len(checkpoint) < len(marker) * 10  # would exceed 100x with per-post duplication


def test_malformed_id_blocks_only_that_stream_and_resume_does_not_retry(setup):
    store, calls, run = setup

    def malformed(req):
        if req.url.path.endswith("/folders/10"):
            return page([{"id": 123}])
        return example(req)

    inv = Inventory(store, OWNER)
    run(inv, malformed)
    assert inv.state["streams"]["folder:10"]["status"] == "blocked"
    assert inv.state["streams"]["folder:10"]["reason"] == "schema_error"
    assert any(r.url.path.endswith("/folders/20") for r in calls)
    run(Inventory(store, OWNER, resume=True, source_unchanged=True), malformed)
    assert len([r for r in calls if r.url.path.endswith("/folders/10")]) == 1


@pytest.mark.parametrize(
    "bound,changed", [("page_size", 7), ("max_records", 50), ("max_folders", 20), ("max_pages", 30)]
)
def test_resume_rejects_explicit_changed_limits_without_mutating_checkpoint(setup, bound, changed):
    store, _, run = setup
    inv = Inventory(store, OWNER, page_size=5)
    run(inv, example, max_calls=3)
    before = (store.root / inv.name).read_bytes()
    with pytest.raises(PilotError, match="Resume limits differ"):
        Inventory(store, OWNER, resume=True, source_unchanged=True, **{bound: changed})
    assert (store.root / inv.name).read_bytes() == before
    resumed = Inventory(store, OWNER, resume=True, source_unchanged=True)
    assert resumed.summary()["active_limits"]["page_size"] == 5
    assert Inventory(store, OWNER, resume=True, source_unchanged=True, page_size=5)


@pytest.mark.parametrize("size", [1, 2, 3, 4])
def test_small_page_sizes_are_sent_to_all_inventory_endpoints(setup, size):
    store, calls, run = setup

    def small(req):
        assert req.url.params["max_results"] == str(size)
        if req.url.path == BASE + "/folders":
            return page([{"id": "10", "name": "synthetic"}])
        if req.url.path == BASE:
            return page([post(A)])
        return page([{"id": A}])

    summary = run(Inventory(store, OWNER, page_size=size), small)
    assert summary["inventory_status"] == "observed_end"
    assert len(calls) == 4


def test_v1_checkpoint_upgrades_expansions_without_changing_disk_or_cursors(setup):
    store, _, run = setup
    inv = Inventory(store, OWNER)
    run(inv, example, max_calls=3)
    legacy = inv.state
    legacy["schema_version"] = 1
    expansions = legacy.pop("expansions")
    for saved in legacy["posts"].values():
        saved["includes"] = expansions.get(saved.pop("expansions_ref"), {})
    store.write(inv.name, legacy)
    before = (store.root / inv.name).read_bytes()
    offline = Inventory.load(store)
    assert offline.state["schema_version"] == 2
    assert offline.state["expansions"] == expansions
    assert offline.state["streams"]["main"]["next_token"] == "main-next"
    offline.export()
    assert (store.root / inv.name).read_bytes() == before
    resumed = Inventory(store, OWNER, resume=True, source_unchanged=True)
    assert run(resumed, example)["records"] == 4


def live_shape(req):
    """Documented folder shape, synthetic IDs, observed over-return behavior."""
    if req.url.path == BASE + "/folders":
        return {"data": [{"id": "10", "name": "synthetic folder"}]}
    if req.url.path == BASE:
        return page([post(A)], "more-main")
    if req.url.path == BASE + "/folders/10":
        return {"data": [{"id": str(int(A) + i)} for i in range(20)]}
    assert req.url.path == "/2/tweets"
    return {"data": [post(pid) for pid in req.url.params["ids"].split(",")]}


def test_documented_folder_shape_preserves_oversized_response_and_lookup_accounting(setup):
    store, calls, run = setup
    inv = Inventory(store, OWNER, page_size=1, max_pages=1)
    result = run(inv, live_shape, max_calls=6, hydrate_batch_size=1)
    assert len(calls) == 6
    assert result["records"] == result["membership_counts"]["filed"] == 20
    assert result["membership_counts"]["confirmed_unfiled"] == 0
    assert result["main_post_bodies"] == 1
    assert result["hydration_counts"] == {"read": 3, "pending": 17}
    assert result["lookup_requests_this_run"] == result["lookup_batches_in_pass"] == 2
    assert result["lookup_returned_records_in_pass"] == 2
    folder = inv.state["streams"]["folder:10"]
    assert folder["reason"] == "pagination_unverified"
    assert folder["pages"][0]["raw_count"] == 20
    assert folder["pages"][0]["exceeded_requested_page_size"]
    assert folder["pages"][0]["requested_page_size"] == 1
    assert all(p["folder_ids"] == ["10"] for p in inv.state["posts"].values())
    assert not result["all_folders_exhausted_in_pass"]
    assert result["stop_reason"] == "request_limit"
    resumed = Inventory(store, OWNER, resume=True, source_unchanged=True)
    done = run(resumed, live_shape, hydrate_batch_size=10)
    assert done["hydration_counts"] == {"read": 20}
    assert done["lookup_returned_records_in_pass"] == 19
    assert done["inventory_status"] == "partial"
    assert len([r for r in calls if r.url.path.endswith("/folders/10")]) == 1


@pytest.mark.parametrize("kind", ["main", "folder", "folders"])
def test_response_count_is_not_constrained_by_request_range(setup, kind):
    store, _, _ = setup
    inv = Inventory(store, OWNER, page_size=1)
    if kind == "folder":
        inv.accept_page("folders", {"data": [{"id": "10", "name": "synthetic"}]}, "folders")
    data = [
        {
            "id": str(1000 + i),
            **(
                {"name": "synthetic"}
                if kind == "folders"
                else {"text": "synthetic"}
                if kind == "main"
                else {}
            ),
        }
        for i in range(101)
    ]
    key = "folder:10" if kind == "folder" else kind
    inv.accept_page(key, {"data": data}, "saved")
    assert len(inv.state["streams"][key]["pages"][0]["ids"]) == 101
    assert inv.state["streams"][key]["reason"] == "pagination_unverified"


def test_record_cap_retains_known_memberships_and_available_capacity(setup):
    store, _, _ = setup
    inv = Inventory(store, OWNER, max_records=2)
    inv.accept_page("main", page([post(A)]), "main")
    inv.accept_page("folders", page([{"id": "10", "name": "synthetic"}]), "folders")
    # The known ID comes after excess new IDs; its positive evidence must survive.
    inv.accept_page(
        "folder:10", page([{"id": B}, {"id": C}, {"id": D}, {"id": A}], "next"), "contents"
    )
    assert set(inv.state["posts"]) == {A, B}
    assert inv.state["posts"][A]["folder_ids"] == ["10"]
    item = inv.state["streams"]["folder:10"]
    assert item["reason"] == "record_limit" and item["next_token"] is None
    assert item["pages"][0]["next_token"] == "next"
    assert item["pages"][0]["omitted_count"] == 2
    assert item["pages"][0]["raw_count"] == 4
    assert not inv.all_folders_exhausted()


@pytest.mark.parametrize("meta", [None, {}, {"unrecognized": "synthetic"}])
def test_missing_or_empty_pagination_metadata_cannot_prove_coverage(setup, meta):
    store, _, _ = setup
    inv = Inventory(store, OWNER)
    inv.accept_page("folders", {"data": [], "meta": meta}, "folders")
    assert not inv.all_folders_exhausted()
    assert inv.state["streams"]["folders"]["reason"] == "pagination_unverified"


def rejected_folder(setup, monkeypatch):
    import x_bookmark_migrator.inventory as module

    store, _, run = setup
    original = module.validate_page

    def legacy_validator(body, kind, size):
        if kind == "folder":
            raise PilotError("legacy page-size rejection", code="schema_error")
        return original(body, kind, size)

    inv = Inventory(store, OWNER, page_size=1, max_pages=1)
    with monkeypatch.context() as patch:
        patch.setattr(module, "validate_page", legacy_validator)
        run(inv, live_shape)
    name = inv.state["streams"]["folder:10"].pop("failed_evidence")
    inv.save()  # merged version did not record the failed filename in its checkpoint
    return inv, name


def test_recover_legacy_rejected_read_offline_without_changing_billing_or_tokens(
    setup, monkeypatch
):
    store, calls, _ = setup
    inv, name = rejected_folder(setup, monkeypatch)
    raw = store.read(name)
    before = {
        n: (store.root / n).read_bytes()
        for n in (name, "tokens.private.json", "budget.private.json")
    }
    call_count = len(calls)
    loaded = Inventory.load(store)
    result = loaded.recover(name)
    assert len(calls) == call_count
    assert result["offline_recovery"] and result["get_attempts_this_run"] == 0
    assert result["lookup_requests_this_run"] == result["lookup_batches_in_pass"] == 0
    assert result["records"] == result["membership_counts"]["filed"] == 20
    assert result["hydration_counts"] == {"read": 1, "pending": 19}
    assert loaded.state["posts"][B]["first_observed_at"] == raw["retrieved_at"]
    assert loaded.state["streams"]["folder:10"]["pages"][0]["observed_at"] == raw["retrieved_at"]
    assert all((store.root / n).read_bytes() == value for n, value in before.items())
    assert len([r for r in read_export(store, result) if r["type"] == "post"]) == 20
    assert store.read(inv.name)["posts"][B]["folder_ids"] == ["10"]
    with pytest.raises(PilotError, match="rejected page"):
        loaded.recover(name)


@pytest.mark.parametrize("change", ["owner", "cursor", "time", "status", "filename", "in_flight"])
def test_recovery_rejects_unrelated_evidence_without_checkpoint_change(setup, monkeypatch, change):
    store, _, _ = setup
    inv, name = rejected_folder(setup, monkeypatch)
    saved = store.read(name)
    if change == "owner":
        saved["endpoint"] = "/2/users/999/bookmarks/folders/10"
    elif change == "cursor":
        saved["request"]["pagination_token"] = "wrong"
    elif change == "time":
        saved["retrieved_at"] = "2000-01-01T00:00:00+00:00"
    elif change == "status":
        saved["status"] = 403
    elif change == "filename":
        saved["private_file"] = "different"
    else:
        inv.state["in_flight"] = True
        inv.save()
    store.write(name, saved)
    checkpoint = (store.root / inv.name).read_bytes()
    with pytest.raises(PilotError):
        inv.recover(name)
    assert (store.root / inv.name).read_bytes() == checkpoint


def test_recovery_rejects_path_traversal(setup):
    store, _, _ = setup
    inv = Inventory(store, OWNER)
    with pytest.raises(PilotError, match="filename"):
        inv.recover("../read-" + "a" * 24 + ".private.json")


def test_recovery_cli_is_offline_and_needs_no_credentials(setup, monkeypatch, capsys):
    import sys

    from x_bookmark_migrator import cli

    store, _, _ = setup
    inv, name = rejected_folder(setup, monkeypatch)
    monkeypatch.delenv("X_CLIENT_ID", raising=False)
    monkeypatch.delenv("X_APP_OWNER_ID", raising=False)
    monkeypatch.setattr(cli.httpx, "Client", lambda **kw: pytest.fail("HTTP client created"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["x-migrator", "--state-dir", str(store.root), "inventory-recover", "--read-file", name],
    )
    assert cli.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["records"] == 20 and result["get_attempts_this_run"] == 0
    assert store.read(inv.name)["posts"][A]["folder_ids"] == ["10"]
    assert not result["consistent_observations"]  # legacy evidence lacks an exact link


def test_folder_limit_keeps_discovered_names_without_advancing_cursor(setup):
    store, _, _ = setup
    inv = Inventory(store, OWNER, max_folders=1)
    inv.accept_page(
        "folders",
        page([{"id": "10", "name": "first"}, {"id": "20", "name": "second"}], "next"),
        "folders",
    )
    assert inv.state["folders"] == {"10": {"id": "10", "names": ["first"]}}
    assert "folder:10" in inv.state["streams"] and "folder:20" not in inv.state["streams"]
    item = inv.state["streams"]["folders"]
    assert item["reason"] == "record_limit" and item["next_token"] is None
    assert item["pages"][0]["omitted_count"] == 1
    assert not inv.all_folders_exhausted()


@pytest.mark.parametrize("returned", [[A, A], [D]])
def test_lookup_rejects_duplicate_or_unrequested_ids_without_changing_posts(setup, returned):
    import copy

    store, _, _ = setup
    inv = Inventory(store, OWNER)
    inv.add_post(A)
    inv.add_post(B)
    before = copy.deepcopy(inv.state)
    with pytest.raises(PilotError, match="unexpected/duplicate"):
        inv.accept_hydration([A, B], {"data": [post(pid) for pid in returned]}, "lookup")
    assert inv.state == before
