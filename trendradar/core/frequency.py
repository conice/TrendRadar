# coding=utf-8
"""
频率词配置加载模块

负责从配置文件加载频率词规则，支持：
- 普通词组
- 必须词（+前缀）
- 组内过滤词（!前缀）
- 全局过滤词（[GLOBAL_FILTER] 区域）
- 最大显示数量（@前缀）
- 正则表达式（/pattern/ 语法）
- 显示名称（=> 别名 语法）
- 组别名（[组别名] 语法，作为词组第一行）
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Union


RuleConfig = Union[str, Dict]


@dataclass(frozen=True)
class FrequencyMatch:
    """A title's observable result at the frequency-matching seam."""

    accepted: bool
    groups: Tuple[Dict, ...] = ()
    filtered_by: Optional[str] = None


@dataclass(frozen=True)
class FrequencyValidationIssue:
    """A structural or semantic issue found in a frequency-word file."""

    level: str
    line: int
    message: str


def _parse_word(word: str) -> Dict:
    """
    解析单个词，识别是否为正则表达式，支持显示名称

    Args:
        word: 原始配置行 (e.g. "/京东|刘强东/ => 京东")

    Returns:
        Dict: 包含 word, is_regex, pattern, display_name
    """
    display_name = None

    # 1. 优先处理显示名称 (=>)
    # 先切分出 "配置内容" 和 "显示名称"
    if '=>' in word:
        parts = re.split(r'\s*=>\s*', word, maxsplit=1)
        word_config = parts[0].strip()
        # 只有当 => 右边有内容时才作为 display_name
        if len(parts) > 1 and parts[1].strip():
            display_name = parts[1].strip()
    else:
        word_config = word.strip()

    # 2. 解析正则表达式
    # 规则：以 / 开头，以 / 结尾(可能跟 flags)，中间内容贪婪提取
    # [a-z]*$ 表示允许末尾有 flags (如 i, g)，但在下面代码中会被忽略
    regex_match = re.match(r'^/(.+)/[a-z]*$', word_config)

    if regex_match:
        pattern_str = regex_match.group(1)
        try:
            pattern = re.compile(pattern_str, re.IGNORECASE)
            
            return {
                "word": pattern_str,
                "is_regex": True,
                "pattern": pattern,
                "display_name": display_name,
            }
        except re.error as e:
            print(f"Warning: Invalid regex pattern '/{pattern_str}/': {e}")
            pass

    return {
        "word": word_config, 
        "is_regex": False, 
        "pattern": None, 
        "display_name": display_name
    }


def _word_matches(word_config: RuleConfig, title_lower: str) -> bool:
    """
    检查词是否在标题中匹配

    Args:
        word_config: 词配置（字符串或字典）
        title_lower: 小写的标题

    Returns:
        是否匹配
    """
    if isinstance(word_config, str):
        # 向后兼容：纯字符串
        return word_config.lower() in title_lower

    if word_config.get("is_regex") and word_config.get("pattern"):
        # 正则匹配
        return bool(word_config["pattern"].search(title_lower))
    else:
        # 子字符串匹配
        return word_config["word"].lower() in title_lower


def load_frequency_words(
    frequency_file: Optional[str] = None,
) -> Tuple[List[Dict], List[Dict], List[Dict]]:
    """
    加载频率词配置

    配置文件格式说明：
    - 每个词组由空行分隔
    - [GLOBAL_FILTER] 区域定义全局过滤词
    - [WORD_GROUPS] 区域定义词组（默认）

    词组语法：
    - 普通词：直接写入，任意匹配即可
    - +词：必须词，所有必须词都要匹配
    - !词：组内过滤词，匹配则从当前组排除
    - @数字：该词组最多显示的条数

    Args:
        frequency_file: 频率词配置文件路径，默认从环境变量 FREQUENCY_WORDS_PATH 获取或使用 config/frequency_words.txt，短文件名从 config/custom/keyword/ 查找

    Returns:
        (词组列表, 兼容用文件级过滤词, 全局过滤词)

        新配置中的 !过滤词保存在对应词组的 ``filters`` 字段中。返回值中的
        第二项仅为兼容旧调用方保留；需要全局排除时请使用 [GLOBAL_FILTER]。

    Raises:
        FileNotFoundError: 频率词文件不存在
    """
    if frequency_file is None:
        frequency_file = os.environ.get(
            "FREQUENCY_WORDS_PATH", "config/frequency_words.txt"
        )

    frequency_path = Path(frequency_file)
    if not frequency_path.exists():
        # 尝试作为短文件名，拼接 config/custom/keyword/ 前缀
        custom_path = Path("config/custom/keyword") / frequency_file
        if custom_path.exists():
            frequency_path = custom_path
        else:
            raise FileNotFoundError(f"频率词文件 {frequency_file} 不存在")

    with open(frequency_path, "r", encoding="utf-8") as f:
        content = f.read()

    word_groups = [group.strip() for group in content.split("\n\n") if group.strip()]

    processed_groups = []
    filter_words = []
    global_filters = []

    # 默认区域（向后兼容）
    current_section = "WORD_GROUPS"

    for group in word_groups:
        # 过滤空行和注释行（# 开头）
        lines = [line.strip() for line in group.split("\n") if line.strip() and not line.strip().startswith("#")]

        if not lines:
            continue

        # 检查是否为区域标记
        if lines[0].startswith("[") and lines[0].endswith("]"):
            section_name = lines[0][1:-1].upper()
            if section_name in ("GLOBAL_FILTER", "WORD_GROUPS"):
                current_section = section_name
                lines = lines[1:]  # 移除标记行

        # 处理全局过滤区域
        if current_section == "GLOBAL_FILTER":
            # 解析并添加所有非空行到全局过滤列表
            for line in lines:
                # 忽略特殊语法前缀，只提取纯文本
                if line.startswith(("!", "+", "@")):
                    continue  # 全局过滤区不支持特殊语法
                if line:
                    global_filters.append(_parse_word(line))
            continue

        # 处理词组区域
        words = lines
        group_alias = None  # 组别名（[别名] 语法）

        # 检查第一行是否为组别名（非区域标记）
        if words and words[0].startswith("[") and words[0].endswith("]"):
            potential_alias = words[0][1:-1].strip()
            # 排除区域标记（GLOBAL_FILTER, WORD_GROUPS）
            if potential_alias.upper() not in ("GLOBAL_FILTER", "WORD_GROUPS"):
                group_alias = potential_alias
                words = words[1:]  # 移除组别名行

        group_required_words = []
        group_normal_words = []
        group_filter_words = []
        group_max_count = 0  # 默认不限制

        for word in words:
            if word.startswith("@"):
                # 解析最大显示数量（只接受正整数）
                try:
                    count = int(word[1:])
                    if count > 0:
                        group_max_count = count
                except (ValueError, IndexError):
                    pass  # 忽略无效的@数字格式
            elif word.startswith("!"):
                # 组内过滤词（支持正则语法）
                filter_word = word[1:]
                parsed = _parse_word(filter_word)
                group_filter_words.append(parsed)
            elif word.startswith("+"):
                # 必须词（支持正则语法）
                req_word = word[1:]
                group_required_words.append(_parse_word(req_word))
            else:
                # 普通词（支持正则语法）
                group_normal_words.append(_parse_word(word))

        if group_required_words or group_normal_words:
            if group_normal_words:
                group_key = " ".join(w["word"] for w in group_normal_words)
            else:
                group_key = " ".join(w["word"] for w in group_required_words)

            # 生成显示名称
            # 优先级：组别名 > 行别名拼接 > 关键词拼接
            if group_alias:
                # 有组别名，直接使用
                display_name = group_alias
            else:
                # 没有组别名，拼接每行的显示名（行别名或关键词本身）
                all_words = group_normal_words + group_required_words
                display_parts = []
                for w in all_words:
                    # 优先使用行别名，否则使用关键词本身
                    part = w.get("display_name") or w["word"]
                    display_parts.append(part)
                # 用 " / " 拼接多个词
                display_name = " / ".join(display_parts) if display_parts else None

            processed_groups.append(
                {
                    "required": group_required_words,
                    "normal": group_normal_words,
                    "filters": group_filter_words,
                    "group_key": group_key,
                    "display_name": display_name,  # 可能为 None
                    "max_count": group_max_count,
                }
            )

    return processed_groups, filter_words, global_filters


def _rule_display_name(rule: RuleConfig) -> str:
    if isinstance(rule, str):
        return rule
    return rule.get("display_name") or rule.get("word", "")


def _group_matches(group: Dict, title_lower: str) -> bool:
    """Evaluate one group, including its required and group-local filter rules."""
    required_words = group.get("required", [])
    normal_words = group.get("normal", [])
    group_filters = group.get("filters", [])

    if required_words and not all(
        _word_matches(req_item, title_lower) for req_item in required_words
    ):
        return False

    if normal_words and not any(
        _word_matches(normal_item, title_lower) for normal_item in normal_words
    ):
        return False

    if any(_word_matches(filter_item, title_lower) for filter_item in group_filters):
        return False

    return True


def match_frequency_title(
    title: str,
    word_groups: List[Dict],
    filter_words: Optional[List[RuleConfig]] = None,
    global_filters: Optional[List[RuleConfig]] = None,
) -> FrequencyMatch:
    """Match a title once and return every matching group in definition order.

    ``global_filters`` and the legacy ``filter_words`` argument reject the whole
    title. A group's ``filters`` reject only that group. Callers that assign a
    title to one bucket should use ``result.groups[0]`` so ordering stays
    explicit and consistent across hot-list, RSS, and MCP paths.
    """
    if not isinstance(title, str):
        title = str(title) if title is not None else ""
    if not title.strip():
        return FrequencyMatch(accepted=False)

    title_lower = title.lower()

    for filter_item in global_filters or []:
        if _word_matches(filter_item, title_lower):
            return FrequencyMatch(
                accepted=False,
                filtered_by=f"global:{_rule_display_name(filter_item)}",
            )

    # Compatibility for callers that still pass an explicit file-level list.
    for filter_item in filter_words or []:
        if _word_matches(filter_item, title_lower):
            return FrequencyMatch(
                accepted=False,
                filtered_by=f"legacy:{_rule_display_name(filter_item)}",
            )

    if not word_groups:
        return FrequencyMatch(accepted=True)

    matched_groups = tuple(
        group for group in word_groups if _group_matches(group, title_lower)
    )
    return FrequencyMatch(accepted=bool(matched_groups), groups=matched_groups)


def matches_word_groups(
    title: str,
    word_groups: List[Dict],
    filter_words: List[RuleConfig],
    global_filters: Optional[List[RuleConfig]] = None,
) -> bool:
    """
    检查标题是否匹配词组规则

    Args:
        title: 标题文本
        word_groups: 词组列表
        filter_words: 过滤词列表（可以是字符串列表或字典列表）
        global_filters: 全局过滤词列表

    Returns:
        是否匹配
    """
    return match_frequency_title(
        title,
        word_groups,
        filter_words=filter_words,
        global_filters=global_filters,
    ).accepted


def validate_frequency_file(
    frequency_file: Union[str, Path],
    *,
    max_global_filters: int = 15,
) -> List[FrequencyValidationIssue]:
    """Validate a frequency-word file without changing runtime behaviour."""
    path = Path(frequency_file)
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    issues: List[FrequencyValidationIssue] = []

    section_lines: Dict[str, List[int]] = {"GLOBAL_FILTER": [], "WORD_GROUPS": []}
    group_aliases: Dict[str, int] = {}
    seen_rules: Dict[str, int] = {}

    for index, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1].strip()
            upper_name = name.upper()
            if upper_name in section_lines:
                section_lines[upper_name].append(index)
            elif not name:
                issues.append(
                    FrequencyValidationIssue("error", index, "词组名称不能为空")
                )
            elif name in group_aliases:
                issues.append(
                    FrequencyValidationIssue(
                        "error",
                        index,
                        f"词组 [{name}] 与第 {group_aliases[name]} 行重复",
                    )
                )
            else:
                group_aliases[name] = index
            continue

        if line.startswith("@"):
            try:
                if int(line[1:]) <= 0:
                    raise ValueError
            except ValueError:
                issues.append(
                    FrequencyValidationIssue(
                        "error", index, f"无效的数量限制：{line}"
                    )
                )
            continue

        rule_text = line[1:].strip() if line.startswith(("!", "+")) else line
        word_config = re.split(r"\s*=>\s*", rule_text, maxsplit=1)[0].strip()
        duplicate_key = f"{line[:1] if line.startswith(('!', '+')) else ''}{word_config}"
        if duplicate_key in seen_rules:
            issues.append(
                FrequencyValidationIssue(
                    "warning",
                    index,
                    f"规则与第 {seen_rules[duplicate_key]} 行重复：{duplicate_key}",
                )
            )
        else:
            seen_rules[duplicate_key] = index

        if word_config.startswith("/"):
            regex_match = re.match(r"^/(.+)/[a-z]*$", word_config)
            if not regex_match:
                issues.append(
                    FrequencyValidationIssue(
                        "error", index, "正则规则缺少结束分隔符 /"
                    )
                )
                continue
            try:
                re.compile(regex_match.group(1), re.IGNORECASE)
            except re.error as exc:
                issues.append(
                    FrequencyValidationIssue(
                        "error", index, f"无效正则：{exc}"
                    )
                )

    for section_name, locations in section_lines.items():
        if len(locations) != 1:
            issues.append(
                FrequencyValidationIssue(
                    "error",
                    locations[0] if locations else 0,
                    f"[{section_name}] 应且只能出现一次，实际 {len(locations)} 次",
                )
            )

    if (
        section_lines["GLOBAL_FILTER"]
        and section_lines["WORD_GROUPS"]
        and section_lines["GLOBAL_FILTER"][0] > section_lines["WORD_GROUPS"][0]
    ):
        issues.append(
            FrequencyValidationIssue(
                "error", section_lines["GLOBAL_FILTER"][0], "全局过滤区必须位于词组区之前"
            )
        )

    word_groups, _, global_filters = load_frequency_words(str(path))
    if len(global_filters) > max_global_filters:
        issues.append(
            FrequencyValidationIssue(
                "warning",
                section_lines["GLOBAL_FILTER"][0]
                if section_lines["GLOBAL_FILTER"]
                else 0,
                f"全局过滤规则共 {len(global_filters)} 条，建议不超过 {max_global_filters} 条",
            )
        )

    for group in word_groups:
        rule_count = len(group.get("normal", [])) + len(group.get("required", []))
        if group.get("max_count", 0) <= 0 and rule_count >= 8:
            issues.append(
                FrequencyValidationIssue(
                    "warning",
                    group_aliases.get(group.get("display_name", ""), 0),
                    f"高频候选组 [{group.get('display_name') or group.get('group_key')}] 含 {rule_count} 条规则但未设置 @数量限制",
                )
            )

    return issues
