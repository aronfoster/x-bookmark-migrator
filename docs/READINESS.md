# Post-backup desktop handoff (FOS-216 / FOS-166 / FOS-169)

Prepared 2026-11-17. This is an execution procedure, **not execution authorization**.
No command here calls X. Do not run the worker, unbookmark, enable timers, or set
any deletion gate as part of this procedure. FOS-169 owns backup acceptance and
protected filesystem/identity rollout; FOS-166 owns the existing Raindrop adapter,
skill and ledger; FOS-216 owns publication and historical reconciliation.

## Entry conditions and preserved checkpoint

1. Finish FOS-169's independently anchored signature, owner decryption/full hash
   verification, and actual off-host backup restore. Its latest recovery-key/SSH
   handoff controls. Do not repeat successful initial encrypted capture, account
   setup, installed skill synchronization, or the old protected program build.
2. Review this changeset independently, then use the separately accepted release
   at its exact pin. The previously installed release lacks `catalog-check` and
   native-helper provenance support (and archive-bound `catalog-verify`). Never edit that protected release in place.
   Any owner-run release installation must follow the existing protected-build
   procedure; no agent receives sudo, runtime impersonation, or Docker access.
3. Read the existing private preparation manifest and staged output. The local
   receipt records eight batches, **103 primary records and 117 stored media
   files**, including three earlier pilots. Main five-batch population remains
   100: 25 confirmed removals, 32 complete not removed, 43 blocked. Counts are
   expectations to verify, never permission to drop rows to make totals match.
4. Preserve all four older catalogs, original annotations/category sidecar,
   immutable archive/restore bytes, native receipts, adapter ledger and budget.
   The staged complete catalog already imported curation. Do not import it again
   from a guessed format or replace it with a one-batch catalog.

Use private observed paths only. `XM_PYTHON` is the absolute Python interpreter
from the same reviewed installed protected environment as `XM`, resolved and
verified from the private release manifest. Invoke it directly; do not use `uv run`
or install/fetch dependencies during acceptance. In the following templates, `XM`
is the absolute reviewed installed x-migrator executable; `CATALOG`, `ANNOTATIONS`,
`LEDGER`, `SKILL`, `AUDIT`, `PROVENANCE`, `SNAPSHOT` and `PID` are owner-shell variables
resolved from the existing private manifest. They are not shell configuration to
source. `BATCH_ARGS` is a Bash array containing all eight `--batch RESTORED RECEIPT`
pairs; `REMOVAL_ARGS` contains each intact native `--removal-dir`. Never paste
private values or command output containing records into GitHub/Linear.

## 1. Publish the already staged catalog and verify the actual reader

Complete the existing FOS-169 protected handoff using its reviewed exact file list,
not a second relocation scheme. Preserve shared desktop Drive access as accepted.
Use maintenance/state lock then catalog lock, with writers/curation quiescent.
Snapshot the old derived files and annotations privately before replacement.

The publication set is `catalog.jsonl`, `index.html`, `source-links.jsonl`, and
`annotations.private.json`. Copy the three staged derived files byte-for-byte to
same-directory temporary files; fsync and atomically replace each, fsync the
parent, then install the staged annotations unchanged. Keep readers quiescent
until the **whole set** passes validation: per-file atomic writes are not a
four-file transaction. The existing private handoff must explicitly bind staged
hashes, final paths, owner/group/modes and rollback copies before Aron runs it.
This remote runbook cannot authenticate those private paths or approve an unseen
privileged script. Stop if the staged hashes or current curation have changed;
reconcile new edits rather than overwriting them.

Verify the staged relative media URLs against the actual final catalog location
and private handoff; a staged-preparation receipt alone does not establish that
promotion preserves the layout. Do not copy the HTML alone. If the final paths do
not match the staged projection, rebuild once at the final location with all eight
batches using `catalog-build`, under the same locks. Install the complete preserved
annotations at `$CATALOG/annotations.private.json` **before** `catalog-build`,
because the build reads them from that directory.
After either byte-identical promotion or rebuild, archive-bound `catalog-verify`
below must pass. Do not rewrite manifests, payloads or receipts. Retain prior
catalogs as rollback evidence.

Publication directories/restored batch directories: dedicated runtime owner,
reader group, 2750. Derived files/restored readable bytes: 0640. Annotations,
locks, state, credentials and Raindrop ledger remain private. Do not recursively
make the state/receipt stores reader-accessible. The repair CLI supports the
2750 publication root while retaining private annotation/receipt checks.

As the owner-supervised runtime identity, after releasing publication locks,
set `ANNOTATIONS="$CATALOG/annotations.private.json"` so both writer checks use
the installed canonical annotation file. First bind all persisted projections
to the original archives and those annotations:

```sh
"$XM" catalog-verify --catalog-dir "$CATALOG" "${BATCH_ARGS[@]}" --expected-records 103
```

This fresh in-memory archive/annotation build catches even a self-consistent but
stale/edited catalog generation. It acquires the writer lock; run it separately
from the reader checks. It does not publish or grant reader access. Then:

```sh
"$XM" catalog-check --catalog-dir "$CATALOG" --expected-records 103 \
  --expected-media 117 --annotations-file "$ANNOTATIONS"
```

This checks every persisted primary/reference/attachment source field, exact
record count, rendered HTML and source sidecar, effective category overrides and
attachment annotations, payload/attachment Drive URLs, and hashes of all locally
stored media (including hidden attachments). It reads files only. Missing or
inconsistent output fails; it does not silently repair/upgrade stored fields.
It is local retrieval evidence, not fresh remote Drive proof or X availability.

Repeat **inside the actual OpenClaw reader/container identity and mount view**,
using its observed path to the same catalog and executable:

```sh
"$XM" catalog-check --catalog-dir "$CATALOG" --expected-records 103 --expected-media 117
"$XM" catalog-lookup --catalog-dir "$CATALOG" --post "$PID"
```

Reader commands never create directories or acquire a writer lock. Reader access
is enforced by the OS; no simulated UID test replaces this step. The reader must
not gain access to annotations/credentials merely to pass: its check omits
`--annotations-file`; writer-side curation comparison is separately required.
Lookup prints private full content. Confirm the matched record's `post_id` is
exactly PID because quote lookup can return a containing primary record. Repeat
for representative text, photo, video, quoted/reposted source, earlier pilot, and
historically removed records; use `--asset-id` for an exact attachment. In the
normal private browser, verify source anchors, preserved category, hidden media
omission and visible media links. Have Mnemosyne retrieve one such exact primary
and attachment through its real reader path. Existing media demonstrations need
not be re-created; this checks the newly published location and identity.

Compare pre/post immutable evidence inventories and original curation copies.
Any mismatch: keep provider work stopped, restore only the backed-up derived set
and its matching annotations under the same locks, recheck it, and retain failed
outputs privately. Never roll back an archive, receipt ledger or provider state.

## 2. Close the native-only helper audit without inventing receipts

Latest local evidence: two wrapper sources call native `apply_plan`; nine native
run reports reference 25 native attempts one-for-one, plus one pre-intent blocked
row. That row is not a rejected/unknown removal. Remote code cannot certify that
all wrappers, invocation sites or reports have been found or retired.

Inventory the two original helper sources, original summary markers, all nine
native reports, every native ledger and any additional source discovered. Hash
original bytes (including every original native intent); inspect call sites, alternate copies and scheduled entry points.
Retain originals. Review a private native-delegation descriptor using the schema
in [REPAIR.md](REPAIR.md#native-delegating-wrappers). It references the actual run
reports, not rewritten outcomes. Map native `/outcomes`, `/post_id`, `/attempt`,
`/state` after verifying the actual originals. A `blocked` row may map to
`not_attempted` only after verifying no intent or request was sent; the attempt
field must be missing/null. Never map a failed or ambiguous request to this state.

The checker requires every supplied native intent to be referenced exactly once,
with matching primary ID/outcome. It retains pre-intent stops separately, requires
their primary IDs to belong to the complete catalog population, and creates no
duplicate removal attempts. Native unknowns remain unknown. Additional independent
helper attempts still use existing `--helper-format` descriptors.
Do not fabricate an empty descriptor to satisfy coverage. Mark
`--all-removal-sources-confirmed` only after the independent local source inventory.
For later FOS-168 configuration, native-only wrappers add no `helper_descriptors`
entries: native scanning already counts them. Retain this provenance audit as
evidence for separately reviewed completeness/retirement gates.
Retirement is separate: disable old invocation paths or prove exclusive delegation
to the new hardened gate; a provenance descriptor explicitly does not certify it.

```sh
"$XM" repair-audit --catalog-dir "$CATALOG" --output-dir "$AUDIT" \
  "${BATCH_ARGS[@]}" "${REMOVAL_ARGS[@]}" \
  --native-helper-provenance "$PROVENANCE" \
  --expected-records 103 --all-removal-sources-confirmed
```

The audit writes uniquely named private reports and no catalog/provider changes.
It may exit 1 because Raindrop proof is still absent: inspect `coverage.complete`,
`removal_coverage_complete`, `deletion_outcomes`, blockers and rows. Do not interpret
that expected missing-readback result as complete repair. Expected historical
attempts: 25 confirmed, zero unknown/rejected according to the local checkpoint;
three pilot records remain in the 103-record discovery population. Any extra
attempt or missing record must be explained, not filtered away.

## 3. Separately authorized read-only Raindrop inventory

Use the already installed skill and **same** private FOS-166 ledger. Record the
actual runtime/script/helper hashes and compare with reviewed bytes. Standalone
Raindrop CLI uses the existing script path and environment; unlike the worker it
does not itself enforce protected-file hash pins. The owner-supervised invocation
must use the protected script/environment with its known identity. Never pass a
token on the command line, source an unreviewed env file or grant it to an agent.
Pause concurrent Raindrop edits. Preserve any prior inventory snapshot before
running; the adapter's canonical snapshot is replaced on successful enumeration.

```sh
"$XM" raindrop-inventory --raindrop-ledger-dir "$LEDGER" --raindrop-script "$SKILL"
```

This uses the existing skill's two complete scans of active 0 and Trash -99,
50/page, short terminal page, counts and unique Raindrop IDs, with stable decision
fields across both passes. Search/sample pages never prove absence. Two stable
passes are local policy, not a provider snapshot guarantee. Snapshot shape/count
or unsupported primary link failure exits nonzero before create. Unsupported
X/profile/list/search, unresolved t.co, malformed or non-HTTP links remain strict
blockers. Numeric blocking Raindrop ID is returned; inspect that item privately
without rewriting/removing it. The first blocker is reported, not an exhaustive
list. Do not repeatedly rescan a known blocker or weaken the accepted parser.

For an unsupported-link stop where the adapter did not save the snapshot, obtain
one complete diagnostic snapshot through the **same** existing skill (GETs only),
after the authorized read-only window is established. Set RUN to a fresh private
0700 evidence directory disjoint from source/ledger/publication directories:

```sh
(umask 077; set -o noclobber; bash "$SKILL" --json --delay 600 list-all > "$RUN/inventory-before.private.json")
```

Require successful helper exit. Never treat a failed/partial capture as valid.
Offline diagnostics use the existing validator/parser/inspector, not a second
matching algorithm. Run this in the protected runtime with `RUN` set in the
command environment; it performs no provider calls and prints only counts:

```sh
RUN="$RUN" "$XM_PYTHON" - <<'PY'
import json, os
from pathlib import Path
from x_bookmark_migrator.catalog import private_bytes
from x_bookmark_migrator.core import PilotError
from x_bookmark_migrator.raindrop import validate_inventory, inspect_inventory, item_identity
from x_bookmark_migrator.source import raindrop_post_id
root = Path(os.environ['RUN'])
snapshot = json.loads(private_bytes(root / 'inventory-before.private.json'))
valid, reason = True, None
try:
    validate_inventory(snapshot)
except PilotError as exc:
    valid, reason = False, (exc.summary or {}).get('reason', exc.code)
groups, unsupported, malformed = {}, [], []
for scope in ('active', 'trash'):
    part = snapshot.get(scope) if isinstance(snapshot, dict) else None
    items = part.get('items') if isinstance(part, dict) else None
    if not isinstance(items, list):
        malformed.append({'scope': scope})
        continue
    for index, item in enumerate(items):
        try:
            rid, _ = item_identity(item)
        except PilotError:
            malformed.append({'scope': scope, 'index': index})
            continue
        try:
            pid = raindrop_post_id(item['link'])
        except PilotError:
            unsupported.append({'scope': scope, 'raindrop_id': rid})
        else:
            if pid is not None:
                groups.setdefault(pid, {'active': [], 'trash': []})[scope].append(rid)
# These groups retain parseable observations even when global coverage fails.
# They are non-authoritative: never filter/revalidate the snapshot or infer absence.
# Only the shared inspector owns authoritative duplicate/Trash states.
checks = {pid: inspect_inventory(snapshot, pid) for pid in sorted(groups)}
report = {'coverage_valid': valid, 'reason': reason, 'unsupported': unsupported,
          'malformed': malformed, 'observed_primary_groups': groups,
          'observed_groups_authoritative': False,
          'exact_primary_checks': checks, 'create_authorized': False}
with open(root / 'inventory-diagnostics.private.json', 'x',
          opener=lambda path, flags: os.open(path, flags, 0o600)) as out:
    json.dump(report, out)
print(json.dumps({'coverage_valid': valid, 'unsupported': len(unsupported),
                  'conflicts': sum(r['state'] == 'conflict' for r in checks.values()) if valid else None}))
raise SystemExit(0 if valid and all(r['state'] == 'active' for r in checks.values()) else 1)
PY
```

On successful adapter enumeration, alternatively copy its canonical snapshot to
`$RUN/inventory-before.private.json` with exclusive creation for those diagnostics.
Do not rescan a known blocker repeatedly. These diagnostics enumerate unsupported
IDs in the saved snapshot and retain non-authoritative per-primary active/Trash
ID groups, so duplicate and Trash observations remain visible even when global
coverage is invalid. Malformed scopes/items are identified without echoing content.
Authoritative states still come only from the shared inspector; they remain
`incomplete` for every ID when coverage is invalid. The snippet exits nonzero for
invalid coverage, duplicates or Trash targets. It never filters/revalidates a
partial snapshot, infers absence from these groups, or grants permission to create.

On success, preserve `raindrop-inventory.private.json` as an immutable per-phase
copy outside the mutable ledger path; set SNAPSHOT to that private copy. Repeat
the offline audit with the same complete inputs, adding:

```sh
  --raindrop-inventory "$SNAPSHOT" --raindrop-receipt-dir "$LEDGER"
```

Inspect all 103 rows: exact primary IDs, active/Trash/conflict/absent states,
duplicate IDs and receipt status. Original username/mobile/photo/video URL variants
match exact primary IDs, never quote/asset IDs or substrings. The three older
pilot entries must be adopted unchanged. A missing prior receipt means
`active_unverified`, not absence. Any duplicate target, Trash target, unsupported
link or incomplete snapshot stops the pilot. Never restore/recreate a trashed
owner item or delete duplicates automatically. A successful list alone is not GET
acceptance. This audit uses shared FOS-166 inspection, not a second matching client.

## 4. Smallest write acceptance: one historical record

Obtain separate owner authorization for **one** already removed, non-exempt primary
record verified absent in the complete inventory, with a complete verified archive
and the reviewed category override/key. Choose from the 25 repair targets. If none
is absent, perform adoption/GET/rerun without creating a dummy bookmark, and record
that create behavior was not exercised. No authorization is inferred from backup
acceptance, a read-only inventory, merge, or this document.

```sh
"$XM" repair-backfill --catalog-dir "$CATALOG" --output-dir "$AUDIT" \
  "${BATCH_ARGS[@]}" "${REMOVAL_ARGS[@]}" \
  --native-helper-provenance "$PROVENANCE" \
  --expected-records 103 --all-removal-sources-confirmed \
  --raindrop-inventory "$SNAPSHOT" --raindrop-receipt-dir "$LEDGER" \
  --raindrop-ledger-dir "$LEDGER" --raindrop-script "$SKILL" \
  --post-id "$PID" --limit 1 --confirm-raindrop-writes
```

This re-audits all evidence, then uses FOS-166 ensure: fresh full enumeration,
durable intent, at most one initial POST, independent GET, saved receipt. Preserve
the report, receipt and create-result; verify exact `primary_link`, ID, active
collection, source URL, archive pointer and short category note privately.
Existing items must retain tags/note/favorite/collection. Compare owner-managed
metadata; background provider enrichment can change unrelated fields.

**Immediately run `raindrop-inventory` again**, before any rerun/second target.
Preserve the snapshot; inspect PID using shared `inspect_inventory` (or the offline
audit). Require exactly one active match equal to the receipt's Raindrop ID, no
Trash match, and total counts consistent with one new item (zero for adoption).
Then rerun the identical one-ID backfill command, require `adopted`, the same
Raindrop ID and no additional create attempt; inspect another complete inventory
for unchanged unique target and counts. Open the source/archive links through the
normal reader to complete user-visible acceptance.

Failure is a **stop**, not automatic rollback: preserve partial/uncertain POST
intent/result/receipts and do not retry writes, clear the ledger, or delete a
possibly created bookmark. Authentication/schema/429 failures stop the pass; read
retry only after the observed reset and separately controlled resumption. If GET
succeeds but immediate listing lags, stop rollout: the adapter's bounded recovery
POST is not a consistency guarantee. Later authorized recovery must first perform
complete active+Trash reconciliation; any uncertainty remains an exception. A
provider rollback needs a separate explicit owner decision for the exact item.

## 5. Bounded historical repair and final reconciliation

After the one-ID pilot is accepted, separately authorize bounded backfill of the
remaining confirmed/unknown group. Reuse the exact command and original ledger,
start with at most three explicitly selected `--post-id` values and `--limit 3`.
After each pass preserve reports, run complete inventory and offline reconciliation,
and verify new IDs are visible once and existing metadata unchanged. Increase only
from observed success; no requirement to increase. Stop on the first failure.
The command itself does not retry blocked calls. Keep 32 queued and 43 blocked
records out of this historical write selection. Unknown outcomes, if discovered,
are repair targets but never become confirmed removals merely from backfill.

Final acceptance records all 25 (plus any newly discovered attempted IDs): exact
archive/source identity, all native/helper evidence, fresh GET receipts from this
repair session for required pointers, or positive content-bound media-only evidence
plus actual reader retrieval. Run final complete inventory and audit over all 103.
`offline_reconciliation_complete` checks coverage plus the confirmed/unknown
repair targets; it does not certify complete archival of every one of the 103.
The report may retain a nonzero `exception_records` count for the 43 blocked
archives even after all 25 historical removals pass. Do not require zero global
exceptions, erase blocked rows, or upgrade the 32/43/pilots to historical removals
just to complete this repair. Saved `fresh:false` evidence is not live acceptance;
verify receipt timestamps and actual pilot/reader observations.
Record counts by outcome/classification, exceptions and helper retirement privately;
only sanitized counts/revisions/check results go to Linear. Confirm immutable
archives, old catalogs and canonical curation are preserved. Capture changed
ledgers/catalog in FOS-169's accepted backup process before leaving the session.

FOS-216 repair acceptance does not automatically accept FOS-166's prospective gate,
FOS-168 scheduling, or FOS-169 full-history drain. Keep timers and X DELETE paused.
No new planning cycle is needed for the successful path; a failed invariant is a
specific stop/exception requiring evidence, not permission to bypass it.

Provider references rechecked 2026-11-17: [objects](https://developer.raindrop.io/v1/raindrops),
[single methods](https://developer.raindrop.io/v1/raindrops/single),
[active/Trash pagination](https://developer.raindrop.io/v1/raindrops/multiple).
No provider HTTP integration or receipt schema changed in this readiness work.
