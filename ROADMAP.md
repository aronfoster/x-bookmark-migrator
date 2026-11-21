# Bookmark project roadmap

Updated 21 November 2026 (America/Denver). Owner-directed v1/v2 scope.

## Planning home and working rules

**GitHub is the primary planning home for this project.** This is the owner's
18 November project-specific change to the earlier Linear-first arrangement.

- **This file:** product scope, milestones, priorities and current checkpoint.
- **[SPRINTS.md](docs/SPRINTS.md):** ordered work, dependencies, exit evidence and
  next actions. **[V1_ACCEPTANCE.md](docs/V1_ACCEPTANCE.md):** search acceptance
  and the manual categorization plan. Existing runbooks retain exact procedures.
- **GitHub PRs:** proposed changes, review and sanitized implementation evidence.
  Accepted main is the baseline; proposed revisions live in their linked PRs.
  Explicit owner decisions take precedence even before merge. Use commit-pinned
  links for pending plans; after merge, update active Linear plan links to main
  and retain reviewed commit links in the journal.
- **Linear:** a short outcome/acceptance summary, status, next actor, dependencies
  and links to the exact GitHub plan/evidence. Do not duplicate sprint plans or
  append competing instructions there. Private receipts/commands remain in their
  existing protected handoff; this public repository never receives private data.

Start a session by reading AGENTS.md, this roadmap, the relevant sprint/runbook
and the linked ticket's latest checkpoint. Name one deliverable and its exit test.
Update the existing plan in place when scope changes; leave history in commits
and concise journals. Finish by recording what passed, what remains, the next
actor and an evidence link. A merge is not installation or user acceptance.

## V1 — X migration and usable retrieval

**Get all the owner's bookmarks out of X, preserved and fully searchable from
OpenClaw/Mnemosyne and Google Drive, with Raindrop included.** Continue processing
new X saves indefinitely. V1 also includes a concrete, resumable plan for working
through manual categorization. A hosted gallery is not required for v1.

| Required outcome | What acceptance means | Tracking |
| --- | --- | --- |
| Complete X migration | Preserve complete obtainable text/media, exact original source and category evidence; safely unbookmark only verified items. Triage and resolve fixable blockers, measure post-removal window progress and cost before scaling, and account for all known streams and exceptions. A sample or an empty observed API page is not completion. | [FOS-169](https://linear.app/fosterstech/issue/FOS-169), FOS-164 |
| Recovery and sustained operation | Prove independent off-host restore, one complete live transaction, scheduled non-root operation, later new-save processing and continuing backup coverage. | FOS-169; existing FOS-168 worker contract |
| Full-corpus OpenClaw search | Find text and vaguely remembered media using ordinary language without knowing IDs or filenames; open the real saved content and original context. | [FOS-220](https://linear.app/fosterstech/issue/FOS-220) |
| Google Drive search and access | Search meaningful text/category/media-description terms in ordinary Drive on desktop and Android, then open the matching preserved content without OpenClaw. Raw JSONL upload alone is not evidence. | FOS-220 |
| Raindrop discovery | Find required original-source pointers with useful concise metadata, category/tag search and archive access; preserve existing entries and annotations. The existing affirmative media-only exception remains. | [FOS-166](https://linear.app/fosterstech/issue/FOS-166), [FOS-216](https://linear.app/fosterstech/issue/FOS-216), FOS-220 |
| Manual categorization plan | A finite review queue, small conversational batches, approval/defer/revisit rules, durable progress, reversible edits and a worked rehearsal. Finishing every manual review is not a v1 release condition. | [FOS-223](https://linear.app/fosterstech/issue/FOS-223) plan; FOS-170 execution |

“Fully searchable” means complete preserved-corpus coverage and demonstrated
useful retrieval, not perfect answers to every conceivable query. The observable
query/coverage tests live in [V1_ACCEPTANCE.md](docs/V1_ACCEPTANCE.md). Search
must improve beyond exact-ID lookup; a particular embedding engine is not mandated.
Raindrop holds concise pointers, not full article bodies or a silently required Pro
subscription. Original text/media remains in Drive.

**All bookmarks remains the goal.** Reachable-history progress is an intermediate
milestone. Inaccessible, incomplete or category-unknown items remain explicit and
saved on X where possible. If official access prevents complete migration, v1
remains incomplete unless the owner explicitly accepts a documented exception;
never quietly redefine “all” as “the first accessible window.” New-save operation
continues after the historical migration is accepted.

## V2 — hosted gallery and additional sources

| Feature | Required result | Tracking |
| --- | --- | --- |
| Private hosted visual gallery | Desktop/Android grid, search/filter, image enlargement, video playback, download and stable item links; independent of OpenClaw. GCP serves application/catalog only; thumbnail and original media bytes travel directly from Drive to the browser. No duplicate originals collection or GCP media proxy. | [FOS-221](https://linear.app/fosterstech/issue/FOS-221), sprint S5 |
| Sources beyond X | Add supported bookmark imports from other services to the same preservation, search, Raindrop and categorization workflow. Reddit/TheMotte are existing candidates; choose one concrete source for the first pilot. | [FOS-222](https://linear.app/fosterstech/issue/FOS-222), sprint S6 |

Additional-source planning lives here; code placement is a separate source-specific
decision. Do not add Reddit/TheMotte parsers to the X migrator by implication.

The gallery starts with a private desktop/Android proof of direct Drive thumbnail,
image and video delivery, OAuth/expiry handling and playback/seeking. Generate
small thumbnails in Drive if necessary; do not depend on expiring thumbnail URLs
or assume Google sign-in authorizes API access. Inspect an existing owner GCP VM
(identified privately in FOS-221) before choosing hosting. Network evidence must
prove media bypasses the GCP VM.
Inline video behavior is unproven; a Drive-viewer fallback must be evaluated openly,
not silently counted as seamless in-page playback. See S5 for reference contracts.

Existing non-X bookmarks already in Raindrop can be reviewed under the v1
categorization plan; that does not implement v2 ingestion from other services.
[FOS-171](https://linear.app/fosterstech/issue/FOS-171)'s authored Wiki remains a
separate later backlog track, with its own versioned backups. FOS-172's reversible
incidental-media hiding supports browsing/curation without blocking migration.

## Milestones and sprint schedule

One-week planning timeboxes, starting 18 November; these are planning targets,
not promises of owner availability or automatic deployment dates. Only S1 is
current. Later dates are provisional; failed exits carry named work forward and
shift subsequent windows. S4 repeats as needed rather than inventing a drain ETA.

| Sprint | Target window | Milestone / demo | Status |
| --- | --- | --- | --- |
| S1 | 18–24 Nov 2026 | **M1 Recoverable:** existing bundle restored off-host and completely verified; S3 search/curation designs ready for focused review. | Recovery/restore/cleanup passed; search/curation preparation remains |
| S2 | 25 Nov–1 Dec 2026 | **M2 Operational:** complete catalog visible, historical repair and prospective gate accepted, one live worker pass and scheduled run proven; blocker triage, exposure evidence and budget checkpoint recorded before scaling. | Planned; requires M1 for live rollout |
| S3 | 2–8 Dec 2026 | **M3 Findable and organizable:** OpenClaw/Drive/Raindrop search acceptance and categorization plan/rehearsal pass. | Planned; design can proceed during S1 |
| S4 | 9–15 Dec 2026, then repeat if needed | **M4 V1 accepted:** all-history reconciliation, continuing intake/recovery and v1 acceptance matrix pass; owner dispositions for any irrecoverable remainder. | Planned; no fixed release date |
| S5 | After v1 acceptance | **M5 Gallery:** private direct-Drive media browsing on desktop and Android. | V2 backlog |
| S6 | After v1 acceptance; sequence after S5 by default | **M6 Multi-source:** one additional source works through preservation/search/curation without breaking X. | V2 backlog |

Sprints group deliverables; their milestones require evidence, not calendar expiry.
M2's worker can drain eligible history while S3 search work proceeds. M3 is a v1
release requirement, not a new per-item X DELETE gate. Categorization completion,
v2 hosting and Wiki construction never hold the safe migration path hostage.
S2 identifies fixable content/category blocks and records policy-limited cases
early, including Articles; it does not defer all blocked work to a final waiver.
No-progress streams pause paid historical probes until diagnosis and a bounded
next step are recorded; authorized new-save checks keep their separate cadence.
Existing receipts are the starting evidence, not proof of today's
full coverage. Budget projections separate reservations from observed charges and
leave unknown population/cost explicit; no automatic ceiling increase or reduced
release scope follows from a failed checkpoint. See S2/S4 for the stopping rule.

## Current checkpoint and next actions

### 21 November — improve the migration path before resuming

Recovery, independent restore and temporary-material cleanup passed. The complete
captured catalog is published: 103 records and 117 media files, with owner-attested
OpenClaw/Mnemosyne retrieval. Do not repeat those completed demonstrations.

Historical Raindrop repair has 19 completed records of the 25 known removals.
One additional pointer was created and identity-verified, but its local link was
not published. Five remain unprocessed. Saved create evidence shows only an
edge-whitespace note difference (250 submitted characters, 249 returned, equal
after trimming). The failed later GET body was not retained. Original receipts
and the stopped run remain evidence, not a reason to repeat POSTs.

The owner directed an efficient, resumable shared repair path before more live
work: one validated inventory per bounded run, targeted item readback, tolerant
note-edge comparison, per-item local checkpoints and useful progress/failure
reports. This replaces repeated pilot-style whole-library checks in historical
repair. Implementation and synthetic checks are not protected installation or
live completion. [S2](docs/SPRINTS.md#s2--publish-reconcile-and-operate) owns the
exit tests and remaining work.

**Next outcomes:** finish the historical 25; retire or gate the audited removal
helpers; accept the existing worker under current policy; process the 32 known
archived-but-not-removed main records and resolve measured blockers among the
43 blocked main records (plus two blocked earlier pilots). These populations
are not all X history. Continue to measure window exposure, spending and explicit
exceptions, and complete the existing search/categorization acceptance. No new
X removal, scheduling or spending authority follows from this checkpoint.

### 19 November Raindrop formatting decision

The owner accepted the revised one-bookmark preview and directed subsequent
migration work to use the same format: the saved primary post's first 250
characters as the note, with a direct Raindrop link in its local catalog record
and index. This replaces the rejected JSON-link/numeric-category note; original
archive and category evidence remain local. See the S2 formatting checkpoint.
The preview update and link persistence passed owner-run checks across the
103-record, 117-media catalog. On 20 November, the owner attested that only the
two audited helpers performed automated removals of these captured bookmarks;
this is owner confirmation, not independent verification.
Next is a separately authorized bounded Raindrop-only backfill; helper retirement and X-operation
acceptance remain separate.

The following 18 November checkpoint is historical; do not repeat completed
recovery or publication work solely from this earlier snapshot.

Baseline: main `9b3ce5e`, merged worker #10, readiness #11 and recovery preparation
#13. Local evidence is owner-reported, not a new remote audit. Effective Raindrop
skill, dedicated runtime/protected build, staged complete catalog and initial
encrypted capture exist. On 18 November, owner-run full local signature,
decryption, exact member/directory/size/hash verification passed. A separate
symmetric recovery-key backup also passed local encryption/readback verification,
using the existing independently accessible GPG passphrase. The owner added only
the ciphertext publication directory to existing Déjà Dup coverage; the agent
verified the settings delta. Quota and local disk headroom passed the bounded
check. Completed remote backup, independent off-host key recovery, full restored
bundle verification and protected staging cleanup remain outstanding.
Publication, live Raindrop acceptance/repair and the current-policy scheduled run
are not established. Complete search acceptance is also not established.

**Paused at owner request; S1 is not complete.** The final settings check still
showed the earlier successful backup. A new backup was requested but its start or
completion was not established. On resumption, inspect existing backup activity
and completion first; do not start a duplicate job. Then complete the existing
backup and restore the pinned generation and separate encrypted recovery-key
file from a completed remote snapshot into a distinct destination. Reverify with
fresh recovery material and resolve protected staging cleanup. See the
[S1 checkpoint](docs/SPRINTS.md#s1--recover-existing-state).

**Owner decision:** the existing independently accessible password database is
accepted and must not be accessed, modified, copied or used for attachment work.
The withdrawn vault helper was never executed and was removed. Back up the GPG
recovery material separately; no new password, repeated capture, key transfer or
retired-host dependency.
**Next remote action (S1 preparation for S3 / FOS-220 and FOS-223):** review the
query/coverage contract and categorization plan, then resolve only demonstrated
implementation gaps. Keep the existing local recovery path moving independently.

## Boundaries preserved across releases

- Official authorized source APIs only; no private GraphQL or account scraping.
- Preserve immutable originals, original categories, receipts and owner curation.
  Exact primary source identity is distinct from quoted/reposted/attachment IDs.
- Before X DELETE: complete archive/content/category/readback proof, actual external
  retrieval, fresh authenticated exact-primary Raindrop proof unless affirmatively
  exempt, historical repair and helper retirement, and explicit owner authorization.
- Preserve the existing cumulative spending ceiling and all reservations; verify
  actual billing separately. New budgets/providers/content uploads need their own
  authorized scope. No broad grants or new infrastructure merely to finish planning.
- Owner personally runs reviewed privileged commands; no agent unrestricted sudo
  or Docker-equivalent access. Preserve ordinary rclone and existing backups.
- Reuse the existing worker, catalog, adapter and ledgers. No second migration
  engine, general preflight framework, speculative platform or repeated broad review.

[Project tracker: FOS-113](https://linear.app/fosterstech/issue/FOS-113).
