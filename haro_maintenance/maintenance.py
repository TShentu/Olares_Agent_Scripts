#!/usr/bin/env python3
"""Haro maintenance entrypoint. Dry-run by default; never merges upstream PRs."""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

from config import Config, Error
from github_api import GitHub, RemoteState
from gitops import Git, app_name, validate_app

MARKER = "<!-- haro-maintenance:"


def metadata(body):
    match = re.search(re.escape(MARKER) + r"(.*?) -->", body or "", re.S)
    if not match:
        return None
    try:
        return json.loads(match[1])
    except ValueError:
        return None


def atomic_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.chmod(0o600)
    temp.replace(path)


class Runner:
    def __init__(self, cfg, api, apply=False):
        self.cfg, self.api, self.apply = cfg, api, apply
        self.report = {"mode": "apply" if apply else "dry-run", "started_at": time.time(),
                       "forks": [], "apps": [], "errors": [], "baseline": False}
        self.owner = str(uuid.uuid4())
        self.state = {"schema": 1, "checkpoint": None, "tasks": {}}
        self.remote = None
        recovery = cfg.raw.get("recovery") or {}
        if recovery.get("repository"):
            self.remote = RemoteState(api, recovery["repository"], recovery.get("branch", "haro-maintenance-state"))
        if apply and not self.remote:
            raise Error("apply requires recovery.repository for recoverable checkpoints")
        self.git = Git(cfg.root / "objects.git", api.token)
        self.heads = {}
        self.login = api.request("GET", "user")["login"]
        signature = [(e.name, e.level, e.repo, e.fork, e.branch) for e in cfg.envs]
        self.signature = hashlib.sha256(json.dumps(signature).encode()).hexdigest()

    def save(self):
        if not self.apply:
            return
        # Remote CAS prevents a second deployment from taking over a live run.
        self.state["lease"] = {"owner": self.owner, "until": time.time() + 1800}
        self.remote.save(self.state)
        atomic_json(self.cfg.root / "state.json", self.state)

    def open(self):
        if self.remote:
            loaded = self.remote.load()
        else:
            p = self.cfg.root / "state.json"
            loaded = json.loads(p.read_text()) if p.exists() else None
        if loaded:
            self.state = loaded
        if self.state.get("schema") != 1:
            raise Error("unsupported recovery state schema")
        lease = self.state.get("lease") or {}
        if lease.get("until", 0) > time.time():
            raise Error("another maintenance run holds the remote lease")
        if self.state.get("configuration") not in (None, self.signature):
            raise Error("environment mapping changed; use a new recovery branch to establish a new baseline")
        self.state["configuration"] = self.signature
        if self.apply:
            self.remote.initialize_branch()
            self.save()

    def close(self):
        if self.apply:
            self.state["lease"] = None
            self.remote.save(self.state)
            atomic_json(self.cfg.root / "state.json", self.state)

    def fetch(self, sync_forks=True):
        for e in self.cfg.envs:
            self.api.check_environment(e)
            upstream = self.git.fetch(e.upstream, e.branch, "refs/haro/" + e.name + "/upstream")
            fork = self.git.fetch(e.github, e.branch, "refs/haro/" + e.name + "/fork")
            self.heads[e.name] = upstream
            if not sync_forks:
                continue
            try:
                self.save()
                self.report["forks"].append(self.git.sync_fork(e, upstream, fork, self.apply))
            except Error as ex:
                self.report["errors"].append({"environment": e.name, "error": str(ex)})

    def discover(self):
        source = self.cfg.envs[-1]
        head = self.heads[source.name]
        previous = self.state["checkpoint"]
        if not previous:
            self.report["baseline"] = True
            self.report["baseline_commit"] = head
            # Initial differences are informational, never an implicit overwrite.
            self.report["initial_differences"] = {e.name: [a for a in self.git.apps(head)
                if self.git.tree(head, a) != self.git.tree(self.heads[e.name], a)] for e in self.cfg.envs[:-1]}
        else:
            if not self.git.ancestor(previous, head):
                raise Error("source history rewritten; explicit baseline recovery required")
            excluded = (self.cfg.maintenance.get("backport") or {}).get("exclude_apps", [])
            for app in self.git.changed_apps(previous, head):
                if app in excluded:
                    continue
                event = self.git.text("log", "-1", "--format=%H", head, "--", app)
                for target in self.cfg.envs[:-1]:
                    key = target.name + "/" + app
                    prior = self.state["tasks"].get(key, {})
                    self.state["tasks"][key] = {**prior, "app": app, "source": source.name,
                        "source_sha": head, "event": event, "target": target.name,
                        "status": "pending"}
        self.state["checkpoint"] = head
        self.save()

    def body(self, task, target_sha, kind, version, summary):
        info = {"schema": 1, "source_repo": self.cfg.environment(task["source"]).repo,
                "source_sha": task["source_sha"], "source_tree": self.git.tree(task["source_sha"], task["app"]),
                "target_sha": target_sha, "app": task["app"], "event": task["event"],
                "strategy": "source_snapshot", "kind": kind}
        return (f"### App Title\n{task['app']}\n\n### Description\n"
                f"Backport {task['source']} to {task['target']} at {version}.\n\n"
                "The app directory is replaced by the selected source snapshot, including deletion of target-only files. "
                "Other apps and repository files are unchanged. No upstream merge is performed by Haro.\n\n"
                f"Source: https://github.com/{info['source_repo']}/commit/{info['source_sha']}\n\n"
                f"Target base: `{target_sha}`\n\n````text\n{summary}\n````\n\n"
                "### Statement\n- [x] This PR contains only the declared application.\n\n"
                + MARKER + json.dumps(info, sort_keys=True) + " -->")

    def process(self, task):
        app, src = task["app"], task["source_sha"]
        target = self.cfg.environment(task["target"])
        source = self.cfg.environment(task["source"])
        # Refresh target for every app; PRs never branch from a stale local checkout.
        base = self.git.fetch(target.upstream, target.branch, "refs/haro/" + target.name + "/upstream")
        branch = task.get("branch") or f"haro/backport/{source.name}/{app}"
        task["branch"] = branch
        pr = self.api.get_pr(target.repo, task["pr"]) if task.get("pr") else None
        if pr and pr["state"] == "closed":
            old_event = (metadata(pr.get("body")) or {}).get("event")
            if old_event == task["event"]:
                task["status"] = "merged" if pr.get("merged_at") else "rejected"
                return
            task.pop("pr", None)
            task.pop("head", None)
            # Never recycle a closed PR's branch.
            branch += "-" + task["event"][:12]
            task["branch"] = branch
            pr = None
        if task.get("status") in {"merged", "rejected", "satisfied"}:
            return
        if not pr:
            open_prs = self.api.pulls(target)
            matches = [p for p in open_prs if p["head"]["ref"] == branch and
                       p["head"].get("repo", {}).get("full_name", "").lower() == target.fork.lower()]
            if matches:
                pr = matches[0]
                if not metadata(pr.get("body")):
                    raise Error("matching branch has an unmanaged PR")
                task["pr"] = pr["number"]
            elif any(re.match(r"^\[[A-Z]+\]\[" + re.escape(app) + r"\]", p["title"]) for p in open_prs):
                raise Error("another open PR exists for this app")
        if pr:
            if pr["head"].get("repo", {}).get("full_name", "").lower() != target.fork.lower() or pr["base"]["ref"] != target.branch:
                raise Error("unexpected PR head/base repository")
            if task.get("head") and pr["head"]["sha"] != task["head"]:
                raise Error("PR branch was changed outside Haro; manual review required")
            managed_head = self.git.fetch(target.github, branch, "refs/haro/pr/" + target.name + "/" + app)
            info = metadata(pr.get("body")) or {}
            recorded = task.get("head") or task.get("pending_head")
            if not recorded or managed_head != recorded:
                raise Error("PR head is not the durably recorded managed commit")
            info = metadata(pr.get("body")) or {}
            if self.apply and info.get("target_sha") != base:
                self.api.status(target.fork, pr["head"]["sha"], "failure", "Target advanced; regenerate snapshot before review")
            if not pr.get("draft", True):
                raise Error("PR is already in review; do not rewrite reviewed branch automatically")
        if self.git.tree(src, app) == self.git.tree(base, app):
            if pr and self.apply:
                self.api.request("PATCH", f"repos/{target.repo}/pulls/{pr['number']}", {"state": "closed"})
            task["status"] = "satisfied"
            return
        rule = self.cfg.raw[target.name].get("suspend_version_rule", "increase")
        if rule not in {"equal", "increase"}:
            raise Error("invalid suspend_version_rule")
        kind, version = validate_app(self.git, src, base, app, self.login, rule)
        if pr and (metadata(pr.get("body")) or {}).get("kind") != kind:
            raise Error("PR type changed; close existing PR before creating a new type")
        title = f"[{kind}][{app}][{version}] Backport from {source.name}"
        msg = (title + f"\n\nHaro-Source: {source.repo}@{src}\nHaro-Target: {base}\n"
               f"Haro-App: {app}\nHaro-Event: {task['event']}\nHaro-Strategy: source_snapshot\n")
        if pr:
            info = metadata(pr.get("body")) or {}
            if info.get("target_sha") == base and info.get("source_sha") == src:
                self.git.verify(src, base, managed_head, app)
                task.update(head=pr["head"]["sha"], status="awaiting_review", url=pr["html_url"])
                if self.apply:
                    self.api.status(target.fork, task["head"], "success", "Source snapshot verified at target " + base[:12])
                return
        commit = self.git.snapshot(src, base, app, msg)
        summary = self.git.text("diff", "--stat", base, commit)
        body = self.body(task, base, kind, version, summary)
        task["planned_base"] = base
        if not self.apply:
            task.update(status="would_create_or_refresh_pr", diff=summary)
            return
        old_head = self.git.remote_head(target.github, branch)
        if old_head and old_head not in (task.get("head"), task.get("pending_head")):
            raise Error("existing branch is not the recorded managed head")
        if self.git.remote_head(target.upstream, target.branch) != base:
            raise Error("target advanced during generation; retry on next run")
        task["pending_head"] = commit
        self.save()  # durable before pushing, so lost network responses are recoverable
        self.git.push(target.github, commit, branch, old_head)
        task["head"] = commit
        self.save()
        if pr:
            pr = self.api.request("PATCH", f"repos/{target.repo}/pulls/{pr['number']}", {"title": title, "body": body})
        else:
            data = {"title": title, "body": body, "head": target.fork.split("/")[0] + ":" + branch,
                    "base": target.branch, "draft": True, "maintainer_can_modify": False}
            try:
                pr = self.api.request("POST", f"repos/{target.repo}/pulls", data)
            except Error:
                found = [p for p in self.api.pulls(target) if p["head"]["ref"] == branch and
                         p["head"].get("repo", {}).get("full_name", "").lower() == target.fork.lower()]
                if len(found) != 1:
                    raise
                pr = found[0]
        task.update(pr=pr["number"], url=pr["html_url"], status="awaiting_review")
        self.save()
        fresh = self.git.remote_head(target.upstream, target.branch) == base
        self.api.status(target.fork, commit, "success" if fresh else "failure",
                        "Source snapshot verified at target " + base[:12] if fresh else "Target advanced; refresh required")
        if not fresh:
            raise Error("target advanced after PR update; snapshot status marked failed")

    def run(self, explicit_app=None, source_name=None, target_names=None, refresh_only=False):
        self.open()
        try:
            self.fetch(sync_forks=not refresh_only and not explicit_app)
            if explicit_app:
                source = self.cfg.environment(source_name or self.cfg.envs[-1].name)
                targets = [self.cfg.environment(n) for n in target_names] if target_names else [e for e in self.cfg.envs if e.level < source.level]
                for target in targets:
                    if target.level >= source.level:
                        raise Error("backport target must be below source")
                    key = target.name + "/" + app_name(explicit_app)
                    sha = self.heads[source.name]
                    self.state["tasks"][key] = {**self.state["tasks"].get(key, {}), "app": explicit_app, "source": source.name, "source_sha": sha,
                        "event": self.git.text("log", "-1", "--format=%H", sha, "--", explicit_app),
                        "target": target.name, "status": "pending"}
            elif not refresh_only:
                self.discover()
            for key, task in self.state["tasks"].items():
                if task.get("status") in {"merged", "rejected", "satisfied"}:
                    continue
                try:
                    task.pop("error", None)
                    self.process(task)
                except Error as ex:
                    task.update(status="blocked", error=str(ex))
                self.save()
                self.report["apps"].append(copy.deepcopy(task))
            return self.report
        finally:
            self.close()


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-c", "--config", default=str(Path(__file__).with_name("config.yaml")))
    p.add_argument("--token-file")
    p.add_argument("--token-env")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("config", help="show validated, redacted configuration")
    sub.add_parser("status", help="read local latest report")
    for name in ("run", "refresh", "backport"):
        s = sub.add_parser(name)
        s.add_argument("--apply", action="store_true", help="allow fork pushes and Draft PR writes")
        if name == "backport":
            s.add_argument("app")
            s.add_argument("--from", dest="source")
            s.add_argument("--to", help="comma-separated target environments")
    s = sub.add_parser("route", help="resolve future promote/publish routing without writing")
    s.add_argument("operation", choices=["promote", "publish"])
    s.add_argument("--from", dest="source")
    return p


def main():
    args = parser().parse_args()
    try:
        cfg = Config(args.config)
        if args.command == "config":
            result = cfg.public()
        elif args.command == "route":
            result = cfg.route(args.operation, args.source)
        elif args.command == "status":
            result = json.loads((cfg.root / "latest-report.json").read_text())
        else:
            if args.apply and cfg.maintenance.get("enabled") is not True:
                raise Error("maintenance.enabled must be true before --apply")
            cfg.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            with (cfg.root / "run.lock").open("w") as lock:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise Error("another local maintenance run is active")
                runner = Runner(cfg, GitHub(cfg.token(args.token_file, args.token_env)), args.apply)
                result = runner.run(getattr(args, "app", None), getattr(args, "source", None),
                    args.to.split(",") if getattr(args, "to", None) else None, args.command == "refresh")
                atomic_json(cfg.root / "latest-report.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if not result.get("errors") and not any(t.get("status") == "blocked" for t in result.get("apps", [])) else 2
    except (Error, OSError, ValueError) as ex:
        # Exceptions must not echo configuration secrets.
        message = str(ex) if isinstance(ex, Error) else type(ex).__name__
        print(json.dumps({"error": message}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
