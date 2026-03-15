"""文本处理工具。

提供消息文本提取、清理等通用功能。
"""

import re
from typing import Any


def extract_text(message: Any) -> str:
    """从消息中提取纯文本内容。
    
    支持多种消息格式：
    - 字符串：直接返回
    - 列表（消息段数组）：提取所有 text 类型的内容
    - 其他：转为字符串返回
    
    Args:
        message: 消息内容，可以是字符串、消息段列表等。
        
    Returns:
        纯文本内容。
        
    Example:
        >>> extract_text("你好")
        '你好'
        >>> extract_text([{"type": "text", "data": {"text": "Hello"}}])
        'Hello'
    """
    if isinstance(message, str):
        return message
    
    if isinstance(message, list):
        texts = []
        for segment in message:
            if isinstance(segment, dict):
                if segment.get("type") == "text":
                    text = segment.get("data", {}).get("text", "")
                    texts.append(text)
        return "".join(texts)
    
    return str(message)


def convert_at_to_text(
    text: str,
    user_map: dict[int, str] | None = None,
    self_id: int | None = None,
    self_name: str = "我"
) -> str:
    """将 CQ:at 代码转换为可读的 @提及格式。
    
    将 [CQ:at,qq=123456] 转换为 @昵称 或 @123456，保留提及的语义信息。
    
    Args:
        text: 原始文本，包含 CQ:at 代码。
        user_map: 用户 ID 到昵称的映射字典，可选。
        self_id: 机器人自己的 QQ 号，用于特殊标识对自己的提及。
        self_name: 对自己的提及显示的名称，默认为 "我"。
        
    Returns:
        转换后的文本，CQ:at 代码被替换为 @提及。
        
    Example:
        >>> convert_at_to_text("[CQ:at,qq=123456] 你好")
        '@123456 你好'
        >>> convert_at_to_text("[CQ:at,qq=123456] 你好", user_map={123456: "小明"})
        '@小明 你好'
        >>> convert_at_to_text("[CQ:at,qq=10000] 你好", self_id=10000, self_name="机器人")
        '@机器人 你好'
    """
    def replace_at(match: re.Match) -> str:
        qq_str = match.group(1)
        try:
            qq = int(qq_str)
        except ValueError:
            return match.group(0)  # 保留原始格式如果解析失败
        
        # 如果是提及自己，使用特殊名称
        if self_id is not None and qq == self_id:
            return f"@{self_name}"
        
        # 如果有昵称映射，使用昵称
        if user_map and qq in user_map:
            return f"@{user_map[qq]}"
        
        # 默认使用 QQ 号
        return f"@{qq_str}"
    
    # 匹配 [CQ:at,qq=数字] 格式
    pattern = r"\[CQ:at,qq=(\d+)\]"
    return re.sub(pattern, replace_at, text)


def clean_at_text(
    text: str, 
    at_pattern: str | None = None,
    user_map: dict[int, str] | None = None,
    self_id: int | None = None,
    self_name: str = "我"
) -> str:
    """清理文本中的 @ 标记，保留提及信息。
    
    将 CQ:at 代码转换为可读的 @提及格式，而不是直接删除。
    这样 LLM 可以理解消息中提到了谁。
    
    Args:
        text: 原始文本。
        at_pattern: @ 的正则模式（已弃用，保留用于兼容性）。
        user_map: 用户 ID 到昵称的映射字典，可选。
        self_id: 机器人自己的 QQ 号。
        self_name: 对自己的提及显示的名称。
        
    Returns:
        清理后的文本，CQ:at 代码被替换为 @提及。
        
    Example:
        >>> clean_at_text("[CQ:at,qq=123456] 你好")
        '@123456 你好'
        >>> clean_at_text("[CQ:at,qq=123456] 你好", user_map={123456: "小明"})
        '@小明 你好'
    """
    # 使用 convert_at_to_text 进行转换
    cleaned = convert_at_to_text(text, user_map=user_map, self_id=self_id, self_name=self_name)
    # 清理多余空白
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """截断文本到指定长度。
    
    Args:
        text: 原始文本。
        max_length: 最大长度。
        suffix: 截断后添加的后缀。
        
    Returns:
        截断后的文本。
        
    Example:
        >>> truncate_text("这是一个很长的文本", 5)
        '这是一个...'
    """
    if len(text) <= max_length:
        return text
    
    return text[:max_length - len(suffix)] + suffix


def extract_qq_from_at(text: str) -> int | None:
    """从 @ 标记中提取 QQ 号。
    
    Args:
        text: 包含 @ 标记的文本。
        
    Returns:
        QQ 号，如果没有找到则返回 None。
        
    Example:
        >>> extract_qq_from_at("[CQ:at,qq=123456]")
        123456
    """
    match = re.search(r"\[CQ:at,qq=(\d+)\]", text)
    if match:
        return int(match.group(1))
    return None


def extract_all_qq_from_at(text: str) -> list[int]:
    """从文本中提取所有 @ 提及的 QQ 号。
    
    Args:
        text: 包含 @ 标记的文本。
        
    Returns:
        QQ 号列表，如果没有找到则返回空列表。
        
    Example:
        >>> extract_all_qq_from_at("[CQ:at,qq=123456] 你好 [CQ:at,qq=789012]")
        [123456, 789012]
    """
    matches = re.findall(r"\[CQ:at,qq=(\d+)\]", text)
    return [int(qq) for qq in matches]


def is_at_me(text: str, self_id: int) -> bool:
    """检查文本是否 @ 了指定 QQ。
    
    Args:
        text: 消息文本。
        self_id: 自己的 QQ 号。
        
    Returns:
        是否被 @。
    """
    pattern = rf"\[CQ:at,qq={self_id}\]"
    return bool(re.search(pattern, text))


def count_tokens_approx(text: str) -> int:
    """估算文本的 token 数量（近似值）。
    
    使用简单的字符数除以 4 来估算，适用于中英文混合文本。
    实际 token 数需要使用 tiktoken 等库精确计算。
    
    Args:
        text: 输入文本。
        
    Returns:
        估算的 token 数量。
    """
    if not text:
        return 0
    
    # 简单估算：每个汉字约 1 token，每个英文单词约 0.25 token
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    english_words = len(re.findall(r"[a-zA-Z]+", text))
    other_chars = len(text) - chinese_chars - english_words
    
    return chinese_chars + english_words // 4 + other_chars // 2


def sanitize_for_display(text: str, max_lines: int = 3) -> str:
    """清理文本用于显示。
    
    移除换行符、多余的空格等。
    
    Args:
        text: 原始文本。
        max_lines: 保留的最大行数。
        
    Returns:
        清理后的文本。
    """
    # 统一换行符
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    
    # 分割行并限制行数
    lines = text.split("\n")
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines.append("...")
    
    # 清理每行的空格
    lines = [line.strip() for line in lines if line.strip()]
    
    return " | ".join(lines)
