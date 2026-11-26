import copy

import httpx
import pytest
from test_archive import storage  # noqa: F401
from test_search_design import archive, category_edit  # noqa: F401

from x_bookmark_migrator.catalog import build, publish
from x_bookmark_migrator.core import PilotError
from x_bookmark_migrator.drive import FOLDER, digest
from x_bookmark_migrator.search_publisher import STATE, publish_search


class SearchTransport:
    """Only the dedicated folder supports updates; archive mutations fail the fixture."""

    def __init__(self, server):
        self.server = server
        server.objects["search"] = dict(
            id="search",
            mimeType=FOLDER,
            ownedByMe=True,
            parents=["root"],
            trashed=False,
            capabilities={"canAddChildren": True},
        )
        self.acks_lost = set()
        self.writes = []
        self.shared = set()

    def handle(self, req):
        path = req.url.path
        objects = self.server.objects
        if path.endswith("/permissions"):
            fid = path.split("/")[-2]
            acl = [{"id": "synthetic-owner", "type": "user", "role": "owner"}]
            if fid in self.shared:
                acl.append({"id": "other", "type": "user", "role": "writer"})
            return httpx.Response(200, json={"permissions": acl})
        if path == "/drive/v3/files" and req.url.params.get("q") == "'search' in parents":
            return httpx.Response(
                200,
                json={
                    "incompleteSearch": False,
                    "files": [
                        {"id": fid}
                        for fid, obj in objects.items()
                        if obj.get("parents") == ["search"]
                    ],
                },
            )
        if req.method == "PATCH":
            fid = path.rsplit("/", 1)[-1]
            assert path == "/upload/drive/v3/files/" + fid
            assert objects[fid]["parents"] == ["search"]
            assert (
                req.url.params["uploadType"] == "media"
                and req.headers["Content-Type"] == "text/plain"
            )
            self.server.bytes[fid] = req.content
            response = httpx.Response(200, json={"id": fid})
        else:
            response = self.server.handle(req)
            if req.method != "PUT":
                return response
            fid = req.url.params["upload_id"]
            assert objects[fid]["parents"] == ["search"]
            objects[fid].update(ownedByMe=True, capabilities={"canEdit": True})
        self.writes.append((req.method, fid))
        if req.method in self.acks_lost:
            self.acks_lost.remove(req.method)
            raise httpx.ReadError("synthetic lost reply")
        return response


@pytest.fixture
def publisher(storage, archive):  # noqa: F811
    store, _, server, _ = storage
    catalog, batches = archive
    transport = SearchTransport(server)

    def rebuild():
        publish(catalog, build(batches, catalog))
        return digest(catalog.path("catalog.jsonl").read_bytes())

    def run(expected):
        with httpx.Client(transport=httpx.MockTransport(transport.handle)) as http:
            return publish_search(
                store,
                catalog,
                batches,
                expected_records=5,
                catalog_sha256=expected,
                root="root",
                parent="search",
                http=http,
            )

    return store, catalog, transport, rebuild, run


def test_restart_after_create_and_update_then_replay_stale_generation(publisher):
    store, catalog, fake, rebuild, run = publisher
    originals = dict(fake.server.bytes)
    first = rebuild()
    fake.acks_lost.add("PUT")
    with pytest.raises(httpx.ReadError):
        run(first)
    assert store.read(STATE)["complete"] is None
    assert run(first)["verified"] == 5
    rows = store.read(STATE)["rows"]
    ids = {pid: row["file_id"] for pid, row in rows.items()}
    assert len(set(ids.values())) == 5 and len(fake.writes) == 5
    assert "The last sluice opened at moonrise." in fake.server.bytes[ids["701"]].decode()
    category_edit(catalog, "edit", "701", "Synthetic new category")
    second = rebuild()
    fake.acks_lost.add("PATCH")
    with pytest.raises(httpx.ReadError):
        run(second)
    assert store.read(STATE)["complete"] is None
    assert "Synthetic new category" in fake.server.bytes[ids["701"]].decode()
    # A newer edit is already published locally when the prior lost reply is recovered.
    category_edit(catalog, "newer", "701", "Newest category")
    third = rebuild()
    assert run(third)["verified"] == 5
    updated = dict(fake.server.bytes)
    assert "Newest category" in updated[ids["701"]].decode()
    assert "Synthetic new category" not in updated[ids["701"]].decode()
    for obsolete in (first, second):
        with pytest.raises(PilotError, match="Stale"):
            run(obsolete)
    assert run(third)["verified"] == 5
    assert fake.writes[5:] == [("PATCH", ids["701"])] * 2
    assert fake.server.bytes == {**originals, **{fid: updated[fid] for fid in ids.values()}}
    state = store.read(STATE)
    assert state["complete"] == third
    assert {pid: row["file_id"] for pid, row in state["rows"].items()} == ids


@pytest.mark.parametrize(
    "fault",
    [
        "trashed",
        "missing_flag",
        "wrong_parent",
        "missing",
        "duplicate",
        "file_acl",
        "folder_acl",
        "foreign_bytes",
        "lost_map",
    ],
)
def test_conflict_stops_without_replacement(publisher, fault):
    store, catalog, fake, rebuild, run = publisher
    run(rebuild())
    fid = store.read(STATE)["rows"]["701"]["file_id"]
    obj = fake.server.objects[fid]
    if fault == "trashed":
        obj["trashed"] = True
    elif fault == "missing_flag":
        del obj["trashed"]
    elif fault == "wrong_parent":
        obj["parents"] = ["other"]
    elif fault == "missing":
        del fake.server.objects[fid]
    elif fault == "duplicate":
        fake.server.objects["duplicate"] = {**obj, "id": "duplicate"}
    elif fault.endswith("_acl"):
        fake.shared.add(fid if fault == "file_acl" else "search")
    elif fault == "foreign_bytes":
        fake.server.bytes[fid] = b"owner edit"
    else:
        store.path(STATE).unlink()
    category_edit(catalog, "edit", "701", "New")
    latest = rebuild()
    before = copy.deepcopy(fake.server.objects), dict(fake.server.bytes), list(fake.writes)
    with pytest.raises(PilotError):
        run(latest)
    assert before == (fake.server.objects, fake.server.bytes, fake.writes)
    assert (store.read(STATE) or {}).get("complete") != latest


def test_failed_connection_then_revert_requires_no_update(publisher, monkeypatch):
    store, catalog, fake, rebuild, run = publisher
    first = rebuild()
    run(first)
    original = store.read(STATE)["rows"]
    category_edit(catalog, "edit", "701", "Never sent")
    with monkeypatch.context() as m:
        real = fake.handle

        def disconnected(req):
            if req.method == "PATCH":
                raise httpx.ConnectError("failed before dispatch")
            return real(req)

        m.setattr(fake, "handle", disconnected)
        with pytest.raises(httpx.ConnectError):
            run(rebuild())
    category_edit(catalog, "revert", "701", None)
    assert run(rebuild())["verified"] == 5
    assert len(fake.writes) == 5 and store.read(STATE)["rows"] == original


@pytest.mark.parametrize("method,missing_flag", [("PUT", False), ("PATCH", False), ("PATCH", True)])
def test_inflight_write_cannot_be_retried_over_a_newer_edit(
    publisher, monkeypatch, method, missing_flag
):
    store, catalog, fake, rebuild, run = publisher
    if method == "PATCH":
        run(rebuild())
    category_edit(catalog, "edit", "701", "Delayed")
    delayed = []
    real = fake.handle
    with monkeypatch.context() as m:

        def timeout(req):
            if req.method == method:
                delayed.append(req)
                raise httpx.ReadTimeout("unknown outcome", request=req)
            return real(req)

        m.setattr(fake, "handle", timeout)
        with pytest.raises(httpx.ReadTimeout):
            run(rebuild())
    state = store.read(STATE)
    interrupted = state["rows"]["701"]
    if missing_flag:
        del interrupted["dispatched"]
        store.write(STATE, state)
    category_edit(catalog, "next", "701", "Newer edit")
    newest = rebuild()
    with pytest.raises(PilotError, match="still pending"):
        run(newest)
    assert store.read(STATE)["rows"]["701"] == interrupted
    assert store.read(STATE)["complete"] is None
    assert len(fake.writes) == (5 if method == "PATCH" else 0)
    real(delayed.pop())
    assert run(newest)["verified"] == 5
    fid = store.read(STATE)["rows"]["701"]["file_id"]
    assert fid == interrupted["file_id"] and b"Newer edit" in fake.server.bytes[fid]
    assert [m for m, target in fake.writes if target == fid] == (
        ["PUT", "PATCH", "PATCH"] if method == "PATCH" else ["PUT", "PATCH"]
    )
    assert len({target for _, target in fake.writes}) == 5
    assert store.read(STATE)["complete"] == newest


def test_generation_change_during_recovered_readback_preserves_intent(publisher, monkeypatch):
    store, catalog, fake, rebuild, run = publisher
    current = rebuild()
    fake.acks_lost.add("PUT")
    with pytest.raises(httpx.ReadError):
        run(current)
    before = store.read(STATE)["rows"]
    real = fake.handle

    def invalidate(req):
        response = real(req)
        if req.url.params.get("alt") == "media":
            catalog.path("catalog.jsonl").write_bytes(b"changed by synthetic fault")
        return response

    monkeypatch.setattr(fake, "handle", invalidate)
    with pytest.raises(PilotError, match="Stale"):
        run(current)
    assert store.read(STATE)["rows"] == before and len(fake.writes) == 1


def test_invalidated_same_generation_coverage_keeps_mapping(publisher):
    store, _, fake, rebuild, run = publisher
    current = rebuild()
    run(current)
    before = store.read(STATE)["rows"]
    fid = before["701"]["file_id"]
    fake.server.objects["duplicate"] = {**fake.server.objects[fid], "id": "duplicate"}
    with pytest.raises(PilotError, match="Unknown search files"):
        run(current)
    state = store.read(STATE)
    assert state["complete"] is None and state["rows"] == before and len(fake.writes) == 5
