"""Explicit offline and owner-authorized live entry point for the bounded worker."""

import json
import os
from pathlib import Path

import httpx

from .catalog import private_bytes
from .core import PilotError, Store, exact_id
from .drive import Drive
from .raindrop import RaindropAdapter, require_distinct_state_dirs
from .worker import (
    GATES,
    CumulativeBudget,
    Deferral,
    GuardedHTTP,
    Worker,
    WorkerClient,
    money,
    snapshot,
)

COMMANDS = ("worker",)


def add_commands(sub):
    p = sub.add_parser("worker", help="Offline audit or explicitly authorized bounded migration")
    p.add_argument("--config", type=Path, required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Offline counts; no API requests")
    mode.add_argument("--report-only", action="store_true", help="Read existing status offline")
    mode.add_argument("--once", action="store_true")
    p.add_argument(
        "--live", action="store_true", help="Explicitly permit paid reads and Drive writes"
    )
    p.add_argument(
        "--allow-delete", action="store_true", help="Also permit Raindrop writes and X DELETE"
    )
    p.add_argument("--scheduled", action="store_true", help="Observe persisted daily due time")


def load_config(path):
    config = json.loads(private_bytes(path))
    if config.get("schema_version") != 1:
        raise PilotError("Unsupported worker configuration.")
    exact_id(config["owner_id"])
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
    offline = args.dry_run or args.report_only
    if (
        offline
        and (args.live or args.allow_delete or args.scheduled)
        or args.once
        and not args.live
    ):
        raise PilotError("Choose offline inspection or --once --live explicitly.")
    config = load_config(args.config)
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
        if offline:
            catalog_path = Path(config["catalog_dir"])
            catalog = Store(catalog_path) if catalog_path.is_dir() else None
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
            GATES
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
        catalog = Store(Path(config["catalog_dir"]))
        restore = Store(Path(config["restore_dir"]))
        adapter = (
            RaindropAdapter(config["raindrop_ledger_dir"], config["raindrop_script"])
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
                store, GuardedHTTP(storage_http, gate, "drive"), config["drive_folder_id"]
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
            if not args.scheduled:
                worker.state["next_daily"] = 0
            result = worker.run(allow_delete=args.allow_delete)
            result.pop("actual_billing", None)
            result["actual_observed_usd"] = str(money(bill["actual_usd"]))
            return result
