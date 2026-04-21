#!/usr/bin/env python3
"""
将 test 仓库中的应用目录同步到 prod 仓库，并向 prod 的 upstream 提交草稿 PR。

流程（默认始终执行）：先对配置中的 prod / test 本地克隆调用 ``git_sync.sync_from_config``，
即 fetch origin、fetch upstream（若已配置）、合并 upstream 与 origin 到当前分支，使 fork
与 upstream 对齐；再从 test 的 local_path 复制指定 chart 目录到 prod 的 local_path；
在 prod 克隆上建分支、提交、推送，并对 upstream 仓库创建草稿 PR。
仅当显式传入 ``--skip-sync`` 时跳过上述 fork 同步，直接使用本地目录当前内容进行复制与提交
（用于已手动对齐仓库等场景）。
批量模式：``--batch charts.txt``，文件中每行一个 chart 名，``#`` 开头为注释。

认证：凭证键与 ``git_sync.parse_github_credentials_file`` 一致——
``GITHUB_TOKEN`` / ``GH_TOKEN``，``GITHUB_USERNAME`` / ``GH_USERNAME``，
``GITHUB_EMAIL`` / ``GH_EMAIL``（``GITHUB_*`` 优先于 ``GH_*``）。
环境变量与 ``--token-source file`` 文件用法相同；env 模式下另支持 ``GITHUB_TOKEN_FILE``、
``--token-env``。
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

from repo_config import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    load_config,
    resolve_roots,
)
from git_sync import (  # noqa: E402
    parse_github_credentials_file,
    resolve_github_identity_from_env,
    resolve_github_token,
    run_git,
    sync_from_config,
    verify_github_token,
)
from pat_url import github_authenticated_https_url  # noqa: E402

_GITHUB_API_ACCEPT = "application/vnd.github.v3+json"


def _must_ok(cp: subprocess.CompletedProcess[str], what: str) -> None:
    if cp.returncode != 0:
        print(f"错误: {what}", file=sys.stderr)
        if cp.stdout:
            print(cp.stdout, file=sys.stderr)
        if cp.stderr:
            print(cp.stderr, file=sys.stderr)
        sys.exit(1)


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
    cp = run_git(prod_repo, "ls-tree", "-d", "--name-only", ref, chart_name, token=None)
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
    cp = run_git(repo, "status", "--porcelain", token=None)
    return bool((cp.stdout or "").strip())


def ensure_commit_identity(
    repo: Path,
    *,
    fallback_name: Optional[str] = None,
    fallback_email: Optional[str] = None,
) -> None:
    """Use local repo config if set; else GIT_AUTHOR_*; else credentials-file fallbacks."""
    name_cp = run_git(repo, "config", "user.name", token=None)
    email_cp = run_git(repo, "config", "user.email", token=None)
    if (name_cp.stdout or "").strip() and (email_cp.stdout or "").strip():
        return
    name = os.environ.get("GIT_AUTHOR_NAME", "").strip()
    email = os.environ.get("GIT_AUTHOR_EMAIL", "").strip()
    if name and email:
        _must_ok(
            run_git(repo, "config", "user.name", name, token=None),
            "git config user.name",
        )
        _must_ok(
            run_git(repo, "config", "user.email", email, token=None),
            "git config user.email",
        )
        return
    fn = (fallback_name or "").strip()
    fe = (fallback_email or "").strip()
    if fn and fe:
        _must_ok(
            run_git(repo, "config", "user.name", fn, token=None),
            "git config user.name",
        )
        _must_ok(
            run_git(repo, "config", "user.email", fe, token=None),
            "git config user.email",
        )
        return
    print(
        "错误: 请在 prod 仓库中配置 git user.name / user.email，"
        "或设置 GIT_AUTHOR_NAME / GIT_AUTHOR_EMAIL，"
        "或在凭证文件 / 环境中提供 GITHUB_USERNAME 或 GH_USERNAME，以及 "
        "GITHUB_EMAIL 或 GH_EMAIL。",
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
        url = github_authenticated_https_url(
            f"https://github.com/{fork_owner}/{fork_repo}.git",
            token,
            style="oauth2",
        )
        if not url:
            print("错误: 无法构建带 PAT 的 push URL（需为 github.com HTTPS）", file=sys.stderr)
            return False
        cp = run_git(prod_repo, "push", url, branch, token=None)
    else:
        cp = run_git(prod_repo, "push", "origin", branch, token=None)
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
    commit_author_name: Optional[str] = None,
    commit_author_email: Optional[str] = None,
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
    _must_ok(
        run_git(prod_repo, "switch", branch, token=None),
        f"git switch {branch}",
    )

    copy_chart_tree(test_folder, prod_root / chart_name)

    if not has_git_changes(prod_repo):
        print(f"  {chart_name}: 与当前 prod 无差异，跳过 PR。")
        return True, None, None

    pr_type = get_pr_type(test_folder, prod_repo, branch, chart_name)
    version = get_folder_version(test_folder)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    sync_branch = f"sync-{chart_name}-{ts}"

    _must_ok(
        run_git(prod_repo, "switch", "-c", sync_branch, token=None),
        f"创建分支 {sync_branch}",
    )

    ensure_commit_identity(
        prod_repo,
        fallback_name=commit_author_name,
        fallback_email=commit_author_email,
    )
    title_core = f"[{pr_type}][{chart_name}][{version}]"
    if optional_title and optional_title.strip():
        commit_title = f"{title_core} {optional_title.strip()}"
    else:
        commit_title = title_core

    _must_ok(run_git(prod_repo, "add", "--", chart_name, token=None), "git add")
    cp_commit = run_git(prod_repo, "commit", "-m", commit_title, token=None)
    if cp_commit.returncode != 0:
        run_git(prod_repo, "switch", branch, token=None)
        return False, None, "git commit 失败"

    if not token:
        print(
            "错误: 未配置 GitHub token（env：GITHUB_TOKEN/GH_TOKEN 或 GITHUB_TOKEN_FILE；"
            "或 --token-source file --token-file PATH），无法推送或创建 PR。",
            file=sys.stderr,
        )
        run_git(prod_repo, "switch", branch, token=None)
        return False, None, "缺少 token"

    if not push_branch_fork(
        prod_repo,
        sync_branch,
        token=token,
        fork_owner=fork_owner,
        fork_repo=fork_repo,
    ):
        run_git(prod_repo, "switch", branch, token=None)
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
        run_git(prod_repo, "switch", branch, token=None)
        return False, None, f"创建 PR 失败: {e}"

    _must_ok(
        run_git(prod_repo, "switch", branch, token=None),
        f"回到 {branch}",
    )
    print(f"  {chart_name}: PR {url}")
    return True, url, None


def _git_toplevel(start: Path) -> Path:
    p = start.resolve()
    if p.is_file():
        p = p.parent
    cp = run_git(p, "rev-parse", "--show-toplevel", token=None)
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
        help="配置文件路径（默认: 脚本所在目录下的 config.yaml）",
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
        "--token-source",
        choices=("env", "file"),
        default="env",
        metavar="MODE",
        help="token 来源：env（默认，环境变量）或 file（需配合 --token-file）",
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=None,
        metavar="PATH",
        help="--token-source file 时读取凭证文件（GITHUB_* 与 GH_* 两套键名，语义与 env 一致）",
    )
    parser.add_argument(
        "--token-env",
        default=None,
        metavar="NAME",
        help="仅在 --token-source env 时生效：优先从该环境变量读取，其次 GITHUB_TOKEN / GH_TOKEN，"
        "仍可使用环境变量 GITHUB_TOKEN_FILE 指向文件",
    )
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        dest="skip_sync",
        help="跳过 fork 同步（不 fetch/合并 upstream 与 origin），直接使用本地 prod/test 数据提交",
    )
    args = parser.parse_args()

    if args.token_source == "file":
        if args.token_file is None:
            parser.error("--token-source file 需要 --token-file PATH")
    elif args.token_file is not None:
        parser.error("使用 --token-file 时请指定 --token-source file")

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

    commit_author_name: Optional[str] = None
    commit_author_email: Optional[str] = None
    if args.token_source == "file":
        assert args.token_file is not None
        tf = args.token_file
        if not tf.is_absolute():
            tf = (Path.cwd() / tf).resolve()
        if not tf.is_file():
            print(f"错误: token 文件不存在: {tf}", file=sys.stderr)
            sys.exit(1)
        token, commit_author_name, commit_author_email = parse_github_credentials_file(
            tf
        )
        if not token:
            print(
                "错误: 未能从 --token-file 解析出 GITHUB_TOKEN 或 GH_TOKEN。\n"
                "请在文件中至少包含一行：GITHUB_TOKEN=... 或 GH_TOKEN=...（可选用户名/邮箱键同上）。",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        token = resolve_github_token(args.token_env)
        commit_author_name, commit_author_email = resolve_github_identity_from_env()

    if not args.skip_sync:
        if not token:
            print(
                "错误: fork 同步需要有效的 GitHub token。"
                "请设置 GITHUB_TOKEN / GH_TOKEN、GITHUB_TOKEN_FILE，"
                "或使用 --token-source file --token-file ...",
                file=sys.stderr,
            )
            sys.exit(1)
        verify_github_token(token)
        sync_from_config(
            cfg,
            cfg_path,
            branch=args.branch,
            token=token,
            allow_dirty=args.allow_dirty,
        )
        print()
    else:
        print(
            "已跳过 fork 同步（--skip-sync），将使用本地 prod/test 目录当前内容。",
            file=sys.stderr,
        )

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
            commit_author_name=commit_author_name,
            commit_author_email=commit_author_email,
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
