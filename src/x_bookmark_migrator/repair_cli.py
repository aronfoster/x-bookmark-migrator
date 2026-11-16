"""Offline repair audit entry point. Provider execution is a separate explicit command."""

import json
from pathlib import Path

from .catalog import build, private_bytes
from .core import PilotError, Store
from .repair import backfill, reconcile, saved_raindrop_inspector, scan_helper, scan_native

COMMANDS = ("repair-audit", "repair-backfill")


def add_commands(sub):
    for command in COMMANDS:
        p = sub.add_parser(command, help="Historical audit or explicitly confirmed Raindrop repair")
        p.add_argument("--catalog-dir", type=Path, required=True)
        p.add_argument("--output-dir", type=Path, required=True)
        p.add_argument("--batch", nargs=2, action="append", required=True)
        p.add_argument("--removal-dir", type=Path, action="append", required=True)
        p.add_argument("--helper-format", type=Path, action="append", default=[])
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


def run(args):
    if args.command == "repair-backfill" and not args.confirm_raindrop_writes:
        raise PilotError("Backfill requires explicit --confirm-raindrop-writes.")
    if args.expected_records < 1:
        raise PilotError("Expected archive population must be positive.")
    catalog, output = Store(args.catalog_dir), Store(args.output_dir)
    if (
        catalog.root == output.root
        or any(output.root == Path(b[0]).resolve() for b in args.batch)
        or any(output.root == d.resolve() for d in args.removal_dir)
    ):
        raise PilotError("Audit output must be separate from source evidence and catalog.")
    with catalog.locked(), output.locked():
        records = build(args.batch, catalog)
        attempts = []
        for directory in dict.fromkeys(args.removal_dir):
            attempts.extend(scan_native(directory))
        for descriptor in dict.fromkeys(args.helper_format):
            attempts.extend(scan_helper(descriptor))
        blockers = []
        if not args.all_removal_sources_confirmed:
            blockers.append("all_removal_sources_not_attested")
        if not args.helper_format:
            blockers.append("private_batch_helper_not_audited")
        if len(records) != args.expected_records:
            blockers.append("archive_population_mismatch")
        decisions = json.loads(private_bytes(args.classifications)) if args.classifications else {}
        report = reconcile(
            records,
            attempts,
            decisions=decisions,
            coverage={"complete": not blockers, "blockers": blockers},
            inspect_raindrop=offline_inspector(args, records),
        )
        output.write("repair-audit.private.json", report)
        if args.command == "repair-backfill":
            module = shared_raindrop()
            adapter = module.RaindropAdapter(args.raindrop_ledger_dir, args.raindrop_script)
            result = backfill(report, adapter, args.post_id, confirmed=True, limit=args.limit)
            output.write("repair-backfill.private.json", result)
            return result["summary"]
        return {
            **report["summary"],
            "blocked": not report["summary"]["offline_reconciliation_complete"],
        }
