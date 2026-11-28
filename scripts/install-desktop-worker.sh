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
runner=$base/$revision-scheduled.py
guard=/usr/local/libexec/verify-scheduled-x-migrator-20x45.py
drop=/etc/systemd/system/$unit.d/40-catalog-curation.conf
old=$base/6323e5e387a51db41faaf64567ee50e801770445

check_commands() {
    python3 - "$1" "$python" "$runner" "$app" "$old" "$state" "$config" "$guard" "$pin" <<'PY'
import json
import subprocess
import sys

mode, python, runner, app, old, state, config, guard, pin = sys.argv[1:]
svc = "org.freedesktop.systemd1"
obj = "/org/freedesktop/systemd1/unit/openclaw_2dx_2dmigrator_2eservice"
pre = [["/usr/bin/python3", "-IB", guard], ["/usr/bin/sha256sum", "--status", "--check", old + ".sha256"]]
if mode == "old":
    runner = "/opt/x-bookmark-migrator/scheduled-20x45.py"
    app = old + ".pyz"
else:
    pre.append(["/usr/bin/sha256sum", "--status", "--check", pin])
for prop, expected in [("ExecStartPre", pre), ("ExecStart", [[python, "-IB", runner, app, state, config]])]:
    raw = subprocess.check_output(["busctl", "--json=short", "get-property", svc, obj, svc+".Service", prop])
    commands = [row[:3] for row in json.loads(raw)["data"]]
    if commands != [[argv[0], argv, False] for argv in expected]:
        sys.exit(f"Unexpected effective {prop} ({mode}).")
PY
}

systemctl is-enabled --quiet "$timer"
systemctl is-active --quiet "$timer"
exec 9>/run/openclaw-x-migrator-rollout.lock
flock -n 9
for artifact in "$app" "$runner" "$pin"; do
    src="$release/${artifact##*/}"
    if [[ -e $artifact ]]; then
        cmp "$src" "$artifact"
    else
        staged=$(mktemp "$base/.install.XXXXXXXX")
        trap 'rm -f "$staged"' EXIT
        install -m 0644 "$src" "$staged"
        mv -T "$staged" "$artifact"
    fi
done
sha256sum -c "$pin"

# Check the actual installed package and protected configuration before selecting it.
systemd-run --quiet --wait --pipe --collect --uid=x-migrator \
    -p PrivateNetwork=yes -p ProtectSystem=strict -p RestrictSUIDSGID=yes \
    -p NoNewPrivileges=yes \
    -p "ReadWritePaths=$state/lock /var/lib/openclaw/x-archive/complete-catalog/lock" \
    "$python" -IB "$app" --state-dir "$state" worker --config "$config" --report-only |
    python3 -c 'import json,sys; r=json.load(sys.stdin); sys.exit(0 if r["api_requests"] == r["outcome_unknown"] == 0 else 1)'

staged=$(mktemp "${drop%/*}/.install.XXXXXXXX")
trap 'rm -f "$staged"' EXIT
cat > "$staged" <<EOF
[Service]
Environment=X_MIGRATOR_SCHEDULED_TARGET_FILE=/etc/openclaw-x-migrator/acceptance-post
ExecStartPre=/usr/bin/sha256sum --status --check $pin
ExecStart=
ExecStart=$python -IB $runner $app $state $config
EOF
chmod 0644 "$staged"
if [[ -e $drop ]]; then
    cmp "$staged" "$drop"
else
    check_commands old
    mv -T "$staged" "$drop"
    trap 'rm -f "$drop"; systemctl daemon-reload; check_commands old' EXIT
fi
systemd-analyze verify "$unit"
systemctl daemon-reload
check_commands new
[[ $(systemctl show "$unit" -p Environment --value) == *"X_MIGRATOR_SCHEDULED_TARGET_FILE=/etc/openclaw-x-migrator/acceptance-post"* ]]
[[ $(systemctl show "$unit" -p RestrictSUIDSGID --value) == yes ]]
trap 'rm -f "$staged"' EXIT

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
