"""Offline replay of a historical missing-author gap. Never updates archive status."""

import argparse
import json
from pathlib import Path

from .archive import media_format, partial_source_response, plan_post
from .catalog import checked_bytes, private_bytes, restored_media_filename
from .core import PilotError, exact_id
from .drive import digest, encode
from .source import source_url


def replay_author_gap(payload, lookup_evidence):
    """Return a private candidate with source bindings, or None if not reproducible.

    Inputs are decoded, trusted local archive payloads and retained official lookup
    envelopes. The caller must verify their storage receipts before using a result.
    The result is diagnostic, not a replacement payload or a completeness receipt.
    """
    if "blocked_missing_author" not in payload["blockers"]:
        return None
    primary = payload["inventory_record"]["post"]
    source = payload["source_evidence"]
    if (
        source["status"] != 200
        or primary["id"] != payload["post_id"]
        or primary not in source["response"]["data"]
    ):
        return None
    includes = source["response"].get("includes", {})
    posts, assets, before = plan_post(primary, includes, source)
    if posts != payload["posts"] or "blocked_missing_author" not in before:
        return None

    missing = {post["raw"].get("author_id") for post in posts if not post["author"]}
    if not missing or not all(isinstance(uid, str) and uid.isdecimal() for uid in missing):
        return None
    supplied = {}
    sources = {}
    for evidence in lookup_evidence:
        if evidence["status"] != 200 or evidence["endpoint"] != "/2/tweets":
            continue
        for user in evidence["response"].get("includes", {}).get("users", []):
            uid = user.get("id")
            if uid not in missing:
                continue
            if (
                not isinstance(user.get("username"), str)
                or not user["username"].strip()
                or supplied.get(uid, user) != user
            ):
                return None
            supplied[uid] = user
            sources.setdefault(uid, set()).add(evidence["private_file"])
    if supplied.keys() != missing:
        return None

    supplemented = {**includes, "users": [*includes.get("users", []), *supplied.values()]}
    # Donor users are diagnostic context, never part of the primary response.
    after_posts, after_assets, after = plan_post(primary, supplemented, source)
    if after_assets != assets or set(before) - {"blocked_missing_author"} != set(after):
        return None
    return {
        "post_id": primary["id"],
        "primary_source": source["private_file"],
        "author_sources": {uid: sorted(names) for uid, names in sources.items()},
        "posts": after_posts,
        "remaining_historical_blockers": sorted(
            set(payload["blockers"]) - {"blocked_missing_author"}
        ),
    }


def load_batches(batches):
    """Bind target and donor envelopes to locally supplied batch receipts."""
    payloads, evidence = {}, {}
    for restored, receipt_file in batches:
        root = Path(restored)
        receipt = json.loads(private_bytes(receipt_file))
        rows = checked_bytes(root / "manifest.jsonl", receipt["manifest"]).splitlines()
        if receipt["schema_version"] != 1 or not 1 <= len(rows) == receipt["records"] <= 20:
            raise PilotError("Unsupported batch receipt.")
        for line in rows:
            row = json.loads(line)
            pid = exact_id(row["post_id"])
            payload = json.loads(checked_bytes(root / (pid + ".json"), row["payload"]))
            fields = (
                "schema_version",
                "post_id",
                "archive_status",
                "blockers",
                "category",
                "folders",
            )
            if row["schema_version"] != 1 or any(payload[k] != row[k] for k in fields):
                raise PilotError("Payload differs from manifest row.")
            if pid in payloads:
                raise PilotError("Duplicate archive version; narrow inputs.")
            payloads[pid] = (root, receipt["manifest"], row, payload)
            if (
                "source_evidence" not in payload
                and row["archive_status"] == "blocked"
                and "blocked_missing_provenance" in row["blockers"]
                and "blocked_missing_author" not in row["blockers"]
            ):
                continue
            envelope = payload["source_evidence"]
            if evidence.setdefault(envelope["private_file"], envelope) != envelope:
                raise PilotError("Conflicting source envelopes.")
    return payloads, evidence


def diagnose(batches):
    records, evidence = load_batches(batches)
    payloads = [p for _, _, _, p in records.values()]
    candidates = [
        candidate
        for payload in payloads
        if (candidate := replay_author_gap(payload, evidence.values())) is not None
    ]
    return {
        "verified_payloads": len(payloads),
        "distinct_envelopes": len(evidence),
        "missing_author_records": sum(
            "blocked_missing_author" in payload["blockers"] for payload in payloads
        ),
        "author_replay_candidates": len(candidates),
        "author_only_replay_candidates": sum(
            not c["remaining_historical_blockers"] for c in candidates
        ),
    }


def select_author_repairs(batches):
    """Select only an author-only gap with one independently archived direct donor."""
    records, evidence = load_batches(batches)
    selected = []
    for pid, (root, manifest, row, payload) in sorted(records.items()):
        if (
            payload["blockers"] != ["blocked_missing_author"]
            or payload["archive_status"] != "blocked"
        ):
            continue
        replay = replay_author_gap(payload, evidence.values())
        if replay is None or len(replay["author_sources"]) != 1:
            continue
        ((uid, names),) = replay["author_sources"].items()
        donors = [
            (dm, dr, dp)
            for _, dm, dr, dp in records.values()
            if dp["post_id"] != pid
            and "source_evidence" in dp
            and dp["source_evidence"]["private_file"] in names
            and dp["inventory_record"]["post"].get("author_id") == uid
            and dp["inventory_record"]["post"] in dp["source_evidence"]["response"]["data"]
        ]
        if len(names) != 1 or len(donors) != 1:
            continue
        dm, dr, dp = donors[0]
        donor = dp["source_evidence"]
        source, record = payload["source_evidence"], payload["inventory_record"]
        posts, assets, blockers = plan_post(
            record["post"], source["response"].get("includes", {}), source
        )
        related = {p["id"] for p in posts}
        for p in posts:
            related.update(
                r["id"]
                for r in p["raw"].get("referenced_posts", p["raw"].get("referenced_tweets", []))
            )
        if (
            source["private_file"] == donor["private_file"]
            or source["endpoint"] != "/2/tweets"
            or record.get("expansions_ref") != source["private_file"]
            or dp["inventory_record"].get("expansions_ref") != donor["private_file"]
            or dp["inventory_record"]["post"]["id"] != dp["post_id"]
            or any(
                t.get("source_post_id", pid) != pid
                or t.get("source_post_url", source_url(pid)) != source_url(pid)
                for t in (row, payload)
            )
            or len(payload["folders"]) != 1
            or len(payload["folders"][0]["names"]) != 1
            or record["folder_ids"] != [payload["folders"][0]["id"]]
            or payload["category"] != "folder-" + exact_id(payload["folders"][0]["id"])
            or donor["response"].get("errors")
            or "original_response" in donor
            or "reference_evidence" in donor
            or set(blockers) != {"blocked_missing_author"}
            or partial_source_response(record, posts, source)
            or any(
                e.get("resource_type") not in ("post", "tweet")
                or not isinstance(e.get("resource_id"), str)
                or not e["resource_id"].isdecimal()
                or e["resource_id"] in related
                or e.get("value") != e["resource_id"]
                for e in source["response"].get("errors", [])
            )
            or assets
            != [
                {k: v for k, v in a.items() if k not in ("storage", "container")}
                for a in payload["media"]
            ]
            or row.get("deletion_eligible") is not False
            or payload.get("deletion_eligible") is not False
        ):
            raise PilotError("Captured evidence does not support an author-only repair.")
        for asset in payload["media"]:
            storage = asset.get("storage")
            if not isinstance(storage, dict):
                raise PilotError("Missing author repair media receipt.")
            data = checked_bytes(root / restored_media_filename(pid, asset), storage)
            if media_format(data, asset["logical_type"]) != (
                storage["mime_type"],
                asset["container"],
            ):
                raise PilotError("Local author repair media format differs.")
        planned = dict(
            payload,
            posts=replay["posts"],
            repair_source={"post_id": pid, "manifest": manifest, "payload": row["payload"]},
            retained_author_source={
                "post_id": dp["post_id"],
                "manifest": dm,
                "payload": dr["payload"],
                "envelope_sha256": digest(encode(donor)),
            },
        )
        selected.append((row, planned))
    if not 1 <= len(selected) <= 20:
        raise PilotError("Select batches with an independently reproducible author-only repair.")
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batch", nargs=2, metavar=("RESTORED", "RECEIPT"), action="append", required=True
    )
    args = parser.parse_args()
    try:
        result = diagnose(args.batch)
    except (OSError, ValueError, KeyError, TypeError, PilotError, IndexError, AttributeError):
        parser.exit(
            1, "Retained author diagnostic stopped: invalid or unavailable archive evidence.\n"
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
