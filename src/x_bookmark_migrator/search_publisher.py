"""Publish verified catalog text to a dedicated Drive folder. No live CLI or schedule."""

import httpx

from .catalog import build, verify_publication
from .core import PilotError, exact_id
from .drive import FOLDER, UPLOAD, Drive, checked, digest, drive_id, encode
from .publication import publication_bytes
from .search_projection import RENDERER_VERSION, drive_text

STATE = "drive-search-text.private.json"
MIME = "text/plain"


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
    parent = drive_id(parent)
    require(store.root != catalog.root and root != parent, "Distinct search destinations required.")
    with store.locked(), catalog.locked():

        def current_generation():
            require(
                digest(publication_bytes(catalog.path("catalog.jsonl"))) == catalog_sha256,
                "Stale search generation.",
            )

        current_generation()
        records = build(batches, catalog)
        verify_publication(catalog, records, expected_records)
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
        require(
            state["destination"] == destination and state["renderer"] <= RENDERER_VERSION,
            "Search destination or renderer changed incompatibly.",
        )
        state["complete"] = None
        store.write(STATE, state)
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
            require(0 < len(body) <= drive.max_bytes, "Search unit exceeds upload bound.")
            if pid not in rows:
                require(
                    token not in drive.journal, "Restore missing search mapping before publication."
                )
                rows[pid] = {"file_id": None, "verified": None, "pending": None}
                store.write(STATE, state)
            row = rows[pid]
            if row["file_id"] is None:
                row["file_id"], _ = drive._allocate(parent, key)
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
            "catalog_sha256": catalog_sha256,
            "renderer": RENDERER_VERSION,
        }
