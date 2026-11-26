"""One adapter/ledger for prospective migration and retrospective backfill.

All authentication and HTTP go through openclaw-config's existing Raindrop skill.
Saved inventories/receipts are historical evidence, never deletion authorization.
"""

import json
import os
import re
import subprocess
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .catalog import drive_link
from .core import PilotError, Store, exact_id
from .drive import digest, encode
from .inventory import now
from .source import raindrop_post_id, source_url


def require_distinct_state_dirs(**directories):
    resolved = {}
    for name, path in directories.items():
        if path is None:
            continue
        canonical = Path(path).resolve()
        if canonical in resolved:
            raise PilotError(
                f"{resolved[canonical]} and {name} must be distinct state directories.",
                code="overlapping_state_directories",
            )
        resolved[canonical] = name


def blocked(reason, *, retryable=False, context=None, raindrop_id=None):
    return PilotError(
        "Raindrop verification blocked: " + reason + ".",
        summary={
            "blocked": True,
            "retryable": retryable,
            "reason": reason,
            **({"provider": context} if context else {}),
            **({"raindrop_id": raindrop_id} if raindrop_id is not None else {}),
        },
        code="blocked_raindrop_retryable" if retryable else "blocked_raindrop_conflict",
    )


def item_identity(item):
    if not isinstance(item, dict):
        raise blocked("invalid_item")
    rid, collection = item.get("_id"), item.get("collection")
    cid = collection.get("$id") if isinstance(collection, dict) else None
    if type(rid) is not int or rid <= 0 or type(cid) is not int:
        raise blocked("invalid_item")
    if not isinstance(item.get("link"), str):
        raise blocked("invalid_primary_link")
    return rid, cid


def validate_inventory(snapshot):
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema_version") != 1
        or snapshot.get("result") is not True
        or snapshot.get("coverage") != {"complete": True, "stable_passes": 2, "perpage": 50}
        or not isinstance(snapshot.get("observed_at"), str)
    ):
        raise blocked("incomplete_enumeration", retryable=True)
    seen = set()
    for key in ("active", "trash"):
        part = snapshot.get(key)
        if not isinstance(part, dict) or not isinstance(part.get("items"), list):
            raise blocked("incomplete_enumeration", retryable=True)
        count, pages = part.get("count"), part.get("pages")
        if (
            type(count) is not int
            or count != len(part["items"])
            or type(pages) is not int
            or not 1 <= pages <= 1000
            or pages != count // 50 + 1
        ):
            raise blocked("incomplete_enumeration", retryable=True)
        for item in part["items"]:
            rid, cid = item_identity(item)
            if rid in seen or (key == "trash") != (cid == -99):
                raise blocked("conflicting_inventory")
            if key == "active" and (
                cid != -1 and cid <= 0 or item.get("removed", False) is not False
            ):
                raise blocked("removed_inventory_item")
            seen.add(rid)
            # Unsupported X links must block absence inference for every target.
            try:
                raindrop_post_id(item["link"])
            except PilotError:
                raise blocked("unsupported_x_link", raindrop_id=rid) from None
    return snapshot


def inspect_inventory(snapshot, pid):
    """Offline inspection only. No freshness or permission to create/delete."""
    exact_id(pid)
    try:
        validate_inventory(snapshot)
        matches = {
            key: [
                item["_id"]
                for item in snapshot[key]["items"]
                if raindrop_post_id(item["link"]) == pid
            ]
            for key in ("active", "trash")
        }
    except PilotError as exc:
        # IDs/reasons are local validated diagnostics; never expose the private URL.
        return {
            "state": "incomplete",
            "raindrop_ids": [],
            "reason": exc.summary.get("reason", "invalid_inventory"),
            **(
                {"blocking_raindrop_id": exc.summary["raindrop_id"]}
                if "raindrop_id" in exc.summary
                else {}
            ),
        }
    except (ValueError, TypeError, KeyError, AttributeError):
        return {"state": "incomplete", "raindrop_ids": [], "reason": "invalid_inventory"}
    active, trash = matches["active"], matches["trash"]
    state = (
        "conflict"
        if len(active) > 1 or (active and trash)
        else ("trash" if trash else "active" if active else "absent")
    )
    return {
        "state": state,
        "raindrop_ids": active + trash,
        "reason": None if state in ("active", "absent") else "explicit_disposition_required",
    }


def validate_receipt(receipt, pid):
    """Validate identity/schema of historical evidence; does not make a GET."""
    exact_id(pid)
    try:
        valid = (
            isinstance(receipt, dict)
            and receipt.get("schema_version") == 1
            and receipt.get("source_post_id") == pid
            and receipt.get("source_post_url") == source_url(pid)
            and type(receipt.get("raindrop_id")) is int
            and receipt["raindrop_id"] > 0
            and type(receipt.get("collection_id")) is int
            and (receipt["collection_id"] == -1 or receipt["collection_id"] > 0)
            and raindrop_post_id(receipt.get("primary_link")) == pid
            and isinstance(receipt.get("verified_at"), str)
            and bool(receipt["verified_at"])
            and isinstance(receipt.get("response_sha256"), str)
            and re.fullmatch("[0-9a-f]{64}", receipt["response_sha256"])
        )
    except (PilotError, TypeError, ValueError):
        valid = False
    if not valid:
        raise blocked("invalid_receipt")
    return receipt


class RaindropAdapter:
    def __init__(self, ledger_dir: Path, script: Path, *, script_sha256=None, helper_sha256=None):
        self.store = Store(Path(ledger_dir))
        self.script = Path(script)
        self.script_sha256 = script_sha256
        self.helper_sha256 = helper_sha256
        self._repair_run = None
        self.repair_stats = {}
        if not self.script.is_absolute() or not self.script.is_file():
            raise blocked("absolute_existing_skill_path_required")

    def _call(self, *args, payload=None):
        environment = None
        if self.script_sha256 is not None:
            try:
                for path, expected in (
                    (self.script, self.script_sha256),
                    (self.script.with_name("list_all.py"), self.helper_sha256),
                ):
                    if (
                        not isinstance(expected, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", expected)
                        or path.is_symlink()
                        or not path.is_file()
                        or digest(path.read_bytes()) != expected
                    ):
                        raise blocked("script_pin_mismatch")
            except OSError:
                raise blocked("script_pin_unavailable") from None
            if not os.environ.get("RAINDROP_TOKEN"):
                raise blocked("explicit_environment_token_required")
            # No BASH_ENV/ENV, shell options, loader/Python hooks, or HOME fallback.
            environment = {
                "PATH": "/usr/bin:/bin",
                "HOME": "/nonexistent",
                "RAINDROP_TOKEN": os.environ["RAINDROP_TOKEN"],
                "LANG": "C.UTF-8",
            }
        try:
            result = subprocess.run(
                ["/bin/bash", str(self.script), "--json", "--delay", "600", *map(str, args)],
                input=json.dumps(payload) if payload is not None else None,
                capture_output=True,
                **({"env": environment} if environment is not None else {}),
                text=True,
                timeout=3600 if args == ("list-all",) else 75,
            )
        except (OSError, subprocess.SubprocessError):
            raise blocked("transport_outcome_unknown", retryable=True) from None
        if result.returncode:
            context = {}
            # Whitelist numeric diagnostic fields only; never relay arbitrary stderr.
            for line in result.stderr.splitlines():
                try:
                    error = json.loads(line)
                    for key in ("http_status", "reset", "retry_after", "remaining"):
                        value = error.get(key)
                        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}", value):
                            context[key] = value
                except (ValueError, AttributeError):
                    pass
            raise blocked("provider_request_failed", retryable=True, context=context)
        try:
            body = json.loads(result.stdout)
        except ValueError:
            raise blocked("invalid_response", retryable=True) from None
        if not isinstance(body, dict) or body.get("result") is not True:
            raise blocked("response_failed", retryable=True)
        return body

    def enumerate_now(self):
        with self.store.locked():
            return self._enumerate()

    def _enumerate(self):
        snapshot = validate_inventory(self._call("list-all"))
        self.store.write("raindrop-inventory.private.json", snapshot)
        return snapshot

    @contextmanager
    def repair_session(self, selected_ids, *, max_seconds=900, verify_created_ids=()):
        """Route existing ensure callers through one bounded historical run.

        Yield the same stats dict exposed as repair_stats. GET/POST counts are
        attempted ensure requests, including failed calls. Baseline page counts
        describe each validated pass, not measured total HTTP requests.
        """
        if self._repair_run is not None:
            raise blocked("repair_session_already_active")
        selected = _selected_ids(selected_ids)
        verify_created = _selected_ids(verify_created_ids) if verify_created_ids else frozenset()
        if not verify_created <= selected:
            raise blocked("metadata_check_outside_selection")
        self.repair_stats = {}
        with self.bounded_inventory(
            max_operations=len(selected), max_age_seconds=max_seconds, post_ids=selected
        ) as run:
            run.verify_created_ids = verify_created
            self._repair_run = run
            self.repair_stats = run.stats
            try:
                yield self.repair_stats
            finally:
                self._repair_run = None

    @contextmanager
    def bounded_inventory(self, *, max_operations, max_age_seconds=900, post_ids=None):
        """Opt-in backfill scope; never use its baseline as fresh DELETE evidence.

        Holds the ledger lock throughout. Start a new scope (and real inventory)
        after failure/resume. External provider writers are not locked: callers
        must keep this short and avoid concurrent account edits.
        Supply post_ids to restrict the scope to a preselected historical batch.
        """
        if (
            type(max_operations) is not int
            or not 1 <= max_operations <= 100
            or type(max_age_seconds) is not int
            or not 1 <= max_age_seconds <= 900
        ):
            raise blocked("invalid_inventory_bounds")
        selection = None
        if post_ids is not None:
            selection = _selected_ids(post_ids)
        with self.store.locked():
            started = time.monotonic()
            run = _InventoryRun(
                self, self._enumerate(), max_operations, started + max_age_seconds, selection
            )
            try:
                run.check()
                yield run
            finally:
                run.closed = True

    def verify_raindrop_now(self, pid, raindrop_id):
        """Always fresh authenticated single GET. No receipt/cache fallback."""
        proof, _ = self._verify_item(pid, raindrop_id)
        return proof

    def _verify_item(self, pid, raindrop_id):
        exact_id(pid)
        if type(raindrop_id) is not int or raindrop_id <= 0:
            raise blocked("invalid_raindrop_id")
        body = self._get_evidence(pid, raindrop_id)
        if (
            not isinstance(body, dict)
            or body.get("result") is not True
            or not isinstance(body.get("item"), dict)
        ):
            raise blocked("response_failed", retryable=True)
        item = body.get("item")
        rid, cid = item_identity(item)
        if (
            rid != raindrop_id
            or (cid != -1 and cid <= 0)
            or item.get("removed", False) is not False
        ):
            raise blocked("inactive_or_wrong_raindrop")
        if raindrop_post_id(item["link"]) != pid:
            raise blocked("wrong_primary_post")
        return {
            "schema_version": 1,
            "source_post_id": pid,
            "source_post_url": source_url(pid),
            "raindrop_id": rid,
            "verified_at": now(),
            "primary_link": item["link"],
            "collection_id": cid,
            "response_sha256": digest(encode(body)),
        }, item

    def _get_evidence(self, pid, raindrop_id):
        body = self._call("get", raindrop_id)
        self.store.write(
            f"raindrop-{pid}.get-{uuid4().hex}.private.json",
            {
                "schema_version": 1,
                "raindrop_id": raindrop_id,
                "response": body,
                "received_at": now(),
            },
        )
        return body

    def replace_note(self, pid, raindrop_id, post_text):
        """Explicit note-only edit, with fresh identity and metadata readback checks."""
        exact_id(pid)
        if not isinstance(post_text, str) or not post_text.strip():
            raise blocked("primary_post_text_required")
        if type(raindrop_id) is not int or raindrop_id <= 0:
            raise blocked("invalid_raindrop_id")
        with self.store.locked():
            _, before = self._verify_item(pid, raindrop_id)
            note = post_text[:250]
            self._call("update-note-json", raindrop_id, payload={"note": note})
            proof, after = self._verify_item(pid, raindrop_id)
            preserved = (
                "_id",
                "link",
                "collection",
                "title",
                "tags",
                "important",
                "excerpt",
                "cover",
                "media",
                "type",
                "created",
                "order",
                "highlights",
                "reminder",
                "removed",
            )
            if not _same_note(after.get("note"), note) or any(
                before.get(key) != after.get(key) for key in preserved
            ):
                raise blocked("note_update_readback_mismatch")
            return proof

    def ensure_raindrop(self, pid, source_url, archive_ref, *, category=None, post_text=None):
        """Create at most once plus one reconciled recovery attempt, under one lock."""
        if self._repair_run is not None:
            return self._repair_run.ensure_raindrop(
                pid, source_url, archive_ref, category=category, post_text=post_text
            )
        return self._ensure_raindrop(
            pid, source_url, archive_ref, category=category, post_text=post_text
        )

    def _ensure_raindrop(
        self,
        pid,
        source_url,
        archive_ref,
        *,
        category=None,
        post_text=None,
        run=None,
        title="Archived X post",
        collection_id=-1,
    ):
        exact_id(pid)
        if (
            not isinstance(title, str)
            or not title.strip()
            or len(title) > 200
            or type(collection_id) is not int
            or not (collection_id == -1 or collection_id > 0)
        ):
            raise blocked("invalid_create_metadata")
        from .source import source_url as canonical_url

        if source_url != canonical_url(pid):
            raise blocked("canonical_saved_primary_url_required")
        if isinstance(archive_ref, dict):
            if set(archive_ref) != {"manifest", "payload"}:
                raise blocked("invalid_archive_reference")
            archive_link = drive_link(archive_ref["payload"])
        else:
            archive_link = archive_ref
        if (
            not isinstance(archive_link, str)
            or not 1 <= len(archive_link) <= 500
            or category is not None
            and (not isinstance(category, str) or len(category) > 200)
        ):
            raise blocked("invalid_concise_archive_reference")
        try:
            archive_url = urlsplit(archive_link)
            if (
                archive_url.scheme != "https"
                or not archive_url.netloc
                or any(c.isspace() for c in archive_link)
            ):
                raise ValueError
        except ValueError:
            raise blocked("archive_reference_must_be_https_url") from None
        base = f"raindrop-{pid}"
        with self.store.locked() if run is None else nullcontext():
            if run is None:
                snapshot = self._enumerate()
                match = inspect_inventory(snapshot, pid)
                evidence = {
                    "coverage": snapshot["coverage"],
                    "inventory_sha256": digest(encode(snapshot)),
                }
            else:
                run.check()
                match = run.match(pid)
                evidence = run.evidence
            if match["state"] not in ("active", "absent"):
                raise blocked("dedupe_" + match["state"])
            if run is not None and pid in run.verify_created_ids and match["state"] != "active":
                raise blocked("created_item_missing")
            old = self.store.read(base + ".receipt.private.json")
            if old:
                validate_receipt(old, pid)
                # Never silently recreate a previously adopted/created item removed
                # by its owner, even when Trash has since been emptied.
                if match["raindrop_ids"] != [old["raindrop_id"]]:
                    raise blocked("previous_receipt_conflicts")
            disposition = "adopted"
            verify_created_metadata = False
            if match["state"] == "active":
                rid = match["raindrop_ids"][0]
                explicit_check = run is not None and pid in run.verify_created_ids
                if not old or explicit_check:
                    created = self.store.read(base + ".create-result.private.json")
                    if explicit_check and created is None:
                        raise blocked("create_result_required")
                    if created is not None:
                        try:
                            created_rid, _ = item_identity(created["response"]["item"])
                        except (KeyError, TypeError):
                            raise blocked("create_result_conflict") from None
                        if created_rid != rid:
                            raise blocked("create_result_conflict")
                        if not isinstance(post_text, str) or not post_text.strip():
                            raise blocked("primary_post_text_required")
                        note = post_text[:250]
                        intent = self.store.read(base + ".intent.private.json")
                        _validate_create_intent(intent, pid, source_url, archive_ref, note)
                        if created.get("schema_version") != 1 or created.get(
                            "intent_sha256"
                        ) != digest(encode(intent)):
                            raise blocked("create_result_conflict")
                        verify_created_metadata = True
            else:
                if not isinstance(post_text, str) or not post_text.strip():
                    raise blocked("primary_post_text_required")
                note = post_text[:250]
                intent_name = base + ".intent.private.json"
                intent = self.store.read(intent_name)
                if intent:
                    # A bounded migration never retries an uncertain POST, even
                    # through a later default-path caller with a fresh inventory.
                    if run is not None or intent.get("no_repost") is True:
                        raise blocked("uncertain_create_requires_reconciliation")
                    _validate_create_intent(intent, pid, source_url, archive_ref, note)
                    if intent["attempts"] >= 2:
                        raise blocked("recovery_attempt_exhausted")
                    # A saved successful response whose object disappeared is not
                    # an uncertain POST; do not recreate it after an owner action.
                    if self.store.read(base + ".create-result.private.json"):
                        raise blocked("created_item_missing")
                else:
                    intent = {
                        "schema_version": 1,
                        "source_post_id": pid,
                        "source_post_url": source_url,
                        "archive_ref": archive_ref,
                        "note": note,
                        "attempts": 0,
                        "created_at": now(),
                    }
                intent = {
                    **intent,
                    "attempts": intent["attempts"] + 1,
                    "last_attempt_at": now(),
                    **evidence,
                    **({"no_repost": True} if run is not None else {}),
                }
                if run is not None:
                    run.check()
                # Atomic replace + file/directory fsync BEFORE every possible POST.
                self.store.write(intent_name, intent)
                payload = {
                    "link": source_url,
                    "collection": {"$id": collection_id},
                    "title": title,
                    "tags": ["to-read"],
                    "note": note,
                }
                if run is not None:
                    run.check()
                    run.stats["post_attempts"] += 1
                response = self._call("add-json", payload=payload)
                self.store.write(
                    base + ".create-result.private.json",
                    {
                        "schema_version": 1,
                        "intent_sha256": digest(encode(intent)),
                        "response": response,
                        "received_at": now(),
                    },
                )
                rid, _ = item_identity(response.get("item"))
                disposition = "created"
                verify_created_metadata = True
            if run is not None:
                run.stats["get_attempts"] += 1
            proof, verified_item = self._verify_item(pid, rid)
            if verify_created_metadata:
                actual_note = verified_item.get("note")
                mismatch = {
                    "note": not _same_note(actual_note, note),
                    "title": verified_item.get("title") != title,
                    **(
                        {"collection": proof["collection_id"] != collection_id}
                        if collection_id != -1
                        else {}
                    ),
                    "tags": verified_item.get("tags") != ["to-read"],
                }
                if any(mismatch.values()):
                    error = blocked("create_metadata_readback_mismatch")
                    error.summary["metadata_mismatch"] = {
                        **mismatch,
                        "note_actual_length": len(actual_note)
                        if isinstance(actual_note, str)
                        else None,
                        "note_expected_length": len(note),
                        "note_equal_after_strip": _same_note(actual_note, note),
                    }
                    raise error
            if run is not None:
                run.check()
                run.record(pid, verified_item)
            receipt = {
                **proof,
                "archive_ref": archive_ref,
                "disposition": disposition,
                **evidence,
            }
            self.store.write(base + ".receipt.private.json", receipt)
            return receipt


def _same_note(actual, expected):
    """Provider edge trimming is harmless; internal whitespace is content."""
    return isinstance(actual, str) and actual.strip() == expected.strip()


def _validate_create_intent(intent, pid, source_url, archive_ref, note):
    if (
        not isinstance(intent, dict)
        or intent.get("schema_version") != 1
        or intent.get("source_post_id") != pid
        or intent.get("source_post_url") != source_url
        or intent.get("archive_ref") != archive_ref
        or intent.get("note") != note
        or type(intent.get("attempts")) is not int
        or not 1 <= intent["attempts"] <= 2
    ):
        raise blocked("intent_conflict")


def _selected_ids(values):
    if isinstance(values, (str, bytes)):
        raise blocked("invalid_inventory_selection")
    selected = set()
    for value in values:
        pid = exact_id(value)
        if pid in selected or len(selected) >= 100:
            raise blocked("invalid_inventory_selection")
        selected.add(pid)
    if not selected:
        raise blocked("invalid_inventory_selection")
    return frozenset(selected)


class _InventoryRun:
    """Ephemeral index, deliberately not a provider inventory/schema."""

    def __init__(self, adapter, baseline, max_operations, deadline, selection):
        self.adapter = adapter
        self.remaining = max_operations
        self.deadline = deadline
        self.closed = False
        self.selection = selection
        self.verify_created_ids = frozenset()
        self.stats = {
            "inventory_operations": 1,
            "baseline_pages_per_pass": {
                state: baseline[state]["pages"] for state in ("active", "trash")
            },
            "baseline_stable_passes": baseline["coverage"]["stable_passes"],
            "get_attempts": 0,
            "post_attempts": 0,
        }
        self.evidence = {
            "baseline_coverage": dict(baseline["coverage"]),
            "baseline_inventory_sha256": digest(encode(baseline)),
        }
        self.index = {}
        for state in ("active", "trash"):
            for item in baseline[state]["items"]:
                pid = raindrop_post_id(item["link"])
                if pid is not None:
                    self.index.setdefault(pid, {"active": [], "trash": []})[state].append(
                        item["_id"]
                    )

    def check(self):
        if self.closed:
            raise blocked("inventory_run_closed")
        if time.monotonic() >= self.deadline:
            raise blocked("inventory_baseline_expired", retryable=True)

    def match(self, pid):
        matches = self.index.get(pid, {"active": [], "trash": []})
        active, trash = matches["active"], matches["trash"]
        state = (
            "conflict"
            if len(active) > 1 or (active and trash)
            else ("trash" if trash else "active" if active else "absent")
        )
        return {"state": state, "raindrop_ids": active + trash}

    def record(self, pid, item):
        # Only the authenticated, identity-checked GET enters this overlay.
        self.index[pid] = {"active": [item["_id"]], "trash": []}

    def ensure_raindrop(
        self,
        pid,
        source_url,
        archive_ref,
        *,
        category=None,
        post_text=None,
        title="Archived X post",
        collection_id=-1,
    ):
        try:
            self.check()
            if self.selection is not None and pid not in self.selection:
                raise blocked("post_outside_inventory_selection")
            if self.remaining <= 0:
                raise blocked("inventory_operation_bound_exhausted")
            self.remaining -= 1
            return self.adapter._ensure_raindrop(
                pid,
                source_url,
                archive_ref,
                category=category,
                post_text=post_text,
                run=self,
                title=title,
                collection_id=collection_id,
            )
        except BaseException:
            # Even a caller catching a transport/crash error must start fresh.
            self.closed = True
            raise
