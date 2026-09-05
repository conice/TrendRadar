"""把同一条新闻渲染为各渠道支持的文字格式。"""

import html
import re
from typing import Dict
from urllib.parse import quote, urlsplit

from trendradar.report.helpers import clean_title
from trendradar.report.presentation import news_metadata
from trendradar.utils.time import DEFAULT_TIMEZONE


class MessageStyle:
    """仅封装渠道语法；模式、排名和时间的含义由 presentation 决定。"""

    def __init__(self, platform: str):
        self.platform = platform
        self.plain = platform in {"plain", "wework_text", "feishu_text", "generic_text"}
        self.line_break = "\n" if self.plain or platform in {"telegram", "slack"} else "  \n"

    def text(self, value: str) -> str:
        value = str(value)
        if self.plain:
            return value
        if self.platform in {"telegram", "html"}:
            return html.escape(value, quote=False)
        if self.platform == "slack":
            return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        value = html.escape(value, quote=False)
        return re.sub(r"([\\`*_\[\]])", r"\\\1", value)

    def bold(self, value: str) -> str:
        text = self.text(value)
        if self.plain:
            return f"【{text}】"
        if self.platform in {"telegram", "html"}:
            return f"<b>{text}</b>"
        marker = "*" if self.platform == "slack" else "**"
        return f"{marker}{text}{marker}"

    def muted(self, value: str) -> str:
        text = self.text(value)
        if self.platform == "feishu":
            return f"<font color='grey'>{text}</font>"
        if self.platform == "html":
            return f'<span class="news-meta">{text}</span>'
        return text

    def link(self, title: str, url: str) -> str:
        text = self.text(title)
        try:
            supported = bool(url) and urlsplit(url).scheme.lower() in {"http", "https"}
        except ValueError:
            supported = False
        if not supported:
            return text
        if self.plain:
            return text
        if self.platform in {"telegram", "html"}:
            return f'<a href="{html.escape(url, quote=True)}">{text}</a>'
        # 链接目标中的括号、空格等需要编码，不能截断或变成 Markdown 语法。
        target = quote(url, safe=":/?#@!$&'*,;=+%~._-")
        if self.platform == "slack":
            return f"<{target.replace('&', '&amp;')}|{text.replace('|', '｜')}>"
        return f"[{text}]({target})"


def format_title_for_platform(
    platform: str,
    title_data: Dict,
    show_source: bool = True,
    show_keyword: bool = False,
    *,
    mode: str = "daily",
    kind: str = "hotlist",
    show_new: bool = True,
    timezone: str = DEFAULT_TIMEZONE,
    reference_date: str = "",
) -> str:
    """标题在前、辅助信息另起一行；纯文本渠道另列完整原文链接。"""
    style = MessageStyle(platform)
    url = title_data.get("mobile_url") or title_data.get("mobileUrl") or title_data.get("url", "")
    title = clean_title(title_data.get("title", "")) or url
    lines = [style.link(title, url)]
    metadata = news_metadata(
        title_data, mode, kind=kind, show_source=show_source,
        show_keyword=show_keyword, show_new=show_new,
        timezone=timezone, reference_date=reference_date,
    )
    if metadata:
        lines.append(style.muted(" · ".join(metadata)))
    if style.plain and url:
        lines.append(url)
    return style.line_break.join(lines)
