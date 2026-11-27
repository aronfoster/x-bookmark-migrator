"""Run each turn in a fresh CLI process with a persistent synthetic provider."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_catalog import corpus, storage  # noqa: F401

from x_bookmark_migrator.catalog import build, publish, read_catalog

LINK = "https://app.raindrop.io/my/0/item/42"
WORDING = "  Blue helix — owner's recall cue: orrery.\n"
FAKE = """
import json, os, sys
from pathlib import Path
root = Path(os.environ["SYNTHETIC_PROVIDER"])
data = root / "item.json"
item = json.loads(data.read_text())
cmd, *args = sys.argv[1:]
with (root / "calls").open("a") as log:
    print(cmd, file=log)
fault = root / "fault"
mode = fault.read_text() if fault.exists() else ""
if cmd == "list-all":
    print(json.dumps(dict(schema_version=1, result=True, observed_at="synthetic",
        coverage=dict(complete=True, stable_passes=2, perpage=50),
        active=dict(items=[item], pages=1, count=1), trash=dict(items=[], pages=1, count=0))))
    sys.exit()
assert args == ["42"]
if cmd == "update-excerpt-json":
    payload = json.load(sys.stdin)
    assert set(payload) == {"excerpt"}
    if mode == "reject":
        print('{"http_status":"400"}', file=sys.stderr)
        sys.exit(1)
    if mode != "unapplied":
        item.update(payload, lastUpdate="new")
    if mode == "drift":
        item["extension"]["flag"] = 1
    data.write_text(json.dumps(item))
    if mode == "lost":
        fault.write_text("offline")
        sys.exit(1)
elif cmd == "get":
    if mode == "offline":
        sys.exit(1)
else:
    raise AssertionError(cmd)
print(json.dumps(dict(result=True, item=item)))
"""


@pytest.fixture
def interaction(corpus, tmp_path):  # noqa: F811
    batch, catalog, _, _ = corpus
    publish(catalog, build([batch], catalog))
    item_file = tmp_path / "item.json"
    item_file.write_text(
        json.dumps(
            {
                "_id": 42,
                "collection": {"$id": -1},
                "link": "https://x.com/i/status/101",
                "excerpt": "Old caption",
                "note": "Keep note",
                "tags": ["keep"],
                "important": True,
                "highlights": [{"text": "keep"}],
                "extension": {"flag": True},
                "lastUpdate": "old",
            }
        )
    )
    script = tmp_path / "skill.sh"
    provider = tmp_path / "provider.py"
    provider.write_text(FAKE)
    script.write_text(
        "set -eu\n[[ $1 == --json && $2 == --delay && $3 == 600 ]]\n"
        'shift 3\nexec "$PYTHON" "$SYNTHETIC_PROVIDER/provider.py" "$@"\n'
    )
    prefix = [
        str(Path(sys.executable).with_name("x-migrator")),
        "bookmark-correction",
        "--raindrop-script",
        str(script),
        "--catalog-dir",
        str(catalog.root),
        "--raindrop-ledger-dir",
        str(tmp_path / "ledger"),
    ]

    def call(*args, ok=True):
        result = subprocess.run(
            [*prefix, *args],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHON": sys.executable, "SYNTHETIC_PROVIDER": str(tmp_path)},
        )
        if ok:
            assert result.returncode == 0, result.stderr
            return json.loads(result.stdout)
        assert result.returncode != 0, result.stdout
        return result.stderr

    return call, item_file, catalog, batch


def test_attachment_decision_restart_and_rebuild(interaction, tmp_path):
    call, item_file, catalog, batch = interaction
    before = json.loads(item_file.read_text())
    inspected = call("inspect", "--link", before["link"])
    assert inspected["raindrop_url"] == LINK and inspected["source_post_id"] == "101"
    photo = next(a for a in inspected["attachments"] if a["type"] == "photo")
    assert call("preview", "--link", LINK, "--description", WORDING, ok=False)
    args = [
        "preview",
        "--link",
        LINK,
        "--asset-id",
        photo["asset_id"],
        "--sha256",
        photo["sha256"],
        "--description",
        WORDING,
    ]
    preview = call(*args)
    assert preview["excerpt"] == before["excerpt"] and preview["description"] == WORDING
    assert call("decline", "--preview-id", preview["preview_id"])["status"] == "declined"
    call("approve", "--preview-id", preview["preview_id"], ok=False)
    assert json.loads(item_file.read_text()) == before
    assert catalog.read("annotations.private.json") is None
    edit = call(*args)["preview_id"]
    fault = tmp_path / "fault"
    fault.write_text("lost")
    call("approve", "--preview-id", edit, ok=False)
    assert catalog.read("annotations.private.json") is None
    fault.unlink()
    result = call("approve", "--preview-id", edit)
    assert result["status"] == "verified" and result["raindrop_id"] == 42
    assert result["asset_id"] == photo["asset_id"] and result["sha256"] == photo["sha256"]
    assert call("approve", "--preview-id", edit) == result
    assert (tmp_path / "calls").read_text().splitlines().count("update-excerpt-json") == 1
    assert json.loads(item_file.read_text()) == {**before, "excerpt": WORDING, "lastUpdate": "new"}
    assert list(catalog.read("annotations.private.json")["description_edit_history"]) == [edit]
    rebuilt = build([batch], catalog)
    assert read_catalog(catalog) == rebuilt
    publish(catalog, rebuilt)
    media = rebuilt[0]["versions"][0]["media"]
    assert [
        (a["asset_id"], a["description_sha256"]) for a in media if a["description"] == WORDING
    ] == [(photo["asset_id"], photo["sha256"])]


@pytest.mark.parametrize("pid", [None, "103"])
def test_bookmark_without_local_media(interaction, tmp_path, pid):
    call, item_file, catalog, batch = interaction
    item = json.loads(item_file.read_text())
    item["link"] = "https://example.org/article" if pid is None else f"https://x.com/i/status/{pid}"
    item_file.write_text(json.dumps(item))
    old = catalog.path("catalog.jsonl").read_bytes()
    preview = call("preview", "--link", LINK, "--description", WORDING)
    result = call("approve", "--preview-id", preview["preview_id"])
    assert result["source_post_id"] == pid and result["status"] == "verified"
    assert json.loads(item_file.read_text()) == {**item, "excerpt": WORDING, "lastUpdate": "new"}
    (journal,) = (tmp_path / "ledger").glob("correction-*.private.json")
    assert json.loads(journal.read_text())["description"] == WORDING
    if pid is None:
        assert catalog.read("annotations.private.json") is None
        assert catalog.path("catalog.jsonl").read_bytes() == old
    else:
        rebuilt = build([batch], catalog)
        assert read_catalog(catalog) == rebuilt
        assert next(r for r in rebuilt if r["post_id"] == pid)["approved_description"] == WORDING


@pytest.mark.parametrize("fault", ["drift", "unapplied", "reject", "stale", "bytes"])
def test_failed_decision_never_reissues_put(interaction, tmp_path, fault):
    call, item_file, catalog, _ = interaction
    asset = next(a for a in call("inspect", "--link", LINK)["attachments"] if a["type"] == "photo")
    edit = call(
        "preview",
        "--link",
        LINK,
        "--description",
        WORDING,
        "--asset-id",
        asset["asset_id"],
        "--sha256",
        asset["sha256"],
    )["preview_id"]
    if fault == "stale":
        item = json.loads(item_file.read_text())
        item_file.write_text(json.dumps({**item, "note": "Concurrent owner edit"}))
    elif fault == "bytes":
        (tmp_path / "restored" / "101_101_01.png").write_bytes(b"changed bytes")
    else:
        (tmp_path / "fault").write_text(fault)
    for _ in range(2):
        call("approve", "--preview-id", edit, ok=False)
    writes = (tmp_path / "calls").read_text().splitlines().count("update-excerpt-json")
    assert writes == (0 if fault in ("stale", "bytes") else 1)
    assert catalog.read("annotations.private.json") is None
