"""配置管理模块。

使用 Pydantic 提供统一的配置验证、默认值管理和多源加载（YAML、环境变量）。
"""

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from qq_bot.core.exceptions import ConfigError


class LLMConfig(BaseModel):
    """LLM 服务配置。"""
    
    provider: Literal["deepseek", "ark"] = "deepseek"
    api_key: str = ""
    model: str = "deepseek-chat"
    base_url: str | None = None
    timeout: int = 60
    max_retries: int = 3
    
    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        if not v:
            # 尝试从环境变量获取
            v = os.getenv("DEEPSEEK_API_KEY", "")
        return v


class ArkConfig(BaseModel):
    """火山方舟配置。"""
    
    api_key: str = ""
    model: str = "doubao-seed-2-0-mini-260215"
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    
    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        if not v:
            v = os.getenv("ARK_API_KEY", "")
        return v


class StorageConfig(BaseModel):
    """存储配置。"""
    
    data_dir: str = "data"
    message_retention_days: int = 7
    conversation_max_context: int = 20
    conversation_max_storage: int = 100  # 最多保存多少条历史


class ChatPluginConfig(BaseModel):
    """聊天插件配置。"""
    
    enabled: bool = True
    system_prompt: str = ""
    max_input_tokens: int = 500
    max_output_tokens: int = 300
    max_prompt_tokens: int = 500
    group_context_messages: int = 10
    dynamic_persona_enabled: bool = True
    affection_enabled: bool = True


class SummaryPluginConfig(BaseModel):
    """总结插件配置。"""
    
    enabled: bool = True
    max_tokens: int = 4000
    default_window: str = "1h"
    max_window_days: int = 3


class DailySummaryConfig(BaseModel):
    """每日总结配置。"""
    
    enabled: bool = False
    group_id: int = 0
    max_tokens: int = 4000
    hour: int = 23
    minute: int = 0


class NewsConfig(BaseModel):
    """新闻服务配置。"""
    
    enabled: bool = False
    probability: float = 0.3
    cache_hours: float = 6.0


class OneBotConfig(BaseModel):
    """OneBot 协议配置。"""
    
    token: str = ""
    napcat_ws_url: str = "ws://127.0.0.1:3000/"
    listen_host: str = "0.0.0.0"
    listen_port: int = 3001
    reconnect_interval: int = 5
    heartbeat_interval: int = 30
    
    @field_validator("token")
    @classmethod
    def validate_token(cls, v: str) -> str:
        if not v:
            v = os.getenv("QQ_BOT_TOKEN", "")
        return v


class DebugConfig(BaseModel):
    """调试配置。"""
    
    enabled: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    save_prompts: bool = False  # 是否保存 prompts 到文件
    save_requests: bool = False  # 是否保存 API 请求


class BotConfig(BaseSettings):
    """机器人主配置。
    
    整合所有子配置，支持从 YAML 文件和环境变量加载。
    
    Example:
        >>> config = BotConfig.from_yaml("config.yaml")
        >>> print(config.llm.api_key)
        >>> 
        >>> # 或者直接实例化（从环境变量）
        >>> config = BotConfig()
    """
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # 忽略未定义的配置项（兼容旧配置）
    )
    
    # 版本号，用于配置迁移
    version: str = "2.0"
    
    # LLM 配置
    llm: LLMConfig = Field(default_factory=LLMConfig)
    ark: ArkConfig = Field(default_factory=ArkConfig)
    
    # 协议适配器配置
    onebot: OneBotConfig = Field(default_factory=OneBotConfig)
    
    # 存储配置
    storage: StorageConfig = Field(default_factory=StorageConfig)
    
    # 插件配置
    plugins: list[str] = Field(default_factory=lambda: ["chat", "summary"])
    chat: ChatPluginConfig = Field(default_factory=ChatPluginConfig)
    summary: SummaryPluginConfig = Field(default_factory=SummaryPluginConfig)
    daily_summary: DailySummaryConfig = Field(default_factory=DailySummaryConfig)
    
    # 新闻配置
    news: NewsConfig = Field(default_factory=NewsConfig)
    
    # 调试配置
    debug: DebugConfig = Field(default_factory=DebugConfig)
    
    # 工作线程数
    max_workers: int = 4
    
    @classmethod
    def from_yaml(cls, path: str | Path = "config.yaml") -> "BotConfig":
        """从 YAML 文件加载配置。
        
        Args:
            path: 配置文件路径，默认为当前目录下的 config.yaml。
            
        Returns:
            配置实例。
            
        Raises:
            ConfigError: 当文件不存在或格式错误时。
        """
        path = Path(path)
        
        if not path.exists():
            # 尝试查找 config_demo.yaml 作为模板
            demo_path = path.parent / "config_demo.yaml"
            if demo_path.exists():
                raise ConfigError(
                    f"配置文件不存在: {path}\n"
                    f"请复制 {demo_path} 到 {path} 并修改配置"
                )
            raise ConfigError(f"配置文件不存在: {path}")
        
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"YAML 格式错误: {e}")
        except Exception as e:
            raise ConfigError(f"读取配置文件失败: {e}")
        
        if not data:
            data = {}
        
        # 兼容旧版配置字段映射
        data = cls._migrate_legacy_config(data)
        
        return cls.model_validate(data)
    
    @classmethod
    def _migrate_legacy_config(cls, data: dict[str, Any]) -> dict[str, Any]:
        """迁移旧版配置到新版格式。
        
        处理以下映射：
        - deepseek_api_key -> llm.api_key
        - qq_bot_token -> onebot.token
        - napcat_ws_url -> onebot.napcat_ws_url
        - system_prompt -> chat.system_prompt
        - max_context -> storage.conversation_max_context
        """
        migrated = {"version": "2.0"}
        
        # LLM 配置
        llm_config = {}
        if "deepseek_api_key" in data:
            llm_config["api_key"] = data.pop("deepseek_api_key")
        if "ark_api_key" in data:
            migrated.setdefault("ark", {})["api_key"] = data.pop("ark_api_key")
        if "ark_model" in data:
            migrated.setdefault("ark", {})["model"] = data.pop("ark_model")
        if llm_config:
            migrated["llm"] = llm_config
        
        # OneBot 配置
        onebot_config = {}
        if "qq_bot_token" in data:
            onebot_config["token"] = data.pop("qq_bot_token")
        if "napcat_ws_url" in data:
            onebot_config["napcat_ws_url"] = data.pop("napcat_ws_url")
        if "listen_host" in data:
            onebot_config["listen_host"] = data.pop("listen_host")
        if "listen_port" in data:
            onebot_config["listen_port"] = data.pop("listen_port")
        if onebot_config:
            migrated["onebot"] = onebot_config
        
        # 存储配置
        storage_config = {}
        if "message_retention_days" in data:
            storage_config["message_retention_days"] = data.pop("message_retention_days")
        if "max_context" in data:
            storage_config["conversation_max_context"] = data.pop("max_context")
        if storage_config:
            migrated["storage"] = storage_config
        
        # 聊天配置
        chat_config = {}
        if "system_prompt" in data:
            chat_config["system_prompt"] = data.pop("system_prompt")
        if "max_input_tokens" in data:
            chat_config["max_input_tokens"] = data.pop("max_input_tokens")
        if "max_output_tokens" in data:
            chat_config["max_output_tokens"] = data.pop("max_output_tokens")
        if "max_prompt_tokens" in data:
            chat_config["max_prompt_tokens"] = data.pop("max_prompt_tokens")
        if "group_context_messages" in data:
            chat_config["group_context_messages"] = data.pop("group_context_messages")
        if "dynamic_persona_enabled" in data:
            chat_config["dynamic_persona_enabled"] = data.pop("dynamic_persona_enabled")
        if chat_config:
            migrated["chat"] = chat_config
        
        # 每日总结配置
        daily_config = {}
        if "daily_summary_enabled" in data:
            daily_config["enabled"] = data.pop("daily_summary_enabled")
        if "daily_summary_group_id" in data:
            daily_config["group_id"] = data.pop("daily_summary_group_id")
        if "daily_summary_max_tokens" in data:
            daily_config["max_tokens"] = data.pop("daily_summary_max_tokens")
        if "daily_summary_hour" in data:
            daily_config["hour"] = data.pop("daily_summary_hour")
        if "daily_summary_minute" in data:
            daily_config["minute"] = data.pop("daily_summary_minute")
        if daily_config:
            migrated["daily_summary"] = daily_config
        
        # 新闻配置
        news_config = {}
        if "news_enabled" in data:
            news_config["enabled"] = data.pop("news_enabled")
        if "news_probability" in data:
            news_config["probability"] = data.pop("news_probability")
        if "news_cache_hours" in data:
            news_config["cache_hours"] = data.pop("news_cache_hours")
        if news_config:
            migrated["news"] = news_config
        
        # 调试配置
        if "debug_mode" in data:
            migrated["debug"] = {"enabled": data.pop("debug_mode")}
        
        # 保留其他未映射的配置
        for key, value in data.items():
            if key not in migrated:
                migrated[key] = value
        
        return migrated
    
    def to_yaml(self, path: str | Path) -> None:
        """保存配置到 YAML 文件。
        
        Args:
            path: 目标文件路径。
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        
        # 使用 model_dump 并排除默认值
        data = self.model_dump(mode="json", exclude_defaults=True)
        
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False)


# 兼容旧版导入
def load_config(config_path: str = "config.yaml") -> dict[str, Any]:
    """兼容旧版的配置加载函数。
    
    Args:
        config_path: 配置文件路径。
        
    Returns:
        配置字典（扁平化格式）。
    """
    config = BotConfig.from_yaml(config_path)
    # 返回扁平化的字典以保持兼容性
    return {
        "deepseek_api_key": config.llm.api_key,
        "qq_bot_token": config.onebot.token,
        "napcat_ws_url": config.onebot.napcat_ws_url,
        "listen_host": config.onebot.listen_host,
        "listen_port": config.onebot.listen_port,
        "system_prompt": config.chat.system_prompt,
        "max_context": config.storage.conversation_max_context,
        "max_input_tokens": config.chat.max_input_tokens,
        "max_output_tokens": config.chat.max_output_tokens,
        "max_prompt_tokens": config.chat.max_prompt_tokens,
        "message_retention_days": config.storage.message_retention_days,
        "daily_summary_enabled": config.daily_summary.enabled,
        "daily_summary_group_id": config.daily_summary.group_id,
        "daily_summary_max_tokens": config.daily_summary.max_tokens,
        "daily_summary_hour": config.daily_summary.hour,
        "daily_summary_minute": config.daily_summary.minute,
        "debug_mode": config.debug.enabled,
    }
