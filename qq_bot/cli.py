"""命令行入口。

提供命令行接口启动机器人。
"""

import argparse
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from qq_bot import create_app
from qq_bot.core.config import BotConfig


def create_parser() -> argparse.ArgumentParser:
    """创建参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="qq-bot",
        description="QQ Bot - 基于 OneBot 11 协议的 QQ 机器人"
    )
    
    parser.add_argument(
        "-c", "--config",
        default="config.yaml",
        help="配置文件路径 (默认: config.yaml)"
    )
    
    parser.add_argument(
        "--debug",
        action="store_true",
        help="启用调试模式"
    )
    
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 2.0.0"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # init 命令
    init_parser = subparsers.add_parser("init", help="初始化配置文件")
    init_parser.add_argument(
        "-o", "--output",
        default="config.yaml",
        help="输出文件路径"
    )
    
    return parser


def cmd_init(args: argparse.Namespace) -> int:
    """初始化配置文件。"""
    output_path = Path(args.output)
    
    if output_path.exists():
        print(f"[!] 配置文件已存在: {output_path}")
        return 1
    
    config_content = '''# QQ Bot 配置文件
# 请填写以下配置项

# DeepSeek API 配置
deepseek_api_key: "your-deepseek-api-key"

# QQ Bot 配置
qq_bot_token: "your-qq-bot-token"

# NapCat WebSocket 配置
napcat_ws_url: "ws://127.0.0.1:3000/"
listen_host: "0.0.0.0"
listen_port: 3001

# 日志级别
# debug_mode: false

# 插件配置
# plugins:
#   - chat
#   - summary

# 聊天配置
# max_context: 20
# system_prompt: ""
'''
    
    output_path.write_text(config_content, encoding="utf-8")
    print(f"[*] 配置文件已创建: {output_path}")
    print("[*] 请编辑配置文件，填入必要的 API Key 和 Token")
    
    return 0


async def cmd_run(args: argparse.Namespace) -> int:
    """运行机器人。"""
    print("=" * 60)
    print("QQ Bot v2.0")
    print("=" * 60)
    
    # 加载配置
    try:
        config = BotConfig.from_yaml(args.config)
    except Exception as e:
        print(f"[!] 加载配置失败: {e}")
        return 1
    
    # 覆盖调试模式
    if args.debug:
        config.debug.enabled = True
    
    # 检查必要配置
    if not config.llm.api_key:
        print("[!] 警告: DeepSeek API Key 未设置")
    if not config.onebot.token:
        print("[!] 警告: QQ Bot Token 未设置")
    
    # 创建并运行应用
    app = create_app(config)
    
    try:
        await app.run()
    except KeyboardInterrupt:
        print("\n[*] 正在停止...")
        await app.shutdown()
        print("[*] 已停止")
    
    return 0


async def main_async() -> int:
    """异步主函数。"""
    parser = create_parser()
    args = parser.parse_args()
    
    if args.command == "init":
        return cmd_init(args)
    else:
        return await cmd_run(args)


def main() -> int:
    """主函数。"""
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
