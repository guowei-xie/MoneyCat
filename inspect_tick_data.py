# -*- coding: utf-8 -*-
"""
临时脚本：探查 tick 数据结构。

用法：在交易日盘中运行，或非交易日仅查看空结构。
  python inspect_tick_data.py
"""
import json
import sys
from typing import Any, Dict

from utils.path import ensure_project_root_on_path

ensure_project_root_on_path(__file__, levels_up=1)

from broker.data import DataBroker
from configparser import ConfigParser
import os


def _json_safe(obj: Any) -> Any:
    """将对象转为 JSON 可序列化形式（处理 NaN、Inf 等）。"""
    if obj is None:
        return None
    if isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        if obj != obj:  # NaN
            return None
        if obj == float("inf") or obj == float("-inf"):
            return str(obj)
        return round(obj, 6) if abs(obj) < 1e10 else obj
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(x) for x in obj]
    return str(type(obj).__name__)


def main() -> None:
    config = ConfigParser()
    if os.path.isfile("config.ini"):
        config.read("config.ini", encoding="utf-8")

    data = DataBroker()
    if not data.connect():
        print("行情连接失败，请确认 MiniQMT 已启动")
        sys.exit(1)

    # 指定标的探查
    sample_codes = ["600569", "600307"]
    print(f"样本股票: {sample_codes}")

    tick_data = data.get_full_tick(sample_codes)

    print("\n" + "=" * 60)
    print("tick_data 类型:", type(tick_data).__name__)
    print("tick_data 键数量:", len(tick_data) if isinstance(tick_data, dict) else "N/A")
    print("=" * 60)

    if not tick_data:
        print("tick_data 为空（非交易时段可能无数据）")
        return

    for code, tick in tick_data.items():
        print(f"\n--- 股票: {code} ---")
        print(f"  类型: {type(tick).__name__}")
        if isinstance(tick, dict):
            print(f"  字段数: {len(tick)}")
            print("  字段列表:")
            for k, v in sorted(tick.items()):
                v_type = type(v).__name__
                v_preview = repr(v)[:100] if v is not None else "None"
                print(f"    {k}: {v_type} = {v_preview}")
            print("\n  完整 JSON (可序列化):")
            safe = _json_safe(tick)
            print(json.dumps(safe, ensure_ascii=False, indent=2))
        else:
            print(f"  原始值: {tick}")

    print("\n" + "=" * 60)
    print("探查完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
