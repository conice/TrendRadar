# coding=utf-8
"""
AI 分析结果格式化模块

将 AI 分析结果格式化为各推送渠道的样式
"""

from __future__ import annotations

import html as html_lib
import re
from typing import TYPE_CHECKING

from trendradar.report.formatter import MessageStyle
from trendradar.report.presentation import ai_heading

if TYPE_CHECKING:
    from .analyzer import AIAnalysisResult


def _escape_html(text: str) -> str:
    """转义 HTML 特殊字符，防止 XSS 攻击"""
    return html_lib.escape(text) if text else ""


def _format_list_content(text: str) -> str:
    """
    格式化列表内容，确保序号前有换行
    例如将 "1. xxx 2. yyy" 转换为:
    1. xxx
    2. yyy
    """
    if not text:
        return ""
    
    # 去除首尾空白，防止 AI 返回的内容开头就有换行导致显示空行
    text = text.strip()

    # 0. 合并序号与紧随的【标签】（防御性处理）
    # 将 "1.\n【投资者】：" 或 "1. 【投资者】：" 合并为 "1. 投资者："
    text = re.sub(r'(\d+\.)\s*【([^】]+)】([:：]?)', r'\1 \2：', text)

    # 1. 规范化：确保 "1." 后面有空格
    result = re.sub(r'(\d+)\.([^ \d])', r'\1. \2', text)

    # 2. 强制换行：匹配 "数字."，且前面不是换行符
    #    (?!\d) 排除版本号/小数（如 2.0、3.5），避免将其误判为列表序号
    result = re.sub(r'(?<=[^\n])\s+(\d+\.)(?!\d)', r'\n\1', result)
    
    # 3. 处理 "1.**粗体**" 这种情况（虽然 Prompt 要求不输出 Markdown，但防御性处理）
    result = re.sub(r'(?<=[^\n])(\d+\.\*\*)', r'\n\1', result)

    # 4. 处理中文标点后的换行（排除版本号/小数）
    result = re.sub(r'([：:;,。；，])\s*(\d+\.)(?!\d)', r'\1\n\2', result)

    # 5. 处理 "XX方面："、"XX领域：" 等子标题换行
    # 只有在中文标点（句号、逗号、分号等）后才触发换行，避免破坏 "1. XX领域：" 格式
    result = re.sub(r'([。！？；，、])\s*([a-zA-Z0-9\u4e00-\u9fa5]+(方面|领域)[:：])', r'\1\n\2', result)

    # 6. 处理 【标签】 格式
    # 6a. 标签前确保空行分隔（文本开头除外）
    result = re.sub(r'(?<=\S)\n*(【[^】]+】)', r'\n\n\1', result)
    # 6b. 合并标签与被换行拆开的冒号：【tag】\n： → 【tag】：
    result = re.sub(r'(【[^】]+】)\n+([:：])', r'\1\2', result)
    # 6c. 标签后（含可选冒号），如果紧跟非空白非冒号内容则另起一行
    # 用 (?=[^\s:：]) 避免正则回溯将冒号误判为"内容"而拆开 【tag】：
    result = re.sub(r'(【[^】]+】[:：]?)[ \t]*(?=[^\s:：])', r'\1\n', result)

    # 7. 在列表项之间增加视觉空行（排除版本号/小数）
    # 排除 【标签】 行（以】结尾）和子标题行（以冒号结尾）之后的情况，避免标题与首项之间出现空行
    result = re.sub(r'(?<![:：】])\n(\d+\.)(?!\d)', r'\n\n\1', result)

    return result


def analysis_sections(result: AIAnalysisResult):
    """所有渠道共用的分析板块和范围说明，内容保持纯文本。"""
    if not getattr(result, "success", False):
        prefix = "已跳过" if getattr(result, "skipped", False) else "分析失败"
        return [("", f"{prefix}：{getattr(result, 'error', '')}")]
    sections = []
    counts = []
    for key, name in (("hotlist_analyzed", "热榜"), ("rss_analyzed", "RSS"),
                      ("standalone_analyzed", "独立展示")):
        value = getattr(result, key, 0)
        if value:
            counts.append(f"{name} {value} 条")
    if counts:
        sections.append(("分析范围", " · ".join(counts)))
    fields = (("core_trends", "热点概览"), ("sentiment_controversy", "舆论与争议"),
              ("signals", "异动信号"), ("rss_insights", "RSS 解读"),
              ("outlook_strategy", "后续观察"))
    for key, name in fields:
        value = getattr(result, key, "")
        if value:
            sections.append((name, _format_list_content(value)))
    for source, value in (getattr(result, "standalone_summaries", {}) or {}).items():
        if value:
            sections.append((f"独立来源 · {source}", _format_list_content(value)))
    return sections


def _render_analysis(result, platform):
    if result is None:
        return ""
    style = MessageStyle(platform)
    parts = [style.bold(ai_heading(result))]
    for name, content in analysis_sections(result):
        body = style.text(content).replace("\n", style.line_break)
        parts.append((style.bold(name) + style.line_break if name else "") + body)
    return "\n\n".join(parts)


def render_ai_analysis_markdown(result: AIAnalysisResult) -> str:
    return _render_analysis(result, "wework")


def render_ai_analysis_feishu(result: AIAnalysisResult) -> str:
    return _render_analysis(result, "feishu")


def render_ai_analysis_dingtalk(result: AIAnalysisResult) -> str:
    return _render_analysis(result, "dingtalk")


def render_ai_analysis_plain(result: AIAnalysisResult) -> str:
    return _render_analysis(result, "plain")


def render_ai_analysis_telegram(result: AIAnalysisResult) -> str:
    return _render_analysis(result, "telegram")


def render_ai_analysis_slack(result: AIAnalysisResult) -> str:
    return _render_analysis(result, "slack")


def render_ai_analysis_html_rich(result: AIAnalysisResult) -> str:
    if result is None:
        return ""
    blocks = []
    for name, content in analysis_sections(result):
        heading = f'<div class="ai-block-title">{_escape_html(name)}</div>' if name else ""
        body = _escape_html(content).replace("\n", "<br>")
        blocks.append(f'<div class="ai-block">{heading}<div class="ai-block-content">{body}</div></div>')
    return (
        '<div class="ai-section"><div class="ai-section-header">'
        f'<div class="ai-section-title">{_escape_html(ai_heading(result))}</div>'
        '<span class="ai-section-badge">AI</span></div><div class="ai-blocks-grid">'
        + "".join(blocks) + "</div></div>"
    )


def get_ai_analysis_renderer(channel: str):
    return {
        "feishu": render_ai_analysis_feishu,
        "dingtalk": render_ai_analysis_dingtalk,
        "wework": render_ai_analysis_markdown,
        "telegram": render_ai_analysis_telegram,
        "email": render_ai_analysis_html_rich,
        "ntfy": render_ai_analysis_markdown,
        "bark": render_ai_analysis_markdown,
        "slack": render_ai_analysis_slack,
        "wework_text": render_ai_analysis_plain,
        "feishu_text": render_ai_analysis_plain,
        "generic_text": render_ai_analysis_plain,
    }.get(channel, render_ai_analysis_markdown)
