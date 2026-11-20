"""Magazine-style notification rendering: byte limits, headings and emoji removal."""

import unittest
from datetime import datetime

from trendradar.notification.magazine import render_magazine_batches


def _title(index: int) -> dict:
    return {
        "title": f"测试标题 {index}",
        "source_name": "来源",
        "ranks": [index],
        "time_display": "12:00",
        "count": 1,
        "url": f"https://example.com/{index}",
        "mobile_url": "",
        "rank_threshold": 5,
    }


def _report_data():
    return {
        "stats": [
            {
                "word": f"关键词{group}",
                "count": 5 - group,
                "titles": [_title(group * 10 + i) for i in range(4)],
            }
            for group in range(4)
        ],
        "new_titles": [{"source_name": "来源", "titles": [_title(99)]}],
        "failed_ids": ["failed-source"],
        "total_new_count": 1,
        "hotlist_total": 100,
        "platform_total": 10,
        "rss_matched_count": 1,
        "rss_total_count": 10,
    }


class MagazineRenderTests(unittest.TestCase):
    def _render(self, channel="telegram", max_bytes=900, ai_content=None):
        return render_magazine_batches(
            _report_data(),
            format_type=channel,
            max_bytes=max_bytes,
            mode="current",
            get_time_func=lambda: datetime(2026, 11, 20, 12, 33),
            rss_items=[{"word": "AI", "count": 1, "titles": [_title(1)]}],
            ai_content=ai_content,
            report_type="当前榜单",
        )

    def test_batches_respect_byte_limit(self):
        for channel, limit in (("telegram", 900), ("feishu", 1200), ("dingtalk", 1000)):
            with self.subTest(channel=channel):
                batches = self._render(channel=channel, max_bytes=limit)
                self.assertGreater(len(batches), 1)
                for batch in batches:
                    self.assertLessEqual(len(batch.encode("utf-8")), limit)

    def test_masthead_and_continuation_header(self):
        batches = self._render(max_bytes=600)
        self.assertIn("T R E N D R A D A R", batches[0])
        self.assertIn("续", batches[1])

    def test_ai_headings_lose_markup_and_emoji(self):
        batches = self._render(
            channel="dingtalk",
            max_bytes=19000,
            ai_content="### ✨ AI 热点分析\n\n#### 核心热点态势\n1. 测试",
        )
        content = "\n".join(batches)
        self.assertNotIn("####", content)
        self.assertNotIn("✨", content)
        self.assertIn("核心热点态势", content)

    def test_no_emoji_in_rendered_output(self):
        content = "\n".join(
            self._render(
                channel="telegram",
                max_bytes=3800,
                ai_content="**✨ AI 热点分析**\n\n**核心热点态势**\n1. 测试",
            )
        )
        for emoji in ("✨", "🔥", "📌", "🆕", "📊", "⚠️"):
            self.assertNotIn(emoji, content)


if __name__ == "__main__":
    unittest.main()
