# Historical source-link repair (FOS-216)

This implements the November 16 contract, superseding the older optional-Raindrop
migration policy. Every saved primary post needs its original X link. Non-media
and ambiguous posts need an independently readable Raindrop pointer. This work
never issues X requests or authorizes another DELETE.

## Ownership and shared API

FOS-216 owns `source.py`, catalog/sidecar/gallery, `repair.py` and `repair_cli.py`.
FOS-166 owns `raindrop.py`, `discovery_gate.py`, the prospective unbookmark gate,
and the existing `openclaw-config/skills/raindrop` script. The two tickets agreed
these interfaces in their November 16 implementation coordination comments:

- `source.post_id(value) -> str`, compatibility-exported as `catalog.post_id`;
  `source.source_url(value) -> str`; `source.raindrop_post_id(link) -> str | None`.
- `RaindropAdapter(ledger_dir: Path, script: Path)` and
  `ensure_raindrop(pid, source_url, archive_ref, *, category=None) -> dict`.
  Backfill passes `archive_ref={"manifest": ..., "payload": ...}` from the first fully verified
  primary snapshot in deterministic manifest/payload hash order. All versions
  remain in the audit report. The resolved category override, or the selected
  snapshot category, is passed as `category=` (first 200 characters for the
  adapter note limit); full original and override values stay in the report.
  Without an override, this is the stable archive category key (`folder-<id>` or
  `unfiled`), not a human-readable folder name. Original folder names remain in
  the private report's `original_categories[].folders[].names`. Review/import
  any desired display override before the first create: adoption never rewrites
  an existing item's note.
- `inspect_inventory(snapshot, pid)` and `validate_receipt(receipt, pid)` are the
  **FOS-166-owned** offline readers. Saved active inventory plus matching valid
  receipt is historical readback evidence (`fresh:false`), never current proof.
- `ensure_raindrop` owns full active/Trash enumeration, exclusivity, pre-POST
  durable intent, crash recovery, fresh GET and receipt persistence. Backfill
  never implements those again or retries errors itself. Missing shared module
  stops with a clear error; there is no fallback authenticated client.

Land/review the source-link prerequisite first; integrate the FOS-166 adapter and
its existing-skill changes before executing repair-backfill. No live validation
is implied by either PR, local tests, saved inventory or merged code.

## Source links and immutable evidence

The shared parser accepts positive decimal strings without numeric coercion or
leading zeros; x.com and
 twitter.com with www/m/mobile; handle/status, i/status and i/web/status; optional
photo/N or video/N, queries and fragments. A known X-host unsupported path is an
error during dedupe, never silently skipped. Renames do not change identity.
Single trailing DNS dots normalize to the same host; unresolved t.co links and
non-HTTP(S)/malformed primary links block absence inference. See CATALOG.md for
the local normalization policy. Preserve existing bookmarks if unsupported X
profile/list/search links block; obtain a shared-contract disposition before
proceeding, never edit or omit owner bookmarks to clear the gate.

New archive payload/manifest rows have `source_post_id` and `source_post_url`.
Existing hashed manifests, payloads and media are never rewritten. A catalog
rebuild adds those fields to derived records and writes deterministic
`source-links.jsonl` alongside `catalog.jsonl` and `index.html`. The sidecar binds
each primary ID and canonical URL to the original manifest/payload receipts.
Legacy catalog reads add source fields in memory without changing the file.

Each HTML record has **Open original saved X post**, including media-only records.
Referenced posts have separately labeled links to their own IDs. Attachment lookup
returns the saved primary link and `attachment_post_url` separately. Quote lookup
can still find its containing record; repair always indexes primary `post_id`.
A source permalink preserves identity, not a claim the post is still live on X.

Media annotations remain in `annotations.private.json`. A `category_overrides`
object in that same file maps primary string IDs to owner-provided display labels.
Rebuild applies those labels without modifying the original folders/category in
any archive version. Existing locally managed category sidecars must be imported
into this explicit mapping during supervised local acceptance; their private
format is not available in this repository. Do not overwrite them or infer an
owner override from changed archive provenance. Save the complete existing
annotations file when adding the mapping; retain attachment entries unchanged.
Rebuild with all batches referenced by that annotations file. Overrides for IDs
outside a partial build fail closed; do not remove valid annotations merely to
make a partial build succeed.

## Offline audit

All examples below use synthetic placeholders. Source files must be owner-only
regular files. Audit output belongs in its own private directory, outside Git.
The command rebuilds an in-memory catalog from hash-checked restored batches and
original receipts; it does not trust an edited/stale catalog as archival evidence.
It writes private per-run reports and prints counts, not IDs, paths or content.
`repair-audit-<run-id>.private.json` and `repair-backfill-<run-id>.private.json`
share one random run identifier; each invocation preserves previous reports.
Output must not overlap the catalog, adapter ledger, restored batches, removal
directories, saved receipts or any input file (including mapped helper sources).
Symlink aliases and ancestor/descendant overlaps are checked before locks/writes.
Sibling private files may share a parent; keep output in its own child directory.

```sh
uv run x-migrator repair-audit \
  --catalog-dir /private/discovery --output-dir /private/repair \
  --batch /private/restored-a /private/receipts/batch-a.private.json \
  --removal-dir /private/native-ledger \
  --helper-format /private/helper-format.private.json \
  --expected-records 100 --all-removal-sources-confirmed
```

Repeat `--batch`, `--removal-dir` and `--helper-format` for every source. Add
`--raindrop-inventory /private/snapshot.private.json` and optionally
`--raindrop-receipt-dir /private/raindrop-ledger` to use the FOS-166 validators.
No provider call occurs in audit, including with saved Raindrop inputs.

Every native intent is read, not just successful/latest attempts. Each filename
must match the owner/post hash, and indices must be contiguous from zero within
each supplied ledger; a missing first/intermediate intent fails the scan. Keep
each native ledger intact rather than partitioning it across input directories.
An entirely absent ledger or missing final attempt still requires the explicit
all-sources inventory; sequence validation alone cannot detect that loss. Raw response
identity/hashes and existing result receipts are cross-checked. Missing response
means unknown; mismatched evidence stays unknown with a blocker. Orphan response
or result files fail the scan. The scanner never calls `result_for` because that
function may write a receipt. It uses the existing native outcome classifier.
If one primary has both confirmed and unknown attempts, the aggregate remains
unknown, with all attempts retained. Never retry historical DELETE.

### Private batch-helper evidence

The out-of-tree helper's real format is **not known remotely**. A local reviewer
must inventory all its intents (including failures/unknowns), document what its
fields mean, and map that existing evidence explicitly. The descriptor below is
an audit input, not another Raindrop ledger or a reconstructed deletion receipt.
It does not magically prove the reviewer supplied every source. Keep the original
private helper and receipts unchanged and independently review the mapping.

```json
{
  "schema_version": 1,
  "format_description": "Synthetic: all attempted DELETE rows; state from original receipts",
  "all_intents_included": true,
  "files": [{
    "path": "helper-evidence.private.json",
    "sha256": "<SHA-256 of original file bytes>",
    "record_count": 3
  }],
  "records_pointer": "/attempts",
  "post_id_pointer": "/id",
  "outcome_pointer": "/state",
  "outcome_map": {
    "removed": "confirmed_removed",
    "lost": "outcome_unknown",
    "denied": "rejected"
  }
}
```

Pointers are JSON Pointers; rows must be an array. Each file's count/hash is
checked; each row must have a primary string ID. Duplicate resolved source files
across descriptors are rejected rather than counted twice. Unmapped outcomes become unknown
and blocked. The report retains the descriptor hash, source file hash and row
index, plus the private format description. Invalid/missing IDs, inaccessible or
corrupt evidence fail rather than disappear from totals. Missing helper mapping,
no all-sources attestation or a population different from `--expected-records`
keeps coverage incomplete. The reported 25/32/43 checkpoint is not hardcoded truth.

### Classification and report

Every primary catalog ID and every intent-only ID gets a row. Orphan intents
remain visible as missing-primary-archive exceptions, never quote matches. Rows
retain source link, all original archive receipts/hashes/categories, display
overrides, deletion attempts/outcome, classification evidence, Raindrop state/IDs,
Drive/catalog links and repair blockers. The summary cross-tabs all five reporting
categories by confirmed removed / unknown / rejected / never attempted.
`non_media_missing_raindrop` means missing **verified readback**, including
unchecked inventory and active items without a matching receipt. The row state
distinguishes these from confirmed absence; they are not unrecoverable records.

The default is ambiguous and **Raindrop-required**. Empty text in every archived
post plus complete verified media can establish a positive media-only exception.
This automatic case is deliberately narrow: any retained t.co URL is text and
prevents exemption. It does not strip links or infer their meaning. For real
media-only records with such text, use a hash-bound `--classifications` review.
Emoji/hashtags with an image are not automatically exempt. Optional
`--classifications` supplies an owner-only JSON object keyed by primary ID:

```json
{
  "101": {
    "classification": "media_only",
    "basis": "owner_review",
    "reason": "Synthetic reviewed reaction",
    "content_binding": "<binding from this record's audit row>"
  }
}
```

Allowed classification: media_only, non_media, ambiguous; basis: owner_review or
archive_review. Binding is the digest of the sorted unique payload SHA-256 list
(encoded with the repository's canonical `encode`). A new content snapshot
invalidates an older decision. An explicit decision is an auditable assertion,
not an AI classifier. Missing/corrupt archival content stays blocked even with an
exemption. The report's `offline_reconciliation_complete` and `fresh:false` are
historical evidence checks; `live_acceptance` remains explicitly unestablished.

## Backfill through FOS-166 (later owner authorization)

`repair-backfill` takes the same fresh local audit inputs plus:

```sh
  --raindrop-ledger-dir /private/raindrop-ledger \
  --raindrop-script /absolute/existing-skill/scripts/raindrop.sh \
  --post-id 101 --post-id 102 --limit 3 --confirm-raindrop-writes
```

These are arguments to `uv run x-migrator repair-backfill`, not permission to run
it now. The first live pilot requires separate owner approval and independent
review of both PR heads. All selected IDs are checked before any provider call.
Only confirmed-removed/unknown primary IDs qualify. Exempt media-only records
make no Raindrop call. For every other selected record, invoke the one adapter's
ensure operation, which re-enumerates and freshly verifies even if the audit has
an old receipt. Existing notes/tags/favorites/collections are the adapter's
preserved state. Trash/conflicts/incomplete archival evidence block; 401/429,
partial inventory and uncertain create stop this pass, with no caller retry.
The default pilot limit is three; an explicitly reviewed limit can be at most 100.

`repair-backfill-<run-id>.private.json` is a run report embedding unchanged adapter results,
not a second durable create ledger. Blocked rows retain the adapter code, fixed
reason, numeric blocking item ID and whitelisted numeric provider status/retry
context (the adapter's bounded decimal strings normalize to integers in the
report); no exception message, URL, subprocess output or provider body is copied. A crash before that report is saved is
recovered by rerunning through the adapter's existing intent/receipt protocol.
The command has no X client or DELETE capability and does not reopen rejected or
never-attempted records. FOS-166 owns prospective migration for those records.

## Documentation and remaining acceptance

Read November 16: [Raindrop objects](https://developer.raindrop.io/v1/raindrops),
[single create/get/update](https://developer.raindrop.io/v1/raindrops/single),
[list/pagination](https://developer.raindrop.io/v1/raindrops/multiple), and the actual
[existing skill](https://github.com/aronfoster/openclaw-config/blob/master/skills/raindrop/SKILL.md)
and [script](https://github.com/aronfoster/openclaw-config/blob/master/skills/raindrop/scripts/raindrop.sh).
Primary `link`, object `collection.$id`, active 0 versus Trash -99 and maximum
50/page are documented. Search is not exact-ID lookup. Provider error handling,
full stable inventory and durable ensure/readback tests belong to FOS-166; FOS-216
consumes that implementation. No X endpoint/auth behavior changes here.

Before the separately authorized live create pilot, run the shared read-only
Raindrop inventory, resolve any unsupported-link block without modifying owner
metadata, and republish the catalog/gallery with `source-links.jsonl`. Schedule
curation outside adapter/gate operations that hold the catalog lock. The pilot
must also confirm immediate post-create list visibility, count and Trash shapes.

Required local evidence after independent review: every original helper intent
accounted for; real 100-record hashes/counts; current saved Raindrop inventory and
readback receipts; owner-approved small create/GET pilot and duplicate-free rerun;
then bounded repair with every confirmed/unknown primary either freshly readable
in Raindrop or a positive media-only exception with catalog/source retrieval.
Check text/photo/video/quote/repost source links and preserve existing curation.
Keep unknown outcomes unknown. Report sanitized counts in Linear. Keep FOS-168
blocked and live X DELETE paused until both recovery and prospective gate are
accepted. Do not infer private-helper retirement from repository changes.

## Native-delegating wrappers

When reviewed private helpers delegate all attempts to native `apply_plan`, use
`--native-helper-provenance FILE` instead of manufacturing duplicate helper rows.
The local November 16 audit reports this situation. Original native run reports
use `/outcomes`, `/post_id`, `/attempt`, `/state`; verify their actual bytes first.
Example (all names, IDs and hashes below are synthetic):

```json
{
  "schema_version": 1,
  "native_delegation_reviewed": true,
  "all_reports_included": true,
  "reviewed_by": "local independent reviewer",
  "format_description": "Reviewed wrappers delegate exclusively to native apply_plan",
  "helper_sources": [{"path": "wrapper.py", "sha256": "<64 lowercase hex characters>"}],
  "files": [{"path": "run.private.json", "sha256": "<64 lowercase hex characters>", "record_count": 2}],
  "records_pointer": "/outcomes",
  "post_id_pointer": "/post_id",
  "attempt_pointer": "/attempt",
  "outcome_pointer": "/state",
  "outcome_map": {
    "unbookmarked": "confirmed_removed",
    "unbookmark_outcome_unknown": "outcome_unknown",
    "unbookmark_rejected": "rejected",
    "blocked": "not_attempted"
  }
}
```

List every source/summary marker whose code/provenance was reviewed in
`helper_sources`, and every original run report in `files`. The checker binds
original hashes, counts every row, compares IDs/outcomes to native intent evidence,
and requires one-to-one coverage of the supplied native intents. A reviewed
`not_attempted` row must have no attempt (missing/null); it is retained as a
pre-intent stop, never a synthetic removal. Unknown/missing/repeated/mismatched
references fail closed. Independent non-native attempts still require the original
`--helper-format` contract. All-source attestation remains separately required.
Code hashes prove which bytes were reviewed, not that every invocation site was
found or disabled. Retirement is **not** certified by this descriptor.

For the full post-backup publication, actual-reader check, inventory, one-item
pilot and bounded repair sequence, follow [READINESS.md](READINESS.md). Use
`--expected-records 103` with the complete eight-batch corpus, not the older
100-record-only example above.
