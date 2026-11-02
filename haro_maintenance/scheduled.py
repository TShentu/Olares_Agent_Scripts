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
    except ValueError:
        result = {"error": "maintenance process did not return JSON"}
    if a.mode == "refresh" and result.get("error") == "another local maintenance run is active":
        print("NO_REPLY")
        return 0
    tasks = result.get("apps", [])
    stable = {"apps": [{k: t.get(k) for k in ("app", "target", "status", "error", "url")} for t in tasks],
              "errors": result.get("errors", []), "error": result.get("error")}
    digest = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
    memo = cfg.root / "notification.json"
    previous = json.loads(memo.read_text()) if memo.exists() else {}
    if a.mode == "refresh" and digest == previous.get("digest"):
        print("NO_REPLY")
        return r.returncode
    if a.mode == "refresh" and not tasks and not result.get("error") and not result.get("errors"):
        print("NO_REPLY")
        return r.returncode
    lines = ["Haro 仓库维护 " + ("完成" if r.returncode == 0 else "需要处理")]
    if result.get("baseline"):
        lines.append("已建立基线；存量差异仅报告，未自动覆盖。")
    for f in result.get("forks", []):
        lines.append(f"{f['environment']}: {f['status']}")
    for t in tasks[:30]:
        lines.append(f"{t['app']} → {t['target']}: {t['status']} {t.get('url', '')} {t.get('error', '')}")
    for e in result.get("errors", []):
        lines.append(str(e))
    if result.get("error"):
        lines.append(result["error"])
    lines.append("PR 由人工审核合并；合并不代表 Market 已生效。")
    # Native command delivery runs after the command exits. Calling the gateway
    # recursively with message send can deadlock while its command queue is held.
    print("\n".join(lines))
    atomic_json(memo, {"digest": digest, "meaning": "last generated summary; delivery is owned by OpenClaw"})
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
