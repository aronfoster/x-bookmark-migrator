# Bookmark project roadmap

Updated 27 November 2026 (America/Denver). Owner-directed v1/v2 scope.

## Start here

| Outcome | Next deliverable | Owner |
| --- | --- | --- |
| Get existing bookmarks off X | Continue the active desktop drain. Verify and remove eligible items, repair recoverable archives, and capture newly exposed IDs. The ticket holds the single live count and protected-session handoff. | [FOS-169](https://linear.app/fosterstech/issue/FOS-169) |
| Process tomorrow's save automatically | Trace the installed timer's normal worker path with history pending. Deliver a real later save through archive, catalog, Drive, Raindrop and confirmed unbookmark on a scheduled run. Verify duplicate-free restart and continuing backup coverage. | [FOS-168](https://linear.app/fosterstech/issue/FOS-168) |
| Find and update that bookmark | Ask the actual Mnemosyne agent for remembered text or media. Open the item in Drive and Raindrop. Preview and approve one description correction, rebuild, and find the same bookmark again. Rehearse on the published catalog. The integrated exit requires the same later save captured by the FOS-168 timer. | [FOS-220](https://linear.app/fosterstech/issue/FOS-220) for retrieval and [FOS-170](https://linear.app/fosterstech/issue/FOS-170) for edits |

Historical migration has first priority. Daily intake and retrieval can progress
alongside the live drain. The local catalog gives Mnemosyne access to saved
content and owner-approved annotations. Drive holds restorable originals and
browseable media. Raindrop keeps off-site source pointers and useful metadata.
Use the existing worker for provider operations and the existing curation path
for approved edits. The full [v1 acceptance matrix](docs/V1_ACCEPTANCE.md)
still applies after the one-bookmark demonstration.

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
| Recovery and sustained operation | Prove independent off-host restore, one complete live transaction, scheduled non-root operation, later new-save processing and continuing backup coverage. | [FOS-168](https://linear.app/fosterstech/issue/FOS-168) for recurring intake and backups; FOS-169 for historical receipts |
| Full-corpus OpenClaw search | Find text and vaguely remembered media using ordinary language without knowing IDs or filenames; open the real saved content and original context. | [FOS-220](https://linear.app/fosterstech/issue/FOS-220) |
| Google Drive search and access | Search meaningful text/category/media-description terms in ordinary Drive on desktop and Android, then open the matching preserved content without OpenClaw. Raw JSONL upload alone is not evidence. | FOS-220 |
| Raindrop discovery | Find required original-source pointers by useful descriptions, including owner corrections, or by existing categories/tags. Preserve archive access, existing entries and annotations. The existing affirmative media-only exception remains. | [FOS-166](https://linear.app/fosterstech/issue/FOS-166), [FOS-216](https://linear.app/fosterstech/issue/FOS-216), FOS-220 |
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

The original timeboxes are planning estimates. Follow the current assignments above.

| Sprint | Target window | Milestone / demo | Status |
| --- | --- | --- | --- |
| S1 | 18–24 Nov 2026 | **M1 Recoverable:** existing bundle restored off-host and completely verified; S3 search/curation designs ready for focused review. | Recovery/restore/cleanup passed; [search design](docs/SEARCH_DESIGN.md) merged; curation tracked separately |
| S2 | 25 Nov–1 Dec 2026 | **M2 Operational:** complete catalog visible, historical repair and prospective gate accepted, one live worker pass and scheduled run proven; blocker triage, exposure evidence and budget checkpoint recorded before scaling. | Live backlog drain under way; unattended new-save acceptance open |
| S3 | 2–8 Dec 2026 | **M3 Findable and organizable:** OpenClaw/Drive/Raindrop search acceptance and categorization plan/rehearsal pass. | Catalog and offline publisher merged; live search and editing open |
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
Use FOS-169 for current counts, operator ownership, spending direction and the
latest live receipts. Earlier snapshots remain in Git history and the ticket
journal. See [S2](docs/SPRINTS.md#s2--publish-reconcile-and-operate) for the
worker exit and S4 for the historical stopping rule.

## Boundaries preserved across releases

- Official authorized source APIs only; no private GraphQL or account scraping.
- Preserve immutable originals, original categories, receipts and owner curation.
  Exact primary source identity is distinct from quoted/reposted/attachment IDs.
- Before X DELETE: complete archive/content/category/readback proof, actual external
  retrieval, fresh authenticated exact-primary Raindrop proof unless affirmatively
  exempt, historical repair and helper retirement, and explicit owner authorization.
- Use [the owner's authorized prepaid X credits](https://linear.app/fosterstech/issue/FOS-169) with auto-reload off. Follow the [worker billing preflight and cumulative limit](docs/WORKER.md). Record actual
  calls and outcomes and reconcile against provider billing afterward. Preserve
  historical accounting. New spending or content uploads require their own scope.
- Owner personally runs reviewed privileged commands; no agent unrestricted sudo
  or Docker-equivalent access. Preserve ordinary rclone and existing backups.
- Reuse the existing worker, catalog, adapter and ledgers. No second migration
  engine, general preflight framework, speculative platform or repeated broad review.

[Project tracker: FOS-113](https://linear.app/fosterstech/issue/FOS-113).
