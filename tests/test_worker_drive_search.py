import copy
import itertools
import json
import sys
import time

import httpx
from test_archive import includes, media_http, photo, post
from test_search_publisher import SearchTransport
from test_unbookmark import migration as migration
from test_unbookmark import storage as storage
from test_worker import make_worker, scheduled_cli

from x_bookmark_migrator import cli
from x_bookmark_migrator.catalog import BATCH_INPUTS, read_catalog
from x_bookmark_migrator.drive import digest
from x_bookmark_migrator.inventory import Inventory
from x_bookmark_migrator.search_publisher import STATE


def test_timer_publishes_new_save_and_reconciles_approved_edit(
    migration, tmp_path, monkeypatch, capsys
):
    store, _, server, source, *_ = migration
    now = [time.time()]
    monkeypatch.setattr(time, "time", lambda: now[0])
    worker = make_worker(migration, tmp_path)
    worker.bootstrap(Inventory.load(store))
    # Simulate a pre-integration worker journal and an already completed daily intake.
    assert not worker.catalog.path(BATCH_INPUTS).exists()
    worker.state["next_daily"] = now[0] + 3600
    worker.save()
    store.write(
        "tokens.private.json",
        {
            **store.read("tokens.private.json"),
            "expires_at": now[0] + 86400,
        },
    )
    scheduled_cli(tmp_path, monkeypatch, store, source, time.time)
    path = tmp_path / "config.json"
    cfg = json.loads(path.read_text())
    path.write_text(json.dumps({**cfg, "drive_search_folder_id": "search"}))
    timer = sys.argv.copy()
    fake = SearchTransport(server)
    source.ids.insert(0, "110")
    body = "Lacquered tide clock beside the quay"
    primary = post("110", text=body, attachments={"media_keys": ["clock"]})
    x_calls = []

    def x(request):
        x_calls.append(request)
        if request.url.path in ("/2/users/100/bookmarks", "/2/tweets") and request.method == "GET":
            return httpx.Response(
                200, json={"data": [primary], "includes": includes([photo("clock")])}
            )
        return source.handle(request)

    def drive(request):
        if request.method == "PUT":
            session = server.sessions[request.url.params["upload_id"]]
            if session["parents"] != ["search"]:
                return server.handle(request)
        return fake.handle(request)

    client = httpx.Client
    handlers = itertools.cycle((x, drive, media_http))
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(next(handlers)), **kw)
    )

    def tick():
        monkeypatch.setattr(sys, "argv", timer)
        return cli.main()

    assert tick() == 0, capsys.readouterr()
    assert len(fake.writes) == 3 and not x_calls
    old_rows = store.read(STATE)["rows"]
    originals = dict(server.bytes)
    now[0] += 3601
    fake.acks_lost.add("PUT")
    assert tick() == 1  # The search create landed; its acknowledgement did not.
    assert [r.url.path.split("/")[-1] for r in source.deletes] == ["110"]
    assert store.read(STATE)["complete"] is None
    assert tick() == 0, capsys.readouterr()
    rows = store.read(STATE)["rows"]
    assert rows == {**old_rows, "110": rows["110"]}
    fid = rows["110"]["file_id"]
    assert fake.writes[3:] == [("PUT", fid)]
    record = next(r for r in read_catalog(worker.catalog) if r["post_id"] == "110")
    (version,) = record["versions"]
    (asset,) = version["media"]
    text = server.bytes[fid].decode()
    for term in (body, "Synthetic category", asset["archive_url"], version["payload_url"]):
        assert term in text
    assert all(server.bytes[k] == data for k, data in originals.items())
    assert digest(server.bytes[asset["storage"]["file_id"]]) == asset["storage"]["sha256"]
    assert (
        digest(server.bytes[version["payload_receipt"]["file_id"]])
        == version["payload_receipt"]["sha256"]
    )

    before_bytes, before_objects = dict(server.bytes), copy.deepcopy(server.objects)
    before_x = len(x_calls)
    edit = [
        "x-migrator",
        "catalog-annotate",
        "--catalog-dir",
        str(worker.catalog.root),
        "--asset-id",
        asset["asset_id"],
        "--sha256",
        asset["storage"]["sha256"],
        "--edit-id",
        "fixture-decision",
        "--description",
        "A vermilion escapement",
    ]
    monkeypatch.setattr(sys, "argv", edit)
    assert cli.main() == 0, capsys.readouterr()
    fake.acks_lost.add("PATCH")
    assert tick() == 1
    assert store.read(STATE)["complete"] is None
    assert tick() == 0, capsys.readouterr()
    monkeypatch.setattr(sys, "argv", edit)
    assert cli.main() == 0
    calls = len(server.calls)
    assert tick() == 0
    assert len(server.calls) == calls and len(x_calls) == before_x
    assert fake.writes[3:] == [("PUT", fid), ("PATCH", fid)]
    assert server.objects == before_objects
    assert server.bytes == {**before_bytes, fid: server.bytes[fid]}
    assert "A vermilion escapement" in server.bytes[fid].decode()
    assert asset["archive_url"] in server.bytes[fid].decode()
    assert store.read(STATE)["rows"]["110"]["file_id"] == fid
    assert store.read(STATE)["complete"] == digest(
        worker.catalog.path("catalog.jsonl").read_bytes()
    )


def test_timer_uses_full_build_inputs_and_requires_every_restore(
    migration, tmp_path, monkeypatch, capsys
):
    from test_search_design import restored_batch

    store, drive, server, source, _, archived, *_ = migration
    worker = make_worker(migration, tmp_path)
    worker.bootstrap(Inventory.load(store))
    worker.state["next_daily"] = time.time() + 3600
    worker.save()
    manual = restored_batch(
        tmp_path, drive, "operator-restore", [post("402", text="Covered footbridge")], includes()
    )
    first = tmp_path / "restored", store.path(archived["private_receipt"])
    scheduled_cli(tmp_path, monkeypatch, store, source, time.time)
    timer = sys.argv.copy()
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({**json.loads(path.read_text()), "drive_search_folder_id": "search"})
    )
    fake = SearchTransport(server)
    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(fake.handle), **kw)
    )

    def rebuild(batches):
        argv = ["x-migrator", "catalog-build", "--catalog-dir", str(worker.catalog.root)]
        for root, receipt in batches:
            argv.extend(["--batch", str(root), str(receipt)])
        monkeypatch.setattr(sys, "argv", argv)
        assert cli.main() == 0, capsys.readouterr()
        monkeypatch.setattr(sys, "argv", timer)

    rebuild([first, manual])
    assert worker.catalog.path(BATCH_INPUTS).stat().st_mode & 0o777 == 0o600
    assert cli.main() == 0, capsys.readouterr()
    rows = store.read(STATE)["rows"]
    assert len(rows) == 4 and b"Covered footbridge" in server.bytes[rows["402"]["file_id"]]
    # A new worker restore publishes its own media paths for the overlapping batch.
    worker.adopt(archived["private_receipt"], Inventory.load(store))
    assert cli.main() == 0, capsys.readouterr()
    assert store.read(STATE)["rows"] == rows

    before = dict(server.bytes)
    rebuild([manual])  # An accidental partial rebuild cannot retire earlier files.
    assert cli.main() == 1
    assert server.bytes == before and len(fake.writes) == 4
    rebuild([first, manual])
    assert cli.main() == 0, capsys.readouterr()
    assert store.read(STATE)["rows"] == rows
