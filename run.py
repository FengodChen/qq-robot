#!/usr/bin/env python3
"""
QQ Bot 启动脚本
支持自然语言交互，自动模式选择
"""

import os
import sys
import asyncio
import argparse

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bot_api import ModeManager, BotConfig
from config_loader import load_config


def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="QQ Bot - 支持自然语言交互")
    parser.add_argument("--debug", action="store_true", help="启用调试模式，输出完整的 system prompt 和对话等详细数据")
    args = parser.parse_args()
    
    print("=" * 60)
    print("QQ Bot - 支持自然语言交互")
    if args.debug:
        print("[DEBUG MODE] 调试模式已启用")
    print("=" * 60)
    
    # 加载配置
    config_data = load_config("config.yaml")
    
    # 检查必要配置
    if not config_data.get('deepseek_api_key'):
        print("[!] 警告: DeepSeek API Key 未设置，请在 config.yaml 中配置")
    if not config_data.get('qq_bot_token'):
        print("[!] 警告: QQ Bot Token 未设置，请在 config.yaml 中配置")
    
    config = BotConfig(
        napcat_ws_url=config_data.get('napcat_ws_url'),
        listen_host=config_data.get('listen_host'),
        listen_port=int(config_data.get('listen_port')),
        token=config_data.get('qq_bot_token'),
        deepseek_api_key=config_data.get('deepseek_api_key'),
        system_prompt=config_data.get('system_prompt', ''),
        max_context=config_data.get('max_context'),
        max_input_tokens=config_data.get('max_input_tokens'),
        max_output_tokens=config_data.get('max_output_tokens'),
        max_prompt_tokens=config_data.get('max_prompt_tokens'),
        max_workers=config_data.get('max_workers'),
        daily_summary_enabled=config_data.get('daily_summary_enabled'),
        daily_summary_group_id=config_data.get('daily_summary_group_id'),
        daily_summary_max_tokens=config_data.get('daily_summary_max_tokens'),
        daily_summary_hour=config_data.get('daily_summary_hour'),
        daily_summary_minute=config_data.get('daily_summary_minute'),
        message_retention_days=config_data.get('message_retention_days'),
        debug_mode=args.debug,
    )
    
    # 创建并启动 ModeManager
    mgr = ModeManager(config)
    mgr.load_modes()
    
    # 设置默认模式为 chat
    if 'chat' in mgr.modes:
        mgr.current_mode = 'chat'
    elif mgr.modes:
        mgr.current_mode = next(iter(mgr.modes.keys()))
    
    print("\n使用说明：")
    print("- 群聊中 @机器人 发送消息")
    print("- 支持自然语言命令：")
    print("  · 更改人设/查看人设")
    print("  · 清除历史/查看历史")
    print("  · 总结一下")
    print("- 传统命令：/help")
    print("- 按 Ctrl+C 停止\n")
    
    try:
        asyncio.run(mgr.start())
    except KeyboardInterrupt:
        print("\n[*] 正在停止...")
        # 清理资源
        try:
            if mgr.current_robot and hasattr(mgr.current_robot, 'executor'):
                mgr.current_robot.executor.shutdown(wait=True)
            for v in mgr.user_modes.values():
                robot = v.get('robot') if isinstance(v, dict) else None
                if robot and hasattr(robot, 'executor'):
                    try:
                        if hasattr(robot, '_owns_executor') and robot._owns_executor:
                            robot.executor.shutdown(wait=True)
                    except Exception:
                        pass
            if hasattr(mgr, 'shared_executor') and mgr.shared_executor is not None:
                mgr.shared_executor.shutdown(wait=True)
        except Exception:
            pass
        print("[*] 已停止")


if __name__ == "__main__":
    main()
