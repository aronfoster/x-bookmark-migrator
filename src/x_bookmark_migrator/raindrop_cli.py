"""Explicit Raindrop-only operations; no X calls or deletion permission."""

import json
import os
from pathlib import Path

from .catalog import checked_bytes, link_raindrop, primary_post_text, private_bytes, read_catalog
from .core import PilotError
from .publication import PublicationStore
from .raindrop import RaindropAdapter, require_distinct_state_dirs
from .source import source_url

COMMANDS = ("raindrop-inventory", "raindrop-ensure", "raindrop-verify")


def adapter_options(parser):
    parser.add_argument("--raindrop-ledger-dir", type=Path, required=True)
    parser.add_argument("--raindrop-script", type=Path, default=os.environ.get("RAINDROP_SCRIPT"))


def adapter_from_args(args):
    require_distinct_state_dirs(
        state_dir=getattr(args, "state_dir", None),
        catalog_dir=getattr(args, "catalog_dir", None),
        raindrop_ledger_dir=args.raindrop_ledger_dir,
    )
    if not args.raindrop_script:
        raise PilotError("Set RAINDROP_SCRIPT or --raindrop-script to the existing skill script.")
    return RaindropAdapter(args.raindrop_ledger_dir, args.raindrop_script)


def add_commands(sub):
    for command in COMMANDS:
        p = sub.add_parser(command)
        adapter_options(p)
        if command != "raindrop-inventory":
            p.add_argument("--post-id", required=True)
        if command == "raindrop-ensure":
            p.add_argument("--archive-ref-file", type=Path, required=True)
            p.add_argument("--payload-file", type=Path, required=True)
            p.add_argument("--catalog-dir", type=Path, required=True)
            p.add_argument(
                "--category", help="Legacy compatibility option; not added to Raindrop notes."
            )
            p.add_argument("--confirm-raindrop-create", action="store_true")
        elif command == "raindrop-verify":
            p.add_argument("--raindrop-id", type=int, required=True)


def run(args):
    adapter = adapter_from_args(args)
    if args.command == "raindrop-inventory":
        snapshot = adapter.enumerate_now()
        return {
            "active": snapshot["active"]["count"],
            "trash": snapshot["trash"]["count"],
            "private_inventory": "raindrop-inventory.private.json",
            "x_api_calls": 0,
        }
    if args.command == "raindrop-ensure":
        if not args.confirm_raindrop_create:
            raise PilotError("Raindrop writes require --confirm-raindrop-create.")
        archive_ref = json.loads(private_bytes(args.archive_ref_file))
        if not isinstance(archive_ref, dict) or set(archive_ref) != {"manifest", "payload"}:
            raise PilotError("An archive manifest and payload receipt are required.")
        payload = json.loads(checked_bytes(args.payload_file, archive_ref["payload"]))
        if payload.get("post_id") != args.post_id:
            raise PilotError("Archive payload does not match the saved primary.")
        catalog = PublicationStore(args.catalog_dir)
        with catalog.locked():
            records = read_catalog(catalog)
            if args.post_id not in {r["post_id"] for r in records}:
                raise PilotError("Saved primary is absent from the local catalog.")
            receipt = adapter.ensure_raindrop(
                args.post_id,
                source_url(args.post_id),
                archive_ref,
                category=args.category,
                post_text=primary_post_text(payload["posts"], args.post_id),
            )
            link_raindrop(catalog, records, receipt)
        return {"verified": True, "disposition": receipt["disposition"], "x_api_calls": 0}
    adapter.verify_raindrop_now(args.post_id, args.raindrop_id)
    return {"verified": True, "x_api_calls": 0, "deletion_authorized": False}
