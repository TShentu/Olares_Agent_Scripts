#!/usr/bin/env python3
"""
Compare Chart.yaml versions between two Olares app roots (e.g. prod vs test clones).
Supports .suspend / .remove markers like the legacy compare_chart script.
Paths, GitHub URLs, and blacklist path are read from config.yaml (see config.yaml.template).
By default, git sync runs before compare; pass --skip-sync to compare local trees only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Optional, Tuple

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
# Relative to process cwd; use -c to point elsewhere.
DEFAULT_CONFIG_PATH = Path("config.yaml")
DEFAULT_BLACKLIST_PATH = SCRIPT_DIR / "blacklist.txt"


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


def _require_str(d: dict[str, Any], key: str, where: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip():
        print(f"错误: {where} 缺少有效字段 {key!r}", file=sys.stderr)
        sys.exit(1)
    return v.strip()


def resolve_roots(cfg: dict[str, Any]) -> tuple[Path, Path, str, str]:
    prod = cfg.get("prod")
    test = cfg.get("test")
    if not isinstance(prod, dict) or not isinstance(test, dict):
        print("错误: 配置需包含 prod 与 test 两个映射。", file=sys.stderr)
        sys.exit(1)
    prod_root = Path(_require_str(prod, "local_path", "prod")).expanduser()
    test_root = Path(_require_str(test, "local_path", "test")).expanduser()
    prod_gh = _require_str(prod, "github", "prod")
    test_gh = _require_str(test, "github", "test")
    return prod_root, test_root, prod_gh, test_gh


def resolve_blacklist_from_config(cfg: dict[str, Any], config_path: Path) -> str:
    """
    Resolve blacklist file path from config.

    - Missing ``blacklist`` key: default ``<script_dir>/blacklist.txt``.
    - ``blacklist: null``: same as missing (default file).
    - ``blacklist: ""``: do not load a blacklist file.
    - Otherwise: non-empty string path; relative paths are resolved against
      ``config_path.parent``.
    """
    if "blacklist" not in cfg or cfg.get("blacklist") is None:
        return str(DEFAULT_BLACKLIST_PATH.resolve())
    raw = cfg["blacklist"]
    if isinstance(raw, str):
        s = raw.strip()
        if s == "":
            return ""
        p = Path(s).expanduser()
        if not p.is_absolute():
            p = (config_path.parent / p).resolve()
        else:
            p = p.resolve()
        return str(p)
    print(
        "错误: blacklist 必须是字符串（文件路径）或 null；空字符串表示不使用黑名单。",
        file=sys.stderr,
    )
    sys.exit(1)


def load_blacklist(blacklist_path: Optional[Path]) -> set[str]:
    if blacklist_path is None or not blacklist_path.exists():
        return set()
    try:
        with open(blacklist_path, "r", encoding="utf-8") as f:
            names: set[str] = set()
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    names.add(line.lower())
            return names
    except OSError:
        return set()


def extract_version_from_chart(chart_path: Path) -> Optional[str]:
    if not chart_path.exists():
        return None
    try:
        with open(chart_path, "r", encoding="utf-8") as f:
            chart_data = yaml.safe_load(f)
            if chart_data and "version" in chart_data:
                version = chart_data["version"]
                return str(version).strip("'\"")
    except Exception as e:
        print(f"警告: 无法解析 {chart_path}: {e}", file=sys.stderr)
        return None
    return None


def get_app_status(dir_path: Path) -> Tuple[Optional[str], Optional[str]]:
    if not dir_path.exists() or not dir_path.is_dir():
        return None, None
    suspend_file = dir_path / ".suspend"
    remove_file = dir_path / ".remove"
    if remove_file.exists():
        return "remove", "status"
    if suspend_file.exists():
        return "suspend", "status"
    chart_path = dir_path / "Chart.yaml"
    version = extract_version_from_chart(chart_path)
    return version, "version"


def origin_status_hidden(
    origin_type: Optional[str],
    origin_value: Optional[str],
    *,
    show_all: bool,
    show_suspend: bool,
    show_remove: bool,
) -> bool:
    """Return True if this row should be hidden due to prod (origin) suspend/remove rules."""
    if show_all:
        return False
    if origin_type != "status":
        return False
    if origin_value == "suspend":
        return not show_suspend
    if origin_value == "remove":
        return not show_remove
    return False


def compare_chart_versions(
    prod_root: Path,
    test_root: Path,
    *,
    prod_label: str = "prod",
    test_label: str = "test",
    show_all: bool = False,
    show_suspend: bool = False,
    show_remove: bool = False,
    blacklist: Optional[str] = None,
) -> None:
    if not prod_root.exists():
        print(f"错误: prod 目录不存在: {prod_root}", file=sys.stderr)
        return
    if not test_root.exists():
        print(f"错误: test 目录不存在: {test_root}", file=sys.stderr)
        return

    if show_all:
        blacklist_names: set[str] = set()
    elif blacklist == "":
        blacklist_names = set()
    else:
        path = Path(blacklist) if blacklist else DEFAULT_BLACKLIST_PATH
        blacklist_names = load_blacklist(path)

    prod_set: set[str] = set()
    for app_dir in prod_root.iterdir():
        if app_dir.is_dir() and not app_dir.name.startswith("."):
            prod_set.add(app_dir.name)

    test_set: set[str] = set()
    for app_dir in test_root.iterdir():
        if app_dir.is_dir() and not app_dir.name.startswith("."):
            test_set.add(app_dir.name)

    all_apps = sorted(prod_set | test_set)
    different_apps: list[dict[str, str]] = []

    for app_name in all_apps:
        if not show_all and app_name.lower() in blacklist_names:
            continue

        app_dir_prod = prod_root / app_name
        app_dir_test = test_root / app_name

        origin_value, origin_type = get_app_status(app_dir_prod)
        other_value, other_type = get_app_status(app_dir_test)

        if origin_value is None and origin_type is None:
            prod_display = "empty"
        elif origin_type == "status":
            prod_display = origin_value or "unknown"
        else:
            prod_display = origin_value if origin_value else "unknown"

        if other_value is None and other_type is None:
            test_display = "empty"
        elif other_type == "status":
            test_display = other_value or "unknown"
        else:
            test_display = other_value if other_value else "unknown"

        should_show = False

        if origin_type == "status" and other_type == "status":
            if origin_value != other_value:
                should_show = True
        elif origin_type == "status" or other_type == "status":
            should_show = True
        elif origin_type == "version" and other_type == "version":
            if origin_value != other_value:
                should_show = True
        elif (origin_value is None and origin_type is None) or (
            other_value is None and other_type is None
        ):
            should_show = True

        if should_show and origin_status_hidden(
            origin_type,
            origin_value,
            show_all=show_all,
            show_suspend=show_suspend,
            show_remove=show_remove,
        ):
            should_show = False

        if should_show:
            different_apps.append(
                {
                    "app_name": app_name,
                    "prod": prod_display,
                    "test": test_display,
                }
            )

    label_a = prod_label
    label_b = test_label
    w = max(len(label_a), len(label_b), 20)

    if different_apps:
        print("=" * 80)
        print(f"发现 {len(different_apps)} 个应用存在差异 (总应用数: {len(all_apps)}):")
        print("=" * 80)
        print(f"{'应用名称':<40} {label_a:<{w}} {label_b:<{w}}")
        print("-" * 80)
        for item in different_apps:
            print(f"{item['app_name']:<40} {item['prod']:<{w}} {item['test']:<{w}}")
        print("=" * 80)
    else:
        print(f"所有应用状态/版本都相同 (总应用数: {len(all_apps)})。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="比较 prod / test 两个目录下各应用的 Chart 版本与 suspend/remove 状态"
    )
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="配置文件路径（默认: 当前目录下的 config.yaml）",
    )
    parser.add_argument(
        "-A",
        "--all",
        action="store_true",
        help="显示全部差异，并忽略 blacklist；等价于不做 suspend/remove 隐藏",
    )
    parser.add_argument(
        "--show-suspend",
        action="store_true",
        help="在默认模式下仍显示 prod 侧为 suspend 的差异行",
    )
    parser.add_argument(
        "--show-remove",
        action="store_true",
        help="在默认模式下仍显示 prod 侧为 remove 的差异行",
    )
    parser.add_argument(
        "-b",
        "--blacklist",
        default=None,
        metavar="FILE",
        help='覆盖配置中的黑名单路径；传 "" 表示不加载黑名单（--all 时忽略黑名单）',
    )
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="跳过 git fetch/merge，直接按本地目录比较（默认会先同步远端）",
    )
    parser.add_argument(
        "--git-branch",
        default=None,
        metavar="BRANCH",
        help="同步时覆盖配置中的 git_branch（默认从 config 读取，否则 main）",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="同步时：工作区有未提交更改时仍继续（不推荐）",
    )
    parser.add_argument(
        "--token-env",
        default=None,
        metavar="NAME",
        help="同步时：仅从该环境变量读取 GitHub token（仍可使用 GITHUB_TOKEN_FILE）",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)

    if not args.skip_sync:
        from sync_repos import resolve_github_token, sync_from_config

        token = resolve_github_token(args.token_env)
        sync_from_config(
            cfg,
            args.config,
            branch=args.git_branch,
            token=token,
            allow_dirty=args.allow_dirty,
        )
        print()
    else:
        print("已跳过 git 同步，直接比较本地目录。\n")
    prod_root, test_root, prod_gh, test_gh = resolve_roots(cfg)

    if args.blacklist is not None:
        effective_blacklist = args.blacklist
    else:
        effective_blacklist = resolve_blacklist_from_config(cfg, args.config)

    print(f"prod: {prod_gh}")
    print(f"      {prod_root}")
    print(f"test: {test_gh}")
    print(f"      {test_root}")
    if not args.all:
        if effective_blacklist == "":
            print("blacklist: (disabled)")
        else:
            print(f"blacklist: {effective_blacklist}")
    print()

    compare_chart_versions(
        prod_root,
        test_root,
        prod_label="prod",
        test_label="test",
        show_all=args.all,
        show_suspend=args.show_suspend,
        show_remove=args.show_remove,
        blacklist=effective_blacklist,
    )


if __name__ == "__main__":
    main()
