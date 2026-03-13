"""时间处理工具。

提供时间解析、格式化等通用功能。
"""

import re
from typing import Tuple


# 时间单位换算（秒）
TIME_UNITS = {
    "s": 1,
    "sec": 1,
    "second": 1,
    "秒": 1,
    "m": 60,
    "min": 60,
    "minute": 60,
    "分钟": 60,
    "h": 3600,
    "hr": 3600,
    "hour": 3600,
    "小时": 3600,
    "d": 86400,
    "day": 86400,
    "天": 86400,
    "w": 604800,
    "week": 604800,
    "周": 604800,
}


def parse_duration(text: str) -> Tuple[int, str]:
    """解析时间长度字符串。
    
    支持格式：
    - 数字+单位："1h", "30m", "2d"
    - 中文格式："1小时", "30分钟", "2天"
    - 小数："1.5h", "2.5天"
    
    Args:
        text: 时间字符串。
        
    Returns:
        (秒数, 显示文本) 元组。
        
    Example:
        >>> parse_duration("1h")
        (3600, '1小时')
        >>> parse_duration("30分钟")
        (1800, '30分钟')
    """
    text = text.strip().lower()
    
    # 匹配数字和单位
    pattern = r"^(\d+(?:\.\d+)?)\s*([a-zA-Z\u4e00-\u9fff]+)$"
    match = re.match(pattern, text)
    
    if not match:
        # 尝试从常用格式解析
        return _parse_common_duration(text)
    
    value = float(match.group(1))
    unit = match.group(2)
    
    # 查找单位对应的秒数
    seconds = TIME_UNITS.get(unit)
    if seconds is None:
        # 尝试模糊匹配
        for key, sec in TIME_UNITS.items():
            if unit.startswith(key) or key.startswith(unit):
                seconds = sec
                break
    
    if seconds is None:
        raise ValueError(f"未知的时间单位: {unit}")
    
    total_seconds = int(value * seconds)
    display = format_duration(total_seconds)
    
    return total_seconds, display


def _parse_common_duration(text: str) -> Tuple[int, str]:
    """解析常用时间格式。"""
    text = text.strip()
    
    # 半天
    if text in ["半天", "half day"]:
        return 43200, "半天"
    
    # 一天
    if text in ["一天", "1天", "1d"]:
        return 86400, "1天"
    
    # 一周
    if text in ["一周", "七天", "7天", "7d", "1w"]:
        return 604800, "一周"
    
    raise ValueError(f"无法解析时间格式: {text}")


def format_duration(seconds: int) -> str:
    """将秒数格式化为易读的时间文本。
    
    Args:
        seconds: 秒数。
        
    Returns:
        格式化的时间文本。
        
    Example:
        >>> format_duration(3600)
        '1小时'
        >>> format_duration(86400)
        '1天'
    """
    if seconds < 60:
        return f"{seconds}秒"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}分钟"
    elif seconds < 86400:
        hours = seconds // 3600
        remaining = seconds % 3600
        if remaining == 0:
            return f"{hours}小时"
        minutes = remaining // 60
        return f"{hours}小时{minutes}分钟"
    elif seconds < 604800:
        days = seconds // 86400
        return f"{days}天"
    else:
        weeks = seconds // 604800
        return f"{weeks}周"


def parse_natural_time(message: str) -> Tuple[int | None, str]:
    """从自然语言消息中解析时间。
    
    Args:
        message: 自然语言消息。
        
    Returns:
        (秒数, 描述) 元组。如果无法解析返回 (None, "")。
        
    Example:
        >>> parse_natural_time("总结过去30分钟的聊天")
        (1800, '30分钟')
        >>> parse_natural_time("总结今天的聊天记录")
        (86400, '1天')
    """
    message = message.lower()
    
    # 匹配模式：X分钟/小时/天
    patterns = [
        # 数字+单位
        (r"(\d+(?:\.\d+)?)\s*(分钟|分|m|min)", 60),
        (r"(\d+(?:\.\d+)?)\s*(小时|时|h|hr|hour)", 3600),
        (r"(\d+(?:\.\d+)?)\s*(天|日|d|day)", 86400),
        # 中文数字+单位
        (r"(半)\s*(小时|时)", 1800),
        (r"一\s*(小时|时)", 3600),
        (r"两\s*(小时|时)", 7200),
    ]
    
    for pattern, multiplier in patterns:
        match = re.search(pattern, message)
        if match:
            value_str = match.group(1)
            if value_str == "半":
                value = 0.5
            else:
                try:
                    value = float(value_str)
                except ValueError:
                    continue
            seconds = int(value * multiplier)
            return seconds, format_duration(seconds)
    
    # 特殊词汇
    if "今天" in message or "今日" in message:
        return 86400, "1天"
    if "昨天" in message:
        return 86400, "1天"
    if "半天" in message:
        return 43200, "半天"
    if "一周" in message or "一星期" in message or "七天" in message:
        return 604800, "一周"
    
    return None, ""
