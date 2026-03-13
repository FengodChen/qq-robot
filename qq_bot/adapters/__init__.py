"""适配器层。

提供协议适配器，处理与 OneBot/NapCat 的通信。
"""

from qq_bot.adapters.base import Adapter, ConnectionState
from qq_bot.adapters.onebot11 import OneBot11Adapter

__all__ = [
    "Adapter",
    "ConnectionState", 
    "OneBot11Adapter",
]
