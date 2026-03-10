# -*- coding: utf-8 -*-
"""
通用工具函数：股票代码规范化、交易日与交易时间判断等。
交易日历使用 akshare 的 tool_trade_date_hist_sina 接口。
"""
import time
from datetime import datetime
from typing import List, Optional, Set

from logging_config import logger
from utils.feishu_notify import send_text as feishu_send_text

# 缓存 akshare 返回的交易日集合（YYYYMMDD），避免重复请求
_akshare_trade_dates: Optional[Set[str]] = None
_trading_day_degraded_notified: bool = False


def _get_trading_dates_akshare() -> Set[str]:
    """
    通过 akshare 获取 A 股交易日集合（YYYYMMDD）。
    使用新浪财经交易日历：tool_trade_date_hist_sina。
    文档：https://akshare.akfamily.xyz/data/tool/tool.html#id1
    """
    global _akshare_trade_dates
    if _akshare_trade_dates is not None:
        return _akshare_trade_dates
    try:
        import akshare as ak
        import pandas as pd
        df = ak.tool_trade_date_hist_sina()
        if df is None or df.empty or "trade_date" not in df.columns:
            _akshare_trade_dates = set()
            return _akshare_trade_dates
        # 统一为 YYYYMMDD 格式
        dates = pd.to_datetime(df["trade_date"], errors="coerce").dropna()
        _akshare_trade_dates = set(dates.dt.strftime("%Y%m%d").tolist())
        return _akshare_trade_dates
    except Exception:
        _akshare_trade_dates = set()
        return _akshare_trade_dates


def add_stock_suffix(stock_code: str) -> str:
    """
    为 6 位股票代码添加交易所后缀。

    :param stock_code: 6 位代码或已带后缀的 code.market
    :return: 如 000001.SZ、600000.SH
    """
    if not stock_code or "." in stock_code:
        return stock_code
    code = stock_code.strip()
    if len(code) != 6 or not code.isdigit():
        return stock_code
    if code.startswith(("00", "30", "15", "16", "18", "12")):
        return f"{code}.SZ"
    if code.startswith(("60", "68", "11")):
        return f"{code}.SH"
    if code.startswith(("83", "43")):
        return f"{code}.BJ"
    return f"{code}.SH"


def add_stock_suffix_list(stock_list: List[str]) -> List[str]:
    """批量添加股票后缀。"""
    return [add_stock_suffix(c) for c in stock_list]


def current_date_str() -> str:
    """当前日期字符串 YYYYMMDD。"""
    return datetime.now().strftime("%Y%m%d")


def is_trading_day(date_str: Optional[str] = None) -> bool:
    """
    判断是否为交易日。使用 akshare 的 tool_trade_date_hist_sina 接口。

    :param date_str: YYYYMMDD，默认当天
    :return: 是否交易日
    """
    dt_str = date_str or current_date_str()
    trade_dates = _get_trading_dates_akshare()
    if trade_dates:
        return dt_str in trade_dates
    # 降级：仅排除周末，并输出明显告警与飞书提醒
    try:
        global _trading_day_degraded_notified
        if not _trading_day_degraded_notified:
            _trading_day_degraded_notified = True
            logger.warning(
                "is_trading_day 使用降级模式：akshare 交易日历不可用，仅按周末判断交易日。"
            )
            try:
                feishu_send_text(
                    "【警告】交易日判断进入降级模式：akshare 交易日历不可用，当前仅按周末判断是否为交易日，请尽快人工核查。"
                )
            except Exception:
                # 飞书通知失败不影响主流程
                pass
        t = datetime.strptime(dt_str, "%Y%m%d")
        return t.weekday() < 5
    except Exception:
        return False


def is_trading_time() -> bool:
    """当前是否在交易时段内（9:30~11:30, 13:00~15:00）。"""
    t = time.strftime("%H:%M:%S", time.localtime())
    if t < "09:30:00" or t > "14:59:59":
        return False
    if "11:30:00" < t < "13:00:00":
        return False
    return True


def is_market_closed() -> bool:
    """是否已收盘（15:00 后）。"""
    return time.strftime("%H:%M:%S", time.localtime()) > "15:00:00"


def get_trading_dates(market: str = "SH", start_time: str = "", end_time: str = "", count: int = -1) -> List[str]:
    """
    获取交易日列表（YYYYMMDD）。使用 akshare 交易日历，market/count 暂未使用。

    :param market: 保留参数，兼容调用
    :param start_time: 起始 YYYYMMDD，空表示不限制
    :param end_time: 结束 YYYYMMDD，空表示不限制
    :param count: 数量，-1 表示不限制
    :return: 交易日列表
    """
    trade_set = _get_trading_dates_akshare()
    if not trade_set:
        return []
    lst = sorted(trade_set)
    if start_time:
        lst = [d for d in lst if d >= start_time]
    if end_time:
        lst = [d for d in lst if d <= end_time]
    if count > 0:
        lst = lst[-count:]
    return lst
