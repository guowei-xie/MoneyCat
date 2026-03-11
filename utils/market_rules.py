# -*- coding: utf-8 -*-
"""
市场规则工具：涨跌停幅度、涨跌停判定、涨跌停价计算等。
"""

from typing import Optional

from utils.common import add_stock_suffix
from xtquant import xtdata


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


def _get_limit_price_by_api(stock_code: str, limit_type: str = "up") -> Optional[float]:
    """
    使用官方接口获取当日涨跌停价，失败时返回 None。

    :param stock_code: 股票代码，可带或不带后缀
    :param limit_type: 'up' 涨停 / 'down' 跌停
    :return: 接口返回的涨跌停价；获取失败时为 None
    """
    try:
        symbol = add_stock_suffix(stock_code)
        detail = xtdata.get_instrument_detail(symbol)
        if not detail:
            return None
        if limit_type == "up":
            price = detail.get("UpStopPrice")
        else:
            price = detail.get("DownStopPrice")
        if price is None:
            return None
        return float(price)
    except Exception:
        return None


def get_limit_price(
    stock_code: str,
    previous_close: float,
    limit_type: str = "up",
    tolerance: float = 0.002,
) -> Optional[float]:
    """
    获取当日涨跌停价，优先使用官方接口，失败时回退到本地计算。

    :param stock_code: 股票代码
    :param previous_close: 前收盘价（用于本地计算回退）
    :param limit_type: 'up' 涨停 / 'down' 跌停
    :param tolerance: 回退计算时的误差修正
    :return: 涨跌停价；若前收盘价非法且接口失败，则为 None
    """
    api_price = _get_limit_price_by_api(stock_code, limit_type=limit_type)
    if api_price is not None:
        return round(api_price, 2)

    if previous_close <= 0:
        return None

    pct = get_limit_percentage(stock_code)
    if limit_type == "up":
        return round(previous_close * (1 + pct) - tolerance, 2)
    if limit_type == "down":
        return round(previous_close * (1 - pct) + tolerance, 2)
    return None

