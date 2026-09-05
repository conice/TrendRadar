"""兼容不分批的飞书、钉钉渲染入口，复用统一的报告结构。"""

from trendradar.notification.splitter import split_content_into_batches


def render_feishu_content(
    report_data, update_info=None, mode="daily", separator="---", region_order=None,
    get_time_func=None, rss_items=None, show_new_section=True,
):
    return "\n\n".join(split_content_into_batches(
        report_data, "feishu", update_info, max_bytes=2 ** 31, mode=mode,
        region_order=region_order, get_time_func=get_time_func, rss_items=rss_items,
        show_new_section=show_new_section,
    ))


def render_dingtalk_content(
    report_data, update_info=None, mode="daily", region_order=None,
    get_time_func=None, rss_items=None, show_new_section=True,
):
    return "\n\n".join(split_content_into_batches(
        report_data, "dingtalk", update_info, max_bytes=2 ** 31, mode=mode,
        region_order=region_order, get_time_func=get_time_func, rss_items=rss_items,
        show_new_section=show_new_section,
    ))
