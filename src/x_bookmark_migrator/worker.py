"""Bounded, model-free migration. Callers hold the existing manual-command lock.

Private provider receipts remain authoritative; this journal is a resumable queue,
never permission to delete. No independent implementation of the deletion gate.
"""

import copy
import json
import re
import time
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .archive import archive_batch, enrich_references, restore_batch
from .catalog import (
    apply_annotations,
    build,
    link_raindrop,
    primary_post_text,
    publish,
    read_annotations,
    read_catalog,
)
from .core import TOKEN_ROTATION, Client, PilotError
from .discovery_gate import positive_media_only
from .drive import digest
from .inventory import Inventory
from .publication import PublicationStore, publication_bytes
from .repair import scan_helper, scan_native
from .source import source_url
from .unbookmark import apply_plan, latest_attempt, make_plan

JOURNAL = "worker.private.json"
DEFER = "worker-defer.private.json"
ROTATION = TOKEN_ROTATION
WorkerClient = Client
OPERATIONS = (
    "identity",
    "main",
    "folders",
    "folder",
    "hydrate",
    "folder_names",
    "before_window",
    "delete",
    "after_window",
    "reconcile_window",
    "other",
)
GATES = (
    "archive_restore_accepted",
    "legacy_helpers_retired",
    "removal_sources_complete",
    "fos166_runtime_accepted",
    "fos216_backfill_accepted",
    "console_cap_confirmed",
    "auto_recharge_off",
    "live_authorized",
)


def money(value):
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0:
            raise ValueError
        return amount
    except (InvalidOperation, ValueError, TypeError):
        raise PilotError("Invalid private budget amount.", code="budget_invalid") from None


def reservation_total(store):
    ledger = store.read("budget.private.json")
    if ledger is None:
        ledger = {}
    if not isinstance(ledger, dict) or any(not re.fullmatch(r"\d{4}-\d{2}", k) for k in ledger):
        raise PilotError("Invalid reservation ledger.", code="budget_invalid")
    return sum((money(v) for v in ledger.values()), Decimal(0))


class CumulativeBudget:
    """Conservative lifetime reservations; no calendar reset, no automatic release.

    Actual Console observations are separate evidence. Without a reconciled billing
    cutoff we add actual and reservations conservatively, never release reservations automatically.
    """

    def __init__(self, store, rates, run_cap, max_calls, *, actual="0", cap="20"):
        if set(rates) != set(OPERATIONS):
            raise PilotError("Supply a reviewed reservation for each operation.")
        self.store, self.rates = store, {k: money(v) for k, v in rates.items()}
        self.per_call = max(self.rates.values())
        self.run_cap, self.cap, self.actual = money(run_cap), money(cap), money(actual)
        if (
            not 0 < self.cap <= 20
            or not 0 < self.run_cap <= self.cap
            or any(v <= 0 for v in self.rates.values())
            or self.rates["delete"] < Decimal(".005")
            or type(max_calls) is not int
            or not 1 <= max_calls <= 100
        ):
            raise PilotError("Invalid worker budget or request bounds.")
        self.used, self.max_calls = Decimal(0), max_calls
        self.operations = Counter()
        self.amounts = Counter()
        reservation_total(store)

    def reserve(self):
        self.reserve_operation("other")

    def reserve_operation(self, operation):
        amount = self.rates[operation]
        if (
            sum(self.operations.values()) >= self.max_calls
            or self.used + amount > self.run_cap
            or (self.actual + reservation_total(self.store)) + amount > self.cap
        ):
            raise PilotError("Cumulative/run reservation or request cap reached.", code="spend_cap")
        ledger = self.store.read("budget.private.json") or {}
        month = datetime.now(UTC).strftime("%Y-%m")
        ledger[month] = str(money(ledger.get(month, "0")) + amount)
        # Same authoritative ledger as manual commands. Crash can overreserve, not underreserve.
        self.store.write("budget.private.json", ledger)
        self.used += amount
        self.operations[operation] += 1
        self.amounts[operation] += amount


class Deferral:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock

    def remaining(self):
        saved = self.store.read(DEFER)
        if not saved:
            return 0
        until = saved.get("until")
        if type(until) not in (int, float) or not 0 <= until < 10**20:
            raise PilotError("Invalid retry checkpoint.")
        return max(0, until - self.clock())

    def check(self):
        if self.remaining():
            raise PilotError("Provider retry is deferred.", code="retry_deferred")

    def save(self, provider, *, reset=None, retry_after=None, seconds=900):
        now = self.clock()
        until = now + seconds
        if reset is not None and re.fullmatch(r"[0-9]{1,20}", str(reset)):
            until = max(now + 1, int(reset) + 1)
        if retry_after is not None and re.fullmatch(r"[0-9]{1,20}", str(retry_after)):
            until = max(until, now + int(retry_after) + 1)
        previous = self.store.read(DEFER) or {}
        until = max(until, previous.get("until", 0))
        self.store.write(DEFER, {"provider": provider, "until": until, "observed_at": now})

    def observe(self, response, provider):
        if response.status_code == 429:
            self.save(
                provider,
                reset=response.headers.get("x-rate-limit-reset"),
                retry_after=response.headers.get("retry-after"),
            )


class GuardedHTTP:
    """Persist reset before returning a 429 to any existing provider implementation."""

    def __init__(self, http, gate, provider):
        self.http, self.gate, self.provider = http, gate, provider

    def request(self, method, url, **kwargs):
        self.gate.check()
        response = self.http.request(method, url, **kwargs)
        self.gate.observe(response, self.provider)
        return response

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    @contextmanager
    def stream(self, method, url, **kwargs):
        self.gate.check()
        with self.http.stream(method, url, **kwargs) as response:
            self.gate.observe(response, self.provider)
            yield response


def removal_history(store, helper_descriptors=()):
    history = scan_native(store.root)
    seen = set()
    for descriptor in helper_descriptors:
        history.extend(scan_helper(descriptor, seen_files=seen))
    result = {}
    for attempt in history:
        pid = attempt["post_id"]
        # Any old intent is a hold, even rejected: no silent new save generation.
        result.setdefault(pid, set()).add(attempt["outcome"])
    return result


def snapshot(store, helpers=(), catalog=None):
    """Offline counts only. Does not create missing result receipts or disclose IDs."""
    history = removal_history(store, helpers)
    journal = store.read(JOURNAL) or {"items": {}, "batches": {}, "streams": {}}
    counts = Counter(i["state"] for i in journal["items"].values())
    archival = {}
    if catalog is not None and catalog.path("catalog.jsonl").exists():
        with catalog.locked():
            records = read_catalog(catalog)
        unattempted = [r for r in records if r["post_id"] not in history]
        complete = sum(
            any(
                v["archive_status"] == "bytes_verified" and not v["blockers"] for v in r["versions"]
            )
            for r in unattempted
        )
        archival = {
            "archived_records": len(records),
            "complete_archives_unattempted": complete,
            "blocked_archives_unattempted": len(unattempted) - complete,
            "catalog_sha256": digest(catalog.path("catalog.jsonl").read_bytes()),
        }
    return {
        **archival,
        "known_records": len(journal["items"]),
        "states": dict(counts),
        "confirmed_removed": sum("confirmed_removed" in h for h in history.values()),
        "outcome_unknown": sum("outcome_unknown" in h for h in history.values()),
        "attempted_ids_held": len(history),
        "reserved_cumulative_usd": str(reservation_total(store)),
        "actual_billing": store.read("worker-billing.private.json"),
        "deferred": bool(Deferral(store).remaining()),
        "all_history_complete": False,
    }


def merge_publication(output, incoming):
    """State lock -> catalog lock. Keep every old version and owner annotation."""
    with output.locked():
        prior = read_catalog(output) if output.path("catalog.jsonl").exists() else []
        merged = {r["post_id"]: r for r in prior}
        for record in incoming:
            target = merged.setdefault(record["post_id"], copy.deepcopy(record))
            for version in record["versions"]:
                identity = (version["manifest"]["sha256"], version["payload_receipt"]["sha256"])
                matches = [
                    i
                    for i, v in enumerate(target["versions"])
                    if (v["manifest"]["sha256"], v["payload_receipt"]["sha256"]) == identity
                ]
                if matches:
                    # Fresh restore may repair missing local bytes. Annotations are reapplied below.
                    target["versions"][matches[0]] = copy.deepcopy(version)
                else:
                    target["versions"].append(copy.deepcopy(version))
        records = [merged[k] for k in sorted(merged)]
        publish(output, apply_annotations(records, read_annotations(output)))


class Worker:
    def __init__(
        self,
        store,
        catalog,
        restore_root,
        client,
        drive,
        media,
        raindrop,
        budget,
        config,
        *,
        clock=time.time,
    ):
        self.store, self.catalog, self.restore_root = store, catalog, Path(restore_root)
        self.client, self.drive, self.media, self.raindrop = client, drive, media, raindrop
        self.budget, self.config, self.clock = budget, config, clock
        self.gate = Deferral(store, clock)
        self.state = store.read(JOURNAL) or {
            "schema_version": 1,
            "items": {},
            "batches": {},
            "streams": {},
            "next_daily": 0,
        }
        if self.state.get("schema_version") != 1:
            raise PilotError("Unsupported worker checkpoint.")
        self.refresh_history()
        self.counts = Counter()

    def refresh_history(self):
        helpers = self.config.get("helper_descriptors", [])
        self.history = removal_history(self.store, helpers)
        helper_ids = {a["post_id"] for path in helpers for a in scan_helper(path)}
        self.retry429 = set()
        for pid, outcomes in self.history.items():
            if outcomes != {"rejected"} or pid in helper_ids:
                continue
            base, _ = latest_attempt(self.store, self.config["owner_id"], pid)
            response = self.store.read(base + ".response.private.json") if base else None
            if response and response.get("status") == 429:
                self.retry429.add(pid)
        self.held = set(self.history) - self.retry429

    def save(self):
        self.store.write(JOURNAL, self.state)

    def eligible(self, pid):
        if pid in self.held:
            return False
        item = self.state["items"].get(pid)
        return item is None or (item["state"] == "retryable" and item["retry_at"] <= self.clock())

    def adopt(self, receipt_name, inventory):
        receipt = self.store.read(receipt_name)
        manifest = receipt["manifest"]
        child = self.restore_root / manifest["sha256"]
        parent = PublicationStore(self.restore_root)
        if not child.exists() and parent.reader_gid is not None:
            child.mkdir(mode=0o700)
            child.chmod(0o2750)  # setgid inherited from audited parent
        root = PublicationStore(child)
        # Fresh independent GET/hash readback; create_local tolerates identical crash remnants.
        restore_batch(self.drive, manifest["file_id"], root, manifest["sha256"], existing_ok=True)
        incoming = build([(root.root, self.store.path(receipt_name))], self.catalog)
        merge_publication(self.catalog, incoming)
        rows = [
            json.loads(line) for line in publication_bytes(root.path("manifest.jsonl")).splitlines()
        ]
        for row in rows:
            pid = row["post_id"]
            payload = json.loads(publication_bytes(root.path(pid + ".json")))
            current = inventory.state["posts"].get(pid)
            matches = current == payload.get("inventory_record")
            state = (
                "held_previous_attempt"
                if pid in self.held
                else ("queued" if not row["blockers"] and matches else "blocked")
            )
            previous = self.state["items"].get(pid)
            if previous and previous["checkpoint"] == inventory.name:
                if not matches:
                    continue
                if (
                    pid not in self.held
                    and previous["receipt"] == receipt_name
                    and (
                        previous["state"] in ("blocked", "retryable")
                        or previous.get("retry_at", 0) > self.clock()
                    )
                ):
                    # Re-publishing a shared batch must not silently clear an item's hold.
                    continue
            retryable = bool(row["blockers"]) and all(
                b in ("blocked_media_transport", "blocked_http_429", "blocked_http_503")
                for b in row["blockers"]
            )
            if state == "blocked" and retryable:
                state = "retryable"
            self.state["items"][pid] = {
                "state": state,
                "retry_at": self.clock() + 86400 if retryable else 0,
                "receipt": receipt_name,
                "restore_dir": str(root.root),
                "checkpoint": inventory.name,
                "blockers": row["blockers"] or ([] if matches else ["inventory_changed"]),
                "folder_ids": [f["id"] for f in row["folders"]],
            }
        if receipt_name not in self.state["batches"]:
            self.counts["archived"] += len(rows)
            self.counts["verified"] += sum(not r["blockers"] for r in rows)
        self.state["batches"][receipt_name] = manifest["sha256"]
        self.save()

    def bootstrap(self, inventory):
        # Adopt existing receipts, including the checkpoint, before making a new inventory.
        names = sorted(
            p.name
            for p in self.store.root.glob("archive-*.private.json")
            if re.fullmatch(r"archive-[a-f0-9]{24}\.private\.json", p.name)
        )
        for name in self.state["batches"]:
            receipt = self.store.read(name)
            if not receipt or receipt["manifest"]["sha256"] != self.state["batches"][name]:
                raise PilotError("Archived receipt missing or changed.")
        pending = [name for name in names if name not in self.state["batches"]]
        for name in pending[: self.config["max_batches"]]:
            self.adopt(name, inventory)
        return len(pending) <= self.config["max_batches"]

    def discover(self):
        # Never resume a positional cursor across a run or DELETE.
        inv = Inventory(
            self.store,
            self.config["owner_id"],
            self.config.get("dialect", "post"),
            page_size=100,
            max_records=200,
            max_folders=20,
            max_pages=1,
        )
        skipped = self.held | {pid for pid in self.state["items"] if not self.eligible(pid)}
        report = inv.run(
            self.client,
            self.budget,
            max_calls=self.config["max_calls"],
            hydrate_batch_size=20,
            skip_hydration=skipped,
        )
        self.counts["discovered"] += len(set(inv.state["posts"]) - set(self.state["items"]))
        self.coverage(inv)
        if report["stop_reason"]:
            raise PilotError("Inventory stopped.", code=report["stop_reason"])
        ids = [pid for pid in inv.state["posts"] if self.eligible(pid)][: self.config["max_items"]]
        for offset in range(0, len(ids), 20):
            batch = ids[offset : offset + 20]
            enriched = [pid for pid in batch if inv.state["posts"][pid].get("post")]
            if enriched:
                enrich_references(
                    inv, self.client, self.budget, self.config["owner_id"], enriched, len(enriched)
                )
            archived = archive_batch(inv, self.drive, self.media, post_ids=batch, limit=20)
            self.adopt(archived["private_receipt"], inv)
        return inv

    def coverage(self, inv):
        for key, stream in inv.state["streams"].items():
            if key == "folders":
                continue
            ids = {pid for page in stream["pages"] for pid in page["ids"]}
            previous = self.state["streams"].get(key, {})
            old = set(previous.get("observed_ids", []))
            cumulative = ids | set(previous.get("all_observed_ids", []))
            blockers = {
                pid for pid in ids if self.state["items"].get(pid, {}).get("state") == "blocked"
            }
            status = "stalled_on_blocked_window" if ids and ids <= blockers else "unresolved"
            # Metadata-free windows never prove exhaustion, even below 20.
            if stream["status"] == "exhausted" and len(ids) < 20:
                status = "exhausted_observed_window"
            elif old and ids - old:
                status = "new_ids_observed_age_unknown"
            self.state["streams"][key] = {
                "observed_ids": sorted(ids),
                "all_observed_ids": sorted(cumulative),
                "archived": sum(p in self.state["items"] for p in cumulative),
                "verified": sum(
                    p in self.state["items"] and not self.state["items"][p].get("blockers")
                    for p in cumulative
                ),
                "raindrop_link_verified": sum(
                    self.state["items"].get(p, {}).get("discovery_state") == "raindrop"
                    for p in cumulative
                ),
                "media_only_verified": sum(
                    self.state["items"].get(p, {}).get("discovery_state") == "media_only"
                    for p in cumulative
                ),
                "raindrop_missing_or_unavailable": sum(
                    self.state["items"].get(p, {}).get("discovery_state") == "raindrop_unverified"
                    for p in cumulative
                ),
                "discovery_gate_blocked": sum(
                    self.state["items"].get(p, {}).get("failure_reason") == "blocked_discovery"
                    for p in cumulative
                ),
                "deletion_blocked_raindrop": sum(
                    self.state["items"].get(p, {}).get("discovery_state") == "raindrop_unverified"
                    for p in cumulative
                ),
                "observed_at": self.clock(),
                "newly_observed": len(ids - old),
                "permanently_blocked": len(blockers),
                "confirmed_removed": sum(
                    "confirmed_removed" in self.history.get(p, ()) for p in cumulative
                ),
                "retryable_blocked": sum(
                    self.state["items"].get(p, {}).get("state") == "retryable" for p in ids
                ),
                "window_exposure_status": status,
                "all_history_complete": False,
            }
        self.save()

    def queued(self, pid, item):
        return (
            item["state"] == "queued"
            and pid not in self.held
            and item.get("retry_at", 0) <= self.clock()
        )

    def drain(self, inventory):
        selected = [pid for pid, item in self.state["items"].items() if self.queued(pid, item)][
            : self.config["max_items"]
        ]
        for pid in selected:
            self.gate.check()
            item = self.state["items"][pid]
            if item["checkpoint"] != inventory.name:
                raise PilotError("Queued archive belongs to another inventory.")
            # Refresh the independent restore and published projection before each transaction.
            self.adopt(item["receipt"], inventory)
            item = self.state["items"][pid]
            root = Path(item["restore_dir"])
            payload = json.loads(publication_bytes(root / (pid + ".json")))
            rows = [
                json.loads(line) for line in publication_bytes(root / "manifest.jsonl").splitlines()
            ]
            row = next((r for r in rows if r["post_id"] == pid), None)
            if row is None:
                raise PilotError(
                    "Queued ID is absent from restored manifest.", code="archive_mismatch"
                )
            with self.catalog.locked():
                classification = (self.catalog.read("media-only.private.json") or {}).get(pid)
            try:
                if not positive_media_only(payload, classification, row["payload"]["sha256"]):
                    raindrop_receipt = self.raindrop.ensure_raindrop(
                        pid,
                        source_url(pid),
                        {
                            "manifest": self.store.read(item["receipt"])["manifest"],
                            "payload": row["payload"],
                        },
                        post_text=primary_post_text(payload["posts"], pid),
                    )
                    with self.catalog.locked():
                        link_raindrop(self.catalog, read_catalog(self.catalog), raindrop_receipt)
            except PilotError as exc:
                retry_at = self.clock() + 86400
                if exc.code == "blocked_raindrop_retryable":
                    context = (exc.summary or {}).get("provider", {})
                    self.gate.save(
                        "raindrop",
                        reset=context.get("reset"),
                        retry_after=context.get("retry_after"),
                        seconds=86400,
                    )
                    retry_at = self.store.read(DEFER)["until"]
                item.update(
                    state="queued" if exc.code == "blocked_raindrop_retryable" else "blocked",
                    retry_at=retry_at,
                    failure_reason=exc.code,
                    discovery_state="raindrop_unverified",
                )
                self.save()
                raise
            plan = make_plan(
                self.store,
                self.drive,
                item["receipt"],
                [pid],
                self.config["owner_id"],
                acceptance_confirmed=True,
            )
            result = apply_plan(
                self.store,
                self.drive,
                self.client,
                self.budget,
                plan["private_plan"],
                self.config["owner_id"],
                confirmed=True,
                retry_rejected=pid in self.retry429,
                catalog_dir=self.catalog.root,
                raindrop=self.raindrop,
                legacy_helper_retired=True,
                max_calls=self.config["max_calls"],
            )
            self.refresh_history()
            self.counts["unbookmarked"] += result["states"].get("unbookmarked", 0)
            self.counts["delete_attempts"] += result["delete_attempts_this_run"]
            if pid in self.held:
                item["state"] = "held_previous_attempt"
                private = self.store.read(result["private_report"])
                for outcome in private["outcomes"]:
                    if outcome.get("attempt"):
                        intent = self.store.read(outcome["attempt"] + ".intent.private.json")
                        item["discovery_state"] = intent.get("discovery_gate", {}).get("kind")
            elif pid in self.retry429:
                item["state"] = "queued"
            elif result["blocked"]:
                item.update(
                    state="queued"
                    if result["stop_reason"]
                    in ("http_429", "retry_deferred", "blocked_raindrop_retryable")
                    else "retryable",
                    retry_at=self.clock() + 86400,
                )
            self.save()
            if result["stop_reason"]:
                private = self.store.read(result["private_report"])
                contexts = [
                    o.get("failure_context")
                    for o in private["outcomes"]
                    if o.get("failure_context")
                ]
                raise PilotError(
                    "Transaction stopped.",
                    code=result["stop_reason"],
                    summary=contexts[-1] if contexts else None,
                )

    def run(self, *, allow_delete=False):
        if self.gate.remaining():
            return {"quiet": True, "deferred": True}
        if self.clock() < self.state["next_daily"]:
            return {"quiet": True, "not_due": True}
        stop = None
        self.state["next_daily"] = self.clock() + 86400
        self.save()  # Must succeed before any provider call, including bootstrap.
        try:
            if self.store.read("inventory-active.private.json"):
                inventory = Inventory.load(self.store)
                if not self.bootstrap(inventory):
                    raise PilotError(
                        "More existing receipts await adoption.", code="bootstrap_pending"
                    )
            else:
                inventory = None
            queued = any(self.queued(pid, i) for pid, i in self.state["items"].items())
            if not queued:
                delayed = [
                    i.get("retry_at", 0)
                    for pid, i in self.state["items"].items()
                    if i["state"] == "queued"
                    and pid not in self.held
                    and i.get("retry_at", 0) > self.clock()
                ]
                if delayed:
                    # Keep the inventory bound to queued receipts; a new pass would invalidate it.
                    self.state["next_daily"] = min(delayed)
                    self.save()
                    return {"quiet": True, "not_due": True}
                inventory = self.discover()
            if allow_delete:
                self.drain(inventory)
            if inventory:
                self.coverage(inventory)
            self.state["next_daily"] = self.clock() + 86400
        except Exception as exc:
            # Never serialize exception text (paths, tokens and provider bodies may be present).
            stop = exc.code if isinstance(exc, PilotError) else "worker_failure"
            context = (exc.summary or {}).get("provider", {}) if isinstance(exc, PilotError) else {}
            if stop == "blocked_raindrop_retryable" and not self.gate.remaining():
                self.gate.save(
                    "raindrop",
                    reset=context.get("reset"),
                    retry_after=context.get("retry_after"),
                    seconds=86400,
                )
            # Only a durably observed rate limit may shorten the prewritten daily deadline.
            saved = self.store.read(DEFER) or {}
            if (
                saved.get("provider") in ("x", "drive", "media", "raindrop")
                and self.gate.remaining()
            ):
                self.state["next_daily"] = saved["until"]
            else:
                self.gate.save("worker", seconds=86400)
        self.save()
        report = {
            **snapshot(self.store, self.config.get("helper_descriptors", [])),
            "run_counts": dict(self.counts),
            "stop_reason": stop,
            "reserved_this_run_usd": str(self.budget.used),
            "reservations_by_operation": {
                k: {"requests": n, "usd": str(self.budget.amounts[k])}
                for k, n in self.budget.operations.items()
            },
            "remaining_reachable_queued": sum(
                i["state"] == "queued" for i in self.state["items"].values()
            ),
            "unreachable_remainder": "unknown",
            "actual_charge_this_run": "unverified",
        }
        self.store.write("worker-report.private.json", report)
        report["quiet"] = not stop and not any(self.counts.values())
        return report
