# Haro maintenance and lifecycle deployment

Deployment date: 2026-11-02. Target: `hzfysttq.olares.com`, OpenClaw 2026.10.1.
Source release: `32498054d9e1d42ebda9f110721ee6e6ac338d1a` on
`feat/haro-maintenance`. Code review: https://github.com/TShentu/Olares_Agent_Scripts/pull/2.

## Installed scope

- Independent module: `/home/userdata/home/Code/Olares_Project/Olares_Agent_Scripts/haro_maintenance`.
- Private configuration: `config.yaml` in that directory, mode 0600. Existing prod/test
  repository keys retained; explicit levels test=10, preview=20, prod=30.
  Preview upstream is `beclab/apps-preview`; fork is `TShentu/apps-preview`, branch `main`.
- Independent state: `/home/node/.openclaw/workspace/state/haro-maintenance`.
- Recovery repository: `TShentu/Olares_Agent_Scripts`, branch
  `haro-maintenance-state-hzfysttq-three-env`. The previous two-environment branch is retained. No publication branch is used for recovery.
- New `haro-reconcile` skill installed beside existing skills, with a private runtime
  locator. Existing application modification/publication routes remain unchanged.

## Schedules

| Job | Schedule in Asia/Shanghai | ID |
|---|---|---|
| haro-maintenance:run | Daily 03:00 | 57340226-f60d-40b5-9e4b-527a417ba7e4 |
| haro-maintenance:refresh | Every 10 minutes | 024e0d9f-2609-4a4a-a3a8-3ef4b9fdaad3 |
| Existing fork sync | Daily 06:00, now static/Olares only | 0eaaad81-515d-4e56-8525-5d5e70caec98 |

New jobs use deterministic command payloads, not LLM-generated shell commands.
Daily reports and meaningful refresh changes are delivered to the configured
application review group. Refreshes with no tasks/changes remain quiet.

## Verified behavior

- 40 tests passed locally (Python 3.9) and in the deployment container (Python 3.14).
- Live dry-run validated fork network relationships and push permissions, without writes.
- Initial apply synchronized test/prod fork main to their upstreams; neither needed a
  divergence archive. No upstream main was written and no application PR was created.
- Initial production checkpoint: `ea047c291b7a0b0b33cea98ac5c1cf0d6aa97d51`.
  Existing app differences were reported only, not automatically copied.
- A real daily scheduler run succeeded and native Feishu delivery returned `delivered`.
- Scheduled refresh succeeded with `NO_REPLY` and delivery suppression `silent`.
  Overlap with a local maintenance run is treated as a quiet refresh skip.
- State recovery from GitHub was verified.
- All 14 pre-existing Python business scripts matched their pre-deployment SHA256 hashes.
  The existing modified `sync_chart.py` was preserved.
- The legacy fork config now contains only static/Olares; its original config and job
  definition were backed up before the scoped handoff.

Files upload on this instance auto-renames existing filenames (for example `(1).py`).
For subsequent releases upload a uniquely named archive, verify SHA256, then replace
only this module's tracked files. Do not interpret upload success as proof
that an existing running file was replaced. Never overwrite private config or backups.

## Recovery and remaining rollout boundaries

Private single-file export: `backups/haro-sync-3249805.secret.yaml` under the
module directory. It contains a credential and embedded legacy configs/blacklist;
do not publish or attach it to an issue. It was checked for mode 0600 and parseability.
Legacy handoff backup: `backups/handoff-1793623251/`.

Application backport PR creation/recovery was tested using a fake GitHub API and real
temporary Git repositories; the initial live run intentionally did not manufacture
an app update merely to exercise a live PR. Subsequent new production changes activate
the pipeline. Version, owner or review-state conflicts are reported rather than bypassed.

Upstream branch protection has not been changed. The ten-minute refresher provides
periodic freshness checks, not a guarantee that a human can never merge a stale PR.
Strict merge-time freshness requires the upstream required-check/branch-protection
rollout described in PLAN.md. Human-ready PR branches are not automatically rewritten.

Remaining scope: batch publish, Git-history distribution queries/reporting, and full
legacy Haro configuration migration. All operations target GitHub; no Olares Market
service API is used.

## Preview configuration

Added preview on 2026-11-02. The existing production checkpoint was preserved when
migrating to the dedicated three-environment recovery branch; adding preview does
not trigger a bulk backport of historical differences. Existing business scripts
and their separate configs remain unchanged. Daily maintenance now includes all
three forks, and new production changes target both lower environments.

## !app routing

Installed `commands/app/SKILL.md`, added the exact `!app` route to
`olares-commands`, and added a capability notice to AGENTS.md. Previous contents
are backed up under workspace `state/haro-maintenance/route-backups`. Bootstrap
reinstalls the integration idempotently. Existing business routes are preserved.

Verified `!app help` on the existing Feishu direct session through the Gateway
agent runner, without channel delivery. The session loaded the new command skill
and returned the implemented command list; no session reset was needed.

## Single-app release rollout

- Added lifecycle.py for promote/publish preflight, durable asynchronous submission,
  job and per-app jobs queries. Existing ten-minute refresh processes the queue.
- Submission records trusted channel actor, fixed source SHA, source/target, title,
  PR and follow-up relationships. Same-app active operations require acknowledgement.
- Actual merged production content triggers intermediate-level backports, deduplicated
  with daily discovery. Failed follow-ups remain independently visible and are audited
  against current repositories even after the production checkpoint advances.
- Automatic titles and single-app background jobs are included; batch is deferred.
- Deployment backed up config/state under `backups/before-lifecycle-9b44134/`.
- Both local Python 3.9 and cloud Python 3.14 passed 40 tests. Tests use real temporary
  Git repositories and a fake GitHub API to exercise creation, merge/close, retry,
  actual-merge snapshots, overlap and failed-follow-up recovery.
- Live source/target preflight for acestep15v2 returned satisfied (already identical)
  with no remote writes; remote jobs query returned all three environment names.
- Live refresh --apply completed successfully with no tasks/errors. No arbitrary
  application was published or merged just for deployment validation.
- The user's existing Feishu direct session was reset through the official gateway
  API after it retained stale help context. Persistent jobs/repository state are intact.

Final integration: the refreshed Feishu session returned the new release/job help
and executed per-app jobs queries successfully. Query guidance was tightened to
use only durable operation records, not cron inventory. Concurrent route installation
was also verified after replacing shared temporary filenames with unique files.

## 2026-11-03 refresh automation auto-disable fix

- Symptom: `haro-maintenance:refresh` was auto-disabled after ten consecutive failed
  runs. Every failed run printed `NO_REPLY` and exited 2, so the automation looked
  broken while it was correctly reporting that nothing had changed.
- Cause: `scheduled.py` returned the maintenance report exit code (2 = blocked tasks
  or environment errors) on quiet refresh paths. OpenClaw records every non-zero run
  as a failure, so an idle poll with already-reported attention state counted as a
  failure and the scheduler disabled a healthy automation.
- Fix: the wrapper now exits 0 for any run that produced a report and 1 only for a
  run without a usable report (crash, timeout, unreadable output). Blocked tasks and
  environment errors remain visible in the delivered summary as `需要处理`.
- Live verification: refresh command returned `NO_REPLY` with exit code 0; the manual
  scheduler run recorded `ok`/`succeeded` with silent delivery suppression; the job was
  re-enabled and its failure state cleared.
- Tests: 44 tests pass on Python 3.14 (four new scheduler tests cover the exit-status
  contract, the quiet blocked-task poll, the delivered daily report and genuine
  maintenance failures).
- Unrelated and left untouched: the `agentj` backports to `test` and `preview` stay
  blocked by the ownership gate (`prod` `agentj/owners` lists only `preangelleo`, the
  configured identity is `TShentu`). Owner and version checks are never bypassed.


## 2026-11-03 sync scope update

- `!app sync` is equivalent to `!app sync --all`; scheduled daily behavior is unchanged.
- `!app sync --preview` only discovers/processes prod-to-preview incremental backports,
  without syncing any fork main, executing test tasks, or advancing release jobs.
- Both modes are preflight by default; `--apply` enables writes. Flags are mutually
  exclusive. Missing preview fails before state acquisition; there is no fallback to all.
- Target-specific cursors are seeded from the existing shared checkpoint before
  selective advancement. Later all-mode discovery still sees test's unseen changes.
- Preserved the concurrent upstream fix 040346e for quiet refresh exit statuses.
- Local Python 3.9 and cloud Python 3.14: 48 tests passed, including selective scope,
  cursor migration, missing-preview errors and CLI mutual exclusion.
- Deployment backed up config/state in `backups/before-sync-scope-951c0e2/` and wrote
  the current private one-file export `backups/haro-sync-951c0e2.secret.yaml`.


## 2026-11-03 Submitter policy (supersedes the preceding sync scope defaults)

- Supported: sync (Olares to preview/test), sync --preview (Olares to preview),
  sync --preview --all (all submitters to preview). Bare --all is rejected.
- Static spec.submitter is read independently of Helm templates elsewhere; developer,
  owners and Git commit authors are not used to infer the submitter. Removed apps use
  their last historical manifest. Missing/dynamic submitters are excluded by default.
- Manual sync invokes single-app backport processing only. Daily scheduled run keeps
  fork/job maintenance through an internal flag and defaults discovery to Olares.
- Legacy automatic tasks are also filtered during refresh; explicit individual
  backports and release-linked children retain their requested scope.
- sync_checkpoints separately tracks each target and Olares/all selectors. Test ahead
  of production is reported without attempting a downgrade/backport.
- Local and cloud suites: 54 tests passed. Live read-only smoke tests confirmed all
  three selectors and bare --all rejection. Five changed Olares apps were already
  identical in test; preview backports remain blocked by existing validation rules.
  Third-party agentj only appeared in the all-app preview mode. No PR was created.
- Main command router, AGENTS context and the app skill now state the current defaults.
  Backup: backups/haro-sync-3249805.secret.yaml, private mode 0600.

## 2026-11-12 Identical release preflight guard

- Preflight now compares the selected source snapshot with the current target snapshot by
  full application-directory tree hash, not by chart version alone. When both hold
  identical content the request is refused with `status: blocked` and
  `reason: identical`, reporting both chart versions; no job is enqueued and no PR
  is created. The chat skill answers "两边的版本是一样的，无法提交" and stops.
- An unreadable comparison fails open and never blocks a valid submission; the worker
  re-checks before creating anything.
- Verified live: `promote wisev3 --from test` returns blocked/identical (1.2.69 on
  both sides, identical tree fc49e9df5c58d7c6b91b3cd1fbd702ad947e2327). A differing
  app is unaffected and still reaches the existing open-PR check.
- Local and worktree suites: 76 tests passed, including three new cases (identical
  blocked before enqueue, unreadable comparison still queues, differing release
  queues normally). Backup: backups/before-identical-guard-20261112/.
