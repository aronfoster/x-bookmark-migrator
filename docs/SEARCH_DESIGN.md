# S1 search design for FOS-220

**Decision for focused review.** Use the verified catalog as the common input.
Try one ordinary UTF-8 text file per saved primary in Drive. Let Mnemosyne search
the same fields locally and resolve candidates through the catalog. Keep Raindrop
as the discovery pointer. The [M3/M4 matrix](V1_ACCEPTANCE.md#observable-acceptance-matrix)
remains the release test. This proposal adds no runtime command or account access.

## One record, all versions, links that survive edits

| Identity | Representation and read path |
| --- | --- |
| Saved primary | Key by exact decimal-string `source_post_id`. One private `bookmark-<primary>.txt` per primary in a dedicated derived folder under the pinned Drive destination. The filename helps a reader return from Raindrop. Natural-language probes must hit terms confined to the body. |
| Snapshot | Within the unit group by the catalog's ordered (manifest SHA-256, payload SHA-256) pair. Show original folder names/category and blockers, primary text in full, author if known, then separately labeled quoted/reposted text with its source URL. Include the version's `payload_url`. Include obtainable Article title/body from the payload when present and identify incomplete Articles explicitly. A stub never counts as a complete body. |
| Attachment occurrence | Under its version retain saved primary, containing post, `asset_id` (containing post:index:media_key), media type, visibility, availability, description and receipt. Open `archive_url` built from `storage.file_id`; compare against that occurrence's size/hash. Identical bytes, quoted attachments under several saved primaries, and changed bytes across versions retain their separate contexts. No expiring preview or original X media URL is an archive locator. |
| Editable metadata | Label source alt text, generated caption/OCR, explicit annotation, and approved display category/tags separately. Preserve original categories after an override. A legacy `explicit_annotation` has unknown authorship unless decision evidence exists. Hidden is a browsing preference. Include the asset in search and label it when returned. |
| Navigation | Start the unit with original saved-post URL and approved display fields. Include verified Raindrop navigation if present. Use absolute Drive viewer URLs for payloads and each stored asset. A missing-local asset can have a historical Drive link; an unarchived asset has no invented link. OpenClaw can also open the catalog-relative local path as its actual reader. |

Render deterministically in catalog order, with readable section labels, LF and UTF-8.
No run timestamps in text. Changes to labels/layout change the renderer version.
The machine consumer keeps structured occurrence identity from JSONL rather than
parsing free text for IDs. Give the reader typed, escaped data fields for candidate
snippets, OCR and captions. Resolve tool arguments from the selected catalog
occurrence and the user's request. S3 must exercise a snippet containing an
instruction and a foreign URL: display it as quoted data, open only the selected
receipt-backed asset, and observe zero extra tool calls.

## Updating one record and recovering a full index

1. Use the same writer lock and canonical `annotations.private.json` as
   [catalog-build/annotate](CATALOG.md). Upsert an approved edit by its exact
   target; record decision/provenance and previous value once per edit ID. Replaying
   that ID with different content is a conflict. Retain unrelated fields, approvals
   and Raindrop receipts. S3 adds a locked set/clear category writer and an edit
   journal in the canonical annotations file. Commit the decision ID, target,
   previous value and new value together under the lock. An identical replay is
   a no-op even after restart or a newer edit; a conflicting replay leaves the
   file unchanged. Rebuild from fresh immutable inputs to remove retired terms.

   Bind each explicit visual description to (asset_id, storage SHA-256) at edit
   time. All primaries containing that asset ID and digest share the edit. A new digest needs
   a separate description or explicit confirmation. Preserve the old description
   and its decision history on the original bytes. The current catalog projects
   legacy descriptions by asset_id alone. Migrate one only with evidence of the
   reviewed digest; otherwise keep the text privately for review and omit it from
   search snippets and sidecars. Never infer the binding from today's sole match.
   Preserve existing visibility preferences independently.
2. Pin the complete receipt set and input generation under the lock, then run
   writer `catalog-verify` and actual-reader `catalog-check` against independent
   expected populations. Keep the verified generation available while preparing
   units. Hash each rendered body and map (primary, surface, format) to intended digest,
   renderer version, Drive file ID and last verified digest in protected state.
   A later batch rebuild includes earlier batches. An edit to a shared attachment
   invalidates every containing primary. Sorting and byte comparison make a full
   rebuild equivalent to the incremental output. Preserve old mappings if an
   input is missing; hold the generation for reconciliation.
3. Extend only the **derived** Drive writer in S3. Reuse the allocation journal,
   parent/key lookup and receipt readback in [drive.py](../src/x_bookmark_migrator/drive.py).
   Use a stable logical key per primary and format independent of digest, file name, category
   or renderer version. Persist the allocated file ID and intent before create.
   Reuse the same ID after a timeout. Keep archive `Drive.put/save` create-only.
   Update just the existing verified sidecar ID for an edit. Check pinned account,
   parent, key, MIME, permissions and explicit `trashed == false`. A missing
   trash flag or a trashed target blocks both writes and checkpoints. Serialize
   the local publishers and fence stale generations before writes and checkpoints.
   Require an owner-controlled, single-writer destination and inspect actual ACLs.
   Prior-byte checks and post-write readback detect observable conflicts; an
   external UI edit between check and update can still be overwritten. Stop on
   observed drift. If exclusive writing cannot be maintained, hold publication.
4. For each current intent, read the target. With those preconditions satisfied,
   matching desired bytes confirm success even after a lost acknowledgement.
   Verified prior bytes allow the pending edit. Any third digest, duplicate
   logical key, wrong parent, or inaccessible previously
   confirmed ID stops reconciliation. Never allocate a replacement merely because a
   lookup failed. Verify uploaded bytes before advancing its receipt. Same digest on
   a verified target requires zero writes. Interrupted multi-file publication remains
   partial. Mark the generation complete only after coverage/readback and a fresh
   input-generation check. A stale worker cannot mark a newer edit complete.
5. Back up canonical annotations, immutable receipts, generated provenance and the
   ID/intent map with protected state. Restore the mapping before republishing. If
   missing, recover only unique proven identities from complete provider evidence;
   ambiguous/lost state stays pending. Full rebuild replaces the local derived index
   from the verified generation. Never delete Drive files or owner metadata on
   absence from a partial build.

Google documents pre-generated IDs for ordinary uploaded files and updates by file
ID. Verify conflicts, interrupted create/update and digest readback with a fake
Drive before enabling that writer. [Upload guidelines](https://developers.google.com/workspace/drive/api/guides/manage-uploads)
and [files.update](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/update).

The [derived publisher](../src/x_bookmark_migrator/search_publisher.py) is exercised
through an injected fake Drive in [the recovery tests](../tests/test_search_publisher.py).
It rebuilds the reviewed catalog from the supplied batches, locks the catalog and
private state through publication, and checks sole-owner access to the derived
folder and files. A failed connection before sending an edit can be retried.
After an ambiguous PATCH, if Drive still returns the old bytes, it holds the
item: sending another edit could let a delayed request overwrite newer text.
Readback of the intended bytes releases that hold. Back up the private search
mapping with the existing allocation journal. This packet has no live command;
desktop/Android indexing and actual-reader checks remain separate acceptance tests.

## Producing descriptions inside the authorized boundary

Start with available source alt text and existing explicit descriptions. **Select
owner-written text as the baseline producer** for missing clues in the bounded
probe. It needs no model transfer or model fee. Record minutes/asset, missing
descriptions, usable captions and remaining assets by type. Extrapolate that
rate only to the measured uncovered population. A handful of labels cannot pass
the corpus coverage or usefulness rows.

If this baseline misses a frozen query, evaluate a local caption/OCR producer only
where its runtime, content class and budget are already authorized. Verify it uses
local files, local inference and no remote fallback/telemetry carrying content.
OpenClaw access to an archive alone establishes no model grant. Feed only verified
bytes; label observed subjects/actions, OCR and video frame timestamps/sampling
limits. Record output separately by asset, byte hash, producer/model revision and
settings. Reuse successful results for that key, retry failed work explicitly,
and invalidate generated output on changed bytes. Never replace an owner's edit.
Missing bytes or uncertain meaning remain explicit. Compare miss/rank changes,
review minutes, compute time, sidecar bytes and incremental fees separately from
X costs. If only a new transfer or spend can close the gap, present its measured
benefit and local/manual alternatives to the owner before processing.

## Three ways to find the same saved item

| Surface | S3 probe and evidence |
| --- | --- |
| OpenClaw / Mnemosyne | Search the complete verified local projection for body terms, categories, tags, OCR and description clues. Start with lexical candidates plus query reformulation by the existing authorized reader. Keep asset-level matches tied to their version and primary. Rank, deduplicate occurrences for presentation, and show at most three candidates with why they matched. Resolve the chosen primary and attachment from that version before opening bytes. For a quoted asset, exact lookup needs the **containing** post ID; disambiguate by saved primary and version. Return full saved text/source, real image or playable video on open. Offer a discriminating follow-up for ties. Report missing bytes, ambiguous results or no match honestly. A miss after reformulation motivates a measured local retrieval change. |
| Ordinary Drive | In the intended desktop browser and Android Drive app, under the intended account, search the same body-only phrase, long-tail term, original/approved category, image description and video description. Open the matching unit and follow its links to context and actual image/video on both clients. Record upload/readback time, first visible hit or last miss, rank, open/play result and file ID per client. Probe at 0, 5, 30 minutes and 24 hours as an observation window; record actual times and pending observations. After a description edit test new and retired terms in isolation, same file ID and unchanged asset links. A provider API match is diagnostic only. |
| Raindrop | Use ordinary collection/tag and concise metadata search on the existing tier. Open a uniquely matched active pointer and validate its original link with the shared exact-primary parser/adapter. To return to preservation, copy the result's original URL to existing catalog lookup, or take its status ID to the named Drive unit. Open the payload/asset there even if X is unavailable. Demonstrate this return trip from the provider UI. Preserve adopted titles, notes, tags and collection. New pointers follow the accepted Unsorted / to-read / first 250 primary characters policy. An approved category may exist only in the catalog; measure provider discovery with actual available metadata. A required hit missing in Raindrop is a failed probe. |

If the bounded text probe fails, isolate indexing, long-tail truncation, clickable
links and permissions separately. For a native Doc trial, allocate a **new**
format-specific mapping. Pre-generated binary IDs and UTF-8 download hashes
apply only to the text path. Persist the Doc create intent and a unique logical
key before creation. If the response is lost, reconcile by that key in the
pinned parent; an uncertain absence or multiple matches holds without retry.
Read back the document structure. Compare ordered paragraph text and hyperlink
targets/ranges to the intended canonical model, normalizing only the documented
paragraph terminator and ignoring formatting outside the renderer's contract.
Retain the verified model digest and revision. Private app properties can locate a
candidate; they cannot certify its content. Use the freshly read Docs revision as
`requiredRevisionId` for each update; on conflict reread and reconcile. A stale
revision or a changed link with identical visible text must fail the S3 fixture.

Repeat the long-body, open and edit probes on desktop and Android before switching
the active mapping. Retain the old ID as a superseded alias with its successor;
prevent further writes to it and account for stale search hits before acceptance.
Measure the native Doc's own return route from Raindrop. Preserve immutable asset
links through either format. Private rollout follows the bounded client proof.
[Docs readback](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/get)
and [revision-controlled updates](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate).
[Desktop Drive](https://support.google.com/drive/answer/2375114?co=GENIE.Platform%3DDesktop),
[Android Drive](https://support.google.com/drive/answer/2375114?co=GENIE.Platform%3DAndroid),
[Raindrop search](https://help.raindrop.io/using-search), [adapter/format](RAINDROP.md).

## Coverage and focused acceptance

Freeze the protected query deck **before tuning or private processing** in the
existing handoff: deck version, generation/receipt anchor, surface, literal query,
expected primary/version/asset or empty set, required open action and follow-up.
Select owner examples across the rows below, including collisions across records.
Keep expected identities separate from what the searcher sees. Log the ordered
returned identities, expected rank or miss, actual open/hash or playback outcome,
latency, exception and rerun revision there. Only counts and dispositions belong
in public evidence. The synthetic cases below can travel with the code.

| Frozen synthetic clue or scenario | Expected result |
| --- | --- |
| Last sluice phrase near the end of a long body; separately, “how the model caught snowmelt” | Primary 701, untruncated saved text and quoted context. Test exact and paraphrased queries separately. |
| “origami bird riding a scarlet circle” | Photo on quoted 704, saved under 701 and also 703 after the late batch. Literal substring search misses; the S3 reader must return and open the target. |
| “paper heron”, then “the one with the brass crank” | Consider both 701/photo and 702/video; narrow to 702 and play the hidden but explicitly requested video. |
| “violet submarine”; image candidate with local bytes absent | Empty result for the first query. For the second, report missing local bytes and only claim a Drive open following successful readback. |
| Late rain-gauge batch, approved Kinetic sculpture category and replacement video description | 703 becomes findable. Updated terms find 702, retired description disappears, original folder remains. Replay/full rebuild preserve identities and curation. |
| Existing tagged Raindrop alias, quoted-post distractor, duplicate primary | Resolve alias to 701 with existing metadata, then open its archived context; flag duplicate as a conflict. Exercise the existing affirmative media-only predicate and its negative tests. |

Run `uv run pytest -q tests/test_search_design.py tests/test_discovery_gate.py tests/test_raindrop.py`.
The first file exercises restored synthetic batches, two primaries sharing a quote,
a changed digest, category set/clear, an incomplete Article and an affirmative
media-only exemption. The text renderer, edit examples and byte reconciliation
decision table specify S3 behavior in tests. S3 must exercise the actual locked
edit command and derived writer against a fake provider, including lost replies,
reverts, Trash and stale generations. The existing
`test_existing_alias_metadata_untouched_zero_create` exercises adoption and
metadata preservation through the adapter.
Its descriptions are invented labels, independent of the fixture media bytes.
Exact/substring results measure field presence. Semantic ranking, desktop/Android
search, real playback and provider discovery require observations in S3.

For **each surface**, join the independently pinned expected primary and attachment
occurrence sets to results. Report exact set differences (including extra or
duplicate rows), record and asset counts, successfully indexed / missing / failed
by reason, and hidden as a separate overlapping flag. Track verified local bytes,
Drive readback, native sampled query/open success, description coverage and their
unknowns independently. A successful index submission does not prove every row is
natively searchable. For Raindrop count required unique pointers and separately
list only exemptions proven by `positive_media_only` for the matching payload;
unknown classifications stay required. All exempt assets still count on the other
two surfaces. A suppressed attachment, failed access or indexing limit stays in
the denominator. Distinguish captured receipts from unobserved X history.

M3 requires complete reconciled coverage, all applicable frozen positives in the
first three with the expected content opened, both ambiguous candidates followed
by correct refinement, and no fabricated negative hit. Keep all failures in the
report, including provider/time-limit failures. Fix and rerun the same deck.
Reconcile again after increment/rebuild and at M4 on the expanded corpus. Prior
publication and the 25 repaired pointers are useful baselines for these checks.
Independent design review is next; S3 owns implementation and live proof.
