# Sprint execution plan

Owner-directed scope, 18 November 2026. [ROADMAP.md](../ROADMAP.md) owns releases
and milestone status. This file owns execution order. Target windows in the
ROADMAP schedule are provisional one-week targets; do not create calendar events,
scheduled automation or live permissions from them. A sprint is not complete
until its exit evidence exists.

## Session and sprint discipline

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

**Remote work available now:** FOS-220 designs the fixed query/coverage set and a
per-record Drive searchable unit linked to immutable originals, with source/asset
IDs, text, categories, descriptions and links. Specify its format, stable file
mapping, idempotent update/rebuild path after annotation edits, and native UI
indexing probe; a derived text sidecar is the starting candidate, not a proven
Drive representation. Select a description producer within existing authorization
and record any genuine privacy/spend decision before processing private content.
FOS-223 reviews the category queue/rehearsal in [V1_ACCEPTANCE.md](V1_ACCEPTANCE.md).
These tasks do not require private recovery keys or wait for M1. No private
content goes to public fixtures.

## S2 — publish, reconcile and operate

**M2. Trackers: FOS-216, FOS-166, FOS-169.**

1. After M1, use the existing protected rollout to install the accepted readiness
   release. Publish the complete catalog/HTML/source sidecar/annotations; preserve
   old catalogs and curation. Run archive-bound writer checks and actual-reader
   checks; open representative text, images and videos at final paths.
2. Audit real native/private helper sources and all attempts; preserve original
   evidence, distinguish pre-intent stops, and prove old entry paths retired or
   exclusively delegated to the hardened gate.
3. Follow [READINESS.md](READINESS.md): separately authorized complete active/Trash
   inventory, one-record create/adopt/GET/immediate-inventory/idempotent rerun,
   then bounded historical backfill, initially at most three IDs. Stop on conflicts
   or uncertain results; never invent receipts or blindly retry provider writes.
4. Accept FOS-166's prospective gate separately from FOS-216 historical repair.
   Run the existing worker dry-run, authorized non-destructive live mode, then a
   separately authorized one-item full transaction. Verify post-removal retrieval,
   billing, restart/overlap/429/unknown-outcome behavior on the intended host.
   Record the frozen inventory baseline and before/after window partitions,
   including `not_previously_inventoried_ids`, with the owner's quiet-window
   observations. New IDs alone do not prove age or complete history; preserve
   `older_bookmarks_exposed: unverified`. Reuse valid earlier receipts; obtain only
   missing current-policy evidence. A zero/unknown result triggers the S4 pause,
   not more removals merely to test a theory about hidden history.
5. Validate/install the existing non-root units, then separately authorize timer
   activation and observe a real scheduled invocation. Account for immediate
   catch-up. Review recurring capture/retention binding so changing catalogs and
   ledgers remain covered by the existing backup system; no speculative new backup.

**Before expanding the drain or enabling unattended discovery (FOS-169):**

- Build a reason/count table from existing per-item blockers, failure reasons and
  stream reports. Separate code-fixable, transient, evidence-unknown and current
  policy/API limitations. Include content, category and reference-depth failures;
  do not infer irrecoverability from the worker's permanent-block label. Fix
  measured code-fixable classes with the existing modules and focused tests,
  prioritizing affected counts; record owners and exit tests for remaining fixes.
  Reprocess saved evidence before paying for another hydration. For Articles and
  other current-policy limits, investigate documented completeness first, then
  present the known impact/options to the owner early. Default: retain on X and
  keep v1 incomplete; a scoped release or policy change requires owner choice.
- Compare cumulative reservations with separately observed Console charges and
  compute conservative cost for the next bounded batch, plus a range for known
  reachable work, including rescans, hydration and retries. Unseen population and
  unmeasured rates remain unknown; do not invent a whole-history total. Low actual
  spend does not release reserved headroom. If the next batch cannot fit, pause it
  and present evidence-backed options: separately reviewed reconciliation or a
  new owner-approved ceiling, continued pause, or an explicitly scoped release.
  Recommend only what measured costs support; never reset reservations silently.
- Verify that no-progress pauses survive restart and timer invocation before
  unattended scaling. Existing stream status reports alone do not enforce this
  rule. Reuse current controls, or prepare the smallest reviewed worker correction
  under FOS-169; if a stream cannot be isolated, keep its containing discovery job
  paused. Independent known-item work proceeds only within its existing gates.

**Exit:** actual reader access, historical and prospective discovery acceptance,
complete live transaction and scheduled-run receipts, preserved curation and
continuing recovery evidence; blocker triage/actions, exposure result and stop
behavior, and affordable next-batch scope or an explicit budget pause. Unresolved
history coverage remains open even if the bounded operational pilot passes.
Follow [WORKER.md](WORKER.md); FOS-168's merged
code is reused, not reimplemented or treated as proof of installed acceptance.
All already-required live permissions and spending limits remain in force.

## S3 — search and categorization readiness

**M3. Trackers: FOS-220 and FOS-223.**

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
- Accept the [manual categorization plan](V1_ACCEPTANCE.md#manual-categorization-plan)
  with a concrete rehearsal, state/approval examples and a resumable next batch.
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
- **No-progress stop:** after the first successful bounded post-removal comparison
  with no IDs outside the frozen inventory baseline, or a blocked-window stall,
  pause further paid historical probes for that stream. Failed/missing comparisons remain
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
  transient failures and record next retry. Revisit S2's reason table as new
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
