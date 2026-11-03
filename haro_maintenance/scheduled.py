#!/usr/bin/env python3
"""Bounded scheduler adapter: daily summary; refresh only on meaningful changes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("mode", choices=["run", "refresh"])
    a = p.parse_args()
    cfg = Config(a.config)
    try:
        r = subprocess.run([sys.executable, str(Path(__file__).with_name("maintenance.py")),
                            "--config", str(cfg.path), a.mode, "--apply"], capture_output=True, text=True, timeout=1400)
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
    tasks = result.get("apps", [])
    jobs = result.get("jobs", [])
    stable = {"jobs": [{k: j.get(k) for k in ("id", "status", "error", "url", "followup_status", "followup_error", "children")} for j in jobs], "apps": [{k: t.get(k) for k in ("app", "target", "status", "error", "url")} for t in tasks],
              "errors": result.get("errors", []), "error": result.get("error")}
    digest = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
    memo = cfg.root / "notification.json"
    previous = json.loads(memo.read_text()) if memo.exists() else {}
    if a.mode == "refresh" and digest == previous.get("digest"):
        # Nothing changed since the last delivered summary. The refresh itself
        # succeeded, so it stays a successful run instead of a failure.
        print("NO_REPLY")
        return run_status(r.returncode, parsed)
    if a.mode == "refresh" and not tasks and not jobs and not result.get("error") and not result.get("errors"):
        print("NO_REPLY")
        return run_status(r.returncode, parsed)
    lines = ["Haro 仓库维护 " + ("完成" if r.returncode == 0 else "需要处理")]
    if result.get("baseline"):
        lines.append("已建立基线；存量差异仅报告，未自动覆盖。")
    for f in result.get("forks", []):
        lines.append(f"{f['environment']}: {f['status']}")
    for t in tasks[:30]:
        lines.append(f"{t['app']} → {t['target']}: {t['status']} {t.get('url', '')} {t.get('error', '')}")
    for j in jobs[-30:]:
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
    lines.append("PR 由人工审核合并；合并不代表 Market 已生效。")
    # Native command delivery runs after the command exits. Calling the gateway
    # recursively with message send can deadlock while its command queue is held.
    print("\n".join(lines))
    atomic_json(memo, {"digest": digest, "meaning": "last generated summary; delivery is owned by OpenClaw"})
    return run_status(r.returncode, parsed)


if __name__ == "__main__":
    sys.exit(main())
