"""每日总结服务。

提供每日定时总结群聊记录的功能。
"""

from qq_bot.services.daily_summary.scheduler import DailySummaryScheduler, DailySummaryConfig

__all__ = ["DailySummaryScheduler", "DailySummaryConfig"]
