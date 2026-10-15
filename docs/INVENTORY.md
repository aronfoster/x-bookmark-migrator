# FOS-164: bookmark and folder inventory

This implements bounded official API reads and portable category evidence. It does
not archive assets, schedule jobs, or remove bookmarks. FOS-165 and FOS-167 consume
this evidence; they must reconcile categories and verify archives before deletion.

## First live check: a limited probe

Use Linux, Python 3.12+, `uv`, direct HTTPS to `api.x.com`, and the completed
[README OAuth setup](../README.md#one-time-owner-setup). Reuse its private state
directory outside Git and the same `X_CLIENT_ID`. Confirm the Console hard spending
limit and auto-recharge setting before spending more. The examples use the ignored
`.env` through `uv run --env-file .env`; if variables are already exported, omit that
option. Find the ID with [the README identity lookup](../README.md#find-your-numeric-account-id).
Export `X_APP_OWNER_ID` in the invoking shell, or supply the owner's verified
numeric ID directly to `--app-owner-id`. `--env-file` only sets the child process's
environment, so it cannot supply the shell's `$X_APP_OWNER_ID` substitution.

**Check existing reservations first.** The UTC-month budget is shared by pilot and
inventory in this state directory and persists across invocations. It does not reset
when an inventory starts or resumes. For the default directory, this offline command
prints only the current month and reserved estimate (no credentials or API calls):

```sh
uv run python - <<'PYCODE'
import json
from datetime import UTC, datetime
from pathlib import Path
month = datetime.now(UTC).strftime("%Y-%m")
path = Path.home() / ".local/state/x-bookmark-migrator/budget.private.json"
ledger = json.loads(path.read_text()) if path.exists() else {}
print(f"UTC month: {month}; estimated reserved USD: {ledger.get(month, '0')}")
PYCODE
```

If using `--state-dir`, inspect `budget.private.json` in that directory instead.
At the example's $0.10 reservation, allow up to $0.60 of **remaining** headroom;
previous pilot reservations count toward the $1.00 example month ceiling. With
$0.60 already reserved, only four further GETs fit. Do not delete the ledger or
switch directories to reset accounting. Set any higher soft ceiling deliberately,
within the verified Console limit and your intended budget, or wait for the next
UTC accounting month (which may differ from the Console billing cycle).

```sh
uv run --env-file .env x-migrator inventory --app-owner-id "$X_APP_OWNER_ID" \
  --console-hard-cap-confirmed --auto-recharge-off-confirmed \
  --max-calls 6 --page-size 1 --hydrate-batch-size 1 --max-pages-per-stream 1 \
  --per-call-reserve-usd 0.10 --run-cap-usd 0.60 --month-cap-usd 1.00
```

This is a **probe**, not a resumable full inventory. Limiting each stream to one page
prevents a large main backlog from consuming every call. A one-item page limits
folder discovery to at most one folder, leaving room for its contents and a lookup.
With a reachable nonempty folder and a folder-only ID, the probe makes five GETs:
identity, folder enumeration, main bookmarks, that folder's contents, and hydration.
Empty/inaccessible folders or a folder ID already present in the main page mean
fewer calls; hydration access is then **still unverified**. Record which endpoints
actually ran. Do not manufacture an extra lookup just to claim verification.

**Exit 1 / partial is expected for a truncated probe** and also occurs if live folder
responses omit pagination metadata. Its private JSONL/checkpoint remains useful.
`page_limit` and `pagination_unverified` blocks cannot be cleared by `--resume`;
start a fresh pass with suitable bounds after interpreting the evidence.

The dollar amounts are illustrative operator reservations, **not guaranteed X
prices**. Larger pages and expansions can cost more. Each GET reserves the same
configured amount against the existing persistent run/month budget. Stdout separates
reservation counts/costs for identity, main bookmarks, folder enumeration, folder
contents, and hydration. Actual charges remain `unverified` until checked in Console.
Published indicative resource rates are $0.001 for eligible main-bookmark Owned Reads
and $0.005 for standard Post lookup; **folder endpoint fees are unverified**. Eligibility,
expansion charges and actual deductions must be measured, not inferred from identity.

Reads occur in this order: identity check, folder enumeration, main bookmarks, each
observed folder's contents, then hydration of IDs without main-stream post bodies.
The request cap includes the identity GET on every invocation, but not OAuth token
refresh. The default page and hydration sizes are 100, within documented limits;
page sizes 1–100 are accepted. The probe deliberately bounds the first live
experiment. Folder page-size quirks are reviewer-reported, not established here. No retries, dialect fallback,
write scopes, or destructive operations occur automatically.

## From probe to a full inventory

1. Compare the probe's endpoint/status evidence and per-operation reservations with
   actual Console charge details/deltas. A combined balance delta alone cannot price
   each endpoint. Keep unobserved hydration and folder charges explicitly unverified.
   Resolve missing pagination metadata before expecting complete category coverage.
2. Choose page/batch sizes and a conservative per-call reserve for those sizes,
   including expanded resources. **Do not reuse a one-item probe's measured charge
   unchanged for 100-item pages.** Account for variable returned-resource counts,
   current published rates and a margin; the single reserve must cover the most
   expensive planned GET. If pricing is unresolved, continue only with deliberate
   small bounded experiments and report the gap.
3. Estimate main pages, folder-list pages, pages in every folder, hydration batches
   for folder-only IDs, and one identity GET per invocation. Add headroom for
   uncertain terminal pages and interrupted reads. Set the run cap to at least
   `max_calls × per_call_reserve` if the request cap should be the normal stopping
   point. Set the month cap to existing reservations **plus** the planned budget,
   within your intended spending and Console limit; ten $0.10 reservations already
   consume the original $1.00 example ceiling.
4. Set the three shell variables below to those deliberate dollar limits. Start a
   **fresh** full pass, without `--resume` or the probe's one-page bound. This example
   uses ten GETs per invocation and 100-item pages/batches; reduce them if the pricing
   evidence supports only a smaller batch. Default pass bounds remain 10,000 records,
   1,000 folders and 100 pages per stream; choose higher bounds up front if needed.

```sh
uv run --env-file .env x-migrator inventory --app-owner-id "$X_APP_OWNER_ID" \
  --console-hard-cap-confirmed --auto-recharge-off-confirmed \
  --max-calls 10 --page-size 100 --hydrate-batch-size 100 \
  --per-call-reserve-usd "${X_INVENTORY_CALL_RESERVE_USD:?Set a measured conservative reserve}" \
  --run-cap-usd "${X_INVENTORY_RUN_CAP_USD:?Set the run reservation cap}" \
  --month-cap-usd "${X_INVENTORY_MONTH_CAP_USD:?Include prior reservations in the month cap}"
```

After `request_limit`, repeat that full-pass command with
`--resume --source-unchanged-confirmed`, only while the source remains unchanged.
After `spend_cap`, inspect the ledger and deliberately revise the applicable soft
cap or wait; immediately repeating the same command cannot restore headroom.
Stop resuming at `observed_end`. If streams are blocked or content remains partial,
inspect the reported reasons instead of looping until success: resuming does not
repair missing metadata, inaccessible content or a page/record limit. A larger
page/record bound needs a fresh pass. Known partial records remain blocked for later
archival while other verified records can progress. Preserve the probe and full-pass
evidence separately, and never infer all-history coverage from `observed_end`.

## Resume versus rescan

A capped run saves its exact next token and all committed page observations. Repeat
the command with `--resume --source-unchanged-confirmed` **only if no saves, folder
edits, moves, or unbookmarks have occurred since the pass began**. This is an operator
attestation, not an API snapshot guarantee. Resume uses the checkpoint's original
page size and record/folder/page bounds. Omit those flags to inherit them; explicitly
supplying a different value is rejected before any API request. `active_limits` in
the summary reports the effective values. Request, hydration-batch and run-spend
limits apply to this invocation; the month ceiling is checked against the persistent
shared pilot/inventory ledger across invocations. The full uninterrupted and split/restarted synthetic tests
produce identical exact IDs, original folder names and memberships.

After any source mutation, including a future migrator deletion, run without
`--resume` to start fresh from page one. Old pass checkpoints/exports are retained;
observations and cursors from different passes are never merged. An unexpected
process death during a request or an ambiguous transport failure invalidates negative
membership claims for that pass. Explicit resume can collect more positive evidence,
but a fresh pass is needed to regain consistent category evidence. A successful GET
whose response was saved just before a crash may be reread on explicit resume; the
original private diagnostic remains available. There is no exactly-once billing claim.

HTTP 401/429/5xx and transport failures stop the invocation and preserve the pending
cursor. Fix authorization or wait as indicated by the private rate-limit evidence
before explicitly resuming. A folder/main 403/404 or schema error blocks that stream
while allowing other reachable streams to progress. Ambiguous/top-level partial
API errors, pagination cycles, missing pagination metadata and per-stream page/record bounds require a
fresh pass after investigating the cause; resume does not erase those failures.

A recognized missing/protected referenced-post expansion is different: when its
explicit expansion parameter and exact ID match a direct or nested reference in
returned posts, pagination continues. Only those posts become `partial`; unrelated
posts remain usable. Main and hydration responses use the same attribution rule.
An error ID merely being absent from `data` is **not enough** to classify it as an
expansion failure: unknown/unattributable errors remain conservative stream/batch
failures. Every original error remains in the page or hydration-batch evidence.

Unavailable folder-only posts receive `blocked_unavailable_source` with their
exact-ID API errors. A bounded cache carries this evidence into fresh passes for the
same owner and field dialect, avoiding repeated paid lookups. A successful current
main-stream read supersedes cached unavailability. Use `--retry-unavailable` on a
**fresh** pass to deliberately recheck missing/protected posts. Unknown lookup
errors or silently omitted IDs stay `unresolved`; neither is called a tombstone.

## Coverage and category semantics

| Field/status | Meaning |
| --- | --- |
| `filed` | One or more positive memberships observed, with exact folder IDs and every original name observed in this pass. Other memberships may remain unknown. |
| `confirmed_unfiled` | No positive memberships, and folder enumeration plus every discovered folder reached an explicit metadata end without errors or detected inconsistency in this pass. This is API-visible negative evidence, not an atomic/global proof. |
| `membership_unknown` | No positive membership and insufficient folder coverage. |
| `category_evidence_complete` | All discovered folder streams and folder enumeration exhausted consistently. Any failed folder makes this false, even for positively filed posts. |
| `observed_end` | Every stream reached its API end; hydration is read or explicitly unavailable. It does not mean all history, usable content, or successful archival. |
| `all_history_complete` | Always false: this ticket cannot prove all-history coverage. |
| `deletion_eligible` | Always false: inventory alone never authorizes deletion. |

A short page with a next token is followed. A page without a next token is an API end
only when pagination metadata is present. Current official folder references list
pagination request parameters but omit `meta` from their example/schema. A live
response without that metadata is saved as `pagination_unverified`, including its
positive folder/name/membership evidence. **No assumption that a short folder list
is complete is made.** Capture its actual shape and entitlement before adapting the
parser. Do not invent a continuation or fall back to private APIs.

Repeated tokens halt the affected stream. Duplicate IDs within a stream or observed
folder renames flag possible source drift and invalidate negative membership claims;
ordinary duplication across different folders is expected and retains all memberships.
An undetectable insertion/deletion between pages can still change results. Freeze
manual edits during a pass and reconcile categories again before FOS-167 deletion.
The reviewer-reported approximately 800-main-bookmark limit is not hardcoded or
claimed as measured. Old/inaccessible items absent from all responses cannot be
counted. A post's `created_at` is preserved as source metadata; `bookmark_time` is null.

## Checkpoints and portable exports

State remains outside Git in the existing owner-only state directory. Each pass has
an atomic/fsynced `inventory-<pass>.private.json` checkpoint. Successful GET evidence,
HTTP failures and malformed responses remain in existing `read-*.private.json` files.
Checkpoint commits contain records and cursor transitions together; a failed page
never advances the checkpoint cursor past uncommitted records. Default bounds are
10,000 unique posts, 1,000 folders and 100 pages per stream. Configure larger bounds
on a fresh pass if needed (supported maxima: 100,000 posts, 1,000 folders/pages).
This intentionally uses one bounded JSON state rather than another database/service.

Every controlled completion or API/cap stop writes a new 0600 JSONL export. To recover
an export without credentials, more X requests, or additional spending:

```sh
uv run x-migrator inventory-export
```

Use global `--state-dir /absolute/private/path` **before** either subcommand if needed.
Stdout prints only sanitized counts, statuses, opaque local filenames and estimates;
IDs, original names, tokens, text and raw errors remain private. Exit status 1 means
partial coverage or a stopped run; it is not a reason to discard the export. Offline
export exits 0 when it successfully writes the existing state, even if that state is partial.

JSONL schema version 2 is UTF-8 and contains `summary`, `folder`, `stream`,
`hydration_batch`, `expansions`, and `post` rows. Each post carries its exact string
ID, canonical URL, full observed post object, original folder ID/name associations,
hydration errors/status, observation time and category flags. Post `expansions_ref`
points to an `expansions` row's `evidence` key within the **same export**; that row
holds the complete original `includes` object once per API response. Join those keys
to resolve users, media and referenced posts. This avoids copying a full page's
expansions onto every post while retaining all returned expansion fields.

Stream rows contain raw/unique counts, request/next tokens, per-page IDs, errors and
timestamps. Hydration rows preserve requested/returned IDs and errors. Private read
filenames are provenance references; post bodies, shared expansions, categories and
API errors are embedded in JSONL, so no external read file or checkpoint is needed
to interpret an export. Assets are still URLs, not archived files.

Version 1 checkpoints are upgraded in memory when loaded, sharing their existing
expansions without new API reads or changing saved cursors/failure states. Offline
export leaves the original checkpoint untouched; the next run persists version 2.
Previously blocked streams remain blocked until a fresh pass, as documented above.

For example, synthetic tests verify one post belongs to both `10` / `Malfoid` and
`20` / `Culture War`, while another post is found only in folder `10` and hydrated
separately. Neither category is converted to a generic tag or a filesystem path.
Folder names including Unicode or path-like characters are preserved as data.

Exports are create-only. Checkpoints, raw diagnostics and repeated exports accumulate
on disk: retain needed evidence, remove obsolete private files manually, and keep the
token store/budget ledger. Do not upload the whole state directory. Any downstream
upload should select only intended private inventory artifacts, never OAuth state.

## Validation and live evidence

Local tests use synthetic HTTP fixtures only: pagination/restart equivalence, named
and multiple folders, folder-only hydration, partial folder failures, rate limits,
spend/record/page bounds, token loops, source drift, unavailable IDs, safe rescan,
transport/process interruption, owner mismatch, private exports and offline export.
Review regressions also cover scoped direct/nested reference failures, ambiguous
errors, one-copy expansion storage with 100 posts, version 1 checkpoint upgrades,
malformed-ID isolation, differing resume bounds and page sizes 1–4.

**External actions for this implementation: zero authenticated X requests; zero
bookmark mutations; no actual billing observed.** FOS-163 proved the main endpoint
and `post` field dialect live. Folder enumeration, folder contents, metadata shape,
folder-only hydration and charges still require a small owner-runtime run. The
fixture verification must not be reported as live category verification.

For that run, retain the private JSONL/checkpoint, report only sanitized endpoint
statuses/counts, continuation presence, named-category association verification and
Console charge deltas. Compare a bounded resumed run with a fresh pass while the
source remains unchanged; never post real IDs, names, text or tokens to the public repo.

Official references checked for implementation:
- [Folder enumeration](https://docs.x.com/x-api/users/get-bookmark-folders)
- [Folder contents](https://docs.x.com/x-api/users/get-bookmarks-by-folder-id)
- [Post lookup](https://docs.x.com/x-api/posts/get-posts-by-ids)
- [Pricing](https://docs.x.com/x-api/getting-started/pricing)
