#!/usr/bin/env python3
"""
总结功能测试脚本
"""

import os
import sys
import asyncio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config_loader import load_config
from robots.summary import SummaryRobot, parse_time_window
from robots.chat import BotConfig


async def test_summary():
    """测试总结功能"""
    print("=" * 60)
    print("总结功能测试")
    print("=" * 60)
    
    # 加载配置
    config_data = load_config("config.yaml")
    
    if not config_data.get('deepseek_api_key'):
        print("[!] 错误: DeepSeek API Key 未设置")
        print("    请在 config.yaml 中配置 deepseek_api_key")
        return
    
    print(f"[*] DeepSeek API Key: {config_data['deepseek_api_key'][:8]}...{config_data['deepseek_api_key'][-4:]}")
    
    # 创建配置
    config = BotConfig(
        deepseek_api_key=config_data.get('deepseek_api_key', ''),
    )
    
    # 创建 SummaryRobot
    print("[*] 创建 SummaryRobot...")
    robot = SummaryRobot(config)
    
    print(f"[*] use_ai: {robot.use_ai}")
    print(f"[*] api: {robot.api}")
    print(f"[*] message_store: {robot.message_store}")
    
    if not robot.use_ai:
        print("[!] 错误: AI 未启用，请检查 API Key 配置")
        return
    
    # 测试 parse_time_window
    print("\n[*] 测试时间窗口解析:")
    test_cases = ["1h", "5m", "1d", "半天", "3小时", None]
    for tc in test_cases:
        seconds, text = parse_time_window(tc)
        print(f"  '{tc}' -> {seconds}s ({text})")
    
    # 测试总结功能（需要 message_store 中有数据）
    print("\n[*] 测试总结执行:")
    print("    注意: 此测试需要 message_store 中有数据才能正常总结")
    
    try:
        # 尝试总结（可能因为没有数据而返回空结果）
        result = await robot._generate_and_summarize(
            group_id=123456,  # 测试群号
            user_id=None,
            seconds=3600,
            max_tokens=4000,
            window_text="1小时"
        )
        print(f"\n[*] 总结结果:")
        print(result[:500] if len(result) > 500 else result)
    except Exception as e:
        print(f"[!] 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_summary())
