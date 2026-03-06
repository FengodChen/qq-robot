#!/usr/bin/env python3
"""
配置加载模块
仅从 config.yaml 加载配置
"""

import os
import yaml
from typing import Dict, Optional


def load_config(config_path: str = "config.yaml") -> Dict:
    """
    从 YAML 配置文件加载配置
    
    Args:
        config_path: YAML 配置文件路径
        
    Returns:
        配置字典
    """
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                yaml_config = yaml.safe_load(f)
            if yaml_config:
                print(f"[*] 已加载配置文件: {config_path}")
                return yaml_config
        except Exception as e:
            print(f"[!] 加载配置文件失败: {e}")
    else:
        print(f"[!] 配置文件不存在: {config_path}")
    return {}


def get_config_value(key: str, config_path: str = "config.yaml") -> Optional[str]:
    """
    获取单个配置值
    
    Args:
        key: 配置键名
        config_path: 配置文件路径
        
    Returns:
        配置值
    """
    config = load_config(config_path)
    return config.get(key)


if __name__ == "__main__":
    # 测试配置加载
    print("=" * 40)
    print("配置加载测试")
    print("=" * 40)
    
    cfg = load_config()
    for key, value in cfg.items():
        if value:
            # 隐藏敏感信息
            if 'key' in key or 'token' in key:
                display = str(value)[:8] + "..." + str(value)[-4:] if len(str(value)) > 12 else "***"
            else:
                display = value
            print(f"{key}: {display}")
        else:
            print(f"{key}: (未设置)")
