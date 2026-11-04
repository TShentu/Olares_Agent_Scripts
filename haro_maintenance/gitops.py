"""Git object operations isolated from legacy working trees."""
from __future__ import annotations

import base64
import os
import re
import subprocess
import tempfile
import textwrap
from pathlib import Path

import yaml

from config import Error


def app_name(name):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        raise Error("invalid app directory name")
    return name


def manifest_field(raw, section, key):
    """Read a static field without evaluating Helm templates elsewhere in YAML."""
    lines = (raw or b'').decode().splitlines()
    roots = [i for i, line in enumerate(lines) if re.fullmatch(re.escape(section) + r":\s*(?:#.*)?", line)]
    if len(roots) != 1:
        return None
    start = roots[0] + 1
    end = next((i for i in range(start, len(lines)) if lines[i] and not lines[i][0].isspace() and not lines[i].startswith('#')), len(lines))
    block = lines[start:end]
    fields = [(i, len(line)-len(line.lstrip())) for i,line in enumerate(block)
              if re.match(r"^\s+[A-Za-z_][A-Za-z0-9_-]*:", line)]
    indent = min((n for _,n in fields), default=0)
    matches = [i for i,n in fields if n == indent and re.match(r"^\s+" + re.escape(key) + r":",block[i])]
    if len(matches) != 1:
        return None
    i = matches[0]
    j = next((j for j in range(i+1,len(block)) if block[j].strip() and len(block[j])-len(block[j].lstrip()) <= indent), len(block))
    try:
        value = yaml.safe_load(textwrap.dedent("\n".join(block[i:j])))
        return value.get(key) if isinstance(value,dict) else None
    except yaml.YAMLError:
        return None


def version_key(value):
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?", value)
    if not match:
        raise Error("chart version must be SemVer")
    pre = match[4]
    parts = []
    if pre:
        for item in pre.split("."):
            if not item or (item.isdigit() and len(item) > 1 and item[0] == "0"):
                raise Error("invalid SemVer prerelease")
            parts.append((0, int(item)) if item.isdigit() else (1, item))
    return tuple(int(match[i]) for i in (1, 2, 3)) + (int(pre is None), tuple(parts))


class Git:
    def __init__(self, path, token=None):
        self.path = Path(path)
        self.env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1")
        # Environment-based HTTP auth: never written into remotes or argv.
        self.env.update(GIT_CONFIG_COUNT="3", GIT_CONFIG_KEY_0="credential.helper", GIT_CONFIG_VALUE_0="",
                        GIT_CONFIG_KEY_1="core.hooksPath", GIT_CONFIG_VALUE_1="/dev/null",
                        GIT_CONFIG_KEY_2="protocol.file.allow", GIT_CONFIG_VALUE_2="never")
        if token:
            header = "AUTHORIZATION: basic " + base64.b64encode(("x-access-token:" + token).encode()).decode()
            self.env.update(GIT_CONFIG_COUNT="4", GIT_CONFIG_KEY_3="http.https://github.com/.extraheader", GIT_CONFIG_VALUE_3=header)
        self.env.update(GIT_AUTHOR_NAME="Haro", GIT_AUTHOR_EMAIL="haro@users.noreply.github.com",
                        GIT_COMMITTER_NAME="Haro", GIT_COMMITTER_EMAIL="haro@users.noreply.github.com")
        self.path.mkdir(parents=True, exist_ok=True)
        if not (self.path / "HEAD").exists():
            self.run("init", "--bare", str(self.path))

    def run(self, *args, input=None, check=True, env=None):
        r = subprocess.run(["git", "--git-dir=" + str(self.path), *args], input=input,
                           capture_output=True, env=env or self.env, timeout=300)
        if check and r.returncode:
            # Do not echo arbitrary remote errors or credential-bearing diagnostics.
            raise Error(f"git {args[0]} failed (exit {r.returncode})")
        return r

    def text(self, *args, **kw):
        return self.run(*args, **kw).stdout.decode().strip()

    def fetch(self, url, branch, ref):
        self.run("fetch", "--no-tags", url, "+refs/heads/" + branch + ":" + ref)
        return self.text("rev-parse", ref)

    def remote_head(self, url, branch):
        lines = self.text("ls-remote", "--heads", url, "refs/heads/" + branch).splitlines()
        return lines[0].split()[0] if lines else None

    def ancestor(self, old, new):
        r = self.run("merge-base", "--is-ancestor", old, new, check=False)
        if r.returncode not in (0, 1):
            raise Error("cannot establish Git ancestry")
        return r.returncode == 0

    def push(self, url, commit, branch, expected):
        self.run("push", "--force-with-lease=refs/heads/" + branch + ":" + (expected or ""),
                 url, commit + ":refs/heads/" + branch)

    def tree(self, commit, path):
        r = self.run("rev-parse", "--verify", commit + ":" + path, check=False)
        return r.stdout.decode().strip() if r.returncode == 0 else None

    def file(self, commit, path):
        r = self.run("show", commit + ":" + path, check=False)
        return r.stdout if r.returncode == 0 else None

    def changed_apps(self, old, new):
        names = self.run("diff", "--name-only", "--no-renames", "-z", old, new).stdout.split(b"\0")
        candidates = {p.decode().split("/")[0] for p in names if b"/" in p}
        return sorted(a for a in candidates if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", a) and
                      any(self.tree(c, a + "/" + f) for c in (old, new)
                          for f in ("OlaresManifest.yaml", ".remove", ".suspend")))

    def apps(self, commit):
        names = self.text("ls-tree", "--name-only", commit).splitlines()
        return sorted(a for a in names if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", a) and
                      any(self.tree(commit, a + "/" + f) for f in ("OlaresManifest.yaml", ".remove")))

    def submitter(self, commit, app):
        path = app + "/OlaresManifest.yaml"
        raw = self.file(commit, path)
        if raw is None and self.tree(commit, app + "/.remove"):
            old = self.text("log", "-1", "--diff-filter=AM", "--format=%H", commit, "--", path)
            raw = self.file(old, path) if old else None
        value = manifest_field(raw, "spec", "submitter")
        return value.strip() if isinstance(value, str) else None

    def snapshot(self, source, target, app, message):
        """Construct an index tree: target outside app, exact source inside app."""
        app_name(app)
        src_tree = self.tree(source, app)
        if not src_tree:
            raise Error("source app is absent; explicit .remove protocol is required")
        if self.text("cat-file", "-t", src_tree) != "tree":
            raise Error("app must be a directory, not a symlink")
        if src_tree == self.tree(target, app):
            return None
        with tempfile.TemporaryDirectory(prefix="haro-index-") as tmp:
            env = dict(self.env, GIT_INDEX_FILE=str(Path(tmp) / "index"))
            self.run("read-tree", target, env=env)
            paths = self.run("ls-files", "-z", "--", app, env=env).stdout
            if paths:
                removals = b"".join(b"0 " + b"0" * 40 + b"\t" + p + b"\0" for p in paths.split(b"\0") if p)
                self.run("update-index", "-z", "--index-info", input=removals, env=env)
            self.run("read-tree", "-i", "--prefix=" + app + "/", src_tree, env=env)
            tree = self.text("write-tree", env=env)
            commit = self.text("commit-tree", tree, "-p", target, input=message.encode())
        self.verify(source, target, commit, app)
        return commit

    def verify(self, source, target, head, app):
        if self.text("rev-parse", head + "^") != target:
            raise Error("PR head is not a direct child of target snapshot")
        if self.tree(source, app) != self.tree(head, app):
            raise Error("source snapshot mismatch")
        paths = self.run("diff", "--name-only", "--no-renames", "-z", target, head).stdout.split(b"\0")
        if any(p and not p.startswith((app + "/").encode()) for p in paths):
            raise Error("changes escape the app directory")
        if self.run("diff", "--binary", target, head).stdout != self.run("diff", "--binary", target + "..." + head).stdout:
            raise Error("two-dot and three-dot diffs disagree")

    def sync_fork(self, environment, upstream, fork, apply):
        if upstream == fork:
            return {"environment": environment.name, "status": "in_sync"}
        archive = None
        if fork and not self.ancestor(fork, upstream):
            archive = "haro/archive/" + environment.branch.replace("/", "-") + "-" + fork
            if apply:
                existing = self.remote_head(environment.github, archive)
                if existing not in (None, fork):
                    raise Error("archive ref collision")
                if not existing:
                    self.push(environment.github, fork, archive, None)
        if apply:
            self.push(environment.github, upstream, environment.branch, fork)
        return {"environment": environment.name, "status": "synced" if apply else "would_sync",
                "archive": archive, "old": fork, "new": upstream}


def owner_authorized(raw, login, org_member=None):
    """Read only owner usernames, never titles/comments or arbitrary YAML words."""
    try:
        data = yaml.safe_load(raw or b"")
    except yaml.YAMLError:
        raise Error("invalid owners YAML") from None
    if isinstance(data, dict):
        data = data.get("owners", [])
    if isinstance(data, str):
        data = data.splitlines()  # legacy plain-login files
    if not isinstance(data, list):
        return False
    names = []
    for item in data:
        value = item.get("username") if isinstance(item, dict) else item
        if isinstance(value, str) and re.fullmatch(r"@?[A-Za-z0-9_-]+", value.strip()):
            names.append(value.strip())
    if any(name.lstrip("@").casefold() == login.casefold() for name in names):
        return True
    return bool(org_member and any(org_member(name[1:]) for name in names if name.startswith("@")))


def validate_app(git, source, target, app, login, suspend_rule="increase", check_owners=True, org_member=None):
    """Validate target protocol without changing the selected source bytes."""
    app_name(app)
    files = git.text("ls-tree", "-r", "--name-only", source, "--", app).splitlines()
    if not files:
        raise Error("source directory missing; cannot infer removal")
    removing = app + "/.remove" in files
    suspending = app + "/.suspend" in files
    old_bytes = git.file(target, app + "/Chart.yaml")
    old = yaml.safe_load(old_bytes) if old_bytes else None
    if removing:
        if files != [app + "/.remove"] or not old:
            raise Error("REMOVE requires only .remove and an existing target version")
        version = str(old["version"])
        kind = "REMOVE"
        owners_bytes = git.file(target, app + "/owners")
    else:
        manifest = git.file(source, app + "/OlaresManifest.yaml")
        chart = yaml.safe_load(git.file(source, app + "/Chart.yaml") or b"")
        if not manifest or not isinstance(chart, dict):
            raise Error("source Chart.yaml and OlaresManifest.yaml are required")
        version = str(chart.get("version", ""))
        if not re.fullmatch(r"[0-9A-Za-z.+-]+", version):
            raise Error("invalid chart version")
        if str(manifest_field(manifest, "metadata", "version")) != version:
            raise Error("Chart/Manifest versions differ")
        for path in files:
            if path.endswith("/Chart.yaml"):
                data = yaml.safe_load(git.file(source, path))
                if str(data.get("version")) != version:
                    raise Error("subchart version differs")
        if git.tree(target, app + "/.remove"):
            raise Error("target app is removed; explicit restoration review required")
        if old:
            valid = version_key(version) > version_key(str(old["version"]))
            if suspending and suspend_rule == "equal":
                valid = version_key(version) == version_key(str(old["version"]))
            if not valid:
                raise Error("source version violates target version rule (no automatic bump)")
        elif suspending:
            raise Error("cannot suspend an app absent from target")
        kind = "SUSPEND" if suspending else ("UPDATE" if old else "NEW")
        owners_bytes = git.file(source, app + "/owners")
        if old and check_owners:
            existing_owners = git.file(target, app + "/owners")
            if not owner_authorized(existing_owners, login, org_member):
                raise Error("GitHub identity is not an existing target owner")
    if check_owners and not owner_authorized(owners_bytes, login, org_member):
        raise Error("GitHub identity is not an app owner")
    return kind, version
