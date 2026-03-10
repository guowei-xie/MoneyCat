# -*- coding: utf-8 -*-
"""
路径引导工具：用于脚本方式运行时确保项目根目录在 sys.path 中。

说明：
- 这是为了兼容直接 `python main.py` 或某些 IDE/运行器在不同 cwd 下启动的情况；
- 若你用 `python -m MoneyCat` 或安装为包，则通常不需要此工具。
"""

import os
import sys


def ensure_project_root_on_path(anchor_file: str, levels_up: int = 2) -> str:
    """
    将 anchor_file 向上 levels_up 级目录作为“项目根”加入 sys.path（若尚未存在）。

    :param anchor_file: 调用方文件的 __file__
    :param levels_up: 向上回退层数（broker/* 默认 2 层到项目根）
    :return: 计算出的项目根路径
    """
    root = os.path.abspath(anchor_file)
    for _ in range(max(int(levels_up), 0)):
        root = os.path.dirname(root)
    if root and root not in sys.path:
        sys.path.insert(0, root)
    return root

