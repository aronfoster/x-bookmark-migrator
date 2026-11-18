# V1 acceptance and categorization plan

18 November 2026. Detailed requirements for roadmap M3/M4. FOS-220 owns search;
FOS-223 tracks categorization-plan acceptance; FOS-170 retains the later live
curation pilot and ongoing loop. These are required
v1 outcomes. The hosted gallery and new source integrations are v2.

## Search contract

Use one corpus with stable source/asset identities and separate original evidence,
generated descriptions/suggestions and human-approved annotations. Every preserved
record and attachment must be accounted for as indexed or explicitly failed with a
reason. A partial index cannot silently become “fully searchable.” Retain original
categories while supporting later approved display categories and searchable tags.

OpenClaw must retrieve across full obtainable saved text, category/annotation
fields and media descriptions; paraphrased visual clues must find actual assets
without exact text or IDs. Generated captions/OCR may supply search material where
needed, but do not assert cultural meaning, provenance or owner intent as fact.
Choose implementation by a measured query set; embeddings are an option, not a
new mandatory platform. Private content/model access and spending constraints
remain unchanged; no automatic upload to a new hosted model.

Drive must offer meaningful search and opening in its ordinary desktop and Android
interfaces without OpenClaw. Determine the smallest supported representation:
searchable text sidecars and/or appropriate derived search metadata linked to
original files. Do not rename or rewrite immutable payloads/receipts to make search
work. Full text remains preserved; native provider indexing limits must be measured
and disclosed, not hidden behind a passing API-only test. Validate long-text terms
and wait/record real indexing latency before declaring a file unsearchable.

Raindrop is a required v1 discovery surface: exact-primary original-source links,
useful concise title/summary/category metadata and a way to reach the preserved
content. Preserve existing tags/notes/collections and approved overrides. Do not
bulk overwrite entries, paste full article bodies, or assume premium full-text
indexing. Existing affirmative media-only exemptions remain explicit; all such
media still needs OpenClaw/Drive retrieval. Missing or ambiguous classification
never exempts a record from the existing removal gate.

## Observable acceptance matrix

| Test | Required evidence | Milestone |
| --- | --- | --- |
| Corpus coverage | Reconcile record/asset population with archive receipts; report indexed, missing, failed and intentionally hidden separately. Hidden media remains describable and explicitly retrievable. | M3, refreshed at M4 |
| OpenClaw text | Retrieve a saved post/article by a distinctive body phrase and a paraphrased subject, without post ID; return complete preserved text and correct original context. | M3 |
| OpenClaw vague media memory | Owner-selected private examples plus synthetic fixtures: describe subject/action/context without exact wording, return a small candidate set with actual assets; refine one ambiguous request conversationally. | M3 |
| Native Drive search | Desktop and Android: search text, category and media-description terms; open correct content. Include a long-body term, image and video. No OpenClaw or developer console needed. | M3 |
| Raindrop discovery | Search/filter required pointers, open exact original post and archive link; verify metadata preservation and zero duplicate adoption. Record media-only exceptions. | M3 |
| No-match and uncertainty | No fabricated results or claim that unavailable bytes were viewed; explain missing/unindexed content and ambiguous candidates. | M3 |
| Incremental updates | A later batch and an approved description/category edit become findable; rebuild/restart retains curation, identity and original bytes. | M3/M4 |
| Manual-review readiness | Categorization plan below, concrete rehearsal, persistent queue example and next-batch selection accepted. No requirement to categorize everything. | M3 |
| All-history and operation | Known coverage reconciled, safe removals evidenced, new-save scheduling and continuing recovery proven; owner disposition for any unreachable/incomplete remainder. | M4 |

Before implementation, fix a representative private query set with expected
record/asset matches covering text, long text, image/meme, video, ambiguous clues,
category, hidden asset and no-match. Use paraphrases that are not merely copied
filenames. Record successes, misses and candidate ranks; fix material failures
before acceptance rather than choosing only examples that already work. Never put
real queries, bookmark text, images or IDs in public fixtures or PRs.

Reference contracts checked 18 November 2026:
[Drive search terms](https://developers.google.com/workspace/drive/api/guides/ref-search-terms),
[Drive metadata](https://developers.google.com/workspace/drive/api/guides/file-metadata),
[Raindrop search](https://developer.raindrop.io/v1/raindrops/multiple).
These describe available primitives, not proof that the deployed corpus is indexed.

## Manual categorization plan

**V1 delivers this executable review plan and its rehearsal.** The entire manual
backlog need not be finished. FOS-170's existing live curation pilot and ongoing
loop remain separately tracked execution; accepting this plan does not close that
ticket or authorize automatic edits.

1. **Establish the queue.** Inspect a complete existing catalog and, when authorized,
   existing Raindrop collection/tag inventory. Retain original folder/category
   evidence and all current owner overrides. Deduplicate review items by exact
   source identity without losing appearances or per-attachment differences.
   Count unreviewed, suggested, reviewed, deferred and revisit items; do not call
   “has a source folder” equivalent to “human categorization finished.”
2. **Choose a small batch.** On request, propose 3–5 items from unreviewed work,
   oldest captured first by default, with an owner-selected topic/source override.
   Unknown save dates remain unknown. Do not repeatedly surface deferred items
   until their agreed revisit condition; no unsolicited scheduled review messages.
3. **Show enough context.** Supply original text/source/category and media preview
   or normal Drive link. Propose categories/tags using existing vocabulary first,
   distinguish suggestions from owner choices, flag ambiguity, and preserve the
   relationship between each post and its attached images/videos.
4. **Accept conversational decisions.** Support approve, edit, keep existing,
   defer, revisit, and per-asset feature/hide/show/describe. “Keep the post; hide
   the incidental GIF” never deletes bytes. Missing meme meaning can be deferred
   without holding up archive or safe source removal.
5. **Persist and reconcile.** Record exact target, previous value, proposed value,
   owner decision, provenance and review state in the existing private annotation
   model or the smallest reviewed extension. Apply only approved changes via the
   existing Raindrop integration. Read back provider edits; uncertain results stop
   and reconcile rather than repeat writes. Never replace unrelated notes/tags.
6. **Resume and measure.** Save the batch outcome and next selection. Restarting
   or repeating the request must not duplicate edits or mark deferred work reviewed.
   Report completed this session, remaining unreviewed and deferred/revisit counts.
   Derive a review-time estimate only from measured owner pace; do not invent dates.
7. **Preserve and undo.** Back up approved annotations/queue state; keep reversible
   edit history and original source facts. Permanent media deletion is a separate
   explicit per-asset decision. Owner commentary is valuable state, not disposable
   generated text; future authored Wiki work remains separately backed up.

### Concrete rehearsal and acceptance

Use three synthetic records to review the plan before live implementation:

| Record | Owner decision | Expected saved state and rerun |
| --- | --- | --- |
| A: article with incidental animation | Keep category; hide animation | Post remains searchable; attachment bytes/context retained, hidden in curated browse; no change on rerun. |
| B: image with uncertain context | Defer until next requested image review | Deferred with revisit condition; no invented cultural tag or provider edit. |
| C: existing Raindrop pointer | Approve one tag, keep existing note/collection | Reviewed only after verified edit; prior metadata retained; interrupted/uncertain write reconciled before retry. |

The plan passes when another operator can explain queue selection, preview,
approval, interruption/restart and next batch from these examples without new
architectural decisions. The implementation pilot later uses an owner-approved
3–5-item real batch, including a defer and restart, and verifies provider readback
and portable annotations. Retain that existing FOS-170 acceptance requirement.
