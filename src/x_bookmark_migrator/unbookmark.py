"""Bounded archive-to-unbookmark transactions. Caller holds the private state lock.

Immutable intent/response/result files are authoritative across plans and restarts.
An ambiguous attempt is never retried, including when a new plan selects the ID.
"""

import json
import re
import secrets
from collections import Counter
from decimal import Decimal

import httpx

from .archive import archive_evidence, category_for, media_format, plan_post
from .core import API, PilotError, exact_id, validate_page
from .drive import digest, encode
from .inventory import Inventory, now


def require(condition, message, code="verification_failed"):
    if not condition:
        raise PilotError(message, code=code)


def archive_rows(drive, receipt):
    require(receipt.get("schema_version") == 1, "Unsupported archive receipt.")
    anchor = receipt["manifest"]
    # Exact-ID lookup plus independent byte retrieval: no upload-cache verification.
    metadata = drive.metadata(anchor["file_id"])
    require(anchor["parent_id"] in metadata.get("parents", []), "Archive manifest moved.")
    parent = drive.metadata(anchor["parent_id"])
    require(drive.root in parent.get("parents", []), "Archive manifest is outside this root.")
    raw = drive.verify(anchor)
    rows = [json.loads(line) for line in raw.decode().splitlines()]
    require(1 <= len(rows) <= 20, "Unexpected archive size.")
    result = {}
    for row in rows:
        pid = exact_id(row["post_id"])
        require(row.get("schema_version") == 1 and pid not in result, "Invalid archive rows.")
        result[pid] = row
    return result


def verify_item(inventory, drive, row):
    """Reconstruct completeness from source evidence, not a bytes_verified label."""
    pid = exact_id(row["post_id"])
    require(
        row.get("archive_status") == "bytes_verified" and row.get("blockers") == [],
        "Archive item is incomplete.",
        "blocked_archive",
    )
    payload = json.loads(drive.verify(row["payload"]))
    require(
        payload.get("schema_version") == 1 and payload.get("post_id") == pid,
        "Archive post identity differs.",
    )
    for key in ("category", "folders", "archive_status", "blockers"):
        require(payload.get(key) == row[key], "Archive manifest/payload association differs.")
    record = inventory.state["posts"].get(pid)
    require(
        record is not None and record == payload.get("inventory_record"),
        "Inventory changed; archive and plan again.",
        "blocked_inventory_changed",
    )
    category, folders, blockers = category_for(inventory, record)
    require(
        not blockers and len(folders) == 1,
        "Pilot requires one known named folder.",
        "blocked_category",
    )
    require(
        category == row["category"] and folders == row["folders"],
        "Archive category differs.",
        "blocked_category",
    )
    require(
        record["hydration_status"] == "read" and not record.get("errors"),
        "Source content is incomplete.",
        "blocked_source",
    )
    evidence = archive_evidence(inventory, record)
    require(evidence == payload.get("source_evidence"), "Source evidence changed.")
    posts, assets, blockers = plan_post(record["post"], evidence["response"].get("includes", {}))
    require(not blockers and posts == payload.get("posts"), "Archive text/context is incomplete.")
    stored = payload.get("media")
    require(isinstance(stored, list) and len(assets) == len(stored), "Archive media missing.")
    for expected, actual in zip(assets, stored, strict=True):
        require(
            expected == {k: v for k, v in actual.items() if k not in ("storage", "container")},
            "Archive media mapping differs.",
        )
        data = drive.verify(actual["storage"])
        mime, extension = media_format(data, actual["logical_type"])
        require(
            extension == actual["container"] and mime == actual["storage"]["mime_type"],
            "Archive media container differs.",
        )
    meta = drive.metadata(row["payload"]["file_id"])
    require(row["payload"]["parent_id"] in meta.get("parents", []), "Archive payload moved.")
    return {
        "post_id": pid,
        "folder": folders[0],
        "payload": row["payload"],
        "media_files": len(stored),
        "external_lookup": "drive_file_id",
    }


def make_plan(store, drive, receipt_name, post_ids, owner_id, *, acceptance_confirmed=False):
    require(acceptance_confirmed, "Complete the ARCHIVE.md runtime/restore acceptance first.")
    require(
        isinstance(post_ids, list)
        and 1 <= len(post_ids) <= 3
        and len(set(post_ids)) == len(post_ids),
        "Select 1–3 distinct post IDs.",
    )
    for pid in post_ids:
        exact_id(pid)
    inventory = Inventory.load(store)
    require(inventory.state["owner_id"] == exact_id(owner_id), "Inventory owner differs.")
    receipt = store.read(receipt_name)
    require(isinstance(receipt, dict), "Archive receipt is missing.")
    context = drive.preflight()
    rows = archive_rows(drive, receipt)
    items = []
    for pid in post_ids:
        require(pid in rows, "Selected post is not in the archive receipt.")
        try:
            item = verify_item(inventory, drive, rows[pid])
            items.append({**item, "state": "verified"})
        except PilotError as exc:
            # Storage/provenance errors stop the plan; known per-item blockers do not.
            if not exc.code.startswith("blocked_"):
                raise
            items.append({"post_id": pid, "state": "blocked", "reason": exc.code})
    plan = {
        "schema_version": 1,
        "created_at": now(),
        "owner_id": owner_id,
        "inventory_checkpoint": inventory.name,
        "archive_receipt": receipt,
        "drive_destination": {k: context[k] for k in ("root", "owner_permission_id")},
        "operator_attestation": {
            "runtime_archive_restore_accepted": True,
            "content_and_media_reviewed": True,
            "restore_anchor_backed_up_independently": True,
        },
        "items": items,
    }
    name = "unbookmark-plan-" + secrets.token_hex(12) + ".private.json"
    store.create(name, plan)
    return {
        "private_plan": name,
        "selected": len(items),
        "verified": sum(i["state"] == "verified" for i in items),
        "blocked": sum(i["state"] == "blocked" for i in items),
        "x_api_calls": 0,
        "deleted": 0,
    }


def load_plan(store, name, owner_id):
    plan = store.read(name)
    require(isinstance(plan, dict) and plan.get("schema_version") == 1, "Invalid unbookmark plan.")
    require(plan["owner_id"] == exact_id(owner_id), "Plan owner differs.")
    require(1 <= len(plan["items"]) <= 3, "Invalid pilot size.")
    ids = [exact_id(i["post_id"]) for i in plan["items"]]
    require(len(set(ids)) == len(ids), "Duplicate plan IDs.")
    require(
        plan.get("operator_attestation")
        == {
            "runtime_archive_restore_accepted": True,
            "content_and_media_reviewed": True,
            "restore_anchor_backed_up_independently": True,
        },
        "Missing archive acceptance.",
    )
    return plan


class Reads:
    def __init__(self, client, budget, max_calls):
        require(type(max_calls) is int and 1 <= max_calls <= 100, "Request bound must be 1–100.")
        self.client, self.budget, self.max_calls = client, budget, max_calls
        self.reservations = Counter()

    def reserve(self, operation):
        require(
            sum(self.reservations.values()) < self.max_calls,
            "Request limit reached; stopped before next request.",
            "request_limit",
        )
        self.budget.reserve()
        self.reservations[operation] += 1

    def get(self, path, params, operation, *, prepaid=False):
        return self.client.get(
            path, params, (lambda: None) if prepaid else (lambda: self.reserve(operation))
        )


def check_identity(reads, owner_id):
    body = reads.get("/2/users/me", {}, "identity")
    require(
        isinstance(body.get("data"), dict)
        and not body.get("errors")
        and body["data"].get("id") == owner_id,
        "Authenticated X owner differs.",
    )


def window(reads, owner_id, folder_id, operation, *, prepaid=False):
    endpoint = f"/2/users/{exact_id(owner_id)}/bookmarks/folders/{exact_id(folder_id)}"
    body = reads.get(endpoint, {"max_results": "100"}, operation, prepaid=prepaid)
    data, token, errors, metadata = validate_page(body, "folder", 100)
    ids = [item["id"] for item in data]
    # Local defensive ceiling, not the requested page size: inventory has observed
    # folder over-return. Preserve valid positives without assuming a 100-ID limit.
    require(
        not errors and len(ids) == len(set(ids)) and len(ids) <= 10000,
        "Ambiguous folder window; stopped.",
        "blocked_folder_window",
    )
    return {
        "ids": ids,
        "evidence": reads.client.observations[-1]["private_file"],
        "has_next_token": bool(token),
        "pagination_metadata_present": metadata,
        "all_history_complete": False,
    }


def check_folder(reads, owner_id, folder):
    """Bounded enumeration, requiring only this exact positive ID/name association."""
    token, seen = None, set()
    for _ in range(10):
        params = {"max_results": "100"}
        if token:
            params["pagination_token"] = token
        body = reads.get(f"/2/users/{owner_id}/bookmarks/folders", params, "folder_names")
        data, token, errors, _ = validate_page(body, "folders", 100)
        require(not errors, "Folder enumeration is partial.", "blocked_folder_name")
        matches = [f for f in data if f["id"] == folder["id"]]
        if matches:
            require(
                len(matches) == 1 and [matches[0]["name"]] == folder["names"],
                "Folder renamed or conflicting; rearchive before deletion.",
                "blocked_folder_name",
            )
            return reads.client.observations[-1]["private_file"]
        if not token or token in seen:
            break
        seen.add(token)
    raise PilotError("Folder name could not be reconciled.", code="blocked_folder_name")


def attempt_base(owner_id, pid):
    return "unbookmark-" + digest(encode([exact_id(owner_id), exact_id(pid)]))


def latest_attempt(store, owner_id, pid):
    prefix = attempt_base(owner_id, pid)
    paths = sorted(store.root.glob(prefix + "-*.intent.private.json"))
    if not paths:
        return None, 0
    path = paths[-1]
    match = re.fullmatch(re.escape(prefix) + r"-([0-9]{6})\.intent\.private\.json", path.name)
    require(
        match is not None and len(paths) == int(match[1]) + 1,
        "Attempt history is incomplete; do not retry.",
    )
    base = path.name.removesuffix(".intent.private.json")
    intent = store.read(path.name)
    require(
        intent["owner_id"] == owner_id and intent["post_id"] == pid, "Attempt identity mismatch."
    )
    return base, int(match[1]) + 1


def classify(response):
    """The endpoint's generic true example is deliberately NOT a success signal."""
    body = response.get("response")
    if response["status"] == 200 and isinstance(body, dict):
        if (
            isinstance(body.get("data"), dict)
            and body["data"].get("bookmarked") is False
            and body.get("errors", []) == []
        ):
            return "unbookmarked"
    if response["status"] in (400, 401, 403, 429):
        return "unbookmark_rejected"
    return "unbookmark_outcome_unknown"


def result_for(store, base):
    intent = store.read(base + ".intent.private.json")
    require(
        intent.get("schema_version") == 1
        and intent.get("state") == "unbookmark_requested"
        and intent.get("endpoint")
        == f"/2/users/{exact_id(intent['owner_id'])}/bookmarks/{exact_id(intent['post_id'])}",
        "Invalid removal intent.",
    )
    response = store.read(base + ".response.private.json")
    if response is None:
        return "unbookmark_outcome_unknown"
    require(
        response.get("intent_sha256") == digest(encode(intent))
        and response.get("endpoint") == intent["endpoint"],
        "Response/intent mismatch.",
    )
    state = classify(response)
    result_name = base + ".result.private.json"
    existing = store.read(result_name)
    if existing:
        require(
            existing.get("state") == state
            and existing.get("response_sha256") == digest(encode(response))
            and existing.get("intent_sha256") == digest(encode(intent))
            and existing.get("owner_id") == intent["owner_id"]
            and existing.get("post_id") == intent["post_id"],
            "Removal receipt mismatch.",
        )
    else:
        store.create(
            result_name,
            {
                "created_at": now(),
                "state": state,
                "response_sha256": digest(encode(response)),
                "intent_sha256": digest(encode(intent)),
                "post_id": intent["post_id"],
                "owner_id": intent["owner_id"],
            },
        )
    return state


def invalidate_inventory(inventory):
    # Even an uncertain mutation invalidates all pre-delete pagination/negative claims.
    inventory.state["consistent"] = False
    if "unbookmark_attempt_requires_fresh_pass" not in inventory.state["continuity_notes"]:
        inventory.state["continuity_notes"].append("unbookmark_attempt_requires_fresh_pass")
    inventory.save()


def compare_window(before, after, pid, inventory_post_ids=None):
    added = sorted(set(after["ids"]) - set(before["ids"]))
    known = set(inventory_post_ids or [])
    absent = pid not in after["ids"]
    return {
        "before": before,
        "after": after,
        "post_id": pid,
        "removed_id_absent": absent,
        "newly_observed_ids": added,
        "inventory_baseline_available": inventory_post_ids is not None,
        "previously_inventoried_ids": sorted(set(added) & known),
        "not_previously_inventoried_ids": sorted(set(added) - known),
        # Neither partition proves bookmark age: even known posts can be re-saved.
        "older_bookmarks_exposed": "unverified",
        "window_status": "target_still_visible"
        if not absent
        else "new_ids_observed"
        if added
        else "observed_empty"
        if not after["ids"]
        else "no_new_ids_observed",
        "all_history_complete": False,
    }


def apply_plan(
    store,
    drive,
    client,
    budget,
    plan_name,
    owner_id,
    *,
    confirmed=False,
    retry_rejected=False,
    max_calls=20,
):
    require(confirmed, "Review the private plan and supply --confirm-unbookmark.")
    require(
        budget.per_call >= Decimal("0.005"),
        "Per-call reservation must cover at least the published $0.005 Bookmark write rate.",
    )
    plan = load_plan(store, plan_name, owner_id)
    reads = Reads(client, budget, max_calls)
    # Check scope before any paid read or destructive intent.
    client.access_token(write=True)
    check_identity(reads, owner_id)
    context = drive.preflight()
    require(
        plan["drive_destination"] == {k: context[k] for k in ("root", "owner_permission_id")},
        "Drive destination differs from the reviewed plan.",
    )
    inventory = Inventory.load(store)
    require(
        inventory.name == plan["inventory_checkpoint"] and inventory.state["owner_id"] == owner_id,
        "Active inventory changed; plan again.",
    )
    rows = archive_rows(drive, plan["archive_receipt"])
    outcomes, stop = [], None
    for item in plan["items"]:
        pid = item["post_id"]
        if item["state"] != "verified":
            outcomes.append({"post_id": pid, "state": "blocked", "reason": item.get("reason")})
            continue
        base, number = latest_attempt(store, owner_id, pid)
        if base:
            previous = result_for(store, base)
            if not (retry_rejected and previous == "unbookmark_rejected"):
                outcome = {
                    "post_id": pid,
                    "state": previous,
                    "attempt": base,
                    "sent_this_run": False,
                }
                comparison = store.read(base + ".window.private.json")
                if comparison:
                    outcome["window_status"] = comparison["window_status"]
                    outcome["reconciled_later"] = comparison.get("reconciled_later", False)
                outcomes.append(outcome)
                if (
                    previous != "unbookmarked"
                    or outcome.get("window_status") != "new_ids_observed"
                    or outcome.get("reconciled_later")
                ):
                    stop = "reconciliation_required"
                    break
                continue
        try:
            verified = verify_item(inventory, drive, rows[pid])
            require(
                verified == {k: v for k, v in item.items() if k != "state"},
                "Verified candidate differs from the reviewed plan.",
            )
            folder_evidence = check_folder(reads, owner_id, item["folder"])
            before = window(reads, owner_id, item["folder"]["id"], "before_window")
            require(
                pid in before["ids"],
                "Candidate is no longer in the named folder window.",
                "blocked_category_changed",
            )
            access = client.access_token(write=True)
            # Reserve DELETE AND its corroborating GET before any mutation.
            reads.reserve("delete")
            reads.reserve("after_window")
        except PilotError as exc:
            outcomes.append({"post_id": pid, "state": "blocked", "reason": exc.code})
            if not exc.code.startswith("blocked_"):
                stop = exc.code
                break
            continue
        require(number < 1000000, "Attempt limit reached.")
        base = attempt_base(owner_id, pid) + f"-{number:06d}"
        endpoint = f"/2/users/{owner_id}/bookmarks/{pid}"
        intent = {
            "schema_version": 1,
            "state": "unbookmark_requested",
            "created_at": now(),
            "owner_id": owner_id,
            "post_id": pid,
            "endpoint": endpoint,
            "plan_sha256": digest(encode(plan)),
            "plan_file": plan_name,
            "archive_manifest": plan["archive_receipt"]["manifest"],
            "verified": verified,
            "folder_name_evidence": folder_evidence,
            "before": before,
            "inventory_post_ids": sorted(inventory.state["posts"]),
            "operator_attestation": plan["operator_attestation"],
        }
        # Both operations must finish durably before the network call. No exception
        # recovery here can accidentally fall through to DELETE.
        invalidate_inventory(inventory)
        store.create(base + ".intent.private.json", intent)
        try:
            response = client.http.delete(
                API + endpoint, headers={"Authorization": "Bearer " + access}
            )
        except httpx.HTTPError:
            outcomes.append(
                {
                    "post_id": pid,
                    "state": "unbookmark_outcome_unknown",
                    "attempt": base,
                    "sent_this_run": True,
                }
            )
            stop = "delete_transport_unknown"
            break
        try:
            body = {"response": response.json()}
        except ValueError:
            body = {"response_text": response.text}
        rates = {
            key: value
            for key, value in response.headers.items()
            if key in ("x-rate-limit-limit", "x-rate-limit-remaining", "x-rate-limit-reset")
            and re.fullmatch(r"[0-9]{1,20}", value)
        }
        store.create(
            base + ".response.private.json",
            {
                **body,
                "status": response.status_code,
                "endpoint": endpoint,
                "rate_limits": rates,
                "retrieved_at": now(),
                "intent_sha256": digest(encode(intent)),
            },
        )
        state = result_for(store, base)
        outcome = {"post_id": pid, "state": state, "attempt": base, "sent_this_run": True}
        outcomes.append(outcome)
        if state != "unbookmarked":
            stop = state
            break
        try:
            after = window(reads, owner_id, item["folder"]["id"], "after_window", prepaid=True)
            comparison = compare_window(before, after, pid, intent["inventory_post_ids"])
            store.create(base + ".window.private.json", comparison)
            outcome["window_status"] = comparison["window_status"]
            if comparison["window_status"] != "new_ids_observed":
                # Stop the pilot on a stagnant/empty/contradictory window. Do not
                # delete another item merely to try to force older records out.
                stop = comparison["window_status"]
                break
        except (PilotError, httpx.HTTPError):
            stop = "after_window_unverified"
            break
    return save_report(store, plan_name, outcomes, reads, stop)


def save_report(store, plan_name, outcomes, reads, stop):
    name = "unbookmark-report-" + secrets.token_hex(12) + ".private.json"
    report = {
        "schema_version": 1,
        "created_at": now(),
        "plan_file": plan_name,
        "outcomes": outcomes,
        "stop_reason": stop,
        "reservations_by_operation": dict(reads.reservations),
        "estimated_reserved_usd": str(reads.budget.used),
        "actual_charge": "unverified",
    }
    store.create(name, report)
    counts = dict(Counter(o["state"] for o in outcomes))
    complete = (
        stop is None
        and outcomes
        and all(
            o["state"] == "unbookmarked" and o.get("window_status") == "new_ids_observed"
            for o in outcomes
        )
    )
    return {
        "private_report": name,
        "states": counts,
        "stop_reason": stop,
        "delete_attempts_this_run": sum(o.get("sent_this_run", False) for o in outcomes),
        "new_window_observations": sum(
            o.get("window_status") == "new_ids_observed" for o in outcomes
        ),
        "blocked": not complete,
        "estimated_reserved_usd": str(reads.budget.used),
        "reservations_by_operation": dict(reads.reservations),
        "actual_charge": "unverified",
    }


def reconcile_plan(store, client, budget, plan_name, owner_id, *, max_calls=20):
    """Read-only recovery. Absence in a capped listing never resolves a lost DELETE."""
    plan = load_plan(store, plan_name, owner_id)
    reads = Reads(client, budget, max_calls)
    check_identity(reads, owner_id)
    outcomes = []
    window_stop = None
    for item in plan["items"]:
        pid = item["post_id"]
        base, _ = latest_attempt(store, owner_id, pid)
        if base is None:
            outcomes.append({"post_id": pid, "state": "not_attempted"})
            continue
        intent = store.read(base + ".intent.private.json")
        state = result_for(store, base)
        outcome = {"post_id": pid, "state": state, "attempt": base, "sent_this_run": False}
        outcomes.append(outcome)
        if window_stop:
            outcome.update(window_status="unverified", window_error=window_stop)
            continue
        try:
            after = window(reads, owner_id, intent["verified"]["folder"]["id"], "reconcile_window")
        except (PilotError, httpx.HTTPError) as exc:
            # Keep the durable deletion result even when corroborating reads fail.
            # Reservation failures also land here; later receipts still reconcile
            # without sending requests beyond the configured budget/request cap.
            outcome.update(
                window_status="unverified",
                window_error=exc.code if isinstance(exc, PilotError) else "transport_error",
            )
            if outcome["window_error"] in (
                "request_limit",
                "spend_cap",
                "http_401",
                "http_403",
                "http_429",
            ):
                window_stop = outcome["window_error"]
            continue
        comparison = {
            **compare_window(intent["before"], after, pid, intent.get("inventory_post_ids")),
            "reconciled_later": True,
        }
        if state == "unbookmarked" and store.read(base + ".window.private.json") is None:
            store.create(base + ".window.private.json", comparison)
        store.create(
            base + ".reconcile-" + secrets.token_hex(6) + ".private.json",
            {"state": state, "created_at": now(), **comparison},
        )
        outcome.update(window_status=comparison["window_status"], reconciled_later=True)
    return save_report(store, plan_name, outcomes, reads, window_stop)
