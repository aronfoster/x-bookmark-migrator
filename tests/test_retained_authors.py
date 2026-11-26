import hashlib
import json
import sys
from copy import deepcopy

import pytest

from x_bookmark_migrator.archive import plan_post
from x_bookmark_migrator.retained_authors import main, replay_author_gap


def test_retained_author_replay_requires_consistent_bound_lookup_evidence():
    primary = {"id": "1", "author_id": "11", "text": "saved"}
    source = {"status": 200, "private_file": "source", "response": {"data": [primary]}}
    posts, _, blockers = plan_post(primary, {})
    payload = {
        "inventory_record": {"post": primary},
        "post_id": "1",
        "posts": posts,
        "blockers": [*blockers, "blocked_missing_media"],
        "source_evidence": source,
    }
    previous = deepcopy(payload)
    author = {"id": "11", "username": "synthetic"}
    donor = {
        "status": 200,
        "endpoint": "/2/tweets",
        "private_file": "lookup",
        "response": {"includes": {"users": [author]}},
    }
    replay = replay_author_gap(payload, [donor, donor])
    assert replay["author_sources"] == {"11": ["lookup"]}
    assert replay["posts"][0]["author"] == author
    assert replay["remaining_historical_blockers"] == ["blocked_missing_media"]
    assert payload == previous
    for changes in (
        {"endpoint": "local"},
        {"status": 403},
        {"response": {"includes": {"users": [{**author, "id": "12"}]}}},
    ):
        assert replay_author_gap(payload, [{**donor, **changes}]) is None
    conflict = deepcopy(donor)
    conflict["response"]["includes"]["users"][0]["username"] = "different"
    assert replay_author_gap(payload, [donor, conflict]) is None
    assert replay_author_gap({**payload, "post_id": "2"}, [donor]) is None


def test_cli_hash_binds_both_inputs_and_redacts_failures(tmp_path, monkeypatch, capsys):
    def save(name, value):
        data = json.dumps(value).encode()
        path = tmp_path / name
        path.write_bytes(data)
        path.chmod(0o600)
        return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}

    rows = []
    for pid in ("123456", "234567"):
        primary = {"id": pid, "author_id": "11", "text": "private-sentinel"}
        users = [] if not rows else [{"id": "11", "username": "synthetic"}]
        body = {"data": [primary], "includes": {"users": users}}
        posts, _, blockers = plan_post(primary, body["includes"])
        row = {
            "schema_version": 1,
            "post_id": pid,
            "archive_status": "blocked" if blockers else "bytes_verified",
            "blockers": blockers,
            "category": "known",
            "folders": [],
        }
        payload = {
            **row,
            "posts": posts,
            "inventory_record": {"post": primary},
            "source_evidence": {
                "status": 200,
                "private_file": pid,
                "endpoint": "/2/tweets",
                "response": body,
            },
        }
        rows.append({**row, "payload": save(pid + ".json", payload)})
    manifest = tmp_path / "manifest.jsonl"
    raw = "\n".join(map(json.dumps, rows)).encode()
    manifest.write_bytes(raw)
    manifest.chmod(0o600)
    anchor = {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
    save("receipt", {"schema_version": 1, "records": 2, "manifest": anchor})
    monkeypatch.setattr(
        sys, "argv", ["diagnostic", "--batch", str(tmp_path), str(tmp_path / "receipt")]
    )
    main()
    output = capsys.readouterr()
    assert json.loads(output.out) == {
        "verified_payloads": 2,
        "distinct_envelopes": 2,
        "missing_author_records": 1,
        "author_replay_candidates": 1,
        "author_only_replay_candidates": 1,
    }
    for filename in ("123456.json", "234567.json", "manifest.jsonl"):
        path = tmp_path / filename
        previous = path.read_bytes()
        path.write_bytes(previous + b" ")
        with pytest.raises(SystemExit) as stop:
            main()
        assert stop.value.code == 1
        output = capsys.readouterr()
        assert not output.out
        assert (
            output.err
            == "Retained author diagnostic stopped: invalid or unavailable archive evidence.\n"
        )
        path.write_bytes(previous)
