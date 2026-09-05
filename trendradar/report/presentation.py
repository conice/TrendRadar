"""报告字段的展示含义，供通知、网页和邮件共用。"""

import re
from typing import Dict, List, Optional

from trendradar.utils.time import DEFAULT_TIMEZONE, convert_time_for_display, format_iso_time_friendly


MODE_LABELS = {"daily": "当日汇总", "current": "当前榜单", "incremental": "增量更新"}
SCOPE_LABELS = {"daily": "当日数据", "current": "当前快照", "incremental": "增量数据"}
_REPORT_ALIASES = {"热点分析报告", "全天汇总", "增量分析", *MODE_LABELS.values()}


def report_label(mode: str, report_type: str = "") -> str:
    """保留连通性测试等自定义标题，统一常规报告的名称。"""
    if report_type and report_type not in _REPORT_ALIASES:
        return report_type
    if mode not in MODE_LABELS:
        mode = {"全天汇总": "daily", "当日汇总": "daily", "当前榜单": "current",
                "增量分析": "incremental", "增量更新": "incremental"}.get(report_type, mode)
    return MODE_LABELS.get(mode, report_type or "热点报告")


def display_count(shown: int, matched: Optional[int] = None, label: str = "匹配") -> str:
    if matched is not None and matched > shown:
        return f"展示 {shown}／{label} {matched} 条"
    return f"{shown} 条"


def _positive_rank(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def rank_metadata(item: Dict, mode: str) -> List[str]:
    """历史范围只表示范围；最新排名和涨跌必须有对应的采样依据。"""
    if item.get("rank_is_placeholder"):
        return []
    ranks = [rank for value in item.get("ranks", []) if (rank := _positive_rank(value))]
    timeline = item.get("rank_timeline") or []
    if mode == "daily":
        observed = ranks or [rank for point in timeline if (rank := _positive_rank(point.get("rank")))]
        return [f"今日最高第 {min(observed)}"] if observed else []

    latest = item.get("current_rank")
    if latest is None:
        latest = item.get("rank")
    if latest is None and timeline:
        # 不过滤脱榜点，否则会把过去一次的排名误当作当前排名。
        latest = timeline[-1].get("rank")
    elif latest is None and len(set(ranks)) == 1:
        latest = ranks[0]
    current = _positive_rank(latest)
    if current is None:
        if timeline and timeline[-1].get("rank") in (None, 0):
            return ["最近采样未上榜"]
        if ranks:
            low, high = min(ranks), max(ranks)
            return [f"{'今日' if mode == 'current' else '观测'}第 {low}–{high}"]
        return []

    parts = [f"{'当前' if mode == 'current' else '本次'}第 {current}"]
    if mode == "current" and len(timeline) >= 2:
        previous, last = timeline[-2:]
        # 只有连续两个带时间的采样才可作比较，不用去重后的 ranks 推断走势。
        if previous.get("time") and last.get("time") and previous["time"] != last["time"]:
            previous_rank = _positive_rank(previous.get("rank"))
            if _positive_rank(last.get("rank")) == current:
                if previous_rank:
                    delta = previous_rank - current
                    if delta:
                        parts.append(f"较上次{'上升' if delta > 0 else '下降'} {abs(delta)} 位")
                elif previous.get("rank") in (None, 0):
                    parts.append("重新上榜")
    return parts


def _observation_time(value: str, item: Dict, reference_date: str = "") -> str:
    value = convert_time_for_display(str(value or ""))
    observed_date = str(item.get("observed_date", ""))
    match = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", value)
    if match:
        observed_date, value = match.groups()
    if value and len(value) == 5 and observed_date and reference_date and observed_date != reference_date:
        date_label = observed_date[5:] if observed_date[:4] == reference_date[:4] else observed_date
        return f"{date_label} {value}"
    return value


def news_metadata(
    item: Dict,
    mode: str,
    *,
    kind: str = "hotlist",
    show_source: bool = True,
    show_keyword: bool = False,
    show_new: bool = True,
    timezone: str = DEFAULT_TIMEZONE,
    reference_date: str = "",
) -> List[str]:
    """返回没有渠道标记的辅助信息；RSS 发布时间和热榜观测时间分别处理。"""
    parts = []
    source = item.get("source_name") or item.get("feed_name") or item.get("source_id") or item.get("feed_id")
    if show_source and source:
        parts.append(str(source))
    if show_keyword and item.get("matched_keyword"):
        parts.append(str(item["matched_keyword"]))
    if show_new and mode != "incremental" and item.get("is_new"):
        parts.append("本轮新发现")

    if kind == "rss":
        published = item.get("published_at", "")
        value = format_iso_time_friendly(published, timezone, include_date=True) if published else item.get("time_display", "")
        if value:
            parts.append(f"发布 {value}")
        if item.get("author"):
            parts.append(str(item["author"]))
        return parts

    parts.extend(rank_metadata(item, mode))
    first = _observation_time(item.get("first_time", ""), item, reference_date)
    last = _observation_time(item.get("last_time", ""), item, reference_date)
    # 兼容尚未带原始时间字段的旧报告和积压推送。
    if not first and item.get("time_display"):
        time_text = str(item["time_display"]).strip("[] ")
        times = time_text.split("~", 1)
        first = _observation_time(times[0].strip(), item, reference_date)
        last = _observation_time(times[-1].strip(), item, reference_date)
    if mode == "daily":
        count = item.get("count", 1)
        if count and count > 1:
            parts.append(f"采集命中 {count} 次")
        if first:
            parts.append(f"观测 {first}–{last}" if last and last != first else f"观测 {first}")
    elif mode == "incremental" and first:
        parts.append(f"首次发现 {first}")
    return parts


def ai_heading(result) -> str:
    scope = SCOPE_LABELS.get(getattr(result, "ai_mode", ""), "")
    return f"AI 解读 · {scope}" if scope else "AI 解读"


def snapshot_time(stats) -> str:
    """最新观测时间来自数据，不能用文章发布时间或推送时间代替。"""
    observed = []
    for stat in stats:
        for item in stat.get("titles", []):
            value = convert_time_for_display(str(item.get("last_time", "")))
            date = str(item.get("observed_date", ""))
            match = re.match(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})", value)
            if match:
                date, value = match.groups()
            if re.fullmatch(r"\d{2}:\d{2}", value):
                observed.append((date, value))
    if not observed:
        return ""
    date, time = max(observed)
    return f"{date[5:]} {time}" if date else time


def rss_display_groups(items, display_mode="keyword"):
    """兼容统计分组和旧的 RSS 平铺输入；按来源展示时保留命中主题。"""
    if not items:
        return []
    if all("titles" in item for item in items) and display_mode == "keyword":
        return items
    grouped = {}
    for group in items:
        entries = group.get("titles", [group])
        for entry in entries:
            source = entry.get("source_name") or entry.get("feed_name") or entry.get("feed_id") or "RSS"
            name = entry.get("source_id") or entry.get("feed_id") or source
            target = grouped.setdefault(name, {"word": source, "titles": [], "count": 0})
            target["titles"].append({**entry, "source_name": source,
                                      "matched_keyword": entry.get("matched_keyword") or group.get("word", "")})
            target["count"] += 1
    return list(grouped.values())
