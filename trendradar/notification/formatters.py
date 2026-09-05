# coding=utf-8
"""
通知内容格式转换模块

提供不同推送平台间的格式转换功能
"""

import html
from html.parser import HTMLParser
import re


class _VisibleText(HTMLParser):
    def __init__(self, keep_urls=False):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.keep_urls = keep_urls
        self.url = ""

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == "a" and self.keep_urls:
            self.url = dict(attrs).get("href", "")
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "a" and self.url:
            self.parts.append(" " + self.url)
            self.url = ""


def telegram_text_length(content: str) -> int:
    """按解析后的文字计量，UTF-16 长度也覆盖 emoji 的实体偏移要求。"""
    parser = _VisibleText()
    parser.feed(content)
    return len("".join(parser.parts).encode("utf-16-le")) // 2


def plain_text(content: str) -> str:
    """兼容旧预渲染内容，保留 HTML、Markdown、Slack 链接的 URL。"""
    content = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2 \1", content)
    parser = _VisibleText(keep_urls=True)
    parser.feed(content)
    return html.unescape(strip_markdown("".join(parser.parts)))


def strip_markdown(text: str) -> str:
    """去除文本中的 markdown 语法格式，用于个人微信推送

    Args:
        text: 包含 markdown 格式的文本

    Returns:
        纯文本内容
    """
    # 转换链接 [text](url) -> text url（保留 URL）
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1 \2', text)

    # 先保护 URL，避免后续 markdown 清洗误伤链接中的下划线等字符
    protected_urls: list[str] = []

    def _protect_url(match: re.Match) -> str:
        protected_urls.append(match.group(0))
        return f"@@URLTOKEN{len(protected_urls) - 1}@@"

    text = re.sub(r'https?://[^\s<>\]]+', _protect_url, text)

    # 去除粗体 **text** 或 __text__
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'(?<!\w)__(?!\s)(.+?)(?<!\s)__(?!\w)', r'\1', text)

    # 去除斜体 *text* 或 _text_
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)', r'\1', text)

    # 去除删除线 ~~text~~
    text = re.sub(r'~~(.+?)~~', r'\1', text)

    # 去除图片 ![alt](url) -> alt
    text = re.sub(r'!\[(.+?)\]\(.+?\)', r'\1', text)

    # 去除行内代码 `code`
    text = re.sub(r'`(.+?)`', r'\1', text)

    # 去除引用符号 >
    text = re.sub(r'^>\s*', '', text, flags=re.MULTILINE)

    # 去除标题符号 # ## ### 等
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)

    # 去除水平分割线 --- 或 ***
    text = re.sub(r'^[\-\*]{3,}\s*$', '', text, flags=re.MULTILINE)

    # 去除 HTML 标签 <font color='xxx'>text</font> -> text
    text = re.sub(r'<font[^>]*>(.+?)</font>', r'\1', text)
    text = re.sub(r'<[^>]+>', '', text)

    # 清理多余的空行（保留最多两个连续空行）
    text = re.sub(r'\n{3,}', '\n\n', text)

    # 还原之前保护的 URL
    for idx, url in enumerate(protected_urls):
        text = text.replace(f"@@URLTOKEN{idx}@@", url)

    return text.strip()


def convert_markdown_to_mrkdwn(content: str) -> str:
    """
    将标准 Markdown 转换为 Slack 的 mrkdwn 格式

    转换规则：
    - **粗体** → *粗体*
    - [文本](url) → <url|文本>
    - 保留其他格式（代码块、列表等）

    Args:
        content: Markdown 格式的内容

    Returns:
        Slack mrkdwn 格式的内容
    """
    # 1. 转换链接格式: [文本](url) → <url|文本>
    content = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', content)

    # 2. 转换粗体: **文本** → *文本*
    content = re.sub(r'\*\*([^*]+)\*\*', r'*\1*', content)

    return content
