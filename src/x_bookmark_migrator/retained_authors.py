"""Offline replay of a historical missing-author gap. Never updates archive status."""

import argparse
import json
from pathlib import Path

from .archive import plan_post
from .catalog import checked_bytes, private_bytes
from .core import PilotError, exact_id


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
    posts, assets, before = plan_post(primary, includes)
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
    after_posts, after_assets, after = plan_post(primary, supplemented)
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


def diagnose(batches):
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
            payloads[pid] = payload
            envelope = payload["source_evidence"]
            if evidence.setdefault(envelope["private_file"], envelope) != envelope:
                raise PilotError("Conflicting source envelopes.")
    candidates = [
        candidate
        for payload in payloads.values()
        if (candidate := replay_author_gap(payload, evidence.values())) is not None
    ]
    return {
        "verified_payloads": len(payloads),
        "distinct_envelopes": len(evidence),
        "missing_author_records": sum(
            "blocked_missing_author" in payload["blockers"] for payload in payloads.values()
        ),
        "author_replay_candidates": len(candidates),
        "author_only_replay_candidates": sum(
            not c["remaining_historical_blockers"] for c in candidates
        ),
    }


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
