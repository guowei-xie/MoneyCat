# -*- coding: utf-8 -*-
"""
技术指标工具：MACD 等。
"""

import pandas as pd


def get_macd(
    bars: pd.DataFrame,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> pd.DataFrame:
    """
    计算 MACD 指标（DIF、DEA、MACD 柱）。

    :param bars: K 线数据，需包含 'close'
    :param fast_period: 快线周期
    :param slow_period: 慢线周期
    :param signal_period: 信号线周期
    :return: 返回新增 dif/dea/macd 列的 DataFrame
    """
    if bars is None or bars.empty:
        return pd.DataFrame()
    df = bars.copy()
    df["ema_fast"] = df["close"].ewm(span=fast_period, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=slow_period, adjust=False).mean()
    df["dif"] = df["ema_fast"] - df["ema_slow"]
    df["dea"] = df["dif"].ewm(span=signal_period, adjust=False).mean()
    df["macd"] = 2 * (df["dif"] - df["dea"])
    df.drop(["ema_fast", "ema_slow"], axis=1, inplace=True)
    return df


def is_macd_top(macd_data: pd.DataFrame) -> bool:
    """
    判断 MACD 柱是否出现“见顶”形态：m1 < m2 < m3 > m4 且四根柱子均大于 0。

    :param macd_data: 包含 macd 列的 DataFrame
    :return: True 表示见顶，否则 False
    """
    if macd_data is None or len(macd_data) < 4 or "macd" not in macd_data.columns:
        return False
    m1, m2, m3, m4 = macd_data["macd"].iloc[-1:-5:-1]
    return m1 < m2 < m3 > m4 and m1 > 0 and m2 > 0 and m3 > 0 and m4 > 0

