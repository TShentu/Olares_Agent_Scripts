#!/usr/bin/env python3
"""Install reproducible command schedules without editing existing Haro skills."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time

import yaml

from config import Config, Error


def cli(*args):
    r = subprocess.run(["openclaw", *args], capture_output=True, text=True, timeout=90)
    if r.returncode:
        raise Error("OpenClaw " + args[0] + " failed; inspect runtime diagnostics")
    start = r.stdout.find("{")
    return json.loads(r.stdout[start:])


def install(cfg, apply=False):
    if not cfg.maintenance.get("enabled"):
        raise Error("enable maintenance before installing schedules")
    script = str(Path(__file__).with_name("scheduled.py").resolve())
    notifications = cfg.raw.get("notifications") or {}
    if not notifications.get("target"):
        raise Error("notifications.target is required")
    jobs = cli("cron", "list", "--all", "--json").get("jobs", [])
    result = []
    for mode, default in (("run", "0 3 * * *"), ("refresh", "*/10 * * * *")):
        key = "haro-maintenance:" + mode
        matched = [j for j in jobs if j.get("declarationKey") == key or j.get("name") == key]
        if len(matched) > 1:
            raise Error("duplicate Haro schedule declarations")
        command = [sys.executable, script, "--config", str(cfg.path), mode]
        expr = cfg.maintenance.get("schedule" if mode == "run" else "refresh_schedule", default)
        every = cfg.maintenance.get("refresh_every") if mode == "refresh" else None
        timing = ["--every", every] if every else ["--cron", expr, "--tz", cfg.maintenance.get("timezone", "Asia/Shanghai")]
        args = ["--name", key, *timing,
                "--command-argv", json.dumps(command), "--timeout-seconds", "1500",
                "--announce", "--channel", notifications.get("channel", "feishu"),
                "--to", (notifications.get("status_target") or notifications["target"]) if mode == "refresh" else notifications["target"]]
        if notifications.get("status_target"):
            args += ["--failure-alert-channel", notifications.get("channel", "feishu"),
                     "--failure-alert-to", notifications["status_target"], "--failure-alert-mode", "announce"]
        if apply:
            if matched:
                r = cli("cron", "edit", matched[0]["id"], *args, "--enable")
            else:
                r = cli("cron", "add", *args, "--declaration-key", key)
            result.append({"mode": mode, "result": r})
        else:
            result.append({"mode": mode, "schedule": {"every": every} if every else expr, "command": command, "existing": bool(matched)})
    if apply:
        workspace = Path((cfg.raw.get("runtime") or {}).get("openclaw_workspace", str(Path.home() / ".openclaw/workspace")))
        skill = workspace / "skills/haro-reconcile"
        if skill.exists() and not (skill / "runtime.json").exists():
            raise Error("existing unowned haro-reconcile skill; refusing overwrite")
        shutil.copytree(Path(__file__).parent / "skills/haro-reconcile", skill, dirs_exist_ok=True)
        (skill / "runtime.json").write_text(json.dumps({"script": str(Path(__file__).with_name("maintenance.py").resolve()), "config": str(cfg.path)}, indent=2))
        result.append({"skill": str(skill)})
        from install_app_route import install as install_app_route
        result.append(install_app_route(workspace))
    return result


def export_config(cfg, output):
    """Produce a single private YAML, including external credentials/rule bytes."""
    data = dict(cfg.raw)
    data["auth"] = {"github_token": cfg.token()}
    files = {}
    legacy = data.get("legacy") or {}
    # Bundle explicit legacy config references, including their relative blacklist.
    for name, filename in (legacy.get("config_files") or {}).items():
        p = Path(filename).expanduser()
        if not p.is_absolute():
            p = cfg.path.parent / p
        content = yaml.safe_load(p.read_text())
        files[name] = content
        blacklist = content.get("blacklist") if isinstance(content, dict) else None
        if blacklist:
            b = Path(blacklist)
            files[name + "_blacklist"] = (b if b.is_absolute() else p.parent / b).read_text()
    if cfg.raw.get("blacklist"):
        b = Path(cfg.raw["blacklist"])
        files["blacklist"] = (b if b.is_absolute() else cfg.path.parent / b).read_text()
    data["recovery"] = {**(data.get("recovery") or {}), "embedded_legacy": files}
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    return {"exported": output, "mode": "0600", "contains_credentials": True}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--export", metavar="NEW_FILE")
    a = p.parse_args()
    try:
        c = Config(a.config)
        print(json.dumps(export_config(c, a.export) if a.export else install(c, a.apply), ensure_ascii=False, indent=2))
    except (Error, OSError, ValueError) as ex:
        print(json.dumps({"error": str(ex) if isinstance(ex, Error) else type(ex).__name__}))
        sys.exit(1)
