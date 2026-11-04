# coding=utf-8
"""
数据处理模块

提供数据读取和检测功能：
- read_all_today_titles: 从存储后端读取当天所有标题
- detect_latest_new_titles: 在指定历史范围内检测最新批次的新增标题

Author: TrendRadar Team
"""

from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional


def read_all_today_titles_from_storage(
    storage_manager,
    current_platform_ids: Optional[List[str]] = None,
) -> Tuple[Dict, Dict, Dict]:
    """
    从存储后端读取当天所有标题（SQLite 数据）

    Args:
        storage_manager: 存储管理器实例
        current_platform_ids: 当前监控的平台 ID 列表（用于过滤）

    Returns:
        Tuple[Dict, Dict, Dict]: (all_results, id_to_name, title_info)
    """
    try:
        news_data = storage_manager.get_today_all_data()

        if not news_data or not news_data.items:
            return {}, {}, {}

        all_results = {}
        final_id_to_name = {}
        title_info = {}

        for source_id, news_list in news_data.items.items():
            # 按平台过滤
            if current_platform_ids is not None and source_id not in current_platform_ids:
                continue

            # 获取来源名称
            source_name = news_data.id_to_name.get(source_id, source_id)
            final_id_to_name[source_id] = source_name

            if source_id not in all_results:
                all_results[source_id] = {}
                title_info[source_id] = {}

            for item in news_list:
                title = item.title
                ranks = item.ranks or [item.rank]
                first_time = item.first_time or item.crawl_time
                last_time = item.last_time or item.crawl_time
                count = item.count
                rank_timeline = item.rank_timeline

                all_results[source_id][title] = {
                    "ranks": ranks,
                    "url": item.url or "",
                    "mobileUrl": item.mobile_url or "",
                }

                title_info[source_id][title] = {
                    "first_time": first_time,
                    "last_time": last_time,
                    "count": count,
                    "ranks": ranks,
                    "url": item.url or "",
                    "mobileUrl": item.mobile_url or "",
                    "rank_timeline": rank_timeline,
                }

        return all_results, final_id_to_name, title_info

    except Exception as e:
        print(f"[存储] 从存储后端读取数据失败: {e}")
        return {}, {}, {}


def read_all_today_titles(
    storage_manager,
    current_platform_ids: Optional[List[str]] = None,
    quiet: bool = False,
) -> Tuple[Dict, Dict, Dict]:
    """
    读取当天所有标题（从存储后端）

    Args:
        storage_manager: 存储管理器实例
        current_platform_ids: 当前监控的平台 ID 列表（用于过滤）
        quiet: 是否静默模式（不打印日志）

    Returns:
        Tuple[Dict, Dict, Dict]: (all_results, id_to_name, title_info)
    """
    all_results, final_id_to_name, title_info = read_all_today_titles_from_storage(
        storage_manager, current_platform_ids
    )

    if not quiet:
        if all_results:
            total_count = sum(len(titles) for titles in all_results.values())
            print(f"[存储] 已从存储后端读取 {total_count} 条标题")
        else:
            print("[存储] 当天暂无数据")

    return all_results, final_id_to_name, title_info


def detect_latest_new_titles_from_storage(
    storage_manager,
    current_platform_ids: Optional[List[str]] = None,
    history_days: int = 1,
) -> Dict:
    """
    从存储后端检测最新批次的新增标题

    Args:
        storage_manager: 存储管理器实例
        current_platform_ids: 当前监控的平台 ID 列表（用于过滤）
        history_days: 比较今天及之前的历史数据库天数

    Returns:
        Dict: 新增标题 {source_id: {title: title_data}}
    """
    try:
        # 获取最新抓取数据
        latest_data = storage_manager.get_latest_crawl_data()
        if not latest_data or not latest_data.items:
            return {}

        # 获取最新批次时间
        latest_time = latest_data.crawl_time

        # 步骤1：收集最新批次的标题（last_crawl_time = latest_time 的标题）
        latest_titles = {}
        for source_id, news_list in latest_data.items.items():
            if current_platform_ids is not None and source_id not in current_platform_ids:
                continue
            latest_titles[source_id] = {}
            for item in news_list:
                latest_titles[source_id][item.title] = {
                    "ranks": [item.rank],
                    "url": item.url or "",
                    "mobileUrl": item.mobile_url or "",
                }

        # 步骤2：收集范围内各日期数据库的历史标题。
        # 当前日期只比较最新批次之前的记录，之前日期的数据库全部属于历史。
        try:
            days = max(1, int(history_days))
        except (TypeError, ValueError):
            days = 1
        try:
            latest_date = datetime.strptime(latest_data.date, "%Y-%m-%d").date()
        except (TypeError, ValueError):
            latest_date = datetime.now().date()

        historical_titles = {}
        for day_offset in range(days):
            date_str = (latest_date - timedelta(days=day_offset)).isoformat()
            day_data = storage_manager.get_today_all_data(date_str)
            if not day_data or not day_data.items:
                continue

            for source_id, news_list in day_data.items.items():
                if current_platform_ids is not None and source_id not in current_platform_ids:
                    continue

                source_history = historical_titles.setdefault(source_id, set())
                for item in news_list:
                    first_time = item.first_time or item.crawl_time
                    if day_offset > 0 or first_time < latest_time:
                        source_history.add(item.title)

        # 检查是否是当天第一次抓取（没有任何历史标题）
        # 如果所有平台的历史标题集合都为空，说明只有一个抓取批次
        # 在这种情况下，将所有最新批次的标题视为"新增"（用于增量模式的第一次推送）
        has_historical_data = any(len(titles) > 0 for titles in historical_titles.values())
        if not has_historical_data:
            # 第一次爬取：返回所有最新标题作为"新增"
            return latest_titles

        # 步骤3：找出新增标题 = 最新批次标题 - 历史标题
        new_titles = {}
        for source_id, source_latest_titles in latest_titles.items():
            historical_set = historical_titles.get(source_id, set())
            source_new_titles = {}

            for title, title_data in source_latest_titles.items():
                if title not in historical_set:
                    source_new_titles[title] = title_data

            if source_new_titles:
                new_titles[source_id] = source_new_titles

        return new_titles

    except Exception as e:
        print(f"[存储] 从存储后端检测新标题失败: {e}")
        return {}


def detect_latest_new_titles(
    storage_manager,
    current_platform_ids: Optional[List[str]] = None,
    quiet: bool = False,
    history_days: int = 1,
) -> Dict:
    """
    在指定历史范围内检测最新批次的新增标题（从存储后端）

    Args:
        storage_manager: 存储管理器实例
        current_platform_ids: 当前监控的平台 ID 列表（用于过滤）
        history_days: 比较今天及之前的历史数据库天数
        quiet: 是否静默模式（不打印日志）

    Returns:
        Dict: 新增标题 {source_id: {title: title_data}}
    """
    new_titles = detect_latest_new_titles_from_storage(
        storage_manager, current_platform_ids, history_days=history_days
    )
    if new_titles and not quiet:
        total_new = sum(len(titles) for titles in new_titles.values())
        print(f"[存储] 从存储后端检测到 {total_new} 条新增标题")
    return new_titles
