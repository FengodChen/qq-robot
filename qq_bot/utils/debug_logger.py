"""Debug 日志工具模块。

提供统一的调试日志输出格式，支持 LLM 上下文、普通调试信息等多种场景。
"""

from typing import List, Optional, Dict, Any
from datetime import datetime


# 角色标签映射（带颜色圆形 emoji）
ROLE_EMOJI = {
    "system": "🟢 SYSTEM",
    "user": "🔵 USER",
    "assistant": "🟣 ASSISTANT",
}

# 分隔线宽度
LINE_WIDTH = 80
# 内容截断阈值
CONTENT_TRUNCATE_THRESHOLD = 1000
# 截断后保留的字符数
CONTENT_TRUNCATE_KEEP = 500


def _print_separator(char: str = "=") -> None:
    """打印分隔线。"""
    print(char * LINE_WIDTH)


def _print_sub_separator(char: str = "─") -> None:
    """打印子分隔线。"""
    print(char * LINE_WIDTH)


def _format_role(role: str) -> str:
    """格式化角色标签。
    
    Args:
        role: 角色名称 (system/user/assistant)
        
    Returns:
        带 emoji 的角色标签
    """
    return ROLE_EMOJI.get(role.lower(), role.upper())


def _truncate_content(content: str, threshold: int = CONTENT_TRUNCATE_THRESHOLD, 
                      keep: int = CONTENT_TRUNCATE_KEEP) -> str:
    """智能截断长内容。
    
    Args:
        content: 原始内容
        threshold: 截断阈值
        keep: 截断后保留的字符数（前后各保留这么多）
        
    Returns:
        截断后的内容
    """
    if len(content) <= threshold:
        return content
    
    omitted = len(content) - 2 * keep
    return f"{content[:keep]}\n[... 省略 {omitted} 字符 ...]\n{content[-keep:]}"


def log_llm_context(
    title: str, 
    messages: List[Any], 
    model: Optional[str] = None,
    extra_info: Optional[Dict[str, Any]] = None
) -> None:
    """输出 LLM 上下文调试信息。
    
    统一的格式：
    - 80字符宽度的分隔线
    - 带 emoji 的角色标签
    - 消息序号
    - 模型信息（如有）
    - 消息数量统计
    - 长内容智能截断
    
    Args:
        title: 标题，描述这是什么上下文
        messages: 消息列表，每个消息需要有 role 和 content 属性
        model: 模型名称（可选）
        extra_info: 额外信息字典（可选）
    """
    # 头部信息
    print()
    _print_separator()
    
    # 构建标题行
    header_parts = [f"[DEBUG] 💬 {title}"]
    if model:
        header_parts.append(f"模型: {model}")
    header_parts.append(f"消息数: {len(messages)}")
    
    header = " | ".join(header_parts)
    print(header)
    _print_separator()
    
    # 消息内容
    for i, msg in enumerate(messages, 1):
        role = getattr(msg, 'role', 'unknown')
        content = getattr(msg, 'content', str(msg))
        
        role_display = _format_role(role)
        print(f"\n【{i}】{role_display}")
        _print_sub_separator()
        print(_truncate_content(content))
    
    # 额外信息
    if extra_info:
        print()
        _print_sub_separator("·")
        for key, value in extra_info.items():
            print(f"• {key}: {value}")
        _print_sub_separator("·")
    
    # 尾部
    _print_separator()
    print()


def log_simple_debug(title: str, message: str) -> None:
    """输出简单的调试信息。
    
    Args:
        title: 标题
        message: 消息内容
    """
    print(f"[DEBUG] {title}: {message}")


def log_debug_block(title: str, content: str) -> None:
    """输出带分隔线的调试信息块。
    
    适用于多行内容的调试输出。
    
    Args:
        title: 标题
        content: 内容
    """
    print()
    _print_separator()
    print(f"[DEBUG] {title}")
    _print_separator()
    print(content)
    _print_separator()
    print()


def log_compact_debug(title: str, **kwargs) -> None:
    """输出紧凑的调试信息。
    
    适用于显示多个键值对的场景。
    
    Args:
        title: 标题
        **kwargs: 键值对
    """
    parts = [f"[DEBUG] {title}"]
    for key, value in kwargs.items():
        parts.append(f"{key}={value}")
    print(" | ".join(parts))
