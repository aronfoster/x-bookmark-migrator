"""Offline accounting. All operations run under the existing state-directory lock."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal, DecimalException, Inexact, InvalidOperation, localcontext

from .core import PilotError

LEDGER = "budget.private.json"
OBSERVATION = "worker-billing.private.json"
SETTLEMENT = "billing-settlement.private.json"
HIGH_WATER = "billing-high-water.private.json"
CAP = Decimal(20)


def money(value):
    try:
        if isinstance(value, bool):
            raise ValueError
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0:
            raise ValueError
        return amount
    except (InvalidOperation, ValueError, TypeError):
        raise PilotError("Invalid private budget amount.", code="budget_invalid") from None


def monthly(value):
    if not isinstance(value, dict) or any(
        not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", key) for key in value
    ):
        raise PilotError("Invalid monthly reservation ledger.")
    try:
        return {key: money(amount) for key, amount in value.items()}
    except PilotError:
        raise PilotError("Invalid monthly reservation ledger.") from None


def ledger(store):
    return monthly(raw_ledger(store))


def raw_ledger(store):
    value = store.read(LEDGER)
    return {} if value is None and not store.path(LEDGER).exists() else value


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed > datetime.now(UTC):
            raise ValueError
        return parsed
    except (TypeError, ValueError):
        raise PilotError("A completed, timezone-qualified billing cutoff is required.") from None


def cutoff(store):
    """Bind owner review to this store, complete ledger and unmodified observation."""
    months = {k: str(v) for k, v in ledger(store).items()}
    bill = store.read(OBSERVATION)
    if not months or not isinstance(bill, dict):
        raise PilotError("A historical ledger and reviewed billing observation are required.")
    timestamp(bill.get("observed_at"))
    actual = money(bill.get("actual_usd"))
    modified_ns = store.path(LEDGER).stat().st_mtime_ns
    return {
        "cutoff_sha256": fingerprint([str(store.root), months, modified_ns, bill]),
        "reserved_by_month_usd": months,
        "ledger_modified_ns": modified_ns,
        "observed_usd": str(actual),
        "observed_at": bill["observed_at"],
    }


def settle(store, *, expected, cumulative, through, evidence, attested):
    if store.path(SETTLEMENT).exists() or store.path(HIGH_WATER).exists():
        raise PilotError("Settlement already recorded or incomplete; no replacement permitted.")
    snapshot = cutoff(store)
    settled = timestamp(through).astimezone(UTC)
    # Round the local write time up, never down, to the provider timestamp precision.
    last_write = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
        microseconds=(snapshot["ledger_modified_ns"] + 999) // 1000
    )
    if (
        not attested
        or expected != snapshot["cutoff_sha256"]
        or not re.fullmatch("[0-9a-f]{64}", evidence)
        or settled < timestamp(snapshot["observed_at"])
        or max(snapshot["reserved_by_month_usd"]) > settled.strftime("%Y-%m")
        or settled < last_write
        or money(cumulative) < money(snapshot["observed_usd"])
    ):
        raise PilotError("Stale cutoff, incomplete attestation, or regressed billing evidence.")
    receipt = {
        **snapshot,
        "provider_cumulative_usd": str(money(cumulative)),
        "settled_through": through,
        "evidence_sha256": evidence,
        "owner_attested_full_coverage": True,
    }
    store.create(SETTLEMENT, receipt)
    store.create(
        HIGH_WATER,
        {
            "settlement_sha256": fingerprint(receipt),
            "months": snapshot["reserved_by_month_usd"],
            "actual_usd": receipt["provider_cumulative_usd"],
        },
    )
    return {"settled": True, "api_requests": 0}


def verify_months(current, floor):
    if any(key not in current or current[key] < amount for key, amount in floor.items()):
        raise PilotError("Monthly reservation ledger regressed; no request permitted.")


def settlement_state(store, months, bill):
    receipt, marker = store.read(SETTLEMENT), store.read(HIGH_WATER)
    if not store.path(SETTLEMENT).exists() and not store.path(HIGH_WATER).exists():
        return {}, None, Decimal(0)
    if (
        not isinstance(receipt, dict)
        or not isinstance(marker, dict)
        or marker.get("settlement_sha256") != fingerprint(receipt)
        or bill is None
    ):
        raise PilotError("Incomplete settlement or high-water record; no request permitted.")
    covered = monthly(receipt["reserved_by_month_usd"])
    previous = monthly(marker.get("months"))
    verify_months(previous, covered)
    verify_months(months, previous)
    return (
        covered,
        marker,
        max(money(receipt["provider_cumulative_usd"]), money(marker["actual_usd"])),
    )


def reserve(store, amount, *, month_cap=None, cap=CAP, actual=Decimal(0)):
    """Charge the shared ledger before dispatch. Neither settlement nor failure releases it."""
    raw = raw_ledger(store)
    months = monthly(raw)
    bill = store.read(OBSERVATION)
    if store.path(OBSERVATION).exists():
        if not isinstance(bill, dict) or not bill.get("observed_at"):
            raise PilotError("Invalid private billing observation.")
        actual = max(actual, money(bill.get("actual_usd")))
    covered, marker, settled_actual = settlement_state(store, months, bill)
    if marker is not None:
        prior_actual = money(marker.get("actual_usd"))
        actual = max(actual, settled_actual)
        if actual > prior_actual:
            # A denied reservation must not forget a higher observed lifetime charge.
            marker = {**marker, "actual_usd": str(actual)}
            store.write(HIGH_WATER, marker)
    month = datetime.now(UTC).strftime("%Y-%m")
    try:
        with localcontext() as ctx:
            ctx.traps[Inexact] = True
            uncovered = sum(v - covered.get(k, 0) for k, v in months.items())
            new_month = months.get(month, 0) + amount
            if actual + uncovered + amount > min(cap, CAP) or (
                month_cap is not None and new_month > month_cap
            ):
                raise PilotError("Estimated spend cap reached; no request sent.", code="spend_cap")
    except DecimalException:
        raise PilotError("Unsupported private budget precision.") from None
    months[month] = new_month
    updated = {k: str(v) for k, v in months.items()}
    if marker is not None:
        # Write ahead: interruption before the ledger write fails closed on next use.
        store.write(HIGH_WATER, {**marker, "months": updated, "actual_usd": str(actual)})
    store.write(LEDGER, {**raw, month: str(new_month)})
