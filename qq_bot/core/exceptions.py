"""异常体系定义。

提供统一的异常层次结构，便于错误处理和调试。
"""


class BotError(Exception):
    """基础异常类。
    
    所有机器人相关异常的基类。
    
    Attributes:
        message: 错误描述信息。
        code: 可选的错误代码。
    """
    
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        
    def __str__(self) -> str:
        if self.code:
            return f"[{self.code}] {self.message}"
        return self.message


class ConfigError(BotError):
    """配置错误。
    
    当配置文件格式错误、缺少必要配置项或配置值无效时抛出。
    
    Example:
        >>> raise ConfigError("缺少必要的 API Key: deepseek_api_key")
    """
    pass


class PluginError(BotError):
    """插件错误。
    
    当插件加载失败、初始化错误或运行时异常时抛出。
    
    Attributes:
        plugin_name: 发生错误的插件名称。
    """
    
    def __init__(self, message: str, plugin_name: str | None = None):
        super().__init__(message)
        self.plugin_name = plugin_name


class StorageError(BotError):
    """存储错误。
    
    当数据库操作失败、数据损坏或存储空间不足时抛出。
    """
    pass


class AdapterError(BotError):
    """适配器错误。
    
    当与 NapCat/OneBot 通信失败时抛出。
    
    Attributes:
        endpoint: 发生错误的 API 端点。
    """
    
    def __init__(self, message: str, endpoint: str | None = None):
        super().__init__(message)
        self.endpoint = endpoint


class LLMError(BotError):
    """LLM 服务错误。
    
    当调用 DeepSeek/Ark 等 LLM API 失败时抛出。
    
    Attributes:
        provider: LLM 提供商名称。
        status_code: HTTP 状态码。
    """
    
    def __init__(
        self, 
        message: str, 
        provider: str | None = None,
        status_code: int | None = None
    ):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class IntentError(BotError):
    """意图识别错误。
    
    当意图分类器无法解析用户意图时抛出。
    """
    pass


class ValidationError(BotError):
    """验证错误。
    
    当输入数据验证失败时抛出。
    """
    pass
