"""Backwards-compatible configuration. No credentials in public projections."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml


class Error(RuntimeError):
    pass


def repo_slug(url):
    u = urlparse(url)
    if u.scheme != "https" or u.netloc != "github.com" or u.query or u.fragment:
        raise Error("repository URL must be clean https://github.com/owner/repo")
    slug = u.path.strip("/").removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", slug):
        raise Error("invalid repository URL")
    return slug


@dataclass(frozen=True)
class Environment:
    name: str
    level: int
    github: str
    upstream: str
    local_path: str
    branch: str

    @property
    def fork(self):
        return repo_slug(self.github)

    @property
    def repo(self):
        return repo_slug(self.upstream)


class Config:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.raw = yaml.safe_load(self.path.read_text())
        if not isinstance(self.raw, dict):
            raise Error("configuration must be a YAML mapping")
        candidates = {}
        reserved = {"auth", "maintenance", "runtime", "notifications", "recovery", "legacy", "schema_version"}
        for name, value in self.raw.items():
            if name in reserved:
                continue
            if name in {"test", "preview", "prod"} or (isinstance(value, dict) and
                    any(k in value for k in ("github", "upstream", "local_path", "level"))):
                if not re.fullmatch(r"[a-z][a-z0-9_-]*", name) or not isinstance(value, dict):
                    raise Error("invalid environment: " + str(name))
                candidates[name] = value
        explicit = any("level" in v for v in candidates.values())
        defaults = {"test": 10, "preview": 20, "prod": 30}
        self.envs = []
        for name, value in candidates.items():
            for k in ("github", "upstream", "local_path"):
                if not isinstance(value.get(k), str) or not value[k].strip():
                    raise Error(f"{name}.{k} is required")
            level = value.get("level") if explicit else defaults.get(name)
            if type(level) is not int:
                raise Error("every environment needs a unique integer level when any level is explicit")
            branch = value.get("git_branch", self.raw.get("git_branch", "main"))
            if not isinstance(branch, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", branch) or ".." in branch:
                raise Error("invalid git branch")
            e = Environment(name, level, value["github"], value["upstream"], value["local_path"], branch)
            if e.fork == e.repo:
                raise Error(f"{name}: fork must differ from upstream")
            self.envs.append(e)
        self.envs.sort(key=lambda e: e.level)
        if len(self.envs) < 2 or len({e.level for e in self.envs}) != len(self.envs):
            raise Error("at least two environments with distinct levels are required")
        if len({(e.fork, e.branch) for e in self.envs}) != len(self.envs):
            raise Error("environments must not share the same fork branch")
        self.maintenance = self.raw.get("maintenance") or {}
        for key, expected in (("strategy", "source_snapshot"), ("delivery", "draft_pr"), ("scope", "changed_apps")):
            if (self.maintenance.get("backport") or {}).get(key, expected) != expected:
                raise Error("unsupported backport " + key)
        root = (self.raw.get("runtime") or {}).get("maintenance_root", str(self.path.parent / "state"))
        self.root = Path(root).expanduser().resolve()
        for e in self.envs:
            old = Path(e.local_path).expanduser().resolve()
            if self.root == old or old in self.root.parents or self.root in old.parents:
                raise Error("maintenance_root must be separate from legacy checkouts")

    def environment(self, name):
        for e in self.envs:
            if e.name == name:
                return e
        raise Error("environment not configured: " + name)

    def route(self, operation, source=None):
        choices = self.envs[:-1]
        if source is None:
            if len(choices) != 1:
                raise Error("specify --from; multiple source environments are available")
            src = choices[0]
        else:
            src = self.environment(source)
        higher = [e for e in self.envs if e.level > src.level]
        if not higher:
            raise Error("source is already the highest environment")
        dst = higher[0] if operation == "promote" else higher[-1]
        return {"source": src.name, "target": dst.name,
                "backport_after_merge": [e.name for e in self.envs if src.level < e.level < dst.level]}

    def token(self, token_file=None, token_env=None):
        auth = self.raw.get("auth") or {}
        if token_file:
            value = self._token_file(token_file)
        elif token_env:
            value = os.environ.get(token_env, "")
        elif auth.get("github_token"):
            value = auth["github_token"]
        elif auth.get("github_token_file"):
            value = self._token_file(auth["github_token_file"])
        else:
            value = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN", "")
            if not value and os.environ.get("GITHUB_TOKEN_FILE"):
                value = self._token_file(os.environ["GITHUB_TOKEN_FILE"])
        if not isinstance(value, str) or not value.strip() or "<" in value:
            raise Error("GitHub credential is missing; no identity fallback is attempted")
        return value.strip()

    def _token_file(self, name):
        p = Path(name).expanduser()
        if not p.is_absolute():
            p = self.path.parent / p
        content = p.read_text().strip()
        for line in content.splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() in {"GITHUB_TOKEN", "GH_TOKEN"}:
                return v.strip().strip("\"'")
        if "\n" not in content and "=" not in content:
            return content
        raise Error("unrecognized token file format")

    def public(self):
        return {"config": str(self.path), "environments": [e.__dict__ for e in self.envs],
                "maintenance_root": str(self.root), "enabled": self.maintenance.get("enabled", False),
                "schedule": self.maintenance.get("schedule", "0 3 * * *"),
                "timezone": self.maintenance.get("timezone", "Asia/Shanghai"),
                "credential_configured": bool(self.raw.get("auth"))}
