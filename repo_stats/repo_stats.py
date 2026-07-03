#!/usr/bin/env python3
"""
Aggregate structured app availability files and produce per-OS-version /
per-architecture counts of effective (visible) apps.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any

import yaml

from repo_lib import DEFAULT_CONFIG_PATH, is_effective_app, load_config, resolve_repos

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "output"
KNOWN_ARCHES = ("amd64", "arm64")


def load_structured_data(structured_path: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    if not structured_path.is_file():
        print(f"错误: 结构化文件不存在: {structured_path}", file=sys.stderr)
        sys.exit(1)
    with open(structured_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    meta = {
        "commit_sha": "",
        "committed_at": "",
        "remote_url": "",
        "git_branch": "",
        "repo": "",
    }
    apps: list[dict[str, Any]]

    if isinstance(data, dict) and "apps" in data:
        apps = data["apps"]
        for key in meta:
            val = data.get(key)
            if isinstance(val, str):
                meta[key] = val
        # backward compat for older all_apps.yaml
        if not meta["remote_url"] and isinstance(data.get("github"), str):
            meta["remote_url"] = data["github"]
    elif isinstance(data, list):
        apps = data
    elif isinstance(data, dict) and "appname" in data:
        apps = [data]
    else:
        print(f"错误: 无法解析结构化文件: {structured_path}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(apps, list):
        print(f"错误: apps 字段格式无效: {structured_path}", file=sys.stderr)
        sys.exit(1)
    return apps, meta


def collect_os_versions(apps: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for app in apps:
        for entry in app.get("os_versions") or []:
            os_ver = entry.get("os_version")
            if isinstance(os_ver, str) and os_ver not in seen:
                seen.append(os_ver)
    return seen


def compute_stats(
    apps: list[dict[str, Any]],
    os_versions: list[str],
    *,
    status_filter: str = "normal",
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {
        arch: {os_ver: 0 for os_ver in os_versions} for arch in KNOWN_ARCHES
    }

    for app in apps:
        if status_filter and app.get("status") != status_filter:
            continue
        os_by_version = {
            entry.get("os_version"): entry
            for entry in (app.get("os_versions") or [])
            if isinstance(entry, dict)
        }
        for os_ver in os_versions:
            entry = os_by_version.get(os_ver)
            if not entry:
                continue
            for arch in KNOWN_ARCHES:
                if is_effective_app(entry, arch):
                    counts[arch][os_ver] += 1
    return counts


def stats_to_markdown(
    counts: dict[str, dict[str, int]],
    os_versions: list[str],
    repo_name: str,
    meta: dict[str, str],
) -> str:
    header = "| os_version | " + " | ".join(os_versions) + " |"
    sep = "| --- | " + " | ".join(["---"] * len(os_versions)) + " |"
    lines = [
        f"# {repo_name} 有效应用统计",
        "",
        "统计条件: status=normal, visibility=true, chart_version 非 empty, 且 supportarch 包含对应架构。",
        "",
    ]
    sha = meta.get("commit_sha", "")
    committed_at = meta.get("committed_at", "")
    remote_url = meta.get("remote_url", "")
    git_branch = meta.get("git_branch", "")
    if sha:
        ref = f"`{sha[:12]}`" if len(sha) > 12 else f"`{sha}`"
        ts = f"（{committed_at}）" if committed_at else ""
        lines.append(f"基于 commit: {ref}{ts}")
        if git_branch:
            lines.append(f"分支: {git_branch}")
        if remote_url:
            lines.append(
                f"仓库: {remote_url}/tree/{sha}" if sha else f"仓库: {remote_url}"
            )
        lines.append("")
    lines.extend([header, sep])
    for arch in KNOWN_ARCHES:
        row_vals = [str(counts[arch].get(os_ver, 0)) for os_ver in os_versions]
        lines.append("| " + arch + " | " + " | ".join(row_vals) + " |")
    lines.append("")
    return "\n".join(lines)


def write_stats_csv(
    path: Path,
    counts: dict[str, dict[str, int]],
    os_versions: list[str],
    meta: dict[str, str],
) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        sha = meta.get("commit_sha", "")
        committed_at = meta.get("committed_at", "")
        remote_url = meta.get("remote_url", "")
        git_branch = meta.get("git_branch", "")
        if sha:
            writer.writerow(["commit_sha", sha])
        if committed_at:
            writer.writerow(["committed_at", committed_at])
        if remote_url:
            writer.writerow(["remote_url", remote_url])
        if git_branch:
            writer.writerow(["git_branch", git_branch])
        if sha or committed_at or remote_url or git_branch:
            writer.writerow([])
        writer.writerow(["os_version", *os_versions])
        for arch in KNOWN_ARCHES:
            writer.writerow([arch, *[counts[arch].get(v, 0) for v in os_versions]])


def run_stats_for_repo(
    repo_name: str,
    input_dir: Path,
    output_dir: Path,
    os_versions: list[str] | None,
) -> None:
    structured_path = input_dir / repo_name / "all_apps.yaml"
    apps, meta = load_structured_data(structured_path)

    versions = os_versions or collect_os_versions(apps)
    if not versions:
        print(f"错误: [{repo_name}] 未找到任何 os_version 数据", file=sys.stderr)
        sys.exit(1)

    counts = compute_stats(apps, versions)
    repo_out = output_dir / repo_name
    repo_out.mkdir(parents=True, exist_ok=True)

    md_path = repo_out / "stats_table.md"
    csv_path = repo_out / "stats_table.csv"
    md_content = stats_to_markdown(counts, versions, repo_name, meta)
    md_path.write_text(md_content, encoding="utf-8")
    write_stats_csv(csv_path, counts, versions, meta)

    print(f"  [{repo_name}] 统计表格: {md_path}")
    print(f"  [{repo_name}] CSV: {csv_path}")
    if meta.get("commit_sha"):
        print(f"  [{repo_name}] 基于 commit: {meta['commit_sha']}")
    print()
    print(md_content)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从结构化可用性文件统计各 OS 版本、各架构下的有效应用数量。",
        epilog="不传任何参数时，默认统计 config.yaml 中 repos 列表里的全部仓库。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="配置文件路径（默认: config.yaml）",
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="结构化文件根目录（默认: repo_stats/output）",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="统计结果输出目录（默认与 --input 相同）",
    )
    parser.add_argument(
        "--repo",
        action="append",
        dest="repo_names",
        metavar="NAME",
        help="仅统计指定名称的 repo（可多次指定）；默认处理配置中全部 repo",
    )
    args = parser.parse_args()

    config_path = args.config
    if not config_path.is_absolute():
        config_path = (Path.cwd() / config_path).resolve()

    cfg = load_config(config_path)
    repos = resolve_repos(cfg)
    os_versions_cfg = cfg.get("os_versions")
    os_versions: list[str] | None = None
    if isinstance(os_versions_cfg, list):
        os_versions = [str(v).strip() for v in os_versions_cfg if str(v).strip()]

    input_dir = args.input
    if not input_dir.is_absolute():
        input_dir = (Path.cwd() / input_dir).resolve()
    output_dir = args.output or input_dir
    if not output_dir.is_absolute():
        output_dir = (Path.cwd() / output_dir).resolve()

    selected = set(args.repo_names) if args.repo_names else None
    if selected:
        unknown = selected - {r["name"] for r in repos}
        if unknown:
            print(f"错误: 配置中未找到 repo: {', '.join(sorted(unknown))}", file=sys.stderr)
            sys.exit(1)

    to_run = [r for r in repos if not selected or r["name"] in selected]
    if selected:
        print(f"指定 repo: {', '.join(r['name'] for r in to_run)}")
    else:
        print(f"默认统计全部 repo: {', '.join(r['name'] for r in to_run)}")

    for repo in to_run:
        print(f"统计仓库: {repo['name']}")
        run_stats_for_repo(repo["name"], input_dir, output_dir, os_versions)


if __name__ == "__main__":
    main()
