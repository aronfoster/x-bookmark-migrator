"""Explicit offline and owner-authorized live entry point for the bounded worker."""

import json
import os
import re
from pathlib import Path

import httpx

from .backlog import prepare_one, require_complete_history, select_backlog, staged_one
from .billing import SETTLEMENT, cutoff, settle
from .catalog import private_bytes
from .core import PilotError, Store, exact_id
from .drive import MAX_BYTES, Drive, digest, encode
from .publication import PublicationStore
from .raindrop import RaindropAdapter, require_distinct_state_dirs
from .worker import (
    CumulativeBudget,
    Deferral,
    GuardedHTTP,
    Worker,
    WorkerClient,
    money,
    removal_gates,
    snapshot,
)

COMMANDS = ("worker", "billing-cutoff", "billing-settle")


def add_commands(sub):
    sub.add_parser("billing-cutoff", help="Inspect the complete reservation cutoff offline")
    billing = sub.add_parser("billing-settle", help="Record one owner-attested cutoff offline")
    billing.add_argument("--expected-cutoff", required=True)
    billing.add_argument("--provider-cumulative-usd", required=True)
    billing.add_argument("--settled-through", required=True)
    billing.add_argument("--evidence-sha256", required=True)
    billing.add_argument(
        "--attest-full-coverage",
        required=True,
        action="store_true",
        help="All lifetime charges settled; no intervening reservations or usage",
    )
    p = sub.add_parser("worker", help="Offline audit or explicitly authorized bounded migration")
    p.add_argument("--config", type=Path, required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Offline counts; no API requests")
    mode.add_argument("--report-only", action="store_true", help="Read existing status offline")
    mode.add_argument("--once", action="store_true")
    mode.add_argument(
        "--inspect-backlog",
        nargs=2,
        metavar=("FOLDER_ID", "VERIFIED_CATALOG_SHA256"),
        help="Count linked candidates on the latest saved first window; no plan or API calls",
    )
    mode.add_argument(
        "--prepare-backlog-one",
        nargs=4,
        metavar=("FOLDER_ID", "VERIFIED_CATALOG_SHA256", "SOURCE_SHA256", "SELECTION_SHA256"),
        help="Verify one inspected binding via Drive and stage its private plan; no X calls",
    )
    p.add_argument(
        "--attest-archive-acceptance",
        action="store_true",
        help="Owner confirms runtime restore acceptance, content/media review, and independent "
        "restore-anchor backup for this exact selection",
    )
    p.add_argument(
        "--live", action="store_true", help="Explicitly permit paid reads and Drive writes"
    )
    p.add_argument(
        "--allow-delete", action="store_true", help="Also permit Raindrop writes and X DELETE"
    )
    p.add_argument("--scheduled", action="store_true", help="Observe persisted daily due time")
    p.add_argument(
        "--apply-verified-plan",
        action="store_true",
        help="Apply only the saved one-item Drive-verified plan; no queue selection",
    )
    p.add_argument(
        "--staged-backlog",
        nargs=3,
        metavar=("SELECTION_SHA256", "PLAN_SHA256", "RESTORED_BATCH_DIR"),
        help="Use the exact inspected and prepared backlog item with --apply-verified-plan",
    )
    p.add_argument(
        "--expected-pointer-sha256",
        help="Manual --apply-verified-plan only: 64 lowercase hex digits for SHA-256 of the "
        "entire pointer encoded as UTF-8 JSON (sorted keys, non-ASCII unescaped, separators "
        "comma-space and colon-space) followed by one LF",
    )


def protected_config(path):
    path = Path(path)
    st = path.lstat()
    if (
        path.is_symlink()
        or not path.is_file()
        or st.st_uid != 0
        or st.st_mode & 0o137
        or st.st_gid not in {os.getgid(), *os.getgroups()}
    ):
        raise PilotError("Live worker configuration must be root-owned and runtime-group readable.")
    for parent in path.parents:
        ps = parent.lstat()
        if parent.is_symlink() or ps.st_uid != 0 or ps.st_mode & 0o022:
            raise PilotError("Live configuration ancestors must be root-protected.")
    if any(x.startswith("system.posix_acl") for x in os.listxattr(path)):
        raise PilotError("Live configuration ACLs are not permitted.")
    return path.read_bytes()


def require_protected_helper(script):
    # Protect the complete helper directory, including possible Python imports.
    for path in (script.parent, *script.parents, *script.parent.rglob("*")):
        st = path.lstat()
        if (
            path.is_symlink()
            or st.st_uid != 0
            or st.st_mode & 0o022
            or any(x.startswith("system.posix_acl") for x in os.listxattr(path))
        ):
            raise PilotError("Worker Raindrop code must be root-protected outside agent storage.")


def load_config(path):
    try:
        raw = private_bytes(path)
    except PilotError:
        raw = protected_config(path)
    config = json.loads(raw)
    if config.get("schema_version") != 1:
        raise PilotError("Unsupported worker configuration.")
    exact_id(config["owner_id"])
    max_asset_bytes = config.setdefault("max_asset_bytes", 64 * 1024 * 1024)
    if type(max_asset_bytes) is not int or not 1024 <= max_asset_bytes <= MAX_BYTES:
        raise PilotError("Invalid worker max_asset_bytes.")
    for key, maximum in (("max_items", 50), ("max_calls", 100), ("max_batches", 100)):
        if type(config.get(key)) is not int or not 1 <= config[key] <= maximum:
            raise PilotError("Invalid worker bounds.")
    roots = [Path(config[k]) for k in ("catalog_dir", "restore_dir", "raindrop_ledger_dir")]
    if any(not p.is_absolute() for p in roots):
        raise PilotError("Worker directories must be absolute.")
    if any(
        a.resolve() == b.resolve()
        or a.resolve() in b.resolve().parents
        or b.resolve() in a.resolve().parents
        for i, a in enumerate(roots)
        for b in roots[i + 1 :]
    ):
        raise PilotError("Worker directories must be disjoint.")
    return config


def run(args):
    if getattr(args, "command", None) in ("billing-cutoff", "billing-settle"):
        if not args.state_dir.is_dir():
            raise PilotError("Audited private state directory must already exist.")
        store = Store(args.state_dir)
        with store.locked():
            if args.command == "billing-cutoff":
                return {**cutoff(store), "api_requests": 0}
            return settle(
                store,
                expected=args.expected_cutoff,
                cumulative=args.provider_cumulative_usd,
                through=args.settled_through,
                evidence=args.evidence_sha256,
                attested=args.attest_full_coverage,
            )
    backlog = getattr(args, "inspect_backlog", None)
    prepare = getattr(args, "prepare_backlog_one", None)
    attested = getattr(args, "attest_archive_acceptance", False)
    if prepare is not None and (
        not attested or args.live or args.allow_delete or args.scheduled or args.apply_verified_plan
    ):
        raise PilotError("Preparation requires manual archive attestation without live flags.")
    if attested and prepare is None:
        raise PilotError("Archive attestation applies only to manual backlog preparation.")
    offline = args.dry_run or args.report_only or backlog is not None
    pointer_sha = getattr(args, "expected_pointer_sha256", None)
    staged = getattr(args, "staged_backlog", None)
    if staged and (not args.apply_verified_plan or pointer_sha is not None):
        raise PilotError("Staged removal requires --apply-verified-plan without a pointer.")
    if pointer_sha is not None and (
        not args.apply_verified_plan or not re.fullmatch("[0-9a-f]{64}", pointer_sha)
    ):
        raise PilotError(
            "--expected-pointer-sha256 requires --apply-verified-plan and 64 lowercase hex digits."
        )
    if args.apply_verified_plan and not (
        args.once and args.live and args.allow_delete and not args.scheduled
    ):
        raise PilotError("Verified-plan removal requires manual --once --live --allow-delete.")
    if (
        offline
        and (args.live or args.allow_delete or args.scheduled)
        or args.once
        and not args.live
    ):
        raise PilotError("Choose offline inspection or --once --live explicitly.")
    config = load_config(args.config)
    if backlog is not None or prepare is not None:
        require_complete_history(config)  # Before even creating lock files.
    state_path = args.state_dir.expanduser().resolve()
    # Never silently initialize a new state directory in place of the audited one.
    if not state_path.is_dir():
        raise PilotError("Audited private state directory must already exist.")
    for key in ("restore_dir", "catalog_dir", "raindrop_ledger_dir"):
        path = Path(config[key]).resolve()
        if path == state_path or path in state_path.parents or state_path in path.parents:
            raise PilotError("Worker state and output directories must be disjoint.")
    store = Store(args.state_dir)
    with store.locked():
        if backlog is not None or prepare is not None:
            path = Path(config["catalog_dir"])
            if not path.is_dir():
                raise PilotError("Existing catalog directory required.")
            catalog = PublicationStore(path)
            with catalog.locked():
                if prepare is not None:
                    return prepare_one(store, catalog, config, *prepare, attested)
                candidates = select_backlog(store, catalog, config, *backlog)
            return {
                "mode": "inspect_backlog",
                "candidates": len(candidates),
                "api_requests": 0,
                "source_sha256": candidates[0]["source_read_sha256"] if candidates else None,
                "selection_sha256": [digest(encode(c)) for c in candidates],
            }
        if offline:
            catalog_path = Path(config["catalog_dir"])
            catalog = PublicationStore(catalog_path) if catalog_path.is_dir() else None
            result = snapshot(store, config.get("helper_descriptors", []), catalog)
            # Billing evidence is whitelisted: no arbitrary private notes enter stdout.
            bill = result.pop("actual_billing")
            result["actual_observed_usd"] = str(money(bill["actual_usd"])) if bill else "unverified"
            return {
                **result,
                "mode": "dry_run" if args.dry_run else "report_only",
                "api_requests": 0,
            }
        required = (
            removal_gates(config, exact_plan=args.apply_verified_plan)
            if args.allow_delete
            else (
                "live_authorized",
                "console_cap_confirmed",
                "auto_recharge_off",
                "removal_sources_complete",
            )
        )
        if any(config.get("gates", {}).get(g) is not True for g in required):
            raise PilotError("Local rollout/owner authorization gates are not satisfied.")
        protected_config(args.config)
        if os.getuid() == 0:
            raise PilotError("Worker must run as an ordinary non-root account.")
        if not os.environ.get("X_CLIENT_ID"):
            raise PilotError("Set X_CLIENT_ID using the existing protected environment.")
        gate = Deferral(store)
        if gate.remaining():
            return {"quiet": True, "deferred": True}
        bill = store.read("worker-billing.private.json")
        if not isinstance(bill, dict) or not bill.get("observed_at"):
            raise PilotError("Record the reviewed Console billing observation before live work.")
        if staged and not store.path(SETTLEMENT).is_file():
            raise PilotError("Reconcile the historical billing cutoff before staged removal.")
        budget = CumulativeBudget(
            store,
            config["reservation_usd"],
            config["run_cap_usd"],
            config["max_calls"],
            actual=bill["actual_usd"],
        )
        require_distinct_state_dirs(
            state_dir=store.root,
            catalog_dir=config["catalog_dir"],
            raindrop_ledger_dir=config["raindrop_ledger_dir"],
        )
        catalog = PublicationStore(Path(config["catalog_dir"]))
        restore = PublicationStore(Path(config["restore_dir"]))
        for output in (catalog, restore):
            if output.reader_gid != config.get("publication_reader_gid"):
                raise PilotError("Publication reader group differs from reviewed configuration.")
        if args.allow_delete and any(
            not re.fullmatch(r"[0-9a-f]{64}", config.get(key) or "")
            for key in ("raindrop_script_sha256", "raindrop_helper_sha256")
        ):
            raise PilotError("Reviewed Raindrop script and list_all.py hashes are required.")
        if args.allow_delete:
            require_protected_helper(Path(config["raindrop_script"]))
            if not os.environ.get("RAINDROP_TOKEN"):
                raise PilotError("Explicit Raindrop environment token is required.")
        adapter = (
            RaindropAdapter(
                config["raindrop_ledger_dir"],
                config["raindrop_script"],
                script_sha256=config["raindrop_script_sha256"],
                helper_sha256=config["raindrop_helper_sha256"],
            )
            if args.allow_delete
            else None
        )
        with (
            httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as x_http,
            httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as storage_http,
            httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as media_http,
        ):
            client = WorkerClient(
                store,
                GuardedHTTP(x_http, gate, "x"),
                os.environ["X_CLIENT_ID"],
                os.environ.get("X_CLIENT_SECRET"),
            )
            drive = Drive(
                store,
                GuardedHTTP(storage_http, gate, "drive"),
                config["drive_folder_id"],
                config["max_asset_bytes"],
            )
            worker = Worker(
                store,
                catalog,
                restore.root,
                client,
                drive,
                GuardedHTTP(media_http, gate, "media"),
                adapter,
                budget,
                config,
            )
            if args.apply_verified_plan:
                if staged:
                    with catalog.locked():
                        name, plan, binding = staged_one(store, catalog, config, *staged[:2])
                    result = worker.apply_one(name, plan, binding=binding, restore_dir=staged[2])
                else:
                    result = worker.apply_verified_plan(
                        expected_pointer=getattr(args, "expected_pointer", None),
                        expected_pointer_sha256=pointer_sha,
                        pointer_name=getattr(
                            args, "pointer_name", "verified-one-plan.private.json"
                        ),
                    )
                result.pop("private_report", None)
            else:
                if not args.scheduled:
                    worker.state["next_daily"] = 0
                result = worker.run(allow_delete=args.allow_delete)
            result.pop("actual_billing", None)
            result["actual_observed_usd"] = str(money(bill["actual_usd"]))
            return result
