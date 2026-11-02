---
name: haro-reconcile
description: Inspect and run Haro repository maintenance, upstream fork synchronization, source-snapshot backports and managed PR refresh. Use for maintenance status and explicit backport requests; existing app modification and publication commands remain unchanged.
---

# Haro repository maintenance

Read the deployed `runtime.json` beside this skill to locate `script` and `config`.
Invoke Python with an argv list and absolute paths. Never print or load the raw
configuration into chat: it can contain GitHub credentials.

Commands (global flags precede the subcommand):

- `python3 <script> --config <config> config`: validate configuration and show a redacted projection.
- `python3 <script> --config <config> status`: latest maintenance report.
- `python3 <script> --config <config> run`: dry-run daily work; remote GitHub is read-only.
- `python3 <script> --config <config> run --apply`: configured daily maintenance.
- `python3 <script> --config <config> refresh --apply`: refresh tracked PRs without fork synchronization or new discovery.
- `python3 <script> --config <config> backport <app> --from <source> --to <targets>`: preview an explicitly requested backport; append `--apply` to create Draft PRs when requested.
- `python3 <script> --config <config> route promote|publish --from <source>`: resolve routing only. This does not publish an app.

The configured daily schedule authorizes its fixed work. An interactive dry-run
does not itself authorize an apply. Honor the user's requested scope; never bypass
owners/version checks or substitute a different GitHub identity after an error.

Source-snapshot policy replaces the complete named app directory, including
target-only files. Other apps and repository files remain unchanged. Report any
version reversal or overwritten target development content in the proposed PR.
No merge command is exposed: all upstream PRs require human review and merge.

First run establishes a baseline and reports existing differences without copying
them. Each repository distributes from its own history; historical OS selections
need not match across environments.

`blocked` means a rule, permission or concurrency condition needs attention.
An external change to a managed branch or a PR already ready for review must not
be overwritten manually to bypass the stop. A closed rejected PR is not reopened
for the same source event. Unknown network-write outcomes must be reconciled from
the recorded task and GitHub before retrying.

Return app, source/target, task status, PR URL and concise error. Distinguish Draft
PR created, merged, and expected Market publication; merging is not proof of live
Market availability. Ordinary results go to the requesting conversation; scheduled
delivery is handled by the deterministic wrapper using the configured destination.

Single-app promote/publish and job/jobs are available in sibling lifecycle.py. The !app command skill defines trusted requester metadata and active-job acknowledgement. Scheduled run/refresh also processes these jobs, detects merges and audits failed follow-ups. No Market service API is used.
