"""
DeepSeek API 调用模块
用于调用 DeepSeek 官方的 deepseek-chat 模型
"""

import os
import requests
from typing import Optional


class DeepSeekAPI:
    """DeepSeek API 客户端"""
    
    BASE_URL = "https://api.deepseek.com/v1/chat/completions"
    DEFAULT_MODEL = "deepseek-chat"
    
    def __init__(self, api_key: Optional[str] = None):
        """
        初始化 DeepSeek API 客户端
        
        Args:
            api_key: DeepSeek API 密钥，如果不提供则从环境变量 DEEPSEEK_API_KEY 获取
        """
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        if not self.api_key:
            raise ValueError("API key 不能为空，请提供 api_key 参数或设置 DEEPSEEK_API_KEY 环境变量")
        
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
    
    def chat(self, user_msg: str, system_msg: Optional[str] = None, model: str = None) -> str:
        """
        调用 DeepSeek Chat API 进行对话
        
        Args:
            user_msg: 用户消息
            system_msg: 系统提示词（可选）
            model: 模型名称，默认为 deepseek-chat
        
        Returns:
            模型的回复消息
        
        Raises:
            requests.RequestException: 当 API 请求失败时抛出
        """
        messages = []
        
        # 如果有系统提示词，添加到消息列表
        if system_msg:
            messages.append({
                "role": "system",
                "content": system_msg
            })
        
        # 添加用户消息
        messages.append({
            "role": "user",
            "content": user_msg
        })
        
        payload = {
            "model": model or self.DEFAULT_MODEL,
            "messages": messages,
            "stream": False
        }
        
        try:
            response = requests.post(
                self.BASE_URL,
                headers=self.headers,
                json=payload,
                timeout=60
            )
            response.raise_for_status()
            
            result = response.json()
            return result["choices"][0]["message"]["content"]
            
        except requests.RequestException as e:
            raise requests.RequestException(f"DeepSeek API 请求失败: {e}")
        except (KeyError, IndexError) as e:
            raise ValueError(f"解析 DeepSeek API 响应失败: {e}")
    
    def chat_stream(self, user_msg: str, system_msg: Optional[str] = None, model: str = None):
        """
        流式调用 DeepSeek Chat API 进行对话
        
        Args:
            user_msg: 用户消息
            system_msg: 系统提示词（可选）
            model: 模型名称，默认为 deepseek-chat
        
        Yields:
            模型回复的消息片段
        """
        messages = []
        
        if system_msg:
            messages.append({
                "role": "system",
                "content": system_msg
            })
        
        messages.append({
            "role": "user",
            "content": user_msg
        })
        
        payload = {
            "model": model or self.DEFAULT_MODEL,
            "messages": messages,
            "stream": True
        }
        
        try:
            response = requests.post(
                self.BASE_URL,
                headers=self.headers,
                json=payload,
                stream=True,
                timeout=60
            )
            response.raise_for_status()
            
            for line in response.iter_lines():
                if line:
                    line = line.decode('utf-8')
                    if line.startswith('data: '):
                        data = line[6:]
                        if data == '[DONE]':
                            break
                        import json
                        try:
                            chunk = json.loads(data)
                            delta = chunk.get('choices', [{}])[0].get('delta', {})
                            content = delta.get('content', '')
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue
                            
        except requests.RequestException as e:
            raise requests.RequestException(f"DeepSeek API 流式请求失败: {e}")


# 便捷函数
def get_deepseek_response(user_msg: str, system_msg: Optional[str] = None, api_key: Optional[str] = None) -> str:
    """
    便捷函数：快速获取 DeepSeek 回复
    
    Args:
        user_msg: 用户消息
        system_msg: 系统提示词（可选）
        api_key: API 密钥（可选，默认从环境变量获取）
    
    Returns:
        模型的回复消息
    """
    client = DeepSeekAPI(api_key=api_key)
    return client.chat(user_msg=user_msg, system_msg=system_msg)


if __name__ == "__main__":
    # 测试代码
    import os
    
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        print("请设置 DEEPSEEK_API_KEY 环境变量")
        exit(1)
    
    client = DeepSeekAPI(api_key=api_key)
    
    # 测试普通对话
    print("测试普通对话:")
    try:
        response = client.chat(
            user_msg="你好，请介绍一下自己",
            system_msg="你是一个有用的助手"
        )
        print(f"回复: {response}")
    except Exception as e:
        print(f"错误: {e}")
    
    # 测试流式对话
    print("\n测试流式对话:")
    try:
        print("回复: ", end="", flush=True)
        for chunk in client.chat_stream(
            user_msg="你好",
            system_msg="你是一个有用的助手"
        ):
            print(chunk, end="", flush=True)
        print()
    except Exception as e:
        print(f"错误: {e}")
