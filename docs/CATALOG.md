# Offline archive discovery (FOS-166 Phase 1)

The migrator owns a portable JSONL catalog, exact-ID CLI lookup, reversible
per-attachment annotations and a plain static HTML index. `openclaw-config` owns
runtime installation, private mounts and the existing Raindrop skill. No new
service, database, credential, subscription or OpenClaw tool protocol is needed.
This code does not import Raindrop entries, call X, or change deletion gates.
FOS-170 owns later conversational curation; FOS-171 owns the separately backed-up
Wiki. Only minimal reversible FOS-172 visibility is implemented here.

## Build from restored archives

Use the existing `archive-restore` result and its separately saved original batch
receipt. The restore operation is documented in [ARCHIVE.md](ARCHIVE.md); this
catalog builder itself is offline and needs no token, environment file, inventory,
state mount or running OpenClaw. It rechecks the manifest against the receipt,
payload hashes and associations, and locally present media hashes/containers.

```sh
uv run x-migrator catalog-build --catalog-dir /private/discovery \
  --batch /private/restored-batch-a /private/receipts/batch-a.private.json \
  --batch /private/restored-batch-b /private/receipts/batch-b.private.json

uv run x-migrator catalog-lookup --catalog-dir /private/discovery \
  --post 'https://twitter.com/example/status/101?s=20'

uv run x-migrator catalog-lookup --catalog-dir /private/discovery \
  --post 101 --asset-id '101:2:synthetic-media-key'
```

All paths above and IDs in examples are synthetic placeholders. Input files must
be owner-only regular files; the output directory is mode 0700, outside Git,
and outputs are mode 0600. Lookup intentionally prints **private full content**:
consume stdout within the private runtime, never paste it into public CI/PR logs.
A missing exact match exits nonzero. Build prints only counts, not private content.

Each build is a complete projection of the explicitly supplied `--batch` inputs;
include all desired batches on every rebuild. It does not silently accumulate
stale batches. Duplicate inputs collapse; different manifest/payload versions
remain separately inspectable under one exact primary post ID. Category changes,
multiple folder appearances and all original folder names stay attached to their
own snapshot. Conflicting category evidence stays blocked, never relabeled unfiled.

Open `/private/discovery/index.html` in a normal browser. Category navigation links
to posts, full saved text expands in place, photos preview and videos use normal
controls. The page contains no JavaScript or external embeds. It references existing
restored media using relative URLs and provides explicit Drive links; no reupload,
relocation or extra media copy occurs. Preserve relative directory layout when
moving the HTML plus restored media, or rebuild after moving them. JSONL full text
and Drive IDs remain portable independently of that layout. Do not host these files
publicly. Raw Drive folders are not the curated view.

## Stable version 1 contract

`catalog.jsonl` has one object per primary `post_id` (decimal string), with
`schema_version: 1`, `canonical_url`, `deletion_eligible: false`, and `versions`.
Each version contains original `folders`/`category`, historical `archive_status`
and `blockers`, the original manifest and payload receipts (Drive ID, SHA-256,
size, filename), `payload_url`, full `posts` including author/raw/reference
context, and `media`. Long text is never shortened to fit a Raindrop field.
Referenced posts can also be found by their exact ID; lookup returns all containing
primary records, preserving the distinction between a saved post and its quote.

Each attachment has an exact `asset_id` = `post_id:index:media_key`, source
relationship, original storage receipt/name/hash, source URL, logical media type
and actual container. Identical bytes on different attachments remain independent;
variants are not silently merged. An annotation follows the attachment across
snapshots. `local_url` is relative to the catalog directory. `archive_url` is an
explicit Drive link, not a claim that the current grant or file still works.

`availability` is `local_hash_verified`, `missing_local`, or `not_archived`.
Missing local media stays describable and keeps its historical Drive receipt;
corrupt present bytes stop a build. A successful build of a blocked archive is
only discovery, never proof of complete preservation. `preservation_state`
reports historical `archived_raw` or `not_archived`, not current remote presence.
No catalog operation revalidates Drive, proves unbookmark, grants deletion
eligibility, or changes existing FOS-165/167 safety checks.

Mnemosyne can read this JSONL directly or invoke the same `catalog-lookup` command
used in tests. Read by exact string ID; URL spellings on x.com/twitter.com and
renamed handles normalize to that ID. Do not use substring matching or coerce
IDs through JavaScript numbers. No authenticated runtime invocation was tested.

## Hide, describe and restore visibility

```sh
uv run x-migrator catalog-annotate --catalog-dir /private/discovery \
  --asset-id '101:2:synthetic-media-key' --role incidental \
  --description 'Synthetic researching reaction GIF'

uv run x-migrator catalog-annotate --catalog-dir /private/discovery \
  --asset-id '101:2:synthetic-media-key' --visibility ordinary
```

`incidental` defaults that attachment to hidden; an explicit visibility overrides
it. Supported visibility: `featured`, `ordinary`, `hidden`; semantic roles:
`important`, `illustrative`, `incidental`, `unknown`. Unannotated media is ordinary,
unknown and unreviewed. Explicit edits are marked `explicit_annotation`, without
pretending to know whether the caller was human or an LLM. Descriptions are editable
and capped at 1,000 characters. Hidden media is absent from HTML but still in exact
lookup/JSONL with its description and original link. Source post text stays visible.

`annotations.private.json` is separate canonical editable metadata; back it up
alongside private batch receipts. Rebuilds retain it, including annotations for
batches temporarily absent from the projection. No physical cold storage,
permanent deletion or tombstones are implemented. Catalog commands use one
output-directory lock. Each generated file is atomically replaced; interruption
between the JSONL/HTML writes can leave the views out of sync. Rebuild to repair;
annotations and immutable source files remain intact. Close/reopen HTML after edits.

## Existing Raindrop contract audited 2026-10-22

Read first-party [object schema](https://developer.raindrop.io/v1/raindrops),
[single-item methods](https://developer.raindrop.io/v1/raindrops/single), and
[list/search methods](https://developer.raindrop.io/v1/raindrops/multiple), plus the
actual [skill](https://github.com/aronfoster/openclaw-config/blob/master/skills/raindrop/SKILL.md)
(blob `dd91216e570977ad235b5c6cf181c6090af670f0`) and
[script](https://github.com/aronfoster/openclaw-config/blob/master/skills/raindrop/scripts/raindrop.sh)
(blob `6baba5f7b1f4f6fea0f87edc2669deae5a242081`). Repository contents were inspected;
the installed production revision was not accessed or verified.

- Documented fields include numeric `_id`, `link`, `collection.$id`, `tags`,
  `title` (1,000 characters), `excerpt` and `note` (10,000 each). Notes are metadata,
  not a demonstrated durable archive. Full content stays in the archive.
- GET `/raindrop/{id}` reads one Raindrop ID; POST `/raindrop` creates; PUT
  `/raindrop/{id}` updates. Creation/update responses expose `result` and `item`.
  Phase 1 makes none of these calls. Future enrichment must preserve existing
  notes/tags/collections and read back the exact returned ID before claiming success.
- GET `/raindrops/0` lists all except Trash; page starts at 0, maximum 50 per page.
  Search is text filtering, not canonical X-ID equality. Bulk creation is limited
  to 100 objects. Listing coverage must be established separately before creating.
- Existing invocation: `{baseDir}/scripts/raindrop.sh list 0 --json --limit 50 --page N`,
  then `get ID --json` for readback. Authentication uses `RAINDROP_TOKEN` or
  `~/.config/raindrop.env` (also supports `--token`, avoid secrets in process args).
  Reuse this path; this repository adds no authenticated Raindrop client.
- Script limitations: `search` does not forward `--page`; `add`/single updates use
  `collectionId` whereas current docs specify `collection.$id`; no note option is
  implemented. Shell exit zero alone is not success: curl lacks `--fail`, so inspect
  JSON `result`/errors and returned items. The skill documents 120 requests/minute
  and stopping on 429; its code warns but does not itself stop on HTTP 429.
  Verify/fix these in the owning integration before enabling later writes. No live
  401/403/429, duplicate create, note durability or billing behavior was exercised.

For offline exact matching of **already saved**, owner-only `list --json` responses:

```sh
uv run x-migrator catalog-raindrop-match --post 'https://x.com/i/status/101' \
  --page-file /private/raindrop-page-0.private.json \
  --page-file /private/raindrop-page-1.private.json
```

This checks response success and matches stored links by numeric X status ID,
returns existing objects unchanged, deduplicates repeated Raindrop IDs, and rejects
conflicting snapshots. Empty matches explicitly report `coverage: not_established`
and `create_authorized: false`; they are never permission to create. Synthetic
regressions cover an existing twitter.com bookmark, renamed handle, exact-ID versus
substring mismatch, unchanged notes/tags/collection and repeated pages/reruns.
No text/media classifier or reading-queue writer is enabled: emoji captions,
ambiguous prose and long posts all remain externally discoverable without forced
Raindrop import. Later import/curation remains separate from safe migration.

## Deferred owner-assisted acceptance

After review/merge, when the owner chooses to participate:

1. In the intended OpenClaw runtime, restore representative existing batches using
   independent receipt anchors; build and invoke the documented exact-ID lookup.
2. Independently inspect full long text, decode original photos/play real video,
   and open Drive links with the intended grant, without X access. Verify source
   category provenance. Do not perform a new unbookmark as part of this catalog test.
3. Open the private HTML in the owner's normal browser; hide/unhide one explicitly
   chosen attachment, confirm its description remains retrievable and raw files
   remain unchanged. Rebuild/restart and confirm annotation persistence.
4. If later Raindrop enrichment is authorized, enumerate through the existing skill,
   verify pagination/error handling and exact-ID get/readback, then demonstrate
   zero duplicate reruns on a known existing twitter.com bookmark. This is not a
   Phase 1 migration dependency.

No desktop access, production OpenClaw invocation, live personal account reads or
writes, X unbookmark, Drive mutation, or Raindrop creation occurred in development.
Synthetic tests cannot establish those authenticated acceptance results or cost.
