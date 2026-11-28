#!/usr/bin/env bash
# Install a prepared release alongside the November 28 desktop worker.
set -euo pipefail
release=$(realpath "$(dirname "$0")/../release")
read -r revision < "$release/revision"
[[ $EUID == 0 && $revision =~ ^[0-9a-f]{40}$ ]]
base=/opt/x-bookmark-migrator
python=$base/0d5c1e9180b17630eff234f3800e01d827c7700b/.venv/bin/python
unit=openclaw-x-migrator.service
timer=openclaw-x-migrator.timer
state=/var/lib/x-bookmark-migrator/state
config=/etc/openclaw-x-migrator/scheduled-20x45.json
app=$base/$revision.pyz
pin=$base/$revision.sha256
guard=/usr/local/libexec/verify-scheduled-x-migrator-20x45.py
drop=/etc/systemd/system/$unit.d/40-catalog-curation.conf

systemctl is-enabled --quiet "$timer"
systemctl is-active --quiet "$timer"
install -m 0644 "$release/$revision.pyz" "$app"
install -m 0644 "$release/$revision.sha256" "$pin"
sha256sum -c "$pin"

# Check the actual installed package and protected configuration before selecting it.
systemd-run --quiet --wait --pipe --collect --uid=x-migrator \
    -p PrivateNetwork=yes -p ProtectSystem=strict -p RestrictSUIDSGID=yes \
    -p NoNewPrivileges=yes \
    -p "ReadWritePaths=$state/lock /var/lib/openclaw/x-archive/complete-catalog/lock" \
    "$python" -IB "$app" --state-dir "$state" worker --config "$config" --report-only |
    python3 -c 'import json,sys; r=json.load(sys.stdin); sys.exit(0 if r["api_requests"] == r["outcome_unknown"] == 0 else 1)'

install -m 0644 "$release/40-catalog-curation.conf" "$drop"
systemd-analyze verify "$unit"
systemctl daemon-reload
command=$(systemctl show "$unit" -p ExecStart --value)
checks=$(systemctl show "$unit" -p ExecStartPre --value)
[[ $command == *"$app $state $config"* && $checks == *"$guard"* && $checks == *"$pin"* ]]
[[ $(systemctl show "$unit" -p RestrictSUIDSGID --value) == yes ]]

# Reset only the known pre-discovery crash. Reruns leave newer work alone.
runuser -u x-migrator -- "$python" -IB - "$app" "$state" <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from x_bookmark_migrator.core import Store
from x_bookmark_migrator.worker import DEFER, JOURNAL

store = Store(Path(sys.argv[2]))
with store.locked():
    hold = store.read(DEFER) or {}
    if hold.get("provider") == "worker" and hold.get("observed_at") == 1795882217.9226046:
        state = store.read(JOURNAL)
        report = store.read("worker-report.private.json")
        deadline = 1795968616.7128916
        if (report["stop_reason"] != "read_failed" or report["request_slots_by_operation"]
                or report["outcome_unknown"] or report["run_counts"]
                or state["next_daily"] != state["next_intake"]
                or state["next_intake"] not in (deadline, 0)
                or hold.get("until") not in (1795968617.9226046, 0)):
            sys.exit("Retry state changed. No reset performed.")
        state.update(next_daily=0, next_intake=0)
        store.write(JOURNAL, state)
        store.write(DEFER, {**hold, "until": 0})
        print("09:10 worker deferral cleared; intake and history are due.")
    else:
        print("09:10 worker deferral no longer present; existing schedule retained.")
PY
systemctl list-timers --all "$timer" --no-pager
