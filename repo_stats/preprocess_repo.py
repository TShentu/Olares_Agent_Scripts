#!/usr/bin/env python3
"""
Preprocess an Olares app repository: walk each app's OlaresManifest.yaml git history
and emit structured availability data per OS version.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from repo_lib import (
    APPDATA_DIRNAME,
    DEFAULT_CONFIG_PATH,
    analyze_app_history,
    apply_visibility_blacklist,
    app_record_to_dict,
    get_app_status,
    get_repo_head_info,
    list_app_dirs,
    load_config,
    resolve_filters,
    resolve_os_versions,
    resolve_repos,
)

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"


def _cleanup_legacy_app_files(repo_output: Path) -> None:
    """Remove per-app yaml files mistakenly written at repo output root."""
    for path in repo_output.glob("*.yaml"):
        if path.name == "all_apps.yaml":
            continue
        path.unlink()


def preprocess_repo(
    repo_name: str,
    repo_root: Path,
    remote_url: str,
    git_branch: str,
    os_versions: list[str],
    output_dir: Path,
    blacklist: list[dict[str, str]] | None = None,
) -> Path:
    if not repo_root.is_dir():
        print(f"错误: 本地仓库目录不存在: {repo_root}", file=sys.stderr)
        sys.exit(1)
    if not (repo_root / ".git").is_dir():
        print(f"错误: 路径不是 git 仓库: {repo_root}", file=sys.stderr)
        sys.exit(1)

    repo_output = output_dir / repo_name
    appdata_dir = repo_output / APPDATA_DIRNAME
    repo_output.mkdir(parents=True, exist_ok=True)
    appdata_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_legacy_app_files(repo_output)

    head = get_repo_head_info(repo_root)
    apps = list_app_dirs(repo_root)
    records: list[dict[str, Any]] = []
    rules = blacklist or []

    total = len(apps)
    for idx, app_name in enumerate(apps, start=1):
        status = get_app_status(repo_root / app_name)
        os_infos = analyze_app_history(repo_root, app_name, os_versions)
        os_infos = apply_visibility_blacklist(os_infos, app_name, rules)
        record = app_record_to_dict(app_name, status, os_infos)
        records.append(record)

        app_file = appdata_dir / f"{app_name}.yaml"
        with open(app_file, "w", encoding="utf-8") as f:
            yaml.dump(
                record,
                f,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )

        if idx % 25 == 0 or idx == total:
            print(f"  [{repo_name}] 已处理 {idx}/{total} 个应用", flush=True)

    combined_path = repo_output / "all_apps.yaml"
    combined: dict[str, Any] = {
        "repo": repo_name,
        "remote_url": remote_url,
        "git_branch": git_branch,
        "commit_sha": head["commit_sha"],
        "committed_at": head["committed_at"],
        "apps": records,
    }
    with open(combined_path, "w", encoding="utf-8") as f:
        yaml.dump(
            combined,
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )

    print(f"  [{repo_name}] 结构化数据已写入: {repo_output}")
    return repo_output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="预处理 app 仓库，生成各应用在不同 OS 版本下的可用性结构化文件。"
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="配置文件路径（默认: config.yaml）",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="输出目录（默认: repo_stats/output）",
    )
    parser.add_argument(
        "--repo",
        action="append",
        dest="repo_names",
        metavar="NAME",
        help="仅处理指定名称的 repo（可多次指定）；默认处理配置中全部 repo",
    )
    args = parser.parse_args()

    config_path = args.config
    if not config_path.is_absolute():
        config_path = (Path.cwd() / config_path).resolve()

    cfg = load_config(config_path)
    repos = resolve_repos(cfg)
    os_versions = resolve_os_versions(cfg)
    repo_names = {r["name"] for r in repos}
    filters_by_repo = resolve_filters(cfg, repo_names)
    output_dir = args.output
    if not output_dir.is_absolute():
        output_dir = (Path.cwd() / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    selected = set(args.repo_names) if args.repo_names else None
    if selected:
        unknown = selected - {r["name"] for r in repos}
        if unknown:
            print(f"错误: 配置中未找到 repo: {', '.join(sorted(unknown))}", file=sys.stderr)
            sys.exit(1)

    print(f"OS 版本: {', '.join(os_versions)}")
    print(f"输出目录: {output_dir}")

    for repo in repos:
        if selected and repo["name"] not in selected:
            continue
        print(f"处理仓库: {repo['name']} ({repo['local_path']})")
        bl = filters_by_repo.get(repo["name"], [])
        if bl:
            print(f"  [{repo['name']}] 应用 visibility 屏蔽规则: {len(bl)} 条")
        preprocess_repo(
            repo["name"],
            Path(repo["local_path"]),
            repo["remote_url"],
            repo["git_branch"],
            os_versions,
            output_dir,
            blacklist=bl,
        )

    print("预处理完成。")


if __name__ == "__main__":
    main()
