import argparse
import getpass
import json
import os
import sys
from decimal import InvalidOperation
from pathlib import Path

import httpx

from .core import Budget, Client, PilotError, Store, auth_start, exact_id, pilot
from .inventory import Inventory, validate_run_limits


def main():
    parser = argparse.ArgumentParser(description="Official X read-only bookmark pilot")
    parser.add_argument(
        "--state-dir", type=Path, default=Path.home() / ".local/state/x-bookmark-migrator"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("auth-start")
    sub.add_parser("auth-finish")
    for command in ("pilot", "inventory"):
        read = sub.add_parser(
            command,
            epilog=(
                "Inventory exits 1 for partial coverage (including unverified folder pagination); "
                "the private checkpoint and export are still useful."
            )
            if command == "inventory"
            else None,
        )
        read.add_argument(
            "--app-owner-id", help="Overrides X_APP_OWNER_ID from the process environment"
        )
        read.add_argument("--console-hard-cap-confirmed", action="store_true")
        read.add_argument("--auto-recharge-off-confirmed", action="store_true")
        reserve_default = os.environ.get("X_PER_CALL_RESERVE_USD")
        read.add_argument(
            "--per-call-reserve-usd",
            default=reserve_default,
            required=reserve_default is None,
            help="Overrides X_PER_CALL_RESERVE_USD; an operator estimate",
        )
        read.add_argument("--run-cap-usd", default=os.environ.get("X_RUN_CAP_USD", "0.25"))
        read.add_argument("--month-cap-usd", default=os.environ.get("X_MONTH_CAP_USD", "1.00"))
        read.add_argument("--field-dialect", choices=("post", "tweet"), default="post")
        if command == "inventory":
            read.add_argument("--resume", action="store_true")
            read.add_argument("--retry-unavailable", action="store_true")
            read.add_argument("--source-unchanged-confirmed", action="store_true")
            read.add_argument("--max-calls", type=int, default=10)
            read.add_argument(
                "--page-size",
                type=int,
                help="Fresh pass default: 100; resume uses stored value and rejects changes",
            )
            read.add_argument("--hydrate-batch-size", type=int, default=100)
            read.add_argument(
                "--max-records",
                type=int,
                help="Fresh pass default: 10000; resume uses stored value and rejects changes",
            )
            read.add_argument(
                "--max-folders",
                type=int,
                help="Fresh pass default: 1000; resume uses stored value and rejects changes",
            )
            read.add_argument(
                "--max-pages-per-stream",
                type=int,
                help="Fresh pass default: 100; resume uses stored value and rejects changes",
            )
    sub.add_parser("inventory-export", help="Export the latest checkpoint offline")
    recovery = sub.add_parser("inventory-recover", help="Recover a rejected folder read offline")
    recovery.add_argument("--read-file", required=True)
    args = parser.parse_args()
    try:
        if args.command in ("inventory-export", "inventory-recover"):
            store = Store(args.state_dir)
            with store.locked():
                inventory = Inventory.load(store)
                result = (
                    inventory.recover(args.read_file)
                    if args.command == "inventory-recover"
                    else {**inventory.summary(), "private_export": inventory.export()}
                )
                print(json.dumps(result))
            return 0
        client_id = os.environ.get("X_CLIENT_ID")
        if not client_id:
            raise PilotError("Set X_CLIENT_ID to the app's OAuth 2.0 client ID.")
        if args.command in ("pilot", "inventory") and not (
            args.console_hard_cap_confirmed and args.auto_recharge_off_confirmed
        ):
            raise PilotError("Confirm Console hard spending limit and auto-recharge off first.")
        if args.command in ("pilot", "inventory"):
            owner_id = args.app_owner_id
            source = "--app-owner-id"
            if owner_id is None:
                owner_id = os.environ.get("X_APP_OWNER_ID")
                source = "X_APP_OWNER_ID"
            if owner_id is None:
                raise PilotError(
                    "Set X_APP_OWNER_ID in .env (uv run --env-file .env), "
                    "or supply --app-owner-id with your numeric X user ID."
                )
            try:
                exact_id(owner_id)
            except PilotError:
                raise PilotError(
                    f"{source} must contain your numeric X user ID (1–19 digits)."
                ) from None
            if args.command == "inventory":
                validate_run_limits(args.max_calls, args.hydrate_batch_size)
        store = Store(args.state_dir)
        with (
            store.locked(),
            httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as http,
        ):
            client = Client(store, http, client_id, os.environ.get("X_CLIENT_SECRET"))
            if args.command == "auth-start":
                print(auth_start(store, client_id))
            elif args.command == "auth-finish":
                if not sys.stdin.isatty():
                    raise PilotError("Run auth-finish in a local interactive terminal.")
                client.finish(getpass.getpass("Paste full callback URL (hidden): "))
                print("Read-only OAuth grant stored privately.")
            else:
                budget = Budget(
                    store, args.per_call_reserve_usd, args.run_cap_usd, args.month_cap_usd
                )
                if args.command == "pilot":
                    result = pilot(client, budget, owner_id, args.field_dialect)
                else:
                    inventory = Inventory(
                        store,
                        owner_id,
                        args.field_dialect,
                        resume=args.resume,
                        source_unchanged=args.source_unchanged_confirmed,
                        retry_unavailable=args.retry_unavailable,
                        page_size=args.page_size,
                        max_records=args.max_records,
                        max_folders=args.max_folders,
                        max_pages=args.max_pages_per_stream,
                    )
                    result = inventory.run(
                        client,
                        budget,
                        max_calls=args.max_calls,
                        hydrate_batch_size=args.hydrate_batch_size,
                    )
                print(json.dumps(result))
                if result.get("inventory_status") == "partial":
                    return 1
    except PilotError as exc:
        if exc.summary is not None:
            print(json.dumps(exc.summary))
        print(str(exc), file=sys.stderr)
        return 1
    except (
        httpx.HTTPError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        InvalidOperation,
    ):
        # Never expose remote error bodies, callback codes, tokens, or local data.
        print(
            "Local state/configuration or transport failed; no automatic retry. "
            "Reauthorize if a token exchange was interrupted.",
            file=sys.stderr,
        )
        return 1
    return 0
