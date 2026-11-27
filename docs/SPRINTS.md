# Sprint execution plan

Owner-directed scope, 18 November 2026. [ROADMAP.md](../ROADMAP.md) owns releases
and milestone status. This file owns execution order. Target windows in the
ROADMAP schedule are provisional one-week targets; do not create calendar events,
scheduled automation or live permissions from them. A sprint is not complete
until its exit evidence exists.

## Session and sprint discipline

### Historical drain and daily intake

[FOS-169](https://linear.app/fosterstech/issue/FOS-169) owns the live counts,
protected desktop handoff, repair candidates and window observations. Continue
from its latest receipt. Remove eligible items after fresh item-specific checks.
Restore and verify each repaired successor before promotion. Record unique IDs
observed outside the catalog and which ones actually reach the archive.

[FOS-168](https://linear.app/fosterstech/issue/FOS-168) owns daily intake.
Its next exit is one later save discovered by the scheduled worker with history
pending. When new-save discovery is due, the timer checks a bounded first window. Keep
historical pagination, paused-stream probes and their restart decisions with
the FOS-169 drain. The timer can process eligible queued items within the
worker limits. Daily intake alone does not authorize a deep historical scan.

### Working rules

1. Select one current-sprint deliverable and name the existing implementation/runbook.
   Keep one active implementation owner per file/slice. Do not start v2 during v1
   unless the owner changes priority. Read latest private receipts before local work.
2. Record only concrete unknowns needed for that deliverable; investigate owned
   producer/consumer/configuration before proposing owner commands. Reuse proven work.
3. Distinguish remote preparation from local execution, and code checks from live
   acceptance. Review new search/curation designs before implementation; prior
   accepted backup/transaction designs do not need another generic review cycle.
4. Close with a compact record: sprint/milestone, revision, test or private receipt
   reference, result, remaining exception, next actor. Update roadmap status in the
   same PR when appropriate; put only a short link/status journal in Linear.
5. At the end of each timebox, compare actual output with the exit test. Carry
   incomplete named work forward, explain why, and revise later dates. No invented
   percentage complete, automatic closure or claim of full coverage from one page.

## S1 — recover existing state

**M1. Tracker: FOS-169.**

- Bind the existing captured generation to its producer receipt, actual manifest
  schema and independent signer anchor. Reuse the preserved desktop keys and
  existing verifier; add only missing checks demonstrated by the actual format.
- Preserve independently accessible owner recovery material. Complete signature,
  successful-decryption, exact member-set, directory, file-size and hash checks.
- Inspect actual Déjà Dup baseline, permissions, includes/excludes, backend,
  retention effects and quota. Add only the ciphertext publication directory if
  needed. Preserve original data, existing backup settings/jobs and ordinary rclone.
- Restore that exact generation from a completed remote snapshot into a distinct
  destination; repeat all checks with independent recovery material.

**Exit:** every [BACKUP_RECOVERY.md](BACKUP_RECOVERY.md) acceptance row passes.
Local decrypt and old marker restores do not count as full recovery. The retired
source machine is not needed. No repeat setup/capture/key transfer.

Independent off-host restore, key recovery and staging cleanup passed for the
captured generation. Preserve the approved separate GPG recovery package and
existing backup scope. The owner's password database remains outside this work.
FOS-168 verifies backup coverage for a subsequent save and catalog update.

## S2 — publish, reconcile and operate

The worker and systemd unit implementations merged. The installed worker has
confirmed real removals. The catalog is published, historical Raindrop links
are repaired, and representative Mnemosyne retrieval has passed. Retain the
existing [worker contract](WORKER.md) and [repair procedure](READINESS.md).

### Scheduled new-save acceptance (FOS-168)

1. Trace the normal systemd invocation, mode, installed revision and discovery
   path. Verify that a due new-save check discovers the later save with an
   eligible historical item queued, an item waiting for retry, or a historical
   stream paused. Preserve provider reset times, historical cursors and per-item
   holds. Record the installed revision with the result.
2. Under the existing host and provider authorizations, observe the timer pick
   up a genuinely later save with history still pending. Follow that same
   primary through durable local and Drive archive, catalog publication,
   verified Raindrop pointer and confirmed X removal. Keep incomplete items on X.
3. Verify restart preserves progress without duplicate publication or removal.
   Confirm the later generation reaches the existing backup. Retain the
   scheduled invocation and item receipts privately. Pass the stable reference to
   FOS-220 and FOS-170 for actual retrieval, one approved edit and rebuild.
   The owner rollout manifest linked in FOS-168 covers service-account permissions, controlled enablement and catch-up.

During unattended rollout, preserve per-item holds, rate-limit recovery,
locking and fresh archive and discovery proofs. An uncertain Raindrop create
requires readback and reconciliation. Preserve GET evidence, repair failed local
publication before requeue, and reconcile an uncertain X removal before retry.

### 19 November formatting checkpoint

Owner-approved default for subsequent migration:

- New Raindrop notes contain only the first 250 Unicode characters of the saved
  primary post text, without an ellipsis. Never substitute a referenced post.
  The owner rejected archive JSON links and numeric category notes as unhelpful.
- Preserve existing Raindrop notes and metadata on adoption. Explicit preview
  edits are separate from the create/adopt path.
- Persist verified Raindrop receipts as local navigation mappings. Project direct
  links into catalog records and HTML; preserve mappings through partial archive
  builds, merged worker publications and full rebuilds.
- A recovery POST must retain the same intended note. Legacy intents lacking the
  note require review rather than silently changing the recovery payload.
- Exit tests: adapter truncation/adoption/recovery, exact-primary text selection,
  historical backfill and worker link persistence, annotation preservation and
  full regression checks. Live installation is separate from local tests.

The accepted preview is complete. On 20 November, the owner attested that only
the two audited helpers performed automated removals of these captured bookmarks,
resolving the earlier “probably” checkpoint. This is owner confirmation, not
independent verification. The owner subsequently completed the historical
backfill. Helper retirement still requires its own evidence.

### Historical migration (FOS-169)

Use the active desktop session for live work. Reuse verified receipts for
catalog publication, the completed one-item trial and installed-worker removals.
Retain original evidence, reconcile old removal paths with FOS-166/FOS-216,
and keep per-item archive, category, external retrieval and primary-link gates.
Group incomplete records by recoverable cause, try retained evidence first and
measure changes to the visible window after each bounded batch. Follow the S4
stop rule for stalled history while daily new-save checks keep their own cadence.

Count distinct saved primaries per primary reason and retain every overlapping
blocker on the record. FOS-169 holds the current counts and protected evidence.
Keep newly observed, captured, archive-complete and confirmed removed totals separate.

| Primary reason | Evidence required to advance |
| --- | --- |
| Missing author or reference | Bind the exact missing identity to retained or targeted official evidence. Preserve the predecessor and independently restore the successor before promotion. A diagnostic replay alone leaves the record incomplete. |
| Media origin | Establish the relationship between the attachment and its containing post. Media bytes alone cannot establish origin. |
| Media byte or count limit | Preserve every declared attachment within the reviewed limits. Verify the complete content, Drive readback and independent restore for each one. A CDN size check or partial set leaves the record blocked. |
| Article or missing media | Account for the obtainable Article body and each referenced cover or attachment key. Require matching media evidence and verified stored bytes before clearing the corresponding blocker. |
| Unknown folder | Require positive placement evidence. Absence from a capped folder page cannot establish unfiled status. |
| Archive verified, removal pending or uncertain | Recheck exact primary membership, archive, retrieval and the applicable Raindrop gate. Reconcile an uncertain prior attempt before retry. Count a removal only with confirmation. Preserve repair-specific removal holds. |

The [owner's prepaid-X direction](https://linear.app/fosterstech/issue/FOS-169)
authorizes migration and testing with auto-reload off. Record calls and outcomes
and measure effective rates from provider billing afterward. Before any live
worker pass, follow the [worker billing procedure](WORKER.md#modes-and-configuration):
record the reviewed Console observation and check cumulative and per-run headroom.
The worker enforces a $20 lifetime ceiling across the retained reservation ledger.
At a local spend-cap stop, preserve the ledger. Use the optional offline
`billing-cutoff` / `billing-settle` procedure only with the required owner-attested
provider evidence. Settlement reconciles overlap and retains the ceiling. If
the next batch still exceeds headroom, the worker needs a reviewed change to
use more of the authorized balance; topping up or resetting the ledger cannot
release that stop.

## S3 — search and categorization readiness

**M3. FOS-220 owns search, FOS-170 owns approved edits, and FOS-223 records plan acceptance.**

The [catalog and description contract](CATALOG.md#reviewing-legacy-descriptions-and-rendering-drive-search-text-offline)
and [recoverable Drive publisher](https://github.com/aronfoster/x-bookmark-migrator/pull/23)
are merged. Freeze the private query set before tuning. Start with published
records and run remembered-text and media queries through Mnemosyne. Exercise
the live publication route and native Drive search on desktop and Android.
Repeat on the later scheduled save for the shared demonstration. FOS-170 owns
one approved correction and its persistence after republishing. FOS-220 owns
ordinary search finding and opening the corrected item across applicable surfaces.

- Use the existing catalog and exact identity mapping to provide searchable full
  preserved text, category/annotation fields and useful per-asset descriptions.
  Determine missing descriptions/OCR or retrieval logic from measured failures.
  Keep generated descriptions distinct from facts and owner-reviewed categories.
  Use the S1 producer decision and estimate generation/storage cost separately
  from X API costs; existing access is not blanket private-content or spend consent.
  If no authorized producer meets the query set, present local-tool, existing-model
  and owner-written options with measured tradeoffs before new processing. A few
  hand-described samples do not establish full-corpus search acceptance.
- Implement the smallest reviewed OpenClaw retrieval path that passes vague-memory
  queries and returns actual media, not just identifiers or invented descriptions.
- Make ordinary Drive search useful with a proven human-readable/searchable
  per-record representation designed in S1, including annotation updates and
  idempotent rebuilds. Test native desktop/Android search and indexing latency; a
  `files.list` match or uploaded JSONL alone does not prove native UI acceptance.
- Prove Raindrop discovery with required exact-source pointers and existing
  categories/metadata. Use the shared adapter and preserve notes/tags/collections;
  do not turn full article text into Raindrop notes or require a paid tier silently.
- Use the accepted [manual categorization plan](V1_ACCEPTANCE.md#manual-categorization-plan)
  with a concrete rehearsal, state/approval examples and a resumable next batch.
  FOS-170 implements [single-bookmark description corrections](V1_ACCEPTANCE.md#correct-one-bookmark-description);
  FOS-220 verifies ordinary Raindrop search finds the corrected item by description alone.
  Scope the later implementation separately; do not require sorting the whole
  corpus or a hosted gallery before v1.

**Exit:** every M3 row of the acceptance matrix passes against the published
corpus, the representative query set has recorded results, and the categorization
plan has no unresolved decision that prevents starting a bounded review session.
No fixed search architecture, hosted model or vector database is presumed.
Live reads/writes or model processing stay within explicitly authorized scope.

## S4 — complete X migration and accept v1

**M4. FOS-169/FOS-113.**

The M2 worker may already drain during S3; S4 owns measured historical closeout.

- Process bounded main/folder windows, starting small and increasing only from
  observed safe results and spend. Reconcile billing without resetting reservations.
- Rescan fresh windows after removals; compare newly exposed IDs. Never reuse
  invalidated cursors or infer unfiled membership from truncated folder windows.
- **No-progress stop — window stall:** at any stage, pause further paid historical
  probes for the stream when a successful post-removal comparison shows no ID
  absent from the before-window, or every visible ID is blocked. A newly visible
  ID counts as window progress even if it was already in the frozen inventory.
  **Hidden-history test:** only after that stream's unprocessed baseline records
  have all completed migration or have explicit blockers, a successful comparison
  with no IDs outside the frozen baseline pauses hidden-history probes and triggers
  a coverage escalation. An empty outside-baseline partition before that point
  does not trigger this second condition. Failed/missing comparisons remain
  unverified and follow existing read-only reconciliation bounds. Diagnose saved
  evidence first; restart needs a recorded changed condition or an explicitly
  authorized bounded probe with request/spend limits. Use this default until a
  different bounded probe is authorized. It is not proof of exhaustion and
  does not invalidate eligible known records. Investigate documented official
  API/export alternatives without assuming they contain bookmarks or a reliable
  total. New data access/import or changed policy needs its own authorized scope.
  Escalate only the surviving budget, coverage or policy choice with evidence.
  Separately authorized daily new-save checks retain their existing cadence and
  budget; they must not silently restart stalled historical probes. If the worker
  cannot enforce that distinction, keep the affected job paused pending the S2
  correction, rather than claiming both stop and continuing intake are proven.
- Retain permanent blockers without repeatedly paying to hydrate them; back off
  transient failures and record next retry. Revisit the [S2 reason table](#historical-migration-fos-169) as new
  classes appear and complete justified fixes; never roll fixable work into an
  unexplained owner waiver. Report known discovered/archived/
  searchable/removed/blocked counts, with globally deduplicated totals by stream.
- Reconcile every removal to its preserved text/media/category, current retrieval
  and transaction proof. Index new batches in all applicable v1 surfaces.
- Demonstrate later new-save intake and continuing backup coverage. Repeat the
  search acceptance set on the expanded corpus, including newly arrived items.
  Reconcile retained receipts against fresh authorized stream observations at
  closeout; a historical catalog is the baseline, not a fresh all-history inventory.
  Reuse matching proof instead of rehydrating every record. Unavailable streams
  remain unknown under the no-progress rule, not an excuse for an unbounded scan.

**Exit:** all known bookmarks accounted for, reachable eligible history processed,
M1–M3 maintained, and v1 acceptance signed off. A blocked/invisible remainder
prevents an unqualified “all migrated” claim. Record exact known exceptions and
unknown coverage separately; only an explicit owner disposition can accept a
limited release. Do not delete incomplete items to expose older pages. Report a
measured ETA only after throughput, remaining reachable population and cost exist.

## S5 — private hosted gallery (v2)

**After v1. M5. FOS-221.**

First deliver a small proof on desktop and Android: private thumbnail, full-size
image, video playback/seeking, download, account selection and expired credentials.
Use browser network evidence to show media bytes travel directly from Google to
the device, never through the GCP VM. Do not make files public to bypass auth.

Then build the responsive grid, search/type/category filters, stable item links,
source context and downloads using the existing catalog. GCP serves app/catalog;
originals stay in Drive, with small derived thumbnails stored there if needed.
Inspect an existing owner GCP VM's headroom (identified privately in FOS-221) and
isolate access before selecting deployment.

Drive API authorization is distinct from sign-in. Generated thumbnails avoid
reliance on expiring built-in thumbnail links. Inline private video and seeking
need real browser proof; a Drive-viewer fallback is a documented UX tradeoff,
not an automatic pass. Do not download a whole large video into mobile memory as
an unexamined substitute for playback. Record quotas and actual costs before scale.

**Exit:** owner-accepted private desktop/mobile browsing, no duplicate GCP media
store or GCP media egress, and automatic catalog updates without OpenClaw running.
The proof is feasibility work, not evidence already obtained.

Reference contracts checked 18 November 2026; recheck before implementation:
[Google browser OAuth](https://developers.google.com/identity/oauth2/web/guides/use-token-model),
[Drive downloads](https://developers.google.com/workspace/drive/api/guides/manage-downloads),
[thumbnail constraints](https://developers.google.com/workspace/drive/api/reference/rest/v3/files),
[Drive video](https://support.google.com/drive/answer/2423694),
[Drive quotas/pricing](https://developers.google.com/workspace/drive/api/guides/limits).

## S6 — additional bookmark sources (v2)

**After v1; follows S5 by default. M6. FOS-222.**

Select one concrete source, with Reddit/TheMotte as charter candidates. Establish
supported official API/export coverage, access and cost. Preserve source-qualified
identity, original URLs/text/media/categories and provenance. Keep different source
appearances separate even when they refer to the same external page or media.
Keep source-specific code placement explicit; do not automatically add parsers
to the X repository. Reuse archive/search/Raindrop/curation contracts; no generic adapter framework
without demonstrated need and no reinterpretation of existing X IDs/receipts.

**Exit:** one real-source bounded import and retrieval pilot, idempotent rerun,
explicit coverage/blocked inventory, and unchanged X regression behavior. Source
removal requires its own plan and owner authorization; X permission never transfers.
