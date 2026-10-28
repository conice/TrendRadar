# coding=utf-8
"""
跨日期去重测试

数据按日期分库后，新一天首次抓取时不应把昨天的内容当成"新增"。
覆盖：
- LocalStorageBackend 的检测与首次抓取判定
- core/data.py 的生产路径 detect_latest_new_titles_from_storage
- __main__.py 增量模式的兜底过滤
"""

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from trendradar.storage.base import NewsData, NewsItem, RSSData, RSSItem
from trendradar.storage.local import LocalStorageBackend

TODAY = "2026-10-28"
YESTERDAY = "2026-10-27"


def _news_data(date, crawl_time, titles_by_source):
    """构造 NewsData（日期显式传入）"""
    items = {}
    id_to_name = {}
    for source_id, titles in titles_by_source.items():
        id_to_name[source_id] = source_id
        items[source_id] = [
            NewsItem(
                title=title,
                source_id=source_id,
                source_name=source_id,
                rank=index + 1,
                url=f"https://example.com/{source_id}/{title}",
                crawl_time=crawl_time,
                first_time=crawl_time,
                last_time=crawl_time,
            )
            for index, title in enumerate(titles)
        ]
    return NewsData(date=date, crawl_time=crawl_time, items=items, id_to_name=id_to_name)


def _rss_data(date, crawl_time, titles_by_feed):
    """构造 RSSData（日期显式传入）"""
    items = {}
    id_to_name = {}
    for feed_id, titles in titles_by_feed.items():
        id_to_name[feed_id] = feed_id
        items[feed_id] = [
            RSSItem(
                title=title,
                feed_id=feed_id,
                feed_name=feed_id,
                url=f"https://example.com/{feed_id}/{title}",
                published_at=f"{date}T00:00:00+08:00",
                crawl_time=crawl_time,
                first_time=crawl_time,
                last_time=crawl_time,
            )
            for title in titles
        ]
    return RSSData(date=date, crawl_time=crawl_time, items=items, id_to_name=id_to_name)


class _BackendTestCase(TestCase):
    """提供指向固定"今天"的本地后端"""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.backend = LocalStorageBackend(
            data_dir=self._tmp.name, enable_txt=False, enable_html=False
        )
        self.addCleanup(self.backend.cleanup)

        # 后端按名称导入了这两个函数，patch 后端模块内的引用才生效
        patcher = patch(
            "trendradar.storage.local.format_date_folder",
            side_effect=lambda date, timezone: date or TODAY,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

        patcher = patch(
            "trendradar.storage.local.get_configured_time",
            return_value=datetime(2026, 10, 28, 9, 0),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def save_news(self, date, crawl_time, titles_by_source):
        self.assertTrue(
            self.backend.save_news_data(_news_data(date, crawl_time, titles_by_source))
        )

    def save_rss(self, date, crawl_time, titles_by_feed):
        self.assertTrue(
            self.backend.save_rss_data(_rss_data(date, crawl_time, titles_by_feed))
        )


class CrossDateDedupTests(_BackendTestCase):
    """存储层跨日期去重"""

    # ---------- is_first_crawl_today ----------

    def test_first_crawl_false_when_previous_day_has_data(self):
        self.save_news(YESTERDAY, "20:00", {"weibo": ["昨天头条"]})

        self.assertFalse(self.backend.is_first_crawl_today(TODAY))

    def test_first_crawl_true_without_any_history(self):
        self.assertTrue(self.backend.is_first_crawl_today(TODAY))

    def test_first_crawl_false_when_same_day_has_multiple_batches(self):
        self.save_news(TODAY, "09:00", {"weibo": ["九点头条"]})
        self.save_news(TODAY, "12:00", {"weibo": ["十二点头条"]})

        self.assertFalse(self.backend.is_first_crawl_today(TODAY))

    # ---------- 热榜 ----------

    def test_detect_new_titles_excludes_previous_day_titles(self):
        self.save_news(YESTERDAY, "20:00", {"weibo": ["老新闻", "旧新闻"]})

        current = _news_data(TODAY, "09:00", {"weibo": ["老新闻", "旧新闻", "真正的新新闻"]})
        new_titles = self.backend.detect_new_titles(current)

        self.assertEqual(set(new_titles.get("weibo", {})), {"真正的新新闻"})

    def test_detect_new_titles_without_history_keeps_all(self):
        current = _news_data(TODAY, "09:00", {"weibo": ["第一条", "第二条"]})
        new_titles = self.backend.detect_new_titles(current)

        self.assertEqual(set(new_titles.get("weibo", {})), {"第一条", "第二条"})

    # ---------- RSS ----------

    def test_detect_new_rss_items_excludes_previous_day_items(self):
        self.save_rss(YESTERDAY, "20:00", {"hn": ["昨天的文章"]})

        current = _rss_data(TODAY, "09:00", {"hn": ["昨天的文章", "今天的文章"]})
        new_items = self.backend.detect_new_rss_items(current)

        titles = {item.title for item in new_items.get("hn", [])}
        self.assertEqual(titles, {"今天的文章"})

    def test_detect_new_rss_items_without_history_keeps_all(self):
        current = _rss_data(TODAY, "09:00", {"hn": ["文章一", "文章二"]})
        new_items = self.backend.detect_new_rss_items(current)

        titles = {item.title for item in new_items.get("hn", [])}
        self.assertEqual(titles, {"文章一", "文章二"})

    def test_detect_new_rss_items_matches_previous_day_by_title_when_url_changes(self):
        self.save_rss(YESTERDAY, "20:00", {"hn": ["同一篇文章"]})

        current = _rss_data(TODAY, "09:00", {"hn": ["同一篇文章"]})
        # 模拟链接带上追踪参数
        current.items["hn"][0].url = "https://example.com/hn/tracked?utm_source=rss"
        new_items = self.backend.detect_new_rss_items(current)

        self.assertEqual(new_items, {})

    # ---------- 辅助方法 ----------

    def test_previous_day_helpers_return_empty_without_history(self):
        self.assertEqual(self.backend.get_previous_day_titles(TODAY), {})
        self.assertEqual(self.backend.get_previous_day_rss_keys(TODAY), {})

    def test_db_exists_does_not_create_file(self):
        self.assertFalse(self.backend._db_exists("2025-01-01", "news"))
        self.assertFalse((Path(self._tmp.name) / "news" / "2025-01-01.db").exists())


class DetectLatestNewTitlesTests(_BackendTestCase):
    """core/data.py 生产路径的跨日期去重"""

    def test_latest_new_titles_excludes_previous_day_titles(self):
        from trendradar.core.data import detect_latest_new_titles_from_storage

        self.save_news(YESTERDAY, "20:00", {"weibo": ["老新闻"]})
        self.save_news(TODAY, "09:00", {"weibo": ["老新闻", "新新闻"]})

        new_titles = detect_latest_new_titles_from_storage(self.backend)

        self.assertEqual(set(new_titles.get("weibo", {})), {"新新闻"})

    def test_latest_new_titles_without_any_history_returns_all(self):
        from trendradar.core.data import detect_latest_new_titles_from_storage

        self.save_news(TODAY, "09:00", {"weibo": ["第一条"]})

        new_titles = detect_latest_new_titles_from_storage(self.backend)

        self.assertEqual(set(new_titles.get("weibo", {})), {"第一条"})


class PreviousDayFilterTests(_BackendTestCase):
    """__main__.py 增量模式的兜底过滤"""

    def setUp(self):
        super().setUp()
        from trendradar.__main__ import NewsAnalyzer

        self.analyzer = NewsAnalyzer.__new__(NewsAnalyzer)
        self.analyzer.storage_manager = self.backend

    def test_filter_previous_day_titles(self):
        self.save_news(YESTERDAY, "20:00", {"weibo": ["老新闻"]})

        filtered = self.analyzer._filter_previous_day_titles(
            {"weibo": {"老新闻": {"ranks": [1]}, "新新闻": {"ranks": [2]}}}
        )

        self.assertEqual(set(filtered.get("weibo", {})), {"新新闻"})

    def test_filter_previous_day_rss_items(self):
        self.save_rss(YESTERDAY, "20:00", {"hn": ["老文章"]})

        current = _rss_data(TODAY, "09:00", {"hn": ["老文章", "新文章"]})
        filtered = self.analyzer._filter_previous_day_rss_items(current.items)

        titles = {item.title for item in filtered.get("hn", [])}
        self.assertEqual(titles, {"新文章"})

    def test_filters_are_noop_without_previous_day_data(self):
        self.assertEqual(self.analyzer._filter_previous_day_titles({"weibo": {}}), {"weibo": {}})
        self.assertEqual(self.analyzer._filter_previous_day_rss_items({}), {})
