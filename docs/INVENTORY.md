# FOS-164: bookmark and folder inventory

This implements bounded official API reads and portable category evidence. It does
not archive assets, schedule jobs, or remove bookmarks. FOS-165 and FOS-167 consume
this evidence; they must reconcile categories and verify archives before deletion.

## Run a small inventory

Use the same private state directory and OAuth setup as the pilot. Confirm the
Console hard spending limit and auto-recharge setting before spending more. If your
configuration is in the ignored `.env`, use `uv run --env-file .env` as below;
otherwise export the environment variables and omit that option.

```sh
uv run --env-file .env x-migrator inventory --app-owner-id "$X_APP_OWNER_ID" \
  --console-hard-cap-confirmed --auto-recharge-off-confirmed \
  --max-calls 6 --page-size 5 --hydrate-batch-size 5 \
  --per-call-reserve-usd 0.10 --run-cap-usd 0.60 --month-cap-usd 1.00
```

`X_APP_OWNER_ID` must be set in the invoking shell for shell substitution above
(the `uv --env-file` option only supplies variables to the child process). Alternatively
supply the owner's verified numeric ID directly to `--app-owner-id`.

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
the small example deliberately bounds the first live experiment. Folder page-size
quirks are reviewer-reported, not established here. No retries, dialect fallback,
write scopes, or destructive operations occur automatically.

## Resume versus rescan

A capped run saves its exact next token and all committed page observations. Repeat
the command with `--resume --source-unchanged-confirmed` **only if no saves, folder
edits, moves, or unbookmarks have occurred since the pass began**. This is an operator
attestation, not an API snapshot guarantee. Resume uses the checkpoint's original
page size and record/folder/page bounds; request, hydration-batch and spend limits
apply to this invocation. The full uninterrupted and split/restarted synthetic tests
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
while allowing other reachable streams to progress. Partial API errors, pagination
cycles, missing pagination metadata and per-stream page/record bounds require a
fresh pass after investigating the cause; resume does not erase those failures.

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

JSONL is UTF-8 and contains `summary`, `folder`, `stream`, `hydration_batch`, and `post`
rows. Each post carries its exact string ID, canonical URL, full observed post object,
expansions, original folder ID/name associations, hydration errors/status, observation
time and category flags. Stream rows contain raw/unique counts, request/next tokens,
per-page IDs, errors and timestamps. Hydration rows preserve requested/returned IDs
and errors. Private read filenames are provenance references; retained post bodies,
expansions, category associations and API errors are also embedded, so the export
can be interpreted without the checkpoint. Assets are still URLs, not archived files.

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
