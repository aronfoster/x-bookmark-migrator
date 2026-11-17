"""Synthetic desktop handoff checks; no account or private corpus access."""

import json
import sys
from pathlib import Path

import pytest
import test_catalog
from test_archive import storage  # noqa: F401
from test_repair import cli_args, native

from x_bookmark_migrator import catalog, cli, repair_cli
from x_bookmark_migrator.core import PilotError, Store
from x_bookmark_migrator.drive import digest
from x_bookmark_migrator.publication import PublicationStore
from x_bookmark_migrator.repair import scan_native, scan_native_helper_audit


@pytest.fixture
def corpus(storage, tmp_path):  # noqa: F811
    return test_catalog.corpus.__wrapped__(storage, tmp_path)


def file_ref(path, **extra):
    return {"path": str(path), "sha256": digest(path.read_bytes()), **extra}


def helper_audit(tmp_path, state):
    evidence = Store(tmp_path / "helper-review")
    source = evidence.path("wrapper.py")
    source.write_text("# Synthetic reviewed wrapper delegates to native apply_plan.\n")
    source.chmod(0o600)
    attempts = scan_native(state.root)
    evidence.write("run.private.json", {"attempts": attempts, "blocked_before_intent": 1})
    evidence.write("marker.private.json", {"run": "run.private.json"})
    evidence.write(
        "audit.private.json",
        {
            "schema_version": 1,
            "mode": "native_delegation",
            "all_intents_included": True,
            "all_helpers_delegate_to_native": True,
            "review_note": "Synthetic source/rows reviewed; one blocked row sent no request.",
            "artifacts": [
                file_ref(source, role="helper_source"),
                file_ref(evidence.path("run.private.json"), role="run_report"),
                file_ref(evidence.path("marker.private.json"), role="summary_marker"),
            ],
            "native_intents": [a["evidence"][0] for a in attempts],
        },
    )
    return evidence, evidence.path("audit.private.json")


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


def test_repair_accepts_reader_group_catalog_without_changing_publications(corpus, tmp_path):
    args = cli_args(corpus, tmp_path)
    args.catalog_dir.chmod(0o2750)
    output = PublicationStore(args.catalog_dir)
    output.write("annotations.private.json", {"category_overrides": {"101": "Owner label"}})
    catalog.publish(output, catalog.build(args.batch, output))
    with output.locked():
        pass
    before = files(output.root)
    repair_cli.run(args)
    assert files(output.root) == before
    assert output.root.stat().st_mode & 0o7777 == 0o2750
    assert output.path("catalog.jsonl").stat().st_mode & 0o777 == 0o640
    assert output.path("annotations.private.json").stat().st_mode & 0o777 == 0o600


def test_native_delegation_counts_native_attempts_once_and_retains_unknowns(corpus, tmp_path):
    args = cli_args(corpus, tmp_path)
    state = Store(args.removal_dir[0])
    native(state, "102", 200, {"data": {"bookmarked": False}})
    evidence, args.native_helper_audit = helper_audit(tmp_path, state)
    args.all_removal_sources_confirmed = True
    before = files(state.root), files(evidence.root)
    summary = repair_cli.run(args)
    assert summary["removal_coverage_complete"] is True
    report = json.loads(next(args.output_dir.glob("repair-audit-*.private.json")).read_text())
    assert sum(len(r["attempts"]) for r in report["rows"]) == 2
    assert {r["source_post_id"]: r["deletion_outcome"] for r in report["rows"]} == {
        "101": "outcome_unknown",
        "102": "confirmed_removed",
        "103": "never_attempted",
    }
    proof = report["coverage"]["native_helper_audit"]
    assert proof["additional_attempts"] == 0
    assert proof["helper_retirement_verified"] is False
    assert (files(state.root), files(evidence.root)) == before


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "hash", "source", "role", "attestation", "extra"]
)
def test_native_delegation_rejects_incomplete_or_changed_evidence(tmp_path, fault):
    state = Store(tmp_path / "native")
    native(state, "101")
    native(state, "102")
    evidence, path = helper_audit(tmp_path, state)
    spec = evidence.read(path.name)
    if fault == "missing":
        spec["native_intents"].pop()
    elif fault == "duplicate":
        spec["native_intents"].append(spec["native_intents"][0])
    elif fault == "hash":
        spec["native_intents"][0]["sha256"] = "0" * 64
    elif fault == "source":
        evidence.path("wrapper.py").write_text("# Changed since review\n")
    elif fault == "role":
        spec["artifacts"] = spec["artifacts"][1:]
    elif fault == "attestation":
        spec["all_helpers_delegate_to_native"] = False
    else:
        native(state, "103")
    evidence.write(path.name, spec)
    with pytest.raises(PilotError):
        scan_native_helper_audit(path, scan_native(state.root))


def test_helper_overlap_fails_before_catalog_or_provider_initialization(
    corpus, tmp_path, monkeypatch
):
    args = cli_args(corpus, tmp_path)
    evidence, args.native_helper_audit = helper_audit(tmp_path, Store(args.removal_dir[0]))
    args.output_dir = evidence.root

    def forbidden(*a, **kw):
        pytest.fail("Must reject overlap before publication/provider initialization")

    monkeypatch.setattr(repair_cli, "PublicationStore", forbidden)
    monkeypatch.setattr(repair_cli, "shared_raindrop", forbidden)
    with pytest.raises(PilotError, match="overlap"):
        repair_cli.run(args)


@pytest.mark.parametrize("case", ["valid", "duplicate", "unsupported", "trash"])
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
    elif case == "unsupported":
        active.extend(
            [
                {**item, "_id": 2, "link": "https://x.com/example"},
                {**item, "_id": 3, "link": "https://t.co/example"},
            ]
        )
    elif case == "trash":
        active, trash = [], [{**item, "collection": {"$id": -99}}]
    output.write(
        "inventory-before.private.json",
        {
            "schema_version": 1,
            "result": True,
            "observed_at": "2026-11-17T00:00:00Z",
            "coverage": {"complete": True, "stable_passes": 2, "perpage": 50},
            "active": {"items": active, "count": len(active), "pages": 1},
            "trash": {"items": trash, "count": len(trash), "pages": 1},
        },
    )
    monkeypatch.setenv("RUN", str(output.root))
    doc = (Path(__file__).parents[1] / "docs/DESKTOP_REPAIR.md").read_text()
    snippet = doc.split("RUN=\"$RUN\" uv run python - <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    with pytest.raises(SystemExit) as end:
        exec(compile(snippet, "inventory-runbook", "exec"), {})
    summary = json.loads(capsys.readouterr().out)
    result = output.read("inventory-diagnostics.private.json")
    assert result["create_authorized"] is False
    if case == "unsupported":
        assert end.value.code == 1 and summary["conflicts"] is None
        assert len(result["unsupported"]) == 2
        assert result["exact_primary_checks"]["101"]["state"] == "incomplete"
    elif case == "duplicate":
        assert end.value.code == 1 and summary["conflicts"] == 1
    else:
        assert end.value.code == 0
        assert result["exact_primary_checks"]["101"]["state"] == (
            "trash" if case == "trash" else "active"
        )
