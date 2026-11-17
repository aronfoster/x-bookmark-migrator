"""Source-bound verification and offline diagnostics consolidated from concurrent PR #12."""

import json
import sys
from pathlib import Path

import pytest
import test_catalog
from test_archive import storage  # noqa: F401

from x_bookmark_migrator import catalog, cli
from x_bookmark_migrator.core import PilotError, Store


@pytest.fixture
def corpus(storage, tmp_path):  # noqa: F811
    return test_catalog.corpus.__wrapped__(storage, tmp_path)


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_catalog_verify_cli_checks_raw_projections_without_republication(
    corpus,
    monkeypatch,
    capsys,
):
    batch, output, restored, _ = corpus
    output.write(
        "annotations.private.json",
        {
            "category_overrides": {"101": "Owner label"},
            "101:1:photo-1": {"browse_visibility": "hidden", "description": "Preserved"},
        },
    )
    records = catalog.build([batch], output)
    catalog.publish(output, records)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "x-migrator",
            "catalog-verify",
            "--catalog-dir",
            str(output.root),
            "--batch",
            str(batch[0]),
            str(batch[1]),
            "--expected-records",
            "3",
        ],
    )
    # Create the normal empty lock before comparing the files.
    with output.locked():
        pass
    before = files(output.root), files(restored.root)
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["reader_access"] == "not_tested"
    assert (files(output.root), files(restored.root)) == before
    # Legacy read_catalog upgrades source fields in memory: verification must
    # instead catch the wrong persisted sidecar/catalog bytes.
    path = output.path("catalog.jsonl")
    path.write_bytes(
        path.read_bytes().replace(b'"source_post_id": "101"', b'"source_post_id": "999"')
    )
    assert cli.main() == 1


@pytest.mark.parametrize(
    "changed", ["source", "attachment", "category", "sidecar", "annotations", "population"]
)
def test_catalog_verify_detects_stale_or_mixed_publication(corpus, changed):
    batch, output, _, _ = corpus
    output.write("annotations.private.json", {"category_overrides": {"101": "Owner label"}})
    records = catalog.build([batch], output)
    catalog.publish(output, records)
    if changed == "source":
        p = output.path("index.html")
        p.write_text(
            p.read_text().replace("https://x.com/i/status/101", "https://x.com/i/status/104")
        )
    elif changed == "attachment":
        p = output.path("index.html")
        p.write_text(p.read_text().replace("../restored/", "../wrong/"))
    elif changed == "category":
        p = output.path("index.html")
        p.write_text(p.read_text().replace("Owner label", "Wrong label"))
    elif changed == "sidecar":
        output.path("source-links.jsonl").write_text("")
    elif changed == "annotations":
        output.write("annotations.private.json", {"category_overrides": {"101": "New label"}})
    with pytest.raises(PilotError):
        catalog.verify_publication(
            output, catalog.build([batch], output), 100 if changed == "population" else 3
        )


@pytest.mark.parametrize(
    "case",
    ["valid", "duplicate", "unsupported", "trash", "mixed", "root", "scope", "items", "item"],
)
def test_runbook_inventory_diagnostics_use_shared_offline_contract(
    tmp_path, monkeypatch, capsys, case
):
    output = Store(tmp_path / "inventory")
    item = {
        "_id": 1,
        "link": "https://twitter.com/old/status/101/photo/1",
        "collection": {"$id": -1},
    }
    active, trash = [item], []
    if case == "duplicate":
        active.append({**item, "_id": 2, "link": "https://x.com/new/status/101"})
    elif case in ("unsupported", "mixed"):
        active.extend(
            [
                {**item, "_id": 2, "link": "https://x.com/example"},
                {**item, "_id": 3, "link": "https://t.co/example"},
            ]
        )
    elif case == "trash":
        active, trash = [], [{**item, "collection": {"$id": -99}}]
    if case == "mixed":
        active.append({**item, "_id": 4, "link": "https://x.com/new/status/101"})
        trash.append({**item, "_id": 5, "collection": {"$id": -99}})
    snapshot = {
        "schema_version": 1,
        "result": True,
        "observed_at": "2026-11-17T00:00:00Z",
        "coverage": {"complete": True, "stable_passes": 2, "perpage": 50},
        "active": {"items": active, "count": len(active), "pages": 1},
        "trash": {"items": trash, "count": len(trash), "pages": 1},
    }
    if case == "root":
        snapshot = []
    elif case == "scope":
        snapshot["active"] = []
    elif case == "items":
        snapshot["active"]["items"] = None
    elif case == "item":
        snapshot["active"]["items"] = [None]
    output.write("inventory-before.private.json", snapshot)
    before = files(output.root)
    monkeypatch.setenv("RUN", str(output.root))
    doc = (Path(__file__).parents[1] / "docs/READINESS.md").read_text()
    snippet = doc.split('RUN="$RUN" "$XM_PYTHON" - <<\'PY\'\n', 1)[1].split("\nPY\n", 1)[0]
    with pytest.raises(SystemExit) as end:
        exec(compile(snippet, "inventory-runbook", "exec"), {})
    summary = json.loads(capsys.readouterr().out)
    result = output.read("inventory-diagnostics.private.json")
    assert result["create_authorized"] is False
    assert result["observed_groups_authoritative"] is False
    assert output.path("inventory-diagnostics.private.json").stat().st_mode & 0o777 == 0o600
    assert all(output.path(name).read_bytes() == raw for name, raw in before.items())
    assert "https://" not in json.dumps(result)
    if case in ("unsupported", "mixed"):
        assert end.value.code == 1 and summary["conflicts"] is None
        assert len(result["unsupported"]) == 2
        assert result["exact_primary_checks"]["101"]["state"] == "incomplete"
        assert result["observed_primary_groups"]["101"] == (
            {"active": [1, 4], "trash": [5]} if case == "mixed" else {"active": [1], "trash": []}
        )
    elif case in ("root", "scope", "items", "item"):
        assert end.value.code == 1 and not result["coverage_valid"]
        assert result["malformed"]
        assert not result["observed_primary_groups"]
    elif case == "duplicate":
        assert end.value.code == 1 and summary["conflicts"] == 1
    else:
        assert end.value.code == (1 if case == "trash" else 0)
        assert result["exact_primary_checks"]["101"]["state"] == (
            "trash" if case == "trash" else "active"
        )
