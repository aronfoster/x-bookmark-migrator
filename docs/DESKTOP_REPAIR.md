# Desktop catalog and historical repair acceptance (FOS-216 / FOS-166)

This is the post-backup execution handoff for the November 17 checkpoint, not
permission to execute it. FOS-169 owns backup acceptance and protected local
handoff. FOS-166 owns the installed skill, provider adapter/ledger and prospective
gate. This procedure uses those implementations; it does not run the worker,
create a timer, call X, or authorize any X DELETE.

## Entry conditions and private inputs

Do not repeat the successful skill installation, protected build, 103-record
staging, or encrypted capture. FOS-169's owner decryption, signing identity and
real off-host restore acceptance must succeed before publication/relocation.
Preserve its existing backup and credential decisions. The owner executes any
reviewed privileged setup; agents do not gain sudo or Docker socket access.

Use the independently reviewed/merged readiness revision containing
`catalog-verify` and `--native-helper-audit`; update a protected release only by
the existing FOS-169 process. A source PR is not an installed runtime receipt.
Recheck installed script/helper hashes and visibility against the already
accepted runtime receipt; do not blindly pull/reset the outbound OpenClaw export.
Keep all automation and provider writers idle for the acceptance window.

Resolve these from the existing private staging/rollout receipts, never guesses:

| Shell name | Local value / invariant |
| --- | --- |
| `CATALOG` | New complete publication directory alongside the four preserved catalogs; runtime-owned 0700 or the approved 2750 reader layout |
| `STAGED_NOTES` | Already merged annotations, including attachment curation and owner category override |
| `BATCH_ARGS` | Bash array containing eight `--batch RESTORED_DIR RECEIPT` pairs: five main batches and three pilots |
| `REMOVAL_ARGS` | Bash array of every complete native `--removal-dir DIR`; never split a ledger |
| `HELPER_AUDIT` | Reviewed native-delegation descriptor described below |
| `LEDGER` | The single existing FOS-166 Raindrop ledger, reused for inventory, pilot, backfill and future worker |
| `SCRIPT` | Absolute effective installed reviewed `raindrop.sh`, with its matching sibling `list_all.py` |
| `RUN` | Newly created 0700 evidence directory outside Git and disjoint from catalog, archives, state, ledger and helper evidence |
| `PILOT_ID` | One fully archived historical confirmed/unknown primary ID requiring Raindrop, selected after inventory |

Use the existing protected runtime interpreter/entry point. `uv run x-migrator`
below denotes that environment, not an instruction to install dependencies or
fetch packages inside a live maintenance window. IDs, original report filenames,
private paths, links and notes belong only in private evidence. Use umask 077.

Record baseline file SHA-256, relative names, modes and owners for the existing
four catalogs, annotations/category sidecar, archives/media, native evidence,
restoration receipts and ledgers. Record the reviewed staging annotation hash.
If curation changed since staging, stop publication and merge those actual changes
into staging under the existing rules; never overwrite a newer owner edit.
Hold the existing migration state lock before the catalog lock for publication;
commands take their own catalog lock, so do not acquire it twice. Serialize
curation and repair; keep readers off an in-progress generation.

## 1. Publish the complete catalog without losing curation

Create a **new**, empty complete-catalog destination through the existing approved
local handoff. Leave all four old catalogs in place. Copy `STAGED_NOTES` into its
`annotations.private.json` using exclusive creation (fail if a file already
exists), preserve all keys, and keep it runtime-owned 0600. No category-sidecar
reimport is necessary: it was already merged. Do not replace it with `{}`.

Build at the final catalog path using the complete eight-pair array:

```sh
uv run x-migrator catalog-build --catalog-dir "$CATALOG" "${BATCH_ARGS[@]}"
uv run x-migrator catalog-verify --catalog-dir "$CATALOG" \
  "${BATCH_ARGS[@]}" --expected-records 103
```

Do not copy staged HTML/catalog bytes into a different directory: media links are
relative to the publication root. The build regenerates all three projections
from the unchanged archives and merged annotations. `catalog-verify` rebuilds in
memory and compares the **persisted raw bytes** of `catalog.jsonl`, `index.html`
and `source-links.jsonl`; it cannot silently normalize a wrong source field away.
It checks exact primary/reference links, relative attachment/Drive links, category
overrides, annotation rendering and archive receipt/hash consistency. Missing
local media remains a catalog availability state, so separately require the
existing 117 unique stored media paths still hash-verify; do not count duplicate
references across snapshots as extra files. The 43 blocked records remain visible
and blocked, not discarded or upgraded to complete.

Confirm the annotations SHA-256 still equals the reviewed staged hash and the
baseline original files are unchanged. Only the new generation and approved
reader configuration may change. Build/verify are offline. Neither proves current
Drive availability or actual reader access.

**Actual reader acceptance before switching the default discovery path:**

* In the intended Mnemosyne runtime/UID and container mount, read the new catalog,
  sidecar and representative attachment bytes. Check real permission/mount
  traversal, not only host-root `test -r`. Use ordinary reader file access: the
  publisher's `catalog-lookup` takes an owner-only lock and is not a reader-group
  permission probe. Readers must still be unable to write the catalog or read
  private annotations/state/credentials. Do not broaden those permissions.
* Confirm 103 unique primary rows, exact source fields and all expected old pilot
  IDs. Ask Mnemosyne for exact-ID primary retrieval and an attachment association
  from the complete catalog. A quote/reference match must not masquerade as the
  saved primary. Compare its reported file/hash/link with publisher evidence.
* Open the new private `index.html` through the owner's actual browser path. Check
  original-X anchors for text, photo, video, quote/repost and an already-removed
  bookmark; inspect href identity without requiring X network access. Check one
  visible photo and video path load and one hidden attachment remains omitted
  while its annotation is retrievable. Confirm the owner category override and
  original category provenance both survive. This checks new links/permissions,
  not a repeat of the already accepted full photo/video/text demonstrations.
* Save sanitized pass/fail evidence. Then update the single intended discovery
  path through the reviewed local mechanism, retaining the old path as rollback.

A failed build/verification/read leaves readers on the old catalog. If a failure
is found after the switch, point readers back to the preserved previous catalog;
retain the failed generation and logs for diagnosis. `publish` replaces three
files individually under lock, not as a directory-wide atomic transaction: do not
expose a partially published generation or claim a lock alone protects browsers.

## 2. Represent private native-only helper provenance

The local audit observed two wrappers delegating to native `apply_plan`, nine
referenced run reports, exactly 25 attempted outcomes matched one-for-one to 25
native intents, plus one pre-intent blocked row with **no sent request**. Treat
these as local reported evidence to bind/recheck, not hardcoded scanner truth.

Create an owner-only descriptor from those original files after local review:

```json
{
  "schema_version": 1,
  "mode": "native_delegation",
  "all_intents_included": true,
  "all_helpers_delegate_to_native": true,
  "review_note": "Describe source review, report-to-intent mapping, no-attempt rows and scope of the all-source search",
  "artifacts": [
    {"role": "helper_source", "path": "wrapper.py", "sha256": "<original bytes>"},
    {"role": "run_report", "path": "run.private.json", "sha256": "<original bytes>"},
    {"role": "summary_marker", "path": "marker.private.json", "sha256": "<original bytes>"}
  ],
  "native_intents": [
    {"path": "<original native intent path>", "sha256": "<original bytes>"}
  ]
}
```

Include **all** wrapper sources, referenced reports/markers and native intents,
not just the three illustrative artifacts. Paths resolve relative to the descriptor
unless absolute. The scanner hashes original bytes and requires an exact bijection
with all native intents supplied to the audit. It adds zero synthetic attempts;
unknown native outcomes stay unknown. Source semantics and interpretation of report
rows remain explicit independent review assertions, not proven by matching hashes.
Do not fabricate helper receipts or map pre-DELETE stops to rejected/unknown DELETE.

If source review discovers a separate direct removal path, use the existing
`--helper-format` JSON Pointer mapping for its **original** independent intent
records as well. Inventory absent/unreferenced ledgers, failed/crashed runs and
scheduled/manual entry points; a missing final native intent cannot be detected
from sequence continuity alone. Do not assert `--all-removal-sources-confirmed`
until this bounded private source search and report mapping are reviewed.

Record retirement separately: preserve originals, disable obsolete entry points
or verify exclusive delegation to the installed hardened v2 gate, and check no
scheduled/manual bypass remains. The new descriptor deliberately reports
`helper_retirement_verified:false`; it never grants future deletion authority.
For later FOS-168 configuration, native-only wrappers add **no** `helper_descriptors`
entries: native scanning already accounts for their attempts. Retain this audit as
evidence for the separately reviewed `removal_sources_complete`/retirement gates.

Run the offline audit with the full 103-record scope, never the default 100:

```sh
uv run x-migrator repair-audit --catalog-dir "$CATALOG" --output-dir "$RUN/audit-before" \
  "${BATCH_ARGS[@]}" "${REMOVAL_ARGS[@]}" --native-helper-audit "$HELPER_AUDIT" \
  --expected-records 103 --all-removal-sources-confirmed
```

Expect 103 primary rows, main cohort 25 confirmed / 32 complete not removed /
43 blocked, plus three preserved pilots. Check pilot histories independently.
Before Raindrop, audit can correctly exit nonzero because readback is unverified;
inspect its private `coverage` and per-row blockers, not merely process exit status.
No claim of all-history completeness follows from these counts. Native unknowns,
orphans, mismatched hashes or changed counts stop expansion and require evidence.

## 3. Read-only active/Trash inventory and diagnostics

Obtain the separately authorized read-only window; pause other Raindrop writers.
Use the **existing** skill's complete inventory operation (GETs only), saving a
new private file with exclusive creation. This retains diagnostic evidence even
when the adapter's strict link validator would reject the snapshot before saving:

```sh
(umask 077; set -o noclobber; bash "$SCRIPT" --json --delay 600 list-all > "$RUN/inventory-before.private.json")
```

Require exit 0. On failure, retain any diagnostic file as failed evidence, never
pass it as a valid snapshot and never infer absence. Do not sample `list`, use
search, omit Trash, or filter out blockers. The shared helper scans collection 0
and -99, 50/page, twice, checking exact counts, duplicate Raindrop IDs, terminal
short pages and stable decision fields. Missing count/Trash shape, changing pages,
401/429, timeout or 1000-page limit stops. Actual provider shapes remain unproven.

Offline diagnostics use the existing validator/parser/inspector, not a second
matching algorithm. Run this in the protected runtime with `RUN` set in the
command environment; it performs no provider calls and prints only counts:

```sh
RUN="$RUN" uv run python - <<'PY'
import json, os
from pathlib import Path
from x_bookmark_migrator.catalog import private_bytes
from x_bookmark_migrator.core import PilotError
from x_bookmark_migrator.raindrop import validate_inventory, inspect_inventory
from x_bookmark_migrator.source import raindrop_post_id
root = Path(os.environ['RUN'])
snapshot = json.loads(private_bytes(root / 'inventory-before.private.json'))
valid, reason = True, None
try:
    validate_inventory(snapshot)
except PilotError as exc:
    valid, reason = False, (exc.summary or {}).get('reason', exc.code)
ids, unsupported = set(), []
for scope in ('active', 'trash'):
    for item in snapshot.get(scope, {}).get('items', []):
        try:
            pid = raindrop_post_id(item.get('link'))
        except PilotError:
            unsupported.append({'scope': scope, 'raindrop_id': item.get('_id')})
        else:
            if pid is not None:
                ids.add(pid)
# The shared inspector owns duplicate/Trash decisions; invalid global coverage
# yields no active/absent conclusion for any ID.
checks = {pid: inspect_inventory(snapshot, pid) for pid in sorted(ids)}
report = {'coverage_valid': valid, 'reason': reason, 'unsupported': unsupported,
          'exact_primary_checks': checks, 'create_authorized': False}
with open(root / 'inventory-diagnostics.private.json', 'x',
          opener=lambda path, flags: os.open(path, flags, 0o600)) as out:
    json.dump(report, out)
print(json.dumps({'coverage_valid': valid, 'unsupported': len(unsupported),
                  'conflicts': sum(r['state'] == 'conflict' for r in checks.values()) if valid else None}))
raise SystemExit(0 if valid and not any(r['state'] == 'conflict' for r in checks.values()) else 1)
PY
```

Use only a successful helper snapshot here; malformed capture is a stop, not a
reason to weaken validation. Inspect every unsupported numeric ID privately.
Unresolved t.co, malformed links, or unsupported X profile/list/search paths block
absence inference for **all** targets under the accepted contract. Preserve them;
no deletion, rewriting or exclusion to get a green result. Duplicate primary links
(active-active or active-Trash) and Trash-only targets require explicit owner
disposition; do not recreate or restore. Existing unrelated Trash is preserved.

Add `--raindrop-inventory "$RUN/inventory-before.private.json"` and
`--raindrop-receipt-dir "$LEDGER"` to a new offline repair audit output directory.
Match all 103 saved **primary** IDs, identify the three earlier pilot entries, and
separate absent from active-but-not-GET-verified. A saved snapshot/receipt has
`fresh:false`. Historical audit success is not fresh provider acceptance.

## 4. Smallest live acceptance: one historical item

Only after publication, source coverage and read-only inventory pass, obtain
explicit owner authorization for **one** Raindrop create/adopt pilot. Choose one
fully verified confirmed/unknown historical record requiring Raindrop. Prefer one
proven absent in both scopes to exercise POST; if none are absent, adopt an existing
item and report create acceptance **not exercised**, without manufacturing a test
bookmark. Retain full existing metadata baseline from the inventory, especially
tags, note, title, collection and favorite for the October pilots.

Run one-item repair using the same inputs and the same account ledger:

```sh
uv run x-migrator repair-backfill --catalog-dir "$CATALOG" --output-dir "$RUN/pilot" \
  "${BATCH_ARGS[@]}" "${REMOVAL_ARGS[@]}" --native-helper-audit "$HELPER_AUDIT" \
  --expected-records 103 --all-removal-sources-confirmed \
  --raindrop-inventory "$RUN/inventory-before.private.json" \
  --raindrop-receipt-dir "$LEDGER" --raindrop-ledger-dir "$LEDGER" \
  --raindrop-script "$SCRIPT" --post-id "$PILOT_ID" --limit 1 --confirm-raindrop-writes
```

All selection checks precede provider calls. The shared adapter independently
re-enumerates current active+Trash, writes intent before POST, GETs by returned ID,
and saves the receipt. Do not call raw `add`, `add-json`, or a second client.
Inspect the one result and shared receipt privately: exact primary ID/link, active
collection, fresh GET, correct archive reference and expected category note (stable
archive key unless an owner override exists). The X source link is the primary
link; no archived body is uploaded. Existing items receive no metadata writes.

**Immediately**, before any rerun or second create:

```sh
uv run x-migrator raindrop-inventory --raindrop-ledger-dir "$LEDGER" --raindrop-script "$SCRIPT"
```

Preserve the resulting ledger inventory under a unique private evidence name
before another inventory overwrites it. Require `inspect_inventory(snapshot,
PILOT_ID)` to return exactly the same single active Raindrop ID as the GET receipt,
no Trash match, stable complete coverage and correct count delta (one if created,
zero if adopted, with unrelated concurrent edits excluded). If the newly GET-
verified item is missing from listing, **stop before rerun**; two stable scans do
not prove read-after-write consistency.

Only after immediate visibility passes, rerun the identical one-item backfill
with a new output directory and the same ledger. Require `adopted`, the same ID,
no additional create intent/POST, a new successful GET, and another complete
inventory showing one matching active item and unchanged count/owner metadata.
Open its primary link in the Raindrop UI to inspect destination identity and use
Mnemosyne/browser exact-ID retrieval; X content availability is not a requirement.

**Failure behavior:** 401/429/timeout, malformed data, conflict, incomplete scan,
unknown POST outcome or GET mismatch stops the pass and all further writes. Keep
intent/result/receipt files and reports. No automatic rollback deletes an account
bookmark. No ledger deletion/reset or blind rerun: an uncertain POST may already
exist. Read-only re-enumeration plus independent GET can establish recovery; only
a separately reviewed resumed ensure may use the adapter's existing single bounded
recovery POST policy. Listing lag is an explicit stop, not permission to trigger
that recovery POST. Any optional account cleanup requires its own owner decision.
Catalog rollback is the preserved-path switch described above.

## 5. Bounded historical repair and completion evidence

After pilot acceptance, select remaining confirmed/unknown historical primary
IDs from the latest audit, excluding resolved rows. Run the same `repair-backfill`
command with explicit `--post-id` arguments and `--limit 3` for the next batch.
Keep batches at three for this 25-record repair; each selected record gets a full
fresh scan and GET via FOS-166. Inspect every batch report before proceeding;
first provider error stops that pass, with later selected IDs unprocessed.
Existing pointers are adopted unchanged. No automatic exemption for ambiguous
text/emoji/t.co; all 25 currently require Raindrop absent affirmative proof.

After each batch save a fresh inventory, perform offline reconciliation with the
shared receipt directory, and confirm the original-file/curation baselines still
match. Preserve unique reports: repair already uses paired random run IDs. GET
receipts establish the time of verification, never permanent live freshness.
At completion obtain final current inventory and fresh GET verification for every
required historical pointer (read-only `raindrop-verify --post-id ID --raindrop-id
RID` through the same adapter); retain private results/timestamps. Positively
exempt records need actual primary/source and archive retrieval, not just a label.

Report the main 25 as verified / affirmative media-only / blocked or unknown,
with unique totals; report the 32/43 and three pilots separately. Never claim
unknown X deletion outcomes became confirmed because Raindrop now exists. Offline
reconciliation may remain incomplete for the 43 already-blocked archive records;
that is not a reason to hide them or falsely claim complete 103-record archival.
Historical helper completeness and retirement need their separate reviewed local
evidence; receipt continuity alone cannot establish an all-sources search.

Update FOS-216 with sanitized counts and private evidence references, FOS-166 with
provider/readers/pilot acceptance, and FOS-169 with the released backup/repair
gates. Keep X deletion suspended and timers disabled: accepting this repair does
not authorize prospective resumption or complete FOS-168/FOS-169.

Provider references checked November 17: [list semantics](https://developer.raindrop.io/v1/raindrops/multiple),
[single create/GET](https://developer.raindrop.io/v1/raindrops/single),
[primary fields](https://developer.raindrop.io/v1/raindrops).
Count/Trash response shapes and immediate listing visibility are live acceptance
checks; stable double-scan, strict unsupported-link policy and bounded recovery
are local safety rules, not documented provider consistency guarantees.
