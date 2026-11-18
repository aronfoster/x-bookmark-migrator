# Bookmark project roadmap

Updated 18 November 2026 (America/Denver). Owner-directed v1/v2 scope.

## Planning home and working rules

**GitHub is the primary planning home for this project.** This is the owner's
18 November project-specific change to the earlier Linear-first arrangement.

- **This file:** product scope, milestones, priorities and current checkpoint.
- **[SPRINTS.md](docs/SPRINTS.md):** ordered work, dependencies, exit evidence and
  next actions. **[V1_ACCEPTANCE.md](docs/V1_ACCEPTANCE.md):** search acceptance
  and the manual categorization plan. Existing runbooks retain exact procedures.
- **GitHub PRs:** proposed changes, review and sanitized implementation evidence.
  Accepted main is the baseline; while PR #14 is open, it is the one pending
  roadmap revision. Explicit owner decisions take precedence even before merge.
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
| Complete X migration | Preserve complete obtainable text/media, exact original source and category evidence; safely unbookmark only verified items. Account for all known streams and exceptions. A sample or an empty observed API page is not completion. | [FOS-169](https://linear.app/fosterstech/issue/FOS-169), FOS-164 |
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
or assume Google sign-in authorizes API access. Inspect the existing genealogy VM
before choosing hosting. Network evidence must prove media bypasses the GCP VM.
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
| S1 | 18–24 Nov 2026 | **M1 Recoverable:** existing bundle restored off-host and completely verified; S3 search/curation designs ready for focused review. | Current; local recovery queued, remote design available |
| S2 | 25 Nov–1 Dec 2026 | **M2 Operational:** complete catalog visible, historical repair accepted, prospective gate and one live worker pass proven, scheduled run accepted. | Planned; requires M1 for live rollout |
| S3 | 2–8 Dec 2026 | **M3 Findable and organizable:** OpenClaw/Drive/Raindrop search acceptance and categorization plan/rehearsal pass. | Planned; design can proceed during S1 |
| S4 | 9–15 Dec 2026, then repeat if needed | **M4 V1 accepted:** all-history reconciliation, continuing intake/recovery and v1 acceptance matrix pass; owner dispositions for any irrecoverable remainder. | Planned; no fixed release date |
| S5 | After v1 acceptance | **M5 Gallery:** private direct-Drive media browsing on desktop and Android. | V2 backlog |
| S6 | After v1 acceptance; sequence after S5 by default | **M6 Multi-source:** one additional source works through preservation/search/curation without breaking X. | V2 backlog |

Sprints group deliverables; their milestones require evidence, not calendar expiry.
M2's worker can drain eligible history while S3 search work proceeds. M3 is a v1
release requirement, not a new per-item X DELETE gate. Categorization completion,
v2 hosting and Wiki construction never hold the safe migration path hostage.

## Current checkpoint and next actions

Baseline: main `9b3ce5e`, merged worker #10, readiness #11 and recovery preparation
#13. Local evidence is owner-reported, not a new remote audit. Effective Raindrop
skill, dedicated runtime/protected build, staged complete catalog and initial
encrypted capture exist. Desktop decryption succeeded. Independent recovery copy,
full signature/member/hash proof and genuine remote restore remain outstanding.
Publication, live Raindrop acceptance/repair and the current-policy scheduled run
are not established. Complete search acceptance is also not established.

**Next local action (S1 / FOS-169):** use existing desktop keys, producer receipt
and captured generation to finish recovery verification and existing Déjà Dup
integration. No repeated setup, capture, key transfer or retired-host dependency.
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
