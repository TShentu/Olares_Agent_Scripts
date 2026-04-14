#!/usr/bin/env python3
"""Load config.yaml and verify prod/test local paths exist (optional sanity check)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from compare_chart_versions import (
    DEFAULT_CONFIG_PATH,
    load_config,
    resolve_blacklist_from_config,
    resolve_roots,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="校验 compare_chart 的 config.yaml")
    parser.add_argument(
        "-c",
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="配置文件路径（默认: 当前目录下的 config.yaml）",
    )
    args = parser.parse_args()
    cfg = load_config(args.config)
    prod_root, test_root, prod_gh, test_gh = resolve_roots(cfg)
    bl = resolve_blacklist_from_config(cfg, args.config)
    ok = True
    for name, path in ("prod", prod_root), ("test", test_root):
        if path.is_dir():
            print(f"OK {name}: {path}")
        else:
            print(f"MISS {name}: {path}", file=sys.stderr)
            ok = False
    print(f"prod github: {prod_gh}")
    print(f"test github: {test_gh}")
    if bl == "":
        print("blacklist: (disabled in config)")
    elif Path(bl).is_file():
        print(f"OK blacklist: {bl}")
    else:
        print(f"WARN blacklist file missing (compare treats as empty): {bl}", file=sys.stderr)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
