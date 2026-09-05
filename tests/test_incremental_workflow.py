from contextlib import closing, redirect_stdout
from copy import deepcopy
from datetime import datetime, timedelta
import io
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pytz
import yaml

from trendradar.__main__ import NewsAnalyzer
from trendradar.core import load_config, load_frequency_words
from trendradar.storage.base import RSSData, RSSItem
from trendradar.storage.manager import StorageManager


ROOT = Path(__file__).resolve().parents[1]


class IncrementalWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.stdout = io.StringIO()
        redirect = redirect_stdout(self.stdout)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)
        self.config = load_config(str(ROOT / "config/config.yaml"))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        previous_dir = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, previous_dir)
        self.now = pytz.timezone("Asia/Shanghai").localize(datetime(2026, 9, 5, 9))
        for target in ("trendradar.context.get_configured_time",
                       "trendradar.utils.time.get_configured_time",
                       "trendradar.storage.local.get_configured_time",
                       "trendradar.storage.manager.get_configured_time"):
            clock = patch(target, side_effect=lambda *args, **kwargs: self.now)
            clock.start()
            self.addCleanup(clock.stop)
        environment = patch.dict(os.environ, {"GITHUB_ACTIONS": "true"})
        environment.start()
        self.addCleanup(environment.stop)
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network access"))
        network.start()
        self.addCleanup(network.stop)
        self.data_dir = Path(self.temp.name) / "output"
        self.config.update({
            "REPORT_MODE": "incremental", "CROSS_DAY_DEDUP": True,
            "PLATFORMS": [{"id": "test", "name": "Test"}],
            "REQUEST_INTERVAL": 0, "ENABLE_NOTIFICATION": True,
            "SHOW_VERSION_UPDATE": False,
        })
        for key in ("FEISHU_WEBHOOK_URL", "DINGTALK_WEBHOOK_URL", "WEWORK_WEBHOOK_URL",
                    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "EMAIL_FROM", "EMAIL_PASSWORD",
                    "EMAIL_TO", "NTFY_SERVER_URL", "NTFY_TOPIC", "BARK_URL", "SLACK_WEBHOOK_URL",
                    "GENERIC_WEBHOOK_URL"):
            self.config[key] = ""
        self.config["FEISHU_WEBHOOK_URL"] = "https://example.invalid/test"
        self.config["STORAGE"].update({
            "BACKEND": "local", "FORMATS": {"SQLITE": True, "HTML": True, "TXT": False},
            "LOCAL": {"DATA_DIR": str(self.data_dir), "RETENTION_DAYS": 30, "CLEANUP_INTERVAL_DAYS": 30},
        })
        self.config["SCHEDULE"]["enabled"] = False
        self.config["FILTER"]["METHOD"] = "keyword"
        self.config["AI_ANALYSIS"]["ENABLED"] = False
        self.config["AI_TRANSLATION"]["ENABLED"] = False
        self.config["DISPLAY"]["REGIONS"].update({"HOTLIST": True, "RSS": True, "STANDALONE": False})
        self.config["RSS"].update({
            "ENABLED": True, "FEEDS": [{"id": "feed", "name": "Feed", "url": "https://example.invalid/rss"}],
            "FRESHNESS_FILTER": {"ENABLED": False},
        })
        frequency = Path(self.temp.name) / "words.txt"
        frequency.write_text("[WORD_GROUPS]\n\n[AI]\nAI\n@1\n", encoding="utf-8")
        self.words = load_frequency_words(str(frequency))

    def run_batch(self, hotlist, rss=(), success=True):
        with patch("trendradar.storage.manager._storage_manager", None):
            analyzer = NewsAnalyzer(deepcopy(self.config))
        analyzer.ctx.load_frequency_words = Mock(return_value=self.words)
        analyzer.data_fetcher.crawl_websites = Mock(return_value=({"test": {
            title: {"url": url, "ranks": [rank], "mobileUrl": ""}
            for rank, (title, url) in enumerate(hotlist, 1)
        }}, {"test": "Test"}, []))
        dispatched = Mock(return_value={"feishu": success})
        analyzer.ctx.create_notification_dispatcher = Mock(return_value=SimpleNamespace(dispatch_all=dispatched))
        rss_data = RSSData(
            date=self.now.strftime("%Y-%m-%d"), crawl_time=self.now.strftime("%H-%M"),
            items={"feed": [RSSItem(title=title, feed_id="feed", url=url,
                                   published_at=self.now.isoformat()) for title, url in rss]},
            id_to_name={"feed": "Feed"},
        )
        with patch("trendradar.crawler.rss.fetcher.RSSFetcher.fetch_all", return_value=rss_data):
            if success:
                analyzer.run()
            else:
                with self.assertRaisesRegex(RuntimeError, "所有通知渠道发送失败"):
                    analyzer.run()
        return dispatched

    @staticmethod
    def sent_titles(dispatched, key="report_data"):
        payload = dispatched.call_args.kwargs[key]
        groups = payload["stats"] if key == "report_data" else payload
        return [item["title"] for group in groups for item in group["titles"]]

    def test_next_day_filters_before_limits_for_hotlist_and_rss(self):
        old = [("AI old", "https://example.com/old")]
        first = self.run_batch(old, old)
        self.assertEqual(self.sent_titles(first), ["AI old"])
        self.now += timedelta(days=1)
        next_batch = old + [("AI new", "https://example.com/new")]
        second = self.run_batch(next_batch, next_batch)
        self.assertEqual(self.sent_titles(second), ["AI new"])
        self.assertEqual(self.sent_titles(second, "rss_items"), ["AI new"])
        for kind in ("news", "rss"):
            self.assertTrue((self.data_dir / kind / "2026-09-05.db").exists())
            self.assertTrue((self.data_dir / kind / "2026-09-06.db").exists())

    def test_failed_delivery_retries_next_day_after_articles_leave_sources(self):
        self.run_batch([("AI retry", "https://example.com/retry")],
                       [("AI RSS retry", "https://example.com/rss-retry")], success=False)
        self.now += timedelta(days=1)
        retried = self.run_batch([], [])
        self.assertEqual(self.sent_titles(retried), ["AI retry"])
        self.assertEqual(self.sent_titles(retried, "rss_items"), ["AI RSS retry"])
        self.now += timedelta(hours=1)
        final = self.run_batch([], [])
        final.assert_not_called()
        with closing(sqlite3.connect(self.data_dir / "state.db")) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM pending").fetchone()[0], 0)

    def test_webpage_only_mode_records_success(self):
        self.config["ENABLE_NOTIFICATION"] = False
        item = [("AI webpage", "https://example.com/webpage")]
        first = self.run_batch(item)
        first.assert_not_called()
        self.assertIn("AI webpage", Path("index.html").read_text())
        self.now += timedelta(days=1)
        self.run_batch(item)
        self.assertNotIn("AI webpage", Path("index.html").read_text())

    def test_hidden_region_does_not_acknowledge_pending_content(self):
        self.run_batch([("AI retry", "https://example.com/retry")], success=False)
        self.now += timedelta(days=1)
        self.config["DISPLAY"]["REGIONS"]["HOTLIST"] = False
        hidden = self.run_batch([])
        hidden.assert_not_called()
        self.config["DISPLAY"]["REGIONS"]["HOTLIST"] = True
        self.now += timedelta(hours=1)
        visible = self.run_batch([])
        self.assertEqual(self.sent_titles(visible), ["AI retry"])

    def test_daily_report_still_includes_previously_recommended_news(self):
        item = [("AI summary", "https://example.com/summary")]
        self.run_batch(item)
        self.now += timedelta(days=1)
        self.config["REPORT_MODE"] = "daily"
        summary = self.run_batch(item)
        self.assertEqual(self.sent_titles(summary), ["AI summary"])

    def test_cleanup_keeps_thirty_calendar_days_and_runs_every_thirty_days(self):
        manager = StorageManager(backend_type="local", data_dir=str(self.data_dir),
                                 local_retention_days=30, cleanup_interval_days=30)
        self.addCleanup(manager.cleanup)
        directory = self.data_dir / "news"
        directory.mkdir(parents=True)
        expired = directory / "2026-08-06.db"
        retained = directory / "2026-08-07.db"
        expired.touch()
        retained.touch()
        manager.cleanup_old_data()
        self.assertFalse(expired.exists())
        self.assertTrue(retained.exists())
        expired.touch()
        self.now += timedelta(days=29)
        manager.cleanup_old_data()
        self.assertTrue(expired.exists())
        self.now += timedelta(days=1)
        manager.cleanup_old_data()
        self.assertFalse(expired.exists())
        self.assertFalse(retained.exists())

    def test_workflow_restores_before_crawling_and_saves_before_deployment(self):
        workflow = yaml.load((ROOT / ".github/workflows/crawler.yml").read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(workflow["permissions"]["contents"], "write")
        self.assertEqual(workflow["concurrency"]["cancel-in-progress"], "false")
        self.assertNotIn("github.ref", workflow["concurrency"]["group"])
        job = workflow["jobs"]["crawl"]
        self.assertEqual(job["env"]["STORAGE_BACKEND"], "local")
        steps = job["steps"]
        ids = {step.get("id"): index for index, step in enumerate(steps) if step.get("id")}
        self.assertLess(ids["restore_data"], ids["crawl"])
        self.assertLess(ids["crawl"], ids["save_data"])
        self.assertLess(ids["save_data"], ids["cf_check"])
        self.assertIn("!cancelled()", steps[ids["save_data"]]["if"])
        self.assertNotIn("success()", steps[ids["save_data"]]["if"])


if __name__ == "__main__":
    unittest.main()
