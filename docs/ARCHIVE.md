# Archive and independently retrieve saved content

FOS-165 implementation; live acceptance is **not complete**. No X DELETE endpoint
is implemented. `bytes_verified` means the selected response and supported media
were saved and retrieved byte-for-byte. It is not permission to unbookmark, proof
of full history coverage, or a claim that a user has reviewed the content.

## Storage decision and observed evidence

**Implement direct Google Drive API with the human owner's offline OAuth grant.**
This reuses the existing archive and needs no new service, database, mount daemon,
or subscription. Production selection is provisional until the actual OpenClaw
runtime passes `archive-probe`, pilot import and representative restoration.

| Option | Evidence and decision |
| --- | --- |
| Direct Drive API | Existing private archive, media/manifests/reports folders and Oct 14 report inspected through ChatGPT's connector. Three earlier asset receipts exist. Implemented here; the connector's access is not an OpenClaw credential. |
| Existing Drive mount/sync | No current mount or sync credential was available to inspect. A successful local file write would not establish upload or remote readback. Do not install a new sync daemon speculatively. |
| Existing OpenClaw filesystem | FOS-99 records a Docker runtime at UID/GID 1001 with persistent mounts. Neither the live mount paths nor Drive credentials were accessible here. Container writable layers alone are not a durable external archive. |
| Another object store | No configured bucket/runtime credentials were established. Adding a provider creates setup and billing work without evidence that Drive is unsuitable. Not selected. |

The private `openclaw-config` repository returned 404 through the GitHub connector;
its shell clone also failed authentication. This development environment has no
established connection to the deployed OpenClaw container. Do not infer that the
repository is absent, that no Drive mount exists, or that production was tested.
Actual X calls, Drive writes, media downloads and observed charges in this
implementation session: **none**. Connected Drive inspection was read-only.

Keep the destination ID, pilot manifest ID and all credentials in private runtime
configuration. The exact existing links are in Linear FOS-113/FOS-165; they are
intentionally not copied into this public repository.

## API contract checked before implementation

Reviewed 2026-10-15. Local tests use invented responses and files. The observed
column distinguishes earlier owner/connector evidence from this implementation.
The bookmark schema, lookup endpoint and partial-error contract were rechecked
against the linked official documentation while addressing PR #4 reviews.

| Endpoint / official reference | Documented contract used | Observed / failure handling |
| --- | --- | --- |
| [Bookmarks](https://docs.x.com/x-api/users/get-bookmarks) | OAuth bearer; current enums use `post.fields`, `note_post`, `article`, `attachments`, `entities`; expansions include `author_id`, `attachments.media_keys`, `referenced_posts`; media `url,type,variants`. Also documents `attachments.media_source_tweet_id` and the `attachments.media_source_tweet` expansion. | Existing saved read evidence is reused. No new live request here. Missing request provenance or content blocks completeness. Source-media attachments are explicitly blocked, including on quotes/reposts and when ordinary media keys coexist; their source is not yet archived. |
| [Folder contents](https://docs.x.com/x-api/users/get-bookmarks-by-folder-id) | `data` contains IDs, not hydrated bodies. Request parameters do not prove complete response coverage. | Owner previously observed the same capped window without metadata. Preserve positive named-category evidence; absent membership stays unknown. |
| [Lookup index](https://docs.x.com/x-api/posts/lookup/introduction), [GET /2/tweets](https://docs.x.com/x-api/posts/get-posts-by-ids) | 1–100 exact IDs, selected fields/expansions; returned data and optional errors. Current enums do not list the old nested referenced-tweet media expansion. | `archive-enrich` explicitly looks up direct quote/repost IDs, at most 20 per primary. Missing nested context remains blocked. No guessed expansion or paid fallback. |
| [Data dictionary](https://docs.x.com/x-api/fundamentals/data-dictionary) | Generic documentation still describes `note_tweet.text`, while endpoint schemas show `note_post.text`. Article shape is not specified sufficiently to prove its full body. Variants expose `content_type`, `bit_rate`, `url`. | Preserve both documented long-text dialects; conflicting values block. Every Article stays `blocked_incomplete_article`, even if an unverified `text` member appears. Highest-bitrate observed MP4 is selected; HLS/posters do not substitute. |
| [Errors](https://docs.x.com/x-api/fundamentals/response-codes-and-errors), [bookmark integration](https://docs.x.com/x-api/posts/bookmarks/integrate) | A successful HTTP response can contain partial errors. Authentication/access/rate errors differ from deletion. | Existing read-only user grant and reservation caps remain. No automatic retries; partial/missing references block affected archives. |
| [Drive upload](https://developers.google.com/workspace/drive/api/guides/manage-uploads), [files.create](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/create), [generateIds](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/generateIds) | Create metadata, obtain a resumable upload session, PUT bytes. Pre-generated IDs support safe create retries; an already completed create can return 409. | Persist the ID before creation. On an explicit rerun, inspect the same ID and GET its bytes; never overwrite. An interrupted partial upload restarts a session with the same file ID, not a new object. No live write here. |
| [files.list](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list), [search](https://developers.google.com/workspace/drive/api/guides/search-files) | Parent query and private appProperties identify objects; pagination/incomplete search must be handled. | A bounded exact-key search recovers the remote object if the local journal was lost. Ambiguous or incomplete results stop instead of choosing a duplicate. One serialized writer/state directory is required. |
| [files.get](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/get), [downloads](https://developers.google.com/workspace/drive/api/guides/manage-downloads), [about.get](https://developers.google.com/workspace/drive/api/reference/rest/v3/about/get) | Metadata/capabilities and `alt=media` byte retrieval; account identity and storage quota. | Root ownership/write capability and account pin checked. Every saved asset/payload/manifest is downloaded and SHA-256/size checked. This implementation supports an owner-controlled My Drive folder. |
| [Scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth), [quota/errors](https://developers.google.com/workspace/drive/api/guides/handle-errors) | `drive.file` covers files created/opened/shared with that app, not automatic access to all existing files. Service accounts lack personal storage quota/file ownership. | Human owner OAuth only. Existing root AND each pilot file must be readable with the chosen grant. 401/403/429/quota errors stop, without duplicate-create retries. |
| [Desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app), [Google Python quickstart](https://developers.google.com/workspace/drive/api/quickstart/python), [helper](https://google-auth-oauthlib.readthedocs.io/en/latest/reference/google_auth_oauthlib.flow.html), [refresh](https://developers.google.com/identity/protocols/oauth2/web-server) | Loopback consent, offline grant, PKCE and token refresh. | One-time desktop helper saves only private credentials; unattended commands refresh directly. API/refresh endpoints are fixed, redirects disabled; bearer tokens never go to the media client. |

## Drive authorization

Use an existing human-owned OAuth grant if the deployed environment already has
one with the required access. Store a Google `authorized_user` JSON containing
`type`, `client_id`, `client_secret`, `refresh_token` as
`drive-credentials.private.json` in the migrator's state directory. The directory
must be owned by the executing user, mode 0700; files mode 0600, outside Git.
The existing X state and Drive state use the same lock. Do not share a writable
state directory among simultaneous job instances or different OAuth owners.

If no suitable grant exists, enable the Drive API in your Google project and
create a Desktop OAuth client using the linked Google quickstart. Download its
client configuration outside Git. On the desktop with the browser, run:

```sh
uv run --with 'google-auth-oauthlib>=1.2,<2' python scripts/authorize_drive.py \
  --client-file /private/path/desktop-client.json \
  --state-dir /private/path/migrator-state --scope drive.file
```

`drive.file` is appropriate only if that app already has access to the archive
root and pilot assets, for example through an existing Picker integration.
This CLI does not implement Picker; pasting a folder ID does not grant access.
For the pre-existing archive created by another app, an owner may deliberately
authorize `--scope drive` instead. That is broad Drive access; it is never silently
requested or upgraded. The implementation still only creates archive objects and
reads selected files. Personal OAuth apps left in Google's testing mode can have
short-lived refresh grants; use the appropriate consent configuration for an
unattended personal application, and verify refresh in the intended runtime.

Provision this credential through the existing protected OpenClaw secret/state
mount, retaining the effective runtime UID (recorded baseline: 1001). Do not put
it in the image, public repository, ChatGPT messages or command arguments. The
desktop authorization helper is a one-time setup; it does not run in the job.

## Runtime acceptance and commands

Run from the installed migrator inside the **actual selected OpenClaw execution
environment**, using its existing persistent state mount. `--state-dir` below is
omitted only when that mount is the default `~/.local/state/x-bookmark-migrator`.
Set `X_ARCHIVE_DRIVE_FOLDER_ID` privately, for example in the ignored `.env`.
Do not guess Compose mounts or redeploy OpenClaw as part of this code PR.

```sh
uv sync --locked
uv run --env-file .env x-migrator archive-probe
uv run --env-file .env x-migrator archive-import-pilot --manifest-id "$PILOT_MANIFEST_ID"
uv run --env-file .env x-migrator archive --max-posts 5
```

The probe creates a small unique file, refreshes again through a fresh backend,
and GETs/hash-checks it. Its private receipt captures root/account permission ID,
reported OAuth scope, storage quota, effective UID and container marker. Those
markers do **not** themselves prove it ran in production: record the actual job
invocation/runtime alongside the result. All commands print counts/status, never
token values, post bodies, account IDs or private categories.

Pilot import reads the Oct 14 JSONL directly from Drive and verifies each of its
three preserved assets through the same API grant. It imports hash/file-ID
associations, not old DOM text or fictional API provenance. Matching new source
bytes reuse the old file in place and record that legacy placement; a different
API rendition is a new distinct asset. No old file is moved or overwritten.

Archival reuses the active inventory and saved read responses. It makes **zero X
API calls**. Repeat `--post-id` to choose specific inventory IDs; otherwise it
uses the first five. It does not automatically skip blocked records or drain a
folder. For a folder-only ID without a body, finish the existing inventory's
bounded hydration first. If direct quote/repost expansions lack authors or media, run the
explicit paid step using the same budget configuration as inventory:

```sh
uv run --env-file .env x-migrator archive-enrich --max-posts 5 \
  --console-hard-cap-confirmed --auto-recharge-off-confirmed
```

This unions direct reference IDs across the selected primaries and reads them in
chunks of at most 100 IDs. The default five primaries need at most one identity
read and one lookup; the maximum 20 primaries with 20 references each need at
most one identity read and four lookups. Shared IDs are requested once per run.
The existing per-call/run/month reservation settings remain required; fewer
requests are not a claim about per-resource billing or actual cost. Repeating
this explicit command repeats paid reads; there is no hidden retry. Actual
Console charges must be recorded separately. The archived payload retains every
raw response. Explicit tweet errors block the affected references (including
nested references); unscoped or other resource errors conservatively block all
primaries sharing the lookup evidence. Missing authors/media stay blocked.

The result identifies a private batch receipt. Its `manifest.file_id` and
`manifest.sha256` are the portable entry point and integrity anchor. **Before
accepting each batch, copy its receipt, or at least both fields, into the existing
private job record or private Linear acceptance record on storage independent of
the OpenClaw state mount.** Record the runtime invocation and backup location
there too. A second file on the same mount is not sufficient. Do not put these
identifiers in this public repository or PR. The CLI does not perform this backup:
`bytes_verified` alone does not establish recoverability after losing state.
Drive's own manifest properties are not an independent integrity anchor.

For the acceptance restore, obtain the ID and hash from that separate record,
without consulting the original state directory. In a **fresh state directory** containing only the same
Drive grant (no X token/inventory/journal), reconstruct to an empty private folder:

```sh
uv run --env-file .env x-migrator --state-dir /private/path/fresh-state \
  archive-restore --manifest-id "$ARCHIVE_MANIFEST_ID" \
  --manifest-sha256 "$ARCHIVE_MANIFEST_SHA256" \
  --output-dir /private/path/reconstructed
```

The restore downloads the remote JSONL, then payloads and every recorded asset;
checks hashes, sizes and ID/category associations; and writes owner-only files
without replacing anything. An interrupted restore can be repeated into a new
empty output directory. The JSON payloads carry full returned text, exact IDs,
permalinks, authors, category ID/original names, references, all observed variants,
quality caveats, media receipts and raw official evidence. No X/browser is used.

## Failure and recovery contract

- Categories use stable `folder-<id>` directories, preserving exact original names
  in JSON. Unknown/conflicting categories remain explicit and blocked. One positive
  folder association is usable even when the folder window cannot be paginated;
  absence from that window never establishes unfiled status.
- Files use post ID/media index names. Within a category, SHA-256 content identity
  reuses bytes; receipts record the actual existing Drive filename. `batch_id`
  matches that manifest's filename stem, including reused legacy manifests;
  `receipt_id` distinguishes local run receipts. New manifest filenames use their
  content hash, so unchanged reruns retain the same batch identity. Per-batch
  JSONL and local receipts are create-only. The mutable journal is an index, not
  the sole archive. Run only one writer with the persistent state lock.
- Defaults: five posts; 64 MiB per file. Explicit limits allow up to 20 posts and
  256 MiB per file. Assets are processed individually in bounded memory. Reference
  traversal is capped at two levels/20 posts and 16 attachments per referenced
  post; excess is blocked. Larger files, unknown containers, HLS-only video,
  missing quotes, source-media attachments, polls, inaccessible media, malformed notes and Articles remain
  blocked. No source URL rewriting, scraping or thumbnail-as-video fallback.
- PNG/JPEG/WebP signatures/end markers and MP4 top-level boxes are checked to
  reject obvious corruption or wrong content. This is not a complete decoder.
  Live acceptance must independently decode photos and play/probe representative
  videos. Compare decoded photo dimensions against the returned API `width` and
  `height` and record the result privately; missing dimensions or a mismatch
  leave full-size quality unverified. Do not rewrite source URLs to guess an
  original. Logical animated GIF remains distinct from its MP4 container.
  Source rendition quality remains unverified (`derivative: "unknown"`);
  `transformed_by_archiver: false` states only that the downloaded bytes were
  saved unchanged. Strict JPEG end-marker checks remain fail-closed; investigate
  rejected live examples before broadening accepted formats.
- Interrupted uploads retain their pre-generated IDs. Explicit rerun checks the
  remote object before creation, and verifies existing bytes. A mismatch stops;
  it never overwrites corruption or chooses a new ID to conceal it. Partially
  uploaded batches can leave unreferenced create-only objects, which reruns reuse.
- Storage failure stops the batch immediately, including failed media uploads or
  legacy asset readback; no batch manifest or completion receipt is issued.
  Individual CDN download failures are retained as blocked records while
  other posts can still archive. Restore failures never authorize deletion.
- Archive/restore exit 1 if any record is blocked. Successful records still exist
  in the immutable manifest. Every record retains `deletion_eligible: false`;
  FOS-167 must separately reconcile current category/content evidence and commit
  the authorized archive-to-unbookmark transaction.

## Acceptance still required to close FOS-165

Local verification: all 203 tests pass, including the new storage/archive/CLI
failure and fresh-state recovery cases, alongside existing OAuth/inventory tests.
A separate rehearsal generated a PNG and H.264 MP4 with FFmpeg, archived/restored
them alongside a text-only post through HTTP doubles, then decoded both restored
assets successfully. That proves the local byte pipeline with valid media; it is
not a live X/CDN/Drive test. Ruff lint/format and whitespace checks also pass.

Confirm actual runtime/credential/mount access; pass unattended probe and reuse
the three pilot assets; archive 3–5 representative official API records (text,
multi-photo, ordinary video and GIF where available, plus a named category);
back up each restore anchor outside the state mount; restore using that separate
record with fresh state and no X access; inspect text and decode media, compare
photo dimensions, and check whether plain quotes include their authors; document
unsupported cases and actual charges privately. Fixture tests and merged code
do not satisfy this gate. FOS-167 live deletion remains blocked until it passes.
