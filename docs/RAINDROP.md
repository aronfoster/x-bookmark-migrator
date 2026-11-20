# Mandatory discovery before removal (FOS-166)

The 2026-11-16 contract supersedes optional-Raindrop statements in older runbooks.
Every non-media saved primary post needs a unique active Raindrop pointer and a
fresh authenticated GET immediately before removal. This change is code only:
**no live operations are authorized by a PR, test result, or existing v1 plan.**

## Ownership and dependency order

1. FOS-216 source-link/parser commit `c16f3cb` supplies `source.py`, additive
   catalog source fields, source sidecars and browser links. It does not change
   existing immutable archive bytes. FOS-166 consumes that implementation.
2. `openclaw-config/skills/raindrop/scripts/raindrop.sh` owns all provider
   authentication/HTTP. Its `list_all.py` uses this same shell script to enumerate.
3. This repository's `raindrop.py` owns the shared adapter, private intent/result/
   readback ledger and saved-evidence validation. FOS-216 backfill imports it.
4. `discovery_gate.py` and `unbookmark.apply_plan` own prospective enforcement.
   FOS-216 owns historical receipt scanning/backfill; FOS-168 owns later scheduling.

There is no replacement authenticated client, database, second renderer, or second
backfill receipt format. Review both repository PRs and the source dependency.

## Shared Python contract

```python
adapter = RaindropAdapter(ledger_dir=private_path, script=absolute_skill_script)
snapshot = adapter.enumerate_now()
receipt = adapter.ensure_raindrop(pid, source_url, archive_ref, category=category)
proof = adapter.verify_raindrop_now(pid, raindrop_id)
```

IDs are exact decimal **strings**, never numeric JSON IDs. New primary links are
exactly `https://x.com/i/status/<pid>`. `source.raindrop_post_id` accepts the
FOS-216 official aliases, renamed handles, photo/video suffixes and i/web/status.
Unsupported X links block complete matching; search alone never establishes absence.
Bare HTTP(S) site roots on the supported X/Twitter hosts (empty path or `/`, no
query, fragment, port or credentials) are non-post links. They remain unchanged in
the complete inventory but do not participate in exact-post matching. This is a
local matching policy, not a provider guarantee. Profile, list, Space, search and
ambiguous links still block; do not generalize this exception to other paths.
The remaining blockers include profile/list/Space links under the fail-closed contract;
they are not silently discarded. A blocked provider summary identifies the numeric
`raindrop_id`; offline inspection adds `blocking_raindrop_id`. Neither exposes the
private URL. Changing which X paths may be ignored requires a shared-parser contract
change coordinated with FOS-216, not a second parser in this adapter.

`archive_ref` is either a concise archive URL or a dict containing exactly
`manifest` and `payload` receipt objects. The structured form is retained privately;
only its payload Drive URL is sent in the short note. New pointers use Unsorted,
`to-read`, a generic title, archive link and optional original category. Full post
text stays in the archive. Existing entries receive **no PUT or metadata changes**.

Snapshot schema (private `raindrop-inventory.private.json`):

| Field | Contract |
| --- | --- |
| `schema_version`, `result` | `1`, literal `true` |
| `active`, `trash` | Each has `items`, exact `count`, terminal-page-inclusive `pages` |
| `coverage` | `{complete: true, stable_passes: 2, perpage: 50}` |
| `observed_at` | UTC observation time; does not confer freshness later |

`inspect_inventory(snapshot, pid)` performs offline schema/identity validation and
returns `state` (`active`, `absent`, `trash`, `conflict`, `incomplete`),
`raindrop_ids` and `reason`. `validate_receipt(receipt, pid)` validates historical
schema/identity, raising PilotError on invalid input. Neither authorizes mutations.

Receipt/proof schema v1: `source_post_id`, `source_post_url`, integer
`raindrop_id`, `verified_at`, original `primary_link`, active integer
`collection_id`, and SHA-256 `response_sha256`. Ensure receipts add `archive_ref`,
`disposition` (`created` or `adopted`), `coverage`, and `inventory_sha256`.
Receipt filenames are `raindrop-<pid>.receipt.private.json`. Fresh verifier proofs
are stored in the X removal intent, not substituted with an old receipt.

## Create/recover state machine

All ensure operations hold the same exclusive private ledger lock. Every state
write uses atomic replacement and file plus directory fsync; files are 0600 and
the directory 0700 outside Git. Keep one stable ledger per Raindrop account and
use that same directory for backfill/manual/scheduled callers. Do not reset it.
The X state, catalog and Raindrop ledger directories must resolve to three distinct
paths (including symlink resolution). Both CLI setup and apply reject overlap before
external requests; separate `flock` acquisitions on one directory would conflict.

1. Enumerate **all** active and Trash items, 50/page, with exact unique-count and
   terminal-page checks. Repeat both complete scans and require identical decision
   fields: `_id`, `link`, `collection.$id`, `removed`, counts and page coverage.
   Ignore title/cover/cache/lastUpdate enrichment; retain full second-pass items.
   Reject changed decision fields, duplicate item IDs, missing counts, and page-bound
   exhaustion. Two stable observations are local policy, not a server snapshot.
2. One exact active primary-ID match is independently GET-verified and adopted.
   Any matching Trash, multiple active matches, unsupported X links, or conflicting
   earlier receipt blocks. Never recreate an owner-removed previously receipted item.
3. For absent items, flush `raindrop-<pid>.intent.private.json` **before POST**.
   It records identity, archive reference, coverage hash and attempt number.
4. Persist `create-result.private.json` before independent GET. GET must return
   `result:true`, the requested ID, matching primary link, active collection and
   no removed flag. Only then write the successful receipt.
5. A crash/lost POST response re-enters step 1. One active match is adopted with
   no POST. Complete stable absence permits at most **one recovery POST** (two
   lifetime attempts under that intent). A saved create response whose item later
   disappears blocks, rather than silently recreating it. Exhausted attempts need
   explicit investigation; deleting intent files is not a recovery procedure.

No blind write retries. HTTP 401/429/5xx, transport and malformed responses stop;
numeric status/reset/Retry-After context is retained without remote error bodies.
The adapter passes `--delay 600` for every operation; list-all forwards that delay
to every page request. Standalone list-all also defaults to 600ms. This limits one
sequential migrator to at most 100 requests/minute, leaving headroom below 120/min;
other account clients can still consume that headroom and cause a safe 429 stop.
Full scans remain per-item; no stale batch snapshot is reused for absence decisions.
The prospective transaction stops the batch on retryable provider failures.
No X DELETE follows an import/readback failure. Raindrop reads are outside X's
paid-call ledger; actual provider/billing behavior remains separately observed.

## Prospective transaction

`unbookmark-plan` now writes schema v2. Apply rejects all v1 plans before any X
call; read-only reconciliation still accepts old plans to preserve history.
`apply_plan` defaults to blocked if the catalog or legacy-helper retirement
attestation is absent. No optional cached-proof callback bypass exists.

After existing Drive byte/content/category checks and current X folder membership,
the transaction checks exactly one catalog record with **record.post_id == pid**,
the matching archive payload version, source fields and the original X link in
that record's browser article. Quote/reference matches cannot substitute for the
saved primary. It then does complete current Raindrop enumeration and a fresh
single GET. No other external call occurs between that GET and durable intent /
DELETE. The exact discovery proof is embedded in the intent before inventory
invalidation and network mutation. Existing ambiguous-DELETE/no-repeat rules stay.

The narrow media-only exception requires an explicit owner classification in
`<catalog-dir>/media-only.private.json`, keyed by primary ID:

```json
{"101":{"schema_version":1,"source_post_id":"101",
"source_post_url":"https://x.com/i/status/101","classification":"media_only",
"affirmative":true,"reviewed_by":"owner","payload_sha256":"<verified payload SHA-256>"}}
```

Additionally the reverified payload must have one primary post, empty text,
actual supported archived primary media and no quote/repost references. Nonempty
captions, emoji/hashtags, mixed text+GIF, article text and ambiguous types require
Raindrop. This intentionally conservative exemption does not need an AI classifier.
FOS-216's broader reconciliation classifications cannot alone waive the gate.

Apply CLI adds `--catalog-dir`, `--raindrop-ledger-dir`, `--raindrop-script`
(or `RAINDROP_SCRIPT`) and `--legacy-removal-helper-retired`. Existing explicit
unbookmark/budget flags still apply. An attestation is not evidence that remote
code disabled a private helper: the owner must identify and retire/disable it
locally, or revise it to call only the reviewed gate, before making the attestation.

Separate `raindrop-inventory`, `raindrop-ensure` and `raindrop-verify` commands
perform no X calls. Ensure reads a private `--archive-ref-file` and requires
`--confirm-raindrop-create`. A standalone verify result never authorizes deletion;
apply always verifies again. CLI stdout contains counts/status rather than content.

## Provider contract checked 2026-11-16

| Official reference | Documented behavior / conservative local policy |
| --- | --- |
| [Raindrop objects](https://developer.raindrop.io/v1/raindrops) | `_id`, primary `link`, `collection.$id`, tags/note/important; unknown fields are not stable APIs. A truthy/invalid removed flag is a defensive rejection only. |
| [Single methods](https://developer.raindrop.io/v1/raindrops/single) | GET/POST/PUT `/raindrop[/id]`; create/update use collection object, not `collectionId`. Single tags replace. Independent GET is required after create. Trash GET availability is not assumed. |
| [Multiple methods](https://developer.raindrop.io/v1/raindrops/multiple) | Collection 0 excludes Trash; -99 is Trash; page starts at 0, perpage max 50. Bulk tags append, unlike single update. Missing count is blocked, not guessed. |
| [Raindrop overview](https://developer.raindrop.io/) | HTTP errors and 120/min rate limits; numeric reset headers. Literal JSON success also required. No write retry based solely on curl exit 0. |
| [X DELETE](https://docs.x.com/x-api/users/delete-bookmark) | Official `/2/users/{id}/bookmarks/{tweet_id}`; generic true example is still not proof. Existing HTTP 200 + literal false + no-errors policy remains. |
| [X folder list](https://docs.x.com/x-api/users/get-bookmark-folders), [membership](https://docs.x.com/x-api/users/get-bookmarks-by-folder-id), [identity](https://docs.x.com/x-api/users/get-my-user) | Exact owner/name/membership; max_results 1–100. No new completeness inference from omitted pagination metadata. |
| [X errors](https://docs.x.com/x-api/fundamentals/response-codes-and-errors), [rates](https://docs.x.com/x-api/fundamentals/rate-limits), [OAuth](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code), [pricing](https://docs.x.com/x-api/getting-started/pricing) | Existing scope/refresh, 429 stop, per-call reservation and unknown-response behavior retained. No live rates/charges measured here. |

## Review and remaining acceptance

Synthetic checks cover v1 zero-DELETE, quote-only membership, missing source link,
Trash/removed/false response, partial counts, unsupported URLs, aliases, preserved
existing metadata, crash after remote POST before result, bounded recovery, locks,
fresh GET ordering, positive media-only and ambiguous/mixed cases. The 25/32/43
fixture proves old status counts are not Raindrop proof; it is not a live audit.
All existing Drive/archive/curation/unknown-DELETE regression tests remain required.

Before live resumption, independent reviewers must approve exact PR heads; the
owner must approve a small Raindrop create/GET and zero-duplicate rerun pilot,
verify actual active/Trash/count response shape, accept FOS-216 historical repair
(including all private helper receipt formats), retire old bypass helpers, and
explicitly resume the bounded FOS-168 workflow. No repeated photo/video/Drive
demonstration is requested. Merging these PRs does not complete those requirements.
Immediately after the pilot create, run a fresh inventory and confirm the new item
is listed. Listing visibility after create is not a documented guarantee: delayed
visibility during crash recovery can cause the one permitted recovery POST to
duplicate an item and subsequently block. Stop rollout if the pilot exposes lag;
do not treat two stable empty scans as proof of read-after-write consistency.
