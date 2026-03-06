# 变更总结

## 2024-02-25 主要更新

### 1. 删除手动切换模式功能

**问题**：原本需要手动输入切换模式（如"切换到总结模式"），现在有 Agent 后不再需要。

**解决方案**：
- 删除 `bot_agent.py` 中的 `SWITCH_MODE` 意图
- 删除 `_handle_switch_mode` 方法
- 修改意图识别逻辑，Agent 自动决定执行哪个功能
- 用户现在只需说"总结一下"，Agent 会自动调用总结功能

**变更文件**：
- `bot_agent.py`: 移除切换模式相关代码
- `bot_api.py`: 移除 `/mode`, `/modes` 命令处理

### 2. 修复总结功能

**问题**：总结功能没有生效。

**修复**：
- 修复 `bot_agent.py` 中 `_handle_summarize` 方法的返回值处理
- 修复 `robots/summary.py` 中 `run_in_executor` 的参数传递方式

**变更文件**：
- `bot_agent.py`: 修复总结处理逻辑
- `robots/summary.py`: 修复 API 调用参数传递

### 3. 配置文件支持

**问题**：API key 和 token 硬编码在代码中。

**解决方案**：
- 创建 `config.yaml` 配置文件（已加入 .gitignore）
- 创建 `config_loader.py` 配置加载模块
- 支持从 YAML 文件或环境变量加载配置
- 移除所有模块中的硬编码 API key

**新增文件**：
- `config.yaml`: 配置文件模板
- `config_loader.py`: 配置加载模块

**变更文件**：
- `.gitignore`: 添加 `config.yaml`, `config.yml`
- `robots/chat.py`: 移除默认 API key
- `qq_api.py`: 移除测试代码中的硬编码 token
- `bot_api.py`: 使用配置文件加载
- `run.py`: 使用配置文件加载

### 4. 文档更新

**变更文件**：
- `README.md`: 更新使用说明，删除切换模式相关内容
- `requirements.txt`: 添加 `pyyaml` 依赖

## 使用方法

1. 确保 `config.yaml` 文件存在并包含正确的配置：

```yaml
deepseek_api_key: "your-deepseek-api-key"
qq_bot_token: "your-qq-bot-token"

# 正向 WS：连接 NapCat 的 WebSocket 服务器（用于发送消息）
napcat_ws_url: "ws://127.0.0.1:3000/"

# 反向 WS：监听 NapCat 的连接（用于接收消息）
listen_host: "0.0.0.0"
listen_port: 3001
```

2. 运行机器人：

```bash
python run.py
```

3. 使用自然语言与机器人交互：

- "总结一下今天的聊天" → 自动执行总结
- "更改人设成温柔的大姐姐" → 自动更改人设
- "清除历史" → 自动清除对话历史
- "查看历史" → 查看对话记录
- 直接聊天 → 普通对话

## 注意事项

- `config.yaml` 包含敏感信息，已被添加到 `.gitignore`，不会被提交到 Git
- 也可以通过环境变量配置：`DEEPSEEK_API_KEY` 和 `QQ_BOT_TOKEN`
- 环境变量优先级高于配置文件
