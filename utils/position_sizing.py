# -*- coding: utf-8 -*-
"""
仓位/数量工具：将交易数量转换为安全可执行的整数倍数量等。
"""

import math


def convert_to_safe_sell_volume(plan_volume: int, available_volume: int) -> int:
    """
    将计划卖出数量转换为“100 股整数倍且不超过可用”的安全数量。

    :param plan_volume: 计划卖出股数
    :param available_volume: 实际可用股数
    :return: 安全的卖出股数（100 的整数倍）
    """
    if plan_volume <= 0 or available_volume <= 0:
        return 0
    plan_volume = math.ceil(plan_volume / 100) * 100
    return min(plan_volume, available_volume)

