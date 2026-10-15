"""Bounded, read-only inventory. Checkpoints are observations, never deletion receipts."""

import copy
import json
import os
import secrets
from datetime import UTC, datetime

import httpx

from .core import PilotError, exact_id, post_fields

LIMITATIONS = [
    "API-visible observations only; no claim of all-history coverage or an atomic snapshot.",
    "X may cap or shift pagination without signaling it; source mutation requires a fresh pass.",
    "Folder responses without pagination metadata have unverified coverage.",
    "Post created_at is not bookmark time; bookmark time is unknown.",
    "Inventory is not an archive; reconcile categories before any future deletion.",
    "Folder entitlement, pagination behavior, and actual charges require live verification.",
]


def now():
    return datetime.now(UTC).isoformat()


def stream(kind, folder_id=None):
    return dict(
        kind=kind, folder_id=folder_id, next_token=None, pages=[], status="pending", reason=None
    )


def validate_page(body, kind, page_size):
    """Validate before changing checkpoint; retain partial positives, never negative claims."""
    if not isinstance(body, dict):
        raise PilotError("Malformed inventory response.", code="schema_error")
    meta = body.get("meta")
    errors = body.get("errors", [])
    if (meta is not None and not isinstance(meta, dict)) or not isinstance(errors, list):
        raise PilotError("Malformed inventory metadata/errors.", code="schema_error")
    data = body.get("data", [])
    if "data" not in body and not errors and (meta or {}).get("result_count") != 0:
        raise PilotError("Missing inventory data/empty-page evidence.", code="schema_error")
    if not isinstance(data, list) or len(data) > page_size:
        raise PilotError("Unexpected inventory page size/shape.", code="schema_error")
    token = (meta or {}).get("next_token")
    if token is not None and (not isinstance(token, str) or not token):
        raise PilotError("Malformed continuation token.", code="schema_error")
    if "result_count" in (meta or {}) and (
        type(meta["result_count"]) is not int or meta["result_count"] != len(data)
    ):
        raise PilotError("Inventory result count mismatch.", code="schema_error")
    includes = body.get("includes", {})
    if not isinstance(includes, dict) or any(
        not isinstance(v, list) or any(not isinstance(i, dict) for i in v)
        for v in includes.values()
    ):
        raise PilotError("Malformed inventory expansions.", code="schema_error")
    for item in data:
        if not isinstance(item, dict):
            raise PilotError("Malformed inventory item.", code="schema_error")
        exact_id(item.get("id"))
        if kind == "folders" and not isinstance(item.get("name"), str):
            raise PilotError("Missing original folder name.", code="schema_error")
        if kind in ("main", "hydrate") and not isinstance(item.get("text"), str):
            raise PilotError("Missing post text.", code="schema_error")
        if "author_id" in item:
            exact_id(item["author_id"])
    return data, token, errors, meta is not None


class Inventory:
    """One capped pass. Caller holds Store.locked across reads, refresh, and exports."""

    def __init__(
        self,
        store,
        owner_id,
        dialect="post",
        *,
        resume=False,
        source_unchanged=False,
        retry_unavailable=False,
        page_size=100,
        max_records=10000,
        max_folders=1000,
        max_pages=100,
    ):
        self.store = store
        exact_id(owner_id)
        post_fields(dialect)
        if not 5 <= page_size <= 100:
            raise PilotError("Page size must be between 5 and 100.")
        if not (1 <= max_records <= 100000 and 1 <= max_folders <= 1000 and 1 <= max_pages <= 1000):
            raise PilotError("Inventory bounds exceed supported limits.")
        active = store.read("inventory-active.private.json")
        if resume:
            if retry_unavailable:
                raise PilotError("Retry unavailable sources in a fresh pass, without --resume.")
            if not source_unchanged:
                raise PilotError(
                    "Resume requires --source-unchanged-confirmed; otherwise start fresh."
                )
            if not active:
                raise PilotError("No inventory to resume.")
            # Never use a path loaded from state as an arbitrary filename.
            self.name = self.checkpoint_name(active["pass_id"])
            self.state = store.read(self.name)
            if not self.state or self.state.get("schema_version") != 1:
                raise PilotError("Unsupported or missing inventory checkpoint.")
            if (self.state["owner_id"], self.state["dialect"]) != (owner_id, dialect):
                raise PilotError("Inventory owner or field dialect changed; start a fresh pass.")
            if self.state["in_flight"]:
                self.state["consistent"] = False
                self.state["continuity_notes"].append(
                    "interrupted_request_replayed_on_explicit_resume"
                )
            self.state["in_flight"] = False
            # Resume may retry HTTP failures; partial/schema/loop states need fresh scans.
            for item in self.state["streams"].values():
                if item["status"] == "paused":
                    item.update(status="pending", reason=None)
        else:
            unavailable_cache = {}
            if active and not retry_unavailable:
                previous = store.read(self.checkpoint_name(active["pass_id"]))
                if previous and (previous["owner_id"], previous["dialect"]) == (owner_id, dialect):
                    unavailable_cache = dict(
                        list(previous.get("unavailable_cache", {}).items())[-max_records:]
                    )
            pass_id = secrets.token_hex(12)
            self.name = self.checkpoint_name(pass_id)
            self.state = dict(
                schema_version=1,
                pass_id=pass_id,
                owner_id=owner_id,
                dialect=dialect,
                started_at=now(),
                updated_at=now(),
                consistent=True,
                continuity_notes=[],
                in_flight=False,
                stop_reason=None,
                limits=dict(
                    page_size=page_size,
                    max_records=max_records,
                    max_folders=max_folders,
                    max_pages=max_pages,
                ),
                streams={"main": stream("main"), "folders": stream("folders")},
                folders={},
                posts={},
                hydration_batches=[],
                unavailable_cache=unavailable_cache,
            )
            self.save()
            store.write("inventory-active.private.json", {"pass_id": pass_id})

    @classmethod
    def load(cls, store):
        active = store.read("inventory-active.private.json")
        if not active:
            raise PilotError("No inventory to export.")
        result = cls.__new__(cls)
        result.store = store
        result.name = cls.checkpoint_name(active["pass_id"])
        result.state = store.read(result.name)
        if not result.state or result.state.get("schema_version") != 1:
            raise PilotError("Unsupported or missing inventory checkpoint.")
        return result

    @staticmethod
    def checkpoint_name(pass_id):
        if (
            not isinstance(pass_id, str)
            or len(pass_id) != 24
            or any(c not in "0123456789abcdef" for c in pass_id)
        ):
            raise PilotError("Malformed inventory pass ID.")
        return f"inventory-{pass_id}.private.json"

    def save(self):
        self.state["updated_at"] = now()
        self.store.write(self.name, self.state)

    def add_post(self, post_id):
        posts = self.state["posts"]
        if post_id not in posts:
            posts[post_id] = dict(
                id=post_id,
                canonical_url=f"https://x.com/i/status/{post_id}",
                seen_in_main=False,
                folder_ids=[],
                first_observed_at=now(),
                bookmark_time=None,
                hydration_status="pending",
                post=None,
                includes={},
                source_evidence=[],
                errors=[],
            )
            if cached := self.state.get("unavailable_cache", {}).get(post_id):
                posts[post_id].update(
                    copy.deepcopy(cached), hydration_status="blocked_unavailable_source"
                )
        return posts[post_id]

    def accept_page(self, key, body, evidence):
        state = self.state
        item = state["streams"][key]
        data, next_token, errors, has_meta = validate_page(
            body, item["kind"], state["limits"]["page_size"]
        )
        ids = [entry["id"] for entry in data]
        target = state["folders"] if key == "folders" else state["posts"]
        limit = state["limits"]["max_folders" if key == "folders" else "max_records"]
        if len(target.keys() | set(ids)) > limit:
            item.update(
                status="blocked",
                reason="record_limit",
                rejected_page={
                    "raw_count": len(data),
                    "request_token": item["next_token"],
                    "evidence": evidence,
                },
            )
            return  # raw response retained; cursor does not advance past an uncommitted page
        seen_tokens = {p["request_token"] for p in item["pages"]}
        seen_tokens.add(item["next_token"])
        reason = None
        if errors:
            reason = "partial_errors"
        elif next_token is not None and next_token in seen_tokens:
            reason = "pagination_cycle"
        elif not has_meta:
            reason = "pagination_unverified"
        prior_ids = {pid for page in item["pages"] for pid in page["ids"]}
        duplicate_count = len(ids) - len(set(ids)) + len(set(ids) & prior_ids)
        if duplicate_count:
            state["consistent"] = False
        for entry in data:
            post_id = entry["id"]
            if key == "folders":
                folder = state["folders"].setdefault(post_id, dict(id=post_id, names=[]))
                if entry["name"] not in folder["names"]:
                    if folder["names"]:
                        state["consistent"] = False
                    folder["names"].append(entry["name"])
                state["streams"].setdefault("folder:" + post_id, stream("folder", post_id))
            else:
                post = self.add_post(post_id)
                if key == "main":
                    post.update(
                        seen_in_main=True,
                        post=entry,
                        includes=body.get("includes", {}),
                        hydration_status="partial" if errors else "read",
                    )
                    state.get("unavailable_cache", {}).pop(post_id, None)
                    post.pop("unavailable_observed_at", None)
                    post["errors"] = errors
                    post["source_evidence"].append(evidence)
                elif item["folder_id"] not in post["folder_ids"]:
                    post["folder_ids"].append(item["folder_id"])
        item["pages"].append(
            dict(
                request_token=item["next_token"],
                next_token=next_token,
                raw_count=len(data),
                duplicate_count=duplicate_count,
                ids=ids,
                errors=errors,
                evidence=evidence,
                observed_at=now(),
            )
        )
        item.update(
            next_token=next_token,
            status="blocked" if reason else ("pending" if next_token else "exhausted"),
            reason=reason or (None if next_token else "api_end"),
        )

    def all_folders_exhausted(self):
        return (
            self.state["consistent"]
            and not self.state["in_flight"]
            and all(
                item["status"] == "exhausted"
                for item in self.state["streams"].values()
                if item["kind"] != "main"
            )
        )

    def membership(self, post):
        if post["folder_ids"]:
            return "filed"
        return "confirmed_unfiled" if self.all_folders_exhausted() else "membership_unknown"

    def summary(self):
        state = self.state
        posts = state["posts"].values()
        categories = {name: 0 for name in ("filed", "confirmed_unfiled", "membership_unknown")}
        hydration = {}
        for post in posts:
            categories[self.membership(post)] += 1
            status = post["hydration_status"]
            hydration[status] = hydration.get(status, 0) + 1
        complete = all(s["status"] == "exhausted" for s in state["streams"].values())
        return dict(
            schema_version=1,
            pass_id=state["pass_id"],
            records=len(state["posts"]),
            folders=len(state["folders"]),
            membership_counts=categories,
            hydration_counts=hydration,
            streams=[
                dict(
                    kind=s["kind"],
                    pages=len(s["pages"]),
                    status=s["status"],
                    reason=s["reason"],
                    has_next_page=bool(s["next_token"]),
                    raw_count=sum(p["raw_count"] for p in s["pages"]),
                    unique_count=len({i for p in s["pages"] for i in p["ids"]}),
                )
                for s in state["streams"].values()
            ],
            all_folders_exhausted_in_pass=self.all_folders_exhausted(),
            inventory_status="observed_end"
            if complete
            and state["consistent"]
            and not state["in_flight"]
            and state["stop_reason"] is None
            and all(p["hydration_status"] in ("read", "blocked_unavailable_source") for p in posts)
            else "partial",
            stop_reason=state["stop_reason"],
            consistent_observations=state["consistent"],
            continuity_notes=state["continuity_notes"],
            observed_charge="unverified",
            all_history_complete=False,
            deletion_eligible=False,
            started_at=state["started_at"],
            updated_at=state["updated_at"],
            limitations=LIMITATIONS,
        )

    def export(self):
        """Create-only self-contained JSONL. Exporting needs no credentials or network."""
        name = f"inventory-{self.state['pass_id']}-{secrets.token_hex(6)}.private.jsonl"
        path = self.store.root / name
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:

                def emit(row):
                    output.write(json.dumps(row, ensure_ascii=False) + "\n")

                emit({"type": "summary", **self.summary(), "owner_id": self.state["owner_id"]})
                for folder in self.state["folders"].values():
                    emit({"type": "folder", **folder})
                for key, item in self.state["streams"].items():
                    emit(
                        {
                            "type": "stream",
                            "key": key,
                            **item,
                            "raw_count": sum(p["raw_count"] for p in item["pages"]),
                            "unique_count": len({i for p in item["pages"] for i in p["ids"]}),
                        }
                    )
                for batch in self.state["hydration_batches"]:
                    emit({"type": "hydration_batch", **batch})
                for post in self.state["posts"].values():
                    emit(
                        {
                            "type": "post",
                            **post,
                            "membership_status": self.membership(post),
                            "category_evidence_complete": self.all_folders_exhausted(),
                            "folders": [self.state["folders"][i] for i in post["folder_ids"]],
                            "deletion_eligible": False,
                            "requires_category_reconciliation": True,
                        }
                    )
                output.flush()
                os.fsync(output.fileno())
            directory = os.open(self.store.root, os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return name

    def run(self, client, budget, *, max_calls=10, hydrate_batch_size=100):
        if not 1 <= max_calls <= 10000 or not 1 <= hydrate_batch_size <= 100:
            raise PilotError("Invalid request or hydration batch limit.")
        calls = 0
        reservations = {}
        state = self.state
        state["stop_reason"] = None

        def get(path, params):
            nonlocal calls
            if calls >= max_calls:
                raise PilotError("Request limit reached.", code="request_limit")
            # Persist before network: a crash here cannot masquerade as a consistent pass.
            state["in_flight"] = True
            self.save()
            calls += 1
            operation = (
                "identity"
                if path.endswith("/me")
                else "hydrate"
                if path == "/2/tweets"
                else "folders"
                if path.endswith("/folders")
                else "folder"
                if "/folders/" in path
                else "main"
            )

            def reserve():
                budget.reserve()
                reservations[operation] = reservations.get(operation, 0) + 1

            return client.get(path, params, reserve)

        active = None
        try:
            me = get("/2/users/me", {})
            if (
                not isinstance(me.get("data"), dict)
                or me.get("errors")
                or (exact_id(me["data"].get("id")) != state["owner_id"])
            ):
                raise PilotError(
                    "Authenticated user does not match inventory owner.", code="identity"
                )
            state["in_flight"] = False
            self.save()
            # Folder list first: discovered folders remain independently runnable if main fails.
            while True:
                keys = ["folders", "main"] + [
                    k for k in state["streams"] if k.startswith("folder:")
                ]
                active = next((k for k in keys if state["streams"][k]["status"] == "pending"), None)
                if active is None:
                    break
                item = state["streams"][active]
                if len(item["pages"]) >= state["limits"]["max_pages"]:
                    item.update(status="blocked", reason="page_limit")
                    self.save()
                    continue
                path = f"/2/users/{state['owner_id']}/bookmarks"
                params = {"max_results": str(state["limits"]["page_size"])}
                if active == "main":
                    params.update(post_fields(state["dialect"]))
                else:
                    path += "/folders"
                    if item["folder_id"]:
                        path += "/" + item["folder_id"]
                if item["next_token"]:
                    params["pagination_token"] = item["next_token"]
                try:
                    body = get(path, params)
                    self.accept_page(active, body, client.observations[-1]["private_file"])
                except PilotError as exc:
                    if exc.code in ("http_403", "http_404", "schema_error"):
                        item.update(status="blocked", reason=exc.code)
                    else:
                        raise
                state["in_flight"] = False
                self.save()
            active = None
            while pending := [
                p["id"] for p in state["posts"].values() if p["hydration_status"] == "pending"
            ][:hydrate_batch_size]:
                body = get("/2/tweets", {"ids": ",".join(pending), **post_fields(state["dialect"])})
                self.accept_hydration(pending, body, client.observations[-1]["private_file"])
                state["in_flight"] = False
                self.save()
        except (PilotError, httpx.HTTPError) as exc:
            reason = exc.code if isinstance(exc, PilotError) else "transport_error"
            state["stop_reason"] = reason
            if active:
                state["streams"][active].update(status="paused", reason=reason)
            # Transport ambiguity means resumed pages cannot justify negative category evidence.
            if reason == "transport_error":
                state["consistent"] = False
            state["in_flight"] = False
            self.save()
        return {
            **self.summary(),
            "get_attempts_this_run": calls,
            "reservations_by_operation": {
                op: {"requests": count, "estimated_reserved_usd": str(budget.per_call * count)}
                for op, count in reservations.items()
            },
            "estimated_reserved_usd": str(budget.used),
            "observed_charge": "unverified",
            "private_export": self.export(),
            "private_checkpoint": self.name,
        }

    def accept_hydration(self, requested, body, evidence):
        data, _, errors, _ = validate_page(body, "hydrate", len(requested))
        if len({p["id"] for p in data}) != len(data) or any(p["id"] not in requested for p in data):
            raise PilotError("Lookup returned unexpected/duplicate IDs.", code="schema_error")
        returned = {p["id"]: p for p in data}
        known_types = {
            f"https://{host}/2/problems/{kind}"
            for host in ("api.x.com", "api.twitter.com")
            for kind in ("resource-not-found", "not-authorized-for-resource")
        }
        matched = {}
        unrelated = []
        for error in errors:
            error_id = (
                error.get("resource_id", error.get("value")) if isinstance(error, dict) else None
            )
            if (
                isinstance(error_id, str)
                and error_id in requested
                and error.get("resource_type") in ("tweet", "post")
                and error.get("type") in known_types
                and error_id not in returned
            ):
                matched.setdefault(error_id, []).append(error)
            else:
                unrelated.append(error)
        for post_id in requested:
            post = self.state["posts"][post_id]
            post["source_evidence"].append(evidence)
            post["errors"] = copy.deepcopy(matched.get(post_id, []) + unrelated)
            if post_id in returned:
                post.update(
                    post=returned[post_id],
                    includes=body.get("includes", {}),
                    hydration_status="partial" if unrelated else "read",
                )
            else:
                post["hydration_status"] = (
                    "blocked_unavailable_source" if post_id in matched else "unresolved"
                )
            cache = self.state.setdefault("unavailable_cache", {})
            if post["hydration_status"] == "blocked_unavailable_source":
                post["unavailable_observed_at"] = now()
                if post_id in cache or len(cache) < self.state["limits"]["max_records"]:
                    cache[post_id] = {
                        k: copy.deepcopy(post[k])
                        for k in ("errors", "source_evidence", "unavailable_observed_at")
                    }
        self.state["hydration_batches"].append(
            dict(
                ids=requested,
                returned_ids=list(returned),
                errors=errors,
                evidence=evidence,
                observed_at=now(),
            )
        )
