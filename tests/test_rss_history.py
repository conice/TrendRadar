"""RSS new-item detection compares URLs within the configured history window."""

import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from trendradar.storage.base import RSSData, RSSItem
from trendradar.storage.local import LocalStorageBackend
from trendradar.storage.manager import StorageManager
from trendradar.storage.remote import RemoteStorageBackend


def _rss_data(date, crawl_time, names, feed_id="feed"):
    return RSSData(
        date=date,
        crawl_time=crawl_time,
        items={feed_id: [
            RSSItem(title=name, url=f"https://example.com/{name}", feed_id=feed_id)
            for name in names
        ]},
        id_to_name={feed_id: feed_id},
    )


class RssHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.backend = LocalStorageBackend(
            data_dir=self.directory.name, enable_txt=False, enable_html=False,
        )
        self.addCleanup(self._close_connections)

    def _close_connections(self):
        for connection in self.backend._db_connections.values():
            connection.close()

    def save(self, data):
        self.assertTrue(self.backend.save_rss_data(data))
        return data

    def test_previous_day_urls_are_not_new_after_saving_current_batch(self):
        self.save(_rss_data("2026-11-05", "23:00", ["old", "another"]))
        current = self.save(_rss_data("2026-11-06", "08:00", ["old", "another", "new"]))

        result = self.backend.detect_new_rss_items(current, history_days=30)

        self.assertEqual(result, {"feed": [current.items["feed"][2]]})

    def test_same_day_earlier_batch_is_history_but_current_batch_is_not(self):
        self.save(_rss_data("2026-11-06", "08:00", ["old"]))
        current = self.save(_rss_data("2026-11-06", "09:00", ["old", "new"]))

        self.assertEqual(
            self.backend.detect_new_rss_items(current, history_days=30),
            {"feed": [current.items["feed"][1]]},
        )

    def test_history_window_includes_today_and_previous_29_days(self):
        self.save(_rss_data("2026-10-07", "23:00", ["outside"]))
        self.save(_rss_data("2026-10-08", "23:00", ["inside"]))
        current = self.save(_rss_data("2026-11-06", "08:00", ["outside", "inside"]))

        self.assertEqual(
            self.backend.detect_new_rss_items(current, history_days=30),
            {"feed": [current.items["feed"][0]]},
        )

    def test_missing_day_does_not_stop_history_search(self):
        self.save(_rss_data("2026-11-04", "23:00", ["old"]))
        current = self.save(_rss_data("2026-11-06", "08:00", ["old", "new"]))

        self.assertEqual(
            self.backend.detect_new_rss_items(current, history_days=3),
            {"feed": [current.items["feed"][1]]},
        )

    def test_first_crawl_with_no_history_returns_all_items(self):
        current = _rss_data("2026-11-06", "08:00", ["first", "second"])

        self.assertEqual(self.backend.detect_new_rss_items(current, history_days=3), current.items)
        self.save(current)
        self.assertEqual(self.backend.detect_new_rss_items(current, history_days=3), current.items)

    def test_same_url_in_different_feed_remains_new(self):
        self.save(_rss_data("2026-11-05", "23:00", ["same"], feed_id="other"))
        current = self.save(_rss_data("2026-11-06", "08:00", ["same"]))

        self.assertEqual(self.backend.detect_new_rss_items(current, history_days=2), current.items)

    def test_title_change_does_not_make_same_url_new(self):
        self.save(_rss_data("2026-11-05", "23:00", ["old"]))
        current = _rss_data("2026-11-06", "08:00", ["old"])
        current.items["feed"][0].title = "Updated title"
        self.save(current)

        self.assertEqual(self.backend.detect_new_rss_items(current, history_days=2), {})

    def test_default_window_only_compares_today(self):
        self.save(_rss_data("2026-11-05", "23:00", ["old"]))
        current = self.save(_rss_data("2026-11-06", "08:00", ["old"]))

        self.assertEqual(self.backend.detect_new_rss_items(current), current.items)

    def test_invalid_or_nonpositive_windows_fall_back_to_one_day(self):
        self.save(_rss_data("2026-11-05", "23:00", ["yesterday"]))
        self.save(_rss_data("2026-11-06", "07:00", ["earlier"]))
        current = self.save(_rss_data("2026-11-06", "08:00", ["yesterday", "earlier", "new"]))

        for history_days in (None, "invalid", 0, -1):
            with self.subTest(history_days=history_days):
                self.assertEqual(
                    self.backend.detect_new_rss_items(current, history_days=history_days),
                    {"feed": [current.items["feed"][0], current.items["feed"][2]]},
                )

    def test_manager_and_backends_forward_history_window(self):
        current = _rss_data("2026-11-06", "08:00", ["new"])
        manager = StorageManager()
        for backend_class in (LocalStorageBackend, RemoteStorageBackend):
            with self.subTest(backend=backend_class.__name__):
                manager._backend = backend_class.__new__(backend_class)
                with patch.object(manager._backend, "_detect_new_rss_items_impl", return_value=current.items) as detect:
                    self.assertEqual(manager.detect_new_rss_items(current, history_days=30), current.items)
                    detect.assert_called_once_with(current, history_days=30)


if __name__ == "__main__":
    unittest.main()
