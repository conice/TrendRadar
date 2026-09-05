from contextlib import redirect_stdout
from functools import partial
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from trendradar.ai.analyzer import AIAnalysisResult
from trendradar.notification import senders
from trendradar.notification.dispatcher import NotificationDispatcher
from trendradar.notification.splitter import split_content_into_batches
from trendradar.report.html import render_html_content

from test_notification_format import NOW, news, report, telegram_plain


class NotificationDeliveryFormatTests(unittest.TestCase):
    def setUp(self):
        output = redirect_stdout(io.StringIO())
        output.__enter__()
        self.addCleanup(output.__exit__, None, None, None)
        sleep = patch("trendradar.notification.senders.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("Unexpected network access"))
        network.start()
        self.addCleanup(network.stop)
        self.split = partial(split_content_into_batches, get_time_func=lambda: NOW)

    def test_every_sender_sends_native_format_within_its_real_budget(self):
        data = report([news(index, title=f"标题 {index:02d} " + "内容" * 20) for index in range(1, 13)])
        cases = (
            ("feishu", senders.send_to_feishu, {"webhook_url": "https://open.feishu.cn/test"}),
            ("feishu_text", senders.send_to_feishu, {"webhook_url": "https://www.feishu.cn/test"}),
            ("dingtalk", senders.send_to_dingtalk, {"webhook_url": "https://example.test/hook"}),
            ("wework", senders.send_to_wework, {"webhook_url": "https://example.test/hook"}),
            ("wework_text", senders.send_to_wework, {"webhook_url": "https://example.test/hook", "msg_type": "text"}),
            ("telegram", senders.send_to_telegram, {"bot_token": "test-token", "chat_id": "test-chat"}),
            ("slack", senders.send_to_slack, {"webhook_url": "https://example.test/hook"}),
            ("bark", senders.send_to_bark, {"bark_url": "https://example.test/device-key"}),
            ("ntfy", senders.send_to_ntfy, {"server_url": "https://example.test", "topic": "test", "token": None}),
            ("generic_webhook", senders.send_to_generic_webhook, {"webhook_url": "https://example.test", "payload_template": ""}),
            ("generic_text", senders.send_to_generic_webhook, {"webhook_url": "https://example.test", "payload_template": "", "message_format": "text"}),
        )
        for channel, send, settings in cases:
            with self.subTest(channel=channel):
                response = Mock(status_code=200, text="ok")
                response.json.return_value = {"code": 200 if channel == "bark" else 0, "errcode": 0, "ok": True}
                with patch("trendradar.notification.senders.requests.post", return_value=response) as post:
                    self.assertTrue(send(report_data=data, report_type="当前榜单", mode="current",
                                         batch_size=1000, split_content_func=self.split, **settings))
                self.assertGreater(post.call_count, 1)
                contents = []
                for call in post.call_args_list:
                    raw = call.kwargs["data"]
                    self.assertIsInstance(raw, bytes)
                    if channel == "ntfy":
                        content = raw.decode("utf-8")
                        self.assertIn("09-06 14:30", call.kwargs["headers"]["Title"])
                        call.kwargs["headers"]["Title"].encode("ascii")
                    else:
                        self.assertEqual(call.kwargs["headers"]["Content-Type"], "application/json")
                        payload = json.loads(raw)
                        if channel == "feishu":
                            content = payload["card"]["body"]["elements"][0]["content"]
                        elif channel == "feishu_text":
                            content = payload["content"]["text"]
                        elif channel == "dingtalk":
                            content = payload["markdown"]["text"]
                        elif channel == "wework":
                            content = payload["markdown"]["content"]
                        elif channel == "wework_text":
                            content = payload["text"]["content"]
                        elif channel == "bark":
                            content = payload["markdown"]
                            self.assertIn("09-06 14:30", payload["title"])
                        elif channel.startswith("generic"):
                            content = payload["content"]
                        else:
                            content = payload["text"]
                        if channel == "telegram":
                            self.assertEqual(payload["parse_mode"], "HTML")
                            self.assertTrue(payload["disable_web_page_preview"])
                    contents.append(content)
                    self.assertIn("当前第 8", content)
                    if channel == "telegram":
                        self.assertLessEqual(len(telegram_plain(content).encode("utf-16-le")) // 2, 1000)
                    elif channel in {"feishu", "feishu_text", "dingtalk", "bark", "generic_webhook", "generic_text"}:
                        self.assertLessEqual(len(raw), 1000)
                    else:
                        self.assertLessEqual(len(content.encode("utf-8")), 1000)
                combined = "\n".join(contents)
                for index in range(1, 13):
                    self.assertEqual(combined.count(f"标题 {index:02d}"), 1)
                if channel in {"feishu_text", "wework_text", "generic_text"}:
                    self.assertIn(news()["url"], combined)
                    self.assertNotIn("**", combined)
                    self.assertNotIn("<font", combined)

    def test_generic_payload_measures_template_overhead_and_does_not_expand_news_placeholders(self):
        template = json.dumps({"wrapper": {"title": "{title}", "text": "{content}", "padding": "附加" * 60}})
        data = report([news(index, title=f"新闻 {index} 保留 {{title}} 和 {{content}}") for index in range(1, 10)])
        response = Mock(status_code=200)
        with patch("trendradar.notification.senders.requests.post", return_value=response) as post:
            self.assertTrue(senders.send_to_generic_webhook(
                "https://example.test", template, data, "当日汇总", batch_size=1100,
                split_content_func=self.split, message_format="text",
            ))
        for call in post.call_args_list:
            raw = call.kwargs["data"]
            self.assertLessEqual(len(raw), 1100)
            self.assertIn("保留 {title} 和 {content}", json.loads(raw)["wrapper"]["text"])

    def test_unrenderable_channel_does_not_prevent_other_channels(self):
        config = {"ENABLE_NOTIFICATION": True, "WEWORK_WEBHOOK_URL": "https://example.test/hook",
                  "WEWORK_MSG_TYPE": "text", "TELEGRAM_BOT_TOKEN": "test-token", "TELEGRAM_CHAT_ID": "test-chat",
                  "NTFY_SERVER_URL": "https://example.test", "NTFY_TOPIC": "test",
                  "GENERIC_WEBHOOK_URL": "https://example.test", "GENERIC_WEBHOOK_FORMAT": "text",
                  "EMAIL_FROM": "reader@example.test", "EMAIL_TO": "inbox@example.test", "EMAIL_PASSWORD": "test"}
        dispatcher = NotificationDispatcher(config, lambda: NOW, self.split)
        data = report([news(url="https://example.test/" + "x" * 5000)])
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True}
        with patch("trendradar.notification.senders.requests.post", return_value=response) as post, \
                patch.object(dispatcher, "_send_email", return_value=True) as email:
            result = dispatcher.dispatch_all(data, "当前榜单", mode="current")
        self.assertFalse(result["wework"])
        self.assertFalse(result["ntfy"])
        self.assertFalse(result["generic_webhook"])
        self.assertTrue(result["telegram"])
        self.assertTrue(result["email"])
        email.assert_called_once()
        self.assertEqual(post.call_count, 1)

    def test_email_contains_the_report_in_both_html_and_plaintext(self):
        config = {"EMAIL_FROM": "reader@example.test", "EMAIL_PASSWORD": "test-password",
                  "EMAIL_TO": "inbox@example.test", "EMAIL_SMTP_SERVER": "smtp.example.test", "EMAIL_SMTP_PORT": 465}
        data = report()
        analysis = AIAnalysisResult(success=True, ai_mode="daily", core_trends="当日分析内容", hotlist_analyzed=50)
        content = render_html_content(data, 100, "current", get_time_func=lambda: NOW, ai_analysis=analysis)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.html"
            path.write_text(content, encoding="utf-8")
            dispatcher = NotificationDispatcher(config, lambda: NOW, self.split)
            with patch("trendradar.notification.senders.smtplib.SMTP_SSL") as smtp:
                result = dispatcher.dispatch_all(data, "当前榜单", mode="current",
                                                 html_file_path=str(path), ai_analysis=analysis)
            self.assertTrue(result["email"])
            message = smtp.return_value.send_message.call_args.args[0]
        self.assertIn("TrendRadar · 当前榜单 · 09-06 14:35", str(message["Subject"]))
        parts = message.get_payload()
        self.assertEqual([part.get_content_type() for part in parts], ["text/plain", "text/html"])
        for part in parts:
            body = part.get_payload(decode=True).decode("utf-8")
            self.assertIn("模型与芯片更新", body)
            self.assertIn("当前第 8", body)
            self.assertIn("AI 解读 · 当日数据", body)
        self.assertIn(news()["url"], parts[0].get_payload(decode=True).decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
