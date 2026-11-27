"""Archive commands are separate from the paid X-read CLI options."""

import os
import secrets

import httpx

from .archive import archive_batch, import_pilot, restore_batch
from .archive_repair import preview_repairs, select_repairs, write_repairs
from .catalog import build, check_archive_directories, read_catalog
from .core import PilotError, Store
from .drive import MAX_BYTES, Drive, encode
from .inventory import Inventory
from .publication import PublicationStore
from .retained_authors import select_author_repairs

COMMANDS = (
    "archive",
    "archive-probe",
    "archive-import-pilot",
    "archive-restore",
    "archive-repair",
    "archive-repair-lookup",
)


def add_commands(sub):
    for name in COMMANDS:
        parser = sub.add_parser(name)
        parser.add_argument(
            "--drive-folder-id", default=os.environ.get("X_ARCHIVE_DRIVE_FOLDER_ID")
        )
        parser.add_argument("--max-asset-bytes", type=int, default=64 * 1024 * 1024)
        if name == "archive-repair-lookup":
            parser.add_argument(
                "--batch", nargs=2, metavar=("RESTORE_DIR", "RECEIPT"), required=True
            )
            parser.add_argument(
                "--lookup", required=True, help="Protected official lookup envelope"
            )
            parser.add_argument("--post-id", required=True)
            parser.add_argument("--catalog-dir", required=True)
            parser.add_argument("--apply-plan", metavar="SHA256")
        elif name == "archive-repair":
            parser.add_argument(
                "--batch",
                nargs=2,
                metavar=("RESTORE_DIR", "RECEIPT"),
                action="append",
                required=True,
            )
            parser.add_argument(
                "--reason", choices=("byte_limit", "media_count", "retained_author"), required=True
            )
            parser.add_argument(
                "--output-dir",
                type=str,
                help="Separate local restore directory for retained-author apply",
            )
            parser.add_argument(
                "--catalog-dir",
                help="Merge the independently restored author successor into this existing catalog",
            )
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

    if args.command == "archive-repair-lookup":
        from . import targeted_lookup

        return targeted_lookup.run(args)
    if args.command == "archive-repair":
        if not 1024 <= args.max_asset_bytes <= MAX_BYTES:
            raise PilotError("Unsupported repair byte limit.")
        author = args.reason == "retained_author"
        selected = (
            select_author_repairs(args.batch) if author else select_repairs(args.batch, args.reason)
        )
        if args.item is not None:
            if not 1 <= args.item <= len(selected):
                raise PilotError("Repair item position outside the selected sources.")
            selected = [selected[args.item - 1]]
        preview = preview_repairs(selected, args.max_asset_bytes)
        if args.apply_plan is None:
            return preview
        if args.apply_plan != preview["plan_sha256"]:
            raise PilotError("Repair plan differs from reviewed preview.")
        output = catalog = None
        if author:
            if not args.output_dir:
                raise PilotError("Author repairs require a separate local restore directory.")
            path = Path(args.output_dir).expanduser().absolute()
            inputs = [
                Path(args.state_dir).expanduser().resolve(),
                *(Path(d).resolve() for d, _ in args.batch),
            ]
            if path != path.resolve() or any(
                path.is_relative_to(other) or other.is_relative_to(path) for other in inputs
            ):
                raise PilotError("Restore must be disjoint from source batches and state.")
            # Check from the filesystem root, including ancestors of the archive tree.
            # A root-owned sticky temporary directory protects existing owned children
            # against rename; never create the restore directly inside such a directory.
            for directory in reversed(path.parents):
                st = directory.lstat()
                sticky_parent = st.st_uid == 0 and st.st_mode & 0o1000 and directory != path.parent
                if st.st_uid not in (0, os.getuid()) or (st.st_mode & 0o022 and not sticky_parent):
                    raise PilotError("Unsafe local archive directory.")
            archive_tree = path.parent
            if getattr(args, "catalog_dir", None):
                catalog_path = Path(args.catalog_dir).expanduser().absolute()
                if (
                    not (catalog_path / "catalog.jsonl").is_file()
                    or catalog_path != catalog_path.resolve()
                    or not path.is_relative_to(catalog_path.parent)
                    or any(
                        catalog_path.is_relative_to(other) or other.is_relative_to(catalog_path)
                        for other in [*inputs, path]
                    )
                ):
                    raise PilotError("Expected an existing separate catalog beside the restore.")
                catalog = PublicationStore(catalog_path)
                archive_tree = catalog.root.parent
                if catalog.reader_gid is not None and path.parent != archive_tree:
                    raise PilotError("Group restore must be a direct sibling of the catalog.")
            check_archive_directories(archive_tree, path if path.exists() else path.parent)
            output = PublicationStore(path)
            if catalog is not None and catalog.reader_gid != output.reader_gid:
                raise PilotError("Restore and catalog reader policies differ.")
        elif getattr(args, "output_dir", None) or getattr(args, "catalog_dir", None):
            raise PilotError(
                "Restore and catalog destinations apply only to retained-author repair."
            )
    if not args.drive_folder_id:
        raise PilotError("Set X_ARCHIVE_DRIVE_FOLDER_ID or --drive-folder-id.")
    store = Store(args.state_dir)
    with (
        store.locked(),
        httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as storage_http,
        httpx.Client(timeout=60, follow_redirects=False, trust_env=False) as media_http,
    ):
        drive = Drive(store, storage_http, args.drive_folder_id, args.max_asset_bytes)
        if args.command == "archive-repair" and author:
            pin = store.read("drive-destination.private.json")
            if (
                not isinstance(pin, dict)
                or pin.get("root") != drive.root
                or not pin.get("owner_permission_id")
            ):
                raise PilotError("Author repair requires the existing pinned Drive destination.")
        context = drive.preflight()
        if args.command == "archive-repair":
            if catalog is not None:
                from .worker import catalog_index, merge_publication, require_catalog_versions

                chains = [
                    p[key]
                    for _, p in selected
                    for key in ("repair_source", "retained_author_source")
                ]
                with catalog.locked():
                    require_catalog_versions(catalog_index(read_catalog(catalog)), chains)
            result = write_repairs(store, drive, media_http, selected, author_restore=output)
            if catalog is not None:
                # The merge applies curation to the full corpus under the catalog lock.
                batches = [(output.root, store.path(result["private_receipt"]))]
                incoming = build(batches, catalog, apply_curation=False)
                if any(
                    a.get("availability") != "local_hash_verified"
                    for r in incoming
                    for v in r["versions"]
                    for a in v["media"]
                ):
                    raise PilotError("Author successor lacks verified restored media.")
                merge_publication(catalog, incoming, batches=batches, required_versions=chains)
                result["catalog_merged"] = True
            return result
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
