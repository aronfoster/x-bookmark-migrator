# X bookmark migrator

FOS-163/164: **read-only official API pilot and resumable bookmark/folder inventory**,
not yet a production archive.
No bookmark deletion, scraping, private GraphQL, media downloads, or scheduler.

## Install and verify (Linux, Python 3.12+)

```sh
uv sync --locked
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```

## Bookmark and folder inventory

After OAuth setup, use `x-migrator inventory` for bounded, resumable reads and
private portable JSONL exports with original folder IDs/names and coverage status.
`x-migrator inventory-export` regenerates an export offline. See
[docs/INVENTORY.md](docs/INVENTORY.md) for commands, limits, restart rules and live
verification still required. Partial category scans never become deletion permission.

## One-time owner setup

1. In the [X Developer Console](https://console.x.com), open the funded app and enable
   OAuth 2.0. A native/public client needs no client secret; confidential clients
   use `X_CLIENT_SECRET` supplied through a protected runtime environment.
   Register the exact callback `http://127.0.0.1:8765/callback`.
2. Record the app owner's **numeric X user ID**, credit balance and current prices.
   Set a hard billing-cycle spending limit and turn auto-recharge **off** before reads.
   The CLI flags below attest to these manual checks; they do not configure or verify Console.
3. In a local interactive terminal, export `X_CLIENT_ID` (the OAuth 2.0 Client ID,
   not an API key or app-only bearer token). Alternatively, copy `.env.example` to
   ignored `.env` and fill it locally; use `uv run --env-file .env` in place of
   `uv run` for the auth, pilot and inventory commands. The CLI does not load it
   automatically. Export `X_APP_OWNER_ID` in your invoking shell for commands using
   `"$X_APP_OWNER_ID"`, or pass the verified numeric ID directly: `--env-file` cannot
   populate that shell substitution.
4. Run `uv run x-migrator auth-start`, open the printed official authorization URL,
   and approve as the bookmark owner. It requests only
   `tweet.read users.read bookmark.read offline.access`. Write scope is deliberately
   deferred until FOS-167 and will require new consent.
5. The browser redirects to an unserved localhost callback, so a connection error is
   expected. Copy the **entire address-bar URL**, run `uv run x-migrator auth-finish`,
   and paste at its hidden terminal prompt within ten minutes. Never paste that URL
   into chat, tickets, shell commands, or Git. No local web server is required.

Tokens and pending OAuth state live in `~/.local/state/x-bookmark-migrator`:
owner-only directory (0700), atomic/fsynced files (0600), one process lock covering
refresh and reads. This is local filesystem protection, **not encryption at rest**;
use a trusted account and encrypted disk. `--state-dir /absolute/private/path` can
select an existing secret volume, outside any Git checkout. Production OpenClaw
mount/UID/secret provisioning remains for FOS-165/168; it has not been validated.
Never sync this directory to Drive or commit it. No live state belongs in CI.

## First real pilot: one command after OAuth setup

Set `X_APP_OWNER_ID` to the numeric owner ID verified in Console, then run:

```sh
uv run x-migrator pilot --app-owner-id "$X_APP_OWNER_ID" \
  --console-hard-cap-confirmed --auto-recharge-off-confirmed \
  --per-call-reserve-usd 0.10 --run-cap-usd 0.25 --month-cap-usd 1.00
```

The **$0.10 per-call reservation is an illustrative operator estimate, not an X
price or guaranteed bound**. Review current Console pricing including expanded
resources before using it. The command makes `/2/users/me` then at most one
`/2/users/{id}/bookmarks?max_results=5` read. Identity must match the supplied app
owner. This comparison cannot independently prove app ownership or Owned Read billing.
It does not follow pagination or retry failed reads. Even an empty result may incur
an identity-read charge. A mismatch stops before the bookmarks read.

The CLI reserves estimated spend before each GET, persists monthly reservations
across runs, and retains them on errors/uncertain transport outcomes. Reusing the
same directory is essential. Changing directories or deleting the ledger resets
this accounting. Caps are per state directory and UTC calendar month, not an
account-wide meter or X billing-cycle meter. **Console's hard limit is the backstop**;
actual billing and expansion costs must be measured. There is no billing API call.

Stdout contains only counts, field names, pagination presence, estimated reservation,
expanded-data/media field names, rate-limit headers for both reads, and the generated
private filename. Missing/non-numeric rate-limit headers are omitted. Full JSON (including expansion objects and
opaque continuation token) is stored privately, never printed. These records are a
pilot snapshot, not a verified archive; missing categories/media/full text remain
unresolved. A response with partial errors prints its summary (including sanitized
`partial_error_types` and `pilot_status=partial`) and exits unsuccessfully. Known
resource-unavailable errors are useful read evidence, but are not silently accepted
as a complete pilot. Unknown error types are reported as `unknown`; raw details stay
private. No second paid run is necessary to recover this summary.
Every completed GET response is saved before HTTP/JSON/schema validation, including
malformed JSON and HTTP failures, in a unique `read-*.private.json`. Evidence includes
status, request parameters, and numeric rate-limit headers. OAuth token responses
are never included in these diagnostic files. On failure, inspect the latest private
read files; do not rerun a paid request just to recover diagnostics. Partial or malformed
responses still fail validation. Field presence is evidence of coverage, not proof of
complete long-form text or media archival.
All HTTP errors stop, with no raw bodies in stderr. 401 requires reauthorization;
403 requires entitlement/scope investigation; 429 requires waiting before a manual
rerun. Expiring tokens are refreshed before reads; rotated tokens are saved atomically.
A crash/network loss after X rotates a token but before local persistence can still
require reauthorization. Atomic writes cannot eliminate that remote/local gap.

## Field naming discrepancy: verify live, don't silently retry

The current [bookmark endpoint reference](https://docs.x.com/x-api/users/get-bookmarks)
uses `post.fields`, `note_post`, `referenced_posts`, and `includes.posts`.
FOS-163 and older examples use `tweet.fields`, `note_tweet`, `referenced_tweets`,
and `includes.tweets`. Default `--field-dialect post` follows the current endpoint
reference, including author/media/reference expansions and all requested media fields.
The legacy request is explicitly available as `--field-dialect tweet`; it includes
nested referenced-post media expansion. Neither dialect is live-verified yet.
There is **no automatic fallback or second paid request**. If the request is rejected,
inspect Console/current docs, select the appropriate dialect, and rerun deliberately.
Raw fields are retained without renaming or truncation. Current docs do not list
`author_id`/`referenced_posts` in the post-field enum or the older nested reference
media expansion: the current profile requests the documented expansions instead.
Live presence/full text coverage must be recorded before downstream archival.

## Finish M1

Use [docs/PILOT_REPORT.md](docs/PILOT_REPORT.md) to record sanitized observations:
identity match, endpoint status, actual grants, field coverage, pagination presence,
and Console balances/charges before and after. Never commit actual post text,
IDs, pagination tokens, callback URLs, or response files. Synthetic tests are not
proof of live access. Folder enumeration belongs to FOS-164; no folder request is
made here. No claim of all-history coverage or unbookmark behavior is made.

Official references: [OAuth PKCE](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code),
[pricing](https://docs.x.com/x-api/getting-started/pricing).

## Network environment

The CLI deliberately uses `trust_env=False`: `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`,
`SSL_CERT_FILE`, and `SSL_CERT_DIR` are not used. Run this pilot from a host with direct
HTTPS access and standard trusted certificates. Proxy/custom-CA support must be
configured and tested explicitly before deployment to a runtime requiring it; the
current OpenClaw network environment has not been validated.

Private `read-*.private.json` diagnostics accumulate without automatic cleanup. Keep
them only while needed to diagnose/report the pilot, then delete those read files
locally; retain tokens and the budget ledger to preserve authorization and accounting.
