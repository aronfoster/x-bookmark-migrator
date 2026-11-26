"""Archive commands are separate from the paid X-read CLI options."""

import os
import secrets

import httpx

from .archive import archive_batch, import_pilot, restore_batch
from .archive_repair import preview_repairs, select_repairs, write_repairs
from .core import PilotError, Store
from .drive import MAX_BYTES, Drive, encode
from .inventory import Inventory

COMMANDS = ("archive", "archive-probe", "archive-import-pilot", "archive-restore", "archive-repair")


def add_commands(sub):
    for name in COMMANDS:
        parser = sub.add_parser(name)
        parser.add_argument(
            "--drive-folder-id", default=os.environ.get("X_ARCHIVE_DRIVE_FOLDER_ID")
        )
        parser.add_argument("--max-asset-bytes", type=int, default=64 * 1024 * 1024)
        if name == "archive-repair":
            parser.add_argument(
                "--batch",
                nargs=2,
                metavar=("RESTORE_DIR", "RECEIPT"),
                action="append",
                required=True,
            )
            parser.add_argument("--reason", choices=("byte_limit", "media_count"), required=True)
            parser.add_argument(
                "--item",
                type=int,
                help="Select one matching record by 1-based position, sorted by primary ID",
            )
            parser.add_argument(
                "--apply-plan",
                metavar="SHA256",
                help="Confirm the reviewed preview and write a successor Drive batch",
            )
        elif name == "archive":
            parser.add_argument("--post-id", action="append", dest="post_ids")
            parser.add_argument("--max-posts", type=int, default=5)
        elif name in ("archive-restore", "archive-import-pilot"):
            parser.add_argument("--manifest-id", required=True)
            if name == "archive-restore":
                parser.add_argument("--output-dir", required=True)
                parser.add_argument("--manifest-sha256", required=True)


def run(args):
    from pathlib import Path

    if args.command == "archive-repair":
        if not 1024 <= args.max_asset_bytes <= MAX_BYTES:
            raise PilotError("Unsupported repair byte limit.")
        selected = select_repairs(args.batch, args.reason)
        if args.item is not None:
            if not 1 <= args.item <= len(selected):
                raise PilotError("Repair item position outside the selected sources.")
            selected = [selected[args.item - 1]]
        preview = preview_repairs(selected, args.max_asset_bytes)
        if args.apply_plan is None:
            return preview
        if args.apply_plan != preview["plan_sha256"]:
            raise PilotError("Repair plan differs from reviewed preview.")
    if not args.drive_folder_id:
        raise PilotError("Set X_ARCHIVE_DRIVE_FOLDER_ID or --drive-folder-id.")
    store = Store(args.state_dir)
    with (
        store.locked(),
        httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as storage_http,
        httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as media_http,
    ):
        drive = Drive(store, storage_http, args.drive_folder_id, args.max_asset_bytes)
        context = drive.preflight()
        if args.command == "archive-repair":
            return write_repairs(store, drive, media_http, selected)
        if args.command == "archive-probe":
            probe = {
                "probe": secrets.token_hex(32),
                "runtime_uid": os.getuid(),
                "in_container": Path("/.dockerenv").exists(),
                "destination": context,
            }
            receipt = drive.save(
                "probes",
                "probe-" + secrets.token_hex(6) + ".json",
                encode(probe),
                "application/json",
            )
            # A new backend instance forces another OAuth exchange and GET, not a cache hit.
            fresh = Drive(store, storage_http, args.drive_folder_id, args.max_asset_bytes)
            fresh.preflight()
            fresh.verify(receipt)
            store.write(
                "drive-probe-" + secrets.token_hex(12) + ".private.json",
                {"probe": probe, "receipt": receipt},
            )
            return {
                "storage_probe": "passed",
                "same_runtime_readback": True,
                "production_runtime_identity": "operator_must_confirm",
                "x_api_calls": 0,
            }
        if args.command == "archive-import-pilot":
            return import_pilot(drive, store, args.manifest_id)
        if args.command == "archive-restore":
            output = Store(Path(args.output_dir))
            with output.locked():
                return restore_batch(drive, args.manifest_id, output, args.manifest_sha256)
        return archive_batch(
            Inventory.load(store), drive, media_http, post_ids=args.post_ids, limit=args.max_posts
        )
