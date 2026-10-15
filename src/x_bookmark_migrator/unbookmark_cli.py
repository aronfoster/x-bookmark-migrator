"""Explicit plan/apply/reconcile entrypoints; private values never printed."""

import os

import httpx

from .core import Budget, Client, PilotError, Store, exact_id
from .drive import Drive
from .unbookmark import apply_plan, make_plan, reconcile_plan

COMMANDS = ("unbookmark-plan", "unbookmark-apply", "unbookmark-reconcile")


def add_commands(sub):
    for command in COMMANDS:
        parser = sub.add_parser(command)
        parser.add_argument("--app-owner-id", default=os.environ.get("X_APP_OWNER_ID"))
        if command != "unbookmark-reconcile":
            parser.add_argument(
                "--drive-folder-id", default=os.environ.get("X_ARCHIVE_DRIVE_FOLDER_ID")
            )
            parser.add_argument("--max-asset-bytes", type=int, default=64 * 1024 * 1024)
        if command == "unbookmark-plan":
            parser.add_argument("--archive-receipt", required=True)
            parser.add_argument("--post-id", action="append", dest="post_ids", required=True)
            parser.add_argument(
                "--archive-acceptance-confirmed",
                action="store_true",
                help="Attest runtime probe, fresh-state restore, content/media review "
                "and independent anchor backup passed",
            )
        else:
            parser.add_argument("--plan-file", required=True)
            parser.add_argument("--max-calls", type=int, default=20)
            parser.add_argument("--console-hard-cap-confirmed", action="store_true")
            parser.add_argument("--auto-recharge-off-confirmed", action="store_true")
            parser.add_argument(
                "--per-call-reserve-usd", default=os.environ.get("X_PER_CALL_RESERVE_USD")
            )
            parser.add_argument("--run-cap-usd", default=os.environ.get("X_RUN_CAP_USD", "0.25"))
            parser.add_argument(
                "--month-cap-usd", default=os.environ.get("X_MONTH_CAP_USD", "1.00")
            )
            if command == "unbookmark-apply":
                parser.add_argument("--confirm-unbookmark", action="store_true")
                parser.add_argument(
                    "--retry-rejected",
                    action="store_true",
                    help="Explicit retry only for recorded HTTP 400/401/403/429 rejection",
                )


def run(args):
    try:
        owner = exact_id(args.app_owner_id)
    except PilotError:
        raise PilotError(
            "Set X_APP_OWNER_ID in .env or --app-owner-id to your numeric X user ID."
        ) from None
    if args.command == "unbookmark-plan" and not args.archive_acceptance_confirmed:
        raise PilotError("Complete the ARCHIVE.md runtime/restore acceptance first.")
    if args.command != "unbookmark-plan":
        if not (args.console_hard_cap_confirmed and args.auto_recharge_off_confirmed):
            raise PilotError("Confirm Console hard spending limit and auto-recharge off first.")
        if not os.environ.get("X_CLIENT_ID"):
            raise PilotError("Set X_CLIENT_ID.")
    if args.command == "unbookmark-apply" and not args.confirm_unbookmark:
        raise PilotError("Review the private plan and supply --confirm-unbookmark.")
    if args.command != "unbookmark-reconcile" and not args.drive_folder_id:
        raise PilotError("Set X_ARCHIVE_DRIVE_FOLDER_ID or --drive-folder-id.")
    store = Store(args.state_dir)
    with (
        store.locked(),
        httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as x_http,
        httpx.Client(timeout=120, follow_redirects=False, trust_env=False) as storage_http,
    ):
        if args.command == "unbookmark-plan":
            drive = Drive(store, storage_http, args.drive_folder_id, args.max_asset_bytes)
            return make_plan(
                store,
                drive,
                args.archive_receipt,
                args.post_ids,
                owner,
                acceptance_confirmed=args.archive_acceptance_confirmed,
            )
        client = Client(store, x_http, os.environ["X_CLIENT_ID"], os.environ.get("X_CLIENT_SECRET"))
        budget = Budget(store, args.per_call_reserve_usd, args.run_cap_usd, args.month_cap_usd)
        if args.command == "unbookmark-reconcile":
            return reconcile_plan(
                store, client, budget, args.plan_file, owner, max_calls=args.max_calls
            )
        drive = Drive(store, storage_http, args.drive_folder_id, args.max_asset_bytes)
        return apply_plan(
            store,
            drive,
            client,
            budget,
            args.plan_file,
            owner,
            confirmed=args.confirm_unbookmark,
            retry_rejected=args.retry_rejected,
            max_calls=args.max_calls,
        )
