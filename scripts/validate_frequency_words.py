#!/usr/bin/env python3
"""Validate a TrendRadar frequency-word configuration."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "trendradar" / "core" / "frequency.py"
SPEC = importlib.util.spec_from_file_location("trendradar_frequency", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"无法加载频率词模块：{MODULE_PATH}")
FREQUENCY_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FREQUENCY_MODULE
SPEC.loader.exec_module(FREQUENCY_MODULE)
validate_frequency_file = FREQUENCY_MODULE.validate_frequency_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="检查频率词结构、重复规则、数量限制和正则语法"
    )
    parser.add_argument(
        "frequency_file",
        nargs="?",
        default="config/frequency_words.txt",
        help="待检查的词表路径（默认：config/frequency_words.txt）",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="将 warning 也视为失败",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = Path(args.frequency_file)
    if not path.is_absolute():
        path = PROJECT_ROOT / path

    try:
        issues = validate_frequency_file(path)
    except (OSError, UnicodeError) as exc:
        print(f"ERROR: 无法读取 {path}: {exc}", file=sys.stderr)
        return 1

    for issue in sorted(issues, key=lambda item: (item.line, item.level)):
        location = str(path)
        if issue.line:
            location += f":{issue.line}"
        print(f"{issue.level.upper()}: {location}: {issue.message}")

    errors = sum(issue.level == "error" for issue in issues)
    warnings = sum(issue.level == "warning" for issue in issues)
    if errors or (args.strict and warnings):
        print(f"校验失败：{errors} error(s), {warnings} warning(s)")
        return 1

    print(f"校验通过：{errors} error(s), {warnings} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
