import argparse
import getpass
import json
import os
import sys
from decimal import InvalidOperation
from pathlib import Path

import httpx

from .core import Budget, Client, PilotError, Store, auth_start, pilot


def main():
    parser = argparse.ArgumentParser(description="Official X read-only bookmark pilot")
    parser.add_argument(
        "--state-dir", type=Path, default=Path.home() / ".local/state/x-bookmark-migrator"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("auth-start")
    sub.add_parser("auth-finish")
    read = sub.add_parser("pilot")
    read.add_argument("--app-owner-id", required=True)
    read.add_argument("--console-hard-cap-confirmed", action="store_true")
    read.add_argument("--auto-recharge-off-confirmed", action="store_true")
    read.add_argument("--per-call-reserve-usd", required=True)
    read.add_argument("--run-cap-usd", default="0.25")
    read.add_argument("--month-cap-usd", default="1.00")
    read.add_argument("--field-dialect", choices=("post", "tweet"), default="post")
    args = parser.parse_args()
    try:
        client_id = os.environ.get("X_CLIENT_ID")
        if not client_id:
            raise PilotError("Set X_CLIENT_ID to the app's OAuth 2.0 client ID.")
        if args.command == "pilot" and not (
            args.console_hard_cap_confirmed and args.auto_recharge_off_confirmed
        ):
            raise PilotError("Confirm Console hard spending limit and auto-recharge off first.")
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
                print(json.dumps(pilot(client, budget, args.app_owner_id, args.field_dialect)))
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
