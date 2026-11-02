#!/usr/bin/env python3
"""Remove only overlapping app repositories from an existing legacy fork schedule."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

import yaml

from bootstrap import cli
from config import Config, Error


def handoff(cfg, apply=False):
    legacy = (cfg.raw.get("legacy") or {}).get("fork_sync") or {}
    if not legacy.get("config") or not legacy.get("job_id"):
        raise Error("legacy.fork_sync.config and job_id are required for handoff")
    filename = Path(legacy["config"])
    original = filename.read_text()
    data = yaml.safe_load(original)
    paths = {str(Path(e.local_path).resolve()) for e in cfg.envs}
    removed = [r for r in data["repos"] if str(Path(r["path"]).resolve()) in paths]
    kept = [r for r in data["repos"] if str(Path(r["path"]).resolve()) not in paths]
    if not removed:
        return {"status": "already_handed_off", "remaining": [r["name"] for r in kept]}
    jobs = cli("cron", "list", "--all", "--json")["jobs"]
    job = next((j for j in jobs if j["id"] == legacy["job_id"]), None)
    if not job or job.get("payload", {}).get("kind") != "agentTurn":
        raise Error("legacy job not found or unexpected payload")
    result = {"removed": [r["name"] for r in removed], "retained": [r["name"] for r in kept], "apply": apply}
    if not apply:
        return result
    backup = cfg.path.parent / "backups" / ("handoff-" + str(int(time.time())))
    backup.mkdir(parents=True, mode=0o700)
    (backup / "config.yaml").write_text(original)
    (backup / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=2))
    for p in backup.iterdir():
        p.chmod(0o600)
    # The old script already honors repos; keep its code and remaining behavior.
    data["repos"] = kept
    temp = filename.with_suffix(".haro-tmp")
    temp.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    temp.chmod(filename.stat().st_mode & 0o777)
    temp.replace(filename)
    script = filename.parent / "sync_fork.py"
    if kept:
        message = ("执行现有 fork 同步脚本，严格以其 config.yaml 的 repos 列表为准：\n"
                   f"python3 {script}\n"
                   "当前仅负责 " + ", ".join(r["name"] for r in kept) + "。"
                   "应用仓库由独立 Haro maintenance 任务接管，不要补回旧列表。"
                   "完成后在任务结果中汇总成功或失败，沿用本任务已有投递设置。")
        cli("cron", "edit", job["id"], "--name", "Daily Fork Sync - remaining repos", "--message", message)
    else:
        cli("cron", "edit", job["id"], "--disable")
    result["backup"] = str(backup)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True)
    p.add_argument("--apply", action="store_true")
    a = p.parse_args()
    try:
        print(json.dumps(handoff(Config(a.config), a.apply), ensure_ascii=False, indent=2))
    except (Error, OSError, ValueError) as ex:
        print(json.dumps({"error": str(ex) if isinstance(ex, Error) else type(ex).__name__}))
        sys.exit(1)
