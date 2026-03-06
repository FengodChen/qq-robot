"""
robots/daily_summary.py

每日定时总结机器人：每天在指定时间自动总结指定群的聊天记录

=== METADATA ===
name: daily_summary
desc: 每日定时总结模式，自动在指定时间总结群消息
cmds: 
=== END ===
"""

import asyncio
import time
from datetime import datetime, timedelta
from typing import Optional

# 从 summary.py 导入总结功能
try:
    from robots.summary import async_summarize_window, SummaryRobot
    SUMMARY_AVAILABLE = True
except Exception as e:
    print(f"[!] DailySummaryRobot: 无法导入 summary 模块: {e}")
    SUMMARY_AVAILABLE = False


class DailySummaryRobot:
    """每日定时总结机器人"""
    
    def __init__(self, config: Optional[object] = None, mode_manager=None):
        self.config = config
        self.mode_manager = mode_manager  # 用于发送消息
        self.enabled = getattr(config, 'daily_summary_enabled', False)
        self.group_id = getattr(config, 'daily_summary_group_id', 0)
        self.max_tokens = getattr(config, 'daily_summary_max_tokens', 0)
        self.hour = getattr(config, 'daily_summary_hour', 0)
        self.minute = getattr(config, 'daily_summary_minute', 0)
        self._task = None
        self._running = False
        
        print(f"[*] DailySummaryRobot 初始化: enabled={self.enabled}, group_id={self.group_id}, "
              f"max_tokens={self.max_tokens}, time={self.hour:02d}:{self.minute:02d}")
    
    def start(self):
        """启动定时任务"""
        if not self.enabled:
            print("[*] DailySummaryRobot 已禁用，不启动定时任务")
            return
        
        if not SUMMARY_AVAILABLE:
            print("[!] DailySummaryRobot: summary 模块不可用，无法启动")
            return
        
        if self._running:
            print("[*] DailySummaryRobot 已经在运行")
            return
        
        self._running = True
        self._task = asyncio.create_task(self._schedule_loop())
        print(f"[*] DailySummaryRobot 已启动，将在每天 {self.hour:02d}:{self.minute:02d} 执行总结")
    
    def stop(self):
        """停止定时任务"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        print("[*] DailySummaryRobot 已停止")
    
    async def _schedule_loop(self):
        """定时循环，等待到指定时间执行总结"""
        while self._running:
            try:
                now = datetime.now()
                target = now.replace(hour=self.hour, minute=self.minute, second=0, microsecond=0)
                
                # 如果目标时间已过，设置为明天
                if target <= now:
                    target = target + timedelta(days=1)
                
                wait_seconds = (target - now).total_seconds()
                print(f"[*] DailySummaryRobot: 下次总结时间 {target.strftime('%Y-%m-%d %H:%M:%S')}，"
                      f"等待 {wait_seconds / 3600:.1f} 小时")
                
                await asyncio.sleep(wait_seconds)
                
                if self._running:
                    await self._do_summary()
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[!] DailySummaryRobot 调度循环出错: {e}")
                await asyncio.sleep(60)  # 出错后等待1分钟再试
    
    async def _do_summary(self):
        """执行总结并发送消息"""
        try:
            print(f"[*] DailySummaryRobot: 开始执行每日总结 (群: {self.group_id})")
            
            # 使用 summary.py 的 async_summarize_window 函数
            summary = await async_summarize_window(
                group_id=self.group_id,
                user_id=None,
                window="1d",  # 总结一天
                max_tokens=self.max_tokens,
                config=self.config
            )
            
            # 发送总结到群里
            if self.mode_manager:
                # 添加定时总结的标识头
                header = f"📅 每日群聊总结 ({datetime.now().strftime('%Y-%m-%d')})\n"
                header += "═" * 20 + "\n\n"
                full_message = header + summary
                
                await self.mode_manager.send_group_msg(self.group_id, full_message)
                print(f"[*] DailySummaryRobot: 每日总结已发送到群 {self.group_id}")
            else:
                print(f"[!] DailySummaryRobot: mode_manager 未设置，无法发送消息")
                
        except Exception as e:
            print(f"[!] DailySummaryRobot 执行总结失败: {e}")
            # 尝试发送错误消息
            if self.mode_manager:
                try:
                    error_msg = f"❌ 每日总结生成失败: {e}\n请检查日志了解详情。"
                    await self.mode_manager.send_group_msg(self.group_id, error_msg)
                except:
                    pass

    # 兼容机器人接口（虽然这个机器人不处理消息）
    async def handle_group(self, data: dict, send_group_reply, sender_info: dict = None):
        """处理群消息 - 此机器人不处理普通消息"""
        pass
    
    async def handle_private(self, data: dict, send_private_msg, sender_info: dict = None):
        """处理私聊消息 - 此机器人不处理普通消息"""
        pass


def create_robot(config: Optional[object] = None):
    """工厂函数，返回 DailySummaryRobot 实例
    
    注意：此机器人需要 mode_manager 才能发送消息，
    请在创建后调用 robot.mode_manager = mgr 然后 robot.start()
    """
    return DailySummaryRobot(config)
