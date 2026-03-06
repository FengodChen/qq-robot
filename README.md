# QQ Bot + DeepSeek AI

基于 OneBot 11 协议的 QQ 机器人，集成 DeepSeek AI 能力，支持自然语言交互。

═══════════════════════════════════════════════════════════════

【新特性：自动模式选择】

Agent 现在会自动识别你的意图并执行相应功能，无需手动切换模式：

  @机器人 总结一下今天的聊天    → 自动执行总结功能
  @机器人 更改人设，变成温柔的大姐姐  → 自动更改人设
  @机器人 清除历史记录           → 自动清除对话历史
  @机器人 查看历史              → 查看对话记录
  @机器人 你好呀               → 普通聊天对话

═══════════════════════════════════════════════════════════════

【文件说明】

run.py              - 启动脚本（推荐）
bot_api.py          - 机器人API，核心协调器
bot_agent.py        - 智能代理，自然语言处理
deepseek_api.py     - DeepSeek API 调用模块
qq_api.py           - QQ Bot API 模块
message_store.py    - 消息存储模块（SQLite）
config_loader.py    - 配置加载模块
config.yaml         - 配置文件（需自行创建，已加入.gitignore）
robots/             - 机器人模式模块目录
  - chat.py         - 聊天对话模式
  - summary.py      - 聊天记录总结模式
data/               - 数据存储目录

═══════════════════════════════════════════════════════════════

【模块架构】

用户消息
    ↓
bot_api.py (消息路由)
    ↓ (自然语言消息)
bot_agent.py (意图识别)
    ↓
自动调用对应功能模块
    ↓
robots/chat.py (聊天) 或 robots/summary.py (总结)

═══════════════════════════════════════════════════════════════

【标识语句块 (METADATA)】

每个模块文件包含统一的标识语句块，帮助 bot_agent 理解其功能：

  === METADATA ===
  name: 模块名称
  description: 模块描述
  functions:
    - name: 功能名
      description: 功能描述
  === END ===

═══════════════════════════════════════════════════════════════

【配置文件】

创建 `config.yaml` 文件（已加入 .gitignore，不会被提交）：

```yaml
# DeepSeek API 配置
deepseek_api_key: "your-deepseek-api-key"

# QQ Bot 配置
qq_bot_token: "your-qq-bot-token"

# NapCat WebSocket 配置
# 正向 WS：连接 NapCat 的 WebSocket 服务器（用于发送消息）
napcat_ws_url: "ws://127.0.0.1:3000/"

# 反向 WS：监听 NapCat 的连接（用于接收消息）
listen_host: "0.0.0.0"
listen_port: 3001
```

或者使用环境变量：

  export DEEPSEEK_API_KEY="your-deepseek-api-key"
  export QQ_BOT_TOKEN="your-qq-bot-token"

═══════════════════════════════════════════════════════════════

【安装依赖】

  pip install -r requirements.txt

需要添加 pyyaml 依赖：

  pip install pyyaml

═══════════════════════════════════════════════════════════════

【NapCat 配置】

编辑 config/onebot11_<QQ号>.json，添加 WebSocket 服务器：

{
  "network": {
    "httpServers": [
      {
        "enable": true,
        "name": "http-api",
        "host": "0.0.0.0",
        "port": 3000,
        "token": "your-qq-bot-token"
      }
    ],
    "websocketServers": [
      {
        "enable": true,
        "name": "ws-server",
        "host": "0.0.0.0",
        "port": 3001,
        "token": "your-qq-bot-token"
      }
    ]
  }
}

重启 NapCat 使配置生效。

═══════════════════════════════════════════════════════════════

【运行机器人】

  python run.py

或：

  python bot_api.py

═══════════════════════════════════════════════════════════════

【使用方式】

群聊：
  - @机器人 发送消息
  - 支持自然语言命令
  - 命令：/help

私聊：
  - 直接发送消息
  - 同样支持自然语言

═══════════════════════════════════════════════════════════════

【自然语言示例】

自然语言                    对应功能
─────────────────────────────────────────
总结一下今天的聊天          执行总结功能
更改人设，变成医生           设置新人设
清除历史                    清除对话历史
查看历史                    查看对话记录
帮助                       显示帮助信息

═══════════════════════════════════════════════════════════════

【使用 API】

from qq_api import QQBotAPI
from deepseek_api import DeepSeekAPI
from bot_agent import create_agent
from config_loader import load_config

# 加载配置
config = load_config("config.yaml")

# QQ Bot
bot = QQBotAPI(base_url="http://127.0.0.1:3000", token=config['qq_bot_token'])
bot.send_private_msg(user_id=123456789, message="你好！")

# DeepSeek
ai = DeepSeekAPI(api_key=config['deepseek_api_key'])
reply = ai.chat(user_msg="你好", system_msg="你是一个助手")

# Bot Agent（意图识别）
agent = create_agent(api_key=config['deepseek_api_key'])
result = agent.classify_intent("总结一下今天的聊天")
print(result.intent)  # IntentType.SUMMARIZE

═══════════════════════════════════════════════════════════════

【扩展开发】

添加新的机器人模式：

1. 在 robots/ 目录下创建新的 Python 文件
2. 添加 METADATA 标识语句块
3. 实现 create_robot(config) 工厂函数
4. 实现 handle_group(data, send_func) 和 handle_private(data, send_func) 方法

示例：

"""
=== METADATA ===
name: mymode
description: 我的自定义模式
functions:
  - name: handle_group
    description: 处理群聊消息
=== METADATA_END ===
"""

class MyRobot:
    def __init__(self, config):
        self.config = config
    
    async def handle_group(self, data, send_func):
        # 处理群聊消息
        pass
    
    async def handle_private(self, data, send_func):
        # 处理私聊消息
        pass

def create_robot(config):
    return MyRobot(config)
