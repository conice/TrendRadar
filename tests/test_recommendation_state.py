from contextlib import closing
from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
import unittest

from trendradar.storage.state import RecommendationState, article_key


def group(title="AI news", url="https://example.com/article", source_id="news", limit=0):
    return {"word": "AI", "count": 1, "_max_count": limit, "titles": [{
        "title": title, "url": url, "source_id": source_id, "source_name": "News",
        "ranks": [1], "count": 1, "rank_threshold": 5, "time_display": "09:00",
    }]}


class RecommendationStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 9, 5, 9, tzinfo=timezone.utc)
        self.state = RecommendationState(self.temp.name)

    def test_identity_survives_title_and_tracking_changes(self):
        original = {"source_id": "a", "title": "Old", "url": "https://EXAMPLE.com/item?id=4&utm_source=rss#top"}
        changed = {"source_id": "a", "title": "New", "url": "https://example.com/item?id=4"}
        self.assertEqual(article_key("hotlist", original), article_key("hotlist", changed))
        self.assertNotEqual(article_key("hotlist", original), article_key("hotlist", {**changed, "source_id": "b"}))
        self.assertNotEqual(article_key("hotlist", original), article_key("hotlist", {**changed, "url": "https://example.com/item?id=5"}))
        self.assertEqual(article_key("rss", {"feed_id": "a", "guid": "123", "title": "Old"}),
                         article_key("rss", {"source_id": "a", "guid": "123", "title": "New"}))
        self.assertEqual(article_key("hotlist", {"source_id": "weibo", "url": "https://s.weibo.com/weibo?q=AI&band_rank=1"}),
                         article_key("hotlist", {"source_id": "weibo", "url": "https://s.weibo.com/weibo?band_rank=8&q=AI"}))

    def test_success_is_deduplicated_after_reopening_on_next_day(self):
        news, rss = self.state.prepare([group()], [group(source_id="feed")], self.now)
        self.state.mark_delivered(news, rss, self.now)
        reopened = RecommendationState(self.temp.name)
        later = self.now + timedelta(days=1)
        self.assertEqual(reopened.prepare([group(title="Updated title")], [group(source_id="feed")], later), ([], []))
        candidates = {"news": {
            "Updated title": {"url": "https://example.com/article"},
            "New article": {"url": "https://example.com/new"},
        }}
        self.assertEqual(list(reopened.filter_news(candidates, later)["news"]), ["New article"])
        self.assertEqual(reopened.filter_rss([{"feed_id": "feed", "url": "https://example.com/article"}], later), [])

    def test_unsent_content_survives_restart_and_disappearing_from_feed(self):
        self.state.prepare([group()], [group(source_id="feed")], self.now)
        reopened = RecommendationState(self.temp.name)
        news, rss = reopened.prepare([], [], self.now + timedelta(days=1))
        self.assertEqual(news[0]["titles"][0]["title"], "AI news")
        self.assertEqual(rss[0]["titles"][0]["source_id"], "feed")
        reopened.mark_delivered(news, rss, self.now + timedelta(days=1))
        self.assertEqual(reopened.prepare([], [], self.now + timedelta(days=2)), ([], []))

    def test_only_visible_items_are_acknowledged_and_translation_is_safe(self):
        incoming = group(limit=1)
        incoming["titles"] += group(title="Second", url="https://example.com/2")["titles"]
        news, rss = self.state.prepare([incoming], None, self.now)
        self.assertEqual(len(news[0]["titles"]), 1)
        news[0]["titles"][0]["title"] = "Translated"
        self.state.mark_delivered(news, rss, self.now)
        remaining, _ = self.state.prepare([], None, self.now + timedelta(hours=1))
        self.assertEqual([item["title"] for item in remaining[0]["titles"]], ["Second"])

    def test_pending_content_respects_scope_and_enabled_sources(self):
        self.state.prepare([group()], None, self.now, scope="keyword:a")
        self.assertEqual(self.state.prepare([], None, self.now, scope="keyword:b"), ([], []))
        self.assertEqual(self.state.prepare([], None, self.now, scope="keyword:a", active_sources={"hotlist": set(), "rss": set()}), ([], []))
        news, _ = self.state.prepare([], None, self.now, scope="keyword:a", active_sources={"hotlist": {"news"}, "rss": set()})
        self.assertEqual(len(news), 1)

    def test_retry_uses_current_display_limit(self):
        incoming = group(limit=2)
        incoming["titles"] += group(title="Second", url="https://example.com/2")["titles"]
        self.state.prepare([incoming], None, self.now)
        new_settings = {**group(limit=1), "titles": [], "count": 0}
        news, _ = self.state.prepare([new_settings], None, self.now + timedelta(days=1))
        self.assertEqual(len(news[0]["titles"]), 1)

    def test_expiration_and_cleanup_interval_are_independent(self):
        calls = []
        cleanup = lambda days: calls.append(days) or 0
        news, rss = self.state.prepare([group()], None, self.now)
        self.state.mark_delivered(news, rss, self.now)
        self.state.cleanup_if_due(self.now + timedelta(days=1), cleanup)
        self.state.cleanup_if_due(self.now + timedelta(days=30), cleanup)
        self.assertEqual(calls, [30])
        # The 30-day window expires even before physical cleanup is due.
        news, _ = self.state.prepare([group()], None, self.now + timedelta(days=30))
        self.assertEqual(len(news[0]["titles"]), 1)
        self.state.cleanup_if_due(self.now + timedelta(days=31), cleanup)
        self.assertEqual(calls, [30, 30])
        with closing(sqlite3.connect(self.state.path)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM delivered").fetchone()[0], 0)

    def test_failed_cleanup_is_retried(self):
        def fail(_):
            raise OSError("disk unavailable")
        with self.assertRaises(OSError):
            self.state.cleanup_if_due(self.now, fail)
        calls = []
        self.state.cleanup_if_due(self.now, lambda days: calls.append(days) or 0)
        self.assertEqual(calls, [30])

    def test_future_schema_is_not_overwritten(self):
        with closing(sqlite3.connect(self.state.path)) as conn:
            conn.execute("PRAGMA user_version=2")
        with self.assertRaisesRegex(RuntimeError, "版本"):
            self.state.prepare([], [], self.now)
        with closing(sqlite3.connect(self.state.path)) as conn:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
