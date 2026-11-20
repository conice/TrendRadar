# coding=utf-8
"""
杂志风格推送排版

将热榜、RSS、新增、独立展示区与 AI 分析编排为刊物结构：
报头（masthead）→ 栏目（numbered columns）→ 速递 → RSS 精选 → 编者按 → 版权行。

设计约束：
- 不引入新数据，只改变排版
- 每个渠道使用自身支持的最简语法（飞书/钉钉 Markdown、Telegram HTML、Slack mrkdwn）
- 不使用 emoji；新增内容用 `NEW` / `·` 标记
- 复用现有字节上限，批次头部/尾部随批次重复
"""

import re
from datetime import datetime
from typing import Dict, List, Optional

from trendradar.report.helpers import clean_title, html_escape
from trendradar.utils.time import (
    DEFAULT_TIMEZONE,
    convert_time_for_display,
    format_iso_time_friendly,
)


RULE = "━━━━━━━━━━━━━━━━━━━━━━━━"
DEFAULT_REGION_ORDER = ["hotlist", "rss", "new_items", "standalone", "ai_analysis"]

_EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\ufe0f\u200d\u2b50\u2b06\u2b07]"
)
_HEADING_RE = re.compile(
    r"^(?:#{1,6}\s*(.+)|\*\*(.+)\*\*|<b>(.+)</b>|\*(.+)\*|【(.+)】|\[(.+)\])$"
)


class _Format:
    """单个渠道的轻量标记语法。"""

    def __init__(self, channel: str):
        self.channel = channel
        if channel == "feishu":
            self._bold = ("**", "**")
            self._muted = ("<font color='grey'>", "</font>")
            self._link = "markdown"
            self.rule = "---"
        elif channel == "dingtalk":
            self._bold = ("**", "**")
            self._muted = ("", "")
            self._link = "markdown"
            self.rule = "---"
        elif channel == "telegram":
            self._bold = ("<b>", "</b>")
            self._muted = ("<i>", "</i>")
            self._link = "html"
            self.rule = RULE
        elif channel == "slack":
            self._bold = ("*", "*")
            self._muted = ("_", "_")
            self._link = "slack"
            self.rule = RULE
        elif channel == "ntfy":
            self._bold = ("**", "**")
            self._muted = ("_", "_")
            self._link = "markdown"
            self.rule = RULE
        else:  # wework / bark / generic webhook (wework)
            self._bold = ("**", "**")
            self._muted = ("*", "*")
            self._link = "markdown"
            self.rule = RULE

    def bold(self, text: str) -> str:
        if not text:
            return ""
        return f"{self._bold[0]}{text}{self._bold[1]}"

    def muted(self, text: str) -> str:
        if not text:
            return ""
        text = self.escape(text)
        start, end = self._muted
        return f"{start}{text}{end}" if start else text

    def link(self, text: str, url: str) -> str:
        if not url:
            return text
        if self._link == "html":
            return f'<a href="{html_escape(url)}">{html_escape(text)}</a>'
        if self._link == "slack":
            return f"<{url}|{text}>"
        return f"[{text}]({url})"

    def escape(self, text: str) -> str:
        """转义动态文本（仅 Telegram 的 HTML 模式需要）。"""
        return html_escape(text) if self._link == "html" else text

    def section(self, label: str, count: Optional[int] = None) -> str:
        title = f"{label}  ·  {count} 条" if count is not None else label
        return f"{self.rule}\n\n{self.bold(title)}"


class _Block:
    """不可再分的最小排版块；group 为换页时需要重复的栏目标题。"""

    __slots__ = ("text", "group")

    def __init__(self, text: str, group: str = ""):
        self.text = text
        self.group = group


class _Line:
    """标题的两行结构：主标题 + 元信息。"""

    __slots__ = ("headline", "meta", "indent")

    def __init__(self, headline: str, meta: str = "", indent: int = 3):
        self.headline = headline
        self.meta = meta
        self.indent = indent


# ========================================
# 文本工具
# ========================================

def _strip_emoji(text: str) -> str:
    return _EMOJI_RE.sub("", text or "")


def _now_str(now: datetime) -> str:
    return now.strftime("%H:%M")


def _rank_label(title_data: Dict) -> str:
    ranks = title_data.get("ranks") or []
    if not ranks:
        rank = title_data.get("rank")
        ranks = [rank] if rank else []
    if not ranks:
        return ""
    rank = ranks[-1]
    return f"#{rank}" if rank else ""


def _title_line(
    fmt: _Format,
    title_data: Dict,
    index: Optional[int] = None,
    show_source: bool = True,
    show_keyword: bool = False,
) -> _Line:
    title = clean_title(title_data.get("title", "")) or title_data.get("url", "") or ""
    url = title_data.get("mobile_url") or title_data.get("url") or ""
    headline = fmt.bold(fmt.link(title, url) if url else fmt.escape(title))

    prefix = f"{index}  " if index is not None else "·  "
    headline = f"{prefix}{headline}"

    meta_parts = []
    if show_keyword and title_data.get("matched_keyword"):
        meta_parts.append(str(title_data["matched_keyword"]))
    elif show_source and title_data.get("source_name"):
        meta_parts.append(str(title_data["source_name"]))
    rank = _rank_label(title_data)
    if rank:
        meta_parts.append(rank)
    if title_data.get("time_display"):
        meta_parts.append(str(title_data["time_display"]))
    if title_data.get("count", 1) and int(title_data.get("count", 1)) > 1:
        meta_parts.append(f"×{title_data['count']}")

    meta = " · ".join(meta_parts)
    return _Line(headline, fmt.muted(meta) if meta else "", len(prefix))


def _render_line(line: _Line) -> str:
    if not line.meta:
        return line.headline
    return f"{line.headline}\n{' ' * line.indent}{line.meta}"


def _split_text(text: str, limit: int) -> List[str]:
    """按行切分文本，单行超限时按字符切分。"""
    limit = max(limit, 200)
    chunks: List[str] = []
    current: List[str] = []
    size = 0

    for line in text.split("\n"):
        line_size = len(line.encode("utf-8")) + 1
        if line_size > limit:
            if current:
                chunks.append("\n".join(current))
                current = []
                size = 0
            buf = ""
            buf_size = 0
            for char in line:
                char_size = len(char.encode("utf-8"))
                if buf_size + char_size > limit:
                    chunks.append(buf)
                    buf = ""
                    buf_size = 0
                buf += char
                buf_size += char_size
            if buf:
                chunks.append(buf)
            continue

        if size + line_size > limit and current:
            chunks.append("\n".join(current))
            current = []
            size = 0
        current.append(line)
        size += line_size

    if current:
        chunks.append("\n".join(current))
    return chunks or [""]


# ========================================
# 报头与页脚
# ========================================

def _masthead(
    fmt: _Format,
    report_data: Dict,
    report_type: str,
    now: datetime,
    rss_items: Optional[List[Dict]],
    rss_new_items: Optional[List[Dict]],
) -> str:
    stats = report_data.get("stats", [])
    hotlist_matched = sum(len(stat.get("titles", [])) for stat in stats)
    hotlist_total = report_data.get("hotlist_total", hotlist_matched)
    platform_total = report_data.get("platform_total", 0)
    failed_count = len(report_data.get("failed_ids", []))
    rss_matched = report_data.get("rss_matched_count", 0)
    if not rss_matched and rss_items:
        rss_matched = sum(stat.get("count", 0) for stat in rss_items)
    rss_total = report_data.get("rss_total_count", 0)
    hotlist_new = report_data.get("total_new_count", 0)
    rss_new = sum(len(stat.get("titles", [])) for stat in (rss_new_items or []))
    total_new = hotlist_new + rss_new

    body = []

    counts = []
    if hotlist_matched or hotlist_total:
        counts.append(f"今日收录 {hotlist_matched} 条")
    if total_new:
        counts.append(f"新增 {total_new} 条")
    if counts:
        body.append("   ".join(counts))

    metrics = []
    if hotlist_total:
        metrics.append(f"热榜 {hotlist_matched}/{hotlist_total}")
    if platform_total:
        metrics.append(f"平台 {max(0, platform_total - failed_count)}/{platform_total}")
    if rss_total:
        metrics.append(f"RSS {rss_matched}/{rss_total}")
    if metrics:
        body.append("   ".join(metrics))

    if stats:
        body.append(
            "最热 "
            + "  ·  ".join(
                f"{fmt.escape(str(s['word']))} {s.get('count', 0)}" for s in stats[:3]
            )
        )

    lines = [
        "T R E N D R A D A R",
        f"热 点 观 察  ·  {now.strftime('%Y.%m.%d')}  ·  {report_type}",
        "",
    ]
    if body:
        lines.extend([fmt.rule, "", *body, "", fmt.rule])
    else:
        lines.append(fmt.rule)

    return "\n".join(lines)


def _continuation_header(fmt: _Format, now: datetime) -> str:
    return f"T R E N D R A D A R  ·  续  ·  {_now_str(now)}\n\n{fmt.rule}"


def _footer(fmt: _Format, now: datetime, update_info: Optional[Dict]) -> str:
    lines = [fmt.rule, "", f"TRENDRADAR  ·  更新于 {now.strftime('%Y-%m-%d %H:%M')}"]
    if update_info:
        lines.append(
            f"发现新版本 {update_info.get('remote_version', '')}"
            f"（当前 {update_info.get('current_version', '')}）"
        )
    return "\n".join(lines)


# ========================================
# 区块构建
# ========================================

def _hotlist_blocks(
    stats: List[Dict],
    fmt: _Format,
    display_mode: str,
) -> List[_Block]:
    blocks: List[_Block] = []
    max_count = max((stat.get("count", 0) for stat in stats), default=0)
    column = 0

    for stat in stats:
        titles = stat.get("titles", [])
        if not titles:
            continue
        column += 1
        word = stat.get("word", "")
        count = stat.get("count", len(titles))
        heading = f"{column:02d} ── {fmt.escape(str(word))}"
        kicker = "今日最热 · {} 条".format(count) if count == max_count else f"{count} 条"
        group = f"{fmt.bold(heading)}\n{fmt.muted(kicker)}"

        show_source = display_mode == "keyword"
        show_keyword = display_mode == "platform"
        for index, title_data in enumerate(titles, 1):
            line = _render_line(
                _title_line(
                    fmt,
                    title_data,
                    index=index,
                    show_source=show_source,
                    show_keyword=show_keyword,
                )
            )
            if index == 1:
                blocks.append(_Block(f"{group}\n\n{line}", group))
            else:
                blocks.append(_Block(line, group))

    return blocks


def _rss_blocks(rss_items: List[Dict], fmt: _Format) -> List[_Block]:
    blocks: List[_Block] = []
    column = 0

    for stat in rss_items:
        titles = stat.get("titles", [])
        if not titles:
            continue
        column += 1
        heading = f"{column:02d} ── {fmt.escape(str(stat.get('word', '')))}"
        group = f"{fmt.bold(heading)}\n{fmt.muted(f'{len(titles)} 条')}"

        for index, title_data in enumerate(titles, 1):
            line = _render_line(_title_line(fmt, title_data, index=index))
            if index == 1:
                blocks.append(_Block(f"{group}\n\n{line}", group))
            else:
                blocks.append(_Block(line, group))

    return blocks


def _new_titles_blocks(new_titles: List[Dict], fmt: _Format) -> List[_Block]:
    blocks: List[_Block] = []

    for source in new_titles:
        titles = source.get("titles", [])
        if not titles:
            continue
        name = fmt.bold(fmt.escape(str(source.get("source_name") or source.get("source_id") or "")))
        for index, title_data in enumerate(titles):
            line = _render_line(_title_line(fmt, title_data, show_source=False))
            if index == 0:
                blocks.append(_Block(f"{name}\n\n{line}", name))
            else:
                blocks.append(_Block(line, name))

    return blocks


def _rss_new_blocks(rss_new_items: List[Dict], fmt: _Format) -> List[_Block]:
    blocks: List[_Block] = []
    seen = set()

    for stat in rss_new_items:
        for title_data in stat.get("titles", []):
            key = title_data.get("url") or title_data.get("title")
            if key in seen:
                continue
            seen.add(key)
            blocks.append(_Block(_render_line(_title_line(fmt, title_data))))

    return blocks


def _standalone_blocks(
    standalone_data: Dict,
    fmt: _Format,
    timezone: str,
) -> List[_Block]:
    blocks: List[_Block] = []

    for platform in standalone_data.get("platforms", []):
        items = platform.get("items", [])
        if not items:
            continue
        name = fmt.bold(fmt.escape(str(platform.get("name") or platform.get("id") or "")))
        for index, item in enumerate(items, 1):
            title_data = {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "mobile_url": item.get("mobileUrl", ""),
                "ranks": item.get("ranks", []),
                "rank": item.get("rank"),
                "count": item.get("count", 1),
                "time_display": _standalone_time(item),
            }
            line = _render_line(
                _title_line(fmt, title_data, index=index, show_source=False)
            )
            if index == 1:
                blocks.append(_Block(f"{name}\n\n{line}", name))
            else:
                blocks.append(_Block(line, name))

    for feed in standalone_data.get("rss_feeds", []):
        items = feed.get("items", [])
        if not items:
            continue
        name = fmt.bold(fmt.escape(str(feed.get("name") or feed.get("id") or "")))
        for index, item in enumerate(items, 1):
            published = item.get("published_at", "")
            friendly = (
                format_iso_time_friendly(published, timezone, include_date=True)
                if published
                else ""
            )
            meta = " · ".join(part for part in (item.get("author", ""), friendly) if part)
            title = clean_title(item.get("title", "")) or item.get("url", "")
            url = item.get("url", "")
            line = _Line(
                f"{index}  {fmt.bold(fmt.link(title, url) if url else fmt.escape(title))}",
                fmt.muted(meta) if meta else "",
                len(f"{index}  "),
            )
            if index == 1:
                blocks.append(_Block(f"{name}\n\n{_render_line(line)}", name))
            else:
                blocks.append(_Block(_render_line(line), name))

    return blocks


def _standalone_time(item: Dict) -> str:
    first_time = item.get("first_time", "")
    last_time = item.get("last_time", "")
    if first_time and last_time and first_time != last_time:
        return f"{convert_time_for_display(first_time)}~{convert_time_for_display(last_time)}"
    if first_time:
        return convert_time_for_display(first_time)
    return ""


def _ai_blocks(ai_content: str, fmt: _Format) -> List[_Block]:
    text = _strip_emoji(ai_content)
    paragraphs = re.split(r"\n\s*\n", text)
    blocks: List[_Block] = []

    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue

        # 去掉重复的 “AI 热点分析” 大标题
        if "AI 热点分析" in paragraph and len(paragraph) <= 30:
            continue

        lines = paragraph.split("\n")
        converted = []
        for line in lines:
            stripped = line.strip()
            match = _HEADING_RE.match(stripped)
            if match:
                heading = next((g for g in match.groups() if g), "").strip()
                if heading and "AI 热点分析" not in heading:
                    converted.append(fmt.bold(f"— {heading}"))
                continue
            converted.append(line)

        block_text = "\n".join(converted).strip()
        if block_text:
            blocks.append(_Block(block_text))

    return blocks


def _failed_blocks(failed_ids: List, fmt: _Format) -> List[_Block]:
    if not failed_ids:
        return []
    blocks = []
    for failed_id in failed_ids:
        blocks.append(_Block(f"·  {fmt.escape(str(failed_id))}"))
    return blocks


# ========================================
# 打包
# ========================================

def _pack(
    blocks: List[_Block],
    first_header: str,
    cont_header: str,
    footer: str,
    max_bytes: int,
) -> List[str]:
    if not blocks:
        return []

    batches: List[str] = []
    current = first_header
    has_content = False

    for block in blocks:
        reserve = len(cont_header.encode("utf-8")) + len(footer.encode("utf-8")) + 256
        limit = max_bytes - reserve
        pieces = _split_text(block.text, limit) if len(block.text.encode("utf-8")) > limit else [block.text]
        group = block.group

        for piece in pieces:
            candidate = f"{current}\n\n{piece}"
            if len(f"{candidate}\n\n{footer}".encode("utf-8")) <= max_bytes:
                current = candidate
                has_content = True
                continue

            if has_content:
                batches.append(f"{current}\n\n{footer}")

            current = cont_header
            if group:
                current += f"\n\n{group}"
            current += f"\n\n{piece}"
            has_content = True

            if len(f"{current}\n\n{footer}".encode("utf-8")) > max_bytes:
                current = f"{cont_header}\n\n{piece}"

    if has_content:
        batches.append(f"{current}\n\n{footer}")

    return batches


# ========================================
# 入口
# ========================================

def render_magazine_batches(
    report_data: Dict,
    format_type: str = "feishu",
    update_info: Optional[Dict] = None,
    max_bytes: int = 4000,
    mode: str = "daily",
    region_order: Optional[List[str]] = None,
    get_time_func=None,
    rss_items: Optional[List[Dict]] = None,
    rss_new_items: Optional[List[Dict]] = None,
    timezone: str = DEFAULT_TIMEZONE,
    display_mode: str = "keyword",
    ai_content: Optional[str] = None,
    standalone_data: Optional[Dict] = None,
    rank_threshold: int = 10,
    report_type: str = "热点分析报告",
    show_new_section: bool = True,
    **_,
) -> List[str]:
    """生成杂志风格的推送批次。"""
    fmt = _Format(format_type)
    now = get_time_func() if get_time_func else datetime.now()
    region_order = region_order or DEFAULT_REGION_ORDER

    first_header = _masthead(fmt, report_data, report_type, now, rss_items, rss_new_items)
    cont_header = _continuation_header(fmt, now)
    footer = _footer(fmt, now, update_info)

    blocks: List[_Block] = []

    for region in region_order:
        if region == "hotlist":
            stats = report_data.get("stats", [])
            if stats:
                blocks.extend(_hotlist_blocks(stats, fmt, display_mode))

        elif region == "rss":
            if rss_items:
                section = fmt.section(
                    "R S S 精 选",
                    sum(len(stat.get("titles", [])) for stat in rss_items),
                )
                rss_blocks = _rss_blocks(rss_items, fmt)
                if rss_blocks:
                    rss_blocks[0].text = f"{section}\n\n{rss_blocks[0].text}"
                    blocks.extend(rss_blocks)

        elif region == "new_items":
            if show_new_section:
                new_titles = report_data.get("new_titles", [])
                new_blocks = _new_titles_blocks(new_titles, fmt)
                if new_blocks:
                    count = sum(len(source.get("titles", [])) for source in new_titles)
                    section = fmt.section("速 递", count)
                    new_blocks[0].text = f"{section}\n\n{new_blocks[0].text}"
                    blocks.extend(new_blocks)

                rss_new_blocks = _rss_new_blocks(rss_new_items or [], fmt)
                if rss_new_blocks:
                    count = len(rss_new_blocks)
                    section = fmt.section("RSS 速 递", count)
                    rss_new_blocks[0].text = f"{section}\n\n{rss_new_blocks[0].text}"
                    blocks.extend(rss_new_blocks)

        elif region == "standalone":
            if standalone_data:
                standalone_blocks = _standalone_blocks(standalone_data, fmt, timezone)
                if standalone_blocks:
                    count = sum(
                        len(block.get("items", []))
                        for key in ("platforms", "rss_feeds")
                        for block in standalone_data.get(key, [])
                    )
                    section = fmt.section("独 立 展 示", count)
                    standalone_blocks[0].text = f"{section}\n\n{standalone_blocks[0].text}"
                    blocks.extend(standalone_blocks)

        elif region == "ai_analysis":
            if ai_content:
                ai_blocks = _ai_blocks(ai_content, fmt)
                if ai_blocks:
                    section = fmt.section("编 者 按")
                    ai_blocks[0].text = f"{section}\n\n{ai_blocks[0].text}"
                    blocks.extend(ai_blocks)

    failed_blocks = _failed_blocks(report_data.get("failed_ids", []), fmt)
    if failed_blocks:
        section = fmt.section("数 据 缺 口")
        failed_blocks[0].text = f"{section}\n\n{failed_blocks[0].text}"
        blocks.extend(failed_blocks)

    if not blocks:
        if mode == "incremental":
            message = "增量模式下暂无新增内容"
        elif mode == "current":
            message = "当前榜单模式下暂无匹配内容"
        else:
            message = "暂无匹配内容"
        return [f"{first_header}\n\n{message}\n\n{footer}"]

    # 报头已以分割线结尾，首个区块若也以分割线开头则去掉，避免连续两条
    rule_prefix = f"{fmt.rule}\n\n"
    if blocks[0].text.startswith(rule_prefix):
        blocks[0].text = blocks[0].text[len(rule_prefix):]

    return _pack(blocks, first_header, cont_header, footer, max_bytes)
