"""Bounded, model-free migration. Callers hold the existing manual-command lock.

Private provider receipts remain authoritative; this journal is a resumable queue,
never permission to delete. No independent implementation of the deletion gate.
"""

import copy
import hashlib
import json
import os
import re
import stat
import time
from collections import Counter
from contextlib import contextmanager
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
    remember_batches,
)
from .core import TOKEN_ROTATION, Client, PilotError
from .discovery_gate import positive_media_only
from .drive import digest, encode
from .inventory import Inventory
from .publication import PublicationStore, publication_bytes
from .repair import scan_helper, scan_native
from .repair_kinds import RestoredRepairs, archive_anchors, removable_version
from .source import source_url
from .unbookmark import apply_plan, latest_attempt, load_plan, make_plan

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
    "live_authorized",
)


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
        self.requests = 0

    @property
    def follow_redirects(self):
        return self.http.follow_redirects

    def request(self, method, url, **kwargs):
        self.gate.check()
        self.requests += 1
        response = self.http.request(method, url, **kwargs)
        self.gate.observe(response, self.provider)
        return response

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def patch(self, url, **kwargs):
        return self.request("PATCH", url, **kwargs)

    def delete(self, url, **kwargs):
        return self.request("DELETE", url, **kwargs)

    def put(self, url, **kwargs):
        return self.request("PUT", url, **kwargs)

    @contextmanager
    def stream(self, method, url, **kwargs):
        self.gate.check()
        self.requests += 1
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


def snapshot(store, helpers=(), catalog=None, *, restore_dir=None):
    """Offline counts only. Does not create missing result receipts or disclose IDs."""
    history = removal_history(store, helpers)
    journal = store.read(JOURNAL) or {"items": {}, "batches": {}, "streams": {}}
    counts = Counter(i["state"] for i in journal["items"].values())
    archival = {}
    if catalog is not None and catalog.path("catalog.jsonl").exists():
        pin = store.read("drive-destination.private.json") or {}
        anchors = archive_anchors(store, pin.get("root"), report_only=True)
        with catalog.locked():
            records = read_catalog(catalog)
            restores = RestoredRepairs(catalog, restore_dir)
            counts_by_archive = Counter()
            for record in records:
                pid = record["post_id"]
                if pid in history:
                    continue
                complete = [
                    v
                    for v in record["versions"]
                    if v["archive_status"] == "bytes_verified" and not v["blockers"]
                ]
                if not complete:
                    counts_by_archive["blocked_archives_unattempted"] += 1
                elif any(removable_version(restores, pid, v, anchors) for v in complete):
                    counts_by_archive["complete_archives_unattempted"] += 1
                else:
                    counts_by_archive["complete_archives_not_removable"] += 1
            archival = {
                "archived_records": len(records),
                "complete_archives_unattempted": counts_by_archive["complete_archives_unattempted"],
                "complete_archives_not_removable": counts_by_archive[
                    "complete_archives_not_removable"
                ],
                "blocked_archives_unattempted": counts_by_archive["blocked_archives_unattempted"],
                "catalog_sha256": digest(catalog.path("catalog.jsonl").read_bytes()),
            }
    return {
        **archival,
        **({"drive_search": journal["drive_search"]} if "drive_search" in journal else {}),
        "known_records": len(journal["items"]),
        "states": dict(counts),
        "confirmed_removed": sum("confirmed_removed" in h for h in history.values()),
        "outcome_unknown": sum("outcome_unknown" in h for h in history.values()),
        "attempted_ids_held": len(history),
        "deferred": bool(Deferral(store).remaining()),
        "all_history_complete": False,
    }


def catalog_index(records):
    indexed = {r["post_id"]: r for r in records}
    if len(indexed) != len(records):
        raise PilotError("Duplicate catalog primary.")
    return indexed


def require_catalog_versions(records, chains):
    for chain in chains:
        record = records.get(chain["post_id"], {})
        if not any(
            v["manifest"] == chain["manifest"] and v["payload_receipt"] == chain["payload"]
            for v in record.get("versions", [])
        ):
            raise PilotError("Catalog lacks the bound predecessor or donor version.")


def merge_publication(output, incoming, *, batches=(), required_versions=()):
    """State lock -> catalog lock. Keep every old version and owner annotation."""
    with output.locked():
        prior = read_catalog(output) if output.path("catalog.jsonl").exists() else []
        merged = catalog_index(prior)
        require_catalog_versions(merged, required_versions)
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
        if batches:
            remember_batches(output, batches)
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
                intent = self.store.read(base + ".intent.private.json")
                plan_file = intent.get("plan_file") if isinstance(intent, dict) else None
                if isinstance(plan_file, str) and not plan_file.startswith("backlog-plan-"):
                    self.retry429.add(pid)
        self.held = set(self.history) - self.retry429

    def save(self):
        self.store.write(JOURNAL, self.state)

    def require_manual_removal(self):
        self.gate.check()
        if self.config["max_items"] != 1 or not all(
            self.config["gates"].get(key) is True for key in GATES
        ):
            raise PilotError("Verified-plan removal requires every live gate and max_items=1.")

    def apply_verified_plan(
        self,
        *,
        expected_pointer=None,
        pointer_name="verified-one-plan.private.json",
        expected_pointer_sha256=None,
    ):
        """Manual single-item transaction; never discover, drain or create a link."""
        self.require_manual_removal()
        pointer = self.store.read(pointer_name) or {}
        if (expected_pointer is not None and pointer != expected_pointer) or (
            expected_pointer_sha256 is not None
            and digest(encode(pointer)) != expected_pointer_sha256
        ):
            raise PilotError("Saved plan pointer changed after verification.")
        key = pointer.get("drive_check_sha256")
        name = pointer.get("plan")
        if not (
            isinstance(key, str)
            and re.fullmatch("[0-9a-f]{64}", key)
            and isinstance(name, str)
            and re.fullmatch(r"unbookmark-plan-[0-9a-f]{24}\.private\.json", name)
        ):
            raise PilotError("Missing or invalid verified-plan pointer.")
        proof = self.store.read(f"one-drive-check-{key}.private.json")
        if (
            not isinstance(proof, dict)
            or hashlib.sha256(json.dumps(proof, sort_keys=True).encode()).hexdigest() != key
        ):
            raise PilotError("Drive proof digest differs.")
        owner = self.config["owner_id"]
        plan = load_plan(self.store, name, owner)
        items = plan["items"]
        pid = proof["post_id"]
        if not (
            proof["schema_version"] == 1
            and len(items) == 1
            and items[0]["post_id"] == pid
            and items[0]["state"] == "verified"
            and items[0]["payload"]["sha256"] == proof["payload_sha256"]
            and plan["archive_receipt"]["manifest"]["sha256"] == proof["manifest_sha256"]
            and plan["inventory_checkpoint"]
            == proof["inventory"]
            == Inventory.load(self.store).name
        ):
            raise PilotError("Saved plan differs from the single verified Drive item.")
        return self.apply_one(name, plan)

    def apply_one(self, name, plan, *, binding=None, restore_dir=None):
        self.require_manual_removal()
        (item,) = plan["items"]
        pid = item["post_id"]
        queued = self.state["items"].get(pid)
        if (
            pid in self.held
            or pid in self.history
            or (queued is not None and not self.queued(pid, queued))
        ):
            raise PilotError("Saved item has a previous attempt or a worker hold.")
        return apply_plan(
            self.store,
            self.drive,
            self.client,
            self.budget,
            name,
            self.config["owner_id"],
            confirmed=True,
            catalog_dir=self.catalog.root,
            raindrop=self.raindrop,
            legacy_helper_retired=True,
            require_raindrop=True,
            staged_plan=plan if binding is not None else None,
            backlog_binding=binding,
            restore_dir=restore_dir,
            max_calls=self.config["max_calls"],
        )

    def eligible(self, pid):
        if pid in self.held:
            return False
        item = self.state["items"].get(pid)
        return item is None or (item["state"] == "retryable" and item["retry_at"] <= self.clock())

    def adopt(self, receipt_name, inventory, *, superseded=False):
        receipt = self.store.read(receipt_name)
        manifest = receipt["manifest"]
        child = self.restore_root / manifest["sha256"]
        parent = PublicationStore(self.restore_root)
        if parent.reader_gid is not None:
            if child.exists():
                entry = child.lstat()
                if (
                    stat.S_ISDIR(entry.st_mode)
                    and stat.S_IMODE(entry.st_mode) == 0o2700
                    and entry.st_uid == os.getuid()
                    and entry.st_gid == parent.reader_gid
                    and not os.listxattr(child)
                ):
                    # Only an empty remnant of the previous mkdir/chmod can be removed.
                    child.rmdir()
            if not child.exists():
                # Inherit setgid from the audited parent. Request only reader r-x;
                # the service forbids explicitly setting setgid via chmod.
                previous_mask = os.umask(0o027)
                try:
                    child.mkdir(mode=0o750)
                finally:
                    os.umask(previous_mask)
        root = PublicationStore(child)
        # Fresh independent GET/hash readback; create_local tolerates identical crash remnants.
        restore_batch(self.drive, manifest["file_id"], root, manifest["sha256"], existing_ok=True)
        # Owner annotations refer to the full catalog, not this individual batch.
        incoming = build(
            [(root.root, self.store.path(receipt_name))], self.catalog, apply_curation=False
        )
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
            if (
                previous
                and (superseded or receipt_name in self.state["batches"])
                and previous["receipt"] != receipt_name
            ):
                # Refreshing a sibling in an old batch cannot supersede a later retry's receipt.
                continue
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
            if superseded and state == "queued":
                state = "retryable"  # Only fresh discovery can authorize another plan.
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
        daily_parents = {}
        for name in pending[: self.config["max_batches"]]:
            daily_pass = self.store.read(name).get("daily_pass")
            if daily_pass is None:
                self.adopt(name, inventory)
                continue
            bound = Inventory.read_pass(self.store, daily_pass)
            if bound.state["owner_id"] != inventory.state["owner_id"]:
                raise PilotError("Daily archive owner differs.")
            parent = bound.state["daily_parent"]
            daily_parents[daily_pass] = parent
            self.adopt(name, bound, superseded=(parent != inventory.state["pass_id"]))

        # The desktop can advance the baseline under the same lock between timer runs.
        # Keep receipts and retry deadlines. Never execute the plan bound to the old pass.
        for pid, item in self.state["items"].items():
            if (
                item["state"] != "queued"
                or pid in self.held
                or item["checkpoint"] == inventory.name
            ):
                continue
            daily_pass = self.store.read(item["receipt"]).get("daily_pass")
            if daily_pass is not None:
                if item["checkpoint"] != Inventory.checkpoint_name(daily_pass):
                    raise PilotError("Queued daily receipt and checkpoint differ.")
                if daily_pass not in daily_parents:
                    daily_parents[daily_pass] = Inventory.read_pass(self.store, daily_pass).state[
                        "daily_parent"
                    ]
                if daily_parents[daily_pass] == inventory.state["pass_id"]:
                    continue
            item.update(
                state="retryable",
                retry_at=item.get("retry_at", 0),
                failure_reason="superseded_inventory",
            )
            self.counts["superseded_queued"] += 1
        if self.counts["superseded_queued"]:
            self.save()
        return len(pending) <= self.config["max_batches"]

    def historical_stops(self, baseline):
        items = self.state["items"]
        blocked = {pid for pid, item in items.items() if item["state"] == "blocked"}
        settled = blocked | {
            pid for pid, states in self.history.items() if "confirmed_removed" in states
        }
        stopped = set()
        if baseline:
            for key, stream in baseline.state["streams"].items():
                visible = set(stream["pages"][0]["ids"]) if stream["pages"] else set()
                if stream["status"] == "paused" or (
                    key != "folders" and visible and visible <= blocked
                ):
                    stopped.add(key)
        for key, coverage in self.state["streams"].items():
            visible = set(coverage.get("observed_ids", []))
            if coverage.get("window_exposure_status") == "stalled_on_blocked_window" or (
                visible and visible <= blocked
            ):
                stopped.add(key)

        # A category check for a daily save cannot release a historical stop.
        # A later successful desktop comparison can record a changed condition.
        comparisons = {}
        for path in self.store.root.glob("unbookmark-*.intent.private.json"):
            intent = self.store.read(path.name)
            if intent["owner_id"] != self.config["owner_id"]:
                continue
            verified = intent.get("verified") or {}
            folder = (verified.get("folder") or {}).get("id")
            if not folder:
                self.counts["historical_stops_unattributed"] += 1
                stopped.add("folders")
                continue
            window = self.store.read(path.name.replace(".intent.", ".window.")) or {}
            stop = window.get("window_status") != "new_ids_observed"
            if not stop:
                visible = set(window["after"]["ids"])
                stop = bool(visible and visible <= blocked)
            plan = self.store.read(intent["plan_file"]) if intent.get("plan_file") else None
            if not stop and not window.get("not_previously_inventoried_ids"):
                members = intent.get("inventory_folder_post_ids")
                if (
                    members is None
                    and baseline
                    and plan
                    and plan.get("inventory_checkpoint") == baseline.name
                ):
                    members = [
                        pid
                        for pid in intent.get("inventory_post_ids", [])
                        if folder in baseline.state["posts"].get(pid, {}).get("folder_ids", [])
                    ]
                stop = members is None or not (set(members) - settled)
            if stop or (plan and not plan.get("archive_receipt", {}).get("daily_pass")):
                key = "folder:" + folder
                observation = (intent.get("created_at", ""), stop)
                comparisons[key] = max(comparisons.get(key, observation), observation)
        stopped.update(key for key, (_, stop) in comparisons.items() if stop)
        if baseline:
            known_folders = {"folder:" + fid for fid in baseline.state["folders"]}
            if known_folders and known_folders <= stopped:
                stopped.add("folders")
        return stopped

    def discover(self, *, scheduled=False, baseline=None, history_only=False):
        # Never resume a positional cursor across a run or DELETE.
        inv = Inventory(
            self.store,
            self.config["owner_id"],
            self.config.get("dialect", "post"),
            daily=scheduled,
            page_size=100,
            max_records=200,
            max_folders=20,
            max_pages=1,
        )
        skipped = self.held | {pid for pid in self.state["items"] if not self.eligible(pid)}
        target = self.config.get("intake_target")
        if scheduled and target and target not in skipped and not history_only:
            inv.state["intake_target"] = target
        if history_only:
            inv.state["historical_fallback"] = True
            inv.state["streams"]["main"].update(status="paused", reason="between_intakes")
            inv.save()
        known_history = set()
        if scheduled and baseline and "daily_parent" not in baseline.state:
            known_history = baseline.state["posts"].keys() - self.state["items"].keys()
        empty_queue = not any(item["state"] == "queued" for item in self.state["items"].values())
        report = inv.run(
            self.client,
            self.budget,
            max_calls=self.config["max_calls"],
            hydrate_batch_size=20,
            skip_hydration=skipped,
            known_history=known_history,
            historical_stops=(lambda: self.historical_stops(baseline))
            if scheduled and (empty_queue or history_only)
            else None,
            primary_limit=self.config["max_items"],
        )
        if not scheduled:
            self.coverage(inv)
        self.counts["discovered"] += len(
            inv.primary_candidates(skipped | self.state["items"].keys(), None, known_history)
        )
        if report["stop_reason"]:
            raise PilotError("Inventory stopped.", code=report["stop_reason"])
        ids = inv.primary_candidates(skipped, self.config["max_items"], known_history)
        for offset in range(0, len(ids), 20):
            batch = ids[offset : offset + 20]
            enriched = [pid for pid in batch if inv.state["posts"][pid].get("post")]
            if enriched:
                enrich_references(
                    inv, self.client, self.budget, self.config["owner_id"], enriched, len(enriched)
                )
            archived = archive_batch(inv, self.drive, self.media, post_ids=batch, limit=20)
            self.adopt(archived["private_receipt"], inv)
            if scheduled:
                self.counts["daily_category_unknown"] += sum(
                    "blocked_category_unknown" in self.state["items"][pid]["blockers"]
                    for pid in batch
                )
        return inv

    def coverage(self, inv):
        for key, stream in inv.state["streams"].items():
            if key == "folders":
                continue
            if inv.state.get("historical_fallback") and (key == "main" or not stream["pages"]):
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

    def pending_retries(self):
        return [
            item["retry_at"]
            for pid, item in self.state["items"].items()
            if item["state"] == "queued"
            and pid not in self.held
            and item.get("retry_at", 0) > self.clock()
        ]

    def drain(self, inventory, *, scheduled=False):
        items = self.state["items"]
        selected = [pid for pid, item in items.items() if self.queued(pid, item)]
        if scheduled and inventory:
            # A pending daily receipt keeps its priority after an interrupted run or provider reset.
            selected.sort(
                key=lambda pid: (
                    pid != self.config.get("intake_target"),
                    items[pid]["checkpoint"] == inventory.name,
                )
            )
        for pid in selected[: self.config["max_items"]]:
            # Don't begin a fresh removal without room for its identity, folder,
            # before-window, DELETE, and after-window checks.
            if scheduled and self.config["max_calls"] - self.budget.operations.total() < 5:
                break
            self.gate.check()
            item = self.state["items"][pid]
            daily_pass = self.store.read(item["receipt"]).get("daily_pass")
            bound = Inventory.load(self.store, daily_pass=daily_pass) if daily_pass else inventory
            if item["checkpoint"] != bound.name:
                raise PilotError("Queued archive belongs to another inventory.")
            # Refresh the independent restore and published projection before each transaction.
            self.adopt(item["receipt"], bound)
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
                links = read_annotations(self.catalog).get("raindrop_links", {})
            target = scheduled and pid == self.config.get("intake_target")
            if target and not payload["media"]:
                item.update(state="blocked", failure_reason="acceptance_media_missing")
                self.counts["acceptance_media_missing"] += 1
                self.save()
                continue
            try:
                if pid not in links and (
                    target
                    or not positive_media_only(payload, classification, row["payload"]["sha256"])
                ):
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
                daily_pass=daily_pass,
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
                daily_pass=daily_pass,
                catalog_dir=self.catalog.root,
                restore_dir=root if scheduled else None,
                require_raindrop=target,
                raindrop=self.raindrop,
                legacy_helper_retired=True,
                max_calls=self.config["max_calls"],
            )
            self.refresh_history()
            if (
                scheduled
                and result["new_window_observations"]
                and result["delete_attempts_this_run"]
            ):
                self.state["history_probe_due"] = True
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
                # If earlier X work used up this run's slots before this item's
                # DELETE intent, leave it eligible for a fresh bounded pass.
                if scheduled and result["stop_reason"] == "request_limit":
                    item.update(state="queued", retry_at=self.clock() + 300)
                    self.save()
                    break
                item.update(
                    state="queued"
                    if result["stop_reason"]
                    in ("http_429", "retry_deferred", "blocked_raindrop_retryable")
                    else "retryable",
                    retry_at=self.clock() + 86400,
                )
            self.save()
            if result["stop_reason"]:
                if (
                    scheduled
                    and self.config.get("continue_queued_history")
                    and item["state"] == "held_previous_attempt"
                    and result["states"].get("unbookmarked") == 1
                    and result["stop_reason"] in {"observed_empty", "no_new_ids_observed"}
                ):
                    # An acknowledged removal with corroborated absence stops this
                    # folder. Don't let that window defer work in unrelated folders.
                    folder_ids = set(item["folder_ids"])
                    for other in items.values():
                        if other["state"] == "queued" and folder_ids.intersection(
                            other["folder_ids"]
                        ):
                            other.update(
                                state="retryable",
                                retry_at=max(other.get("retry_at", 0), self.clock() + 86400),
                                failure_reason=result["stop_reason"],
                            )
                    self.state["history_probe_due"] = True
                    self.counts[result["stop_reason"]] += 1
                    self.save()
                    break
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

    def run(self, *, allow_delete=False, scheduled=False):
        if self.gate.remaining():
            return {"quiet": True, "deferred": True}
        now = self.clock()
        if scheduled and "next_intake" not in self.state:
            # Legacy next_daily may be a single item's distant retry. Honor at most one paid day.
            self.state["next_intake"] = min(self.state["next_daily"], now + 86400)
            self.save()
        intake_pending = scheduled and now >= self.state["next_intake"]
        if not intake_pending and now < self.state["next_daily"]:
            return {"quiet": True, "not_due": True}
        stop = None
        self.state["next_daily"] = now + 86400
        if intake_pending:
            self.state["next_intake"] = self.state["next_daily"]
        self.save()  # Must succeed before any provider call, including bootstrap.
        daily = None
        try:
            if self.store.read("inventory-active.private.json"):
                inventory = Inventory.load(self.store)
                if inventory.state["owner_id"] != self.config["owner_id"]:
                    raise PilotError("Active inventory owner differs.")
                if not self.bootstrap(inventory):
                    if scheduled and self.config.get("continue_queued_history"):
                        self.state["next_daily"] = now + 300
                        if intake_pending:
                            self.state["next_intake"] = self.state["next_daily"]
                        self.save()
                        return {"bootstrap_pending": True, "run_counts": dict(self.counts)}
                    raise PilotError(
                        "More existing receipts await adoption.", code="bootstrap_pending"
                    )
            else:
                inventory = None
            if intake_pending:
                daily = self.discover(scheduled=True, baseline=inventory)
                intake_pending = (
                    False  # A later transaction reset must not repeat this paid window.
                )
                if inventory is None:
                    inventory = daily
            elif (
                scheduled
                and allow_delete
                and self.config.get("continue_queued_history")
                and self.state.get("history_probe_due")
                and not any(self.queued(pid, item) for pid, item in self.state["items"].items())
            ):
                daily = self.discover(scheduled=True, baseline=inventory, history_only=True)
                self.state.pop("history_probe_due", None)
                self.save()
            elif not scheduled and not any(
                self.queued(pid, i) for pid, i in self.state["items"].items()
            ):
                delayed = self.pending_retries()
                if delayed:
                    # Keep the inventory bound to queued receipts; a new pass would invalidate it.
                    self.state["next_daily"] = min(delayed)
                    self.save()
                    return {"quiet": True, "not_due": True}
                inventory = self.discover()
            if allow_delete:
                self.drain(inventory, scheduled=scheduled)
            if inventory and not scheduled:
                self.coverage(inventory)
            deadline = self.clock() + 86400
            retries = self.pending_retries() if scheduled and allow_delete else []
            if (
                scheduled
                and allow_delete
                and self.config.get("continue_queued_history") is True
                and (
                    self.state.get("history_probe_due")
                    or any(self.queued(pid, item) for pid, item in self.state["items"].items())
                )
            ):
                retries.append(self.clock() + 300)
            self.state["next_daily"] = min([deadline, *retries])
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
                if intake_pending:
                    self.state["next_intake"] = saved["until"]
            else:
                self.gate.save("worker", seconds=86400)
        if daily and daily.state.get("historical_fallback"):
            self.coverage(daily)
        self.save()
        report = {
            **snapshot(self.store, self.config.get("helper_descriptors", [])),
            "run_counts": dict(self.counts),
            "stop_reason": stop,
            "request_slots_by_operation": dict(self.budget.operations),
            "remaining_reachable_queued": sum(
                i["state"] == "queued" for i in self.state["items"].values()
            ),
            "unreachable_remainder": "unknown",
            "actual_charge_this_run": "unverified",
        }
        self.store.write("worker-report.private.json", report)
        report["quiet"] = not stop and not any(self.counts.values())
        return report
