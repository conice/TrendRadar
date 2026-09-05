from copy import deepcopy
from datetime import datetime
import html
import unittest
import xml.etree.ElementTree as ET

from trendradar.ai.analyzer import AIAnalysisResult
from trendradar.core.analyzer import convert_keyword_stats_to_platform_stats
from trendradar.notification.splitter import split_content_into_batches
from trendradar.report.generator import prepare_report_data
from trendradar.report.html import render_html_content


NOW = datetime(2026, 9, 6, 14, 35)
CHANNELS = ("telegram", "feishu", "feishu_text", "dingtalk", "wework", "wework_text",
            "slack", "bark", "ntfy", "generic_webhook", "generic_text")


def news(index=1, **overrides):
    return {
        "title": f"新闻 {index}：模型与芯片更新",
        "source_id": "source", "source_name": "科技来源",
        "url": f"https://example.test/article({index})?a=1&name=x_y", "mobile_url": "",
        "ranks": [3, 6, 9, 12, 8], "current_rank": 8, "count": 6, "rank_threshold": 5,
        "first_time": "2026-09-06 08:10", "last_time": "2026-09-06 14:30",
        "time_display": "08:10~14:30", "observed_date": "2026-09-06",
        "rank_timeline": [{"time": time, "rank": rank} for time, rank in
                          (("08:10", 3), ("09:00", 6), ("10:00", 3), ("12:00", 9), ("14:00", 12), ("14:30", 8))],
        "is_new": True, **overrides,
    }


def report(items=None, matched=15, mode="daily"):
    items = items if items is not None else [news()]
    return prepare_report_data([{"word": "海外 AI", "count": matched, "titles": items}], mode=mode)


def render(data, channel="telegram", mode="daily", **kwargs):
    return split_content_into_batches(data, channel, mode=mode, get_time_func=lambda: NOW, **kwargs)


def telegram_plain(content):
    root = ET.fromstring(f"<root>{content}</root>")
    return "".join(root.itertext())


class NotificationFormatTests(unittest.TestCase):
    def test_modes_share_layout_but_use_distinct_rank_and_time_meanings(self):
        data = report()
        rss = [{"word": "论文", "count": 1, "titles": [{
            "title": "论文标题", "source_name": "研究订阅", "source_id": "rss",
            "url": "https://example.test/paper", "published_at": "2026-09-06T05:20:00Z",
            "ranks": [1], "count": 9, "is_new": True,
        }]}]
        for channel in CHANNELS:
            for mode, label in (("daily", "当日汇总"), ("current", "当前榜单"), ("incremental", "增量更新")):
                with self.subTest(channel=channel, mode=mode):
                    content = "\n".join(render(data, channel, mode, rss_items=rss))
                    self.assertIn(label, content)
                    self.assertIn("展示 1／匹配 15 条", content)
                    self.assertIn("本次推送 2 条" if mode == "incremental" else "展示 2 条", content)
                    self.assertLess(content.index("模型与芯片更新"), content.index("科技来源"))
                    self.assertIn("发布 09-06 13:20", content)
                    self.assertNotIn("第 1", content[content.index("论文标题"):])
                    self.assertNotIn("采集命中 9", content)
                    if mode == "daily":
                        self.assertIn("今日最高第 3", content)
                        self.assertIn("采集命中 6 次", content)
                        self.assertIn("观测 08:10–14:30", content)
                    elif mode == "current":
                        self.assertIn("当前第 8", content)
                        self.assertIn("较上次上升 4 位", content)
                        self.assertIn("快照 09-06 14:30", content)
                        self.assertNotIn("今日最高第", content)
                    else:
                        self.assertIn("本次第 8", content)
                        self.assertIn("首次发现 08:10", content)
                        self.assertNotIn("本轮新发现", content)
                        self.assertNotIn("采集命中", content)

    def test_current_rank_does_not_guess_from_history_or_skip_missing_samples(self):
        cases = (
            ({"current_rank": None, "rank_timeline": []}, "今日第 3–12", "当前第"),
            ({"rank_timeline": [{"time": "14:00", "rank": None}, {"time": "14:30", "rank": 8}]}, "重新上榜", "较上次"),
            ({"current_rank": None, "rank_timeline": [{"time": "14:00", "rank": 8}, {"time": "14:30", "rank": None}]}, "最近采样未上榜", "当前第"),
            ({"ranks": [99], "rank_is_placeholder": True}, "科技来源", "第 99"),
        )
        for overrides, expected, absent in cases:
            with self.subTest(expected=expected):
                content = "".join(render(report([news(**overrides)]), mode="current"))
                self.assertIn(expected, content)
                self.assertNotIn(absent, content)

    def test_cross_day_pending_items_keep_their_observation_date(self):
        item = news(first_time="08-10", last_time="14-30", observed_date="2026-09-05")
        content = "".join(render(report([item]), mode="incremental"))
        self.assertIn("首次发现 09-05 08:10", content)

    def test_source_group_keeps_keyword_without_repeating_source(self):
        groups = [{"word": "海外 AI", "count": 1, "titles": [news()]}]
        groups = convert_keyword_stats_to_platform_stats(
            groups, {"RANK_WEIGHT": 0.6, "FREQUENCY_WEIGHT": 0.3, "HOTNESS_WEIGHT": 0.1}, 5,
        )
        data = prepare_report_data(groups, mode="current")
        content = "".join(render(data, mode="current", display_mode="platform"))
        self.assertEqual(content.count("科技来源"), 1)
        self.assertIn("海外 AI", content)

    def test_new_section_does_not_inflate_count_and_respects_switches(self):
        data = report([news(1), news(2, is_new=False)], matched=2)
        data["new_titles"] = [{"source_id": "source", "source_name": "科技来源", "titles": [news(1)]}]
        normal = "".join(render(data, mode="current"))
        self.assertIn("展示 2 条", normal)
        self.assertIn("本轮新发现 · 热榜", normal)
        self.assertNotIn("展示 3 条", normal)
        self.assertNotIn("本轮新发现 · 热榜", "".join(render(data, mode="current", show_new_section=False)))
        self.assertNotIn("本轮新发现", "".join(render(data, mode="incremental")))

    def test_independent_and_ai_scopes_are_not_inherited_from_incremental_header(self):
        data = report()
        standalone = {"platforms": [{"name": "独立热榜", "count": 30, "items": [news(2)]}]}
        analysis = AIAnalysisResult(success=True, ai_mode="daily", core_trends="当日趋势内容", hotlist_analyzed=150)
        content = "".join(render(data, mode="incremental", standalone_data=standalone, ai_analysis=analysis,
                                 region_order=["ai_analysis", "standalone", "hotlist"]))
        self.assertIn("本次推送 1 条 · 独立展示 1 条", content)
        self.assertIn("独立展示 · 热榜 · 当前快照", content)
        self.assertIn("展示 1／共 30 条", content)
        self.assertIn("当前第 8", content)
        self.assertIn("AI 解读 · 当日数据", content)
        self.assertIn("热榜 150 条", content)
        self.assertLess(content.index("当日趋势内容"), content.index("独立热榜"))

    def test_plaintext_and_links_preserve_special_characters(self):
        item = news(title='模型 [A] *B* <C> & "D"')
        for channel in CHANNELS:
            with self.subTest(channel=channel):
                content = "".join(render(report([item]), channel))
                if channel in {"wework_text", "feishu_text", "generic_text"}:
                    self.assertIn(item["title"], content)
                    self.assertIn("\n" + item["url"], content)
                    self.assertNotIn('<a href=', content)
                elif channel == "telegram":
                    root = ET.fromstring(f"<root>{content}</root>")
                    self.assertEqual(root.find("a").attrib["href"], item["url"])
                    self.assertIn(item["title"], "".join(root.itertext()))
                elif channel == "slack":
                    self.assertIn("article%281%29", content)
                    self.assertIn("|模型", content)
                else:
                    self.assertIn("article%281%29", content)
                    self.assertNotIn("<C>", content)

    def test_batching_keeps_entries_group_context_and_numbering(self):
        items = [news(index, title=f"标题 {index:02d} " + "内容" * 15) for index in range(1, 19)]
        data = report(items, matched=24)
        for channel in CHANNELS:
            with self.subTest(channel=channel):
                batches = render(data, channel, max_bytes=800)
                self.assertGreater(len(batches), 1)
                for page, content in enumerate(batches, 1):
                    self.assertIn(f"（{page}/{len(batches)}）", content.splitlines()[0])
                    self.assertIn("海外 AI" if page == 1 else "海外 AI（续）", content)
                    if channel == "telegram":
                        self.assertLessEqual(len(telegram_plain(content).encode("utf-16-le")) // 2, 800)
                    else:
                        self.assertLessEqual(len(content.encode("utf-8")), 800)
                    if page > 1:
                        self.assertNotIn("匹配 24", content)
                        self.assertNotIn("展示 18 条", content)
                for index in range(1, 19):
                    matching = [content for content in batches if f"标题 {index:02d}" in content]
                    self.assertEqual(len(matching), 1)
                    self.assertIn(f"{index}. ", matching[0])
                    self.assertIn("科技来源", matching[0])

    def test_telegram_limit_counts_text_instead_of_utf8_or_link_markup(self):
        item = news(title="中" * 1200, url="https://example.test/" + "x" * 7000)
        batches = render(report([item]), max_bytes=4000)
        self.assertEqual(len(batches), 1)
        root = ET.fromstring(f"<root>{batches[0]}</root>")
        self.assertEqual(root.find("a").attrib["href"], item["url"])
        self.assertGreater(len(batches[0].encode("utf-8")), 4000)

    def test_oversized_title_and_analysis_are_split_without_lost_text_or_broken_html(self):
        item = news(title="新闻起点" + "文🙂<&>" * 600 + "新闻终点")
        analysis = AIAnalysisResult(success=True, ai_mode="daily", core_trends="分析起点" + "析🙂<&>" * 600 + "分析终点")
        for channel in ("telegram", "wework_text", "feishu", "slack"):
            with self.subTest(channel=channel):
                batches = render(report([item]), channel, mode="current", max_bytes=700, ai_analysis=analysis)
                if channel == "telegram":
                    for content in batches:
                        self.assertLessEqual(len(telegram_plain(content).encode("utf-16-le")) // 2, 700)
                    combined = "".join(map(telegram_plain, batches))
                else:
                    self.assertTrue(all(len(content.encode("utf-8")) <= 700 for content in batches))
                    combined = html.unescape("".join(batches))
                self.assertEqual(combined.count("文"), 600)
                self.assertIn("新闻起点", combined)
                self.assertIn("新闻终点", combined)
                self.assertIn("分析起点", combined)
                self.assertIn("分析终点", combined)

    def test_impossible_url_is_rejected_instead_of_silently_truncated(self):
        item = news(url="https://example.test/" + "x" * 2000)
        with self.assertRaisesRegex(ValueError, "完整原文链接"):
            render(report([item]), "wework_text", max_bytes=500)

    def test_rendering_does_not_mutate_input(self):
        data = report()
        before = deepcopy(data)
        for channel in CHANNELS:
            render(data, channel)
        self.assertEqual(data, before)

    def test_collection_notes_follow_news_and_analysis_after_paging(self):
        data = report([news(index) for index in range(1, 10)])
        data.update(hotlist_total=120, rss_total_count=30, platform_total=3,
                    failed_ids=["缺失来源"], rss_source_total=2, rss_source_failed=1)
        analysis = AIAnalysisResult(success=True, ai_mode="daily", core_trends="分析结束标记")
        batches = render(data, max_bytes=700, ai_analysis=analysis)
        self.assertGreater(len(batches), 1)
        combined = "\n".join(map(telegram_plain, batches))
        self.assertLess(combined.index("新闻 9"), combined.index("分析结束标记"))
        self.assertLess(combined.index("分析结束标记"), combined.index("数据范围"))
        self.assertEqual(combined.count("数据范围：热榜 120 条 · RSS 30 条"), 1)
        self.assertIn("成功来源：热榜 2/3 · RSS 1/2", combined)
        self.assertIn("来源暂不可用：缺失来源", combined)

    def test_rss_source_groups_and_legacy_flat_items_agree_with_html(self):
        items = [{"title": f"订阅条目 {index}", "feed_id": f"feed{index}", "feed_name": f"订阅源 {index}",
                  "url": f"https://example.test/feed/{index}", "published_at": "2026-09-06T05:20:00Z"}
                 for index in (1, 2)]
        grouped = [{"word": "科技主题", "count": 2, "titles": items}]
        for rss in (items, grouped):
            with self.subTest(legacy_flat=rss is items):
                text = "".join(render(report([]), rss_items=rss, display_mode="platform"))
                page = render_html_content(report([]), 0, rss_items=rss,
                                           display_mode="platform", get_time_func=lambda: NOW)
                self.assertIn("展示 2 条", text)
                self.assertRegex(page, r'RSS 新闻</span>\s*<span class="info-value">2 条')
                for content in (text, page):
                    self.assertIn("订阅源 1", content)
                    self.assertIn("订阅源 2", content)
                    self.assertIn("发布 09-06 13:20", content)
                    self.assertNotIn("今日最高第", content)
                    if rss is grouped:
                        self.assertIn("科技主题", content)

    def test_missing_observation_fields_do_not_create_ranks_or_times(self):
        item = news(ranks=[], current_rank=None, rank_timeline=[], first_time="", last_time="",
                    time_display="", count=1, is_new=False)
        for mode in ("daily", "current", "incremental"):
            with self.subTest(mode=mode):
                content = "".join(render(report([item]), mode=mode))
                self.assertNotIn("第 ", content)
                self.assertNotIn("首次发现", content)
                self.assertNotIn("观测 ", content)
                self.assertIn(item["title"], content)

    def test_html_and_email_fields_follow_the_same_mode_rules(self):
        for mode, expected in (("daily", "今日最高第 3"), ("current", "当前第 8"), ("incremental", "本次第 8")):
            with self.subTest(mode=mode):
                content = render_html_content(report(), 100, mode, get_time_func=lambda: NOW)
                body = content[content.index('<div class="hotlist-section">'):]
                self.assertIn(expected, body)
                self.assertIn("展示 1／匹配 15 条", body)
                self.assertLess(body.index('class="news-title"'), body.index('class="news-header"'))
                if mode == "incremental":
                    self.assertNotIn("本轮新发现", body)


if __name__ == "__main__":
    unittest.main()
