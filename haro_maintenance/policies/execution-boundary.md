## Execution and error boundary

User policy (2026-11-04): before explicit authorization, report errors; do not proactively troubleshoot.

- Route the current request and run its normal script workflow. Scripts own comparison, Git operations, PR creation, labels and task recording.
- On error/blocked/timeout, report the command, failed step, concise error, known partial results and whether the outcome is uncertain. Use only evidence already returned by the task. Do not claim a failed operation had no side effects.
- Then stop. Do not inspect source/config/backups/GitHub to investigate, substitute promote with publish, retry a command, bypass a guard, repair files, or change identity without explicit user authorization for that work. A retry needs explicit authorization too.
- Progress feedback is allowed for the current task: use its completion event or a minimal status lookup when requested. Do not turn progress checks into diagnostics. Avoid polling loops.
- Error reporting is not permission to load triage/debug skills. Load them only when the user explicitly asks to investigate, diagnose or fix that error, and stay within that scope.
- Do not read historical daily memory for routine !commands, resume old tasks from memory, write execution/error narratives to memory, or rewrite Skills after ordinary runs. Durable jobs/reports are the task record. Update persistent preferences only on an explicit remember/documentation request.
- Existing scheduled scripts retain their configured scope. Do not add Agent-driven heartbeat retries or new recovery jobs after failure.
- Operational status/failure alerts go to private chat oc_a9b760e24325ec0b769a885ace67e9ab. Group oc_bad8aa94aaecfea2542fb6f8b5226400 receives only fixed scheduled summaries and replies in the relevant topic when a user invokes Haro there.
