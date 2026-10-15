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
`x-migrator inventory-export` regenerates an export offline; `inventory-recover`
can reprocess a saved rejected folder response without another API read. See
[docs/INVENTORY.md](docs/INVENTORY.md) for commands, limits, restart rules and live
verification still required. Partial category scans never become deletion permission.

## One-time owner setup

1. In the [X Developer Console](https://console.x.com), open the funded app and enable
   OAuth 2.0. A native/public client needs no client secret; confidential clients
   use `X_CLIENT_SECRET` supplied through a protected runtime environment.
   Register the exact callback `http://127.0.0.1:8765/callback`.
2. Record the credit balance and current prices. Retrieve the numeric user ID
   after OAuth setup using the instructions below.
   Set a hard billing-cycle spending limit and turn auto-recharge **off** before reads.
   The CLI flags below attest to these manual checks; they do not configure or verify Console.
3. In a local interactive terminal, export `X_CLIENT_ID` (the OAuth 2.0 Client ID,
   not an API key or app-only bearer token). Alternatively, copy `.env.example` to
   ignored `.env` and fill it locally; use `uv run --env-file .env` in place of
   `uv run` for the auth, pilot and inventory commands. The CLI does not load it
   automatically. Pilot and inventory read `X_APP_OWNER_ID` from that process
   environment. An explicit `--app-owner-id` overrides it; no shell export is needed.
4. Run `uv run x-migrator auth-start`, open the printed official authorization URL,
   and approve as the bookmark owner. It requests only
   `tweet.read users.read bookmark.read offline.access`. Write scope is deliberately
   deferred until FOS-167 and will require new consent.
5. The browser redirects to an unserved localhost callback, so a connection error is
   expected. Copy the **entire address-bar URL**, run `uv run x-migrator auth-finish`,
   and paste at its hidden terminal prompt within ten minutes. Never paste that URL
   into chat, tickets, shell commands, or Git. No local web server is required.
6. Follow [Find your numeric account ID](#find-your-numeric-account-id) before the
   pilot or inventory. You do not need that ID to complete OAuth setup.

Tokens and pending OAuth state live in `~/.local/state/x-bookmark-migrator`:
owner-only directory (0700), atomic/fsynced files (0600), one process lock covering
refresh and reads. This is local filesystem protection, **not encryption at rest**;
use a trusted account and encrypted disk. `--state-dir /absolute/private/path` can
select an existing secret volume, outside any Git checkout. Production OpenClaw
mount/UID/secret provisioning remains for FOS-165/168; it has not been validated.
Never sync this directory to Drive or commit it. No live state belongs in CI.

## Find your numeric account ID

`--app-owner-id` / `X_APP_OWNER_ID` means the numeric **X user ID of the account
whose bookmarks you are reading**, which must also own the funded developer app
for the intended Owned Read setup. It is not the app's ID, OAuth Client ID, API key,
or an `@username`. The official identity endpoint is
[`GET /2/users/me`](https://docs.x.com/x-api/users/get-my-user); it returns the user
represented by your OAuth grant. It does not independently prove app ownership or
billing eligibility: confirm that account owns the app in the Developer Console.

**Already ran the pilot? Recover its saved identity without another paid read:**

```sh
uv run python - <<'PYCODE'
import json
from pathlib import Path
from x_bookmark_migrator.core import Store, exact_id
store = Store(Path.home() / ".local/state/x-bookmark-migrator")
with store.locked():
    for path in sorted(store.root.glob("read-*.private.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True):
        saved = store.read(path.name)
        body = saved.get("response", {})
        if saved.get("endpoint") != "/2/users/me" or saved.get("status") != 200:
            continue
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, dict) and not body.get("errors"):
            print(json.dumps({"username": data.get("username"), "id": exact_id(data.get("id"))}))
            break
    else:
        raise SystemExit("No saved identity found; use the one-read lookup below.")
PYCODE
```

Check that the displayed account is the one you authorized and that owns the app.
Saved evidence may belong to an earlier login; if uncertain, use the fresh lookup.
For a custom state directory, replace the `Store(...)` path in these snippets with
that same absolute private directory.

**No saved identity? After `auth-finish`, make one official identity lookup.** First
confirm the Console hard cap and auto-recharge off, as for the pilot. This reserves
an illustrative $0.10 against the same $1.00 UTC-month ledger; these amounts are
operator estimates, not a price guarantee. Adjust them deliberately for your budget
and current Console pricing. This command uses the existing client, token refresh,
lock and budget controls; it does not request bookmarks or require the unknown ID.

```sh
uv run --env-file .env python - <<'PYCODE'
import json
import os
from pathlib import Path
import httpx
from x_bookmark_migrator.core import Budget, Client, Store, exact_id
store = Store(Path.home() / ".local/state/x-bookmark-migrator")
with store.locked(), httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as http:
    client = Client(store, http, os.environ["X_CLIENT_ID"], os.environ.get("X_CLIENT_SECRET"))
    budget = Budget(store, "0.10", "0.10", "1.00")
    body = client.get("/2/users/me", {}, budget.reserve)
    data = body.get("data")
    if body.get("errors") or not isinstance(data, dict):
        raise SystemExit("Identity incomplete; inspect the saved private read evidence.")
    print(json.dumps({"username": data.get("username"), "id": exact_id(data.get("id"))}))
PYCODE
```

Omit `--env-file .env` if `X_CLIENT_ID` is already exported. Both snippets print only
identity fields locally, never tokens or bookmark bodies. Keep the resulting account
ID/username in your local configuration; do not copy them into public examples,
issues, PR comments or committed files. Put the verified numeric ID in the
`X_APP_OWNER_ID` entry of your ignored `.env` using your local editor. No shell
substitution or export is needed when running with `uv run --env-file .env`.

## First real pilot: one command after OAuth setup

Set `X_APP_OWNER_ID` using [the identity instructions above](#find-your-numeric-account-id),
confirm the account owns the app in Console, then run:

```sh
uv run --env-file .env x-migrator pilot \
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
nested referenced-post media expansion. The owner has verified the `post` dialect on the main bookmark endpoint.
That does not establish a successful folder-only lookup; track it separately.
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

The [API audit](docs/API_AUDIT.md) records endpoint contracts, corrections and
remaining live-verification gaps.

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
