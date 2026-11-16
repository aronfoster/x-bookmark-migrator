"""Private, offline historical reconciliation. Never sends an X request."""

import copy
import json
from collections import Counter
from pathlib import Path

from .catalog import private_bytes, source_sidecar
from .core import PilotError, exact_id
from .drive import digest, encode
from .source import source_url
from .unbookmark import classify

OUTCOMES = ("confirmed_removed", "outcome_unknown", "rejected", "never_attempted")
CATEGORIES = (
    "confirmed_media_only",
    "non_media_valid_raindrop",
    "non_media_missing_raindrop",
    "ambiguous_classification",
    "exceptional_unrecoverable",
)
STATES = {
    "unbookmarked": "confirmed_removed",
    "unbookmark_outcome_unknown": "outcome_unknown",
    "unbookmark_rejected": "rejected",
}


def read_json(path):
    raw = private_bytes(path)
    return json.loads(raw), {"path": str(Path(path).absolute()), "sha256": digest(raw)}


def scan_native(directory):
    """Read all native intents, not just latest/successful ones; never write receipts."""
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise PilotError("Removal evidence directory must exist and not be a symlink.")
    intents = sorted(root.glob("unbookmark-*.intent.private.json"))
    bases = {p.name.removesuffix(".intent.private.json") for p in intents}
    for suffix in ("response", "result"):
        for path in root.glob(f"unbookmark-*.{suffix}.private.json"):
            if path.name.removesuffix(f".{suffix}.private.json") not in bases:
                raise PilotError("Orphan removal receipt: supply its original intent.")
    attempts = []
    for path in intents:
        intent, evidence = read_json(path)
        pid = exact_id(intent["post_id"])
        owner = exact_id(intent["owner_id"])
        endpoint = f"/2/users/{owner}/bookmarks/{pid}"
        if (
            intent.get("schema_version") != 1
            or intent.get("state") != "unbookmark_requested"
            or intent.get("endpoint") != endpoint
        ):
            raise PilotError("Invalid historical removal intent.")
        base = path.name.removesuffix(".intent.private.json")
        response_path = root / (base + ".response.private.json")
        result_path = root / (base + ".result.private.json")
        state, blockers, sources = "unbookmark_outcome_unknown", [], [evidence]
        response = None
        if response_path.exists():
            response, proof = read_json(response_path)
            sources.append(proof)
            if (
                response.get("intent_sha256") != digest(encode(intent))
                or response.get("endpoint") != endpoint
                or type(response.get("status")) is not int
            ):
                blockers.append("response_intent_mismatch")
            else:
                state = classify(response)
        if result_path.exists():
            result, proof = read_json(result_path)
            sources.append(proof)
            if (
                response is None
                or result.get("state") != state
                or result.get("intent_sha256") != digest(encode(intent))
                or result.get("response_sha256") != digest(encode(response))
                or result.get("post_id") != pid
                or result.get("owner_id") != owner
            ):
                blockers.append("result_evidence_mismatch")
        attempts.append(
            {
                "post_id": pid,
                "outcome": "outcome_unknown" if blockers else STATES[state],
                "format": "native_unbookmark_v1",
                "evidence": sources,
                "archive_manifest": intent.get("archive_manifest"),
                "blockers": blockers,
            }
        )
    return attempts


def pointer(value, path):
    """Small JSON Pointer reader for an explicitly reviewed private helper format."""
    if not isinstance(path, str) or (path and not path.startswith("/")):
        raise PilotError("Helper field must be a JSON Pointer.")
    for key in path.split("/")[1:]:
        key = key.replace("~1", "/").replace("~0", "~")
        try:
            value = value[int(key)] if isinstance(value, list) else value[key]
        except (KeyError, IndexError, ValueError, TypeError):
            raise PilotError("Helper format does not match supplied evidence.") from None
    return value


def scan_helper(descriptor_path):
    """No guessed local format: explicit reviewed mapping + hash-bound source files."""
    spec, spec_ref = read_json(descriptor_path)
    if (
        spec.get("schema_version") != 1
        or not isinstance(spec.get("format_description"), str)
        or not spec["format_description"].strip()
        or spec.get("all_intents_included") is not True
        or not isinstance(spec.get("files"), list)
        or not spec["files"]
        or not isinstance(spec.get("outcome_map"), dict)
        or any(v not in OUTCOMES[:3] for v in spec["outcome_map"].values())
    ):
        raise PilotError("Document and inventory the private helper format before audit.")
    attempts, seen = [], set()
    for item in spec["files"]:
        path = Path(descriptor_path).parent / item["path"]
        data, evidence = read_json(path)
        if evidence["sha256"] != item["sha256"] or evidence["path"] in seen:
            raise PilotError("Helper evidence hash mismatch or duplicate file.")
        seen.add(evidence["path"])
        rows = pointer(data, spec["records_pointer"])
        if not isinstance(rows, list) or len(rows) != item["record_count"]:
            raise PilotError("Helper intent inventory count mismatch.")
        for index, row in enumerate(rows):
            pid = exact_id(pointer(row, spec["post_id_pointer"]))
            outcome = pointer(row, spec["outcome_pointer"])
            mapped = spec["outcome_map"].get(outcome) if isinstance(outcome, str) else None
            attempts.append(
                {
                    "post_id": pid,
                    "outcome": mapped or "outcome_unknown",
                    "format": "private_helper_mapping_v1",
                    "format_description": spec["format_description"],
                    "descriptor": spec_ref,
                    "evidence": [{**evidence, "record_index": index}],
                    "blockers": [] if mapped else ["unmapped_helper_outcome"],
                }
            )
    return attempts


def content_binding(record):
    return digest(encode(sorted({v["payload_receipt"]["sha256"] for v in record["versions"]})))


def classification(record, decisions):
    """Only empty-text complete media or explicit hash-bound evidence is exempt."""
    evidence = decisions.get(record["post_id"])
    if evidence is not None:
        if (
            evidence.get("content_binding") != content_binding(record)
            or evidence.get("classification") not in ("media_only", "non_media", "ambiguous")
            or evidence.get("basis") not in ("owner_review", "archive_review")
            or not isinstance(evidence.get("reason"), str)
            or not evidence["reason"].strip()
        ):
            raise PilotError("Classification evidence is invalid or refers to stale content.")
        return evidence["classification"], copy.deepcopy(evidence)
    versions = record["versions"]
    if versions and all(
        v["archive_status"] == "bytes_verified"
        and not v["blockers"]
        and v["posts"]
        and all(isinstance(p.get("text"), str) and not p["text"].strip() for p in v["posts"])
        and v["media"]
        and all(a["availability"] == "local_hash_verified" for a in v["media"])
        for v in versions
    ):
        return "media_only", {
            "basis": "archive_empty_text_and_verified_media",
            "content_binding": content_binding(record),
        }
    # Do not infer meaning from emoji, hashtags, GIFs or a lack of annotations.
    return "ambiguous", {"basis": "no_affirmative_media_only_evidence"}


def reconcile(records, attempts, *, decisions=None, coverage=None, inspect_raindrop=None):
    """One row per primary ID, including orphan attempts, plus a counts-only summary.

    inspect_raindrop is supplied by FOS-166; this module never parses provider pages.
    """
    decisions = decisions or {}
    coverage = coverage or {"complete": False, "blockers": ["removal_sources_not_attested"]}
    by_id = {}
    for record in records:
        pid = exact_id(record["post_id"])
        if pid in by_id:
            raise PilotError("Duplicate primary catalog identity.")
        by_id[pid] = record
    if set(decisions) - set(by_id):
        raise PilotError("Classification refers to an absent saved primary record.")
    histories = {}
    for attempt in attempts:
        pid = exact_id(attempt["post_id"])
        if attempt["outcome"] not in OUTCOMES[:3]:
            raise PilotError("Invalid removal outcome.")
        histories.setdefault(pid, []).append(attempt)
    rows = []
    for pid in sorted(by_id.keys() | histories.keys()):
        record = by_id.get(pid)
        history = histories.get(pid, [])
        outcomes = {a["outcome"] for a in history}
        outcome = next(
            (o for o in ("outcome_unknown", "confirmed_removed", "rejected") if o in outcomes),
            "never_attempted",
        )
        blockers = sorted({b for a in history for b in a.get("blockers", [])})
        row = {
            "source_post_id": pid,
            "source_post_url": source_url(pid),
            "catalog_link": f"index.html#post-{pid}" if record else None,
            "deletion_outcome": outcome,
            "attempts": history,
            "backfill_target": outcome in ("confirmed_removed", "outcome_unknown"),
            "deletion_eligible": False,
        }
        if record:
            kind, proof = classification(record, decisions)
            row.update(
                classification=kind,
                classification_evidence=proof,
                content_binding=content_binding(record),
                archives=source_sidecar([record])[0]["archives"],
                original_categories=[
                    {"category": v["category"], "folders": v["folders"]} for v in record["versions"]
                ],
                category_override=record.get("category_override"),
                drive_links=[v["payload_url"] for v in record["versions"]],
            )
            for attempt in history:
                if attempt.get("archive_manifest") is not None and not any(
                    v["manifest"] == attempt["archive_manifest"] for v in record["versions"]
                ):
                    blockers.append("removal_archive_mismatch")
            # A minimal pointer does not require perfect category metadata, but
            # requires exact primary content and no corrupt/missing archive evidence.
            usable = [
                v
                for v in record["versions"]
                if any(p["id"] == pid for p in v["posts"])
                and v["archive_status"] == "bytes_verified"
                and not v["blockers"]
                and all(a["availability"] == "local_hash_verified" for a in v["media"])
            ]
            if not usable:
                blockers.append("archive_incomplete_or_missing_primary")
            else:
                chosen = min(
                    usable, key=lambda v: (v["manifest"]["sha256"], v["payload_receipt"]["sha256"])
                )
                row["repair_archive_ref"] = {
                    "manifest": chosen["manifest"],
                    "payload": chosen["payload_receipt"],
                }
        else:
            kind = "ambiguous"
            row.update(classification=kind, archives=[], original_categories=[], drive_links=[])
            blockers.append("attempt_without_primary_archive")
        raindrop = inspect_raindrop(pid) if inspect_raindrop else {"state": "unverified"}
        row["raindrop"] = raindrop
        state = raindrop["state"]
        if state not in (
            "verified",
            "missing",
            "active_unverified",
            "trash",
            "conflict",
            "unverified",
        ):
            raise PilotError("Unsupported shared Raindrop inspection state.")
        if state in ("trash", "conflict"):
            blockers.append("raindrop_" + state)
        row["raindrop_required"] = kind != "media_only"
        row["backfill_needed"] = (
            row["backfill_target"] and kind != "media_only" and state != "verified"
        )
        row["repair_ready"] = (
            row["backfill_target"]
            and not blockers
            and (kind == "media_only" or state == "verified")
        )
        row["report_category"] = (
            "exceptional_unrecoverable"
            if blockers
            else "confirmed_media_only"
            if kind == "media_only"
            else "ambiguous_classification"
            if kind == "ambiguous"
            else "non_media_valid_raindrop"
            if state == "verified"
            else "non_media_missing_raindrop"
            if state == "missing"
            else "exceptional_unrecoverable"
        )
        if kind != "media_only" and state in ("unverified", "active_unverified"):
            blockers.append("raindrop_readback_unverified")
        row["blockers"] = sorted(set(blockers))
        rows.append(row)
    cross_tab = {o: {c: 0 for c in CATEGORIES} for o in OUTCOMES}
    for row in rows:
        cross_tab[row["deletion_outcome"]][row["report_category"]] += 1
    targets = [r for r in rows if r["backfill_target"]]
    summary = {
        "catalog_records": len(by_id),
        "distinct_primary_ids": len(rows),
        "attempted_primary_ids": len(histories),
        "removal_attempts": len(attempts),
        "cross_tab": cross_tab,
        "deletion_outcomes": dict(Counter(r["deletion_outcome"] for r in rows)),
        "backfill_needed": sum(r["backfill_needed"] for r in rows),
        "exception_records": sum(bool(r["blockers"]) for r in rows),
        "removal_coverage_complete": coverage.get("complete") is True,
        "offline_reconciliation_complete": coverage.get("complete") is True
        and all(r["repair_ready"] for r in targets),
        "live_acceptance": "not_established_by_offline_audit",
        "x_api_calls": 0,
    }
    return {"schema_version": 1, "coverage": coverage, "rows": rows, "summary": summary}


def saved_raindrop_inspector(snapshot, receipts, inspect_inventory, validate_receipt):
    """Consume FOS-166 validators; no local pagination or provider receipt format."""

    def inspect(pid):
        result = inspect_inventory(snapshot, pid)
        state = {
            "active": "active_unverified",
            "absent": "missing",
            "trash": "trash",
            "conflict": "conflict",
            "incomplete": "unverified",
        }[result["state"]]
        evidence = {**result, "state": state, "fresh": False}
        if state == "active_unverified" and pid in receipts:
            try:
                receipt = validate_receipt(receipts[pid], pid)
            except PilotError:
                evidence["reason"] = "invalid_saved_receipt"
            else:
                if result["raindrop_ids"] == [receipt["raindrop_id"]]:
                    evidence.update(state="verified", receipt=receipt)
        return evidence

    return inspect


def backfill(report, adapter, selected_ids, *, confirmed=False, limit=3):
    """Call the single FOS-166 ensure operation; never persist a competing receipt."""
    if not confirmed:
        raise PilotError("Backfill requires explicit --confirm-raindrop-writes.")
    if not report["coverage"]["complete"]:
        raise PilotError("Reconcile all removal evidence and archive coverage before backfill.")
    if not selected_ids or len(set(selected_ids)) != len(selected_ids) or not 1 <= limit <= 100:
        raise PilotError("Select distinct primary IDs and a bounded backfill limit.")
    if len(selected_ids) > limit:
        raise PilotError("Selected backfill exceeds its reviewed item limit.")
    rows = {r["source_post_id"]: r for r in report["rows"]}
    selected = []
    # Preflight the whole selection before any provider operation.
    for value in selected_ids:
        pid = exact_id(value)
        row = rows.get(pid)
        if row is None or not row["backfill_target"]:
            raise PilotError("Backfill requires a confirmed/unknown historical primary attempt.")
        if set(row["blockers"]) - {"raindrop_readback_unverified"}:
            raise PilotError("Selected historical record has unresolved repair blockers.")
        selected.append(row)
    results = []
    for row in selected:
        pid = row["source_post_id"]
        if not row["raindrop_required"]:
            results.append({"source_post_id": pid, "state": "media_only_exception"})
            continue
        try:
            # Adapter holds the lock, refreshes full inventory, recovers uncertain
            # POSTs and returns only after fresh GET. Do not implement retries here.
            receipt = adapter.ensure_raindrop(pid, source_url(pid), row["repair_archive_ref"])
        except PilotError as exc:
            # Error messages may contain provider material: persist only its code.
            results.append({"source_post_id": pid, "state": "blocked", "reason": exc.code})
            break
        results.append({"source_post_id": pid, "state": "verified", "receipt": receipt})
    return {
        "schema_version": 1,
        "results": results,
        "summary": {
            "selected": len(selected),
            "processed": len(results),
            "verified": sum(r["state"] == "verified" for r in results),
            "media_only_exceptions": sum(r["state"] == "media_only_exception" for r in results),
            "blocked": len(results) != len(selected)
            or any(r["state"] == "blocked" for r in results),
            "x_api_calls": 0,
        },
    }
