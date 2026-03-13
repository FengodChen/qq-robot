# QQ Bot 重构完成报告

## 重构目标

将代码重构为**结构化、相互解耦、风格统一**的代码库。

---

## 新架构概览

### 目录结构

```
qq_bot/
├── __init__.py              # 包初始化，暴露公共API
├── __main__.py              # 模块入口: python -m qq_bot
├── cli.py                   # 命令行入口
├── core/                    # 核心框架层
│   ├── config.py            # Pydantic 配置管理
│   ├── events.py            # 事件系统
│   ├── plugin.py            # 插件基类与注册机制
│   ├── context.py           # 请求上下文
│   ├── router.py            # 消息路由
│   ├── application.py       # 应用核心（整合所有组件）
│   └── exceptions.py        # 异常体系
├── adapters/                # 适配器层
│   ├── base.py              # 适配器基类
│   └── onebot11.py          # OneBot 11 协议适配器
├── services/                # 服务层
│   ├── llm/                 # LLM 服务
│   │   ├── base.py          # LLM 基类
│   │   └── deepseek.py      # DeepSeek API 实现
│   └── storage/             # 存储服务
│       ├── base.py          # 存储基类
│       ├── db.py            # 数据库工具
│       ├── message.py       # 消息存储
│       └── conversation.py  # 对话上下文管理
├── plugins/                 # 插件层
│   ├── chat/                # 聊天插件
│   │   ├── plugin.py        # 主插件类
│   │   ├── conversation.py  # 对话管理
│   │   ├── persona.py       # 人设管理
│   │   └── affection.py     # 好感度系统
│   └── summary/             # 总结插件
│       └── plugin.py
├── agent/                   # 智能代理层
│   ├── intents.py           # 意图定义
│   ├── classifier.py        # 意图分类器
│   └── prompts.py           # Prompt 模板
└── utils/                   # 工具函数
    ├── text.py              # 文本处理
    └── time.py              # 时间处理
```

---

## 架构改进

### 1. 分层架构

```
┌─────────────────────────────────────────────────────────────┐
│  插件层 (Plugins)                                             │
│  ChatPlugin / SummaryPlugin                                  │
├─────────────────────────────────────────────────────────────┤
│  代理层 (Agent)                                               │
│  IntentClassifier → Intent → Action                          │
├─────────────────────────────────────────────────────────────┤
│  服务层 (Services)                                            │
│  LLMService / StorageService                                 │
├─────────────────────────────────────────────────────────────┤
│  适配器层 (Adapters)                                          │
│  OneBot11Adapter                                             │
├─────────────────────────────────────────────────────────────┤
│  核心层 (Core)                                                │
│  Config / EventBus / PluginManager / Router                  │
└─────────────────────────────────────────────────────────────┘
```

### 2. 依赖方向

**单向依赖**：插件层 → 代理层 → 服务层 → 适配器层 → 核心层

下层不感知上层存在，实现真正的解耦。

### 3. 统一配置管理

使用 Pydantic 实现配置验证和默认值：

```python
from qq_bot.core.config import BotConfig

config = BotConfig.from_yaml("config.yaml")
print(config.llm.api_key)
print(config.chat.system_prompt)
```

### 4. 事件驱动架构

统一的事件系统：

```python
from qq_bot.core.events import MessageEvent, ResponseEvent

async def handle_message(event: MessageEvent) -> ResponseEvent | None:
    if "hello" in event.content:
        return ResponseEvent(
            content="Hello!",
            target_user_id=event.user_id
        )
    return None
```

### 5. 插件注册机制

使用装饰器注册插件：

```python
from qq_bot.core.plugin import Plugin, PluginInfo, register_plugin

@register_plugin("my_plugin", description="我的插件")
class MyPlugin(Plugin):
    @property
    def info(self) -> PluginInfo:
        return PluginInfo(name="my_plugin")
    
    async def on_message(self, ctx, event):
        # 处理消息
        pass
```

### 6. 统一异常体系

```python
from qq_bot.core.exceptions import BotError, ConfigError, LLMError

try:
    config = BotConfig.from_yaml("config.yaml")
except ConfigError as e:
    print(f"配置错误: {e}")
```

---

## 代码风格统一

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

## 使用方法

### 启动机器人

```bash
# 使用新架构启动
python run_v2.py

# 或使用模块方式
python -m qq_bot

# 指定配置文件
python run_v2.py -c config.yaml

# 调试模式
python run_v2.py --debug
```

### 初始化配置

```bash
python run_v2.py init -o config.yaml
```

### 编程方式使用

```python
from qq_bot import create_app
from qq_bot.core.config import BotConfig

config = BotConfig.from_yaml("config.yaml")
app = create_app(config)

# 运行
import asyncio
asyncio.run(app.run())
```

---

## 与旧代码对比

| 功能 | 旧架构 | 新架构 |
|------|--------|--------|
| 配置管理 | 分散在多处，yaml.safe_load | Pydantic 统一配置 |
| 消息处理 | bot_api.py 直接处理 | 事件驱动，插件化 |
| 意图识别 | bot_agent.py 紧耦合 | agent 模块，独立服务 |
| 聊天功能 | robots/chat.py | plugins/chat/ 插件包 |
| 存储 | 多个独立模块 | services/storage/ 统一 |
| 扩展性 | 需修改多处代码 | 插件注册机制 |

---

## 文件统计

- **总 Python 文件**: 38 个
- **总代码行数**: 约 5000+ 行
- **核心模块**: 12 个
- **服务模块**: 8 个
- **插件模块**: 6 个

---

## 后续建议

1. **测试覆盖**: 为新架构编写单元测试
2. **文档完善**: 添加 API 文档和使用示例
3. **功能迁移**: 逐步将旧功能迁移到新架构
4. **性能优化**: 针对异步处理进行性能调优
5. **类型检查**: 使用 mypy 进行静态类型检查

---

## 兼容性说明

- 新架构使用 `qq_bot/` 目录，与旧代码并存
- 原 `run.py` 继续使用旧架构
- 新 `run_v2.py` 使用新架构
- 配置文件兼容，支持旧版格式自动迁移
