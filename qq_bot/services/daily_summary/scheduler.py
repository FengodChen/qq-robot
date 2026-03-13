"""每日总结调度器。

实现每日定时总结群聊记录的功能。
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from qq_bot.services.llm.base import LLMService, ChatMessage
from qq_bot.services.storage.message import MessageStore


@dataclass
class DailySummaryConfig:
    """每日总结配置。"""
    
    enabled: bool = False
    group_id: int = 0
    max_tokens: int = 4000
    hour: int = 23
    minute: int = 0


class DailySummaryScheduler:
    """每日总结调度器。
    
    每天在指定时间自动总结指定群的聊天记录。
    
    Attributes:
        config: 每日总结配置。
        adapter: OneBot 适配器实例，用于发送消息。
        llm_service: LLM 服务实例，用于生成总结。
        message_store: 消息存储实例，用于获取聊天记录。
    
    Example:
        >>> config = DailySummaryConfig(enabled=True, group_id=123456, hour=23, minute=0)
        >>> scheduler = DailySummaryScheduler(config, adapter, llm, store)
        >>> scheduler.start()
    """
    
    def __init__(
        self,
        config: DailySummaryConfig,
        adapter: Any,
        llm_service: LLMService,
        message_store: MessageStore
    ):
        """初始化调度器。
        
        Args:
            config: 每日总结配置。
            adapter: OneBot 适配器实例。
            llm_service: LLM 服务实例。
            message_store: 消息存储实例。
        """
        self.config = config
        self.adapter = adapter
        self.llm = llm_service
        self.store = message_store
        
        self._task: Optional[asyncio.Task] = None
        self._running = False
        
        print(f"[*] DailySummaryScheduler 初始化: enabled={config.enabled}, "
              f"group_id={config.group_id}, time={config.hour:02d}:{config.minute:02d}")
    
    def start(self) -> None:
        """启动定时任务。"""
        if not self.config.enabled:
            print("[*] DailySummaryScheduler 已禁用，不启动定时任务")
            return
        
        if self._running:
            print("[*] DailySummaryScheduler 已经在运行")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._schedule_loop())
        print(f"[*] DailySummaryScheduler 已启动，将在每天 {self.config.hour:02d}:{self.config.minute:02d} 执行总结")
    
    def stop(self) -> None:
        """停止定时任务。"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        print("[*] DailySummaryScheduler 已停止")
    
    def _calculate_wait_time(self) -> float:
        """计算到下次执行需要等待的秒数。
        
        Returns:
            等待秒数。
        """
        now = datetime.now()
        target = now.replace(hour=self.config.hour, minute=self.config.minute, second=0, microsecond=0)
        
        # 如果目标时间已过，设置为明天
        if target <= now:
            target = target + timedelta(days=1)
        
        return (target - now).total_seconds()
    
    async def _schedule_loop(self) -> None:
        """定时循环，等待到指定时间执行总结。"""
        while self._running:
            try:
                wait_seconds = self._calculate_wait_time()
                next_run = datetime.now() + timedelta(seconds=wait_seconds)
                print(f"[*] DailySummaryScheduler: 下次总结时间 {next_run.strftime('%Y-%m-%d %H:%M:%S')}，"
                      f"等待 {wait_seconds / 3600:.1f} 小时")
                
                await asyncio.sleep(wait_seconds)
                
                if self._running:
                    await self._do_summary()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[!] DailySummaryScheduler 调度循环出错: {e}")
                await asyncio.sleep(60)  # 出错后等待1分钟再试
    
    async def _do_summary(self) -> None:
        """执行总结并发送消息。"""
        try:
            print(f"[*] DailySummaryScheduler: 开始执行每日总结 (群: {self.config.group_id})")
            
            summary = await self._generate_summary()
            
            # 发送总结到群里
            if summary:
                header = f"📅 每日群聊总结 ({datetime.now().strftime('%Y-%m-%d')})\n"
                header += "═" * 20 + "\n\n"
                full_message = header + summary
                
                await self.adapter.send_group_message(
                    group_id=self.config.group_id,
                    content=full_message
                )
                print(f"[*] DailySummaryScheduler: 每日总结已发送到群 {self.config.group_id}")
            
        except Exception as e:
            print(f"[!] DailySummaryScheduler 执行总结失败: {e}")
            # 尝试发送错误消息
            try:
                error_msg = f"❌ 每日总结生成失败: {e}\n请检查日志了解详情。"
                await self.adapter.send_group_message(
                    group_id=self.config.group_id,
                    content=error_msg
                )
            except:
                pass
    
    async def _generate_summary(self) -> str:
        """生成聊天记录总结。
        
        Returns:
            总结文本。
        """
        import time
        
        # 获取过去24小时的消息
        now = int(time.time())
        start_time = now - 86400  # 24小时
        
        messages = self.store.get_messages_since(
            since=start_time,
            group_id=self.config.group_id,
            limit=5000
        )
        
        if not messages:
            return "📭 过去24小时内没有聊天记录。"
        
        # 转换为简单格式
        msg_list = []
        for m in messages:
            ts = time.strftime("%H:%M", time.localtime(m.timestamp))
            msg_list.append(f"[{ts}] {m.nickname}: {m.content}")
        
        # 限制消息数量
        MAX_MESSAGES = 200
        if len(msg_list) > MAX_MESSAGES:
            # 均匀采样
            step = len(msg_list) // MAX_MESSAGES
            msg_list = msg_list[::step][:MAX_MESSAGES]
        
        chat_text = "\n".join(msg_list)
        
        # 构建总结提示词
        prompt = f"""请以温柔、活泼的口吻总结以下24小时的群聊记录。

聊天记录：
{chat_text}

要求：
1. 纯文本输出，不要使用Markdown格式
2. 总结3-6条关键要点
3. 列出讨论的主要话题和结论
4. 如有待办事项或争议点，请单独列出
5. 给出1-2条温馨的后续建议
6. 使用QQ表情符号增加可读性

请开始总结："""
        
        # 调用 LLM
        response = await self.llm.chat(
            messages=[ChatMessage(role="user", content=prompt)],
            max_tokens=self.config.max_tokens,
            temperature=0.7
        )
        
        return response.content
