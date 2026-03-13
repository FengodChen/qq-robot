#!/usr/bin/env python3
"""QQ Bot v2 启动脚本。

使用新的结构化架构运行机器人。
"""

import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qq_bot.cli import main

if __name__ == "__main__":
    sys.exit(main())
