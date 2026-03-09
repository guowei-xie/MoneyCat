# -*- coding: utf-8 -*-
"""
股票池工具：构建常用股票池（如沪深A股主板）。
"""

from typing import Iterable, List

from utils.common import add_stock_suffix


def get_stock_market_type(stock_code: str) -> str:
    """
    根据股票代码判断所属市场类型。

    :param stock_code: 股票代码，可带后缀如 .SH/.SZ/.BJ，也可不带
    :return: 市场类型：'主板'/'创业板'/'科创板'/'北交所'
    """
    symbol = add_stock_suffix(stock_code)
    prefix = symbol.split(".")[0]
    if prefix.startswith(("688", "689")):
        return "科创板"
    if prefix.startswith("30"):
        return "创业板"
    if prefix.startswith(("83", "43")) or symbol.endswith(".BJ"):
        return "北交所"
    return "主板"


def is_st_name(stock_name: str) -> bool:
    """
    判断股票名称是否为 ST/*ST。

    :param stock_name: 股票名称
    """
    if not stock_name:
        return False
    name = str(stock_name)
    return ("*ST" in name) or ("ST" in name)


def is_delisting_name(stock_name: str) -> bool:
    """
    判断股票名称是否为“退市”相关标的（简化判定：名称含“退市”）。

    :param stock_name: 股票名称
    """
    if not stock_name:
        return False
    return "退市" in str(stock_name)


def filter_main_board(stock_list: Iterable[str]) -> List[str]:
    """
    过滤出主板股票代码列表。

    :param stock_list: 输入股票代码序列
    :return: 仅主板股票列表（带后缀）
    """
    result: List[str] = []
    for s in stock_list:
        if not s:
            continue
        code = add_stock_suffix(str(s).strip())
        if get_stock_market_type(code) == "主板":
            result.append(code)
    return result

