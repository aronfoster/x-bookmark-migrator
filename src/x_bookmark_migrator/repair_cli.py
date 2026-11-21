"""Offline repair audit entry point. Provider execution is a separate explicit command."""

import json
import secrets
import sys
import time
from pathlib import Path

from .catalog import build, link_raindrop, private_bytes, read_catalog
from .core import PilotError, Store
from .publication import PublicationStore
from .repair import (
    audit_native_helpers,
    backfill,
    backfill_selection,
    reconcile,
    saved_raindrop_inspector,
    scan_helper,
    scan_native,
)

COMMANDS = ("repair-audit", "repair-backfill")


def add_commands(sub):
    for command in COMMANDS:
        p = sub.add_parser(command, help="Historical audit or explicitly confirmed Raindrop repair")
        p.add_argument("--catalog-dir", type=Path, required=True)
        p.add_argument("--output-dir", type=Path, required=True)
        p.add_argument("--batch", nargs=2, action="append", required=True)
        p.add_argument("--removal-dir", type=Path, action="append", required=True)
        p.add_argument("--helper-format", type=Path, action="append", default=[])
        p.add_argument("--native-helper-provenance", type=Path)
        p.add_argument("--classifications", type=Path)
        p.add_argument("--expected-records", type=int, default=100)
        p.add_argument("--all-removal-sources-confirmed", action="store_true")
        p.add_argument("--raindrop-inventory", type=Path)
        p.add_argument("--raindrop-receipt-dir", type=Path)
        if command == "repair-backfill":
            p.add_argument("--raindrop-ledger-dir", type=Path, required=True)
            p.add_argument("--raindrop-script", type=Path, required=True)
            p.add_argument("--post-id", action="append", required=True)
            p.add_argument("--limit", type=int, default=3)
            p.add_argument("--confirm-raindrop-writes", action="store_true")


def shared_raindrop():
    try:
        from . import raindrop
    except ImportError:
        raise PilotError(
            "FOS-166 shared Raindrop adapter is required; no fallback client."
        ) from None
    return raindrop


def offline_inspector(args, records):
    if not args.raindrop_inventory:
        if args.raindrop_receipt_dir:
            raise PilotError("Saved receipts need a complete saved inventory for reconciliation.")
        return None
    module = shared_raindrop()
    snapshot = json.loads(private_bytes(args.raindrop_inventory))
    receipts = {}
    if args.raindrop_receipt_dir:
        for record in records:
            pid = record["post_id"]
            path = args.raindrop_receipt_dir / f"raindrop-{pid}.receipt.private.json"
            if path.exists():
                receipts[pid] = json.loads(private_bytes(path))
    return saved_raindrop_inspector(
        snapshot, receipts, module.inspect_inventory, module.validate_receipt
    )


def overlap(a, b):
    return a.is_relative_to(b) or b.is_relative_to(a)


def run(args):
    if args.command == "repair-backfill" and not args.confirm_raindrop_writes:
        raise PilotError("Backfill requires explicit --confirm-raindrop-writes.")
    if args.expected_records < 1:
        raise PilotError("Expected archive population must be positive.")
    # Resolve aliases and reject overlap before Store creates/chmods directories
    # or acquires locks. Sibling evidence files may share a parent with output.
    catalog_root, output_root = args.catalog_dir.resolve(), args.output_dir.resolve()
    ledger = getattr(args, "raindrop_ledger_dir", None)
    writable = [catalog_root, output_root] + ([ledger.resolve()] if ledger else [])
    if any(overlap(a, b) for i, a in enumerate(writable) for b in writable[i + 1 :]):
        raise PilotError("Repair catalog, output and ledger directories must not overlap.")
    sources = [Path(p).resolve() for batch in args.batch for p in batch]
    directories = list(dict.fromkeys(d.resolve() for d in args.removal_dir))
    descriptors = list(dict.fromkeys(d.resolve() for d in args.helper_format))
    sources.extend(directories + descriptors)
    sources.extend(
        p.resolve()
        for p in (
            args.classifications,
            args.raindrop_inventory,
            args.raindrop_receipt_dir,
            args.native_helper_provenance,
        )
        if p is not None
    )
    if any(overlap(output_root, path) for path in sources):
        raise PilotError("Audit output must not overlap source evidence.")
    attempts, seen_files = [], set()
    for directory in directories:
        attempts.extend(scan_native(directory))
    provenance = None
    if args.native_helper_provenance:
        provenance = audit_native_helpers(
            args.native_helper_provenance, attempts, seen_files=seen_files
        )
    for descriptor in descriptors:
        attempts.extend(scan_helper(descriptor, seen_files=seen_files))
    if any(overlap(output_root, Path(path)) for path in seen_files):
        raise PilotError("Audit output must not overlap helper source evidence.")
    catalog, output = PublicationStore(catalog_root), Store(output_root)
    run_id = secrets.token_hex(16)
    with catalog.locked(), output.locked():
        records = build(args.batch, catalog)
        blockers = []
        if not args.all_removal_sources_confirmed:
            blockers.append("all_removal_sources_not_attested")
        if not args.helper_format and provenance is None:
            blockers.append("private_batch_helper_not_audited")
        if len(records) != args.expected_records:
            blockers.append("archive_population_mismatch")
        if provenance is not None:
            population = {record["post_id"] for record in records}
            if any(stop["post_id"] not in population for stop in provenance["pre_intent_stops"]):
                blockers.append("pre_intent_stop_outside_catalog")
        decisions = json.loads(private_bytes(args.classifications)) if args.classifications else {}
        report = reconcile(
            records,
            attempts,
            decisions=decisions,
            coverage={"complete": not blockers, "blockers": blockers},
            inspect_raindrop=offline_inspector(args, records),
        )
        if provenance is not None:
            report["native_helper_provenance"] = provenance
        output.write(f"repair-audit-{run_id}.private.json", report)
        if args.command == "repair-backfill":
            published = read_catalog(catalog)
            if set(args.post_id) - {r["post_id"] for r in published}:
                raise PilotError("Selected primary is absent from the published catalog.")
            module = shared_raindrop()
            adapter = module.RaindropAdapter(args.raindrop_ledger_dir, args.raindrop_script)
            backfill_selection(report, args.post_id, confirmed=True, limit=args.limit)
            local_links = (catalog.read("annotations.private.json") or {}).get("raindrop_links", {})
            for pid in args.post_id:
                if pid in local_links:
                    local = module.validate_receipt(local_links[pid], pid)
                    canonical = adapter.store.read(f"raindrop-{pid}.receipt.private.json")
                    if (
                        canonical is None
                        or module.validate_receipt(canonical, pid)["raindrop_id"]
                        != local["raindrop_id"]
                    ):
                        raise PilotError("Conflicting local and ledger Raindrop association.")
            started = time.monotonic()
            progress = {
                "selected": len(args.post_id),
                "results": [],
                "complete": False,
                "stage": "inventory",
            }
            progress_name = f"repair-progress-{run_id}.private.json"

            def checkpoint(entry):
                progress["results"].append(entry)
                output.write(progress_name, progress)
                if entry["state"] == "verified":
                    progress["stage"] = "local_publication"
                    output.write(progress_name, progress)
                    link_raindrop(catalog, published, entry["receipt"])
                    entry["local_link_persisted"] = True
                    output.write(progress_name, progress)
                completed = sum(e.get("local_link_persisted", False) for e in progress["results"])
                print(
                    f"Repair: {completed}/{len(args.post_id)} local links verified; "
                    f"{len(progress['results'])} processed; "
                    f"{int(time.monotonic() - started)}s elapsed.",
                    file=sys.stderr,
                    flush=True,
                )
                if entry["state"] == "blocked":
                    print(
                        json.dumps(
                            {
                                key: entry[key]
                                for key in ("reason", "metadata_mismatch", "provider")
                                if key in entry
                            }
                        ),
                        file=sys.stderr,
                        flush=True,
                    )
                progress["stage"] = "item_verification"

            print(
                "Repair: validating one active/Trash inventory (two-pass pagination); "
                "then targeted item checks.",
                file=sys.stderr,
                flush=True,
            )
            try:
                output.write(progress_name, progress)
                with adapter.repair_session(args.post_id) as stats:
                    progress["provider"] = stats
                    progress["stage"] = "item_verification"
                    output.write(progress_name, progress)
                    result = backfill(
                        report,
                        adapter,
                        args.post_id,
                        confirmed=True,
                        limit=args.limit,
                        on_result=checkpoint,
                    )
                result["summary"]["local_links_persisted"] = sum(
                    e.get("local_link_persisted", False) for e in result["results"]
                )
                result["summary"]["elapsed_seconds"] = round(time.monotonic() - started, 3)
                result["summary"]["provider"] = dict(adapter.repair_stats)
                output.write(f"repair-backfill-{run_id}.private.json", result)
                progress["complete"] = not result["summary"]["blocked"]
                progress["stage"] = "complete" if progress["complete"] else "blocked"
                output.write(progress_name, progress)
            except Exception:
                # The pending result/receipt remains authoritative even if this
                # final diagnostic checkpoint itself encounters a full disk.
                try:
                    progress["stopped"] = True
                    progress["provider"] = dict(adapter.repair_stats)
                    output.write(progress_name, progress)
                except (OSError, PilotError):
                    pass
                print(
                    "Repair stopped; completed links and provider receipts are retained. "
                    "No further items attempted.",
                    file=sys.stderr,
                    flush=True,
                )
                raise
            return result["summary"]
        return {
            **report["summary"],
            "blocked": not report["summary"]["offline_reconciliation_complete"],
        }
