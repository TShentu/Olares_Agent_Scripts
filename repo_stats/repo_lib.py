"""Shared helpers for repo_stats preprocessing and statistics."""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = Path("config.yaml")
MANIFEST_NAME = "OlaresManifest.yaml"
APPDATA_DIRNAME = "appdata"


def get_repo_head_info(repo_root: Path) -> dict[str, str]:
    """Return HEAD commit sha and ISO-8601 timestamp for the local clone."""
    info: dict[str, str] = {"commit_sha": "", "committed_at": ""}
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "log",
                "-1",
                "--format=%H %cI",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return info
    if proc.returncode != 0:
        return info
    line = proc.stdout.strip()
    if not line:
        return info
    parts = line.split(" ", 1)
    info["commit_sha"] = parts[0]
    if len(parts) > 1:
        info["committed_at"] = parts[1]
    return info


def load_config(config_path: Path) -> dict[str, Any]:
    if not config_path.exists():
        print(f"错误: 配置文件不存在: {config_path}", file=sys.stderr)
        print("请复制 config.yaml.template 为 config.yaml 并填写路径。", file=sys.stderr)
        sys.exit(1)
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        print(f"错误: 配置文件格式无效: {config_path}", file=sys.stderr)
        sys.exit(1)
    return data


def resolve_repos(cfg: dict[str, Any]) -> list[dict[str, str]]:
    repos = cfg.get("repos")
    if not isinstance(repos, list) or not repos:
        print("错误: 配置需包含 repos 列表，且至少有一项。", file=sys.stderr)
        sys.exit(1)
    resolved: list[dict[str, str]] = []
    seen: set[str] = set()
    for i, item in enumerate(repos):
        if not isinstance(item, dict):
            print(f"错误: repos[{i}] 必须是映射。", file=sys.stderr)
            sys.exit(1)
        name = item.get("name")
        local_path = item.get("local_path")
        remote_url = item.get("remote_url")
        git_branch = item.get("git_branch", "main")
        if not isinstance(name, str) or not name.strip():
            print(f"错误: repos[{i}] 缺少有效字段 name", file=sys.stderr)
            sys.exit(1)
        if not isinstance(local_path, str) or not local_path.strip():
            print(f"错误: repos[{i}] 缺少有效字段 local_path", file=sys.stderr)
            sys.exit(1)
        if not isinstance(remote_url, str) or not remote_url.strip():
            print(f"错误: repos[{i}] 缺少有效字段 remote_url", file=sys.stderr)
            sys.exit(1)
        if not isinstance(git_branch, str) or not git_branch.strip():
            print(f"错误: repos[{i}] 缺少有效字段 git_branch", file=sys.stderr)
            sys.exit(1)
        name = name.strip()
        if name in seen:
            print(f"错误: repos 中存在重复名称: {name}", file=sys.stderr)
            sys.exit(1)
        seen.add(name)
        resolved.append(
            {
                "name": name,
                "local_path": str(Path(local_path).expanduser().resolve()),
                "remote_url": remote_url.strip(),
                "git_branch": git_branch.strip(),
            }
        )
    return resolved


def resolve_filters(
    cfg: dict[str, Any], repo_names: set[str]
) -> dict[str, list[dict[str, str]]]:
    """Parse per-repo visibility blacklist from config ``filters`` section."""
    raw = cfg.get("filters")
    if raw is None:
        return {}
    if not isinstance(raw, list):
        print("错误: filters 必须是列表。", file=sys.stderr)
        sys.exit(1)

    result: dict[str, list[dict[str, str]]] = {}
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            print(f"错误: filters[{i}] 必须是映射。", file=sys.stderr)
            sys.exit(1)
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            print(f"错误: filters[{i}] 缺少有效字段 name", file=sys.stderr)
            sys.exit(1)
        name = name.strip()
        if name not in repo_names:
            print(
                f"错误: filters[{i}] 的 name {name!r} 未在 repos 中定义。",
                file=sys.stderr,
            )
            sys.exit(1)

        # accept both spellings; config template uses blacklist
        bl = item.get("blacklist")
        if bl is None:
            bl = item.get("backlist")
        if bl is None:
            continue
        if not isinstance(bl, list):
            print(f"错误: filters[{i}].blacklist 必须是列表。", file=sys.stderr)
            sys.exit(1)

        entries: list[dict[str, str]] = []
        for j, rule in enumerate(bl):
            if not isinstance(rule, dict):
                print(f"错误: filters[{i}].blacklist[{j}] 必须是映射。", file=sys.stderr)
                sys.exit(1)
            app_name = rule.get("appName")
            version_constraint = rule.get("versionConstraint")
            if not isinstance(app_name, str) or not app_name.strip():
                print(
                    f"错误: filters[{i}].blacklist[{j}] 缺少有效字段 appName",
                    file=sys.stderr,
                )
                sys.exit(1)
            if not isinstance(version_constraint, str) or not version_constraint.strip():
                print(
                    f"错误: filters[{i}].blacklist[{j}] 缺少有效字段 versionConstraint",
                    file=sys.stderr,
                )
                sys.exit(1)
            entries.append(
                {
                    "appName": app_name.strip(),
                    "versionConstraint": version_constraint.strip(),
                }
            )
        if entries:
            result.setdefault(name, []).extend(entries)
    return result


def resolve_os_versions(cfg: dict[str, Any]) -> list[str]:
    versions = cfg.get("os_versions")
    if not isinstance(versions, list) or not versions:
        return ["1.12.3", "1.12.4", "1.12.5", "1.12.6"]
    out: list[str] = []
    for v in versions:
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    if not out:
        print("错误: os_versions 不能为空。", file=sys.stderr)
        sys.exit(1)
    return out


def get_app_status(app_dir: Path) -> str:
    if not app_dir.is_dir():
        return "remove"
    if (app_dir / ".remove").exists():
        return "remove"
    if (app_dir / ".suspend").exists():
        return "suspend"
    return "normal"


def list_app_dirs(repo_root: Path) -> list[str]:
    if not repo_root.is_dir():
        return []
    names: list[str] = []
    for child in sorted(repo_root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue
        if (
            (child / MANIFEST_NAME).exists()
            or (child / "Chart.yaml").exists()
            or (child / ".remove").exists()
        ):
            names.append(child.name)
    return names


def _normalize_os_version(os_version: str) -> str:
    parts = os_version.split(".")
    if len(parts) == 3 and "-" not in os_version:
        return f"{os_version}-0"
    return os_version


def os_version_satisfies_constraint(os_version: str, constraint: Optional[str]) -> bool:
    if not constraint or not isinstance(constraint, str):
        return False
    cleaned = constraint.strip().strip("'\"")
    if not cleaned or "{{" in cleaned:
        return False
    try:
        return Version(_normalize_os_version(os_version)) in SpecifierSet(
            cleaned.replace(" ", "")
        )
    except (InvalidVersion, ValueError):
        return False


_OLARES_DEP_RE = re.compile(
    r"name:\s*olares\s*\n(?:\s+type:\s*\S+\s*\n)?\s+version:\s*['\"]?([^'\"}\n]+)",
    re.IGNORECASE,
)


def _extract_olares_constraint_regex(text: str) -> Optional[str]:
    match = _OLARES_DEP_RE.search(text)
    if match:
        return match.group(1).strip()
    return None


def parse_manifest_text(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "chart_version": None,
        "olares_constraint": None,
        "support_arch": [],
    }
    if not text or not text.strip():
        return result

    data: Any = None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        data = None

    if isinstance(data, dict):
        metadata = data.get("metadata")
        if isinstance(metadata, dict):
            version = metadata.get("version")
            if version is not None:
                result["chart_version"] = str(version).strip().strip("'\"")

        spec = data.get("spec")
        if isinstance(spec, dict):
            arch = spec.get("supportArch")
            if isinstance(arch, list):
                result["support_arch"] = [
                    str(a).strip() for a in arch if a is not None and str(a).strip()
                ]

        options = data.get("options")
        if isinstance(options, dict):
            deps = options.get("dependencies")
            if isinstance(deps, list):
                for dep in deps:
                    if (
                        isinstance(dep, dict)
                        and str(dep.get("name", "")).lower() == "olares"
                    ):
                        version = dep.get("version")
                        if version is not None:
                            result["olares_constraint"] = str(version).strip().strip(
                                "'\""
                            )
                        break

    if result["olares_constraint"] is None:
        result["olares_constraint"] = _extract_olares_constraint_regex(text)

    if not result["support_arch"]:
        arch_match = re.search(
            r"supportArch:\s*\n((?:\s*-\s*\S+\s*\n)+)", text, re.IGNORECASE
        )
        if arch_match:
            result["support_arch"] = re.findall(
                r"-\s*(\S+)", arch_match.group(1)
            )

    if result["chart_version"] is None:
        ver_match = re.search(
            r"metadata:\s*\n(?:\s+\S+.*\n)*?\s+version:\s*['\"]?([^'\"}\n]+)",
            text,
        )
        if ver_match:
            result["chart_version"] = ver_match.group(1).strip().strip("'\"")

    return result


def git_show_file(repo_root: Path, commit: str, rel_path: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "show", f"{commit}:{rel_path}"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def git_manifest_commits(repo_root: Path, app_name: str) -> list[str]:
    rel = f"{app_name}/{MANIFEST_NAME}"
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo_root),
                "log",
                "--format=%H",
                "--reverse",
                "--",
                rel,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return []
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


@dataclass
class OsVersionInfo:
    os_version: str
    visibility: bool = False
    chart_version: str = "empty"
    support_arch: list[str] = field(default_factory=list)


def analyze_app_history(
    repo_root: Path,
    app_name: str,
    os_versions: list[str],
) -> list[OsVersionInfo]:
    rel = f"{app_name}/{MANIFEST_NAME}"
    commits = git_manifest_commits(repo_root, app_name)
    last_match: dict[str, dict[str, Any]] = {}

    for commit in commits:
        text = git_show_file(repo_root, commit, rel)
        if text is None:
            continue
        parsed = parse_manifest_text(text)
        constraint = parsed.get("olares_constraint")
        chart_version = parsed.get("chart_version")
        support_arch = parsed.get("support_arch") or []

        for os_ver in os_versions:
            if os_version_satisfies_constraint(os_ver, constraint):
                last_match[os_ver] = {
                    "chart_version": chart_version,
                    "support_arch": support_arch,
                }

    results: list[OsVersionInfo] = []
    for os_ver in os_versions:
        info = OsVersionInfo(os_version=os_ver)
        match = last_match.get(os_ver)
        if match:
            info.visibility = True
            cv = match.get("chart_version")
            if cv:
                info.chart_version = str(cv)
                info.support_arch = list(match.get("support_arch") or [])
            else:
                info.chart_version = "empty"
        results.append(info)
    return results


def apply_visibility_blacklist(
    os_infos: list[OsVersionInfo],
    app_name: str,
    blacklist: list[dict[str, str]],
) -> list[OsVersionInfo]:
    """Force visibility=false for OS versions matching versionConstraint rules."""
    rules = [r for r in blacklist if r.get("appName") == app_name]
    if not rules:
        return os_infos

    updated: list[OsVersionInfo] = []
    for info in os_infos:
        blocked = any(
            os_version_satisfies_constraint(
                info.os_version, r.get("versionConstraint")
            )
            for r in rules
        )
        if blocked:
            updated.append(
                OsVersionInfo(
                    os_version=info.os_version,
                    visibility=False,
                    chart_version=info.chart_version,
                    support_arch=list(info.support_arch),
                )
            )
        else:
            updated.append(info)
    return updated


def app_record_to_dict(
    app_name: str,
    status: str,
    os_infos: list[OsVersionInfo],
) -> dict[str, Any]:
    versions: list[dict[str, Any]] = []
    for info in os_infos:
        entry: dict[str, Any] = {
            "os_version": info.os_version,
            "visibility": info.visibility,
            "chart_version": info.chart_version,
        }
        if info.chart_version != "empty":
            entry["supportarch"] = info.support_arch
        versions.append(entry)
    return {
        "appname": app_name,
        "status": status,
        "os_versions": versions,
    }


def is_effective_app(os_entry: dict[str, Any], arch: str) -> bool:
    if not os_entry.get("visibility"):
        return False
    if os_entry.get("chart_version") == "empty":
        return False
    arches = os_entry.get("supportarch") or []
    return arch in arches
