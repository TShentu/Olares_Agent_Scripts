#!/usr/bin/env python3
"""
Git operations and GitHub credential helpers for sync_chart (standalone; no compare_chart).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import requests

from pat_url import github_authenticated_https_url
from repo_config import DEFAULT_CONFIG_PATH, load_config, resolve_roots

_GITHUB_EXTRAHEADER = "http.https://github.com/.extraheader"
_GITHUB_API_ACCEPT = "application/vnd.github+json"


def _git_subprocess_env() -> dict[str, str]:
    """Avoid interactive username/password prompts when HTTPS auth fails or is missing."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GCM_INTERACTIVE", "never")
    return env


_CREDENTIAL_FILE_KEYS = frozenset(
    {
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "GITHUB_USERNAME",
        "GH_USERNAME",
        "GITHUB_EMAIL",
        "GH_EMAIL",
    }
)


def parse_github_credentials_file(
    path: Path,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Parse a credential file: ``KEY=value`` lines only (case-insensitive keys)."""
    p = Path(path).expanduser()
    if not p.is_file():
        return None, None, None
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return None, None, None
    kv: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line or line.startswith("http"):
            continue
        key, _, rest = line.partition("=")
        ku = key.strip().upper()
        if ku not in _CREDENTIAL_FILE_KEYS:
            continue
        val = _strip_token(rest)
        if val:
            kv[ku] = val

    token = kv.get("GITHUB_TOKEN") or kv.get("GH_TOKEN")
    username = kv.get("GITHUB_USERNAME") or kv.get("GH_USERNAME")
    email = kv.get("GITHUB_EMAIL") or kv.get("GH_EMAIL")
    return token, username, email


def resolve_github_identity_from_env() -> tuple[Optional[str], Optional[str]]:
    u = os.environ.get("GITHUB_USERNAME") or os.environ.get("GH_USERNAME")
    e = os.environ.get("GITHUB_EMAIL") or os.environ.get("GH_EMAIL")
    u = u.strip() if isinstance(u, str) and u.strip() else None
    e = e.strip() if isinstance(e, str) and e.strip() else None
    return u, e


def read_github_token_from_file(path: Path) -> Optional[str]:
    t, _, _ = parse_github_credentials_file(path)
    return t


def resolve_github_token(
    env_var: Optional[str] = None,
    *,
    token_file: Optional[Path] = None,
) -> Optional[str]:
    if token_file is not None:
        return read_github_token_from_file(token_file)
    if env_var:
        v = os.environ.get(env_var)
        if v and v.strip():
            return _strip_token(v)
    for key in ("GITHUB_TOKEN", "GH_TOKEN"):
        v = os.environ.get(key)
        if v and v.strip():
            return _strip_token(v)
    path = os.environ.get("GITHUB_TOKEN_FILE")
    if path:
        return read_github_token_from_file(Path(path))
    return None


def verify_github_token(token: str, *, timeout: float = 30.0) -> None:
    """GET api.github.com/user; exit with error message if token is not valid."""
    r = requests.get(
        "https://api.github.com/user",
        headers={
            "Authorization": f"token {token}",
            "Accept": _GITHUB_API_ACCEPT,
        },
        timeout=timeout,
    )
    if r.status_code == 401:
        print("错误: GitHub token 无效或已撤销。", file=sys.stderr)
        sys.exit(1)
    if r.status_code >= 400:
        print(r.text, file=sys.stderr)
        print(f"错误: 校验 token 失败 (HTTP {r.status_code})。", file=sys.stderr)
        sys.exit(1)


def _strip_token(s: str) -> str:
    s = s.strip().strip('"').strip("'")
    return s


def _git_base(token: Optional[str]) -> list[str]:
    cmd = ["git", "-c", "credential.helper="]
    if token:
        # GitHub PAT: use ``Authorization: token`` for git/libcurl HTTPS; ``bearer``
        # can be rejected on some setups while ``curl``/REST with Bearer still returns 200.
        cmd.extend(
            [
                "-c",
                f"{_GITHUB_EXTRAHEADER}=Authorization: token {token}",
            ]
        )
    return cmd


def run_git(
    repo: Path,
    *git_args: str,
    token: Optional[str] = None,
) -> subprocess.CompletedProcess:
    # PAT over HTTPS: embed in URL (oauth2:) — works cross-platform; extraHeader is flaky.
    if token and len(git_args) >= 2 and git_args[0] == "fetch":
        remote_name = git_args[1]
        rest = list(git_args[2:])
        cp_url = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", remote_name],
            capture_output=True,
            text=True,
            check=False,
            env=_git_subprocess_env(),
        )
        if cp_url.returncode == 0:
            raw = (cp_url.stdout or "").strip()
            authed = github_authenticated_https_url(raw, token, style="oauth2")
            if authed:
                refspec = f"+refs/heads/*:refs/remotes/{remote_name}/*"
                cmd = ["git", "-c", "credential.helper=", "-C", str(repo), "fetch"]
                if "--prune" in rest:
                    cmd.append("--prune")
                cmd.extend([authed, refspec])
                return subprocess.run(
                    cmd,
                    text=True,
                    capture_output=True,
                    check=False,
                    env=_git_subprocess_env(),
                )
    cmd = _git_base(token) + ["-C", str(repo), *git_args]
    return subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        check=False,
        env=_git_subprocess_env(),
    )


def git_toplevel(start: Path) -> Path:
    p = start.resolve()
    if p.is_file():
        p = p.parent
    cp = run_git(p, "rev-parse", "--show-toplevel", token=None)
    if cp.returncode != 0:
        print(f"错误: 不是 git 仓库: {start}", file=sys.stderr)
        if cp.stderr:
            print(cp.stderr, file=sys.stderr)
        sys.exit(1)
    return Path(cp.stdout.strip())


def has_dirty_tree(repo: Path) -> bool:
    cp = run_git(repo, "status", "--porcelain", token=None)
    return bool(cp.stdout.strip())


def remote_exists(repo: Path, name: str) -> bool:
    cp = run_git(repo, "remote", "get-url", name, token=None)
    return cp.returncode == 0


def ensure_upstream_remote(repo: Path, upstream_url: Optional[str]) -> bool:
    if upstream_url and upstream_url.strip():
        u = upstream_url.strip()
        if not remote_exists(repo, "upstream"):
            cp = run_git(repo, "remote", "add", "upstream", u, token=None)
            if cp.returncode != 0:
                print(cp.stderr, file=sys.stderr)
                sys.exit(1)
            print(f"  已添加 remote upstream -> {u}")
            return True
        print("  已存在 remote upstream，跳过 add")
        return True
    return remote_exists(repo, "upstream")


def checkout_branch(repo: Path, branch: str) -> None:
    cp = run_git(repo, "switch", branch, token=None)
    if cp.returncode == 0:
        return
    cp2 = run_git(repo, "switch", "-C", branch, f"origin/{branch}", token=None)
    if cp2.returncode == 0:
        print(f"  已基于 origin/{branch} 创建并切换分支 {branch}")
        return
    print(cp.stderr or cp.stdout, file=sys.stderr)
    print(cp2.stderr or cp2.stdout, file=sys.stderr)
    sys.exit(1)


def must_ok(cp: subprocess.CompletedProcess[str], what: str) -> None:
    if cp.returncode != 0:
        print(f"错误: {what}", file=sys.stderr)
        if cp.stdout:
            print(cp.stdout, file=sys.stderr)
        if cp.stderr:
            print(cp.stderr, file=sys.stderr)
        sys.exit(1)


def sync_one_repo(
    name: str,
    work_tree: Path,
    *,
    branch: str,
    upstream_url: Optional[str],
    token: Optional[str],
    allow_dirty: bool,
) -> None:
    print(f"--- {name} ---")
    repo = git_toplevel(work_tree)
    print(f"  git 根目录: {repo}")

    if has_dirty_tree(repo) and not allow_dirty:
        print(
            "错误: 工作区有未提交更改；请先提交或 stash，或使用 --allow-dirty",
            file=sys.stderr,
        )
        sys.exit(1)

    print("  git fetch origin …")
    must_ok(run_git(repo, "fetch", "origin", "--prune", token=token), "git fetch origin")

    has_upstream = ensure_upstream_remote(repo, upstream_url)
    if has_upstream:
        print("  git fetch upstream …")
        must_ok(
            run_git(repo, "fetch", "upstream", "--prune", token=token),
            "git fetch upstream",
        )

    checkout_branch(repo, branch)

    if has_upstream:
        print(f"  合并 upstream/{branch} …")
        cp = run_git(
            repo,
            "merge",
            "--no-edit",
            f"upstream/{branch}",
            token=None,
        )
        if cp.returncode != 0:
            print(cp.stderr or cp.stdout, file=sys.stderr)
            sys.exit(1)

    print(f"  合并 origin/{branch} …")
    cp = run_git(repo, "merge", "--no-edit", f"origin/{branch}", token=None)
    if cp.returncode != 0:
        print(cp.stderr or cp.stdout, file=sys.stderr)
        sys.exit(1)

    short = run_git(repo, "rev-parse", "--short", "HEAD", token=None)
    print(f"  当前 HEAD: {(short.stdout or '').strip()}")
    print()


def resolve_branch(cfg: dict[str, Any]) -> str:
    raw = cfg.get("git_branch", cfg.get("branch"))
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return "main"


def resolve_targets(cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    prod_root, test_root, prod_gh, test_gh = resolve_roots(cfg)
    prod = cfg.get("prod")
    test = cfg.get("test")
    assert isinstance(prod, dict) and isinstance(test, dict)

    def up(sec: dict[str, Any]) -> Optional[str]:
        u = sec.get("upstream")
        if isinstance(u, str) and u.strip():
            return u.strip()
        return None

    branch = resolve_branch(cfg)
    targets = [
        {
            "name": "prod",
            "path": prod_root,
            "github": prod_gh,
            "upstream": up(prod),
        },
        {
            "name": "test",
            "path": test_root,
            "github": test_gh,
            "upstream": up(test),
        },
    ]
    return targets, branch


def sync_from_config(
    cfg: dict[str, Any],
    _config_path: Path,
    *,
    branch: Optional[str] = None,
    token: Optional[str] = None,
    allow_dirty: bool = False,
) -> None:
    targets, cfg_branch = resolve_targets(cfg)
    b = branch or cfg_branch
    print(f"目标分支: {b}")
    if token:
        print("已检测到 GitHub token（来自环境变量或文件），将用于 git fetch。")
    else:
        print(
            "未设置 GITHUB_TOKEN / GH_TOKEN / GITHUB_TOKEN_FILE；"
            "若仓库为私有，fetch 可能失败。",
        )
    print()
    for t in targets:
        sync_one_repo(
            t["name"],
            t["path"],
            branch=b,
            upstream_url=t.get("upstream"),
            token=token,
            allow_dirty=allow_dirty,
        )
    print("同步完成。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="将 prod/test 本地仓库置于指定分支，并与 upstream（若存在）及 origin 合并",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"配置文件路径（默认: {DEFAULT_CONFIG_PATH}）",
    )
    parser.add_argument(
        "--branch",
        default=None,
        help="覆盖配置中的 git_branch（默认从 config 读取，否则 main）",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="工作区有未提交更改时仍继续（不推荐）",
    )
    parser.add_argument(
        "--token-env",
        default=None,
        metavar="NAME",
        help="仅从该环境变量读取 token（仍回退到 GITHUB_TOKEN_FILE）",
    )
    args = parser.parse_args()

    cfg_path = args.config
    if not cfg_path.is_absolute():
        cfg_path = (Path.cwd() / cfg_path).resolve()
    cfg = load_config(cfg_path)
    token = resolve_github_token(args.token_env)
    sync_from_config(
        cfg,
        cfg_path,
        branch=args.branch,
        token=token,
        allow_dirty=args.allow_dirty,
    )


if __name__ == "__main__":
    main()
