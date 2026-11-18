# Roadmap

Updated 18 November 2026 (America/Denver).

## The outcome

Move saved content out of X into a durable, usable personal collection, safely
remove verified source bookmarks, drain the reachable historical backlog, and
keep processing new saves indefinitely. Copying a sample, merging code, or
clearing one batch is not completion.

The working pipeline is: official X discovery → preserve complete obtainable
text, media and original categories → independently restore and verify → publish
usable external discovery and required exact-source Raindrop pointers → verify
all removal gates → official X unbookmark with durable receipts.

This file is the project's navigation and execution order. [FOS-113][charter]
holds the charter; [FOS-169][drain] holds the current execution checkpoint and
historical-drain acceptance. Linked tickets retain requirements, decisions and
private evidence; linked runbooks retain commands. Update this roadmap when a
milestone changes, rather than appending another competing plan. A later explicit
owner decision controls a conflict; reconcile the documents before executing it.

## Where we are

Snapshot from GitHub main `9b3ce5e` and the latest FOS-169 owner checkpoint;
desktop results below are recorded evidence, not a new live audit.

| Work | Established | Still missing |
| --- | --- | --- |
| Core migration | Official inventory, portable Drive archive/restore, catalog and receipt-backed removal code exist. Earlier bounded removals have receipts. | Acceptance under the current source-link/Raindrop policy. |
| Daily worker | [PR #10](https://github.com/aronfoster/x-bookmark-migrator/pull/10) merged; dedicated worker environment and protected build established locally. | Current release acceptance and an actual scheduled run. |
| Discovery repair | [PR #11](https://github.com/aronfoster/x-bookmark-migrator/pull/11) merged; complete catalog/source links/curation staged; effective Raindrop skill corrected. | Publication, actual-reader access, complete helper audit and live historical repair. |
| Recovery | [PR #13](https://github.com/aronfoster/x-bookmark-migrator/pull/13) merged; encrypted capture succeeded; owner reported successful desktop decryption with copied keys. | Independent recovery copy, full signature/member/hash verification and genuine off-host restore. |

Code merged is distinct from installed, live-verified and accepted. A ticket's
Done status does not supply missing runtime evidence. Further X DELETE and timer
activation remain paused. Do not repeat successful setup, initial capture, key
transfer or skill synchronization. The retired source machine is not a dependency.

## Remaining milestones, in execution order

### 1. Recover the existing protected bundle independently — current next step

**Owner:** FOS-169; supervised local agent prepares, owner runs reviewed privileged
and secret-bearing operations. **Procedure:** [BACKUP_RECOVERY.md](docs/BACKUP_RECOVERY.md).

- Start from the actual producer receipt and private capture source. Resolve the
  existing generation and manifest contract; use the already preserved desktop
  keys. Reuse the verifier and add only demonstrated missing checks.
- Preserve independently accessible owner recovery material. Verify the trusted
  signer, successful decryption, complete file/directory membership and every
  required size/hash against the existing format.
- Inspect actual Déjà Dup coverage, reader access, exclusions, destination,
  retention behavior and quota. Add only the ciphertext publication directory if
  needed; preserve ordinary rclone, other backups and existing settings.
- Restore that exact generation from a completed remote backup into a distinct
  location, using independent recovery material, and repeat full verification.

**Exit evidence:** all recovery acceptance rows pass. Local decrypt or an old
marker restore alone does not pass. Preserve the original state and archives.

### 2. Make the preserved collection usable at its final location

**Owner:** [FOS-216][repair], coordinated with FOS-169's protected rollout.
**Procedure:** [READINESS.md, publication and access](docs/READINESS.md).
**Depends on:** milestone 1's accepted recovery before the protected handoff.

- Install the accepted readiness release through the existing rollout; inspect
  installed revisions rather than assuming the older protected build includes it.
- Publish the complete staged catalog, HTML, original-source sidecar and current
  annotations together. Preserve older catalogs, original bytes and owner curation.
- Run archive-bound `catalog-verify`, writer curation checks and `catalog-check`
  through the actual reader/container. Open representative archived text, images,
  video and exact original source links in the normal browser and Mnemosyne.

**Exit evidence:** the complete expected population is accessible at its real
paths, curation survives, and immutable evidence is unchanged. Staging, a healthy
container or simulated reader tests are insufficient.

### 3. Reconcile historical removals and prove Raindrop discovery

**Owner:** FOS-216; [FOS-166][discovery] owns the shared adapter, ledger and
prospective gate. **Procedure:** remaining [READINESS.md](docs/READINESS.md) steps.
**Depends on:** milestones 1–2; retain the existing separate provider authorizations.

- Finish the actual native/private-helper source audit and retire old entry paths
  or prove exclusive use of the hardened transaction. Preserve original intents,
  reports and unknown outcomes; do not invent duplicate attempts.
- Take complete active/Trash inventory through the existing adapter. Preserve
  existing bookmarks and metadata; stop on incomplete coverage or conflicts.
- Prove one-record create/adopt → authenticated GET → immediate inventory →
  idempotent rerun. If no target is absent, adopt without fabricating a create test.
- Backfill the historical removal population in the accepted small batches,
  initially at most three IDs. Account for every confirmed/unknown attempt;
  queued and blocked source bookmarks are not historical removals.

**Exit evidence:** each historical repair target has fresh exact-primary Raindrop
proof or affirmative media-only evidence plus actual external retrieval. Accept
the prospective FOS-166 gate separately; historical repair alone does not prove it.
Back up changed catalogs and ledgers through the accepted recovery process.

### 4. Prove one complete live migration through the existing worker

**Owner:** [FOS-168][worker] runtime contract, coordinated in FOS-169.
**Procedure:** [WORKER.md](docs/WORKER.md) and the private owner rollout manifest.
**Depends on:** milestones 1–3 and explicit owner authorization for live spending
and removal. Do not build a second historical-drain executable.

- Reconcile actual Console spending with all retained reservations; preserve the
  existing cumulative budget, hard cap and auto-recharge policy. Recheck current
  official API contracts before changing integration or resuming paid execution.
- Verify the actual release/configuration, identities, mounts and shared state;
  run the existing offline dry-run, then the authorized non-destructive live mode.
- Begin removal acceptance with one freshly verified eligible item. Prove remote
  archive/readback, usable catalog, exact-source discovery, official transaction
  and post-removal retrieval. Previously queued items need fresh gate checks.
- Verify restart, overlap exclusion, rate-limit deferral and uncertain-outcome
  holds on the intended host without replaying historical removals.

**Exit evidence:** a real end-to-end pass with preserved receipts, retrieval and
cost evidence. Missing content, category proof or discovery leaves the item on X.

### 5. Establish sustained daily operation and continuing recovery

**Owner:** FOS-168 runtime acceptance and FOS-169 recovery follow-through.
**Depends on:** milestone 4; timer activation requires its separate owner approval.

- Install/validate the existing non-root scheduling units and enable only after
  acceptance. Account for an immediate catch-up invocation when enabling.
- Observe an actual scheduled pass and persistent state, then verify later new
  saves enter the same pipeline. An installed timer is not an accepted service.
- Complete the separately reviewed recurring capture/retention configuration so
  changing catalogs, annotations and ledgers remain recoverable. The initial
  encrypted generation is not continuing backup coverage. Use the existing backup
  system; record freshness, quota/retention behavior and restore evidence.

**Exit evidence:** unattended bounded migration works, failures remain visible,
incomplete items remain saved, and ongoing recovery coverage is demonstrated.

### 6. Drain reachable history and account for what remains

**Owner:** FOS-169. **Depends on:** accepted scheduled operation; use that same
worker for both historical and new saves.

- Continue bounded batches; increase only from measured safe operation and spend.
- After removals, scan fresh windows to measure whether older IDs become visible.
  Never reuse invalidated positional cursors or assume full-history API access.
- Track each observed stream and deduplicate global totals. Distinguish permanent
  blockers, retryable failures, stalled windows and unknown category membership.
  Avoid repeatedly paying to hydrate known permanent blockers.
- Reconcile every removal to preserved content, receipts and required discovery.

**Exit evidence:** sustained runs decrease the backlog; reachable eligible history
is processed; blocked/inaccessible/unknown remainder is honestly documented.
Never invent a numerical total for invisible history. Daily new-save processing
continues after this finite historical milestone.

## Later tracks, independent of migration acceptance

| Track | Intended result | Boundary |
| --- | --- | --- |
| [FOS-172](https://linear.app/fosterstech/issue/FOS-172) | Reversible incidental-media hiding with source/attachment context retained. | Browse hygiene; not a new migration gate. |
| [FOS-170](https://linear.app/fosterstech/issue/FOS-170) | Optional conversational curation of small owner-approved batches across X and Raindrop. | Preserve original metadata and owner commentary separately from AI suggestions. |
| [FOS-171](https://linear.app/fosterstech/issue/FOS-171) | Incremental Mnemosyne knowledge pages and owner synthesis. | Authored work needs its own versioning/backups; raw archives cannot recreate it. |
| Later sources under FOS-113 | Extend preservation to Reddit/TheMotte. | Separate scoped work; do not expand the present X execution path. |

## Execution rules that keep this plan small

- Use existing code, receipts and runbooks. Inspect the real producer, installed
  configuration and consumer before choosing commands; fix the demonstrated
  mismatch. No general preflight framework, new platform or speculative rewrite.
- For each session, name the current milestone, its concrete missing evidence and
  the next action. If blocked, record the precise missing input/failing check and
  smallest correction. Do not restart completed work or generic review rounds.
- Use only official authorized APIs. Keep actual content, IDs, folder names,
  credentials, private paths and diagnostic filenames out of this public repo.
- Every non-media or ambiguous post requires independently GET-verified Raindrop
  primary-link identity before DELETE. Positive media-only exemption still needs
  the original source URL and external retrieval. Full bodies stay in the archive.
- Preserve the existing privilege boundary: owner executes reviewed sudo commands;
  agents receive no unrestricted sudo or Docker-equivalent authority. This roadmap
  does not authorize provider writes, spending, deletion or timer activation.
- Close sessions with a short checkpoint in Linear: milestone, verified revision,
  result/receipt location, remaining blocker and next actor. Reflect milestone
  transitions here; retain detailed private commands and evidence in their existing
  handoff. Do not call the project finished because a PR or backup step is done.

[charter]: https://linear.app/fosterstech/issue/FOS-113
[drain]: https://linear.app/fosterstech/issue/FOS-169
[repair]: https://linear.app/fosterstech/issue/FOS-216
[discovery]: https://linear.app/fosterstech/issue/FOS-166
[worker]: https://linear.app/fosterstech/issue/FOS-168
