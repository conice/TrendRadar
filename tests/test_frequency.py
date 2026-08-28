from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = PROJECT_ROOT / "trendradar" / "core" / "frequency.py"
SPEC = importlib.util.spec_from_file_location("trendradar_frequency_tests", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"无法加载频率词模块：{MODULE_PATH}")
frequency = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = frequency
SPEC.loader.exec_module(frequency)


class FrequencyTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.default_path = PROJECT_ROOT / "config" / "frequency_words.txt"
        cls.groups, cls.legacy_filters, cls.global_filters = (
            frequency.load_frequency_words(str(cls.default_path))
        )

    def load_text(self, content: str):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        path = Path(temporary_directory.name) / "frequency_words.txt"
        path.write_text(content, encoding="utf-8")
        return path, frequency.load_frequency_words(str(path))

    def match_default(self, title: str):
        return frequency.match_frequency_title(
            title,
            self.groups,
            filter_words=self.legacy_filters,
            global_filters=self.global_filters,
        )

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

    def test_default_global_filters_are_high_precision(self) -> None:
        self.assertEqual(len(self.global_filters), 14)
        self.assertFalse(self.match_default("震惊！ChatGPT 发布新品").accepted)
        self.assertFalse(
            self.match_default("iPhone 到手价仅 999 元，限时抢购").accepted
        )
        self.assertTrue(
            self.match_default("中方对日本高官拥核言论感到震惊").accepted
        )
        self.assertTrue(
            self.match_default("官方已辟谣：OpenAI 停止服务为不实消息").accepted
        )
        self.assertTrue(
            self.match_default("新型电信诈骗曝光，警方发布反诈提示").accepted
        )
        self.assertIn(
            "剧集综艺",
            self.group_names(self.match_default("微短剧行业监管新规发布")),
        )

    def test_ambiguous_terms_are_scoped(self) -> None:
        self.assertNotIn(
            "电动汽车",
            self.group_names(self.match_default("宋亚轩滑溜溜海豹舞要素过多")),
        )
        self.assertNotIn(
            "隐私与安全",
            self.group_names(
                self.match_default("大理五部门联合约谈辖区租车企业")
            ),
        )
        self.assertNotIn(
            "电动汽车",
            self.group_names(self.match_default("软件团队讨论端到端测试")),
        )
        self.assertNotIn(
            "国产AI",
            self.group_names(self.match_default("登山队成功登顶 K2")),
        )
        self.assertNotIn(
            "医疗健康",
            self.group_names(self.match_default("TES.A 战胜 DRG 晋级挑战者杯")),
        )

    def test_new_high_value_topics_match(self) -> None:
        cases = {
            "金价又涨了": "贵金属",
            "台湾民意机构通过赖清德弹劾提案": "台海两岸",
            "江西省政协副主席尹建业被查": "政务纪检",
            "世界最长高速公路隧道通车": "重大工程交通",
            "国家卫健委要求医院清退门诊押金": "医疗健康",
            "云计算数据中心加快采用液冷服务器": "云计算与数据中心",
            "工业母机实现五轴联动技术突破": "产业制造",
            "中央企业加快战略性重组": "国资央企",
        }
        for title, expected_group in cases.items():
            with self.subTest(title=title):
                self.assertIn(expected_group, self.group_names(self.match_default(title)))

    def test_default_file_passes_strict_validation(self) -> None:
        self.assertEqual(frequency.validate_frequency_file(self.default_path), [])

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


if __name__ == "__main__":
    unittest.main()
