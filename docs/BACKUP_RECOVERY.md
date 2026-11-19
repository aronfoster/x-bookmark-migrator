# Protected bookmark backup: recovery handoff

**Checkpoint update — 18 November 2026, supervised pause:** owner-run complete
local signature/decryption/member/directory/size/hash verification passed.
A separate symmetric GPG recovery-key backup passed local readback, using the
existing GPG passphrase. The owner confirmed that passphrase is independently
accessible. Quota/headroom and publication-reader checks passed; the owner added
only the ciphertext publication directory to Déjà Dup, and the settings delta was
verified. A new remote backup's start/completion, remote restore, independent
restored-key proof and protected staging cleanup remain unestablished.

The source machine is permanently retired. Do not repeat source-host diagnosis,
export/transfer, initial capture, completed local verification or folder inclusion.
Check current backup activity and successful snapshots before starting any job.
The owner explicitly rejected password-database access, copies, edits or
attachments; the unused vault helper was removed without execution.

The accepted recovery-key artifact is separately symmetrically encrypted with the
existing GPG passphrase, not encrypted to the private key it must recover. It sits
alongside the archive ciphertext for transport by the existing encrypted backup.
No plaintext key package, live keyring or plaintext verification area is included.
Recover both ciphertexts from the completed remote snapshot; reconstruct a fresh
keyring from the recovered key package and repeat complete archive verification.
The older preparation sequence below is a contract, not a claim that every step
remains undone. See the [S1 checkpoint](SPRINTS.md#s1--recover-existing-state) and
current FOS-169 evidence for the remaining work.

FOS-169, 17 November 2026. This is the remaining recovery procedure, not a new
capture/installation procedure. Preserve the successfully captured
generation, original archives and state. Do not rerun setup/capture, move state,
activate services, change grants, or resume X/Raindrop operations here. The later
owner-run existing Déjà Dup backup/restore in C is the only cloud activity in this
procedure; the remote preparation session performs none.

## What is implemented, and what is unavailable remotely

The November 17 local receipts establish successful protected capture and
unprivileged ciphertext/hash/SSH-signature verification. They do **not** establish
an independently trusted signer, owner decryption or off-host recovery.

The reviewed private setup/capture helpers, configuration, source-root mapping,
manifest/receipt schema and desktop retention helper are not in the remotely
available migrator tree. The private openclaw-config tree was also checked;
its other backup scripts are separate jobs, not the new bookmark capture helper.
Do not substitute them, their paths, their manifest format or their retention.
The migrator's `archive-restore`/`restore_batch` downloads individual Drive batches;
it does not restore this complete protected bundle and must not be used as proof.

This change supplies the recovery contract and a disposable-key crypto rehearsal.
It adds **no production key-transfer, decrypt/extract or settings-writing helper**.
The local agent must bind the following procedure to the actual private code,
reuse its existing verifier if it satisfies this contract, and fill only missing
checks. Do not invent a second manifest format or regenerate the captured bundle
to fit a verifier. Remaining local work is this binding and exact-command review,
then execution; remote review is not approval of unseen privileged commands.

## 0. Bind the private inputs once

Read the current FOS-169 and existing private rollout handoff. Preserve Gate D's
separate FOS-216 readiness release; this document only covers backup gates A–C.
Record these inputs in the existing private owner handoff, never in this repository
or public logs. Missing/mismatched input is a stop, not permission to rediscover
keys by reading their contents or to recreate completed setup.

| Required local input | Check before execution |
| --- | --- |
| Reviewed capture and setup source, installed identity | Match the exact November 17 approved hashes recorded in FOS-169, or stop for disposition of a changed revision. Owner supplies a protected hash result if agent cannot read installed code. |
| One immutable captured generation | Resolve the current pointer once; pin generation, ciphertext/signature/receipt names, hashes, sizes and capture inventory. Use that generation throughout; never compare a later `current` pointer. |
| Complete manifest contract | Read producer and existing tests: schema/version, path mapping for all accepted roots, directory accounting, manifest/control-file accounting, size/hash encoding, code identity and limits. |
| Trust anchors | Owner independently authenticates root signing public-key fingerprint from protected installation/record, plus GPG primary and encryption-subkey fingerprints from the approved recipient. A key delivered beside the ciphertext is not its own trust anchor. |
| Recovery locations and identity | Owner-selected root-only desktop keyring and temporary recovery area; protected ancestors, numeric owner/group, modes and ACLs; no agent, worker or container access. Neither is under the ciphertext output or any backup include. |
| Source-host transfer binding | Existing trusted SSH alias/key, actual source identity/keyring and supported GPG versions. Check the latest private connectivity receipt; do not infer power/address/key loss. |
| Desktop backup baseline | Actual user/session, installed version, existing set format, all include/exclude entries, destination identity, schedule/retention, current backup/retention activity and available remote quota. Keep raw settings/logs private. |
| Independent recovery materials | Existing Déjà Dup password/account access, signer anchor and owner-controlled key/passphrase recovery access that survives loss of this desktop. Do not put the sole key copy inside the backup encrypted to that same key. |

Review **exact bound scripts and invocations**, their hashes, fixed executables,
scrubbed environment and failure cleanup with an independent local reviewer.
Scripts executed by the owner with privilege must be protected against changes
between review/hash-check and execution, including imported code and ancestors.
No agent executes privileged host commands. No shell tracing, session recording,
credential-bearing command arguments or passphrases in environment variables.

## A. Preserve the existing owner key

1. Resolve the source host's SSH availability only if still necessary. If unavailable,
   use the owner's console to inspect listener/socket/service/address/log evidence. Keep
   existing trusted host-key validation; no blind `ssh-keyscan`, trust-on-first-use
   replacement, disabled checking or agent forwarding. This is diagnosis, not
   authorization to enable/reconfigure services.
2. Owner exports **only the pinned existing key**, including its usable encryption
   subkey, using GnuPG's secret-key export into owner-only storage. Do not create a
   replacement key, export every key or alter the existing key/job. A public-key
   export, secret-key stub or passphrase entry alone cannot decrypt the backup.
3. Transfer the passphrase-protected export over the authenticated channel directly
   into the selected protected desktop staging area. No temporary file or pipe
   endpoint readable by the agent's UID, no agent-captured SSH stdout, no clipboard
   or chat. Remote export and local destination must both remain owner-only.
   Verify protection rather than assuming the existing key has a passphrase.
4. Import into a **new isolated** owner-controlled GPG home (0700, files 0600,
   no ACL access for agents). Use explicit homedir/no-options, trusted binaries,
   no inherited agent socket/config, no network key discovery. Verify the full
   primary and subkey fingerprints and usable secret material; reject extra keys.
   Never use this keyring for the capture/encryption job.
5. After clearing **only this isolated keyring's** cached agent, owner enters the
   passphrase from the owner's password manager through private pinentry and
   decrypts the pinned bundle as specified in B. Do not kill the default or other
   backup GPG agent. Failed unlock,
   missing subkey or ambiguous fingerprint stops acceptance.
6. Preserve a recoverable owner-controlled copy independently of this desktop
   (retain the source host's working copy unchanged; use the owner's existing
   offline/vault storage for the export). Prove a fresh isolated import from the independently
   stored copy can decrypt this generation. If that location is not available,
   record **key disaster recovery pending**; local key import alone is insufficient.
   Do not retire the source host's key. Powering it down is distinct from erasing it.

The owner selects actual vault/media locations privately. The agent prepares and
reviews commands but never receives the private export, passphrase or plaintext.
Retain independently accessible public fingerprints and a sanitized generation
receipt for disaster recovery. A desktop-only password-manager database is not
an off-host passphrase recovery path.

## B. Verify the pinned generation completely

Copy the immutable ciphertext, detached SSH signature and capture receipt into
the owner-only verification area. Reject symlink/hardlink/special-file inputs and
untrusted writable ancestors; validate that bytes cannot change during use.
Verify copied sizes and SHA-256 against the pinned private receipt, then verify
the signature over those exact ciphertext bytes with `ssh-keygen -Y verify`, the
independently anchored key, and the **actual producer's** principal and namespace.
`check-novalidate` is insufficient. Do not confuse the SSH signature with GPG.
An unsigned receipt's claims are not authenticated merely by signing ciphertext;
cross-check them with authenticated decrypted contents and the independent record.

Decrypt with the new isolated GPG home, owner pinentry and explicit protected
output, with bounded output/disk usage and exit/status checks. Require exit zero
**and** machine-readable `DECRYPTION_OKAY`/`END_DECRYPTION`, reject failure/integrity
error statuses, and still require the complete plaintext checks below. Even
`DECRYPTION_OKAY`/`GOODMDC` followed by `FAILURE` is failure; evaluate the entire
status stream and process exit, not just a success line. Exit zero
alone is insufficient: the synthetic hosted rehearsal observed it for a severely
truncated input that processed no encrypted payload. Keep status on a separate
descriptor from plaintext and validate the installed version's status contract.
Never stream
plaintext to an agent tool or into an extracting tar process before GPG succeeds.
Remove/quarantine failed partial plaintext privately; never mark it verified.

The verifier must meet all of these requirements against the **existing format**:

* Validate the single expected manifest/control members, supported schema and
  code identity. Reject malformed types, duplicate JSON keys/manifest entries,
  invalid hashes/sizes and ambiguous normalized paths. Account explicitly for
  control members that cannot list their own hash.
* Require equality of the complete expected and actual member sets, including
  empty directories and root mappings: no missing **or extra** members, duplicate
  tar names, file/directory collisions or silently skipped entries. Match all
  regular-file sizes and SHA-256, all directory types and any metadata the format
  promises. Confirm coverage of all accepted source roots, not just counts.
* Reject absolute/traversal paths, symlinks, hardlinks, devices, FIFOs, sockets,
  sparse/unsupported members and dangerous metadata. Bound member counts, name
  lengths, manifest size, individual/total uncompressed sizes and parsing time
  from the actual captured contract before allocating/extracting. Do not trust
  receipt-supplied limits without independent bounds.
* Prefer verification by reading member streams without extraction. If recovery
  extraction is required, validate first and create only regular files/directories
  in a fresh protected destination; never use unchecked `tar -xf`, `extractall`,
  restore ownership, setuid bits, ACLs, xattrs or execute restored code/config.
  Re-enumerate the destination and repeat exact-set/type/size/hash verification.
* Check full payload coverage: actual posts/media, old and current catalogs and
  annotations, migration state, native intents/results, reservations/budget and
  adapter ledger, protected configuration and code/helper inventory. Absence of
  an optional future ledger must be explicit in the captured contract, not filled
  in with invented data. No provider calls or automatic use of restored tokens.

For the recorded generation, reconcile file/directory counts and plaintext byte
totals against the private capture receipt using the producer's exact accounting
(control members may be counted separately). A disagreement stops; do not alter the expected counts to
make a new implementation pass. Matching counts alone is not acceptance.

Local synthetic regression requirements for the bound verifier: correct full
round-trip; one-byte corruption; missing/extra/duplicate member; omitted/extra
directory; path traversal/absolute path; symlink/hardlink/special member;
malformed/duplicate-key manifest; size/resource overflow; wrong key/signer or
namespace; truncated ciphertext/decrypt failure; failed cleanup. Record which
existing tests cover each case and add only missing tests. The repository crypto
rehearsal below does not replace this private-format test suite.

Use a private workspace backed by storage whose confidentiality and cleanup are
understood (including swap/snapshots). Close the isolated GPG agent and clean only
owned test files. `rm`/`shred` is not proof of secure erasure on SSD/COW/snapshots;
retain failed artifacts protected if safe cleanup cannot be established. Do not
claim cleanup complete until owner checks it. Original archives/key remain intact.

## C. Minimal Déjà Dup inclusion and real off-host recovery

**Smallest change: one additive folder inclusion, only if needed.** Use the existing
root-controlled ciphertext-only publication directory outside the agent-owned
home. Include immutable generations with their detached signatures and generation
receipts, plus public verification material. Never include recovery keyrings,
plaintext staging, private configuration roots or a broad ancestor to solve access.
Inspect actual contents and ACLs, and prove the existing desktop backup user can
traverse/read them without write access or broader credential access. A private
verification receipt containing paths/content must stay private; publish only a
sanitized receipt if needed, without modifying the immutable capture generation.

1. Privately snapshot settings and relevant job identities immediately before
   editing. Detect the **existing set format**; do not switch engines or guess from
   today's Déjà Dup version. Inspect the local retention helper before starting
   a new backup: its established post-success action may prune old full chains.
   Do not change/disable it or issue manual pruning. If this threatens required
   retained evidence or its behavior is uncertain, stop for owner disposition.
2. Inspect include/exclude coverage, ignore markers (`.deja-dup-ignore`,
   `CACHEDIR.TAG`) and actual reader permissions throughout the selected directory.
   Do not assume an explicit include overrides an ancestor exclusion. Stop on
   ambiguity/conflict; never remove existing exclusions or broaden permissions.
3. If already effectively covered, change nothing. Otherwise, in the **existing
   desktop user's** Backups app, add only that exact publication directory under
   Folders to Back Up. No root GSettings session, replacement array, `dconf load`,
   backend migration, new cloud grant, rclone/other-backup change or timer install.
   Re-read the baseline just before applying; a changed preimage requires a fresh
   review. Verify afterward that the only configuration delta is that one include
   (normal backup status/timestamps may change after running).
4. Check available remote quota using the existing owner-authorized backup access.
   Budget for the captured generation plus backend overhead/current retained
   chains; do not assume compression/dedup shrinks randomized ciphertext. Do not
   reseal unchanged plaintext. Multiple retained local generations remain in later
   snapshots: two-chain retention alone does not cap them. Recurring capture,
   generation pruning and retention/cadence changes remain a separate reviewed step.
5. Owner runs one backup to the **existing** destination. Keep the pinned immutable
   generation unchanged and available throughout; do not hold migration locks
   during network transfer. Record backup completion and snapshot/date privately.
6. Through Backups' restore flow, select that completed **remote** snapshot and
   restore the exact generation to a new empty directory, not its source path.
   Never substitute the local published bundle, a synced raw archive or the old
   marker test. Record remote snapshot identity/provenance. Use a fresh restore
   context/cache as supported by the actual backend so success cannot come solely
   from original local payloads; preserve the source. If the app/backend cannot
   establish remote provenance, stop rather than invent a low-level restore call.
7. Compare restored ciphertext, signature and receipt bytes/hashes to the pinned
   pre-backup record. In the protected owner-only area, repeat **all of B**, using
   only the remote-restored bundle and independently preserved recovery material.
   Original state/archive must not be read as a fallback. Independently check the
   signer again; matching an attacker-replaced public key in the backup is not proof.
8. Verify no unintended settings/job/grant/source changes. Preserve existing chains,
   generated evidence and original migration state. If the added include must be
   rolled back, remove only that exact addition after rechecking current settings;
   never restore an entire stale settings dump. Record loss of coverage and keep
   relocation gated. Do not delete remote snapshots as rollback.

This proves remote data retrieval and independent key recovery, not a full bare-metal
rebuild. No relocated production state, restored service or live credential use is
required for this acceptance.

## Acceptance receipt and next action

Keep detailed evidence private; publish only aggregate counts, pass/fail and
reviewed code revision. Every row must pass for **backup recovery accepted**:

| Gate | Required evidence |
| --- | --- |
| Private binding/review | Actual producer/schema and exact owner commands reviewed at recorded hashes; no placeholder invoked. |
| Owner key preservation | Pinned primary/subkey; protected desktop import; fresh import from independent copy; owner passphrase works without relying on cached unlock; source retained. |
| Local recovery | Independently anchored signer; pinned ciphertext; successful decrypt; all expected files/directories and hashes verified; all accepted roots covered. |
| Backup scope | Actual reader can read only intended publication; effective inclusion; previous includes/excludes, destination, schedule/retention, other backups and ordinary rclone preserved; quota sufficient. |
| Off-host recovery | Fresh completed remote snapshot; exact pinned generation restored to distinct destination; byte/hash match and repeat complete owner decryption/manifest verification. |
| Cleanup/preservation | Protected test workspace/cache disposition checked; source archives/state intact; no service activation, relocation, X/Raindrop operation, new grant or unrelated provider activity. |

**Exact next local action:** resume at the current checkpoint above. Inspect
existing backup activity and completion; do not launch another job if one is
already running or repeat completed preparation. Complete C's remote backup and
fresh-destination restore, then verify using only restored ciphertexts and
independently accessible recovery access. Bind and review any new restore
invocations to the actual backend; local helper approval is not approval of an
unseen restore command. Keep protected staging until its owner-approved cleanup.
Report a missing private artifact/check precisely instead of redesigning the backup.
After all rows pass, update FOS-169; Gate D and relocation retain their own review
and authorization requirements. Do not mark the whole historical-drain issue Done.

## Remote rehearsal and reference contracts

`uv run pytest -q tests/test_backup_crypto_rehearsal.py` uses only invented payloads,
disposable GPG primary/encryption subkeys and SSH signing keys in temporary folders.
It exercises passphrase-protected export → fresh import → uncached unlock/decrypt,
and failures for public-only keyrings, wrong passphrase, altered ciphertext and
wrong SSH signer/namespace. It is not the unavailable desktop capture/verifier,
root/ACL boundary, SSH transfer, pinentry or Déjà Dup integration test.

The crypto cases require `gpg`, `gpgconf`, `ssh-keygen` and permitted Unix sockets
for the disposable GPG agent. Missing binaries or denied socket capability skip
only those cases locally with an explicit reason. Set `REQUIRE_CRYPTO_REHEARSAL=1`
to make them mandatory; the checked-in CI workflow does so, and must report zero
crypto skips. Install the test dependencies through the normal environment setup
(`gnupg` and `openssh-client` on Ubuntu); the tests do not install packages or ask
for privilege. Once preflight succeeds, crypto execution errors are failures,
never converted into skips. Separate tests verify the mandatory-CI behavior.

For public handoffs, keep host names, password-manager choice, observed connection
failures and exact generation counts/sizes in the private receipt. This runbook
uses roles and required checks; it does not supply those private values.

Reference contracts checked 17 November 2026:

* [GnuPG operations](https://gnupg.org/documentation/manuals/gnupg/Operational-GPG-Commands.html):
  secret-key export/import and decrypt; export must name the intended fingerprint.
* [GnuPG input/output](https://www.gnupg.org/documentation/manuals/gnupg/GPG-Input-and-Output.html):
  explicit output and output bounds. Check local-version support before using an option.
* [GnuPG 2.4.8 machine status contract](https://github.com/gpg/gnupg/blob/gnupg-2.4.8/doc/DETAILS):
  explicit successful-decryption status is required in addition to exit status.
  This is a versioned reference, not proof of the installed version; bind the
  local check to its actual version.
* [OpenSSH ssh-keygen](https://man.openbsd.org/ssh-keygen):
  `-Y verify` uses allowed signers, identity and namespace to authenticate signed input.
* [GNOME restore](https://help.gnome.org/deja-dup/restore-full.html) and
  [folder preferences](https://help.gnome.org/deja-dup/prefs.html):
  existing backup format controls restore behavior; preserve exclusions/ignore markers.

These references describe primitives. They do not attest to the private installed
commands or current desktop state.
