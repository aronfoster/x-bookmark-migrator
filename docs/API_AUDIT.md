# Official API audit and correction

The FOS-165 archive/Drive integration has a separate current
[API contract and storage audit](ARCHIVE.md#api-contract-checked-before-implementation),
reviewed 2026-10-15. Its synthetic tests do not establish deployed OpenClaw access.

Reviewed 2026-10-14 (America/Denver). This audit covers all three source modules,
CLI entry points, tests and runbooks. All execution here used synthetic responses;
no authenticated X calls, real content, account identifiers or charges are published.

## Documentation required before implementation

Read the current official references for every affected endpoint and shared API
behavior before changing code. Cite direct links in the issue and PR, identify
conflicting examples or undocumented behavior, and test the documented response
shapes plus sanitized observed failures. Do not substitute mocks for live evidence.

| Area | Official reference | Contract used |
| --- | --- | --- |
| Identity | [Get Users Me](https://docs.x.com/x-api/users/get-my-user) | `/2/users/me` identifies the token's user; compare exact string ID before bookmark reads. App ownership remains a Console check. |
| Main inventory | [Get Users Bookmarks](https://docs.x.com/x-api/users/get-bookmarks) | Full Post objects, fields/expansions and response pagination metadata. |
| Folder enumeration | [Get Users Bookmark Folders](https://docs.x.com/x-api/users/get-bookmark-folders) | Folder ID/name objects; request pagination parameters; current response schema omits `meta`. |
| Folder membership | [Get Users Bookmarks by Folder ID](https://docs.x.com/x-api/users/get-bookmarks-by-folder-id) | Post ID objects; current response schema also omits `meta`. |
| Hydration | [Get Posts by IDs](https://docs.x.com/x-api/posts/get-posts-by-ids) | `/2/tweets`, 1–100 requested IDs; validate returned IDs against that batch. |
| Authentication | [OAuth PKCE](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code) | User authorization, PKCE, offline access and refresh. Keep the existing read-only grant and serialized token rotation. |
| Pagination | [Pagination](https://docs.x.com/x-api/fundamentals/pagination) | Follow supplied continuation tokens. The generic guide does not resolve the folder response-schema gap. Never synthesize cursors. |
| Fields | [Fields](https://docs.x.com/x-api/fundamentals/fields), [Expansions](https://docs.x.com/x-api/fundamentals/expansions) | Preserve returned objects and shared expansions. Endpoint enums use `post` names while generic examples still use `tweet`; no automatic paid fallback. |
| Errors | [Response codes and errors](https://docs.x.com/x-api/fundamentals/response-codes-and-errors) | HTTP 200 can include partial errors. Retain positives; do not infer missing records are deleted. |
| Rate limits | [Rate limits](https://docs.x.com/x-api/fundamentals/rate-limits) | Preserve numeric rate headers; stop on 429, with explicit later retry after the reset. |
| Cost | [Pricing](https://docs.x.com/x-api/getting-started/pricing) | Billing is resource-based; Console spending limits are distinct from local reservations. Owned Read eligibility is conditional; folder prices remain unverified. |

## Review verification

Rechecked the two linked folder endpoint pages on 2026-10-14 after review. Both
currently show `max_results` with a request range of 1–100 and response `data` and
`errors`, without `meta`. Neither page states that over-return is supported or
expected. Accepting extra valid records is a defensive local policy based on the
reported live response, not an X guarantee that `max_results` may be ignored.

Review follow-up adds setting-specific budget errors (including blank environment
values), redacted malformed-ledger errors, exact-linked recovery success/mismatch
tests, clearing the obsolete failed-read link after acceptance, and shared preflight
limit validation. Original evidence remains in the accepted page and private read.

## Corrections and regression evidence

- **Environment:** pilot/inventory now read `X_APP_OWNER_ID` from the process
  environment. Explicit arguments win, including an explicitly invalid empty value.
  Missing/invalid values explain the numeric user-ID requirement without echoing
  private input. Budget options also accept documented process-environment defaults.
  A subprocess regression executes `uv run --env-file` and the installed CLI.
- **Response validation:** pilot and inventory share one validator with
  endpoint-specific record requirements. A requested `max_results` is no longer
  treated as a maximum valid response length. Folder records contain IDs, not text.
  Lookup batches still reject excess, duplicate or unrelated returned IDs.
- **Retention limits:** retain known memberships even when new IDs exceed capacity;
  fill remaining capacity deterministically, count omissions and stop that stream
  without advancing its saved cursor. Preserve the complete raw diagnostic. Tests
  cover over-return beyond both the requested size and 100 records; 100 is a request
  limit, not an invented response-array ceiling. These bounds limit retained record
  counts, not HTTP response bytes or disk accumulation.
- **Coverage:** missing/empty/unrecognized pagination metadata cannot establish an
  end. Valid folder data without metadata is retained with `pagination_unverified`,
  not rejected as malformed. Positive membership remains useful; absence remains
  unknown. This deliberately leaves an unresolved external coverage gap visible.
- **Recovery:** `inventory-recover` reprocesses a matching rejected folder read
  offline. Validate the endpoint, request, timestamp and failed-read link when present;
  preserve original observation timestamps. Legacy matching is restricted to the
  oversized-page failure and cannot prove negative category evidence. Tests verify
  unchanged tokens, reservations and raw evidence; reject unrelated files and replay.
- **Reporting:** distinguish post-body status from lookup requests and accepted
  lookup batches/records. A six-request synthetic probe retains 20 memberships,
  one main body and two looked-up bodies; resume hydrates the remaining 17 without
  repeating the folder read. It still reports incomplete coverage.
- **Preflight:** reject invalid request/hydration limits before creating a new pass,
  preserving the active checkpoint. Reject negative/nonfinite persisted budget
  totals instead of allowing them to weaken the reservation cap.
- **Privacy:** restrict Store filenames to basenames. Pilot summaries print only
  recognized field names, so arbitrary response keys cannot leak private strings.
  Raw data stays in the private files. No new write scopes or destructive endpoints.
- **Runbook:** remove shell-expanded owner-ID arguments, false one-item response /
  five-request guarantees and stale live-status wording. Document local versus
  server limits, offline recovery and remaining verification honestly.

Existing tests also cover OAuth/state validation and refresh rotation; owner mismatch;
HTTP/transport failures without automatic retries; caps across runs; split/restarted
pagination; duplicate pages/tokens; multi-folder membership; malformed-ID isolation;
partial reference errors; unavailable-post caching and explicit retry; shared expansion
exports; old checkpoint upgrade; file permissions and create-only export behavior.

## Remaining external facts

No documentation-backed mechanism here proves complete coverage when folder responses
omit pagination metadata. The parser preserves all received evidence and reports the
gap. Do not relabel it complete, invent cursor parameters, use a private API, or permit
deletion on this basis. A real successful lookup and actual billing deltas must be
verified in the authorized runtime. Inventory remains observation, not an archive
or deletion receipt; content/media preservation belongs to the downstream work.
