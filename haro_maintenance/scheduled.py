#!/usr/bin/env python3
"""Bounded scheduler adapter: daily summary; refresh only on meaningful changes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from config import Config
from maintenance import atomic_json


def run_status(returncode, parsed):
    """Exit status of the wrapper for a finished maintenance run.

    A produced report is a successful run even when it reports blocked tasks
    or environment errors: that attention state belongs to the summary text.
    Only a run without a usable report is a failed run. Mapping business
    state onto the exit status would make a scheduler count every idle poll
    as a failure and disable a healthy automation.
    """
    if not parsed:
        return 1
    if returncode in (0, 2):
        return 0
    return 1



def refresh_rows(result):
    rows = {}
    for kind in ("jobs", "apps"):
        for row in result.get(kind, []):
            key = kind + ":" + str(row.get("id") or (str(row.get("app")) + ":" + str(row.get("target"))))
            fields = ("id", "app", "source", "target", "status", "error", "url",
                      "followup_status", "followup_error", "children", "consistency")
            rows[key] = {k: row[k] for k in fields if k in row}
    return rows


def attention(row):
    return bool(row.get("error") or row.get("followup_error") or
                row.get("status") in {"blocked", "needs_engineer", "needs_attention", "rejected"} or
                row.get("followup_status") == "needs_engineer" or
                any(c.get("status") == "needs_engineer" for c in row.get("consistency", [])))


def batch_refresh(result, previous, now, interval):
    """Return a delta report and durable pending state; daily reports never touch it.

    State records generated output, not a delivery acknowledgment. Native OpenClaw
    owns delivery/failure diagnostics, as with the previous notification digest.
    """
    rows = refresh_rows(result)
    seen = dict(previous.get("seen", {}))
    pending = dict(previous.get("pending", {}))
    urgent = {}
    for key, row in rows.items():
        if seen.get(key) != row:
            pending[key] = row
            if attention(row):
                urgent[key] = row
        seen[key] = row
    errors = list(result.get("errors", []))
    if result.get("error"):
        errors.append(result["error"])
    signatures = {json.dumps(e, sort_keys=True, ensure_ascii=False): e for e in errors}
    new_errors = [v for k, v in signatures.items() if k not in previous.get("active_errors", [])]
    # Failed/partial reports must not erase pending successful observations.
    last = previous.get("last_summary_at", now)
    due = now - last >= interval
    selected = dict(pending) if due else urgent
    for key in selected:
        pending.pop(key, None)
    if due:
        last = now
    state = {"seen": seen, "pending": pending, "active_errors": list(signatures),
             "last_summary_at": last, "meaning": "last generated output; native delivery owns receipt"}
    if not selected and not new_errors:
        return None, state
    report = {"jobs": [v for k, v in selected.items() if k.startswith("jobs:")],
              "apps": [v for k, v in selected.items() if k.startswith("apps:")],
              "errors": new_errors}
    report["attention"] = bool(new_errors or any(attention(r) for r in selected.values()))
    return report, state


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("mode", choices=["run", "refresh"])
    a = p.parse_args()
    cfg = Config(a.config)
    try:
        r = subprocess.run([sys.executable, str(Path(__file__).with_name("maintenance.py")),
                            "--config", str(cfg.path), a.mode, "--apply"] + (["--maintenance"] if a.mode == "run" else []), capture_output=True, text=True, timeout=1400)
    except subprocess.TimeoutExpired:
        print("Haro 仓库维护超时；请检查记录，不要直接重跑未知写入。")
        return 1
    try:
        result = json.loads(r.stdout)
        parsed = True
    except ValueError:
        result = {"error": "maintenance process did not return JSON"}
        parsed = False
    if a.mode == "refresh" and result.get("error") == "another local maintenance run is active":
        print("NO_REPLY")
        return 0
    interval = int((cfg.raw.get("notifications") or {}).get("refresh_summary_seconds", 0))
    batched = a.mode == "refresh" and interval > 0
    if batched:
        batch_memo = cfg.root / "refresh-notification.json"
        prior = json.loads(batch_memo.read_text()) if batch_memo.exists() else {}
        delta, state = batch_refresh(result, prior, time.time(), interval)
        atomic_json(batch_memo, state)
        if delta is None:
            print("NO_REPLY")
            return run_status(r.returncode, parsed)
        result = delta
    tasks = result.get("apps", [])
    jobs = result.get("jobs", [])
    stable = {"jobs": [{k: j.get(k) for k in ("id", "status", "error", "url", "followup_status", "followup_error", "children")} for j in jobs], "apps": [{k: t.get(k) for k in ("app", "target", "status", "error", "url")} for t in tasks],
              "errors": result.get("errors", []), "error": result.get("error")}
    digest = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
    memo = cfg.root / "notification.json"
    previous = json.loads(memo.read_text()) if memo.exists() else {}
    if a.mode == "refresh" and not batched and digest == previous.get("digest"):
        # Nothing changed since the last delivered summary. The refresh itself
        # succeeded, so it stays a successful run instead of a failure.
        print("NO_REPLY")
        return run_status(r.returncode, parsed)
    if a.mode == "refresh" and not tasks and not jobs and not result.get("error") and not result.get("errors"):
        print("NO_REPLY")
        return run_status(r.returncode, parsed)
    lines = ["Haro 仓库维护 " + ("需要处理" if result.get("attention") or r.returncode != 0 else "状态更新" if batched else "完成")]
    if result.get("baseline"):
        lines.append("已建立基线；存量差异仅报告，未自动覆盖。")
    for f in result.get("forks", []):
        lines.append(f"{f['environment']}: {f['status']}")
    for t in (tasks if batched else tasks[:30]):
        lines.append(f"{t['app']} → {t['target']}: {t['status']} {t.get('url', '')} {t.get('error', '')}")
    for j in (jobs if batched else jobs[-30:]):
        lines.append(f"Job {j['id']} {j['app']} {j['source']} → {j['target']}: {j['status']} {j.get('url', '')} {j.get('error', '')}")
        if j.get("followup_status"):
            lines.append(f"回流: {j['followup_status']} {j.get('followup_error', '')}")
        for c in j.get("consistency", []):
            if c['status'] == 'needs_engineer':
                lines.append(f"需工程师检查 {j['app']} → {c['target']}: 仓库内容仍不一致 {c.get('error') or ''}")
    for e in result.get("errors", []):
        lines.append(str(e))
    if result.get("error"):
        lines.append(result["error"])
    # Native command delivery runs after the command exits. Calling the gateway
    # recursively with message send can deadlock while its command queue is held.
    print("\n".join(lines))
    atomic_json(memo, {"digest": digest, "meaning": "last generated summary; delivery is owned by OpenClaw"})
    return run_status(r.returncode, parsed)


if __name__ == "__main__":
    sys.exit(main())
