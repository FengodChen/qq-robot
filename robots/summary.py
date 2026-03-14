"""
robots/summary.py

Summary robot: summarize recent private/group chat history using DeepSeek API
as the persona defined in robots.chat.BotConfig (音理).

=== METADATA ===
name: summary
desc: 聊天记录总结模式，支持时间窗口选择
cmds: 支持自然语言指令，如"帮助"、"总结"、"统计"
=== END ===

Commands (in group or private):
  /summary [window] [max_tokens]
  /总结 [window] [max_tokens]

window: 5m | 1h | 3h | 12h | 1d (also accepts Chinese labels like "5分钟", "半天", "一天").
max_tokens: optional integer to limit token usage (default 4000, max 8000).

Design notes:
 - Reads from SQLite message store (data/messages.db) instead of chat_history.json
 - Uses intelligent sampling strategies to reduce token consumption:
   1. Hierarchical sampling: prioritize active users
   2. Time-based aggregation: group messages into time buckets
   3. Deduplication: remove repetitive content
   4. Smart truncation: keep important parts of long messages
 - Controls max token budget to avoid excessive API costs
"""

import os
import json
import time
import re
import random
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, Tuple, List, Dict, Set
from collections import defaultdict

from deepseek_api import DeepSeekAPI

# Import BotConfig for type reference
try:
    from robots.chat import BotConfig as ChatBotConfig
    DEFAULT_CONFIG = ChatBotConfig()
except Exception:
    DEFAULT_CONFIG = None

# Import message store（已移到主目录）
try:
    from message_store import get_message_store, MessageStore
    MESSAGE_STORE_AVAILABLE = True
except Exception as e:
    print(f"[!] MessageStore not available: {e}")
    MESSAGE_STORE_AVAILABLE = False


def parse_time_window(token: Optional[str]) -> Tuple[int, str]:
    """Parse time window string to seconds."""
    if not token:
        return 3600, "1小时"
    s = token.strip().lower()
    # Common mappings
    if s in ("5m", "5min", "5分钟"):
        return 5 * 60, "5分钟"
    if s in ("1h", "1小时"):
        return 60 * 60, "1小时"
    if s in ("3h", "3小时"):
        return 3 * 60 * 60, "3小时"
    if s in ("6h", "6小时"):
        return 6 * 60 * 60, "6小时"
    if s in ("12h", "12小时", "半天"):
        return 12 * 60 * 60, "半天"
    if s in ("1d", "24h", "1天", "一天"):
        return 24 * 60 * 60, "一天"
    if s in ("3d", "3天"):
        return 3 * 24 * 60 * 60, "3天"
    if s in ("7d", "7天", "一周"):
        return 7 * 24 * 60 * 60, "一周"
    # Generic parse like '30m', '2h', '2.5h' - 支持小数
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(m|min|分钟)$", s)
    if m:
        val = float(m.group(1))
        display = f"{int(val)}分钟" if val == int(val) else f"{val:.1f}分钟"
        return int(val * 60), display
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(h|小时)$", s)
    if m:
        val = float(m.group(1))
        display = f"{int(val)}小时" if val == int(val) else f"{val:.1f}小时"
        return int(val * 3600), display
    m = re.match(r"^(\d+(?:\.\d+)?)\s*(d|天)$", s)
    if m:
        val = float(m.group(1))
        display = f"{int(val)}天" if val == int(val) else f"{val:.1f}天"
        return int(val * 86400), display
    # Fallback
    return 3600, "1小时"


# 最大支持的时间范围：3天（单位：秒）
MAX_SUMMARY_WINDOW_SECONDS = 3 * 24 * 60 * 60  # 259200秒


def parse_natural_time_window(message: str) -> Tuple[Optional[int], Optional[str], str]:
    """
    从自然语言消息中解析时间窗口。
    
    支持的自然语言表达：
    - "总结过去30分钟的聊天"
    - "总结一下2小时内的消息"  
    - "总结今天的聊天记录"
    - "总结过去1天半的聊天"
    - "总结3天内的消息"
    - "总结过去的48小时"
    - "总结最近2.5小时的聊天"
    
    返回: (seconds, display_text, error_message)
    - seconds: 解析出的秒数，如果超出范围或为None则返回None
    - display_text: 用于显示的时间描述
    - error_message: 错误提示信息，成功时为空字符串
    """
    if not message:
        return 3600, "1小时", ""
    
    message = message.strip()
    message_lower = message.lower()
    
    # ========== 第一步：检测超出范围的请求 ==========
    # 检测 "X天" 且 X > 3
    large_day_patterns = [
        r"(\d+(?:\.\d+)?)\s*个?\s*天",
        r"(\d+(?:\.\d+)?)\s*d(?:ay)?s?",
    ]
    for pattern in large_day_patterns:
        for match in re.finditer(pattern, message_lower):
            try:
                days = float(match.group(1))
                if days > 3:
                    return None, None, f"❌ 你想总结{days}天的聊天记录呀？这太久了呢~\n最多只能总结最近3天的内容哦！"
            except (ValueError, IndexError):
                continue
    
    # 检测 "一周"、"七天"、"7天" 等
    if re.search(r"一?\s*周|七\s*天|7\s*天", message):
        return None, None, "❌ 你想总结一周的聊天记录呀？这太久了呢~\n最多只能总结最近3天的内容哦！"
    
    # ========== 第二步：直接解析（兼容原有格式如 "1h", "30m" 等） ==========
    tokens = message.split()
    for token in tokens:
        token_lower = token.lower()
        # 尝试直接匹配格式如 "1h", "30m", "2d", "1.5小时"
        direct_match = re.match(r"^(\d+(?:\.\d+)?)\s*([hmd]|小时|分钟|天)$", token_lower)
        if direct_match:
            value = float(direct_match.group(1))
            unit = direct_match.group(2)
            seconds, display = _convert_to_seconds(value, unit)
            if seconds > MAX_SUMMARY_WINDOW_SECONDS:
                return None, None, f"❌ 时间范围太大了啦！最多只能总结最近3天的聊天记录哦~\n"
            return seconds, display, ""
    
    # ========== 第三步：自然语言解析 ==========
    # 按优先级排序：先匹配更具体的模式
    
    # 天相关 - 先检查（避免"今天"被小时模式抢先）
    day_patterns = [
        # X天 / X.d天
        (r"(\d+(?:\.\d+)?)\s*个?\s*天", _parse_day_pattern),
        (r"(\d+(?:\.\d+)?)\s*d(?:ay)?s?", _parse_day_pattern),
        # 今天
        (r"今\s*天", lambda m: (24 * 3600, "1天")),
        # 昨 天 (算作1天)
        (r"昨\s*天", lambda m: (24 * 3600, "1天")),
        # 前天 (算作2天)
        (r"前\s*天", lambda m: (2 * 24 * 3600, "2天")),
        # 这两/三天
        (r"[这|那|上|近]\s*两\s*天", lambda m: (2 * 86400, "2天")),
        (r"[这|那|上|近]\s*三\s*天", lambda m: (3 * 86400, "3天")),
    ]
    
    for pattern, parser in day_patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            seconds, display = parser(match)
            if seconds > MAX_SUMMARY_WINDOW_SECONDS:
                return None, None, f"❌ 时间范围太大了啦！最多只能总结最近3天的聊天记录哦~\n"
            return seconds, display, ""
    
    # 小时相关 - 注意：先匹配更长的模式，避免"一个半小时"被匹配成"半小时"
    hour_patterns = [
        # X个半小时 / X.5小时 - 必须先匹配（如"一个半小时"、"2个半小时"）
        # 支持阿拉伯数字和中文数字
        (r"([\d一二两三四五六七八九十]+)\s*个?\s*半\s*个?\s*小?时", _parse_hour_and_half),
        # 一小时半 - 特殊处理
        (r"一\s*小?时\s*半", lambda m: (5400, "1.5小时")),
        (r"(\d+(?:\.\d+)?)\s*个?\s*小?时", _parse_hour_pattern),
        (r"(\d+(?:\.\d+)?)\s*h(?:our)?s?", _parse_hour_pattern),
        # 半天
        (r"半\s*天", lambda m: (12 * 3600, "半天")),
        # 一小时 / 1小时
        (r"一\s*小?时", lambda m: (3600, "1小时")),
        (r"两\s*小?时", lambda m: (2 * 3600, "2小时")),
        # 半小时 - 放在最后，避免抢先匹配"一个半小时"
        (r"半\s*小?时", lambda m: (1800, "30分钟")),
    ]
    
    for pattern, parser in hour_patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            seconds, display = parser(match)
            if seconds > MAX_SUMMARY_WINDOW_SECONDS:
                return None, None, f"❌ 时间范围太大了啦！最多只能总结最近3天的聊天记录哦~\n"
            return seconds, display, ""
    
    # 分钟相关
    minute_patterns = [
        (r"(\d+(?:\.\d+)?)\s*分(?:钟|鈡)?", _parse_minute_pattern),
        (r"(\d+(?:\.\d+)?)\s*m(?:in)?", _parse_minute_pattern),
    ]
    
    for pattern, parser in minute_patterns:
        match = re.search(pattern, message, re.IGNORECASE)
        if match:
            seconds, display = parser(match)
            if seconds > MAX_SUMMARY_WINDOW_SECONDS:
                return None, None, f"❌ 时间范围太大了啦！最多只能总结最近3天的聊天记录哦~\n"
            return seconds, display, ""
    
    # ========== 默认返回1小时 ==========
    return 3600, "1小时", ""


def _convert_to_seconds(value: float, unit: str) -> Tuple[int, str]:
    """将数值和单位转换为秒数和显示文本"""
    unit = unit.lower()
    if unit in ("m", "min", "分钟"):
        seconds = int(value * 60)
        return seconds, f"{value}分钟" if value == int(value) else f"{value:.1f}分钟"
    elif unit in ("h", "hour", "小时"):
        seconds = int(value * 3600)
        return seconds, f"{value}小时" if value == int(value) else f"{value:.1f}小时"
    elif unit in ("d", "day", "天"):
        seconds = int(value * 86400)
        return seconds, f"{value}天" if value == int(value) else f"{value:.1f}天"
    return 3600, "1小时"


def _parse_hour_pattern(match) -> Tuple[int, str]:
    """解析小时模式"""
    value_str = match.group(1)
    total_hours = float(value_str)
    
    seconds = int(total_hours * 3600)
    if total_hours == int(total_hours):
        display = f"{int(total_hours)}小时"
    else:
        display = f"{total_hours:.1f}小时"
    return seconds, display


def _parse_hour_and_half(match) -> Tuple[int, str]:
    """解析X个半小时的模式，如'一个半小时'、'2个半小时'"""
    value_str = match.group(1)
    # 支持中文数字
    cn_numbers = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
                  '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
    if value_str in cn_numbers:
        hours = cn_numbers[value_str]
    else:
        hours = int(value_str)
    total_hours = hours + 0.5
    
    seconds = int(total_hours * 3600)
    display = f"{total_hours:.1f}小时"
    return seconds, display


def _parse_minute_pattern(match) -> Tuple[int, str]:
    """解析分钟模式"""
    value = float(match.group(1))
    seconds = int(value * 60)
    if value == int(value):
        display = f"{int(value)}分钟"
    else:
        display = f"{value:.1f}分钟"
    return seconds, display


def _parse_day_pattern(match) -> Tuple[int, str]:
    """解析天模式"""
    value = float(match.group(1))
    seconds = int(value * 86400)
    if value == int(value):
        display = f"{int(value)}天"
    else:
        display = f"{value:.1f}天"
    return seconds, display


# === AGENT-FRIENDLY-API START ===

def create_summary_robot_instance(config=None):
    """Return a SummaryRobot instance for programmatic use."""
    return SummaryRobot(config)


async def async_summarize_window(group_id, user_id=None, window="1h", max_tokens=4000, config=None):
    """Async wrapper that summarizes recent history and returns DeepSeek result."""
    seconds, window_text = parse_time_window(window)
    robot = SummaryRobot(config)
    return await robot._generate_and_summarize(
        group_id=group_id, user_id=user_id, seconds=seconds, 
        max_tokens=max_tokens, window_text=window_text
    )


def summarize_window(group_id, user_id=None, window="1h", max_tokens=4000, config=None):
    """Synchronous wrapper that runs async_summarize_window and returns result."""
    return asyncio.run(async_summarize_window(group_id, user_id, window, max_tokens, config))

# === AGENT-FRIENDLY-API END ===


class MessageSampler:
    """
    消息采样器：实现多种策略来控制token消耗
    
    策略：
    1. 分层采样：按用户活跃度分层，活跃用户多采样
    2. 时间分桶：将消息按时间段分组，每个时间段内采样
    3. 去重压缩：去除重复和无意义内容
    4. 智能截断：长消息保留关键部分
    """
    
    # 估算每个token约1.5个中文字符或4个英文字符
    CHARS_PER_TOKEN = 1.5
    
    def __init__(self, max_tokens: int = 4000):
        self.max_tokens = max_tokens
        # 预留一部分token给prompt和输出
        self.max_content_tokens = int(max_tokens * 0.7)  # 70%用于内容
        self.estimated_max_chars = int(self.max_content_tokens * self.CHARS_PER_TOKEN)
    
    def estimate_tokens(self, text: str) -> int:
        """估算文本的token数量"""
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(text) - chinese_chars
        # 中文字符按1 token/字，其他按0.3 token/字符
        return int(chinese_chars + other_chars * 0.3)
    
    def clean_content(self, content: str) -> str:
        """清理消息内容"""
        if not content:
            return ""
        # 去除CQ码
        content = re.sub(r'\[CQ:.+?\]', '', content)
        # 去除URL
        content = re.sub(r'https?://\S+', '[链接]', content)
        # 合并空白
        content = re.sub(r'\s+', ' ', content)
        # 去除无意义内容
        meaningless = ['赞', '👍', 'ok', '好的', '嗯', '哦', '啊', '呵呵', '哈哈哈']
        if content.strip() in meaningless:
            return content.strip()
        return content.strip()
    
    def is_meaningful(self, content: str) -> bool:
        """判断消息是否有意义"""
        if not content:
            return False
        content = content.strip()
        # 过滤纯表情
        if re.match(r'^[\[\]\s\ud83c[\udf00-\udfff]|\ud83d[\udc00-\ude4f]|\ud83d[\ude80-\udeff]|\u2600-\u26ff\u2700-\u27bf]+$', content):
            return False
        # 过滤过短的消息（除非是有效短句）
        if len(content) < 2:
            return False
        return True
    
    def truncate_content(self, content: str, max_len: int = 200) -> str:
        """智能截断长消息"""
        if len(content) <= max_len:
            return content
        
        # 尝试在句子边界截断
        sentences = re.split(r'([。！？.!?])', content)
        result = ""
        for i in range(0, len(sentences), 2):
            sentence = sentences[i]
            if i + 1 < len(sentences):
                sentence += sentences[i + 1]
            if len(result) + len(sentence) > max_len:
                break
            result += sentence
        
        if result:
            return result + "..."
        # 如果找不到句子边界，直接截断
        return content[:max_len] + "..."
    
    def hierarchical_sample(self, messages: List[Dict], target_count: int) -> List[Dict]:
        """
        分层采样：
        1. 计算每个用户的消息数量
        2. 按活跃度分层：高频(>20条)、中频(5-20条)、低频(<5条)
        3. 按层分配采样配额
        """
        if len(messages) <= target_count:
            return messages
        
        # 按用户分组
        user_messages = defaultdict(list)
        for m in messages:
            user_messages[m['user_id']].append(m)
        
        # 按活跃度分层
        high_freq = []  # >20条
        mid_freq = []   # 5-20条
        low_freq = []   # <5条
        
        for uid, msgs in user_messages.items():
            if len(msgs) > 20:
                high_freq.append((uid, msgs))
            elif len(msgs) >= 5:
                mid_freq.append((uid, msgs))
            else:
                low_freq.append((uid, msgs))
        
        # 按活跃度排序
        high_freq.sort(key=lambda x: len(x[1]), reverse=True)
        mid_freq.sort(key=lambda x: len(x[1]), reverse=True)
        
        # 分配配额：高频30%，中频40%，低频30%
        result = []
        remaining = target_count
        
        # 高频用户：保证每个至少有几条
        high_quota = min(int(target_count * 0.3), len(high_freq) * 5)
        for uid, msgs in high_freq:
            if high_quota <= 0:
                break
            sample_size = max(2, min(len(msgs), high_quota // max(1, len(high_freq))))
            # 均匀采样
            step = max(1, len(msgs) // sample_size)
            sampled = msgs[::step][:sample_size]
            result.extend(sampled)
            high_quota -= len(sampled)
        
        # 中频用户：均匀分配
        mid_quota = int(target_count * 0.4)
        for uid, msgs in mid_freq:
            if mid_quota <= 0:
                break
            sample_size = max(1, min(len(msgs), mid_quota // max(1, len(mid_freq))))
            sampled = random.sample(msgs, sample_size) if len(msgs) > sample_size else msgs
            result.extend(sampled)
            mid_quota -= len(sampled)
        
        # 低频用户：全部包含或随机采样
        low_quota = target_count - len(result)
        if low_freq and low_quota > 0:
            low_all = []
            for uid, msgs in low_freq:
                low_all.extend(msgs)
            if len(low_all) > low_quota:
                result.extend(random.sample(low_all, low_quota))
            else:
                result.extend(low_all)
        
        # 按时间排序
        result.sort(key=lambda x: x['timestamp'])
        return result
    
    def time_bucket_sample(self, messages: List[Dict], bucket_minutes: int = 10) -> List[Dict]:
        """
        时间分桶采样：将消息按时间段分组，每个桶内保留有代表性的消息
        """
        if not messages:
            return []
        
        # 按时间分桶
        buckets = defaultdict(list)
        for m in messages:
            bucket_key = m['timestamp'] // (bucket_minutes * 60)
            buckets[bucket_key].append(m)
        
        result = []
        for bucket_key in sorted(buckets.keys()):
            bucket = buckets[bucket_key]
            if len(bucket) <= 3:
                # 少量消息全部保留
                result.extend(bucket)
            else:
                # 大量消息：保留首尾和中间采样
                result.append(bucket[0])  # 第一条
                if len(bucket) > 5:
                    # 中间均匀采样
                    middle = bucket[1:-1]
                    step = max(1, len(middle) // 2)
                    result.extend(middle[::step][:2])
                result.append(bucket[-1])  # 最后一条
        
        return result
    
    def deduplicate(self, messages: List[Dict]) -> List[Dict]:
        """去除重复或高度相似的消息"""
        if not messages:
            return []
        
        result = []
        seen_hashes = set()
        
        for m in messages:
            content = self.clean_content(m.get('content', ''))
            if not self.is_meaningful(content):
                continue
            
            # 计算内容指纹（前20个字符）
            fingerprint = content[:20].lower()
            if fingerprint in seen_hashes:
                continue
            
            seen_hashes.add(fingerprint)
            m['content'] = content
            result.append(m)
        
        return result
    
    def sample(self, messages: List[Dict]) -> Tuple[List[Dict], Dict]:
        """
        执行完整的采样流程
        
        Returns:
            (sampled_messages, stats)
        """
        if not messages:
            return [], {"original": 0, "after_dedup": 0, "final": 0, "estimated_tokens": 0}
        
        original_count = len(messages)
        
        # Step 1: 去重和清理
        messages = self.deduplicate(messages)
        after_dedup = len(messages)
        
        # Step 2: 根据token预算估算目标消息数
        # 假设每条消息平均占用约100字符（含元数据）
        avg_chars_per_msg = 100
        target_count = max(20, self.estimated_max_chars // avg_chars_per_msg)
        
        # Step 3: 如果消息还是太多，进行分层采样
        if len(messages) > target_count:
            messages = self.hierarchical_sample(messages, target_count)
        
        # Step 4: 时间分桶优化
        messages = self.time_bucket_sample(messages)
        
        # Step 5: 内容截断
        for m in messages:
            content = m.get('content', '')
            if len(content) > 200:
                m['content'] = self.truncate_content(content, 200)
        
        # 估算token
        total_text = "\n".join([
            f"{m.get('nickname', '未知')}: {m.get('content', '')}"
            for m in messages
        ])
        estimated_tokens = self.estimate_tokens(total_text)
        
        stats = {
            "original": original_count,
            "after_dedup": after_dedup,
            "final": len(messages),
            "estimated_tokens": estimated_tokens
        }
        
        return messages, stats


class SummaryRobot:
    """Robot that summarizes recent chat using DeepSeek as 音理."""

    def __init__(self, config: Optional[object] = None):
        self.config = config or DEFAULT_CONFIG
        self.api_key = getattr(self.config, 'deepseek_api_key', None) if self.config is not None else None
        self.use_ai = bool(self.api_key)
        self.api = DeepSeekAPI(api_key=self.api_key) if self.use_ai else None
        
        # Message store
        self.message_store: Optional[MessageStore] = None
        if MESSAGE_STORE_AVAILABLE:
            try:
                self.message_store = get_message_store()
            except Exception as e:
                print(f"[!] SummaryRobot: Failed to init message store: {e}")
        
        # Executor
        if getattr(self.config, 'shared_executor', None):
            self.executor = self.config.shared_executor
            self._owns_executor = False
        else:
            max_workers = getattr(self.config, 'max_workers', 4)
            self.executor = ThreadPoolExecutor(max_workers=max_workers)
            self._owns_executor = True
        
        # Commands
        self.commands = {
            "/help": ("显示帮助菜单", self._cmd_help),
            "/summary": ("总结聊天记录", self._cmd_summary),
            "/总结": ("总结聊天记录（中文）", self._cmd_summary),
            "/stats": ("显示聊天统计", self._cmd_stats),
        }
        
        print(f"[*] SummaryRobot 初始化: use_ai={self.use_ai}, message_store={self.message_store is not None}")

    def extract_text(self, message) -> str:
        """Extract text from message."""
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            return "".join(seg.get("data", {}).get("text", "")
                          for seg in message if seg.get("type") == "text")
        return str(message)

    # ---------- 命令实现 ----------
    async def _cmd_help(self, group_id: int, user_id: int, send_func, *send_args) -> None:
        """显示帮助菜单"""
        help_text = """【总结模式帮助】
你可以直接对我说：

【总结功能】
· "总结一下" - 总结最近1小时的聊天
· "总结今天的聊天" - 总结今天的记录
· "总结过去3小时的聊天" - 指定时间范围

【时间范围】
· 支持：5分钟、1小时、3小时、半天、1天、一周
· 最长支持总结最近3天的聊天记录

【统计功能】
· "统计聊天" - 显示聊天统计数据

我会理解你的自然语言指令，直接说出来就好~"""
        await send_func(*send_args, help_text)

    async def _cmd_summary(self, group_id: int, user_id: int, send_func, *send_args) -> None:
        """总结命令的实现（由handle_group/handle_private调用）"""
        # 此方法不会被直接调用，实际逻辑在handle_group/handle_private中
        pass

    async def _cmd_stats(self, group_id: int, user_id: int, send_func, *send_args) -> None:
        """显示聊天统计"""
        if not self.message_store:
            await send_func(*send_args, "❌ 消息存储功能未启用")
            return
        
        try:
            now = int(time.time())
            # 获取不同时间段的统计
            stats_1h = self.message_store.get_stats(now - 3600, now)
            stats_24h = self.message_store.get_stats(now - 86400, now)
            
            stats_text = (
                "📊 聊天统计\n"
                "=" * 30 + "\n\n"
                f"过去1小时:\n"
                f"  总消息: {stats_1h['total_messages']}\n"
            )
            if stats_1h['active_users']:
                stats_text += f"  活跃用户: {len(stats_1h['active_users'])}\n"
            
            stats_text += (
                f"\n过去24小时:\n"
                f"  总消息: {stats_24h['total_messages']}\n"
            )
            if stats_24h['active_users']:
                stats_text += "  最活跃用户:\n"
                for i, user in enumerate(stats_24h['active_users'][:3], 1):
                    stats_text += f"    {i}. {user['nickname']}: {user['count']}条\n"
            
            # 数据库大小
            db_size = self.message_store.get_db_size()
            stats_text += f"\n数据库大小: {db_size / 1024:.1f} KB"
            
            await send_func(*send_args, stats_text)
        except Exception as e:
            await send_func(*send_args, f"❌ 获取统计失败: {e}")

    async def handle_command(self, text: str, group_id: int, user_id: int,
                            send_func, *send_args) -> bool:
        """处理命令，返回是否已处理"""
        # 提取命令（处理@机器人的情况）
        clean_text = re.sub(r"\[CQ:at,qq=\d+\]", "", text).strip()
        tokens = clean_text.split()
        if not tokens:
            return False
        
        cmd = tokens[0]
        if cmd in self.commands:
            _, handler = self.commands[cmd]
            # /summary 和 /总结 由 handle_group/handle_private 处理
            if cmd in ("/summary", "/总结"):
                return False  # 让主处理流程处理
            await handler(group_id, user_id, send_func, *send_args)
            return True
        return False

    async def handle_group(self, data: dict, send_group_reply, sender_info: dict = None):
        """Handle group message events; triggers on commands like /summary or /总结."""
        group_id = data.get('group_id')
        user_id = data.get('user_id')
        message_id = data.get('message_id')
        text = self.extract_text(data.get('message', []))
        clean = re.sub(r"\[CQ:at,qq=\d+\]", "", text).strip()
        if not clean:
            return
        
        # 先尝试处理命令
        if await self.handle_command(text, group_id, user_id, send_group_reply, group_id, user_id, message_id):
            return
        
        tokens = clean.split()
        if not tokens:
            return
        cmd = tokens[0]
        if not (cmd.startswith("/") or "总结" in cmd):
            return
        
        # Parse optional args
        window_token = tokens[1] if len(tokens) > 1 else None
        seconds, window_text = parse_time_window(window_token)
        
        max_tokens = 4000
        if len(tokens) > 2:
            try:
                max_tokens = max(500, min(8000, int(tokens[2])))
            except ValueError:
                pass
        
        # Send a processing hint
        await send_group_reply(group_id, user_id, message_id, f"正在总结过去{window_text}的聊天记录，请稍等...")
        
        summary = await self._generate_and_summarize(
            group_id=group_id, user_id=None, seconds=seconds,
            max_tokens=max_tokens, window_text=window_text
        )
        await send_group_reply(group_id, user_id, message_id, summary)

    async def handle_private(self, data: dict, send_private_msg, sender_info: dict = None):
        """Handle private message events; triggers on /summary and /总结."""
        user_id = data.get('user_id')
        text = self.extract_text(data.get('message', []))
        if not text or not text.strip():
            return
        
        # 先尝试处理命令
        if await self.handle_command(text, 0, user_id, send_private_msg, user_id):
            return
        
        tokens = text.strip().split()
        if not tokens:
            return
        cmd = tokens[0]
        if not (cmd.startswith("/") or "总结" in cmd):
            return
        
        window_token = tokens[1] if len(tokens) > 1 else None
        seconds, window_text = parse_time_window(window_token)
        
        max_tokens = 4000
        if len(tokens) > 2:
            try:
                max_tokens = max(500, min(8000, int(tokens[2])))
            except ValueError:
                pass
        
        await send_private_msg(user_id, f"正在总结过去{window_text}的聊天记录，请稍等...")
        
        summary = await self._generate_and_summarize(
            group_id=0, user_id=user_id, seconds=seconds,
            max_tokens=max_tokens, window_text=window_text
        )
        await send_private_msg(user_id, summary)

    async def _generate_and_summarize(self, group_id: int, user_id: Optional[int],
                                     seconds: int, max_tokens: int, window_text: str) -> str:
        """Load history, sample messages, call DeepSeek to summarize."""
        
        if not self.message_store:
            return "❌ 消息存储功能未启用，无法获取聊天记录。"
        
        now = int(time.time())
        start_time = now - seconds
        
        # Load messages
        try:
            if group_id == 0 and user_id is not None:
                # 私聊总结
                messages = self.message_store.get_private_messages(user_id, start_time, now, limit=5000)
            else:
                # 群聊总结
                messages = self.message_store.get_group_messages(group_id, start_time, now, limit=5000)
        except Exception as e:
            return f"❌ 读取聊天记录失败: {e}"
        
        # Convert to dict format
        msg_dicts = [m.to_dict() for m in messages]
        
        total_raw = len(msg_dicts)
        if total_raw == 0:
            return f"📭 在过去{window_text}内未找到相关对话记录。"
        
        # 使用采样器控制token
        sampler = MessageSampler(max_tokens=max_tokens)
        sampled, stats = sampler.sample(msg_dicts)
        
        if not sampled:
            return f"📭 处理后未找到有效的对话内容（原始消息{total_raw}条）。"
        
        # Build prompt
        lines = []
        for m in sampled:
            ts = time.strftime("%H:%M", time.localtime(m['timestamp']))
            nick = m.get('nickname') or '未知'
            content = m.get('content', '')
            lines.append(f"[{ts}] {nick}: {content}")
        
        sample_text = "\n".join(lines)
        
        # 构建prompt，包含统计信息
        instruction = (
            f"请以音理的身份（温柔、活泼、略带天真的口吻）总结以下{window_text}的聊天记录。\n\n"
            f"统计信息：\n"
            f"• 原始消息：{stats['original']} 条\n"
            f"• 去重后：{stats['after_dedup']} 条\n"
            f"• 采样分析：{stats['final']} 条\n\n"
            f"重要要求：\n"
            f"1. 纯文本输出，不要使用任何Markdown格式（如**粗体**、*斜体*、-列表、#标题等）\n"
            f"2. 用简洁中文给出 3-6 条关键要点\n"
            f"3. 使用QQ表情符号如✨、💡、📌、💬等增加可读性\n"
            f"4. 用简单符号（如•、→、★）代替Markdown列表\n"
            f"5. 分段清晰，每段之间空一行\n"
            f"6. 列出讨论的主要话题和结论\n"
            f"7. 如有待办事项或争议点，请单独列出\n"
            f"8. 给出 1-2 条温馨的后续建议\n"
            f"9. 不要复述原话，用自己的话总结\n\n"
            f"聊天记录（按时间排序）：\n"
            f"{sample_text}\n\n"
            f"请开始总结（记住：纯文本，无Markdown）："
        )
        
        if not self.use_ai or not self.api:
            # Fallback: QQ友好格式的统计
            fallback = f"""⚠️ 本机摘要 - 未配置DeepSeek

📊 统计：
• 原始消息：{stats['original']} 条
• 有效消息：{stats['after_dedup']} 条
• 采样分析：{stats['final']} 条

💬 最新5条：
{chr(10).join(lines[-5:])}

💡 请配置DeepSeek API以获得智能摘要"""
            return fallback
        
        # Call DeepSeek API
        loop = asyncio.get_running_loop()
        try:
            # 使用 lambda 确保参数正确传递
            resp = await loop.run_in_executor(
                self.executor, 
                lambda: self.api.chat(user_msg=instruction, system_msg=self.config.system_prompt if self.config else "")
            )
            
            if not resp:
                return "❌ DeepSeek 未返回结果。"
            
            # Add header - QQ友好格式
            header = f"✨ 聊天记录总结 ({window_text})\n"
            header += f"💬 分析了 {stats['original']} 条消息\n"
            header += "═" * 20 + "\n\n"
            
            # 清理可能的Markdown格式
            clean_resp = self._clean_for_qq(resp)
            
            return header + clean_resp
            
        except Exception as e:
            return f"❌ 调用 DeepSeek 失败: {e}"


    def _clean_for_qq(self, text: str) -> str:
        """清理Markdown格式，使其适合QQ显示"""
        import re
        
        # 移除 Markdown 粗体 **text**
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        # 移除 Markdown 斜体 *text*
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        # 移除 Markdown 标题 #
        text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
        # 将 Markdown 列表 - 替换为 •
        text = re.sub(r'^\s*[-\*]\s+', '• ', text, flags=re.MULTILINE)
        # 移除 Markdown 代码块标记
        text = re.sub(r'```\w*\n?', '', text)
        text = re.sub(r'`(.+?)`', r'\1', text)
        # 移除链接标记 [text](url) -> text
        text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
        # 移除多余的空行（保留一个）
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        return text.strip()


def create_robot(config: Optional[object] = None):
    return SummaryRobot(config)
