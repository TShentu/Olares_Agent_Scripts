"""YAML config loading and prod/test roots for sync_chart (no compare_chart dependency)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "config.yaml"


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
