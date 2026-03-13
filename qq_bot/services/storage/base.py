"""存储服务基类。

定义存储服务的通用接口。
"""

from abc import ABC, abstractmethod
from typing import Any


class StorageService(ABC):
    """存储服务基类。"""
    
    @abstractmethod
    async def initialize(self) -> None:
        """初始化存储服务。"""
        pass
    
    @abstractmethod
    async def close(self) -> None:
        """关闭存储服务。"""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查。
        
        Returns:
            服务是否正常。
        """
        pass
