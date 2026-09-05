"""通知的内容结构：各区域共用条目布局，渠道只负责文字样式。"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from trendradar.report.formatter import MessageStyle, format_title_for_platform
from trendradar.report.presentation import SCOPE_LABELS, ai_heading, display_count, rss_display_groups


DEFAULT_REGION_ORDER = ["hotlist", "rss", "new_items", "standalone", "ai_analysis"]


@dataclass
class Entry:
    item: Optional[Dict] = None
    text: str = ""
    index: int = 0
    mode: str = "daily"
    kind: str = "hotlist"
    show_source: bool = True
    show_keyword: bool = False
    show_new: bool = True

    def render(self, style: MessageStyle, timezone: str, reference_date: str) -> str:
        if self.item is None:
            return style.text(self.text).replace("\n", style.line_break)
        body = format_title_for_platform(
            style.platform, self.item, self.show_source, self.show_keyword,
            mode=self.mode, kind=self.kind, show_new=self.show_new,
            timezone=timezone, reference_date=reference_date,
        )
        return f"{self.index}. {body}"


@dataclass
class Group:
    section: str
    name: str = ""
    entries: List[Entry] = field(default_factory=list)
    matched: Optional[int] = None
    primary: bool = False
    standalone: bool = False
    count_label: str = "匹配"

    def heading(self, continued: bool = False) -> str:
        if not self.name:
            return ""
        if continued:
            return f"{self.name}（续）"
        if self.matched is not None:
            return f"{self.name} · {display_count(len(self.entries), self.matched, self.count_label)}"
        return self.name


def _stats_groups(stats, section, mode, kind, display_mode="keyword", *, show_new=True):
    groups = []
    for stat in stats or []:
        items = stat.get("titles", [])
        if not items:
            continue
        entries = [Entry(
            item=item, index=index, mode=mode, kind=kind,
            show_source=display_mode == "keyword", show_keyword=display_mode == "platform",
            show_new=show_new,
        ) for index, item in enumerate(items, 1)]
        groups.append(Group(section, stat.get("word", "新闻"), entries,
                            max(len(items), stat.get("matched_count", stat.get("count", len(items)))), primary=True))
    return groups



def _new_groups(items, section, kind):
    grouped = {}
    for group in items or []:
        for item in group.get("titles", [group]):
            source = item.get("source_name") or group.get("source_name") or item.get("feed_name") or "RSS"
            source_id = item.get("source_id") or group.get("source_id") or source
            grouped.setdefault(source_id, {"word": source, "count": 0, "titles": []})
            grouped[source_id]["titles"].append({**item, "source_name": source})
            grouped[source_id]["count"] += 1
    return _stats_groups(list(grouped.values()), section, "incremental", kind, "platform", show_new=False)


def _ai_groups(result):
    from trendradar.ai.formatter import analysis_sections

    groups = []
    for name, content in analysis_sections(result):
        entries = [Entry(text=part) for part in content.split("\n\n") if part.strip()]
        if entries:
            groups.append(Group(ai_heading(result), name, entries))
    return groups


def build_groups(
    report_data, mode, region_order, *, rss_items=None, rss_new_items=None,
    standalone_data=None, ai_analysis=None, ai_content=None, display_mode="keyword",
    show_new_section=True, update_info=None,
):
    regions = {
        "hotlist": _stats_groups(report_data.get("stats"), "热榜", mode, "hotlist", display_mode),
        "rss": _stats_groups(rss_display_groups(rss_items, display_mode), "RSS 订阅", mode, "rss", display_mode),
        "new_items": [], "standalone": [], "ai_analysis": [],
    }
    if show_new_section and mode != "incremental":
        regions["new_items"] = [
            *_new_groups(report_data.get("new_titles"), "本轮新发现 · 热榜", "hotlist"),
            *_new_groups(rss_new_items, "本轮新发现 · RSS", "rss"),
        ]
    if standalone_data:
        for key, kind, fallback_mode in (("platforms", "hotlist", "current"), ("rss_feeds", "rss", mode)):
            data_mode = standalone_data.get(f"{kind}_mode", fallback_mode)
            scope = SCOPE_LABELS.get(data_mode, "")
            section = f"独立展示 · {'热榜' if kind == 'hotlist' else 'RSS'} · {scope}"
            for source in standalone_data.get(key, []):
                items = source.get("items", [])
                if items:
                    entries = [Entry(item=item, index=index, mode=data_mode, kind=kind,
                                     show_source=False) for index, item in enumerate(items, 1)]
                    regions["standalone"].append(Group(section, source.get("name", source.get("id", "")),
                                                       entries, source.get("count", len(items)), standalone=True, count_label="共"))
    if ai_analysis is not None:
        regions["ai_analysis"] = _ai_groups(ai_analysis)
    elif ai_content:
        # 兼容旧调用方预渲染的 AI 文本；主推送路径使用上方的结构化结果。
        from trendradar.notification.formatters import plain_text
        regions["ai_analysis"] = [Group("AI 解读", entries=[Entry(text=plain_text(ai_content))])]

    groups = [group for region in dict.fromkeys(region_order) for group in regions.get(region, [])]
    notes = []
    scope = []
    for key, label in (("hotlist_total", "热榜"), ("rss_total_count", "RSS")):
        if key in report_data:
            scope.append(f"{label} {report_data[key]} 条")
    if scope:
        notes.append("数据范围：" + " · ".join(scope))
    sources = []
    for total, failed, label in (
        (report_data.get("platform_total", 0), len(report_data.get("failed_ids", [])), "热榜"),
        (report_data.get("rss_source_total", 0), report_data.get("rss_source_failed", 0), "RSS"),
    ):
        if total:
            sources.append(f"{label} {max(0, total - failed)}/{total}")
    if sources:
        notes.append("成功来源：" + " · ".join(sources))
    if report_data.get("failed_ids"):
        notes.append("来源暂不可用：" + "、".join(map(str, report_data["failed_ids"])))
    if report_data.get("rss_source_failed"):
        notes.append(f"RSS 来源暂不可用：{report_data['rss_source_failed']} 个")
    if update_info:
        notes.append(f"TrendRadar 新版本 {update_info['remote_version']}（当前 {update_info['current_version']}）")
    if notes:
        groups.append(Group("备注", entries=[Entry(text=note) for note in notes]))
    return groups


def news_counts(groups):
    """新增区与主区域的相同条目只计一次；独立展示另计。"""
    primary = set()
    standalone = 0
    for group in groups:
        if group.standalone:
            standalone += len(group.entries)
        if group.primary:
            for entry in group.entries:
                item = entry.item
                source = item.get("source_id") or item.get("feed_id") or item.get("source_name", "")
                identity = item.get("url") or item.get("mobile_url") or item.get("mobileUrl") or item.get("title", "")
                primary.add((entry.kind, source, identity))
    return len(primary), standalone
