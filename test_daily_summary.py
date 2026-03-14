#!/usr/bin/env python3
"""
qq_bot 每日总结功能测试脚本
"""

import os
import sys
import asyncio
from datetime import datetime, timedelta

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qq_bot.core.config import BotConfig
from qq_bot.services.daily_summary import DailySummaryScheduler, DailySummaryConfig
from qq_bot.services.storage.message import MessageStore
from qq_bot.services.llm.deepseek import DeepSeekService


class MockAdapter:
    """模拟适配器用于测试"""
    
    def __init__(self):
        self.messages_sent = []
    
    async def send_group_message(self, group_id: int, content: str, **kwargs) -> bool:
        """模拟发送群消息"""
        self.messages_sent.append({
            'group_id': group_id,
            'content': content[:200] + '...' if len(content) > 200 else content
        })
        print(f"  [模拟发送] 群 {group_id}:")
        print(f"  {content[:150]}...")
        return True


async def test_daily_summary_scheduler():
    """测试每日总结调度器"""
    print("=" * 60)
    print("测试 1: DailySummaryScheduler 初始化")
    print("=" * 60)
    
    # 创建配置
    config = DailySummaryConfig(
        enabled=True,
        group_id=123456,
        max_tokens=4000,
        hour=23,
        minute=0
    )
    
    print(f"[*] 配置信息:")
    print(f"    enabled: {config.enabled}")
    print(f"    group_id: {config.group_id}")
    print(f"    max_tokens: {config.max_tokens}")
    print(f"    time: {config.hour:02d}:{config.minute:02d}")
    
    # 创建模拟适配器
    adapter = MockAdapter()
    
    # 创建消息存储
    data_dir = "data"
    os.makedirs(data_dir, exist_ok=True)
    message_store = MessageStore(
        db_path=f"{data_dir}/test_messages.db",
        retention_days=7
    )
    
    # 添加一些测试消息
    print("\n[*] 添加测试消息到数据库...")
    now = datetime.now()
    for i in range(10):
        message_store.add_message(
            msg_type="group",
            user_id=10000 + i,
            group_id=123456,
            nickname=f"用户{i+1}",
            content=f"这是测试消息 {i+1}，讨论一下今天的天气真不错",
            raw_message=f"这是测试消息 {i+1}",
            timestamp=(now - timedelta(hours=i)).timestamp()
        )
    print(f"[*] 已添加 10 条测试消息")
    
    # 尝试创建 LLM 服务
    llm_service = None
    try:
        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key:
            # 尝试从配置文件读取
            bot_config = BotConfig.from_yaml("config.yaml")
            api_key = bot_config.llm.api_key
        
        if api_key:
            llm_service = DeepSeekService(api_key=api_key)
            print(f"[*] LLM 服务已初始化")
        else:
            print("[!] 未找到 API Key，跳过 LLM 测试")
    except Exception as e:
        print(f"[!] LLM 服务初始化失败: {e}")
    
    # 创建调度器
    scheduler = DailySummaryScheduler(
        config=config,
        adapter=adapter,
        llm_service=llm_service,
        message_store=message_store
    )
    
    print("\n[*] 调度器初始化成功")
    
    # 测试等待时间计算
    print("\n" + "=" * 60)
    print("测试 2: 等待时间计算")
    print("=" * 60)
    
    wait_time = scheduler._calculate_wait_time()
    next_run = datetime.now() + timedelta(seconds=wait_time)
    print(f"[*] 当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[*] 下次执行: {next_run.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"[*] 等待秒数: {wait_time:.1f}s ({wait_time/3600:.2f}小时)")
    
    # 测试总结生成
    print("\n" + "=" * 60)
    print("测试 3: 总结生成")
    print("=" * 60)
    
    if llm_service:
        print("[*] 调用 LLM 生成总结...")
        try:
            summary = await scheduler._generate_summary()
            print(f"[*] 生成成功！长度: {len(summary)} 字符")
            print(f"\n[*] 总结内容预览:")
            print("-" * 40)
            print(summary[:300] + "..." if len(summary) > 300 else summary)
            print("-" * 40)
        except Exception as e:
            print(f"[!] 总结生成失败: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("[!] 跳过测试（LLM 服务不可用）")
        # 测试无 LLM 时的消息获取
        print("[*] 测试消息获取功能...")
        import time
        messages = message_store.get_messages_since(
            since=time.time() - 86400,
            group_id=123456,
            limit=5000
        )
        print(f"[*] 获取到 {len(messages)} 条消息")
        if messages:
            print(f"[*] 最新消息: {messages[-1].nickname}: {messages[-1].content[:50]}")
    
    # 测试调度器启停（不实际运行）
    print("\n" + "=" * 60)
    print("测试 4: 调度器启停")
    print("=" * 60)
    
    # 不真正启动，因为会阻塞
    print("[*] 调度器状态检查:")
    print(f"    config.enabled: {scheduler.config.enabled}")
    print(f"    _running: {scheduler._running}")
    print(f"    _task: {scheduler._task}")
    
    # 清理
    print("\n" + "=" * 60)
    print("测试 5: 清理")
    print("=" * 60)
    
    # 关闭并删除测试数据库
    db_path = f"{data_dir}/test_messages.db"
    if os.path.exists(db_path):
        os.remove(db_path)
        print(f"[*] 已删除测试数据库: {db_path}")
    
    print("\n[*] 所有测试完成!")


async def test_config_loading():
    """测试配置加载"""
    print("\n" + "=" * 60)
    print("测试 6: 配置加载")
    print("=" * 60)
    
    try:
        config = BotConfig.from_yaml("config.yaml")
        print(f"[*] 配置加载成功")
        print(f"    daily_summary.enabled: {config.daily_summary.enabled}")
        print(f"    daily_summary.group_id: {config.daily_summary.group_id}")
        print(f"    daily_summary.hour: {config.daily_summary.hour}")
        print(f"    daily_summary.minute: {config.daily_summary.minute}")
        print(f"    daily_summary.max_tokens: {config.daily_summary.max_tokens}")
    except Exception as e:
        print(f"[!] 配置加载失败: {e}")


async def main():
    """主函数"""
    print("\n" + "=" * 60)
    print("qq_bot 每日总结功能测试")
    print("=" * 60)
    
    try:
        await test_daily_summary_scheduler()
        await test_config_loading()
        
        print("\n" + "=" * 60)
        print("✓ 测试完成!")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n[!] 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
