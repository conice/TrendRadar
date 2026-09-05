"""按完整条目组织通知，依各渠道长度限制生成带上下文的续条。"""

from datetime import datetime
from typing import Callable, Dict, List, Optional

from trendradar.notification.document import (
    DEFAULT_REGION_ORDER, Entry, Group, build_groups, news_counts,
)
from trendradar.notification.formatters import telegram_text_length
from trendradar.report.formatter import MessageStyle
from trendradar.report.helpers import clean_title
from trendradar.report.presentation import news_metadata, report_label
from trendradar.utils.time import DEFAULT_TIMEZONE


DEFAULT_BATCH_SIZES = {"dingtalk": 20000, "feishu": 29000, "ntfy": 3800, "default": 4000}


def _text_parts(text, render, fits):
    """在渲染之前切分文本，避免切断 HTML 实体、标签或多字节字符。"""
    while text:
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if fits(render(text[:middle])):
                low = middle
            else:
                high = middle - 1
        if not low:
            raise ValueError("消息上限不足以容纳标题、分组和完整原文链接")
        end = low
        if end < len(text):
            # 优先在句子或单词末尾拆开；无分隔符的长文字仍可按字符安全续接。
            for position in range(end - 1, max(0, end // 2) - 1, -1):
                if text[position] in "\n 。！？；.!?;":
                    end = position + 1
                    break
        yield render(text[:end])
        text = text[end:]


def _entry_parts(entry, style, timezone, reference_date, fits):
    if entry.item is None:
        yield from _text_parts(entry.text, lambda text: style.text(text).replace("\n", style.line_break), fits)
        return

    item = entry.item
    url = item.get("mobile_url") or item.get("mobileUrl") or item.get("url", "")
    title = clean_title(item.get("title", "")) or url
    part_number = 0

    def render_title(text):
        number = str(entry.index) if part_number == 0 else f"{entry.index}（续）"
        body = f"{number}. {style.link(text, url)}"
        if style.plain and url:
            body += style.line_break + url
        return body

    for part in _text_parts(title, render_title, fits):
        yield part
        part_number += 1
    metadata = news_metadata(
        item, entry.mode, kind=entry.kind, show_source=entry.show_source,
        show_keyword=entry.show_keyword, show_new=entry.show_new,
        timezone=timezone, reference_date=reference_date,
    )
    if metadata:
        yield from _text_parts(
            " · ".join(metadata),
            lambda text: f"{entry.index}. 辅助信息{style.line_break}{style.muted(text)}", fits,
        )


def _pack_groups(groups, style, header, limit, measure, timezone, reference_date, total_pages):
    batches = []
    current = header(1, total_pages)
    has_content = False
    current_group = None
    current_section = None

    def context(group, continued, new_page=False):
        parts = []
        if new_page or current_section != group.section:
            parts.append(style.bold(group.section))
        if new_page or current_group is not group:
            name = group.heading(continued)
            if name:
                parts.append(style.bold(name))
        return "\n\n".join(parts)

    def candidate(text, group, continued, new_page=False):
        prefix = context(group, continued, new_page)
        base = header(len(batches) + 1, total_pages) if new_page else current
        return base + "\n\n" + (prefix + "\n\n" if prefix else "") + text

    def append(text, group, continued):
        nonlocal current, has_content, current_group, current_section
        # 正常条目完整落在同一条消息中；跨页时重建区域和分组上下文。
        proposal = candidate(text, group, continued)
        if measure(proposal) > limit and has_content:
            batches.append(current)
            current = header(len(batches) + 1, total_pages)
            has_content = False
            current_group = current_section = None
            proposal = candidate(text, group, continued, new_page=True)
        if measure(proposal) > limit:
            return False
        current = proposal
        has_content = True
        current_group, current_section = group, group.section
        return True

    for group in groups:
        for index, entry in enumerate(group.entries):
            rendered = entry.render(style, timezone, reference_date)
            if append(rendered, group, index > 0):
                continue

            def fits(text):
                # 超长条目的所有片段都为续条页码和分组标题留出空间。
                prefixes = [header(len(batches) + 1, total_pages), header(max(total_pages, 9999), max(total_pages, 9999))]
                contexts = [context(group, index > 0, True), context(group, True, True)]
                return all(measure(prefix + "\n\n" + labels + "\n\n" + text) <= limit
                           for prefix in prefixes for labels in contexts)

            for part_index, part in enumerate(_entry_parts(entry, style, timezone, reference_date, fits)):
                if not append(part, group, index > 0 or part_index > 0):
                    raise ValueError("单条内容无法在当前渠道的消息上限内安全发送")
    if has_content:
        batches.append(current)
    return batches


def split_content_into_batches(
    report_data: Dict,
    format_type: str,
    update_info: Optional[Dict] = None,
    max_bytes: Optional[int] = None,
    mode: str = "daily",
    batch_sizes: Optional[Dict[str, int]] = None,
    feishu_separator: str = "---",
    region_order: Optional[List[str]] = None,
    get_time_func: Optional[Callable[[], datetime]] = None,
    rss_items: Optional[list] = None,
    rss_new_items: Optional[list] = None,
    timezone: str = DEFAULT_TIMEZONE,
    display_mode: str = "keyword",
    ai_content: Optional[str] = None,
    standalone_data: Optional[Dict] = None,
    rank_threshold: int = 10,
    ai_stats: Optional[Dict] = None,
    report_type: str = "热点分析报告",
    show_new_section: bool = True,
    *,
    ai_analysis=None,
    content_measure: Optional[Callable[[str], int]] = None,
) -> List[str]:
    """返回可直接发送的完整消息，已包含页码，不需要再调用 add_batch_headers。

    max_bytes 为历史参数名：默认按 UTF-8 字节计量，Telegram 按解析后的
    文字长度计量。content_measure 可计量整个请求体（例如 Bark / 飞书卡片）。
    无法保留完整链接的极端输入会报错，调用方不应将其标记为发送成功。
    """
    style = MessageStyle(format_type)
    sizes = {**DEFAULT_BATCH_SIZES, **(batch_sizes or {})}
    limit = max_bytes if max_bytes is not None else sizes.get(format_type, sizes["default"])
    if limit <= 0:
        raise ValueError("消息大小上限必须大于 0")
    measure = content_measure or (telegram_text_length if format_type == "telegram" else lambda text: len(text.encode("utf-8")))
    if format_type == "telegram":
        limit = min(limit, 4096)
    now = get_time_func() if get_time_func else datetime.now()
    reference_date = now.strftime("%Y-%m-%d")
    groups = build_groups(
        report_data, mode, DEFAULT_REGION_ORDER if region_order is None else region_order,
        rss_items=rss_items, rss_new_items=rss_new_items, standalone_data=standalone_data,
        ai_analysis=ai_analysis, ai_content=ai_content, display_mode=display_mode,
        show_new_section=show_new_section, update_info=update_info,
    )
    primary_count, standalone_count = news_counts(groups)
    if not groups:
        empty_text = {"daily": "当日暂无匹配新闻", "current": "当前暂无匹配新闻",
                      "incremental": "本次暂无新增匹配新闻"}.get(mode, "暂无匹配新闻")
        groups = [Group("新闻", entries=[Entry(text=empty_text)])]

    title = report_label(mode, report_type)
    if mode == "daily":
        time_text = now.strftime("%m-%d · 截至 %H:%M")
    elif mode == "current" and report_data.get("snapshot_time"):
        time_text = f"快照 {report_data['snapshot_time']}"
    else:
        time_text = now.strftime("%m-%d %H:%M")
    counts = []
    if primary_count:
        counts.append(f"{'本次推送' if mode == 'incremental' else '展示'} {primary_count} 条")
    if standalone_count:
        counts.append(f"独立展示 {standalone_count} 条")
    if not counts and (ai_analysis or ai_content):
        counts.append("AI 解读")
    if not counts:
        counts.append("展示 0 条")

    def header(page, total):
        suffix = f"（{page}/{total}）" if total > 1 else ""
        details = time_text + (" · " + " · ".join(counts) if page == 1 else "")
        return style.bold(f"TrendRadar · {title}{suffix}") + style.line_break + style.text(details)

    total_pages = 1
    while True:
        batches = _pack_groups(groups, style, header, limit, measure, timezone, reference_date, total_pages)
        if len(batches) == total_pages:
            return batches
        # 页数改变会影响头部长度；重新排版直至页码与最终条数一致。
        total_pages = len(batches)
