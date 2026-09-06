from __future__ import annotations

from contextlib import chdir, redirect_stdout
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "trendradar" / "core" / "frequency.py"
SPEC = importlib.util.spec_from_file_location("trendradar_frequency_tests", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"无法加载频率词模块：{MODULE_PATH}")
frequency = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = frequency
SPEC.loader.exec_module(frequency)


class FrequencyTestCase(unittest.TestCase):
    def load_text(self, content: str):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        path = Path(temporary_directory.name) / "frequency_words.txt"
        path.write_text(content, encoding="utf-8")
        return path, frequency.load_frequency_words(str(path))

    @staticmethod
    def group_names(result) -> list[str]:
        return [group.get("display_name") for group in result.groups]

    def test_named_regex_global_filter_is_applied(self) -> None:
        _, (groups, legacy_filters, global_filters) = self.load_text(
            """[GLOBAL_FILTER]
/^震惊[！!]/ => 标题党

[WORD_GROUPS]

[AI]
ChatGPT
@5
"""
        )

        result = frequency.match_frequency_title(
            "震惊！ChatGPT 发布新品",
            groups,
            legacy_filters,
            global_filters,
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.filtered_by, "global:标题党")

    def test_group_filter_only_rejects_its_own_group(self) -> None:
        _, (groups, legacy_filters, global_filters) = self.load_text(
            """[GLOBAL_FILTER]

[WORD_GROUPS]

[AI]
ChatGPT
!广告
@5

[广告行业]
广告
@5
"""
        )

        result = frequency.match_frequency_title(
            "ChatGPT 广告行业规范发布",
            groups,
            legacy_filters,
            global_filters,
        )

        self.assertTrue(result.accepted)
        self.assertEqual(self.group_names(result), ["广告行业"])
        self.assertEqual(legacy_filters, [])

    def test_required_words_are_all_required(self) -> None:
        _, (groups, legacy_filters, global_filters) = self.load_text(
            """[GLOBAL_FILTER]

[WORD_GROUPS]

[AI发布]
ChatGPT
+发布
@5
"""
        )

        self.assertFalse(
            frequency.match_frequency_title(
                "ChatGPT 使用教程", groups, legacy_filters, global_filters
            ).accepted
        )
        self.assertTrue(
            frequency.match_frequency_title(
                "ChatGPT 发布新版本", groups, legacy_filters, global_filters
            ).accepted
        )

    def test_definition_order_is_preserved(self) -> None:
        _, (groups, legacy_filters, global_filters) = self.load_text(
            """[GLOBAL_FILTER]

[WORD_GROUPS]

[具体主题]
OpenAI
@5

[宽泛主题]
AI
@5
"""
        )

        result = frequency.match_frequency_title(
            "OpenAI 推出 AI 新功能", groups, legacy_filters, global_filters
        )

        self.assertEqual(self.group_names(result), ["具体主题", "宽泛主题"])
        self.assertTrue(
            frequency.matches_word_groups(
                "OpenAI 推出 AI 新功能",
                groups,
                legacy_filters,
                global_filters,
            )
        )

    def test_global_filter_regexes_are_scoped(self) -> None:
        _, rules = self.load_text(
            """[GLOBAL_FILTER]
/^震惊[！!]/ => 标题党
/到手价.*限时抢购/ => 促销

[WORD_GROUPS]

[测试主题]
ChatGPT
iPhone
OpenAI
日本
诈骗
@5
"""
        )
        cases = (
            ("震惊！ChatGPT 发布新品", False),
            ("iPhone 到手价仅 999 元，限时抢购", False),
            ("中方对日本高官拥核言论感到震惊", True),
            ("官方已辟谣：OpenAI 停止服务为不实消息", True),
            ("新型电信诈骗曝光，警方发布反诈提示", True),
        )
        for title, expected_accepted in cases:
            with self.subTest(title=title):
                result = frequency.match_frequency_title(title, *rules)
                self.assertEqual(result.accepted, expected_accepted)

    def test_ambiguous_terms_are_scoped(self) -> None:
        _, rules = self.load_text(
            """[GLOBAL_FILTER]

[WORD_GROUPS]

[车型]
/比亚迪.*海豹|海豹.*比亚迪/ => 海豹车型
@5

[医疗付费]
/(?<![A-Za-z0-9_])DRG(?![A-Za-z0-9_])/ => DRG
+医保
@5
"""
        )
        cases = (
            ("比亚迪海豹新车发布", ["车型"]),
            ("宋亚轩滑溜溜海豹舞要素过多", []),
            ("医保 DRG 支付改革推进", ["医疗付费"]),
            ("医保 drg 支付改革推进", ["医疗付费"]),
            ("医保 ADRG 分组调整", []),
            ("TES.A 战胜 DRG 晋级挑战者杯", []),
        )
        for title, expected_groups in cases:
            with self.subTest(title=title):
                result = frequency.match_frequency_title(title, *rules)
                self.assertEqual(result.accepted, bool(expected_groups))
                self.assertEqual(self.group_names(result), expected_groups)

    def test_custom_group_definitions_are_respected(self) -> None:
        cases = (
            ("[自定义主题]\n云计算\n数据中心\n@5", ["自定义主题"]),
            (
                "[主题甲]\n云计算\n@5\n\n[主题乙]\n数据中心\n@5",
                ["主题甲", "主题乙"],
            ),
            ("[主题乙]\n数据中心\n@5", ["主题乙"]),
        )
        for group_definitions, expected_groups in cases:
            with self.subTest(groups=expected_groups):
                _, rules = self.load_text(
                    f"[GLOBAL_FILTER]\n\n[WORD_GROUPS]\n\n{group_definitions}\n"
                )
                result = frequency.match_frequency_title(
                    "云计算数据中心加快采用液冷服务器", *rules
                )
                self.assertTrue(result.accepted)
                self.assertEqual(self.group_names(result), expected_groups)

    def test_default_file_passes_strict_validation(self) -> None:
        # 实际词库允许自定义分组和规则，这里只校验合法性；匹配行为使用独立样例。
        default_path = PROJECT_ROOT / "config" / "frequency_words.txt"
        self.assertEqual(frequency.validate_frequency_file(default_path), [])

    def test_invalid_regex_is_reported(self) -> None:
        with redirect_stdout(io.StringIO()):
            path, _ = self.load_text(
                """[GLOBAL_FILTER]

[WORD_GROUPS]

[坏规则]
/(/
@5
"""
            )
        with redirect_stdout(io.StringIO()):
            issues = frequency.validate_frequency_file(path)
        self.assertTrue(
            any(issue.level == "error" and "无效正则" in issue.message for issue in issues)
        )


class FrequencyMergeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.root = Path(temporary_directory.name)
        self.custom_dir = self.root / "config" / "custom" / "keyword"
        self.custom_dir.mkdir(parents=True)
        self.default_file = self.root / "config" / "frequency_words.txt"
        self.default_file.write_text(
            "[GLOBAL_FILTER]\n/^震惊[！!]/ => 标题党\n\n"
            "[WORD_GROUPS]\n\n[芯片]\n芯片\n@2\n",
            encoding="utf-8",
        )

    def write_custom(self, name: str, content: str) -> Path:
        path = self.custom_dir / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_default_load_merges_custom_files_in_filename_order(self) -> None:
        self.write_custom("Total.txt", "[WORD_GROUPS]\n\n[黄仁勋]\n黄仁勋\n@5\n")
        self.write_custom("Authors.txt", "[WORD_GROUPS]\n\n[鲁迅]\n鲁迅\n@5\n")
        self.write_custom("notes.md", "不应加载的说明")
        (self.custom_dir / "directory.txt").mkdir()

        with chdir(self.root), patch.dict(os.environ, {}, clear=True):
            rules = frequency.load_frequency_words()

        self.assertEqual(
            [group["display_name"] for group in rules[0]], ["鲁迅", "黄仁勋", "芯片"]
        )
        result = frequency.match_frequency_title("黄仁勋介绍新芯片", *rules)
        self.assertEqual(
            FrequencyTestCase.group_names(result), ["黄仁勋", "芯片"]
        )
        self.assertTrue(frequency.match_frequency_title("芯片行业新闻", *rules).accepted)
        self.assertFalse(
            frequency.match_frequency_title("震惊！黄仁勋介绍新芯片", *rules).accepted
        )

    def test_selected_custom_file_merges_only_itself_and_the_base(self) -> None:
        selected = self.write_custom(
            "Total.txt", "[WORD_GROUPS]\n\n[黄仁勋]\n黄仁勋\n@5\n"
        )
        self.write_custom("Authors.txt", "[WORD_GROUPS]\n\n[鲁迅]\n鲁迅\n")

        with chdir(self.root):
            for path in ("Total.txt", "config/custom/keyword/Total.txt", str(selected)):
                with self.subTest(path=path):
                    groups, _, _ = frequency.load_frequency_words(path)
                    self.assertEqual(
                        [group["display_name"] for group in groups], ["黄仁勋", "芯片"]
                    )
            with patch.dict(os.environ, {"FREQUENCY_WORDS_PATH": "Total.txt"}):
                groups, _, _ = frequency.load_frequency_words()
                self.assertEqual(
                    [group["display_name"] for group in groups], ["黄仁勋", "芯片"]
                )

    def test_merge_preserves_global_filters_and_group_conditions(self) -> None:
        self.write_custom(
            "Total.txt",
            "[GLOBAL_FILTER]\n剧透\n\n[WORD_GROUPS]\n\n"
            "[人物访谈]\n黄仁勋\n+访谈\n!广告\n@5\n",
        )
        rules = frequency.load_frequency_words(str(self.default_file))
        cases = (
            ("黄仁勋访谈", ["人物访谈"]),
            ("黄仁勋发布新计划", []),
            ("黄仁勋访谈广告", []),
            ("黄仁勋访谈芯片广告", ["芯片"]),
            ("震惊！黄仁勋访谈", []),
            ("黄仁勋访谈剧透", []),
            ("芯片剧透", []),
        )
        for title, expected_groups in cases:
            with self.subTest(title=title):
                result = frequency.match_frequency_title(title, *rules)
                self.assertEqual(FrequencyTestCase.group_names(result), expected_groups)
                self.assertEqual(result.accepted, bool(expected_groups))
        self.assertEqual([group["max_count"] for group in rules[0]], [5, 2])

    def test_identical_groups_and_global_filters_are_deduplicated(self) -> None:
        self.write_custom("Total.txt", self.default_file.read_text(encoding="utf-8"))
        groups, _, global_filters = frequency.load_frequency_words(str(self.default_file))
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(global_filters), 1)

    def test_shared_keywords_keep_distinct_group_conditions_and_statistic_keys(self) -> None:
        self.write_custom(
            "Total.txt", "[WORD_GROUPS]\n\n[人物]\n芯片\n+黄仁勋\n@5\n"
        )
        rules = frequency.load_frequency_words(str(self.default_file))
        self.assertEqual(len({group["group_key"] for group in rules[0]}), 2)
        result = frequency.match_frequency_title("黄仁勋介绍芯片", *rules)
        self.assertEqual(FrequencyTestCase.group_names(result), ["人物", "芯片"])
        result = frequency.match_frequency_title("芯片新闻", *rules)
        self.assertEqual(FrequencyTestCase.group_names(result), ["芯片"])

    def test_empty_custom_file_preserves_default_groups(self) -> None:
        self.write_custom("Total.txt", "[WORD_GROUPS]\n")
        rules = frequency.load_frequency_words(str(self.default_file))
        result = frequency.match_frequency_title("芯片新闻", *rules)
        self.assertEqual(FrequencyTestCase.group_names(result), ["芯片"])

    def test_custom_validation_checks_only_the_selected_file(self) -> None:
        selected = self.write_custom(
            "Total.txt", "[WORD_GROUPS]\n\n[黄仁勋]\n黄仁勋\n@5\n"
        )
        self.default_file.unlink()
        self.assertEqual(frequency.validate_frequency_file(selected), [])

        selected.write_text("[WORD_GROUPS]\n\n[人物]\n/(/\n", encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            issues = frequency.validate_frequency_file(selected)
        self.assertTrue(any("无效正则" in issue.message for issue in issues))

    def test_base_validation_still_requires_global_filter_section(self) -> None:
        self.default_file.write_text("[WORD_GROUPS]\n\n芯片\n", encoding="utf-8")
        issues = frequency.validate_frequency_file(self.default_file)
        self.assertTrue(
            any(issue.level == "error" and "[GLOBAL_FILTER]" in issue.message for issue in issues)
        )

    def test_missing_selected_file_or_base_is_reported(self) -> None:
        with self.assertRaises(FileNotFoundError):
            frequency.load_frequency_words(str(self.custom_dir / "missing.txt"))
        selected = self.write_custom("Total.txt", "[WORD_GROUPS]\n\n黄仁勋\n")
        self.default_file.unlink()
        with self.assertRaises(FileNotFoundError):
            frequency.load_frequency_words(str(selected))


if __name__ == "__main__":
    unittest.main()
