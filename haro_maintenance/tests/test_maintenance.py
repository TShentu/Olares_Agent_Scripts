import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import io
from contextlib import redirect_stdout
from unittest.mock import patch, Mock

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

    def test_recovery_cannot_write_publication_branch(self):
        self.raw["recovery"] = {"repository": "f/state", "branch": "main"}
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
        self.write("app/OlaresManifest.yaml", "metadata:\n  version: 1.0.0\nspec:\n  submitter: Olares\n")
        self.write("app/owners", "tester\n")
        self.write("other/file", "keep")
        self.base = self.commit("base")
        self.cmd("checkout", "-qb", "source")
        self.write("app/config", "source\n")
        self.write("app/Chart.yaml", "version: 2.0.0\n")
        self.write("app/OlaresManifest.yaml", "metadata:\n  version: 2.0.0\nspec:\n  submitter: Olares\n")
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

    def test_backport_skips_owners_but_keeps_version_guard(self):
        self.assertEqual(validate_app(self.git, self.source, self.target, "app", "stranger",
                                      check_owners=False), ("UPDATE", "2.0.0"))
        with self.assertRaises(Error):
            validate_app(self.git, self.target, self.source, "app", "stranger", check_owners=False)

    def test_org_owner_applies_to_source_and_existing_target(self):
        original = self.git.file
        def files(commit, path):
            if path == "app/owners":
                org = "sourceorg" if commit == self.source else "targetorg"
                return ("owners:\n- username: '@" + org + "'\n  title: Olares\n").encode()
            return original(commit, path)
        with patch.object(self.git, "file", side_effect=files):
            member = Mock(return_value=True)
            self.assertEqual(validate_app(self.git, self.source, self.target, "app", "stranger", org_member=member), ("UPDATE", "2.0.0"))
            self.assertEqual({c.args[0] for c in member.call_args_list}, {"sourceorg", "targetorg"})
            with self.assertRaisesRegex(Error, "existing target owner"):
                validate_app(self.git, self.source, self.target, "app", "stranger", org_member=lambda org: org == "sourceorg")

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
        runner.state.pop("sync_checkpoints", None)
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
                if method == "POST" and path.endswith("/labels"):
                    self.pr["labels"] = [{"name": name} for name in data["labels"]]
                    return self.pr["labels"]
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

    def test_backport_process_accepts_nonowner_and_labels_pr(self):
        r, task = self.runner_task()
        r.login = "not-an-owner"
        r.process(task)
        self.assertEqual(task["status"], "awaiting_review")
        self.assertIn({"name": "enhancement"}, r.api.pr["labels"])
        self.git.verify(self.source, self.target, task["head"], "app")
        for operation in ("promote", "publish"):
            r, task = self.runner_task()
            r.login = "not-an-owner"
            task["operation"] = operation
            with self.assertRaisesRegex(Error, "owner"):
                r.process(task)
            self.assertEqual(r.api.creates, 0)

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

    def test_unknown_push_response_is_reconciled(self):
        r, task = self.runner_task()
        r.process(task)
        r.base = self.base
        push = self.git.push
        def lost(*args):
            push(*args)
            raise Error("git push failed (exit 128)")
        self.git.push = lost
        with self.assertRaises(Error):
            r.process(task)
        self.git.push = push
        r.process(task)
        self.assertEqual(task["status"], "awaiting_review")
        self.assertEqual(r.api.creates, 1)

    def test_closed_event_is_not_reopened(self):
        r, task = self.runner_task()
        r.process(task)
        r.api.pr["state"] = "closed"
        r.process(task)
        self.assertEqual(task["status"], "rejected")
        self.assertEqual(r.api.creates, 1)

    def test_current_ready_pr_remains_awaiting_review(self):
        r, task = self.runner_task()
        r.process(task)
        old_head = task['head']
        r.api.pr['draft'] = False
        r.process(task)
        self.assertEqual(task['status'], 'awaiting_review')
        self.assertEqual(task['head'], old_head)
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

    def release_runner(self):
        import lifecycle
        r, task = self.runner_task()
        raw = sample(self.root)
        raw['preview'] = {**raw['test'], 'github': 'https://github.com/f/preview', 'upstream': 'https://github.com/u/preview'}
        (self.root/'config.yaml').write_text(yaml.safe_dump(raw))
        r.cfg = Config(self.root/'config.yaml')
        r.state = {'schema': 1, 'checkpoint': self.base, 'tasks': {}, 'jobs': {}}
        r.report = {'apps': [], 'errors': []}
        r.heads = {'test': self.source, 'preview': self.target, 'prod': self.target}
        r.open = lambda: None
        r.close = lambda: None
        r.fetch = lambda **kw: None
        request = r.api.request
        r.api.request = lambda method, path, data=None: {'sha': self.source} if method == 'GET' else request(method, path, data)
        return r

    def test_preview_cursor_does_not_consume_test_updates(self):
        r = self.release_runner()
        r.apply = False
        r.heads = {'prod': self.source, 'preview': self.target, 'test': self.target}
        r.discover([r.cfg.environment('preview')])
        self.assertEqual(r.state['sync_checkpoints'], {'test': {'olares': self.base, 'all': self.base}, 'preview': {'olares': self.source, 'all': self.base}})
        self.assertEqual(set(r.state['tasks']), {'preview/app'})
        self.assertEqual(r.state['checkpoint'], self.base)
        r.discover()
        self.assertEqual(set(r.state['tasks']), {'preview/app', 'test/app'})
        self.assertEqual(r.state['sync_checkpoints']['test']['olares'], self.source)

    def test_preview_run_does_not_process_other_targets_or_release_jobs(self):
        r = self.release_runner()
        r.apply = False
        r.heads = {'prod': self.source, 'preview': self.target}
        untouched = {'app': 'else', 'source': 'prod', 'target': 'test', 'status': 'pending'}
        r.state['tasks']['test/else'] = dict(untouched)
        r.state['jobs']['queued'] = {'id': 'queued', 'status': 'queued', 'app': 'app', 'target': 'prod', 'source': 'test'}
        fetched = []
        r.fetch = lambda **kw: fetched.append(kw)
        processed = []
        r.process = lambda task: processed.append((task['source'], task['target']))
        r.run(scope='preview')
        self.assertEqual(fetched, [{'sync_forks': False, 'names': {'prod', 'preview'}}])
        self.assertEqual(processed, [('prod', 'preview')])
        self.assertEqual(r.state['tasks']['test/else'], untouched)
        self.assertEqual(r.state['jobs']['queued']['status'], 'queued')

    def test_preview_missing_fails_before_open_or_fetch(self):
        r, task = self.runner_task()
        with self.assertRaisesRegex(Error, 'environment not configured: preview'):
            r.run(scope='preview')

    def test_sync_scope_parser_default_explicit_and_exclusive(self):
        from maintenance import parser
        self.assertEqual(parser().parse_args(['run']).scope, 'all')
        self.assertEqual(parser().parse_args(['run', '--all']).scope, 'all')
        self.assertEqual(parser().parse_args(['run', '--preview', '--apply']).scope, 'preview')
        args = parser().parse_args(['run', '--preview', '--all'])
        self.assertTrue(args.all_apps)
        r = self.release_runner()
        with self.assertRaisesRegex(Error, '--all requires --preview'):
            r.run(all_apps=True)

    def test_submitter_field_ignores_helm_elsewhere_and_developer(self):
        from gitops import manifest_field
        raw = b'metadata:\n  version: 2.0.0\nspec:\n  developer: Olares\n  submitter: "Community"\n  upgradeDescription: |\n    first line\n    second line\nresource:\n  {{ if .Values.x }}\n  memory: 1Gi\n  {{ end }}\n'
        self.assertEqual(manifest_field(raw, 'spec', 'submitter'), 'Community')
        self.assertEqual(manifest_field(raw, 'metadata', 'version'), '2.0.0')
        self.assertEqual(manifest_field(raw, 'spec', 'upgradeDescription'), 'first line\nsecond line')

    def test_olares_then_all_preview_does_not_lose_third_party(self):
        r = self.release_runner(); r.apply = False
        r.heads = {'prod': self.source, 'preview': self.target, 'test': self.target}
        r.git.submitter = lambda *args: 'Community'
        r.discover([r.cfg.environment('preview')], 'olares')
        self.assertEqual(r.state['tasks'], {})
        r.discover([r.cfg.environment('preview')], 'all')
        self.assertEqual(set(r.state['tasks']), {'preview/app'})
        self.assertEqual(r.state['sync_checkpoints']['test']['olares'], self.base)

    def test_legacy_third_party_test_task_is_not_executed_on_refresh(self):
        r = self.release_runner();r.apply = False
        r.state['tasks']['test/app'] = {'app': 'app','source':'prod','target':'test','source_sha':self.source,'status':'pending'}
        r.git.submitter = lambda *args:'Community'
        processed=[];r.process=lambda t:processed.append(t)
        r.run(refresh_only=True)
        self.assertEqual(processed,[])
        self.assertEqual(r.state['tasks']['test/app']['status'],'pending')

    def test_test_newer_version_is_reported_without_backport(self):
        r=self.release_runner();r.apply=False
        r.heads={'prod':self.target,'test':self.source,'preview':self.target}
        r.state['checkpoint']=self.target
        r.state['tasks']['test/app']={'app':'app','source':'prod','target':'test','source_sha':self.target,'status':'pending','sync_managed':True}
        processed=[];r.process=lambda t:processed.append(t)
        report=r.run()
        self.assertEqual(processed,[])
        self.assertEqual(report['apps'][0]['status'],'target_ahead')

    def test_default_sync_does_not_execute_preview_origin_task(self):
        r=self.release_runner();r.apply=False
        r.heads={'prod':self.source,'preview':self.source,'test':self.target}
        r.state['checkpoint']=self.source
        r.state['tasks']['test/app']={'app':'app','source':'preview','target':'test','source_sha':self.source,'status':'pending','sync_managed':False}
        processed=[];r.process=lambda t:processed.append(t)
        r.run()
        self.assertEqual(processed,[])

    def test_removed_app_submitter_comes_from_last_manifest(self):
        self.cmd('checkout','source')
        import shutil
        shutil.rmtree(self.work/'app');self.write('app/.remove','')
        removed=self.commit('remove')
        self.git.run('fetch',str(self.work),'+refs/heads/source:refs/heads/source')
        self.assertEqual(self.git.submitter(removed,'app'),'Olares')

    def test_identical_release_is_blocked_before_enqueue(self):
        from lifecycle import submit
        r = self.release_runner()
        r.heads['prod'] = self.source
        result = submit(r, 'publish', 'app', 'test', 'feishu:u')
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['reason'], 'identical')
        self.assertEqual(result['source'], 'test')
        self.assertEqual(result['target'], 'prod')
        self.assertEqual(result['versions'], {'source': '2.0.0', 'target': '2.0.0'})
        self.assertEqual(r.state['jobs'], {})
        self.assertEqual(r.api.creates, 0)

    def test_identical_release_block_survives_unreadable_comparison(self):
        from lifecycle import submit
        r = self.release_runner()
        def boom(*args, **kwargs):
            raise Error('git fetch failed (exit 128)')
        r.fetch = boom
        result = submit(r, 'publish', 'app', 'test', 'feishu:u')
        self.assertEqual(result['job']['status'], 'queued')
        self.assertEqual(len(r.state['jobs']), 1)

    def test_different_release_is_not_blocked(self):
        from lifecycle import submit
        r = self.release_runner()
        result = submit(r, 'publish', 'app', 'test', 'feishu:u')
        self.assertEqual(result['job']['status'], 'queued')

    def test_release_submit_queues_and_deduplicates_message(self):
        from lifecycle import submit
        r = self.release_runner()
        first = submit(r, 'publish', 'app', 'test', 'feishu:user', request_id='msg1')
        j = first['job']; self.assertEqual(j['status'], 'queued')
        self.assertEqual(j['target'], 'prod')
        self.assertEqual(j['backport_after_merge'], ['preview'])
        self.assertEqual(r.api.creates, 0)
        again = submit(r, 'publish', 'app', 'test', 'feishu:user', request_id='msg1')
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['job']['id'], j['id'])
        with self.assertRaises(Error):
            submit(r, 'promote', 'app', 'test', 'feishu:user', request_id='msg1')

    def test_concurrent_app_requests_require_current_acknowledgement(self):
        from lifecycle import submit
        r = self.release_runner()
        one = submit(r, 'promote', 'app', 'test', 'feishu:u', request_id='one')['job']
        two = submit(r, 'publish', 'app', 'test', 'feishu:u', request_id='two')
        self.assertEqual(two['status'], 'confirmation_required')
        self.assertEqual(len(r.state['jobs']), 1)
        accepted = submit(r, 'publish', 'app', 'test', 'feishu:u', ack=[one['id']], request_id='two')
        self.assertEqual(accepted['job']['status'], 'queued')
        self.assertEqual(len(r.state['jobs']), 2)

    def test_background_launch_failure_preserves_queued_job(self):
        import lifecycle
        r = self.release_runner()
        config = self.root/'config.yaml'
        raw = yaml.safe_load(config.read_text()); raw['auth'] = {'github_token': 'test'}
        raw['maintenance'] = {'enabled': True}; config.write_text(yaml.safe_dump(raw))
        output = io.StringIO()
        with patch.object(sys, 'argv', ['lifecycle', '--config', str(config), 'publish', 'app', '--from', 'test', '--actor', 'feishu:u', '--apply']), patch('maintenance.Runner', return_value=r), patch.object(lifecycle, 'GitHub', return_value=r.api), patch.object(lifecycle.subprocess, 'Popen', side_effect=OSError('spawn failed')), redirect_stdout(output):
            self.assertEqual(lifecycle.main(), 0)
        result = json.loads(output.getvalue())
        self.assertEqual(result['job']['status'], 'queued')
        self.assertIn(result['job']['id'], r.state['jobs'])
        self.assertIn('heartbeat', result['worker'])

    def test_release_dry_run_creates_no_remote_pr_or_job(self):
        from lifecycle import submit
        r = self.release_runner(); r.apply = False
        result = submit(r, 'publish', 'app', 'test', 'feishu:u')
        self.assertEqual(result['job']['status'], 'would_create_or_refresh_pr')
        self.assertEqual(r.state['jobs'], {})
        self.assertEqual(r.api.creates, 0)

    def test_release_uses_snapshot_title_and_version(self):
        r, task = self.runner_task()
        self.cmd('checkout', 'source')
        self.write('app/OlaresManifest.yaml', 'metadata:\n  version: 2.0.0\nspec:\n  upgradeDescription: Adds reliable jobs\n')
        sha = self.commit('description')
        self.git.run('fetch', str(self.work), '+refs/heads/source:refs/heads/source')
        task.update(source_sha=sha, operation='publish', requested_title=None)
        r.process(task)
        self.assertEqual(r.api.pr['title'], '[UPDATE][app][2.0.0] Adds reliable jobs')
        self.assertIn('Adds reliable jobs', r.api.pr['body'])
        self.assertEqual(task['previous_version'], '1.0.0')

    def test_merge_dispatch_uses_actual_merge_and_daily_deduplicates(self):
        from lifecycle import followup, enqueue_backport
        r = self.release_runner(); r.base = self.source
        job = {'id': 'release1', 'operation': 'publish', 'app': 'app', 'source': 'test',
               'target': 'prod', 'status': 'merged', 'merge_sha': self.source,
               'backport_after_merge': ['preview']}
        followup(r, job)
        child = r.state['tasks']['preview/app']
        self.assertEqual(child['source_sha'], self.source)
        self.assertEqual(job['children']['preview'], child['id'])
        again = enqueue_backport(r, 'app', 'prod', 'preview', self.source)
        self.assertEqual(child['id'], again['id'])
        self.assertEqual(len(r.state['tasks']), 1)

    def test_unmerged_closed_release_does_not_backport(self):
        from lifecycle import followup
        r = self.release_runner()
        job = {'id': 'release1', 'app': 'app', 'target': 'prod', 'status': 'rejected',
               'backport_after_merge': ['preview']}
        followup(r, job)
        self.assertEqual(r.state['tasks'], {})

    def test_followup_failure_keeps_merged_and_audits_until_consistent(self):
        from lifecycle import followup, audit
        r = self.release_runner()
        job = {'id': 'release1', 'operation': 'publish', 'app': 'app', 'source': 'test',
               'target': 'prod', 'status': 'merged', 'merge_sha': None,
               'backport_after_merge': ['preview']}
        r.state['jobs']['release1'] = job
        r.heads['prod'] = self.source
        followup(r, job); audit(r)
        self.assertEqual(job['status'], 'merged')
        self.assertEqual(job['followup_status'], 'needs_engineer')
        self.assertEqual(job['consistency'][0]['status'], 'needs_engineer')
        r.heads['preview'] = self.source
        audit(r)
        self.assertEqual(job['followup_status'], 'complete')

    def test_new_production_update_supersedes_merge_snapshot(self):
        from lifecycle import followup
        r = self.release_runner(); r.base = self.source
        job = {'id': 'release1', 'app': 'app', 'target': 'prod', 'status': 'merged',
               'merge_sha': self.base, 'backport_after_merge': ['preview']}
        followup(r, job)
        self.assertEqual(r.state['tasks']['preview/app']['source_sha'], self.source)
        self.assertEqual(job['followup_superseded_by'], self.source)

    def test_pending_backport_not_overwritten_by_new_event(self):
        from lifecycle import enqueue_backport
        r = self.release_runner()
        old = enqueue_backport(r, 'app', 'prod', 'preview', self.base)
        old.update(pr=12, status='awaiting_review')
        deferred = enqueue_backport(r, 'app', 'prod', 'preview', self.source)
        self.assertEqual(deferred['id'], old['id'])
        self.assertEqual(old['source_sha'], self.base)
        self.assertEqual(old['next_event']['source_sha'], self.source)
        old['status'] = 'merged'
        new = enqueue_backport(r, 'app', 'prod', 'preview', self.source)
        self.assertNotEqual(old['id'], new['id'])
        self.assertEqual(r.state['task_history'][0]['id'], old['id'])

    def test_closed_release_tracks_actual_merge_sha(self):
        r, task = self.runner_task()
        task['operation'] = 'publish'
        r.process(task)
        r.api.pr.update(state='closed', merged_at='now', merge_commit_sha=self.source)
        r.process(task)
        self.assertEqual(task['status'], 'merged')
        self.assertEqual(task['merge_sha'], self.source)

    def test_waiting_release_does_not_rewrite_active_backport(self):
        from lifecycle import tick
        r = self.release_runner()
        r.state['tasks']['prod/app'] = {'app': 'app', 'target': 'prod', 'status': 'awaiting_review', 'pr': 1}
        r.state['jobs']['j'] = {'id': 'j', 'app': 'app', 'source': 'test', 'target': 'prod',
                              'status': 'queued', 'created_at': 1}
        tick(r)
        self.assertEqual(r.state['jobs']['j']['status'], 'waiting')
        self.assertEqual(r.api.creates, 0)


    def test_needs_attention_release_still_detects_later_merge(self):
        from lifecycle import tick
        r = self.release_runner()
        r.state['jobs']['j'] = {'id': 'j', 'operation': 'publish', 'app': 'app', 'source': 'test',
            'target': 'prod', 'status': 'needs_attention', 'pr': 9, 'created_at': 1, 'error': 'old'}
        r.api.get_pr = lambda *args: {'state': 'closed', 'merged_at': 'now', 'merge_commit_sha': self.source}
        tick(r)
        self.assertEqual(r.state['jobs']['j']['status'], 'merged')
        self.assertNotIn('error', r.state['jobs']['j'])

    def test_absent_source_app_never_reported_satisfied(self):
        r, task = self.runner_task()
        task['app'] = 'missing'
        with self.assertRaisesRegex(Error, 'app is absent'):
            r.process(task)



class SemverTests(unittest.TestCase):
    def test_order(self):
        values = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0"]
        self.assertEqual(sorted(values, key=version_key), values)
        self.assertEqual(version_key("1.0.0+a"), version_key("1.0.0+b"))


class SchedulerTests(unittest.TestCase):
    def test_native_delivery_and_quiet_refresh(self):
        import scheduled
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "config.yaml"
            config.write_text(yaml.safe_dump(sample(root)))
            (root / "state").mkdir()
            reply = subprocess.CompletedProcess([], 0, json.dumps({"apps": [], "forks": [], "errors": []}), "")
            for mode, expected in (("run", "Haro 仓库维护 完成"), ("refresh", "NO_REPLY")):
                output = io.StringIO()
                with patch.object(sys, "argv", ["scheduled", "--config", str(config), mode]), \
                     patch.object(scheduled.subprocess, "run", return_value=reply) as run, redirect_stdout(output):
                    self.assertEqual(scheduled.main(), 0)
                    self.assertEqual(run.call_count, 1)  # no nested gateway message call
                    self.assertIn(expected, output.getvalue())

    def test_overlapping_refresh_skips_without_alert(self):
        import scheduled
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.yaml"
            config.write_text(yaml.safe_dump(sample(Path(tmp))))
            reply = subprocess.CompletedProcess([], 1, json.dumps({"error": "another local maintenance run is active"}), "")
            output = io.StringIO()
            with patch.object(sys, "argv", ["scheduled", "--config", str(config), "refresh"]), patch.object(scheduled.subprocess, "run", return_value=reply), redirect_stdout(output):
                self.assertEqual(scheduled.main(), 0)
                self.assertEqual(output.getvalue().strip(), "NO_REPLY")


    def test_job_only_failure_is_not_suppressed(self):
        import scheduled
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); config = root/'config.yaml'
            config.write_text(yaml.safe_dump(sample(root))); (root/'state').mkdir()
            job = {'id': 'j', 'app': 'app', 'source': 'test', 'target': 'prod', 'status': 'merged',
                   'followup_status': 'needs_engineer', 'consistency': [{'target': 'preview', 'status': 'needs_engineer'}]}
            reply = subprocess.CompletedProcess([], 0, json.dumps({'apps': [], 'jobs': [job]}), '')
            output = io.StringIO()
            with patch.object(sys, 'argv', ['scheduled', '--config', str(config), 'refresh']), patch.object(scheduled.subprocess, 'run', return_value=reply), redirect_stdout(output):
                self.assertEqual(scheduled.main(), 0)
            self.assertIn('需工程师检查', output.getvalue())
            self.assertIn('Job j', output.getvalue())

    def test_blocked_tasks_report_once_and_stay_successful_runs(self):
        """Attention state is reported in the summary, never as a failed run.

        A blocked task keeps the maintenance report at exit code 2. The first
        refresh delivers that state; later refreshes are quiet. Neither may be
        recorded as a failed run, otherwise the scheduler disables a healthy
        automation after repeated quiet polls.
        """
        import scheduled
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); config = root / 'config.yaml'
            config.write_text(yaml.safe_dump(sample(root))); (root / 'state').mkdir()
            task = {'app': 'agentj', 'target': 'test', 'status': 'blocked',
                    'error': 'GitHub identity is not an app owner', 'url': ''}
            reply = subprocess.CompletedProcess([], 2, json.dumps({'apps': [task], 'errors': []}), '')
            output = io.StringIO()
            with patch.object(sys, 'argv', ['scheduled', '--config', str(config), 'refresh']), patch.object(scheduled.subprocess, 'run', return_value=reply), redirect_stdout(output):
                self.assertEqual(scheduled.main(), 0)
            self.assertIn('需要处理', output.getvalue())
            self.assertIn('GitHub identity is not an app owner', output.getvalue())
            quiet = io.StringIO()
            with patch.object(sys, 'argv', ['scheduled', '--config', str(config), 'refresh']), patch.object(scheduled.subprocess, 'run', return_value=reply), redirect_stdout(quiet):
                self.assertEqual(scheduled.main(), 0)
            self.assertEqual(quiet.getvalue().strip(), 'NO_REPLY')

    def test_daily_report_with_blocked_tasks_is_delivered_without_failure(self):
        import scheduled
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); config = root / 'config.yaml'
            config.write_text(yaml.safe_dump(sample(root))); (root / 'state').mkdir()
            task = {'app': 'agentj', 'target': 'preview', 'status': 'blocked',
                    'error': 'GitHub identity is not an existing target owner', 'url': ''}
            reply = subprocess.CompletedProcess([], 2, json.dumps({'apps': [task], 'errors': []}), '')
            output = io.StringIO()
            with patch.object(sys, 'argv', ['scheduled', '--config', str(config), 'run']), patch.object(scheduled.subprocess, 'run', return_value=reply), redirect_stdout(output):
                self.assertEqual(scheduled.main(), 0)
            self.assertIn('需要处理', output.getvalue())
            self.assertIn('agentj', output.getvalue())

    def test_maintenance_failure_keeps_failing_the_run(self):
        import scheduled
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); config = root / 'config.yaml'
            config.write_text(yaml.safe_dump(sample(root))); (root / 'state').mkdir()
            reply = subprocess.CompletedProcess([], 1, json.dumps({"error": "GitHub transport error; write outcome may be unknown"}), '')
            output = io.StringIO()
            with patch.object(sys, 'argv', ['scheduled', '--config', str(config), 'run']), patch.object(scheduled.subprocess, 'run', return_value=reply), redirect_stdout(output):
                self.assertEqual(scheduled.main(), 1)
            self.assertIn('GitHub transport error', output.getvalue())

    def test_unreadable_report_fails_the_run(self):
        import scheduled
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); config = root / 'config.yaml'
            config.write_text(yaml.safe_dump(sample(root))); (root / 'state').mkdir()
            reply = subprocess.CompletedProcess([], 0, 'not json', '')
            output = io.StringIO()
            with patch.object(sys, 'argv', ['scheduled', '--config', str(config), 'run']), patch.object(scheduled.subprocess, 'run', return_value=reply), redirect_stdout(output):
                self.assertEqual(scheduled.main(), 1)
            self.assertIn('did not return JSON', output.getvalue())



if __name__ == "__main__":
    unittest.main()


class SyncLabelTests(unittest.TestCase):
    def test_labels_only_backports_and_only_apply(self):
        r = Runner.__new__(Runner)
        r.api = Mock()
        target = Mock(repo="org/apps")
        pr = {"number": 12, "labels": [{"name": "existing"}]}
        r.apply = False
        task = {"operation": "backport"}
        r.ensure_sync_label(task, target, pr)
        self.assertEqual(task["labels"], ["enhancement"])
        r.api.request.assert_not_called()
        r.apply = True
        for op in ("promote", "publish"):
            r.ensure_sync_label({"operation": op}, target, pr)
        r.api.request.assert_not_called()
        r.ensure_sync_label({}, target, pr)  # legacy backport
        r.api.request.assert_called_once_with("POST", "repos/org/apps/issues/12/labels",
                                              {"labels": ["enhancement"]})
        r.api.reset_mock()
        r.ensure_sync_label(task, target, {"number": 12, "labels": [{"name": "enhancement"}]})
        r.api.request.assert_not_called()

    def test_label_failure_is_not_silently_accepted(self):
        r = Runner.__new__(Runner)
        r.apply = True
        r.api = Mock()
        r.api.request.side_effect = Error("label write failed")
        with self.assertRaisesRegex(Error, "label write failed"):
            r.ensure_sync_label({}, Mock(repo="org/apps"), {"number": 12})
