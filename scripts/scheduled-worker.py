"""Run the installed worker and check the owner's one-item acceptance receipt."""

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


def verified(root, config, post):
    from x_bookmark_migrator.catalog import read_catalog
    from x_bookmark_migrator.core import Store
    from x_bookmark_migrator.discovery_gate import verify_local_media
    from x_bookmark_migrator.drive import digest, encode
    from x_bookmark_migrator.publication import PublicationStore
    from x_bookmark_migrator.raindrop import validate_receipt
    from x_bookmark_migrator.unbookmark import classify, latest_attempt

    store = Store(root)
    with store.locked():
        base, _ = latest_attempt(store, config["owner_id"], post)
        if not base:
            return False
        intent = store.read(base + ".intent.private.json")
        origin = intent.get("scheduled_origin") or {}
        invocation = origin.get("invocation", "")
        package = origin.get("package_sha256", "")
        if not re.fullmatch("[0-9a-f]{32}", invocation) or not re.fullmatch(
            "[0-9a-f]{64}", package
        ):
            return False
        name = f"scheduled-acceptance-{invocation}.private.json"
        if store.read(name):
            return False
        response = store.read(base + ".response.private.json") or {}
        window = store.read(base + ".window.private.json") or {}
        if not (
            response.get("intent_sha256") == digest(encode(intent))
            and classify(response) == "unbookmarked"
            and window.get("removed_id_absent")
            and post in window["before"]["ids"]
            and post not in window["after"]["ids"]
        ):
            return False
        gate = intent["discovery_gate"]
        if gate["kind"] != "raindrop":
            return False
        validate_receipt(gate["proof"], post)
        catalog = PublicationStore(Path(config["catalog_dir"]))
        with catalog.locked():
            (record,) = (r for r in read_catalog(catalog) if r["post_id"] == post)
            if record["raindrop_id"] != gate["proof"]["raindrop_id"]:
                return False
            (version,) = (
                v
                for v in record["versions"]
                if v["manifest"] == intent["archive_manifest"]
                and v["payload_receipt"]["sha256"] == gate["payload_sha256"]
            )
            item = store.read("worker.private.json")["items"][post]
            restore = Path(item["restore_dir"])
            payload = json.loads((restore / (post + ".json")).read_bytes())
            if not version["media"] or version["archive_status"] != "bytes_verified":
                return False
            verify_local_media(
                catalog, version, payload, version["manifest"], version["payload_receipt"], restore
            )
        store.create(
            name,
            {
                "post_id": post,
                "invocation": invocation,
                "attempt": base,
                "manifest": version["manifest"],
                "raindrop_id": record["raindrop_id"],
                "package_sha256": package,
            },
        )
        return True


if __name__ == "__main__":
    app, state, cfg = sys.argv[1:]
    os.environ["X_MIGRATOR_SCHEDULED_PACKAGE_SHA256"] = hashlib.sha256(
        Path(app).read_bytes()
    ).hexdigest()
    result = subprocess.run(
        [
            sys.executable,
            "-IB",
            app,
            "--state-dir",
            state,
            "worker",
            "--config",
            cfg,
            "--once",
            "--live",
            "--allow-delete",
            "--scheduled",
        ]
    )
    sys.path.insert(0, app)
    try:
        target = Path("/etc/openclaw-x-migrator/acceptance-post").read_text().strip()
        if verified(Path(state), json.loads(Path(cfg).read_bytes()), target):
            print(
                "Bookmark acceptance PASS: scheduled catalog, restored Drive media, "
                "Raindrop and confirmed X removal.",
                file=sys.stderr,
            )
    except Exception:
        print(
            "Bookmark acceptance evidence unavailable; no removal retry requested.", file=sys.stderr
        )
    sys.exit(result.returncode)
