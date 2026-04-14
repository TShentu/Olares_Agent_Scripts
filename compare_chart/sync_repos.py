#!/usr/bin/env python3
"""
Ensure prod/test local clones are on the configured branch (default main),
sync forks with upstream when configured, then fast-forward to origin.

Authentication: set GITHUB_TOKEN or GH_TOKEN in the environment, or
GITHUB_TOKEN_FILE pointing to a file whose first line is the raw token.
The caller (CI / wrapper) is responsible for injecting credentials — do not
commit tokens in the repo.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from compare_chart_versions import (
    DEFAULT_CONFIG_PATH,
    load_config,
    resolve_roots,
)

_GITHUB_EXTRAHEADER = "http.https://github.com/.extraheader"


def resolve_github_token(env_var: Optional[str] = None) -> Optional[str]:
    """Return token from env (or file), or None if unset."""
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
        p = Path(path).expanduser()
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    # Allow KEY=value lines for convenience
                    if "=" in line and not line.startswith("http"):
                        _, _, rest = line.partition("=")
                        return _strip_token(rest.strip().strip('"').strip("'"))
                    return _strip_token(line)
    return None


def _strip_token(s: str) -> str:
    s = s.strip().strip('"').strip("'")
    return s


def _git_base(token: Optional[str]) -> list[str]:
    cmd = ["git"]
    if token:
        cmd.extend(
            [
                "-c",
                f"{_GITHUB_EXTRAHEADER}=AUTHORIZATION: bearer {token}",
            ]
        )
    return cmd


def run_git(
    repo: Path,
    *git_args: str,
    token: Optional[str] = None,
) -> subprocess.CompletedProcess:
    cmd = _git_base(token) + ["-C", str(repo), *git_args]
    return subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        check=False,
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
    # try create from origin
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

    # Fetch origin first (fork / own remote)
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
        help="配置文件路径（默认: 当前目录下的 config.yaml）",
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

    cfg = load_config(args.config)
    token = resolve_github_token(args.token_env)
    sync_from_config(
        cfg,
        args.config,
        branch=args.branch,
        token=token,
        allow_dirty=args.allow_dirty,
    )


if __name__ == "__main__":
    main()
