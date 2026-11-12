# Haro repository maintenance

Repository maintenance and single-app release jobs for `Olares_Agent_Scripts`.
Legacy business scripts and checkouts are preserved; the !app route is installed additively. Python 3.9+ and Git 2.39+; install
`pip install -r requirements.txt` if PyYAML is unavailable.

## Configuration

Copy `config.yaml.template` to private `config.yaml`, or extend an existing
prod/test config. `github` still means fork and `upstream` means PR base repository.
URLs must be clean GitHub HTTPS URLs. `local_path` remains legacy metadata; this
module exclusively uses `runtime.maintenance_root/objects.git` and temporary indexes.

With no explicit levels, infer test=10, preview=20, prod=30. If any level is explicit,
all environments must have unique integer levels. An incomplete preview is an error;
omit the whole section for two levels. Custom environments require explicit levels.
`route promote` selects the nearest higher level; `route publish` selects the highest.
These are routing previews only; existing `push_app` is unchanged in phase one.

`auth.github_token` or `auth.github_token_file` supplies credentials. Explicit
`--token-file` / `--token-env` takes precedence, then configured token/file, then
GITHUB_TOKEN / GH_TOKEN / GITHUB_TOKEN_FILE. A failed explicit credential does not
fall back. The token must read upstreams, push forks, create PRs/statuses, add upstream issue labels, and write
the configured private recovery branch. Never commit config.yaml or export files.

`suspend_version_rule` is environment-specific: `increase` by default, or `equal`
where the target repository requires matching versions. UPDATE always requires an
increased SemVer. REMOVE requires only `.remove`, an existing target version and
target owners for promote/publish. Sync/backport skips owner checks. No automatic version rewrite is performed. A high test version can
therefore block a production backport instead of silently dropping its version guard.

## Commands

```sh
python3 maintenance.py -c /absolute/config.yaml config
python3 maintenance.py -c /absolute/config.yaml route promote
python3 maintenance.py -c /absolute/config.yaml run
python3 maintenance.py -c /absolute/config.yaml run --apply
python3 maintenance.py -c /absolute/config.yaml refresh --apply
python3 maintenance.py -c /absolute/config.yaml backport dify --from prod --to test
python3 maintenance.py -c /absolute/config.yaml status
python3 -m unittest discover -s tests -v
```

Dry-run makes local Git/cache/report writes, but no remote mutations. Apply also
requires `maintenance.enabled: true` and `recovery.repository`. First apply records
the highest environment's current commit as a baseline and reports existing
differences. Only subsequent source app changes create automatic backport tasks.
Explicit `backport` can process a selected existing difference.

The recovery branch stores checkpoints, pending heads, PRs, rejected events and an
expiring lease using GitHub Contents API compare-and-swap. Local state is a cache.
The branch defaults to `haro-maintenance-state` and must never be a publication
branch. Run only one installation per recovery branch. Environment mapping changes
require a new recovery branch/baseline, rather than reinterpreting old tasks.

## Source snapshots and PRs

Fork main is mirrored to upstream. Divergent fork heads are archived under
`haro/archive/` first. Every update uses an explicit SHA lease.

Backport uses a temporary Git index, starting at target HEAD and substituting only
the source app tree. This handles deletions, binary files, modes and symlinks without
executing repository code. The one resulting commit has target HEAD as its sole
parent and records source repository/SHA, event, strategy and target baseline.
Exact subtree equality, path scope and two-dot/three-dot diff equality are verified.
Each repository retains its independent publication history; source history is not
imported or reconciled.

Managed Draft PRs regenerate on a new target base; never use a normal merge to
refresh a source snapshot. Human branch edits, PRs ready for review, other app PRs,
version failures, release-job owner failures and PR-type changes block automatic rewriting. Unknown
create results are reconciled against the head branch before retry. A rejected
closed event is suppressed until a newer source event.

`haro/source-snapshot` status reports the last verified baseline. The refresher
marks stale managed Draft PRs failed before regeneration. This does **not** prove
continuous freshness: strict GitHub branch protection plus a required freshness
check is needed to guarantee merge-time freshness. This module does not modify
upstream branch protection. Human-ready PRs are deliberately not rewritten; reviewers
must return them to Draft for automatic regeneration. Upstream GitBot/checks remain
authoritative; Haro does not mark Draft PRs ready or merge them.

## Deployment and schedules

OpenClaw 2026.10.1 supports deterministic command payloads. `bootstrap.py` validates
and upserts two idempotent cron declarations (dry-run unless `--apply`):

```sh
python3 bootstrap.py --config /absolute/config.yaml
python3 bootstrap.py --config /absolute/config.yaml --apply
```

- Daily 03:00 Asia/Shanghai: fork sync, discover new source changes, backport PRs.
- Every 5 hours in the new template: refresh tracked PRs without fork synchronization or new discovery.
  Set `maintenance.refresh_every: "5h"` for a fixed interval. It takes precedence over
  legacy `refresh_schedule`; existing configs without it retain their cron schedule.
  Merge detection and queued-job recovery can wait up to this interval; initial
  submission still starts its background worker immediately.

The wrapper emits daily summaries and meaningful refresh changes; OpenClaw's native
command announcement delivers them to the configured Feishu target. Quiet refreshes
emit only `NO_REPLY`. Do not nest `openclaw message send` inside command jobs: it can
stall on the gateway command queue. Native delivery owns retries/receipts. Command timeout is
bounded. Existing unrelated OpenClaw jobs remain untouched. Before enabling the
schedule, remove only overlapping application repos from an existing fork-sync job;
preserve static/Olares and all unrelated jobs. Back up legacy config and cron JSON
first. No legacy source code is rewritten.

Wrapper exit status: `0` when the maintenance run produced a report, `1` when it
produced no usable report (crash, timeout, unreadable output). Blocked tasks and
environment errors are attention state and travel in the summary text
(`需要处理`), never in the exit status. A scheduler records every non-zero run as a
failure, so mapping attention state onto it made idle refresh polls count as
failures and disabled a healthy automation after ten quiet polls.

Install `skills/haro-reconcile` into the workspace skills directory and add a private
`runtime.json` with `script` and `config` absolute paths. This skill coexists with
existing command routing. Use pinned source commits when deploying; do not reset a
dirty legacy checkout. Copy only this new module from the pinned commit.

## Backup and rollback

```sh
python3 bootstrap.py --config /absolute/config.yaml --export /private/new-backup.secret.yaml
```

Export refuses overwrite, uses 0600 and embeds credentials and explicitly configured
`legacy.config_files` YAML plus their blacklist data. Encrypt this file externally.
It restores maintenance settings and remote state access, not chat history or
uncommitted files. Source release must remain available at the configured pinned SHA;
expired tokens must be replaced. To restore maintenance on a new host, install that
release, place the exported config, choose a new independent maintenance_root,
validate `config`, dry-run, then use bootstrap. The remote recovery branch restores
task history. Full legacy Haro restoration remains a later migration stage.

Rollback: disable `haro-maintenance:run` and `haro-maintenance:refresh`, then restore
the backed-up legacy fork-sync configuration and job. Keep recovery state and PRs for
audit. Do not reset app repos or delete human branches. No upstream PR was auto-merged.

## Feishu !app entrypoint

Bootstrap installs `!app` into the existing `olares-commands` router and updates
AGENTS.md, preserving prior content with hash-named backups. Start with `!app help`.
Implemented commands: `config`, `status`, `sync`, `refresh`, `backport`, and `route`.
Chat write commands execute by default; add `--dry-run` for preflight only. The Skill consumes `--dry-run` and otherwise passes `--apply` to Python. Explicit `--apply` remains compatible; both flags together are rejected. Read-only commands never receive `--apply`. `route promote/publish` only resolves
targets. Single-app promote/publish and job/jobs are implemented by lifecycle.py. Batch release and repository-history distribution queries remain separate.

## Single-app lifecycle jobs

```sh
python3 lifecycle.py --config /absolute/config.yaml promote dify --from test --actor feishu:USER
python3 lifecycle.py --config /absolute/config.yaml publish dify --from test --actor feishu:USER --request-id MESSAGE_ID --apply
python3 lifecycle.py --config /absolute/config.yaml job app-JOB_ID
python3 lifecycle.py --config /absolute/config.yaml jobs --app dify
```

At the Python CLI level, no `--apply` means preflight (unchanged). `--actor` and `--request-id` come from trusted channel
metadata in the Skill, never from user-supplied identity arguments. Requests capture
the source repository commit; local uncommitted files are not included. Titles use
the selected snapshot's upgradeDescription (first 20 words), or explicit --title.
Submissions with active same-app operations return a preview and require acknowledging
that exact active ID set using --ack-active. Requests for the same message are idempotent.
Preflight compares the selected source snapshot with the current target snapshot. When both
hold identical app content the request is refused with `status: blocked` and
`reason: identical`; no job is enqueued and no PR is created. An unreadable comparison
never blocks a submission; the worker re-checks before creating anything.

Apply durably enqueues before launching a detached worker. The existing ten-minute
refresh recovers queued jobs and monitors PR merges; no extra cron is required. Query
returns GitHub-backed records across all configured environments, including prior
backport events. New submissions do not replace another app operation's job record.

Only a merged release dispatches skipped-level backports. It uses actual merged
content, or a newer production event if one already superseded it. Daily discovery
and post-merge dispatch share records and defer new events while a prior PR is open.
A failed/rejected backport never changes the successful production merge status.
Daily and refresh reports compare the affected repositories even after the discovery
checkpoint advanced: existing Draft PR = awaiting review; unresolved difference =
needs engineer. No automated upstream merge or Olares Market service call occurs.

A ready PR whose base is still current remains awaiting review. A stale ready PR is
flagged for review rather than rewritten. Failed release execution retries up to three
times, then remains visible as needs_attention; known PRs still get merge/close checks.

## Sync scope and submitter

- `!app sync`: only `spec.submitter: Olares`, from prod to configured lower environments (preview/test).
- `!app sync --preview`: only Olares apps, prod to preview.
- `!app sync --preview --all`: all submitters, prod to preview only.
- `!app sync --all` is rejected; all third-party apps must not be sent to test implicitly.

Chat sync commands default to single-app backport Draft PR processing; add `--dry-run` for preflight only.
The filter reads the static `spec.submitter` field without evaluating Helm templates;
missing/unreadable values are excluded, not inferred from developer/owners/commit author.
For .remove entries, the most recent historical Manifest supplies the submitter.
Test's newer development version is reported as target_ahead without a backport;
equal-version different content remains subject to existing version checks.

Manual sync only invokes per-app backport logic: it does not mirror fork main or run
release jobs. The scheduled daily runner uses the internal --maintenance flag to keep
fork synchronization and release job monitoring, with Olares-only auto discovery.
Existing unclassified automatic tasks are filtered too, including during refresh.
Explicit single-app backports and release-follow-up children retain their explicit scope.

`sync_checkpoints[target][olares|all]` migrates from legacy per-target/shared checkpoints
before either selector advances. Choosing Olares first does not consume future all-app
preview updates. This remains incremental discovery, not historical full backfill.

Sync/backport PRs use the `enhancement` label as batch maintenance. Haro does not gate them on Gitbot title format, app owners or file count. Each PR still carries one app snapshot with verified diff and baseline; promote/publish keep their existing validation. Label write failures are reported and retried, not silently ignored.

Notification routing: `notifications.target` receives fixed daily summaries; optional `notifications.status_target` receives refresh updates and native failure alerts. Without it, legacy delivery is preserved. Interactive replies remain in the requesting conversation/thread.

Promote/publish owners are parsed from YAML `owners[].username` (legacy plain-login files remain supported). Explicit `@organization` entries authorize an authenticated active member verified through GitHub; organization membership alone does not override unrelated owners. Both existing target and source owners are checked. Titles/comments never authorize; unavailable membership checks fail closed. Membership results are cached only within one invocation.

Refresh notification batching: set `notifications.refresh_summary_seconds: 18000`
independently of the ten-minute check schedule. Ordinary job/app changes are
persisted and coalesced into the latest state per app/job, then reported every
five hours (within one refresh tick). Only changed rows are included. New blocked,
rejected, consistency and execution errors are immediate and deduplicated until
changed or recovered; they do not reset the ordinary summary clock. Daily reports
and interactive replies are unaffected. Omit/set zero to retain legacy behavior.
Native scheduler infrastructure failure alerts remain enabled. Notification state
tracks generated output; OpenClaw owns delivery receipts/failures, not this script.
