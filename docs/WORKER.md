# Bounded daily worker (FOS-168)

The `worker` command composes the existing inventory, archive, independent Drive
restore, catalog, shared Raindrop adapter and v2 unbookmark transaction. It does
not run an LLM, Docker, shell commands from configuration, or a daemon. Deployment
units and owner commands belong to private `openclaw-config/deploy/docker-stack`.

**Code/tests are not runtime acceptance.** The historical source-link repair,
prospective Raindrop pilot, effective desktop skill synchronization, retirement of
private legacy helpers, budget observations, exact host paths/account/mounts, and
owner authorization remain rollout gates. Never activate using example values.

## Modes and configuration

All modes use the existing state directory and its shared exclusive `lock`.
`--dry-run` and `--report-only` make **zero provider requests**, including OAuth,
X paid reads, Drive reads/writes and Raindrop operations. They never construct a
provider client. The only local write is acquiring/creating the shared lock.
`--once --live` permits paid X reads and Drive/archive/catalog writes, but cannot
create Raindrops or DELETE. Adding `--allow-delete` explicitly permits both and
requires every local rollout gate. There is no default live mode.

For an owner-approved **specific** bookmark, `max_items=1` alone does not select
that bookmark. `--apply-verified-plan` requires manual `--once --live
--allow-delete`, every live gate, and `max_items=1`. It loads the protected
one-item plan and matches it to the saved Drive proof and active inventory.
The existing proof format covers text and media: the payload digest binds each
media receipt. The transaction verifies each archived file from Drive against its
receipt and checks its media format before writing a removal intent. This mode
also checks the restored manifest, payload and each local media file against the
selected receipts before the fresh Raindrop check, even for a media-only post.
It uses the existing removal transaction without draining the queue or creating
a Raindrop entry. Check the reported removal state and post-removal window
separately; a lost response is not permission to resend the request.

```sh
x-migrator --state-dir /private/state worker --config /private/worker.json --dry-run
x-migrator --state-dir /private/state worker --config /private/worker.json --report-only
# Separately authorized, non-destructive with respect to source bookmarks:
x-migrator --state-dir /private/state worker --config /private/worker.json --once --live
# Only after independent reviews and explicit owner live/delete approval:
x-migrator --state-dir /private/state worker --config /private/worker.json --once --live --allow-delete
```

Paths here are placeholders. Keep config outside Git. Live config is root-owned
0640 with read access only for the dedicated runtime group, under root-protected
ancestors. The runtime UID must differ from every agent UID. State and the shared
Raindrop ledger stay runtime-owned 0700; credentials/receipts/locks stay 0600.
Catalog and restored publication roots may opt into an audited reader group via
mode 2750 (setgid, group read/traverse only); published bytes use 0640. Their
`publication_reader_gid` must match the explicit local profile. Private 0700 roots
remain the default outside this deployment. All four roots must be disjoint.
The reader gains no write access to deletion state, annotations, classification
files or locks. Preserve those owner-only files and their existing curation;
perform curation commands through the owner-supervised runtime account. Preserve existing X/Drive credentials in the
original state directory and existing Raindrop authentication. `X_CLIENT_ID` and
optional `X_CLIENT_SECRET` come from the protected existing environment. No new
OAuth store, rclone grant or second Raindrop client is introduced. The shared
Raindrop adapter accepts explicit `raindrop_script_sha256` and
`raindrop_helper_sha256` (the sibling `list_all.py`): the worker requires both and
rechecks both files before every invocation, including GET. The complete dedicated
script directory and its ancestors must
be root-owned, without group/other write or ACL grants, outside agent workspaces.
`RAINDROP_TOKEN` must be supplied by the protected EnvironmentFile; a minimal
subprocess environment removes BASH_ENV/ENV and HOME credential sourcing. Manual
non-worker adapter callers retain their existing behavior.

Private JSON configuration fields:

| Field | Required value |
| --- | --- |
| `schema_version` | `1` |
| `owner_id`, `drive_folder_id` | Locally verified existing identities |
| `catalog_dir`, `restore_dir`, `raindrop_ledger_dir`, `raindrop_script` | Verified absolute paths to existing runtime resources |
| `max_items` | 1–50; start at 1 for local acceptance |
| `max_calls` | 1–100; shared across inventory/enrichment/DELETE and reserved after-reads |
| `max_batches` | 1–100 existing archive receipts to adopt per invocation |
| `max_asset_bytes` | Optional integer, 1,024–268,435,456 bytes per file; defaults to 64 MiB. Set it high enough to read back the largest repaired asset. |
| `dialect` | Existing observed `post` or `tweet` profile |
| `run_cap_usd` | Positive reservation limit, at most 20 |
| `reservation_usd` | Positive **whole-request** conservative reserves for every operation below; no implicit prices |
| `helper_descriptors` | All reviewed private helper mappings using `REPAIR.md`; empty only when the local audit proves no out-of-tree attempts |
| `raindrop_script_sha256` | Reviewed SHA256 of protected raindrop.sh; required for DELETE |
| `raindrop_helper_sha256` | Reviewed SHA256 of sibling list_all.py; required for DELETE |
| `publication_reader_gid` | Explicit reader GID for 2750 catalog/restore roots, or null for private roots |
| `gates` | Explicit booleans below, initially false |

Reservations must specify `identity`, `main`, `folders`, `folder`, `hydrate`,
`folder_names`, `before_window`, `delete`, `after_window`, `reconcile_window`,
`other`. Enrichment uses the conservative `other` allowance. Account for resource
counts, expansions and folder over-return, **not just cost per one resource**.
`delete` cannot be below $0.005. Bounds are local spending policy, not a guarantee
of provider charges. A Console hard limit is required except for a manual
`--apply-verified-plan` run with `manual_plan_local_budget_only: true` in
the protected configuration. In that case leave `console_cap_confirmed` false
if no cap is set. The same cumulative and per-run reservations, auto-recharge
check and remaining removal gates still apply. Queue and scheduled runs
cannot use this exception.

Live gates: `live_authorized`, `console_cap_confirmed`, `auto_recharge_off`,
`removal_sources_complete`. DELETE additionally requires `archive_restore_accepted`,
`legacy_helpers_retired`, `fos166_runtime_accepted`, `fos216_backfill_accepted`.
Do not assert these merely because dependencies merged. Re-consent uses existing
interactive auth commands; the worker never opens a consent flow.

Before live execution, store the latest reviewed Console observation as
`worker-billing.private.json` with `actual_usd` (decimal string) and `observed_at`
(ISO timestamp). Preserve prior evidence independently. The worker reports actual
observations and reservations separately. It sums **every month** in the existing
`budget.private.json` and conservatively adds the actual observation when enforcing
$20. This intentionally double-counts potentially overlapping historical usage
until an owner-attested settlement is explicitly recorded. It never clears/rebases a
ledger or silently renews the allowance. New reservations survive failed requests
and crashes, and per-operation counts/amounts appear in each private run report.

**Optional one-time offline settlement.** Pause all writers using the billing
account, including scheduled jobs and other tools. Use the existing private state
directory, with the updated worker and manual commands. Run
`x-migrator --state-dir "$STATE" billing-cutoff`. Retain the returned cutoff hash,
all monthly reservation totals, and the prior observation privately. These totals
are local estimates, not charges.

The owner must verify the provider's **lifetime cumulative USD charges** for
the same account, project and spending scope, the timezone-qualified timestamp
through which charges are final, and the SHA-256 of the retained private evidence.
Verify that the evidence includes every reservation in the snapshot (including
requests ultimately charged zero), that no charge is pending, and that there was
no intervening usage or reservation through application. Do not substitute a
remaining balance, a top-up, a monthly subtotal, or a balance change. If these
facts cannot be established, keep the conservative accounting. Once verified:

```
x-migrator --state-dir "$STATE" billing-settle \
  --expected-cutoff "$CUTOFF_SHA256" --provider-cumulative-usd "$CUMULATIVE_USD" \
  --settled-through "$SETTLED_THROUGH" --evidence-sha256 "$EVIDENCE_SHA256" \
  --attest-full-coverage
```

Both commands are offline. Settlement accepts the entire reviewed snapshot
once, without editing the ledger or observation. Future reservations count at
full value on top of the settled actual (or higher observed actual). The $20
lifetime limit and narrower run, request, and manual monthly limits still apply.
Partial or repeated settlement is unsupported. An incomplete checkpoint or
monthly regression stops reservations: retain all files for inspection rather
than deleting a checkpoint or resetting a month. Settlement authorizes no live work.

## Recovery and safety

* Existing immutable archive receipts are restored and adopted before a new pass.
  Every native removal intent and reviewed legacy-helper mapping is scanned with
  the shared FOS-216 parser. Confirmed, unknown and rejected attempts hold the
  exact ID; a later re-save is **not** inferred to be the old generation. The only
  automatic retry is a validated native HTTP 429 rejection after its durable reset,
  with a completely fresh archive/category/discovery gate and new attempt receipt.
  Re-save or other rejection resolution needs a separately reviewed disposition. Missing,
  orphaned, changed or incomplete receipts fail closed.
* One daily pass reads bounded first windows (one page per stream, 200 retained
  records, 20 folders). On a fresh discovery pass, select at most `max_items`
  eligible primary records in inventory order. A main-stream post whose body
  is already present uses one slot. Only pending bodies in that selection are
  looked up. Other observed IDs and their folder memberships stay in the private
  inventory. Page reads, expansions, reference enrichment for selected posts,
  and verification still need their own reservations and request bounds.
  No old positional cursor is resumed after DELETE or
  restart. Existing permanent blockers and attempts are excluded from hydration;
  known transient media failures get a daily retry. Unknown category is retained;
  this inherited transaction requires a positively identified single named folder.
  Confirmed unfiled items remain blocked under the current pilot contract.
* Each selected removal gets a fresh remote restore/hash check, merged catalog
  publication, fresh plan, and the existing v2 apply gate. Non-media records use
  the shared idempotent create/adopt adapter and its final fresh exact-ID GET.
  The narrow existing affirmative media-only test is unchanged. Source/article
  bodies never enter Raindrop fields.
* Atomic create-only restore now has an explicit `existing_ok` recovery option:
  identical owner-written regular files (private or explicit reader-group-only) may be reused **after fresh remote reads**;
  conflicting bytes and symlinks fail. Partial temp files are never accepted as
  final restores. Default standalone restore still refuses existing destinations.
* Publication merges all prior records/versions and reapplies annotations under
  the catalog lock. The state lock also excludes manual migration commands. Lock
  order is state → catalog → Raindrop. Catalog publication is complete before
  apply takes its own catalog lock; no nested acquisition of the same lock.
* HTTP 429 reset/Retry-After is persisted before handing the response back to the
  provider code. The whole worker stops. Every later HTTP request is checked
  against that durable deadline; missing headers use a conservative 15 minutes.
  Raindrop's numeric failure context is preserved through the shared adapter.
  A lightweight systemd tick checks due time every five minutes; no external call
  occurs before reset, and ordinary successful runs remain daily. This is bounded
  retry scheduling, not a sleeping process or paid retry loop. Before any provider
  work, the worker durably records the next daily deadline. Non-rate-limit failures
  (including filesystem/schema exceptions) retain a full-day deferral; a hard kill
  or inability to write a later error report cannot erase the prewritten deadline.
  Retryable Raindrop errors without provider reset evidence also wait a day.
  Per-item retry deadlines survive shared-batch adoption and govern queue selection;
  an entirely delayed queue retains its inventory instead of invalidating queued
  receipts with a new discovery pass. Fixed failure text goes to stderr/journald;
  detailed evidence stays in the protected report.
* OAuth rotation writes an intent before refresh and stores the new token atomically
  before other work. All normal CLI clients respect it. An interruption with the
  old token still installed stops as `owner_reconsent_needed`; a new durable token
  permits cleanup without another refresh. `invalid_grant` never loops.
* DELETE intent/response/result/window files remain authoritative. No worker
  exception handler retries an uncertain or successful DELETE. A missing response is unknown, even when a
  current listing no longer contains that ID. Post-delete reads remain part of
  the inherited transaction; a failure stops the run with the successful receipt
  retained.

`worker.private.json` contains the resumable queue and per-stream observed IDs,
counts, blocked state and window exposure. `worker-report.private.json` contains
counts, failures, cumulative reservations and billing observations. Detailed IDs,
paths, categories and provider evidence remain private. Empty/not-due/deferred
runs exit quietly; failed work reports a nonzero exit. No report claims inaccessible
history complete or equates new observed IDs with proven older bookmark age.

Before upgrading code or rebuilding a full catalog manually, use the same state
lock and catalog lock in that order. Preserve the published catalog and annotations;
never build a one-batch replacement over the complete catalog. The worker merges
partial batches; the standalone `catalog-build` command still expects the complete
batch list. Do not overwrite the pinned checkout while a worker is running.

## First-party contracts rechecked 2026-11-16

| Reference | Contract / remaining uncertainty |
| --- | --- |
| [OAuth PKCE/refresh](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code) | `offline.access` refresh; conservatively treat interrupted rotation as uncertain. Live rotation behavior remains a local acceptance check. |
| [Main bookmarks](https://docs.x.com/x-api/users/get-bookmarks) | Full post objects and endpoint pagination. Daily discovery is bounded, not all-history proof. |
| [Folder list](https://docs.x.com/x-api/users/get-bookmark-folders), [folder contents](https://docs.x.com/x-api/users/get-bookmarks-by-folder-id) | Request pagination parameters do not establish complete response coverage; retain positive exact identity. |
| [Post hydration](https://docs.x.com/x-api/posts/get-posts-by-ids) | Exact requested IDs; retain original expansions/reference provenance. |
| [Bookmark DELETE](https://docs.x.com/x-api/users/delete-bookmark) | Existing exact-ID transaction; generic `bookmarked:true` example is not accepted as removal success. |
| [Rate limits](https://docs.x.com/x-api/fundamentals/rate-limits) | Main 180/15 min; folder list/members 50/15 min; DELETE 50/15 min. Response deadlines govern deferral. |
| [Pricing](https://docs.x.com/x-api/getting-started/pricing) | Owned Reads eligibility is conditional; folder prices and actual account charges require Console evidence. Never hardcode a per-run or monthly renewal of $20. |
| [systemd timers](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html), [execution](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html) | Non-root service plus persistent local tick. Catch-up can run immediately when enabled; install and enable are separate. |
| [OpenClaw payloads](https://docs.openclaw.ai/automation/cron-jobs/payloads) | Current command payload capability is not proof the installed Gateway supports it; no admin job or Docker access is requested. |

All automatic tests use invented data and mock providers. Local tests do not prove
installed versions, live billing, archive mount access, Raindrop list visibility,
scheduled execution, or owner acceptance.
