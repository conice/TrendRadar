#!/usr/bin/env python3
"""Replay frequency rules against local TrendRadar SQLite news archives."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import importlib.util
from pathlib import Path
import sqlite3
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "trendradar" / "core" / "frequency.py"
SPEC = importlib.util.spec_from_file_location("trendradar_frequency", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"无法加载频率词模块：{MODULE_PATH}")
FREQUENCY_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FREQUENCY_MODULE
SPEC.loader.exec_module(FREQUENCY_MODULE)
_word_matches = FREQUENCY_MODULE._word_matches
load_frequency_words = FREQUENCY_MODULE.load_frequency_words
match_frequency_title = FREQUENCY_MODULE.match_frequency_title


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="用历史新闻回放词表，输出分组、过滤和零命中统计"
    )
    parser.add_argument(
        "--frequency-file",
        default="config/frequency_words.txt",
        help="词表路径（默认：config/frequency_words.txt）",
    )
    parser.add_argument(
        "--news-dir",
        default="output/news",
        help="SQLite 新闻目录（默认：output/news）",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="最多回放最近多少个数据库文件（默认：30）",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=3,
        help="每条过滤规则展示的样本数（默认：3）",
    )
    parser.add_argument(
        "--show-zero-rules",
        action="store_true",
        help="列出本次语料中零命中的具体规则",
    )
    return parser.parse_args()


def resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_titles(news_dir: Path, days: int) -> tuple[list[Path], list[str]]:
    database_paths = sorted(news_dir.glob("*.db"))[-days:]
    titles: set[str] = set()
    for database_path in database_paths:
        connection = sqlite3.connect(
            f"file:{database_path}?mode=ro",
            uri=True,
        )
        try:
            titles.update(
                title
                for (title,) in connection.execute(
                    "SELECT title FROM news_items WHERE title <> ''"
                )
            )
        finally:
            connection.close()
    return database_paths, sorted(titles)


def main() -> int:
    args = parse_args()
    frequency_file = resolve_path(args.frequency_file)
    news_dir = resolve_path(args.news_dir)
    database_paths, titles = load_titles(news_dir, max(args.days, 1))
    if not database_paths:
        print(f"未在 {news_dir} 找到数据库文件", file=sys.stderr)
        return 1

    word_groups, filter_words, global_filters = load_frequency_words(
        str(frequency_file)
    )
    first_group_hits: Counter[str] = Counter()
    any_group_hits: Counter[str] = Counter()
    filter_hits: Counter[str] = Counter()
    filter_samples: dict[str, list[str]] = defaultdict(list)
    accepted_count = 0
    multi_group_count = 0

    for title in titles:
        result = match_frequency_title(
            title,
            word_groups,
            filter_words=filter_words,
            global_filters=global_filters,
        )
        if result.filtered_by and result.filtered_by.startswith("global:"):
            unfiltered = match_frequency_title(
                title,
                word_groups,
                filter_words=filter_words,
            )
            if unfiltered.accepted:
                filter_hits[result.filtered_by] += 1
                if len(filter_samples[result.filtered_by]) < max(args.samples, 0):
                    filter_samples[result.filtered_by].append(title)
        if not result.accepted or not result.groups:
            continue

        accepted_count += 1
        if len(result.groups) > 1:
            multi_group_count += 1
        first_name = result.groups[0].get("display_name") or result.groups[0].get(
            "group_key", ""
        )
        first_group_hits[first_name] += 1
        for group in result.groups:
            name = group.get("display_name") or group.get("group_key", "")
            any_group_hits[name] += 1

    lower_titles = [title.lower() for title in titles]
    rule_hits: list[tuple[int, str, str]] = []
    for group in word_groups:
        group_name = group.get("display_name") or group.get("group_key", "")
        for rule in group.get("normal", []) + group.get("required", []):
            hits = sum(_word_matches(rule, title) for title in lower_titles)
            rule_name = rule.get("display_name") or rule.get("word", "")
            rule_hits.append((hits, group_name, rule_name))

    print(
        f"语料：{len(database_paths)} 个数据库，{len(titles)} 个唯一标题；"
        f"通过 {accepted_count}，多组命中 {multi_group_count}"
    )
    print("\n分组命中（首组 / 任意组）：")
    for group in word_groups:
        name = group.get("display_name") or group.get("group_key", "")
        print(f"{name}\t{first_group_hits[name]}\t{any_group_hits[name]}")

    print("\n会移除关注标题的全局过滤规则：")
    if not filter_hits:
        print("无")
    for reason, count in filter_hits.most_common():
        print(f"{reason}\t{count}")
        for sample in filter_samples[reason]:
            print(f"  - {sample}")

    zero_rules = [item for item in rule_hits if item[0] == 0]
    print(f"\n零命中规则：{len(zero_rules)} / {len(rule_hits)}")
    if args.show_zero_rules:
        for _, group_name, rule_name in zero_rules:
            print(f"[{group_name}] {rule_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
