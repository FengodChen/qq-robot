"""每日总结调度器。

实现每日定时总结群聊记录的功能。
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from qq_bot.services.summary_service import SummaryService


@dataclass
class DailySummaryConfig:
    """每日总结配置。"""
    
    enabled: bool = False
    group_id: int = 0
    hour: int = 23
    minute: int = 0


class DailySummaryScheduler:
    """每日总结调度器。
    
    每天在指定时间自动总结指定群的聊天记录。
    
    Attributes:
        config: 每日总结配置。
        adapter: OneBot 适配器实例，用于发送消息。
        summary_service: 总结服务实例，用于生成总结。
    
    Example:
        >>> config = DailySummaryConfig(enabled=True, group_id=123456, hour=23, minute=0)
        >>> scheduler = DailySummaryScheduler(config, adapter, summary_service)
        >>> scheduler.start()
    """
    
    def __init__(
        self,
        config: DailySummaryConfig,
        adapter: Any,
        summary_service: SummaryService
    ):
        """初始化调度器。
        
        Args:
            config: 每日总结配置。
            adapter: OneBot 适配器实例。
            summary_service: 总结服务实例。
        """
        self.config = config
        self.adapter = adapter
        self.summary_service = summary_service
        
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
        import time
        
        try:
            print(f"[*] DailySummaryScheduler: 开始执行每日总结 (群: {self.config.group_id})")
            
            # 使用 SummaryService 生成总结
            since = time.time() - 86400  # 24小时前
            summary = await self.summary_service.generate_summary(
                group_id=self.config.group_id,
                since=since,
                window_display="过去24小时",
                custom_persona=None,  # 使用 SummaryService 的默认人设（config.yaml 的 system_prompt）
                max_messages=200
            )
            
            # 发送总结到群里（调整格式为每日总结风格）
            if summary:
                # 将 SummaryService 的输出格式转换为每日总结格式
                # 原格式：✨ 过去24小时群聊小结 ✨\n───\ncontent\n───
                # 新格式：📅 每日群聊总结 (日期)\n════════════════════\n\ncontent
                
                lines = summary.split('\n')
                # 提取内容部分（去掉第一行的标题和分隔符）
                if len(lines) >= 4:
                    content = '\n'.join(lines[2:-1])  # 去掉标题行、第一个分隔符、最后一个分隔符
                else:
                    content = summary
                
                header = f"📅 每日群聊总结 ({datetime.now().strftime('%Y-%m-%d')})\n"
                header += "═" * 20 + "\n\n"
                full_message = header + content
                
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
