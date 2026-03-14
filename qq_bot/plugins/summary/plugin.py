"""总结插件。

支持按时间窗口总结聊天记录。
"""

import time
from dataclasses import dataclass
from typing import Any

from qq_bot.core.plugin import Plugin, PluginInfo
from qq_bot.core.events import MessageEvent, ResponseEvent
from qq_bot.core.context import Context
from qq_bot.services.summary_service import SummaryService
from qq_bot.utils.time import parse_natural_time, format_duration


@dataclass
class SummaryConfig:
    """总结配置。"""
    max_tokens: int = 4000
    default_window_seconds: int = 3600  # 默认1小时
    max_window_days: int = 3


class SummaryPlugin(Plugin):
    """总结插件。
    
    支持按时间窗口总结聊天记录。
    
    Commands:
        /summary [时间窗口] - 总结指定时间范围的聊天记录
        /stats [时间窗口] - 显示聊天统计
    """
    
    @property
    def info(self) -> PluginInfo:
        return PluginInfo(
            name="summary",
            description="聊天记录总结模式，支持时间窗口选择",
            version="1.0"
        )
    
    def __init__(self, ctx: Context):
        super().__init__(ctx)
        self.config = SummaryConfig(
            max_tokens=ctx.config.summary.max_tokens,
            max_window_days=ctx.config.summary.max_window_days
        )
        self.summary_service: SummaryService | None = ctx.services.summary
    
    async def on_message(self, ctx: Context, event: MessageEvent) -> ResponseEvent | None:
        """处理总结相关消息。
        
        注意：当消息被路由到 SummaryPlugin 时，意图分类器已经确认这是总结请求，
        因此不需要再次进行关键词检查，直接调用自然语言处理方法即可。
        """
        content = event.content.strip()
        
        # 检查是否是总结命令
        if content.startswith("/summary") or content.startswith("/总结"):
            return await self._handle_summary(ctx, event)
        
        if content.startswith("/stats") or content.startswith("/统计"):
            return await self._handle_stats(ctx, event)
        
        # 直接处理自然语言总结请求
        # 意图分类器已经确认这是总结请求，parse_natural_time 会解析时间参数
        return await self._handle_natural_summary(ctx, event)
    
    async def _handle_summary(
        self,
        ctx: Context,
        event: MessageEvent
    ) -> ResponseEvent:
        """处理总结命令。"""
        # 解析时间窗口
        parts = event.content.split(maxsplit=1)
        if len(parts) > 1:
            time_spec = parts[1]
            seconds, display = self._parse_time_window(time_spec)
        else:
            seconds = self.config.default_window_seconds
            display = format_duration(seconds)
        
        # 检查时间范围
        max_seconds = self.config.max_window_days * 86400
        if seconds > max_seconds:
            return ResponseEvent(
                content=f"❌ 时间范围太大了！最多只能总结最近{self.config.max_window_days}天的内容。",
                target_user_id=event.user_id,
                target_group_id=event.group_id
            )
        
        # 获取消息
        since = time.time() - seconds
        store = ctx.services.message_store
        
        if event.is_group:
            messages = store.get_messages_since(
                since=since,
                group_id=event.group_id
            )
        else:
            messages = store.get_messages_since(
                since=since,
                user_id=event.user_id
            )
        
        if not messages:
            return ResponseEvent(
                content=f"过去{display}没有聊天记录呢~",
                target_user_id=event.user_id,
                target_group_id=event.group_id
            )
        
        # 生成总结
        summary = await self._generate_summary(ctx, messages, display)
        
        return ResponseEvent(
            content=summary,
            target_user_id=event.user_id,
            target_group_id=event.group_id
        )
    
    async def _handle_natural_summary(
        self,
        ctx: Context,
        event: MessageEvent
    ) -> ResponseEvent:
        """处理自然语言总结请求。"""
        print(f"[*] SummaryPlugin: 开始处理自然语言总结请求: {event.content[:50]}...")
        
        # 解析时间
        seconds, display = parse_natural_time(event.content)
        if seconds is None:
            seconds = self.config.default_window_seconds
            display = format_duration(seconds)
        print(f"[*] SummaryPlugin: 解析时间窗口: {display} ({seconds}秒)")
        
        # 检查范围
        max_seconds = self.config.max_window_days * 86400
        if seconds > max_seconds:
            print(f"[*] SummaryPlugin: 时间范围过大")
            return ResponseEvent(
                content=f"❌ 你想总结{display}的聊天记录？太久了呢~最多只能总结最近{self.config.max_window_days}天的内容！",
                target_user_id=event.user_id,
                target_group_id=event.group_id
            )
        
        # 获取消息
        since = time.time() - seconds
        store = ctx.services.message_store
        print(f"[*] SummaryPlugin: 获取消息 since={since}, group_id={event.group_id}, user_id={event.user_id}")
        
        if event.is_group:
            messages = store.get_messages_since(
                since=since,
                group_id=event.group_id
            )
        else:
            messages = store.get_messages_since(
                since=since,
                user_id=event.user_id
            )
        
        print(f"[*] SummaryPlugin: 获取到 {len(messages)} 条消息")
        
        if not messages:
            print(f"[*] SummaryPlugin: 无消息记录，返回提示")
            return ResponseEvent(
                content=f"过去{display}没有聊天记录呢~",
                target_user_id=event.user_id,
                target_group_id=event.group_id
            )
        
        try:
            summary = await self._generate_summary(ctx, messages, display)
            print(f"[*] SummaryPlugin: 总结生成完成，长度={len(summary)}")
            return ResponseEvent(
                content=summary,
                target_user_id=event.user_id,
                target_group_id=event.group_id
            )
        except Exception as e:
            print(f"[!] SummaryPlugin: 生成总结失败: {e}")
            import traceback
            traceback.print_exc()
            return ResponseEvent(
                content=f"生成总结时出错: {e}",
                target_user_id=event.user_id,
                target_group_id=event.group_id
            )
    
    async def _generate_summary(
        self,
        ctx: Context,
        messages: list,
        window_display: str
    ) -> str:
        """使用 SummaryService 生成总结。"""
        if not self.summary_service:
            return "❌ 总结服务不可用，请检查配置。"
        
        # 获取时间戳（从消息中提取最早的）
        import time
        if messages:
            since = min(msg.timestamp for msg in messages)
        else:
            since = time.time() - 3600  # 默认1小时
        
        # 获取用户自定义人设（如果有）
        # 注意：这里使用 config.chat.system_prompt 作为用户自定义人设
        # 如果用户有临时更改人设的功能，可以在这里获取
        custom_persona = getattr(ctx.config.chat, 'system_prompt', None)
        
        # 使用 SummaryService 生成总结
        return await self.summary_service.generate_summary(
            group_id=messages[0].group_id if messages else 0,
            since=since,
            window_display=window_display,
            custom_persona=custom_persona,
            max_messages=100
        )
    
    async def _handle_stats(
        self,
        ctx: Context,
        event: MessageEvent
    ) -> ResponseEvent:
        """处理统计命令。"""
        # 解析时间窗口
        parts = event.content.split(maxsplit=1)
        if len(parts) > 1:
            seconds, _ = self._parse_time_window(parts[1])
        else:
            seconds = self.config.default_window_seconds
        
        since = time.time() - seconds
        store = ctx.services.message_store
        
        if event.is_group:
            stats = store.get_message_stats(
                start=since,
                end=time.time(),
                group_id=event.group_id
            )
        else:
            stats = store.get_message_stats(
                start=since,
                end=time.time()
            )
        
        total = stats.get("total_messages", 0)
        users = stats.get("active_users", 0)
        type_dist = stats.get("type_distribution", {})
        
        display = format_duration(seconds)
        
        content = f"""【{display}聊天统计】
━━━━━━━━━━━━━━
📊 总消息数: {total}
👥 活跃用户: {users}

📋 消息类型分布:
"""
        for msg_type, count in type_dist.items():
            content += f"  • {msg_type}: {count}\n"
        
        return ResponseEvent(
            content=content,
            target_user_id=event.user_id,
            target_group_id=event.group_id
        )
    
    def _parse_time_window(self, text: str) -> tuple[int, str]:
        """解析时间窗口。"""
        text = text.strip().lower()
        
        # 常用映射
        if text in ("5m", "5min", "5分钟"):
            return 300, "5分钟"
        if text in ("1h", "1小时"):
            return 3600, "1小时"
        if text in ("3h", "3小时"):
            return 10800, "3小时"
        if text in ("12h", "12小时", "半天"):
            return 43200, "半天"
        if text in ("1d", "24h", "1天", "一天"):
            return 86400, "1天"
        if text in ("3d", "3天"):
            return 259200, "3天"
        
        # 尝试解析数字+单位
        import re
        match = re.match(r"^(\d+(?:\.\d+)?)\s*([hmd])$", text)
        if match:
            value = float(match.group(1))
            unit = match.group(2)
            if unit == "h":
                return int(value * 3600), f"{value}小时"
            elif unit == "m":
                return int(value * 60), f"{value}分钟"
            elif unit == "d":
                return int(value * 86400), f"{value}天"
        
        return self.config.default_window_seconds, "1小时"
