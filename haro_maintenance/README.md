# Haro repository maintenance

Independent phase-one maintenance for `Olares_Agent_Scripts`. Existing scripts,
skills and checkouts are not modified. Python 3.9+ and Git 2.39+; install
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
fall back. The token must read upstreams, push forks, create PRs/statuses, and write
the configured private recovery branch. Never commit config.yaml or export files.

`suspend_version_rule` is environment-specific: `increase` by default, or `equal`
where the target repository requires matching versions. UPDATE always requires an
increased SemVer. REMOVE requires only `.remove`, an existing target version and
target owners. No automatic version rewrite is performed. A high test version can
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
owners/version failures and PR-type changes block automatic rewriting. Unknown
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
- Every 10 minutes: refresh tracked PRs without fork synchronization or new discovery.

The wrapper sends daily summaries and meaningful refresh changes to the configured
Feishu target; unknown delivery results are not blindly retried. Command timeout is
bounded. Existing unrelated OpenClaw jobs remain untouched. Before enabling the
schedule, remove only overlapping application repos from an existing fork-sync job;
preserve static/Olares and all unrelated jobs. Back up legacy config and cron JSON
first. No legacy source code is rewritten.

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
