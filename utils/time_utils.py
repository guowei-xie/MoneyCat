# -*- coding: utf-8 -*-
"""
时间相关工具：统一处理来自 xtquant/pandas 的各类时间格式。
"""

from __future__ import annotations

from typing import Any, Optional


def format_hms(value: Any) -> Optional[str]:
    """
    将“可能包含时间”的任意值格式化为 HH:MM:SS。

    支持常见输入形态：
    - pandas Timestamp / datetime：带 to_pydatetime()
    - 字符串："HH:MM:SS"、"YYYY-MM-DD HH:MM:SS"、"20260310103059"、"103059" 等
    - 数字：20260310103059、103059 等

    :param value: 任意可能包含时间信息的值
    :return: HH:MM:SS；若无法解析则返回 None
    """
    if value is None:
        return None

    # pandas Timestamp / datetime
    try:
        if hasattr(value, "to_pydatetime"):
            dt = value.to_pydatetime()  # type: ignore[call-arg]
            return dt.strftime("%H:%M:%S")
    except Exception:
        pass

    s = str(value).strip()
    if not s:
        return None

    # 已是标准格式，或包含标准格式：取最后一个 HH:MM:SS
    if ":" in s:
        parts = s.split()
        for p in reversed(parts):
            p = p.strip()
            if p.count(":") >= 2 and len(p) >= 8:
                return p[-8:]
        # 兜底：直接取末尾 8 位
        if len(s) >= 8 and s.count(":") >= 2:
            return s[-8:]

    # 提取数字，取最后 6 位为 HHMMSS
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) < 6:
        return None
    hms = digits[-6:]
    return f"{hms[0:2]}:{hms[2:4]}:{hms[4:6]}"


def get_last_bar_hms(bars: Any) -> Optional[str]:
    """
    从分时 K 线（DataFrame）中解析最后一根 K 对应的时间（HH:MM:SS）。

    约定：bars 需具备 index 与可通过 index[-1] 访问最后一个索引值。

    :param bars: pandas DataFrame 或兼容对象
    :return: HH:MM:SS；解析失败返回 None
    """
    if bars is None or getattr(bars, "empty", True):
        return None
    try:
        idx = bars.index[-1]
    except Exception:
        return None
    return format_hms(idx)

