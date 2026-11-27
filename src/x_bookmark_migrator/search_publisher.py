"""Publish verified catalog text to a dedicated Drive folder."""

import json
from pathlib import Path
from time import monotonic

import httpx

from .catalog import (
    BATCH_INPUTS,
    build,
    catalog_relative_url,
    checked_bytes,
    private_bytes,
    verify_publication,
)
from .core import PilotError, exact_id
from .drive import FOLDER, UPLOAD, Drive, DriveHTTPError, checked, digest, drive_id, encode
from .publication import publication_bytes
from .search_projection import RENDERER_VERSION, drive_text

STATE = "drive-search-text.private.json"
MIME = "text/plain"
SIMPLE_UPLOAD_LIMIT = 5_000_000
SEARCH_RETRY_SECONDS = 86400


def require(condition, reason):
    if not condition:
        raise PilotError(reason)


def check_owner(drive, file_id, owner):
    page = checked(
        drive.request(
            "GET",
            f"/files/{file_id}/permissions",
            params={
                "fields": "nextPageToken,permissions(id,type,role)",
                "pageSize": 100,
            },
        )
    ).json()
    require(
        not page.get("nextPageToken")
        and page.get("permissions") == [{"id": owner, "type": "user", "role": "owner"}],
        "Search destination requires private, sole-owner access.",
    )


def children(drive, parent):
    params = {
        "q": f"'{parent}' in parents",
        "fields": "nextPageToken,incompleteSearch,files(id)",
        "pageSize": 1000,
    }
    found, tokens = set(), set()
    while True:
        page = checked(drive.request("GET", "/files", params=params)).json()
        require(
            page.get("incompleteSearch") is False and isinstance(page.get("files"), list),
            "Incomplete search folder inventory.",
        )
        for item in page["files"]:
            file_id = drive_id(item["id"])
            require(file_id not in found, "Duplicate search folder identity.")
            found.add(file_id)
        token = page.get("nextPageToken")
        if not token:
            return found
        require(isinstance(token, str) and token not in tokens, "Repeated search folder page.")
        tokens.add(token)
        params["pageToken"] = token


def read_target(drive, row, parent, token, owner):
    file_id = row["file_id"]
    metadata = drive.metadata(file_id, missing_ok=True)
    if metadata is None:
        require(row["verified"] is None, "Previously verified search file is inaccessible.")
        return None
    require(
        metadata.get("parents") == [parent]
        and metadata.get("mimeType") == MIME
        and metadata.get("appProperties") == {"xm_key": token}
        and metadata.get("ownedByMe") is True
        and metadata.get("capabilities", {}).get("canEdit") is True,
        "Search file identity or access changed.",
    )
    check_owner(drive, file_id, owner)
    return digest(drive.download(file_id))


def publish_search(
    store, catalog, batches, *, expected_records, catalog_sha256, root, parent, http
):
    """Caller supplies reviewed inputs and transport. Serialize with archive and catalog writers."""
    with store.locked():
        return _publish_under_state_lock(
            store,
            catalog,
            batches,
            expected_records=expected_records,
            catalog_sha256=catalog_sha256,
            root=root,
            parent=parent,
            http=http,
        )


def _worker_batches(worker, lines):
    catalog, store = worker.catalog, worker.store
    # Older workers already pinned ordinary receipts and restored each under its hash.
    # Recorded writer inputs also cover manual full builds and promoted repair batches.
    manual = catalog.read(BATCH_INPUTS) or {}
    adopted = {
        sha: (worker.restore_root / sha, store.path(name))
        for name, sha in worker.state["batches"].items()
    }
    inputs = {**adopted, **manual}
    # A manual build and a worker can publish the same batch from different restores.
    # Select the directory used for published media. build() verifies all its actual bytes.
    worker_urls = {
        sha: catalog_relative_url(catalog, adopted[sha][0])
        for sha in adopted.keys() & manual.keys()
    }
    if worker_urls:
        for line in lines:
            for version in json.loads(line)["versions"]:
                sha = version["manifest"]["sha256"]
                if sha in worker_urls and any(
                    asset.get("local_url", "").rpartition("/")[0] == worker_urls[sha]
                    for asset in version["media"]
                ):
                    inputs[sha] = adopted[sha]
    primaries = set()
    for sha, (directory, path) in inputs.items():
        receipt = json.loads(private_bytes(path))
        manifest = receipt["manifest"]
        require(manifest["sha256"] == sha, "Search batch receipt changed.")
        rows = checked_bytes(Path(directory) / "manifest.jsonl", manifest).splitlines()
        require(len(rows) == receipt["records"], "Search batch population differs from receipt.")
        primaries.update(exact_id(json.loads(row)["post_id"]) for row in rows)
    return inputs.values(), len(primaries)


def _record_search(worker, outcome):
    # The checkpoint survives the next migration report. Preserve the migration's own outcome.
    worker.state["drive_search"] = outcome
    worker.save()
    report = worker.store.read("worker-report.private.json") or {}
    report["drive_search"] = outcome
    worker.store.write("worker-report.private.json", report)
    return outcome


def publish_current(worker, parent):
    """Called after migration while the normal live worker still owns the state lock."""
    store, catalog, drive = worker.store, worker.catalog, worker.drive
    started = monotonic()
    with catalog.locked():
        state = store.read(STATE)
        path = catalog.path("catalog.jsonl")
        if not path.exists() and state is None and not worker.state["batches"]:
            return None
        raw = publication_bytes(path)
        generation = digest(raw)
        pin = store.read("drive-destination.private.json")
        # A destination or renderer change can make the same catalog bytes new work.
        target = digest(encode([generation, RENDERER_VERSION, pin, drive.root, parent]))
        prior = worker.state.get("drive_search", {})
        if (
            state
            and pin
            and pin.get("root") == drive.root
            and state["destination"] == {**pin, "parent": parent}
            and state["renderer"] == RENDERER_VERSION
            and state["complete"] == generation
        ):
            if prior.get("status") == "pending":
                return _record_search(
                    worker,
                    {
                        "status": "verified",
                        "catalog_sha256": generation,
                        "renderer": RENDERER_VERSION,
                        "verified": len(state["rows"]),
                    },
                )
            return None

        if prior.get("target") == target and worker.clock() < prior.get("retry_at", 0):
            return None
        # Arm before hashing or dispatch. A killed process also leaves a bounded retry.
        _record_search(
            worker,
            {
                "status": "pending",
                "target": target,
                "catalog_sha256": generation,
                "retry_at": worker.clock() + SEARCH_RETRY_SECONDS,
            },
        )
        requests_before = drive.http.requests
        batches, population = _worker_batches(worker, raw.splitlines())
    # An annotation can land between locks. The publisher fences the pinned generation.
    published = _publish_under_state_lock(
        store,
        catalog,
        batches,
        expected_records=population,
        catalog_sha256=generation,
        root=drive.root,
        parent=parent,
        http=drive.http,
    )
    published.update(
        status="verified",
        elapsed_seconds=round(monotonic() - started, 3),
        drive_requests=drive.http.requests - requests_before,
    )
    return _record_search(worker, published)


def _publish_under_state_lock(
    store, catalog, batches, *, expected_records, catalog_sha256, root, parent, http
):
    require(not http.follow_redirects, "Search publication requires redirects disabled.")
    parent = drive_id(parent)
    require(store.root != catalog.root and root != parent, "Distinct search destinations required.")

    with catalog.locked():

        def current_generation():
            require(
                digest(publication_bytes(catalog.path("catalog.jsonl"))) == catalog_sha256,
                "Stale search generation.",
            )

        current_generation()
        rebuild_started = monotonic()
        records = build(batches, catalog)
        verify_publication(catalog, records, expected_records)
        rebuild_seconds = round(monotonic() - rebuild_started, 3)
        units = {exact_id(r["post_id"]): drive_text(r) for r in records}
        snapshots = {
            r["post_id"]: [
                [v["manifest"]["sha256"], v["payload_receipt"]["sha256"]] for v in r["versions"]
            ]
            for r in records
        }
        require(bool(units) and len(units) == expected_records, "Incomplete search corpus.")
        drive = Drive(store, http, root)
        pin = store.read("drive-destination.private.json")
        require(pin and pin.get("root") == root, "Previously pinned archive required.")
        owner = pin["owner_permission_id"]
        destination = {**pin, "parent": parent}
        state = store.read(STATE)
        if state is None and not store.path(STATE).exists():
            state = {
                "destination": destination,
                "renderer": RENDERER_VERSION,
                "rows": {},
                "snapshots": {},
                "complete": None,
            }
        require(isinstance(state, dict), "Invalid search publication state.")
        require(
            state["destination"] == destination and state["renderer"] <= RENDERER_VERSION,
            "Search destination or renderer changed incompatibly.",
        )
        state["complete"] = None
        store.write(STATE, state)
        require(
            all(
                0 < len(unit["body"].encode("utf-8")) <= SIMPLE_UPLOAD_LIMIT
                for unit in units.values()
            ),
            "Search unit exceeds simple-upload bound.",
        )
        drive.preflight()
        folder = drive.metadata(parent)
        require(
            folder.get("mimeType") == FOLDER
            and folder.get("parents") == [root]
            and folder.get("ownedByMe") is True
            and folder.get("capabilities", {}).get("canAddChildren") is True,
            "Expected dedicated search folder under the pinned archive.",
        )
        check_owner(drive, parent, owner)
        rows = state["rows"]
        require(rows.keys() <= units.keys(), "Missing previously published primary.")
        require(
            all(
                pid in snapshots and all(pair in snapshots[pid] for pair in prior)
                for pid, prior in state["snapshots"].items()
            ),
            "Missing prior archive snapshot.",
        )
        known = {row["file_id"] for row in rows.values() if row.get("file_id")}
        require(children(drive, parent) <= known, "Unknown search files; restore the mapping.")
        state.update(renderer=RENDERER_VERSION, snapshots=snapshots)
        store.write(STATE, state)
        for pid, unit in units.items():
            key = "search:text/plain:" + pid
            token = digest(encode([root, parent, key]))
            body = unit["body"].encode("utf-8")
            desired = unit["sha256"]
            if pid not in rows:
                require(
                    token not in drive.journal, "Restore missing search mapping before publication."
                )
                rows[pid] = {"file_id": None, "verified": None, "pending": None}
                store.write(STATE, state)
            row = rows[pid]
            if row["file_id"] is None:
                row["file_id"], _ = drive.allocate(parent, key)
                store.write(STATE, state)
            file_id = drive_id(row["file_id"])
            require(drive.journal.get(token) == file_id, "Search allocation journal differs.")
            observed = read_target(drive, row, parent, token, owner)
            # Recover an acknowledged-by-readback write even if newer local edits arrived.
            if row["pending"] is not None:
                if observed != row["pending"]:
                    require(observed == row["verified"], "Unexpected search file bytes.")
                    # Neither prior bytes nor a 404 proves a timed-out write cannot land later.
                    require(
                        row.get("dispatched") is False, "Uncertain search write is still pending."
                    )
                current_generation()
                row.update(verified=observed, pending=None, dispatched=False)
                store.write(STATE, state)
            require(
                observed == row["verified"],
                "Unexpected search file bytes; reconcile before writing.",
            )
            if observed != desired:
                current_generation()
                row.update(pending=desired, dispatched=False)
                store.write(STATE, state)

                def before_content(row=row):
                    current_generation()
                    row["dispatched"] = True
                    store.write(STATE, state)

                try:
                    if observed is None:
                        drive.create_upload(
                            {
                                "id": file_id,
                                "name": unit["name"],
                                "parents": [parent],
                                "mimeType": MIME,
                                "appProperties": {"xm_key": token},
                            },
                            body,
                            before_content=before_content,
                        )
                    else:
                        headers = {**drive.headers(), "Content-Type": MIME}
                        before_content()
                        response = http.patch(
                            f"{UPLOAD}/{file_id}",
                            params={"uploadType": "media"},
                            headers=headers,
                            content=body,
                        )
                        checked(response)
                except DriveHTTPError as error:
                    if error.status in (400, 401, 403, 404, 405, 410, 411, 412, 413, 415, 422, 429):
                        row["dispatched"] = False
                        store.write(STATE, state)
                    raise
                except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
                    # No content request reached Drive. A later caller may use newer bytes.
                    row["dispatched"] = False
                    store.write(STATE, state)
                    raise
                require(
                    read_target(drive, row, parent, token, owner) == desired,
                    "Search readback differs.",
                )
                current_generation()
                row.update(verified=desired, pending=None, dispatched=False)
                store.write(STATE, state)
        current_generation()
        require(
            children(drive, parent) == {r["file_id"] for r in rows.values()},
            "Search file coverage differs.",
        )
        current_generation()
        state["complete"] = catalog_sha256
        store.write(STATE, state)
        return {
            "verified": len(rows),
            "rebuild_verify_seconds": rebuild_seconds,
            "catalog_sha256": catalog_sha256,
            "renderer": RENDERER_VERSION,
        }
