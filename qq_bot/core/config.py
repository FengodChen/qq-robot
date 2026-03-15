"""配置管理模块。

使用 Pydantic 提供统一的配置验证、默认值管理和多源加载（YAML、环境变量）。
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from qq_bot.core.exceptions import ConfigError


class LLMConfig(BaseModel):
    """LLM 服务配置。"""
    
    model_config = ConfigDict(extra="forbid")
    
    provider: Literal["deepseek", "ark"]
    api_key: str
    model: str
    base_url: str | None = None
    timeout: int = 60
    max_retries: int = 3


class ArkConfig(BaseModel):
    """火山方舟配置。"""
    
    model_config = ConfigDict(extra="forbid")
    
    api_key: str
    model: str
    base_url: str = "https://ark.cn-beijing.volces.com/api/v3"


class StorageConfig(BaseModel):
    """存储配置。"""
    
    model_config = ConfigDict(extra="forbid")
    
    data_dir: str
    message_retention_days: int
    conversation_max_context: int
    conversation_max_storage: int = 100  # 最多保存多少条历史


class ChatPluginConfig(BaseModel):
    """聊天插件配置。"""
    
    model_config = ConfigDict(extra="forbid")
    
    enabled: bool = True
    system_prompt: str
    max_input_tokens: int
    max_output_tokens: int
    max_prompt_tokens: int
    group_context_messages: int
    dynamic_persona_enabled: bool = True
    affection_enabled: bool = True


class SummaryPluginConfig(BaseModel):
    """总结插件配置。"""
    
    model_config = ConfigDict(extra="forbid")
    
    enabled: bool = True
    max_tokens: int
    default_window: str
    max_window_days: int


class DailySummaryConfig(BaseModel):
    """每日总结配置。"""
    
    model_config = ConfigDict(extra="forbid")
    
    enabled: bool
    group_id: int
    hour: int
    minute: int


class NewsConfig(BaseModel):
    """新闻服务配置。"""
    
    model_config = ConfigDict(extra="forbid")
    
    enabled: bool
    probability: float
    cache_hours: float
    system_prompt: str = "你是一个新闻助手。请使用 web_search 工具搜索今天的最新真实新闻，提供简洁准确的新闻摘要。"
    user_prompt: str = "请搜索今天（{date}）的最新重要新闻，列出3-5条真实新闻，每条用一句话概括，总字数控制在200字以内。"


class OneBotConfig(BaseModel):
    """OneBot 协议配置。"""
    
    model_config = ConfigDict(extra="forbid")
    
    token: str
    napcat_ws_url: str
    listen_host: str
    listen_port: int
    reconnect_interval: int = 5
    heartbeat_interval: int = 30


class DebugConfig(BaseModel):
    """调试配置。"""
    
    model_config = ConfigDict(extra="forbid")
    
    enabled: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    save_prompts: bool = False  # 是否保存 prompts 到文件
    save_requests: bool = False  # 是否保存 API 请求


class ChatPromptsConfig(BaseModel):
    """聊天插件提示词配置。"""
    
    model_config = ConfigDict(extra="forbid")
    
    level_descriptions: dict[str, str]
    tone_descriptions: dict[str, str]
    chat_requirements: str
    help_text: str
    persona_extraction: str
    affection_evaluation: str


class AgentPromptsConfig(BaseModel):
    """Agent 提示词配置。"""
    
    model_config = ConfigDict(extra="forbid")
    
    intent_classification: str
    persona_extraction: str


class AffectionPromptsConfig(BaseModel):
    """好感度系统提示词配置。"""
    
    model_config = ConfigDict(extra="forbid")
    
    preference_generation: str
    evaluation: str


class SummaryPromptsConfig(BaseModel):
    """总结服务提示词配置。"""
    
    model_config = ConfigDict(extra="forbid")
    
    instructions: str


class PromptsConfig(BaseModel):
    """提示词总配置。"""
    
    model_config = ConfigDict(extra="forbid")
    
    chat: ChatPromptsConfig
    agent: AgentPromptsConfig
    affection: AffectionPromptsConfig
    summary: SummaryPromptsConfig


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
        extra="forbid",
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
    
    # 提示词配置
    prompts: PromptsConfig
    
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
        
        return cls.model_validate(data)
    
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
