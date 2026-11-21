"""One adapter/ledger for prospective migration and retrospective backfill.

All authentication and HTTP go through openclaw-config's existing Raindrop skill.
Saved inventories/receipts are historical evidence, never deletion authorization.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

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

    def verify_raindrop_now(self, pid, raindrop_id):
        """Always fresh authenticated single GET. No receipt/cache fallback."""
        exact_id(pid)
        if type(raindrop_id) is not int or raindrop_id <= 0:
            raise blocked("invalid_raindrop_id")
        body = self._call("get", raindrop_id)
        if body.get("result") is not True:
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
        }

    def replace_note(self, pid, raindrop_id, post_text):
        """Explicit note-only edit, with fresh identity and metadata readback checks."""
        exact_id(pid)
        if not isinstance(post_text, str) or not post_text.strip():
            raise blocked("primary_post_text_required")
        if type(raindrop_id) is not int or raindrop_id <= 0:
            raise blocked("invalid_raindrop_id")
        with self.store.locked():
            before = self._call("get", raindrop_id)["item"]
            rid, cid = item_identity(before)
            if (
                rid != raindrop_id
                or (cid != -1 and cid <= 0)
                or before.get("removed", False) is not False
                or raindrop_post_id(before["link"]) != pid
            ):
                raise blocked("inactive_or_wrong_raindrop")
            note = post_text[:250]
            self._call("update-note-json", rid, payload={"note": note})
            after = self._call("get", rid)["item"]
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
            if after.get("note") != note or any(
                before.get(key) != after.get(key) for key in preserved
            ):
                raise blocked("note_update_readback_mismatch")
            return self.verify_raindrop_now(pid, rid)

    def ensure_raindrop(self, pid, source_url, archive_ref, *, category=None, post_text=None):
        """Create at most once plus one reconciled recovery attempt, under one lock."""
        exact_id(pid)
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
        with self.store.locked():
            snapshot = self._enumerate()
            match = inspect_inventory(snapshot, pid)
            if match["state"] not in ("active", "absent"):
                raise blocked("dedupe_" + match["state"])
            old = self.store.read(base + ".receipt.private.json")
            if old:
                validate_receipt(old, pid)
                # Never silently recreate a previously adopted/created item removed
                # by its owner, even when Trash has since been emptied.
                if match["raindrop_ids"] != [old["raindrop_id"]]:
                    raise blocked("previous_receipt_conflicts")
            disposition = "adopted"
            if match["state"] == "active":
                rid = match["raindrop_ids"][0]
            else:
                if not isinstance(post_text, str) or not post_text.strip():
                    raise blocked("primary_post_text_required")
                note = post_text[:250]
                intent_name = base + ".intent.private.json"
                intent = self.store.read(intent_name)
                if intent:
                    if (
                        intent.get("schema_version") != 1
                        or intent.get("source_post_id") != pid
                        or intent.get("source_post_url") != source_url
                        or intent.get("archive_ref") != archive_ref
                        or intent.get("note") != note
                        or type(intent.get("attempts")) is not int
                        or not 1 <= intent["attempts"] <= 2
                    ):
                        raise blocked("intent_conflict")
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
                    "coverage": snapshot["coverage"],
                    "inventory_sha256": digest(encode(snapshot)),
                }
                # Atomic replace + file/directory fsync BEFORE every possible POST.
                self.store.write(intent_name, intent)
                payload = {
                    "link": source_url,
                    "collection": {"$id": -1},
                    "title": "Archived X post",
                    "tags": ["to-read"],
                    "note": note,
                }
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
            proof = self.verify_raindrop_now(pid, rid)
            receipt = {
                **proof,
                "archive_ref": archive_ref,
                "disposition": disposition,
                "coverage": snapshot["coverage"],
                "inventory_sha256": digest(encode(snapshot)),
            }
            self.store.write(base + ".receipt.private.json", receipt)
            return receipt
