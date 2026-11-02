import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import Config, Error
from gitops import Git, validate_app, version_key
from maintenance import Runner, metadata
from github_api import RemoteState, ApiError


def sample(root):
    return {"test": {"github": "https://github.com/f/test", "upstream": "https://github.com/u/test", "local_path": str(root / "old-test")},
            "prod": {"github": "https://github.com/f/prod", "upstream": "https://github.com/u/prod", "local_path": str(root / "old-prod")},
            "runtime": {"maintenance_root": str(root / "state")}}


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.raw = sample(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def load(self):
        path = self.root / "config.yaml"
        path.write_text(yaml.safe_dump(self.raw))
        return Config(path)

    def test_legacy_two_levels(self):
        cfg = self.load()
        for op in ("promote", "publish"):
            self.assertEqual(cfg.route(op), {"source": "test", "target": "prod", "backport_after_merge": []})
        self.assertFalse(cfg.public()["enabled"])

    def test_three_and_four_levels(self):
        for k, level in (("test", 10), ("prod", 40)):
            self.raw[k]["level"] = level
        for k, level in (("preview", 20), ("staging", 30)):
            self.raw[k] = {**self.raw["test"], "level": level, "github": f"https://github.com/f/{k}"}
        cfg = self.load()
        self.assertEqual(cfg.route("promote", "test")["target"], "preview")
        self.assertEqual(cfg.route("publish", "test")["backport_after_merge"], ["preview", "staging"])
        with self.assertRaises(Error):
            cfg.route("publish")
        with self.assertRaises(Error):
            cfg.route("promote", "prod")

    def test_invalid_levels(self):
        self.raw["test"]["level"] = 10
        with self.assertRaises(Error):
            self.load()
        self.raw["prod"]["level"] = 10
        with self.assertRaises(Error):
            self.load()

    def test_incomplete_preview_not_ignored(self):
        self.raw["preview"] = {}
        with self.assertRaises(Error):
            self.load()

    def test_secrets_are_not_in_public_config(self):
        self.raw["auth"] = {"github_token": "a-secret-value"}
        cfg = self.load()
        self.assertNotIn("a-secret-value", json.dumps(cfg.public()))
        with patch.dict(os.environ, {"GITHUB_TOKEN": "fallback"}):
            self.assertEqual(cfg.token(), "a-secret-value")
            with self.assertRaises(Error):
                cfg.token(token_env="MISSING_TEST_VARIABLE")

    def test_legacy_worktree_cannot_be_maintenance_root(self):
        self.raw["runtime"]["maintenance_root"] = self.raw["test"]["local_path"] + "/state"
        with self.assertRaises(Error):
            self.load()


class GitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.work = self.root / "work"
        self.work.mkdir()
        self.cmd("init", "-q")
        self.cmd("config", "user.name", "Test")
        self.cmd("config", "user.email", "test@example.invalid")
        self.write("app/Chart.yaml", "version: 1.0.0\n")
        self.write("app/OlaresManifest.yaml", "metadata:\n  version: 1.0.0\n")
        self.write("app/owners", "tester\n")
        self.write("other/file", "keep")
        self.base = self.commit("base")
        self.cmd("checkout", "-qb", "source")
        self.write("app/config", "source\n")
        self.write("app/Chart.yaml", "version: 2.0.0\n")
        self.write("app/OlaresManifest.yaml", "metadata:\n  version: 2.0.0\n")
        (self.work / "app/binary").write_bytes(b"\0\xff")
        self.write("app/executable", "hello")
        (self.work / "app/executable").chmod(0o755)
        (self.work / "app/link").symlink_to("config")
        self.source = self.commit("source")
        self.cmd("checkout", "-qb", "target", self.base)
        self.write("app/config", "target\n")
        self.write("app/target-only", "delete me")
        self.target = self.commit("target")
        self.git = Git(self.root / "objects.git")
        # Tests only: local remotes, explicitly opt into file transport.
        self.git.env["GIT_CONFIG_VALUE_2"] = "always"
        self.git.run("fetch", str(self.work), "+refs/heads/*:refs/heads/*")

    def tearDown(self):
        self.tmp.cleanup()

    def cmd(self, *args):
        r = subprocess.run(["git", *args], cwd=self.work, capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stderr)
        return r.stdout.strip()

    def write(self, name, content):
        p = self.work / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def commit(self, msg):
        self.cmd("add", ".")
        self.cmd("commit", "-qm", msg)
        return self.cmd("rev-parse", "HEAD")

    def test_exact_snapshot_and_scoped_diff(self):
        c = self.git.snapshot(self.source, self.target, "app", "snapshot")
        self.assertEqual(self.git.tree(c, "app"), self.git.tree(self.source, "app"))
        self.assertEqual(self.git.tree(c, "other"), self.git.tree(self.target, "other"))
        self.assertIsNone(self.git.file(c, "app/target-only"))
        self.git.verify(self.source, self.target, c, "app")

    def test_advance_target_requires_regeneration(self):
        c = self.git.snapshot(self.source, self.target, "app", "snapshot")
        self.write("app/new-target-file", "new")
        new = self.commit("advance")
        self.git.run("fetch", str(self.work), "+refs/heads/target:refs/heads/target")
        self.assertNotEqual(self.git.run("diff", new, c).stdout, self.git.run("diff", new + "..." + c).stdout)
        c2 = self.git.snapshot(self.source, new, "app", "snapshot")
        self.git.verify(self.source, new, c2, "app")
        self.assertIsNone(self.git.file(c2, "app/new-target-file"))

    def test_noop_and_bad_path(self):
        self.assertIsNone(self.git.snapshot(self.source, self.source, "app", "noop"))
        with self.assertRaises(Error):
            self.git.snapshot(self.source, self.target, "../other", "bad")

    def test_version_and_owner_guard(self):
        self.assertEqual(validate_app(self.git, self.source, self.target, "app", "tester"), ("UPDATE", "2.0.0"))
        with self.assertRaises(Error):
            validate_app(self.git, self.target, self.source, "app", "tester")
        with self.assertRaises(Error):
            validate_app(self.git, self.source, self.target, "app", "stranger")

    def test_detect_only_changed_apps(self):
        self.assertEqual(self.git.changed_apps(self.base, self.source), ["app"])

    def test_lease_rejects_concurrent_push(self):
        remote = self.root / "fork.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        self.git.push(str(remote), self.target, "main", None)
        self.git.push(str(remote), self.source, "main", self.target)
        with self.assertRaises(Error):
            self.git.push(str(remote), self.base, "main", self.target)
        self.assertEqual(self.git.remote_head(str(remote), "main"), self.source)

    def test_fork_divergence_archived(self):
        from types import SimpleNamespace
        remote = self.root / "fork.git"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        self.git.push(str(remote), self.target, "main", None)
        e = SimpleNamespace(name="test", branch="main", github=str(remote))
        result = self.git.sync_fork(e, self.source, self.target, True)
        self.assertEqual(self.git.remote_head(str(remote), result["archive"]), self.target)
        self.assertEqual(self.git.remote_head(str(remote), "main"), self.source)

    def test_first_baseline_does_not_backport(self):
        cfgfile = self.root / "config.yaml"
        cfgfile.write_text(yaml.safe_dump(sample(self.root)))
        runner = object.__new__(Runner)
        runner.cfg = Config(cfgfile)
        runner.heads = {"test": self.target, "prod": self.source}
        runner.git = self.git
        runner.report = {}
        runner.state = {"checkpoint": None, "tasks": {}}
        runner.apply = False
        runner.discover()
        self.assertTrue(runner.report["baseline"])
        self.assertEqual(runner.state["tasks"], {})
        runner.state["checkpoint"] = self.base
        runner.discover()
        self.assertEqual(list(runner.state["tasks"]), ["test/app"])

    def runner_task(self):
        cfgfile = self.root / "config.yaml"
        cfgfile.write_text(yaml.safe_dump(sample(self.root)))
        r = object.__new__(Runner)
        r.cfg = Config(cfgfile)
        r.git, r.apply, r.login = self.git, True, "tester"
        r.save = lambda: None
        r.base = self.target
        r.branches = {}
        self.git.fetch = lambda url, branch, ref: r.base if url.startswith("https://github.com/u/") else r.branches[branch]
        self.git.remote_head = lambda url, branch: r.base if url.startswith("https://github.com/u/") else r.branches.get(branch)
        self.git.push = lambda url, commit, branch, expected: r.branches.__setitem__(branch, commit)
        class API:
            def __init__(self):
                self.pr = None
                self.creates = 0
                self.statuses = []
                self.lose_create_response = False
            def pulls(self, e):
                return [self.get_pr(None, None)] if self.pr and self.pr["state"] == "open" else []
            def get_pr(self, repo, number):
                pr = dict(self.pr)
                pr["head"] = {**pr["head"], "sha": r.branches[pr["head"]["ref"]]}
                return pr
            def status(self, *args):
                self.statuses.append(args)
            def request(self, method, path, data):
                if method == "POST":
                    self.creates += 1
                    self.pr = {**data, "number": 1, "state": "open", "html_url": "https://github.com/u/test/pull/1",
                        "head": {"ref": data["head"].split(":")[1], "repo": {"full_name": "f/test"}},
                        "base": {"ref": data["base"]}}
                    if self.lose_create_response:
                        raise Error("unknown write outcome")
                else:
                    self.pr.update(data)
                return self.get_pr(None, None)
        r.api = API()
        task = {"app": "app", "source": "prod", "source_sha": self.source,
                "event": self.source, "target": "test", "status": "pending"}
        return r, task

    def test_pr_retry_does_not_duplicate_and_human_edit_blocks(self):
        r, task = self.runner_task()
        r.process(task)
        head = task["head"]
        r.process(task)
        self.assertEqual(r.api.creates, 1)
        self.assertEqual(task["head"], head)
        r.branches[task["branch"]] = self.source
        with self.assertRaisesRegex(Error, "outside Haro"):
            r.process(task)

    def test_unknown_create_response_is_reconciled(self):
        r, task = self.runner_task()
        r.api.lose_create_response = True
        r.process(task)
        self.assertEqual(task["pr"], 1)
        self.assertEqual(r.api.creates, 1)

    def test_closed_event_is_not_reopened(self):
        r, task = self.runner_task()
        r.process(task)
        r.api.pr["state"] = "closed"
        r.process(task)
        self.assertEqual(task["status"], "rejected")
        self.assertEqual(r.api.creates, 1)

    def test_stale_ready_pr_is_marked_failed_not_rewritten(self):
        r, task = self.runner_task()
        r.process(task)
        r.api.pr["draft"] = False
        r.base = self.base
        with self.assertRaisesRegex(Error, "already in review"):
            r.process(task)
        self.assertEqual(r.api.statuses[-1][2], "failure")

    def test_managed_draft_regenerated_on_new_base(self):
        r, task = self.runner_task()
        r.process(task)
        r.base = self.base
        r.process(task)
        self.git.verify(self.source, self.base, task["head"], "app")
        self.assertEqual(r.api.creates, 1)


class SemverTests(unittest.TestCase):
    def test_order(self):
        values = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
        self.assertEqual(sorted(values, key=version_key), values)
        self.assertEqual(version_key("1.0.0+a"), version_key("1.0.0+b"))


if __name__ == "__main__":
    unittest.main()
