# Haro maintenance and lifecycle deployment

Deployment date: 2026-11-02. Target: `hzfysttq.olares.com`, OpenClaw 2026.10.1.
Source release: `9b44134b905cd7afd8aa71bfb9a8dda6b04613d4` on
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

Private single-file export: `backups/haro-lifecycle-9b44134.secret.yaml` under the
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
