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
    r = subprocess.run([sys.executable, str(Path(__file__).with_name("maintenance.py")),
                        "--config", str(cfg.path), a.mode, "--apply"], capture_output=True, text=True, timeout=1400)
    try:
        result = json.loads(r.stdout)
    except ValueError:
        result = {"error": "maintenance process did not return JSON"}
    print(json.dumps(result, ensure_ascii=False))
    tasks = result.get("apps", [])
    stable = {"apps": [{k: t.get(k) for k in ("app", "target", "status", "error", "url")} for t in tasks],
              "errors": result.get("errors", []), "error": result.get("error")}
    digest = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
    memo = cfg.root / "notification.json"
    previous = json.loads(memo.read_text()) if memo.exists() else {}
    if a.mode == "refresh" and digest == previous.get("digest"):
        return r.returncode
    if a.mode == "refresh" and not tasks and not result.get("error") and not result.get("errors"):
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
    n = cfg.raw.get("notifications") or {}
    # Delivery is intentionally not retried on unknown outcome.
    if n.get("target"):
        send = subprocess.run(["openclaw", "message", "send", "--channel", n.get("channel", "feishu"),
                               "--target", n["target"], "--message", "\n".join(lines)], capture_output=True, timeout=90)
        if send.returncode:
            print(json.dumps({"notification": "failed_or_unknown; inspect delivery before retry"}))
            return 3
        atomic_json(memo, {"digest": digest})
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
