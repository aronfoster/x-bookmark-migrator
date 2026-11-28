# Desktop rollout handoff — 28 November 2026

**Blocked at deployment.** The owner saved the test bookmark and enabled the
timer. The first due run failed. The attempted repair installer did not parse;
the running service still selects the original scheduled build. Do not ask for
another bookmark. Neither scheduled acceptance nor historical progress from
this rollout has been demonstrated.

## What is actually running

| Component | Revision or setting |
| --- | --- |
| Merged migrator baseline | `6c7544be` |
| Installed scheduled worker | `2cabff3fdc85938be8ae2ed56738295ff186ea32`; local desktop build, not merged main |
| Separate historical worker | `10e28ef`; its staged config and shared removal history must be preserved |
| Candidate restore fix | `6323e5e387a51db41faaf64567ee50e801770445`; local desktop Git ref `desktop/restore-permissions-6323e5e`, not installed |
| Timer | `openclaw-x-migrator.timer`, enabled and active; five-minute checks, daily intake, earlier ticks for eligible queued items |
| Scheduled bounds | 20 bookmarks and 45 X request slots per run, not 20 guaranteed removals |

## Evidence and failure boundary (desktop local time, MST)

- **08:45:02:** the timer started the service as `x-migrator`. It exited 1.
  The protected run report showed `worker_failure`, empty run and X-operation
  counts, and zero unknown outcomes. The catalog still matched its pre-run
  digest (103 records). No new X removal or same-bookmark proof was observed.
- During receipt adoption, the first pending restore had an empty directory
  with mode `02700`. The worker creates that directory, then explicitly requests
  `chmod(02750)`; the installed unit has `RestrictSUIDSGID=yes`. The candidate
  inherits setgid from the audited parent and handles the empty remnant. Its
  1,069 passing synthetic tests exercised worker code, **not the installer**.
- The owner then ran the repair installer. Python raised `IndentationError:
  unexpected indent` at line 29 before executing any statements. In the
  `DropInPaths` check on line 28, the `require()` call closes before its message
  argument. **No repair was installed by that command.** `systemctl show`
  still points to `2cabff3f`.
- **08:50:11:** another timer invocation exited successfully. The protected
  failure report was unchanged. The saved next intake and daily deadline were
  **29 November at 08:45:03 MST**. A successful idle tick does not verify a
  migration. The earlier 53 confirmed removals predate this scheduled rollout.

## Where to resume

1. On the desktop, `git worktree list` locates the candidate source checkout.
   The local activation directory containing that checkout also contains
   `install-restore-fix.py`, `activate.py`, `scheduled.py`, the packaged worker
   and the activation fixture. The helper is outside tracked source and was not
   covered by the package tests. Inspect the candidate delta with
   `git diff 2cabff3f..6323e5e -- src tests`.
2. Fix and syntax-check the **exact owner-facing installer** before handing it
   back. Exercise it end to end against a fixture of the already enabled
   deployment, including a second invocation. Check effective `ExecStart`, both
   pre-start checks, the pinned package, `RestrictSUIDSGID=yes`, the existing
   test target and timer. Verify offline under the actual service identity and
   sandbox. The service account can be used for protected read-only inspection
   via `sudo -n -u x-migrator`; installing system units requires owner sudo.
3. Preserve the archive, protected configuration, receipts, locks, saved due
   times and local OpenClaw changes. Do not manually trigger a live removal or
   reset the deferral to simulate a scheduled run. Installation alone is not
   acceptance.
4. On a normal due run, match the saved target to a catalog entry, independently
   restored Drive media bytes, exact Raindrop ID/link readback, acknowledged X
   removal and subsequent absence in the same folder. The URL identifies the
   target; its category comes from observed X folder membership. This test
   covers a named folder, not automatic classification of unfiled bookmarks.
   Report distinct historical removals separately from the test result.

[FOS-168](https://linear.app/fosterstech/issue/FOS-168) owns scheduled
acceptance and private evidence. [FOS-169](https://linear.app/fosterstech/issue/FOS-169)
owns backlog coverage. Link verified retrieval evidence to
[FOS-220](https://linear.app/fosterstech/issue/FOS-220) and
[FOS-170](https://linear.app/fosterstech/issue/FOS-170) only after acceptance.
[FOS-234](https://linear.app/fosterstech/issue/FOS-234) tracks notifications;
do not assume an unattended failure will alert the owner.
