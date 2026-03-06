"""
QQ Bot API 模块（WebSocket 版本）
基于 OneBot 11 协议，使用 WebSocket 接收消息，HTTP 发送消息
"""

import requests
import websocket
import json
import threading
import time
from typing import Optional, Callable, Dict, Any, List, Union
from dataclasses import dataclass
from urllib.parse import urlencode


def extract_text_from_message(message):
    """从消息段数组中提取纯文本"""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        texts = []
        for seg in message:
            if seg.get("type") == "text":
                texts.append(seg.get("data", {}).get("text", ""))
        return "".join(texts)
    return str(message)


@dataclass
class PrivateMessage:
    """私聊消息数据类"""
    user_id: int
    message: str          # 纯文本消息
    message_id: int
    raw_message: str
    time: int
    self_id: int
    message_segments: list  # 原始消息段
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PrivateMessage":
        msg_segments = data.get("message", [])
        text = extract_text_from_message(msg_segments)
        return cls(
            user_id=data.get("user_id", 0),
            message=text,
            message_id=data.get("message_id", 0),
            raw_message=data.get("raw_message", ""),
            time=data.get("time", 0),
            self_id=data.get("self_id", 0),
            message_segments=msg_segments if isinstance(msg_segments, list) else []
        )


@dataclass
class GroupMessage:
    """群聊消息数据类"""
    group_id: int
    user_id: int
    message: str          # 纯文本消息
    message_id: int
    raw_message: str
    sender: Dict[str, Any]
    time: int
    self_id: int
    message_segments: list  # 原始消息段
    nickname: str         # 用户昵称
    card: str             # 群名片/群昵称
    sex: str              # 性别 (male/female/unknown)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GroupMessage":
        msg_segments = data.get("message", [])
        text = extract_text_from_message(msg_segments)
        sender = data.get("sender", {})
        return cls(
            group_id=data.get("group_id", 0),
            user_id=data.get("user_id", 0),
            message=text,
            message_id=data.get("message_id", 0),
            raw_message=data.get("raw_message", ""),
            sender=sender,
            time=data.get("time", 0),
            self_id=data.get("self_id", 0),
            message_segments=msg_segments if isinstance(msg_segments, list) else [],
            nickname=sender.get("nickname", "未知"),
            card=sender.get("card", ""),
            sex=sender.get("sex", "unknown")
        )


class QQBotAPI:
    """QQ Bot API 客户端（WebSocket 版本）"""
    
    def __init__(self, base_url: str = "http://127.0.0.1:3000", 
                 ws_url: str = None,  # 如果为 None，自动从 base_url 推断
                 token: Optional[str] = None):
        """
        初始化 QQ Bot API 客户端
        
        Args:
            base_url: OneBot HTTP API 地址
            ws_url: OneBot WebSocket 地址
            token: 访问令牌（可选）
        """
        self.base_url = base_url.rstrip("/")
        # 自动推断 WebSocket URL
        if ws_url is None:
            ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
        self.ws_url = ws_url.rstrip("/")
        self.token = token
        self.headers = {"Content-Type": "application/json"}
        
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        
        # 消息处理器
        self._private_message_handler: Optional[Callable[[PrivateMessage], None]] = None
        self._group_message_handler: Optional[Callable[[GroupMessage], None]] = None
        
        # WebSocket
        self._ws: Optional[websocket.WebSocketApp] = None
        self._ws_thread: Optional[threading.Thread] = None
        self._running = False
    
    def _call_api(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """调用 OneBot HTTP API"""
        url = f"{self.base_url}/{endpoint}"
        try:
            response = requests.post(url, headers=self.headers, json=data, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            raise requests.RequestException(f"API 请求失败: {e}")
    
    # ==================== 消息发送接口 ====================
    
    def send_private_msg(self, user_id: int, message: Union[str, List[Dict]]) -> int:
        """发送私聊消息"""
        if isinstance(message, str):
            message = [{"type": "text", "data": {"text": message}}]
        
        result = self._call_api("send_private_msg", {
            "user_id": user_id,
            "message": message
        })
        
        if result.get("status") == "ok":
            return result["data"]["message_id"]
        else:
            raise RuntimeError(f"发送私聊消息失败: {result.get('message', '未知错误')}")
    
    def send_group_msg(self, group_id: int, message: Union[str, List[Dict]]) -> int:
        """发送群聊消息"""
        if isinstance(message, str):
            message = [{"type": "text", "data": {"text": message}}]
        
        result = self._call_api("send_group_msg", {
            "group_id": group_id,
            "message": message
        })
        
        if result.get("status") == "ok":
            return result["data"]["message_id"]
        else:
            raise RuntimeError(f"发送群聊消息失败: {result.get('message', '未知错误')}")
    
    def send_group_reply(self, group_id: int, user_id: int, 
                         message: Union[str, List[Dict]], at_user: bool = True) -> int:
        """发送群聊回复消息（@某人并回复）"""
        msg_segments = []
        
        if at_user:
            msg_segments.append({
                "type": "at",
                "data": {"qq": str(user_id)}
            })
            msg_segments.append({
                "type": "text",
                "data": {"text": " "}
            })
        
        if isinstance(message, str):
            msg_segments.append({"type": "text", "data": {"text": message}})
        else:
            msg_segments.extend(message)
        
        return self.send_group_msg(group_id, msg_segments)
    
    # ==================== 消息接收接口 ====================
    
    def on_private_message(self, handler: Callable[[PrivateMessage], None]):
        """注册私聊消息处理器"""
        self._private_message_handler = handler
        return handler
    
    def on_group_message(self, handler: Callable[[GroupMessage], None]):
        """注册群聊消息处理器"""
        self._group_message_handler = handler
        return handler
    
    def _on_ws_message(self, ws, message):
        """处理 WebSocket 消息"""
        try:
            data = json.loads(message)
            post_type = data.get("post_type")
            message_type = data.get("message_type")
            
            if post_type == "message":
                if message_type == "private" and self._private_message_handler:
                    msg = PrivateMessage.from_dict(data)
                    try:
                        self._private_message_handler(msg)
                    except Exception as e:
                        print(f"处理私聊消息时出错: {e}")
                
                elif message_type == "group" and self._group_message_handler:
                    msg = GroupMessage.from_dict(data)
                    try:
                        self._group_message_handler(msg)
                    except Exception as e:
                        print(f"处理群聊消息时出错: {e}")
                        
        except json.JSONDecodeError:
            print(f"解析消息失败: {message}")
    
    def _on_ws_error(self, ws, error):
        """处理 WebSocket 错误"""
        print(f"WebSocket 错误: {error}")
    
    def _on_ws_close(self, ws, close_status_code, close_msg):
        """处理 WebSocket 关闭"""
        print(f"WebSocket 连接关闭: {close_status_code} - {close_msg}")
        if self._running:
            print("尝试重新连接...")
            time.sleep(3)
            self.start()
    
    def _on_ws_open(self, ws):
        """处理 WebSocket 连接建立"""
        print("✓ WebSocket 连接已建立，开始接收消息...")
    
    def start(self):
        """启动 WebSocket 连接接收消息"""
        self._running = True
        
        # NapCat WebSocket 统一接口
        ws_url = f"{self.ws_url}/"
        print(f"正在连接 WebSocket: {ws_url}")
        
        headers = []
        if self.token:
            headers.append(f"Authorization: Bearer {self.token}")
        
        self._ws = websocket.WebSocketApp(
            ws_url,
            on_open=self._on_ws_open,
            on_message=self._on_ws_message,
            on_error=self._on_ws_error,
            on_close=self._on_ws_close,
            header=headers
        )
        
        self._ws_thread = threading.Thread(target=self._ws.run_forever)
        self._ws_thread.daemon = True
        self._ws_thread.start()
    
    def stop(self):
        """停止 WebSocket 连接"""
        self._running = False
        if self._ws:
            self._ws.close()
        print("WebSocket 连接已停止")
    
    # ==================== 辅助接口 ====================
    
    def get_login_info(self) -> Dict[str, Any]:
        """获取登录信息"""
        result = self._call_api("get_login_info", {})
        if result.get("status") == "ok":
            return result["data"]
        else:
            raise RuntimeError(f"获取登录信息失败: {result.get('message', '未知错误')}")
    
    def get_friend_list(self) -> List[Dict[str, Any]]:
        """获取好友列表"""
        result = self._call_api("get_friend_list", {})
        if result.get("status") == "ok":
            return result["data"]
        else:
            raise RuntimeError(f"获取好友列表失败: {result.get('message', '未知错误')}")
    
    def get_group_list(self) -> List[Dict[str, Any]]:
        """获取群列表"""
        result = self._call_api("get_group_list", {})
        if result.get("status") == "ok":
            return result["data"]
        else:
            raise RuntimeError(f"获取群列表失败: {result.get('message', '未知错误')}")


if __name__ == "__main__":
    import os
    
    # 测试代码 - 从环境变量获取配置
    token = os.getenv("QQ_BOT_TOKEN", "")
    bot = QQBotAPI(
        base_url="http://127.0.0.1:3000",
        ws_url="ws://127.0.0.1:3000/",
        token=token
    )
    
    # 测试获取登录信息
    print("测试获取登录信息:")
    try:
        info = bot.get_login_info()
        print(f"  账号: {info['user_id']}")
        print(f"  昵称: {info['nickname']}")
    except Exception as e:
        print(f"  错误: {e}")
    
    # 注册消息处理器
    @bot.on_private_message
    def handle_private(msg: PrivateMessage):
        print(f"\n[私聊] {msg.user_id}: {msg.message}")
        # 自动回复
        bot.send_private_msg(msg.user_id, f"收到: {msg.message}")
    
    @bot.on_group_message
    def handle_group(msg: GroupMessage):
        print(f"\n[群聊 {msg.group_id}] {msg.user_id}: {msg.message}")
    
    # 启动 WebSocket 接收消息
    print("\n启动消息接收...")
    bot.start()
    
    # 保持运行
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n停止中...")
        bot.stop()
