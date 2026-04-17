#!/usr/bin/env python3
"""
将 test 仓库中的应用目录同步到 prod 仓库，并向 prod 的 upstream 提交草稿 PR。

流程：先与 compare_chart 相同地 fetch/合并 upstream 与 origin；再从 test 的 local_path
复制指定 chart 目录到 prod 的 local_path；在 prod 克隆上建分支、提交、推送，并对
upstream 仓库创建 PR（格式参考 Scripts/GithubSync/sync_folders.py）。
批量模式：``--batch charts.txt``，文件中每行一个 chart 名，``#`` 开头为注释。

认证：GITHUB_TOKEN / GH_TOKEN / GITHUB_TOKEN_FILE（与 compare_chart/sync_repos 一致）。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import requests
import yaml

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_COMPARE_CHART = _REPO_ROOT / "compare_chart"
if str(_COMPARE_CHART) not in sys.path:
    sys.path.insert(0, str(_COMPARE_CHART))

from compare_chart_versions import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    load_config,
    resolve_roots,
)
from sync_repos import resolve_github_token, sync_from_config  # noqa: E402

_GITHUB_API_ACCEPT = "application/vnd.github.v3+json"


def _must_ok(cp: subprocess.CompletedProcess[str], what: str) -> None:
    if cp.returncode != 0:
        print(f"错误: {what}", file=sys.stderr)
        if cp.stdout:
            print(cp.stdout, file=sys.stderr)
        if cp.stderr:
            print(cp.stderr, file=sys.stderr)
        sys.exit(1)


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )


def parse_github_owner_repo(url: str) -> tuple[str, str]:
    """Parse owner and repo name from a GitHub https URL."""
    s = url.strip().rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    m = re.match(r"^https?://github\.com/([^/]+)/([^/]+)/?$", s, re.I)
    if not m:
        print(f"错误: 无法解析 GitHub 仓库 URL: {url}", file=sys.stderr)
        sys.exit(1)
    return m.group(1), m.group(2)


def resolve_branch(cfg: dict[str, Any]) -> str:
    raw = cfg.get("git_branch", cfg.get("branch"))
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return "main"


def load_chart_list_file(path: Path) -> list[str]:
    """Load chart directory names: one per line; ``#`` starts a comment; empty lines skipped."""
    if not path.is_file():
        print(f"错误: chart 列表文件不存在: {path}", file=sys.stderr)
        sys.exit(1)
    names: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"错误: 无法读取 {path}: {e}", file=sys.stderr)
        sys.exit(1)
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.append(line)
    return names


def get_folder_version(folder: Path) -> str:
    chart_yaml = folder / "Chart.yaml"
    if not chart_yaml.is_file():
        return "1.0.0"
    try:
        with open(chart_yaml, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict) and data.get("version") is not None:
            return str(data["version"]).strip().strip("'\"")
    except OSError:
        pass
    return "1.0.0"


def chart_exists_upstream(
    prod_repo: Path, branch: str, chart_name: str
) -> bool:
    ref = f"upstream/{branch}"
    cp = _run_git(prod_repo, "ls-tree", "-d", "--name-only", ref, chart_name)
    return cp.returncode == 0 and bool((cp.stdout or "").strip())


def get_pr_type(
    test_folder: Path, prod_repo: Path, branch: str, chart_name: str
) -> str:
    if (test_folder / ".remove").exists():
        return "REMOVE"
    if (test_folder / ".suspend").exists():
        return "SUSPEND"
    if chart_exists_upstream(prod_repo, branch, chart_name):
        return "UPDATE"
    return "NEW"


def copy_chart_tree(source: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest)


def has_git_changes(repo: Path) -> bool:
    cp = _run_git(repo, "status", "--porcelain")
    return bool((cp.stdout or "").strip())


def ensure_commit_identity(repo: Path) -> None:
    """Use local repo config if set; else optional env GIT_AUTHOR_*."""
    name_cp = _run_git(repo, "config", "user.name")
    email_cp = _run_git(repo, "config", "user.email")
    if (name_cp.stdout or "").strip() and (email_cp.stdout or "").strip():
        return
    name = os.environ.get("GIT_AUTHOR_NAME", "").strip()
    email = os.environ.get("GIT_AUTHOR_EMAIL", "").strip()
    if name and email:
        _must_ok(_run_git(repo, "config", "user.name", name), "git config user.name")
        _must_ok(
            _run_git(repo, "config", "user.email", email), "git config user.email"
        )
        return
    print(
        "错误: 请在 prod 仓库中配置 git user.name / user.email，"
        "或设置环境变量 GIT_AUTHOR_NAME 与 GIT_AUTHOR_EMAIL。",
        file=sys.stderr,
    )
    sys.exit(1)


def push_branch_fork(
    prod_repo: Path,
    branch: str,
    *,
    token: Optional[str],
    fork_owner: str,
    fork_repo: str,
) -> bool:
    if token:
        url = f"https://{token}@github.com/{fork_owner}/{fork_repo}.git"
        cp = _run_git(prod_repo, "push", url, branch)
    else:
        cp = _run_git(prod_repo, "push", "origin", branch)
    if cp.returncode != 0:
        if cp.stderr:
            print(cp.stderr, file=sys.stderr)
        if cp.stdout:
            print(cp.stdout, file=sys.stderr)
        return False
    return True


def create_pull_request(
    *,
    token: str,
    upstream_owner: str,
    upstream_repo: str,
    base_branch: str,
    head_owner: str,
    head_branch: str,
    title: str,
    body: str,
) -> str:
    url = f"https://api.github.com/repos/{upstream_owner}/{upstream_repo}/pulls"
    headers = {
        "Authorization": f"token {token}",
        "Accept": _GITHUB_API_ACCEPT,
    }
    data = {
        "title": title,
        "body": body,
        "head": f"{head_owner}:{head_branch}",
        "base": base_branch,
        "draft": True,
    }
    r = requests.post(url, headers=headers, json=data, timeout=120)
    if r.status_code >= 400:
        print(r.text, file=sys.stderr)
        r.raise_for_status()
    pr = r.json()
    return str(pr.get("html_url", ""))


def build_pr_body(chart_name: str) -> str:
    return (
        f"### App Title\n{chart_name}\n\n### Description\n\n"
        "### Statement\n"
        "- [x] I have tested this application to ensure it is compatible with the "
        "Olares OS version stated in the `OlaresManifest.yaml`"
    )


def sync_one_chart(
    *,
    cfg: dict[str, Any],
    branch: str,
    chart_name: str,
    token: Optional[str],
    optional_title: Optional[str],
    allow_dirty: bool,
) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Returns (success, pr_url_or_none_if_skipped, error_message).
    """
    prod_root, test_root, prod_gh, _test_gh = resolve_roots(cfg)
    prod = cfg.get("prod")
    assert isinstance(prod, dict)
    upstream_url = prod.get("upstream")
    if not isinstance(upstream_url, str) or not upstream_url.strip():
        return False, None, "prod.upstream 未配置，无法向 upstream 开 PR"

    prod_repo = _git_toplevel(prod_root)

    if not allow_dirty and has_git_changes(prod_repo):
        return False, None, "prod 工作区已有未提交更改，请先处理或使用 --allow-dirty"

    test_folder = test_root / chart_name
    if not test_folder.is_dir():
        return False, None, f"test 侧不存在目录: {test_folder}"

    up_owner, up_repo = parse_github_owner_repo(upstream_url)
    fork_owner, fork_repo = parse_github_owner_repo(prod_gh)

    # Ensure on configured branch before copy (sync_from_config already checked out)
    _must_ok(_run_git(prod_repo, "switch", branch), f"git switch {branch}")

    copy_chart_tree(test_folder, prod_root / chart_name)

    if not has_git_changes(prod_repo):
        print(f"  {chart_name}: 与当前 prod 无差异，跳过 PR。")
        return True, None, None

    pr_type = get_pr_type(test_folder, prod_repo, branch, chart_name)
    version = get_folder_version(test_folder)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    sync_branch = f"sync-{chart_name}-{ts}"

    _must_ok(
        _run_git(prod_repo, "switch", "-c", sync_branch),
        f"创建分支 {sync_branch}",
    )

    ensure_commit_identity(prod_repo)
    title_core = f"[{pr_type}][{chart_name}][{version}]"
    if optional_title and optional_title.strip():
        commit_title = f"{title_core} {optional_title.strip()}"
    else:
        commit_title = title_core

    _must_ok(_run_git(prod_repo, "add", "--", chart_name), "git add")
    cp_commit = _run_git(prod_repo, "commit", "-m", commit_title)
    if cp_commit.returncode != 0:
        _run_git(prod_repo, "switch", branch)
        return False, None, "git commit 失败"

    if not token:
        print(
            "错误: 未设置 GITHUB_TOKEN / GH_TOKEN / GITHUB_TOKEN_FILE，无法推送或创建 PR。",
            file=sys.stderr,
        )
        _run_git(prod_repo, "switch", branch)
        return False, None, "缺少 token"

    if not push_branch_fork(
        prod_repo,
        sync_branch,
        token=token,
        fork_owner=fork_owner,
        fork_repo=fork_repo,
    ):
        _run_git(prod_repo, "switch", branch)
        return False, None, "git push 失败"

    pr_title = commit_title
    body = build_pr_body(chart_name)
    try:
        url = create_pull_request(
            token=token,
            upstream_owner=up_owner,
            upstream_repo=up_repo,
            base_branch=branch,
            head_owner=fork_owner,
            head_branch=sync_branch,
            title=pr_title,
            body=body,
        )
    except Exception as e:
        _run_git(prod_repo, "switch", branch)
        return False, None, f"创建 PR 失败: {e}"

    _must_ok(_run_git(prod_repo, "switch", branch), f"回到 {branch}")
    print(f"  {chart_name}: PR {url}")
    return True, url, None


def _git_toplevel(start: Path) -> Path:
    p = start.resolve()
    if p.is_file():
        p = p.parent
    cp = _run_git(p, "rev-parse", "--show-toplevel")
    if cp.returncode != 0:
        print(f"错误: 不是 git 仓库: {start}", file=sys.stderr)
        sys.exit(1)
    return Path((cp.stdout or "").strip())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从 test 同步 chart 到 prod，并向 prod upstream 提交草稿 PR",
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="配置文件路径（默认: 当前目录下的 config.yaml）",
    )
    parser.add_argument(
        "chart",
        nargs="?",
        default=None,
        metavar="CHART",
        help="单个 chart 目录名（默认模式）",
    )
    parser.add_argument(
        "--batch",
        "-batch",
        type=Path,
        metavar="FILE",
        dest="batch_file",
        help="批量同步：每行一个 chart 目录名的 txt 文件路径",
    )
    parser.add_argument(
        "--title",
        default=None,
        metavar="TEXT",
        help="可选：追加在 PR/提交标题的方括号字段之后",
    )
    parser.add_argument(
        "--branch",
        default=None,
        help="覆盖配置中的 git_branch",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="允许 prod 工作区有未提交更改时仍继续",
    )
    parser.add_argument(
        "--token-env",
        default=None,
        metavar="NAME",
        help="仅从该环境变量读取 token（仍可使用 GITHUB_TOKEN_FILE）",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="跳过 fetch upstream / 合并（仅用于调试；默认会先同步两 fork）",
    )
    args = parser.parse_args()

    if args.batch_file is not None and args.chart is not None:
        parser.error("不能同时指定单个 CHART 与 --batch")

    if args.batch_file is not None:
        batch_path = args.batch_file
        if not batch_path.is_absolute():
            batch_path = (Path.cwd() / batch_path).resolve()
        charts = load_chart_list_file(batch_path)
        if not charts:
            print(f"错误: chart 列表为空: {batch_path}", file=sys.stderr)
            sys.exit(1)
    elif args.chart:
        charts = [args.chart]
    else:
        parser.error("请指定 CHART，或使用 --batch 指向 chart 列表 txt 文件")

    cfg_path = args.config
    if not cfg_path.is_absolute():
        cfg_path = (Path.cwd() / cfg_path).resolve()

    cfg = load_config(cfg_path)
    branch = args.branch or resolve_branch(cfg)

    if not args.skip_preflight:
        token = resolve_github_token(args.token_env)
        sync_from_config(
            cfg,
            cfg_path,
            branch=args.branch,
            token=token,
            allow_dirty=args.allow_dirty,
        )
        print()

    token = resolve_github_token(args.token_env)

    failed: list[tuple[str, str]] = []
    for i, name in enumerate(charts):
        print(f"--- 同步 {name} ({i + 1}/{len(charts)}) ---")
        ok, pr_url, err = sync_one_chart(
            cfg=cfg,
            branch=branch,
            chart_name=name,
            token=token,
            optional_title=args.title,
            allow_dirty=args.allow_dirty,
        )
        if not ok:
            failed.append((name, err or "unknown"))
        if i < len(charts) - 1 and ok and pr_url:
            time.sleep(5)

    if failed:
        print("以下 chart 失败:", file=sys.stderr)
        for n, e in failed:
            print(f"  {n}: {e}", file=sys.stderr)
        sys.exit(1)
    print("全部完成。")


if __name__ == "__main__":
    main()
