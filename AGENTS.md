# QQ Bot - AI Coding Agent Guide

本文档为 AI 编程助手提供项目背景、架构说明和开发指南。

---

## 项目概述

QQ Bot 是一个基于 OneBot 11 协议的 QQ 机器人框架，集成 DeepSeek AI 能力，支持自然语言交互。机器人采用插件化架构，具有意图识别、人设定制、好感度系统、聊天记录总结等功能。

### 主要功能

- **AI 聊天**: 支持群聊和私聊，具备上下文记忆
- **人设定制**: 动态更改机器人角色设定
- **好感度系统**: 根据对话内容动态调整用户关系
- **聊天记录总结**: 支持自然语言表达时间范围
- **新闻服务**: 自动获取并推送当日新闻
- **每日定时总结**: 定时发送群聊总结

---

## 技术栈

- **Python**: >= 3.13
- **异步框架**: asyncio
- **配置管理**: Pydantic + Pydantic-Settings
- **WebSocket**: websockets 库（用于 OneBot 协议通信）
- **HTTP 客户端**: httpx, aiohttp
- **数据存储**: SQLite（消息存储、对话历史、好感度数据）
- **包管理**: uv (ultraviolet)

### 主要依赖

```
aiohttp>=3.13.3
httpx>=0.28.1
pydantic>=2.0
pydantic-settings>=2.0
pyyaml>=6.0.3
requests>=2.32.5
textual>=8.0.2
tiktoken>=0.12.0
websocket-client>=1.9.0
websockets>=16.0
```

---

## 项目结构

```
qq_bot/
├── __init__.py              # 包初始化，暴露公共 API
├── __main__.py              # 模块入口: python -m qq_bot
├── cli.py                   # 命令行入口和配置初始化
├── core/                    # 核心框架层
│   ├── config.py            # Pydantic 配置管理
│   ├── events.py            # 事件系统（MessageEvent, ResponseEvent 等）
│   ├── plugin.py            # 插件基类与注册机制
│   ├── context.py           # 请求上下文和服务容器
│   ├── application.py       # 应用核心（整合所有组件）
│   ├── router.py            # 消息路由
│   └── exceptions.py        # 异常体系
├── adapters/                # 适配器层
│   ├── base.py              # 适配器基类
│   └── onebot11.py          # OneBot 11 协议适配器
├── services/                # 服务层
│   ├── llm/                 # LLM 服务
│   │   ├── base.py          # LLM 基类和消息类型
│   │   └── deepseek.py      # DeepSeek API 实现
│   ├── storage/             # 存储服务
│   │   ├── base.py          # 存储基类
│   │   ├── db.py            # 数据库工具
│   │   ├── message.py       # 消息存储（SQLite）
│   │   └── conversation.py  # 对话上下文管理
│   ├── daily_summary/       # 每日总结服务
│   │   └── scheduler.py     # 定时任务调度
│   ├── news_service.py      # 新闻服务
│   └── summary_service.py   # 总结服务
├── plugins/                 # 插件层
│   ├── chat/                # 聊天插件
│   │   ├── plugin.py        # 主插件类（实现 ChatPlugin）
│   │   ├── conversation.py  # 对话管理
│   │   ├── persona.py       # 人设管理
│   │   └── affection.py     # 好感度系统
│   └── summary/             # 总结插件
│       └── plugin.py
├── agent/                   # 智能代理层
│   ├── intents.py           # 意图类型定义（IntentType）
│   ├── classifier.py        # 意图分类器
│   └── prompts.py           # Prompt 模板
└── utils/                   # 工具函数
    ├── text.py              # 文本处理
    ├── time.py              # 时间处理
    └── debug_logger.py      # 调试日志

data/                        # 数据存储目录（SQLite 数据库）
config.yaml                  # 主配置文件（已加入 .gitignore）
pyproject.toml              # Python 项目配置
requirements.txt            # 依赖列表
REFACTOR.md                 # 重构文档
CHANGES.md                  # 变更日志
```

---

## 架构说明

### 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│  插件层 (Plugins)                                            │
│  ChatPlugin / SummaryPlugin                                 │
├─────────────────────────────────────────────────────────────┤
│  代理层 (Agent)                                              │
│  IntentClassifier → Intent → Action                         │
├─────────────────────────────────────────────────────────────┤
│  服务层 (Services)                                           │
│  LLMService / StorageService / NewsService                  │
├─────────────────────────────────────────────────────────────┤
│  适配器层 (Adapters)                                         │
│  OneBot11Adapter                                            │
├─────────────────────────────────────────────────────────────┤
│  核心层 (Core)                                               │
│  Config / EventBus / PluginManager / Application            │
└─────────────────────────────────────────────────────────────┘
```

**依赖方向**: 插件层 → 代理层 → 服务层 → 适配器层 → 核心层

下层不感知上层存在，实现真正的解耦。

### 核心流程

1. **消息接收**: OneBot11Adapter 通过反向 WebSocket 接收消息
2. **消息存储**: 所有消息存入 SQLite 数据库
3. **意图识别**: IntentClassifier 使用 LLM 识别用户意图
4. **事件路由**: Application 根据意图路由到对应插件
5. **插件处理**: ChatPlugin 或 SummaryPlugin 处理业务逻辑
6. **消息发送**: 通过正向 WebSocket 发送回复

### 并发模型

- 每个用户有独立的消息队列 `(group_id, user_id)`
- 同一用户的消息顺序处理
- 不同用户的消息并发处理
- 使用 asyncio.Task 管理用户级处理任务

---

## 配置文件

配置文件为 `config.yaml`，位于项目根目录。模板可参考 `qq_bot/cli.py` 中的 `DEFAULT_CONFIG_CONTENT`。

### 关键配置项

```yaml
# LLM API 配置
llm:
  provider: deepseek
  api_key: "your-deepseek-api-key"
  model: deepseek-chat
  base_url: "https://api.deepseek.com/v1"

# 火山引擎 Ark API 配置（用于新闻搜索）
ark:
  api_key: "your-ark-api-key"
  model: "your-model"

# OneBot 协议配置
onebot:
  token: "your-bot-token"
  napcat_ws_url: "ws://127.0.0.1:3000/"  # 正向 WS（发送消息）
  listen_host: "0.0.0.0"
  listen_port: 3001                      # 反向 WS（接收消息）

# 存储配置
storage:
  data_dir: "data"
  message_retention_days: 7
  conversation_max_context: 10

# 插件列表
plugins:
  - chat
  - summary

# 调试配置
debug:
  enabled: false
  log_level: INFO
  save_prompts: false
  save_requests: false
```

**注意**: `config.yaml` 包含敏感信息，已加入 `.gitignore`，不会被提交到 Git。

---

## 运行和调试

### 安装依赖

```bash
# 使用 uv 安装依赖
uv sync

# 或传统方式
pip install -r requirements.txt
```

### 启动机器人

```bash
# 方式 1: 使用模块方式（推荐）
python -m qq_bot

# 方式 2: 使用 run.py（旧版兼容）
python run.py

# 指定配置文件
python -m qq_bot -c config.yaml

# 调试模式
python -m qq_bot --debug
```

### 初始化配置

```bash
# 生成默认配置文件
python -m qq_bot init -o config.yaml
```

### 编程方式使用

```python
from qq_bot import create_app
from qq_bot.core.config import BotConfig

# 加载配置
config = BotConfig.from_yaml("config.yaml")

# 创建应用
app = create_app(config)

# 运行
import asyncio
asyncio.run(app.run())
```

---

## 代码规范

### 命名规范

| 类型 | 规范 | 示例 |
|------|------|------|
| 模块 | 小写 + 下划线 | `message_store.py` |
| 类 | 大驼峰 | `MessageStore` |
| 函数/方法 | 小写 + 下划线 | `get_context()` |
| 常量 | 大写 + 下划线 | `MAX_CONTEXT_SIZE` |
| 私有 | 下划线前缀 | `_internal_method()` |

### 文档规范

统一使用 Google 风格 docstring：

```python
def add_message(self, msg: Message) -> bool:
    """添加消息到存储。
    
    Args:
        msg: 消息对象。
        
    Returns:
        是否成功添加。
        
    Raises:
        StorageError: 当数据库操作失败时抛出。
    """
```

### 类型注解

强制使用类型注解：

```python
def get_messages(
    user_id: int,
    group_id: Optional[int] = None
) -> List[Message]:
    ...
```

---

## 插件开发

### 创建新插件

1. 在 `qq_bot/plugins/` 下创建新目录
2. 创建 `plugin.py` 文件，继承 `Plugin` 基类
3. 在 `application.py` 中注册插件

示例：

```python
from qq_bot.core.plugin import Plugin, PluginInfo
from qq_bot.core.context import Context
from qq_bot.core.events import MessageEvent, ResponseEvent

class MyPlugin(Plugin):
    @property
    def info(self) -> PluginInfo:
        return PluginInfo(
            name="my_plugin",
            description="我的插件",
            version="1.0.0"
        )
    
    async def on_message(self, ctx: Context, event: MessageEvent) -> ResponseEvent | None:
        if "hello" in event.content:
            return ResponseEvent(
                content="Hello!",
                target_user_id=event.user_id,
                target_group_id=event.group_id
            )
        return None
```

### 注册插件

在 `qq_bot/core/application.py` 的 `_setup_plugins` 方法中：

```python
self._plugin_manager.register("my_plugin", MyPlugin, description="我的插件")
```

---

## 意图类型

意图定义在 `qq_bot/agent/intents.py`：

| 意图 | 说明 | 典型触发语 |
|------|------|-----------|
| CHAT | 普通聊天 | 任意对话 |
| SUMMARIZE | 总结聊天记录 | "总结一下" |
| SET_PERSONA | 更改人设 | "更改人设成xxx" |
| GET_PERSONA | 查看人设 | "查看人设" |
| RESET_PERSONA | 恢复默认人设 | "恢复默认" |
| CLEAR_HISTORY | 清除历史 | "清除历史" |
| VIEW_HISTORY | 查看历史 | "查看历史" |
| VIEW_AFFECTION | 查看好感度 | "好感度" |
| CONFIRM | 确认操作 | "确认" |
| CANCEL | 取消操作 | "取消" |
| HELP | 帮助 | "帮助" |

---

## 数据库说明

数据存储在 `data/` 目录下：

- `messages.db`: 所有接收到的消息（用于总结和上下文）
- `chat_history.db`: 对话历史（用户与机器人的对话）
- `affection_data.db`: 好感度数据
- `user_modes.db`: 用户模式设置
- `news_cache.json`: 新闻缓存

---

## NapCat 配置

NapCat 是基于 OneBot 11 协议的 QQ 机器人框架。编辑 `config/onebot11_<QQ号>.json`：

```json
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
```

---

## 测试策略

当前项目**缺少自动化测试**，建议：

1. 使用 `pytest` 编写单元测试
2. 测试目录建议为 `tests/`
3. 优先测试以下模块：
   - `agent/classifier.py` - 意图分类
   - `services/llm/` - LLM 服务
   - `services/storage/` - 存储服务
   - `plugins/chat/affection.py` - 好感度计算

---

## 安全注意事项

1. **API Key 保护**: `config.yaml` 包含敏感信息，已加入 `.gitignore`
2. **输入验证**: 所有用户输入都应经过验证，参见 `persona.validate_prompt()`
3. **敏感操作确认**: 清除历史、更改人设等操作需要用户确认
4. **消息长度限制**: 聊天消息有长度限制，防止 prompt 注入

---

## 常见问题

### 连接失败

- 检查 NapCat 是否正确启动
- 检查 `napcat_ws_url` 和 `listen_port` 配置
- 检查防火墙设置

### 意图识别失败

- 检查 DeepSeek API Key 是否有效
- 开启 `debug.enabled` 查看详细日志
- 检查 `prompts.agent.intent_classification` 配置

### 消息发送失败

- 检查正向 WebSocket 连接状态
- 检查 Bot Token 是否正确
- 查看 NapCat 日志

---

## 相关文档

- `README.md` - 用户使用说明
- `REFACTOR.md` - 重构架构说明
- `CHANGES.md` - 变更日志
- OneBot 11 协议文档: https://11.onebot.dev/
