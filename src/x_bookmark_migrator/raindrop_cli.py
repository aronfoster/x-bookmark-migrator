"""Explicit Raindrop-only operations; no X calls or deletion permission."""

import json
import os
from pathlib import Path

from .catalog import private_bytes
from .core import PilotError
from .raindrop import RaindropAdapter
from .source import source_url

COMMANDS = ("raindrop-inventory", "raindrop-ensure", "raindrop-verify")


def adapter_options(parser):
    parser.add_argument("--raindrop-ledger-dir", type=Path, required=True)
    parser.add_argument("--raindrop-script", type=Path, default=os.environ.get("RAINDROP_SCRIPT"))


def adapter_from_args(args):
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
            p.add_argument("--category")
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
        receipt = adapter.ensure_raindrop(
            args.post_id,
            source_url(args.post_id),
            json.loads(private_bytes(args.archive_ref_file)),
            category=args.category,
        )
        return {"verified": True, "disposition": receipt["disposition"], "x_api_calls": 0}
    adapter.verify_raindrop_now(args.post_id, args.raindrop_id)
    return {"verified": True, "x_api_calls": 0, "deletion_authorized": False}
