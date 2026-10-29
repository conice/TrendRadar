# coding=utf-8
"""
Telegram 消息渲染模块

视觉规则参考 Apple 的信息层级（iOS 通知、Apple News、设置列表）：
- 用字重区分层级：粗体标题，斜体承载次要信息，不用方括号和等宽块
- 每个区块只有一个图标，条目本身不加 emoji
- 条目为「标题链接 + 一行元信息」，元信息用 · 分隔
- 用留白分组，不画分割线

Telegram HTML 模式下标签不能跨消息，所以每个渲染单元自身标签闭合，
分批只发生在单元之间。
"""

import re
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from trendradar.notification.batch import truncate_at_line_boundary
from trendradar.report.helpers import calculate_rank_trend, clean_title, html_escape
from trendradar.utils.time import (
    DEFAULT_TIMEZONE,
    convert_time_for_display,
    format_iso_time_friendly,
)

_WEEKDAYS = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
_DOT = " · "

# 渲染单元：(前置空行数, 文本, 所属区块标题, 所属分组标题)
# 跨批次时，续页会先补上区块和分组标题，读者不会丢失上下文
Unit = Tuple[int, str, Optional[str], Optional[str]]


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


def _normalize_time(text: str) -> str:
    """'[08:00 ~ 10:30]' → '08:00–10:30'"""
    text = (text or "").strip().strip("[]").strip()
    return re.sub(r"\s*~\s*", "–", text)


def _rank_text(ranks, threshold: int, rank_timeline=None) -> str:
    if not ranks:
        return ""
    unique = sorted(set(ranks))
    low, high = unique[0], unique[-1]
    text = f"#{low}" if low == high else f"#{low}–{high}"
    if low <= threshold:
        text = f"<b>{text}</b>"
    trend = calculate_rank_trend(rank_timeline, ranks)
    return text + {"up": " ↑", "down": " ↓"}.get(trend, "")


def _item(
    title: str,
    url: str,
    meta_parts: List[str],
    is_new: bool = False,
) -> str:
    """一条新闻：标题链接 + 斜体元信息行（meta_parts 需已转义）"""
    title = html_escape(clean_title(title) or url or "")
    head = f'<a href="{html_escape(url)}">{title}</a>' if url else title
    parts = [p for p in meta_parts if p]
    if is_new:
        parts.insert(0, "<b>新</b>")
    if not parts:
        return head
    return f"{head}\n<i>{_DOT.join(parts)}</i>"


def _title_item(t: Dict, show_source: bool, show_keyword: bool = False) -> str:
    """热榜/RSS 统计条目（title_data 结构，见 report/formatter.py）"""
    meta = []
    if show_source and t.get("source_name"):
        meta.append(html_escape(t["source_name"]))
    elif show_keyword and t.get("matched_keyword"):
        meta.append(html_escape(t["matched_keyword"]))
    meta.append(_rank_text(t.get("ranks"), t.get("rank_threshold", 0), t.get("rank_timeline")))
    meta.append(html_escape(_normalize_time(t.get("time_display", ""))))
    if t.get("count", 1) > 1:
        meta.append(f"{t['count']} 次")
    return _item(t.get("title", ""), t.get("mobile_url") or t.get("url", ""), meta, t.get("is_new", False))


def _section_title(icon: str, name: str, total: int) -> str:
    return f"{icon} <b>{name}</b>{_DOT}{total} 条"


def _group_title(name: str, count: int) -> str:
    return f"<b>{html_escape(name)}</b>  <i>{count}</i>"


def _grouped_units(section: str, groups: List[Tuple[str, List[str]]]) -> List[Unit]:
    """区块标题 → 分组标题 → 条目，分组标题与首条绑定，避免分组标题孤零零落在页尾"""
    units: List[Unit] = [(2, section, None, None)]
    for g_idx, (g_title, items) in enumerate(groups):
        if not items:
            continue
        units.append((1 if g_idx == 0 else 2, f"{g_title}\n{items[0]}", section, None))
        for item in items[1:]:
            units.append((1, item, section, g_title))
    return units


def _stats_units(stats: List[Dict], section: str, show_source: bool, show_keyword: bool) -> List[Unit]:
    groups = [
        (_group_title(s["word"], s["count"]), [_title_item(t, show_source, show_keyword) for t in s["titles"]])
        for s in stats
    ]
    return _grouped_units(section, groups)


def _new_titles_units(new_titles: List[Dict], total: int) -> List[Unit]:
    groups = []
    for src in new_titles:
        items = [_title_item({**t, "is_new": False}, show_source=False) for t in src["titles"]]
        groups.append((_group_title(src["source_name"], len(items)), items))
    return _grouped_units(_section_title("🆕", "本次新增", total), groups)


def _rss_new_units(rss_new_items: List[Dict]) -> List[Unit]:
    by_source: Dict[str, List[Dict]] = {}
    for stat in rss_new_items:
        for t in stat.get("titles", []):
            by_source.setdefault(t.get("source_name", "未知来源"), []).append(t)
    if not by_source:
        return []
    total = sum(len(v) for v in by_source.values())
    groups = [
        (_group_title(name, len(ts)), [_title_item({**t, "is_new": False}, show_source=False) for t in ts])
        for name, ts in by_source.items()
    ]
    return _grouped_units(_section_title("🆕", "RSS 新增", total), groups)


def _standalone_units(data: Dict, rank_threshold: int, timezone: str) -> List[Unit]:
    groups = []
    for p in data.get("platforms", []):
        items = []
        for it in p.get("items", []):
            ranks = it.get("ranks") or ([it["rank"]] if it.get("rank") else [])
            first, last = it.get("first_time", ""), it.get("last_time", "")
            time_text = convert_time_for_display(first) if first else ""
            if first and last and first != last:
                time_text += f"–{convert_time_for_display(last)}"
            count = it.get("count", 1)
            items.append(_item(
                it.get("title", ""),
                it.get("url", "") or it.get("mobileUrl", ""),
                [
                    _rank_text(ranks, rank_threshold, it.get("rank_timeline")),
                    html_escape(time_text),
                    f"{count} 次" if count > 1 else "",
                ],
            ))
        groups.append((_group_title(p.get("name", p.get("id", "")), len(items)), items))
    for f in data.get("rss_feeds", []):
        items = []
        for it in f.get("items", []):
            published = it.get("published_at", "")
            items.append(_item(
                it.get("title", ""),
                it.get("url", ""),
                [
                    html_escape(format_iso_time_friendly(published, timezone, include_date=True)) if published else "",
                    html_escape(it.get("author", "")),
                ],
            ))
        groups.append((_group_title(f.get("name", f.get("id", "")), len(items)), items))
    total = sum(len(items) for _, items in groups)
    if total == 0:
        return []
    return _grouped_units(_section_title("📌", "独立展示", total), groups)


def _ai_units(ai_content: str) -> List[Unit]:
    """AI 内容按段落拆成单元（段落内标签已闭合，见 ai/formatter.py）"""
    paragraphs = [p for p in ai_content.strip().split("\n\n") if p.strip()]
    if not paragraphs:
        return []
    section = paragraphs[0]
    return [(2, section, None, None)] + [(1, p, section, None) for p in paragraphs[1:]]


def _header(report_type: str, now: datetime, summary_lines: List[str]) -> str:
    date_line = f"{now.month}月{now.day}日 {_WEEKDAYS[now.weekday()]} {now.strftime('%H:%M')}"
    text = f"<b>{html_escape(report_type)}</b>\n<i>{date_line}</i>"
    if summary_lines:
        text += "\n\n<blockquote>" + "\n".join(summary_lines) + "</blockquote>"
    return text


def _continued_header(report_type: str, now: datetime) -> str:
    return f"<b>{html_escape(report_type)}</b>  <i>{now.strftime('%H:%M')} · 续</i>"


def _footer(page: int, total: int, update_info: Optional[Dict]) -> str:
    parts = ["TrendRadar"]
    if total > 1:
        parts.append(f"{page}/{total}")
    text = f"<i>{_DOT.join(parts)}</i>"
    if update_info:
        text += (
            f"\n<i>新版本 {html_escape(update_info['remote_version'])} 可用，"
            f"当前 {html_escape(update_info['current_version'])}</i>"
        )
    return text


def _pack(units: List[Unit], first_header: str, next_header: str, footer_reserve: int, max_bytes: int) -> List[str]:
    """按单元装箱；续页补齐区块/分组标题。返回不含 footer 的正文列表"""
    pages: List[str] = []
    current = first_header
    for gap, text, section, group in units:
        piece = "\n" * (gap + 1) + text
        if _size(current + piece) + footer_reserve <= max_bytes:
            current += piece
            continue
        pages.append(current)
        current = next_header
        if section:
            current += "\n\n\n" + section
        current += f"\n\n{group}\n{text}" if group else f"\n\n{text}"
        if _size(current) + footer_reserve > max_bytes:
            # 单个单元超限（极少见，如超长 AI 段落），只能按行截断
            current = truncate_at_line_boundary(current, max_bytes - footer_reserve)
    pages.append(current)
    return pages


def _summary_lines(
    report_data: Dict,
    rss_items: Optional[list],
    rss_new_items: Optional[list],
    standalone_data: Optional[Dict],
    ai_stats: Optional[Dict],
) -> List[str]:
    hot_matched = sum(len(s["titles"]) for s in report_data["stats"] if s["count"] > 0)
    rss_matched = sum(s.get("count", 0) for s in (rss_items or []))
    new_total = report_data.get("total_new_count", 0) + sum(
        len(s.get("titles", [])) for s in (rss_new_items or [])
    )

    headline = f"匹配 <b>{hot_matched + rss_matched}</b> 条"
    if new_total:
        headline += f"{_DOT}新增 <b>{new_total}</b> 条"
    lines = [headline]

    hot_line = f"热榜 {hot_matched}/{report_data.get('hotlist_total', hot_matched)}"
    platform_total = report_data.get("platform_total", 0)
    if platform_total:
        ok = platform_total - len(report_data.get("failed_ids", []))
        hot_line += f"{_DOT}平台 {ok}/{platform_total}"
    lines.append(hot_line)

    rss_source_total = report_data.get("rss_source_total", 0)
    if rss_source_total:
        ok = max(0, rss_source_total - report_data.get("rss_source_failed", 0))
        lines.append(
            f"RSS {report_data.get('rss_matched_count', 0)}/{report_data.get('rss_total_count', 0)}"
            f"{_DOT}订阅源 {ok}/{rss_source_total}"
        )

    if ai_stats:
        analyzed = (
            ai_stats.get("hotlist_analyzed", 0)
            + (ai_stats.get("rss_analyzed", 0) if ai_stats.get("include_rss", True) else 0)
            + (ai_stats.get("standalone_analyzed", 0) if ai_stats.get("include_standalone", False) else 0)
        )
        if analyzed:
            lines.append(f"AI 已分析 {analyzed} 条")

    top = report_data.get("stats", [])[:3]
    if top:
        lines.append("热门 " + _DOT.join(html_escape(s["word"]) for s in top))
    return lines


def render_telegram_batches(
    report_data: Dict,
    update_info: Optional[Dict] = None,
    max_bytes: int = 4000,
    mode: str = "daily",
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
) -> List[str]:
    """渲染 Telegram HTML 消息并分批，页码已写入每批 footer"""
    now = get_time_func() if get_time_func else datetime.now()
    region_order = region_order or ["hotlist", "rss", "new_items", "standalone", "ai_analysis"]

    units: List[Unit] = []
    show_source = display_mode == "keyword"
    for region in region_order:
        if region == "hotlist" and report_data["stats"]:
            total = sum(len(s["titles"]) for s in report_data["stats"] if s["count"] > 0)
            name = "热点话题" if display_mode == "keyword" else "热点新闻"
            units += _stats_units(
                report_data["stats"], _section_title("🔥", name, total), show_source, not show_source
            )
        elif region == "rss" and rss_items:
            total = sum(s["count"] for s in rss_items)
            units += _stats_units(rss_items, _section_title("📰", "RSS 订阅", total), True, False)
        elif region == "new_items":
            if show_new_section and report_data["new_titles"]:
                units += _new_titles_units(report_data["new_titles"], report_data.get("total_new_count", 0))
            if rss_new_items:
                units += _rss_new_units(rss_new_items)
        elif region == "standalone" and standalone_data:
            units += _standalone_units(standalone_data, rank_threshold, timezone)
        elif region == "ai_analysis" and ai_content:
            units += _ai_units(ai_content)

    if report_data["failed_ids"]:
        failed = _DOT.join(html_escape(str(i)) for i in report_data["failed_ids"])
        units.append((2, f"⚠️ <b>未能获取</b>\n<i>{failed}</i>", None, None))

    # 空报告不展示全是 0 的摘要
    summary = _summary_lines(report_data, rss_items, rss_new_items, standalone_data, ai_stats) if units else []
    first_header = _header(report_type, now, summary)
    next_header = _continued_header(report_type, now)

    if not units:
        reason = {
            "incremental": "本轮没有新增的匹配内容",
            "current": "当前榜单没有匹配的内容",
        }.get(mode, "暂时没有匹配的内容")
        units.append((2, f"<b>暂无新内容</b>\n<i>{reason}</i>", None, None))

    # 按最坏情况（两位数页码）预留 footer 空间
    footer_reserve = _size("\n\n\n" + _footer(99, 99, update_info))
    pages = _pack(units, first_header, next_header, footer_reserve, max_bytes)
    total = len(pages)
    return [f"{body}\n\n\n{_footer(i, total, update_info)}" for i, body in enumerate(pages, 1)]
