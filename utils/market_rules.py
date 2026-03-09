# -*- coding: utf-8 -*-
"""
市场规则工具：涨跌停幅度、涨跌停判定、涨跌停价计算等。
"""

from typing import Optional

from utils.common import add_stock_suffix


def get_limit_percentage(stock_code: str) -> float:
    """
    根据股票所属板块估算涨跌停幅度。

    :param stock_code: 股票代码，可带或不带后缀
    :return: 涨跌停幅度（如 0.1 / 0.2 / 0.3）
    """
    symbol = add_stock_suffix(stock_code)
    prefix = symbol.split(".")[0]
    if prefix.startswith(("688", "689")):
        return 0.20  # 科创板
    if prefix.startswith("30"):
        return 0.20  # 创业板
    if prefix.startswith(("83", "43")) or symbol.endswith(".BJ"):
        return 0.30  # 北交所
    return 0.10  # 其余视作主板 10%


def is_limit(
    stock_code: str,
    price: float,
    previous_close: float,
    limit_type: str = "up",
    tolerance: float = 0.002,
) -> bool:
    """
    判断是否涨跌停（简化版：按板块估算涨跌停幅度，并保留误差容忍）。

    :param stock_code: 股票代码
    :param price: 当前价格
    :param previous_close: 前收盘价
    :param limit_type: 'up' 涨停 / 'down' 跌停
    :param tolerance: 误差容忍度
    """
    if previous_close <= 0:
        return False
    pct = get_limit_percentage(stock_code)
    if limit_type == "up":
        limit_price = previous_close * (1 + pct - tolerance)
        return price >= limit_price
    if limit_type == "down":
        limit_price = previous_close * (1 - pct + tolerance)
        return price <= limit_price
    return False


def get_limit_price(
    stock_code: str,
    previous_close: float,
    limit_type: str = "up",
    tolerance: float = 0.002,
) -> Optional[float]:
    """
    计算当日理论涨跌停价（用于炸板判断等）。

    :param stock_code: 股票代码
    :param previous_close: 前收盘价
    :param limit_type: 'up' 涨停 / 'down' 跌停
    :param tolerance: 再减去/加上的误差
    """
    if previous_close <= 0:
        return None
    pct = get_limit_percentage(stock_code)
    if limit_type == "up":
        return round(previous_close * (1 + pct) - tolerance, 2)
    if limit_type == "down":
        return round(previous_close * (1 - pct) + tolerance, 2)
    return None

