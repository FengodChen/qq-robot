"""OneBot 11 协议适配器。

基于 OneBot 11 协议实现的 WebSocket 适配器，使用正向 WS 发送消息，反向 WS 接收消息。
"""

import asyncio
import json
import time
from typing import Any, Optional

import websockets
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK, ConnectionClosedError

from qq_bot.adapters.base import Adapter, ConnectionState
from qq_bot.core.events import MessageEvent
from qq_bot.core.exceptions import AdapterError


class OneBot11Adapter(Adapter):
    """OneBot 11 协议适配器。
    
    使用正向 WebSocket 连接发送 API 请求，反向 WebSocket 连接接收事件。
    支持自动重连和消息解析。
    
    Attributes:
        config: 配置对象，需包含 napcat_ws_url, listen_host, listen_port, token 等字段。
        state: 连接状态。
    
    Example:
        >>> from qq_bot.adapters.onebot11 import OneBot11Adapter
        >>> config = {
        ...     "napcat_ws_url": "ws://127.0.0.1:3000",
        ...     "listen_host": "0.0.0.0",
        ...     "listen_port": 3001,
        ...     "token": "your_token"
        ... }
        >>> adapter = OneBot11Adapter(config)
        >>> await adapter.start()
    """
    
    def __init__(self, config: Any):
        """初始化适配器。
        
        Args:
            config: 配置对象，需包含以下字段：
                - napcat_ws_url: NapCat 正向 WebSocket 地址
                - listen_host: 反向 WebSocket 监听地址
                - listen_port: 反向 WebSocket 监听端口
                - token: 可选的访问令牌
        """
        super().__init__(config)
        self.state = ConnectionState()
        
        # WebSocket 连接
        self._send_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._recv_ws: Optional[websockets.WebSocketServerProtocol] = None
        self._server: Optional[websockets.WebSocketServer] = None
        
        # 请求响应映射
        self._pending_responses: dict[str, asyncio.Future[dict]] = {}
        
        # 控制标志
        self._running: bool = False
        self._reconnect_delay: float = 3.0
        self._max_reconnect_delay: float = 60.0
        
        # 任务引用
        self._tasks: set[asyncio.Task] = set()
        self._server_task: Optional[asyncio.Task] = None
    
    def _extract_text_from_message(self, message: Any) -> str:
        """从消息段数组中提取纯文本。
        
        Args:
            message: 消息内容，可以是字符串或消息段列表。
            
        Returns:
            提取的纯文本内容。
        """
        if isinstance(message, str):
            return message
        if isinstance(message, list):
            texts = []
            for seg in message:
                if seg.get("type") == "text":
                    texts.append(seg.get("data", {}).get("text", ""))
            return "".join(texts)
        return str(message)
    
    def _parse_message_event(self, data: dict[str, Any]) -> Optional[MessageEvent]:
        """解析 OneBot 消息事件为 MessageEvent。
        
        Args:
            data: OneBot 原始消息数据。
            
        Returns:
            解析后的 MessageEvent，如果不是消息事件则返回 None。
        """
        post_type = data.get("post_type")
        if post_type != "message":
            return None
        
        message_type = data.get("message_type")
        if message_type not in ("private", "group"):
            return None
        
        message = data.get("message", [])
        content = self._extract_text_from_message(message)
        
        return MessageEvent(
            message_type=message_type,
            user_id=data.get("user_id", 0),
            group_id=data.get("group_id", 0) if message_type == "group" else 0,
            content=content,
            raw_message=data.get("raw_message", ""),
            sender=data.get("sender", {}),
            message_id=data.get("message_id", 0),
            timestamp=data.get("time", time.time())
        )
    
    async def _connect_sender(self) -> None:
        """建立正向 WebSocket 连接用于发送消息。
        
        Raises:
            AdapterError: 连接失败时抛出。
        """
        ws_url = self._get_config("napcat_ws_url", "ws://127.0.0.1:3000")
        token = self._get_config("token", None)
        
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        
        try:
            self._send_ws = await websockets.connect(
                ws_url,
                additional_headers=headers if headers else None
            )
            print(f"✓ 已连接到 NapCat 正向 WS: {ws_url}")
            
            # 启动响应处理任务
            task = asyncio.create_task(self._handle_send_responses())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            
        except Exception as e:
            raise AdapterError(f"连接正向 WS 失败: {e}", endpoint="connect_sender")
    
    async def _handle_send_responses(self) -> None:
        """处理正向 WebSocket 的响应消息。"""
        if not self._send_ws:
            return
        
        try:
            async for message in self._send_ws:
                try:
                    data = json.loads(message)
                    echo = data.get("echo")
                    if echo and echo in self._pending_responses:
                        future = self._pending_responses.pop(echo)
                        if not future.done():
                            future.set_result(data)
                except json.JSONDecodeError:
                    print(f"[!] 解析响应消息失败: {message[:100]}")
                except Exception as e:
                    print(f"[!] 处理响应消息出错: {e}")
        except ConnectionClosed:
            pass
        except Exception as e:
            print(f"[!] 响应处理循环出错: {e}")
    
    def _is_send_ws_connected(self) -> bool:
        """检查正向 WebSocket 是否已连接。
        
        Returns:
            连接是否正常。
        """
        if not self._send_ws:
            return False
        # websockets 16+ 使用 state 属性
        return self._send_ws.state.name == "OPEN"
    
    async def _send_api_request(self, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """发送 API 请求并等待响应。
        
        如果连接断开，会尝试自动重连一次。
        
        Args:
            action: API 动作名称。
            params: 请求参数。
            
        Returns:
            API 响应数据。
            
        Raises:
            AdapterError: 请求失败时抛出。
        """
        # 检查连接状态，如果断开尝试重连
        if not self._is_send_ws_connected():
            print(f"[*] 正向 WS 未连接，尝试重连...")
            try:
                await self._connect_sender()
            except AdapterError:
                raise AdapterError("正向 WebSocket 连接失败，无法发送消息", endpoint=action)
        
        echo = f"req_{int(time.time() * 1000)}_{id(asyncio.current_task())}"
        request = {"action": action, "params": params, "echo": echo}
        
        future = asyncio.get_event_loop().create_future()
        self._pending_responses[echo] = future
        
        try:
            await self._send_ws.send(json.dumps(request))
            return await asyncio.wait_for(future, timeout=10.0)
        except asyncio.TimeoutError:
            self._pending_responses.pop(echo, None)
            raise AdapterError(f"API 请求超时: {action}", endpoint=action)
        except ConnectionClosed:
            self._pending_responses.pop(echo, None)
            # 标记连接已断开，下次会自动重连
            self._send_ws = None
            raise AdapterError("WebSocket 连接已关闭，请重试", endpoint=action)
        except Exception as e:
            self._pending_responses.pop(echo, None)
            raise AdapterError(f"API 请求失败: {e}", endpoint=action)
    
    async def _handle_incoming(self, websocket: websockets.WebSocketServerProtocol) -> None:
        """处理反向 WebSocket 连接。
        
        Args:
            websocket: WebSocket 服务器协议对象。
        """
        print(f"\n[*] NapCat 反向 WS 已连接: {websocket.remote_address}")
        self._recv_ws = websocket
        
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    
                    # 更新 self_id
                    if data.get("self_id") and not self.state.self_id:
                        self.state.self_id = data.get("self_id")
                        print(f"[*] 机器人 QQ: {self.state.self_id}")
                        self.state.connected = True
                    
                    # 解析并处理消息事件
                    event = self._parse_message_event(data)
                    if event and self._message_handler:
                        asyncio.create_task(self._message_handler(event))
                        
                except json.JSONDecodeError:
                    print(f"[!] 解析消息失败: {message[:100]}")
                except Exception as e:
                    print(f"[!] 处理消息出错: {e}")
                    
        except ConnectionClosedOK:
            print("[*] NapCat 反向 WS 连接正常关闭")
        except ConnectionClosedError as e:
            print(f"[!] NapCat 反向 WS 连接异常关闭: {e}")
        except Exception as e:
            print(f"[!] 反向 WS 处理出错: {e}")
        finally:
            self._recv_ws = None
            if self.state.self_id:
                self.state.connected = False
    
    def _get_config(self, key: str, default: Any = None) -> Any:
        """获取配置值，支持嵌套配置。
        
        Args:
            key: 配置键名。
            default: 默认值。
            
        Returns:
            配置值。
        """
        # 直接属性访问
        if hasattr(self.config, key):
            return getattr(self.config, key)
        
        # 嵌套配置访问 (config.onebot.xxx)
        if hasattr(self.config, 'onebot'):
            onebot_config = self.config.onebot
            if hasattr(onebot_config, key):
                return getattr(onebot_config, key)
        
        return default
    
    async def _start_server(self) -> None:
        """启动反向 WebSocket 服务器。"""
        host = self._get_config("listen_host", "0.0.0.0")
        port = self._get_config("listen_port", 3001)
        
        self._server = await websockets.serve(
            self._handle_incoming,
            host,
            port
        )
        print(f"[*] 反向 WS 服务器已启动: ws://{host}:{port}/")
    
    async def _reconnect_loop(self) -> None:
        """自动重连循环。"""
        while self._running:
            try:
                if not self._is_send_ws_connected():
                    self.state.reconnect_count += 1
                    delay = min(
                        self._reconnect_delay * (1.5 ** (self.state.reconnect_count - 1)),
                        self._max_reconnect_delay
                    )
                    print(f"[*] {delay:.1f}秒后尝试重连... (第{self.state.reconnect_count}次)")
                    await asyncio.sleep(delay)
                    
                    try:
                        await self._connect_sender()
                        self.state.reconnect_count = 0
                    except AdapterError:
                        continue
                else:
                    await asyncio.sleep(5.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[!] 重连循环出错: {e}")
                await asyncio.sleep(self._reconnect_delay)
    
    async def start(self) -> None:
        """启动适配器。
        
        启动正向和反向 WebSocket 连接，开始接收消息。
        
        Raises:
            AdapterError: 启动失败时抛出。
        """
        self._running = True
        
        print("[*] 启动 OneBot 11 适配器...")
        
        # 连接正向 WS
        try:
            await self._connect_sender()
        except AdapterError as e:
            print(f"[!] 初始连接失败: {e}")
            # 启动重连任务
            reconnect_task = asyncio.create_task(self._reconnect_loop())
            self._tasks.add(reconnect_task)
            reconnect_task.add_done_callback(self._tasks.discard)
        
        # 启动反向 WS 服务器
        try:
            await self._start_server()
        except Exception as e:
            raise AdapterError(f"启动反向 WS 服务器失败: {e}")
        
        # 如果没有启动重连任务，启动一个状态监控任务
        if not any(t.get_name() == "_reconnect_loop" for t in self._tasks):
            reconnect_task = asyncio.create_task(self._reconnect_loop())
            reconnect_task.set_name("_reconnect_loop")
            self._tasks.add(reconnect_task)
            reconnect_task.add_done_callback(self._tasks.discard)
    
    async def stop(self) -> None:
        """停止适配器。
        
        关闭所有 WebSocket 连接并清理资源。
        """
        print("[*] 停止 OneBot 11 适配器...")
        self._running = False
        
        # 取消所有任务
        for task in self._tasks:
            task.cancel()
        
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        
        # 关闭正向 WS
        if self._send_ws:
            try:
                await self._send_ws.close()
            except Exception as e:
                print(f"[!] 关闭正向 WS 出错: {e}")
            self._send_ws = None
        
        # 关闭反向 WS 服务器
        if self._server:
            try:
                self._server.close()
                await self._server.wait_closed()
            except Exception as e:
                print(f"[!] 关闭反向 WS 服务器出错: {e}")
            self._server = None
        
        # 清理待处理的响应
        for future in self._pending_responses.values():
            if not future.done():
                future.cancel()
        self._pending_responses.clear()
        
        self.state.connected = False
        print("[*] OneBot 11 适配器已停止")
    
    async def send_private_message(self, user_id: int, content: str) -> Optional[int]:
        """发送私聊消息。
        
        Args:
            user_id: 目标用户 QQ 号。
            content: 消息内容。
            
        Returns:
            发送成功的消息 ID，失败返回 None。
        """
        try:
            message_segments = [{"type": "text", "data": {"text": content}}]
            result = await self._send_api_request(
                "send_private_msg",
                {"user_id": user_id, "message": message_segments}
            )
            
            if result.get("status") == "ok":
                print(f"[发送私聊] -> {user_id}: {content[:50]}...")
                return result.get("data", {}).get("message_id")
            else:
                print(f"[!] 发送私聊消息失败: {result.get('message', '未知错误')}")
                return None
                
        except AdapterError as e:
            print(f"[!] 发送私聊消息失败: {e}")
            return None
    
    async def send_group_message(
        self,
        group_id: int,
        content: str,
        at_user: Optional[int] = None,
        reply_to: Optional[int] = None
    ) -> Optional[int]:
        """发送群消息。
        
        Args:
            group_id: 目标群号。
            content: 消息内容。
            at_user: @ 的目标用户 QQ 号（可选）。
            reply_to: 回复的消息 ID（可选）。
            
        Returns:
            发送成功的消息 ID，失败返回 None。
        """
        try:
            message_segments = []
            
            # 添加回复
            if reply_to:
                message_segments.append({"type": "reply", "data": {"id": str(reply_to)}})
            
            # 添加 @
            if at_user:
                message_segments.append({"type": "at", "data": {"qq": str(at_user)}})
                message_segments.append({"type": "text", "data": {"text": " "}})
            
            # 添加文本内容
            message_segments.append({"type": "text", "data": {"text": content}})
            
            result = await self._send_api_request(
                "send_group_msg",
                {"group_id": group_id, "message": message_segments}
            )
            
            if result.get("status") == "ok":
                print(f"[发送群聊] -> 群{group_id}: {content[:50]}...")
                return result.get("data", {}).get("message_id")
            else:
                print(f"[!] 发送群消息失败: {result.get('message', '未知错误')}")
                return None
                
        except AdapterError as e:
            print(f"[!] 发送群消息失败: {e}")
            return None
    
    async def get_group_member_info(self, group_id: int, user_id: int) -> dict[str, Any]:
        """获取群成员信息。
        
        Args:
            group_id: 群号。
            user_id: 用户 QQ 号。
            
        Returns:
            成员信息字典，包含 nickname, card, sex 等字段。
        """
        try:
            result = await self._send_api_request(
                "get_group_member_info",
                {"group_id": group_id, "user_id": user_id, "no_cache": True}
            )
            
            if result.get("status") == "ok":
                return result.get("data", {})
            else:
                print(f"[!] 获取群成员信息失败: {result.get('message', '未知错误')}")
                return {}
                
        except AdapterError as e:
            print(f"[!] 获取群成员信息失败: {e}")
            return {}
    
    async def get_stranger_info(self, user_id: int) -> dict[str, Any]:
        """获取陌生人信息。
        
        Args:
            user_id: 用户 QQ 号。
            
        Returns:
            用户信息字典，包含 nickname, sex, age 等字段。
        """
        try:
            result = await self._send_api_request(
                "get_stranger_info",
                {"user_id": user_id, "no_cache": True}
            )
            
            if result.get("status") == "ok":
                return result.get("data", {})
            else:
                print(f"[!] 获取陌生人信息失败: {result.get('message', '未知错误')}")
                return {}
                
        except AdapterError as e:
            print(f"[!] 获取陌生人信息失败: {e}")
            return {}
    
    async def delete_message(self, message_id: int) -> bool:
        """撤回消息。
        
        Args:
            message_id: 要撤回的消息 ID。
            
        Returns:
            是否撤回成功。
        """
        try:
            result = await self._send_api_request(
                "delete_msg",
                {"message_id": message_id}
            )
            
            if result.get("status") == "ok":
                print(f"[撤回消息] -> 消息ID: {message_id}")
                return True
            else:
                print(f"[!] 撤回消息失败: {result.get('message', '未知错误')}")
                return False
                
        except AdapterError as e:
            print(f"[!] 撤回消息失败: {e}")
            return False
