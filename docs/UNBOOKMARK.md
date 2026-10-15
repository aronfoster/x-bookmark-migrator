# Verified archive-to-unbookmark pilot (FOS-167)

This implements a **1–3-item named-folder pilot** through the official X API.
It removes bookmarks, never Posts. It is ready for code review; no live deletion,
deployed-runtime storage acceptance, or actual billing is claimed by this PR.
Daily scheduling, larger backlog draining and unfiled reconciliation remain
FOS-168/169. The same bounded commands can be invoked noninteractively after the
owner accepts the pilot; this change does not install an automatic job.

## Before the live pilot

Complete [the archive runtime acceptance](ARCHIVE.md#acceptance-still-required-to-close-fos-165)
in the actual selected execution environment: Drive probe, existing pilot asset
readback, representative archival, independent restore-anchor backup and fresh-state
restoration. Inspect text, decode photos/check dimensions and play/probe videos.
The code's structural media checks and hash equality cannot establish visual quality.
Keep the selected candidates' receipt and exact IDs private. Stop manual saving,
moving, renaming and re-bookmarking during plan/apply/reconciliation; X offers no
atomic compare-and-delete snapshot. Existing confirmed folder membership is useful
despite missing pagination. Do not repeat the old page-size experiment.

Use the same persistent state directory, X OAuth app/owner, Drive grant and archive
root as the earlier commands. Configure `.env` with `X_CLIENT_ID`, `X_APP_OWNER_ID`,
`X_ARCHIVE_DRIVE_FOLDER_ID` and deliberate `X_PER_CALL_RESERVE_USD`, `X_RUN_CAP_USD`,
`X_MONTH_CAP_USD` values within the Console hard limit. No shell export of these
values is necessary. Optional `X_CLIENT_SECRET` still applies to confidential clients.
All commands below use a shell variable only for the already-known state path:

```sh
XM_STATE_DIR="$HOME/.local/state/x-bookmark-migrator"
uv sync --locked
```

Use the actual mounted path instead when different. Do not create fresh state to
reset spending or defeat previous-attempt checks. Keep the whole state volume
durable/protected, including removal receipts. Selectively back up non-secret
archive anchors and deletion receipts in the existing private job record; never
copy OAuth tokens to a ticket or upload the entire state directory.

## Authorize bookmark removal once

The default `auth-start` remains read-only. To explicitly add `bookmark.write`:

```sh
uv run --env-file .env x-migrator --state-dir "$XM_STATE_DIR" auth-start --allow-unbookmark
uv run --env-file .env x-migrator --state-dir "$XM_STATE_DIR" auth-finish
```

Open the first command's official authorization URL as the correct owner. Paste
the entire callback into the second command's hidden terminal prompt. The new
grant contains exactly the four existing read/offline scopes plus `bookmark.write`;
no tweet-write, follow or other permission is requested. A refresh must retain
exactly that chosen set. Existing read/archive commands accept either supported
grant, and never use the new permission implicitly. Run ordinary `auth-start`
and `auth-finish` to replace it with a read-only grant again if desired.

## Plan, review, apply

Start with **one** archived post in a named folder known to contain hidden items.
Use the `private_receipt` basename from the successful archive result. For a
three-item pilot repeat `--post-id` with two more exact IDs. No wildcard or
automatic candidate selection is available.

```sh
read -r -p "Archive receipt basename: " XM_ARCHIVE_RECEIPT
read -r -p "Archived post ID: " XM_POST_ID
uv run --env-file .env x-migrator --state-dir "$XM_STATE_DIR" unbookmark-plan \
  --archive-receipt "$XM_ARCHIVE_RECEIPT" --post-id "$XM_POST_ID" \
  --archive-acceptance-confirmed
```

`--archive-acceptance-confirmed` records the owner's attestation that the runtime
probe, fresh-state restore, content/media review and independent anchor backup
above passed. It does not perform or claim those checks on the owner's behalf.
The command **does** independently retrieve the manifest, candidate payload and
every required media object from Drive, check hashes/association, rebuild the
expected text/media mapping from saved official evidence, and confirm direct Drive
file-ID lookup. It makes zero X requests and sends no DELETE. This is the Drive
retrieval option in FOS-167; searchable Raindrop/Mnemosyne indexing remains FOS-166.

It prints counts and a `private_plan` basename. Read that file locally to review
the exact IDs, folder IDs/original names and archive anchors. Do not paste its
contents into a public PR. The plan can contain individually blocked items;
only its verified candidates can proceed.

```sh
read -r -p "Private plan basename: " XM_PLAN_FILE
uv run python -m json.tool "$XM_STATE_DIR/$XM_PLAN_FILE"
uv run --env-file .env x-migrator --state-dir "$XM_STATE_DIR" unbookmark-apply \
  --plan-file "$XM_PLAN_FILE" --confirm-unbookmark \
  --console-hard-cap-confirmed --auto-recharge-off-confirmed
```

Apply checks the authenticated X owner and write scope, pins the Drive identity/root
and active inventory, and re-fetches archived bytes. For each candidate it retrieves
the current exact folder name and first ID window, and requires that the selected
ID is present there. An ID outside this first response is blocked even if earlier
inventory saw it on a deeper page; the request asks for at most 100 IDs, but the
observed response may be smaller. A rename, missing membership, incomplete archive or conflicting
category blocks that item. Unknown/unfiled items cannot use this pilot; it does
not manufacture negative membership evidence from capped windows.

Before sending DELETE, it reserves budget for both DELETE and the post-delete GET,
invalidates old inventory continuation/negative evidence, and durably creates
`unbookmark_requested` with exact owner/post, before-window, category, archive
checksums and reviewed-plan checksum. It stores the raw response before the result
receipt. After an accepted result, it fetches the **same folder from page one**,
records old/new counts and exact newly observed IDs, and stops if the target remains
visible, the window produces no new IDs, the window is empty, or readback fails.
An empty window is an observation, not complete-history proof. No additional item
is deleted merely to force progress through a stuck window. A rerun preserves that stop.

Each intent freezes the inventory's known post IDs before deletion. Comparisons
partition newly observed IDs into `previously_inventoried_ids` and
`not_previously_inventoried_ids`; older intents without that baseline explicitly
report it unavailable. Unseen IDs may be previously hidden bookmarks or concurrent
saves. Known IDs can also have been re-saved or moved. Neither partition proves
bookmark age or why the window changed: `older_bookmarks_exposed` stays `unverified`.
`new_ids_observed` means only that the immediate window changed; both partitions
remain eligible observations because hidden bookmarks may be absent from inventory.
Review this evidence alongside the owner's quiet-window observations for FOS-164.

The first window refresh's new IDs are private evidence, not automatically imported
or archived records. After the accepted pilot, run a fresh inventory/archive/plan
for a new batch. Never resume the old inventory cursor after mutation.

## Response and recovery rules

| Evidence | Result and next action |
| --- | --- |
| HTTP 200, `data.bookmarked` is boolean `false`, no errors | `unbookmarked`. Exact request-bound response is primary proof; fresh same-folder absence corroborates it. |
| HTTP 400/401/403/429 | `unbookmark_rejected`; stop. Correct configuration/entitlement or wait for the numeric rate reset. An explicit `--retry-rejected` on apply re-verifies all gates and creates a new immutable attempt. |
| Timeout, invalid JSON, 5xx, 204, `bookmarked: true`, missing/non-boolean flag, or partial errors | `unbookmark_outcome_unknown`. No automatic or explicit repeated DELETE path exists for this ambiguous attempt. |
| Response durably saved, process dies before final receipt | Reconciliation derives the result from that exact stored response and intent checksum. No second DELETE. |
| Intent saved, response absent (even if the process may have died before sending) | Unknown. Listing absence cannot prove the earlier request succeeded. Reconciliation remains read-only and records that limitation. |
| Deletion accepted but post-delete read fails | The deletion receipt remains valid; window progress is unverified. Reconciliation retries only the read. |
| The same ID is later re-bookmarked | Prior successful/unknown intent prevents another DELETE even under a different plan. Deliberate re-save generations need separate FOS-169 handling; do not erase receipts to bypass this. |

Read-only reconciliation uses no Drive credential or X write scope:

```sh
uv run --env-file .env x-migrator --state-dir "$XM_STATE_DIR" unbookmark-reconcile \
  --plan-file "$XM_PLAN_FILE" \
  --console-hard-cap-confirmed --auto-recharge-off-confirmed
```

This writes another immutable observation/report. A failed per-item window read
records `window_status: unverified` and a safe error code, retains that item's
deletion result, and continues reconciling the remaining receipts within the
request/spend bounds. It does not change an unknown result based on listing absence.
If a previously missing window is now read successfully, it is marked
`reconciled_later`; that delayed comparison cannot authorize apply to continue
the remaining IDs. It may include unrelated saves since the original intent.
Review it, then inventory/archive/plan the remaining candidates afresh. A recorded
stagnant/contradictory window also remains a stop for that plan.

Exit 1 / `blocked: true` means blocked, incomplete or stopped; it can coexist with
`unbookmarked` results for earlier successful deletions. Apply reports complete
only when every selected item succeeded and every immediate window showed new IDs.
Reconcile's summary describes its read observations, not permission to resume
deletion. A local disk error can prevent a summary after an actual DELETE:
do not assume nothing happened. Run reconciliation. Intent/result filenames are
deterministic by owner/post/attempt; immutable files use exclusive creation and
fsync before network actions. A state lock serializes all operations.

Successful and ambiguous DELETEs are never repeated. This intentionally declines
the optional repeated-DELETE live experiment; no idempotency guarantee is assumed
and no success/unknown retry automation can be enabled by this PR. The explicit
retry switch applies only to recorded HTTP rejections, while saves remain paused.

## Bounds, cost and privacy

At most three selected IDs, ten folder-list pages per candidate, a default
20 reserved X requests (configurable 1–100), and existing media-byte caps apply.
When each folder name is on the first page, an uninterrupted pilot needs
`1 + 4 × selected_count` X requests: identity, then folder-name GET, before GET,
DELETE and after GET per candidate. Plan makes only Drive reads. OAuth refresh
requests and Drive requests are outside X reservation accounting.

Each X request uses the configured per-call reservation; it must be at least
the indicative $0.005 Bookmark-write rate. That floor does **not** cover every
possible read charge. Choose it for the most expensive read, including a folder
response larger than the requested size. The default $0.25 run/$1 month limits
may be too small with existing reservations. Set `.env` deliberately before the
pilot; do not clear the ledger. Reserve both final calls before deletion, retaining
unused reservations on failures. Reports separate operation reservation counts,
DELETE attempts sent this run, known states and unverified actual charges.
Observe Console before/after charges independently; local estimates are not bills.

Stdout contains safe counts/status and opaque private basenames. Exact IDs, names,
text, API errors, response headers and archive file IDs stay in private 0600 files.
No response payload, credential, or real owner artifact belongs in Git or CI.

## Official contracts checked 2026-10-15

Review follow-up rechecked the linked identity, folder-contents, pagination and
error documentation on 2026-10-15. They do not establish a stable snapshot or
bookmark save times. Provenance partitioning and delayed-continuation refusal
are local policies. The 10,000-ID defensive response ceiling retains the existing
observed-over-return policy in [API_AUDIT.md](API_AUDIT.md#review-verification);
it does not claim X guarantees more than the requested 100 IDs.

| Reference | Contract and implementation policy |
| --- | --- |
| [DELETE Bookmark](https://docs.x.com/x-api/users/delete-bookmark) | DELETE `/2/users/{id}/bookmarks/{tweet_id}`, decimal-string path IDs, OAuth user bearer; response data and errors. Generic example says `bookmarked: true`. Requiring literal false/no errors is an explicit conservative local policy, pending live evidence. |
| [OAuth PKCE/scopes](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code) | Explicit `bookmark.write` consent for removal; offline access and refresh. No expansion of an existing grant during refresh. |
| [Identity](https://docs.x.com/x-api/users/get-my-user) | GET `/2/users/me`; compare exact user ID before mutation. App ownership/Console settings remain operator checks. |
| [Folder list](https://docs.x.com/x-api/users/get-bookmark-folders), [folder contents](https://docs.x.com/x-api/users/get-bookmarks-by-folder-id) | ID/name and ID-only data respectively, request max_results 1–100 and returned continuation only. Missing metadata never establishes complete history. Fresh first-window positive evidence is usable. |
| [Main bookmarks](https://docs.x.com/x-api/users/get-bookmarks), [integration](https://docs.x.com/x-api/posts/bookmarks/integrate), [pagination](https://docs.x.com/x-api/fundamentals/pagination) | Main-list absence is not exact-ID deletion proof. Existing inventory data is retained; after mutation all cursors must start fresh. |
| [Fields](https://docs.x.com/x-api/fundamentals/fields), [expansions](https://docs.x.com/x-api/fundamentals/expansions), [lookup](https://docs.x.com/x-api/posts/get-posts-by-ids) | Existing archive evidence retains endpoint-specific fields and references. This pilot does not add speculative fields or silently perform paid hydration. |
| [Errors](https://docs.x.com/x-api/fundamentals/response-codes-and-errors) | HTTP success can contain partial errors; classify conservatively and preserve raw evidence. |
| [Rate limits](https://docs.x.com/x-api/fundamentals/rate-limits), [pricing](https://docs.x.com/x-api/getting-started/pricing) | Published DELETE 50/15 min/user, indicative Bookmark writes $0.005/request. Numeric returned rate headers are retained; no automatic 429 or 5xx retries. Console actuals remain unverified. |
| [Drive files.get](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/get), [downloads](https://developers.google.com/workspace/drive/api/guides/manage-downloads) | Exact-ID metadata lookup and `alt=media` retrieve stored bytes. Verify manifest/payload/assets against independently saved hashes; never treat an upload acknowledgement as readback. |

## Live evidence still required

Retain a private table per selected ID: archive receipt and independent restore
anchor, current folder ID/name, before count, DELETE HTTP status and actual boolean/
error shape, immutable intent/result filenames, after count, newly observed IDs,
actual Console delta and runtime invocation. The source Posts must remain intact.
Record unsupported cases as blocked. A 1–3-item actual migration and a newly
exposed folder ID close the shared FOS-164/FOS-167 behavior gate; fixture tests
or merging this PR do not. Record unknown/failed outcomes honestly rather than
changing the success parser to match an unexplained response.
