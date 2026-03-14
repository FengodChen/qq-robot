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

# 默认配置文件内容
DEFAULT_CONFIG_CONTENT = '''# QQ Bot + DeepSeek AI 配置文件

version: "2.0"

# ===========================================
# LLM API 配置
# ===========================================
llm:
  provider: deepseek
  api_key: "your-deepseek-api-key"
  model: deepseek-chat
  base_url: "https://api.deepseek.com/v1"
  timeout: 60
  max_retries: 3

# ===========================================
# 火山引擎 Ark API 配置（新闻搜索）
# ===========================================
ark:
  api_key: "your-ark-api-key"
  model: "your-ark-model"
  base_url: "https://ark.cn-beijing.volces.com/api/v3"

# ===========================================
# OneBot 协议配置
# ===========================================
onebot:
  token: "your-qq-bot-token"
  napcat_ws_url: "ws://127.0.0.1:3000/"
  listen_host: "0.0.0.0"
  listen_port: 3001
  reconnect_interval: 5
  heartbeat_interval: 30

# ===========================================
# 存储配置
# ===========================================
storage:
  data_dir: "data"
  message_retention_days: 7
  conversation_max_context: 10
  conversation_max_storage: 100

# ===========================================
# 聊天插件配置
# ===========================================
chat:
  enabled: true
  system_prompt: |
    你是一个友好的AI助手，可以帮助用户解答问题。
  max_input_tokens: 500
  max_output_tokens: 100
  max_prompt_tokens: 500
  group_context_messages: 10
  dynamic_persona_enabled: true
  affection_enabled: true

# ===========================================
# 总结插件配置
# ===========================================
summary:
  enabled: true
  max_tokens: 4000
  default_window: "1h"
  max_window_days: 3

# ===========================================
# 每日定时总结配置
# ===========================================
daily_summary:
  enabled: true
  group_id: 123456789
  hour: 23
  minute: 0

# ===========================================
# 新闻服务配置
# ===========================================
news:
  enabled: true
  probability: 1.0
  cache_hours: 6.0

# ===========================================
# 插件列表
# ===========================================
plugins:
  - chat
  - summary

# ===========================================
# 调试配置
# ===========================================
debug:
  enabled: false
  log_level: INFO
  save_prompts: false
  save_requests: false

# ===========================================
# 工作线程数
# ===========================================
max_workers: 10
'''


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
    
    output_path.write_text(DEFAULT_CONFIG_CONTENT, encoding="utf-8")
    print(f"[*] 配置文件已创建: {output_path}")
    print("[*] 请编辑配置文件，填入必要的 API Key 和 Token")
    
    return 0


async def cmd_run(args: argparse.Namespace) -> int:
    """运行机器人。"""
    print("=" * 60)
    print("QQ Bot v2.0")
    print("=" * 60)
    
    config_path = Path(args.config)
    
    # 检查配置文件是否存在
    if not config_path.exists():
        print(f"[!] 配置文件不存在: {config_path}")
        print("[*] 正在自动创建默认配置文件...")
        
        config_path.write_text(DEFAULT_CONFIG_CONTENT, encoding="utf-8")
        print(f"[*] 配置文件已创建: {config_path}")
        print("=" * 60)
        print("[!] 请先编辑配置文件，填入必要的 API Key 和 Token:")
        print(f"    1. llm.api_key - DeepSeek API Key")
        print(f"    2. ark.api_key - ARK API Key")
        print(f"    3. onebot.token - QQ Bot Token")
        print(f"    4. onebot.napcat_ws_url - NapCat WebSocket 地址")
        print("=" * 60)
        print("[*] 配置完成后重新运行程序")
        return 0
    
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
