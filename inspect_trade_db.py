#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
独立数据库浏览脚本：用于快速查看本地 SQLite 交易记录库的基础情况。

使用方式（在项目根目录下执行）：
    python inspect_trade_db.py
    python inspect_trade_db.py --date 20260312
    python inspect_trade_db.py --limit 50
"""

import argparse
import os
import sqlite3
from configparser import ConfigParser
from datetime import datetime

from logging_config import logger, setup_logger
from utils.path import ensure_project_root_on_path


def _ensure_project_root() -> str:
    """
    确保项目根目录已加入 sys.path，返回项目根路径。
    """
    return ensure_project_root_on_path(__file__, levels_up=1)


def load_config(config_path: str = "config.ini") -> ConfigParser:
    """
    加载配置文件，仅用于解析数据库与日志相关配置。
    """
    cfg = ConfigParser()
    if os.path.isfile(config_path):
        cfg.read(config_path, encoding="utf-8")
    else:
        logger.warning("未找到配置文件 %s，将使用默认设置", config_path)
    return cfg


def resolve_db_path(config: ConfigParser, default_path: str = "trade_records.db") -> str:
    """
    根据配置解析 SQLite 数据库路径（与主程序保持一致）。"""
    db_path = default_path
    if isinstance(config, ConfigParser) and config.has_section("DB"):
        db_path = config.get("DB", "TRADE_DB_PATH", fallback=db_path)
    return db_path


def open_connection(db_path: str) -> sqlite3.Connection:
    """
    打开 SQLite 连接并设置基础参数。
    """
    abs_path = os.path.abspath(db_path)
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"未找到交易记录数据库文件: {abs_path}")
    conn = sqlite3.connect(abs_path)
    conn.row_factory = sqlite3.Row
    return conn


def format_yyyymmdd(date_str: str) -> str:
    """
    将形如 YYYYMMDD 的日期字符串转换为 YYYY-MM-DD 格式。
    """
    date_str = (date_str or "").strip()
    if len(date_str) != 8 or not date_str.isdigit():
        raise ValueError(f"非法日期格式: {date_str}（期望形如 20260312）")
    return f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"


def print_basic_info(conn: sqlite3.Connection) -> None:
    """
    打印 trade_records 表的基础信息：总行数、最近一条记录时间等。
    """
    cur = conn.cursor()
    cur.execute("SELECT COUNT(1) AS cnt FROM trade_records;")
    row = cur.fetchone()
    total = int(row["cnt"] if row and row["cnt"] is not None else 0)

    cur.execute(
        "SELECT event_time, event_type FROM trade_records ORDER BY id DESC LIMIT 1;"
    )
    last = cur.fetchone()
    last_time = last["event_time"] if last else None
    last_type = last["event_type"] if last else None

    logger.info("trade_records 总行数: %d", total)
    if last_time:
        logger.info("最近一条记录: %s (event_type=%s)", last_time, last_type)
    else:
        logger.info("数据库中暂未找到任何记录。")


def print_event_type_summary(
    conn: sqlite3.Connection,
    date_prefix: str | None = None,
) -> None:
    """
    按事件类型与方向统计记录数量及金额。

    :param conn: 已打开的 SQLite 连接
    :param date_prefix: 可选日期前缀（YYYY-MM-DD），用于按 event_time 过滤
    """
    params = []
    where = "1=1"
    if date_prefix:
        where += " AND event_time LIKE ?"
        params.append(f"{date_prefix}%")

    sql = f"""
    SELECT event_type,
           direction,
           COUNT(1) AS cnt,
           COALESCE(SUM(COALESCE(amount, 0)), 0) AS amt
    FROM trade_records
    WHERE {where}
    GROUP BY event_type, direction
    ORDER BY event_type, direction;
    """
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()

    if not rows:
        if date_prefix:
            logger.info("在指定日期 %s 未查询到任何记录。", date_prefix)
        else:
            logger.info("未查询到任何记录。")
        return

    logger.info("=== 按事件类型统计 ===")
    for r in rows:
        et = str(r["event_type"] or "")
        dr = str(r["direction"] or "")
        cnt = int(r["cnt"] or 0)
        amt = float(r["amt"] or 0.0)
        if dr:
            logger.info("event=%s, direction=%s, count=%d, amount=%.2f", et, dr, cnt, amt)
        else:
            logger.info("event=%s, count=%d, amount=%.2f", et, cnt, amt)


def print_recent_trades(
    conn: sqlite3.Connection,
    limit: int = 20,
    date_prefix: str | None = None,
) -> None:
    """
    打印最近若干条成交记录（TRADE），可按日期过滤。

    :param conn: 已打开的 SQLite 连接
    :param limit: 显示的记录条数上限
    :param date_prefix: 可选日期前缀（YYYY-MM-DD），用于按 event_time 过滤
    """
    params = []
    where = "event_type='TRADE'"
    if date_prefix:
        where += " AND event_time LIKE ?"
        params.append(f"{date_prefix}%")

    sql = f"""
    SELECT event_time,
           stock_code,
           direction,
           volume,
           price,
           amount,
           order_id,
           remark,
           strategy_name
    FROM trade_records
    WHERE {where}
    ORDER BY id DESC
    LIMIT ?;
    """
    params.append(int(limit))

    cur = conn.cursor()
    cur.execute(sql, params)
    rows = cur.fetchall()

    logger.info("=== 最近成交明细（最多 %d 条） ===", limit)
    if not rows:
        logger.info("暂无成交记录。")
        return

    for r in rows:
        strat = str(r["strategy_name"] or "")
        logger.info(
            "%s %s %s vol=%s price=%.3f amt=%.2f order_id=%s strat=%r remark=%s",
            r["event_time"],
            r["stock_code"],
            r["direction"],
            r["volume"],
            float(r["price"] or 0.0),
            float(r["amount"] or 0.0),
            r["order_id"],
            strat[:50] if strat else "(empty)",
            (r["remark"] or "")[:80],
        )


def parse_args() -> argparse.Namespace:
    """
    解析命令行参数。
    """
    parser = argparse.ArgumentParser(
        description="浏览 MoneyCat 本地 SQLite 交易记录数据库的基础数据情况。",
    )
    parser.add_argument(
        "--config",
        dest="config_path",
        default="config.ini",
        help="配置文件路径（默认: config.ini）",
    )
    parser.add_argument(
        "--date",
        dest="date_yyyymmdd",
        default="",
        help="按指定交易日过滤，格式为 YYYYMMDD，例如 20260312",
    )
    parser.add_argument(
        "--limit",
        dest="limit",
        type=int,
        default=20,
        help="显示最近成交记录条数上限（默认: 20）",
    )
    return parser.parse_args()


def main() -> None:
    """
    程序入口：读取配置 → 打开数据库 → 打印汇总与样例数据。
    """
    _ensure_project_root()
    args = parse_args()

    config = load_config(args.config_path)
    log_level = config.get("LOG", "LEVEL", fallback="INFO")
    setup_logger(level=log_level)

    db_path = resolve_db_path(config)
    logger.info("使用的交易记录数据库路径: %s", os.path.abspath(db_path))

    date_prefix = None
    if args.date_yyyymmdd:
        try:
            date_prefix = format_yyyymmdd(args.date_yyyymmdd)
        except ValueError as exc:
            logger.error("日期参数错误: %s", exc)
            return

    try:
        conn = open_connection(db_path)
    except Exception as exc:
        logger.error("打开 SQLite 数据库失败: %s", exc)
        return

    with conn:
        print_basic_info(conn)
        print_event_type_summary(conn, date_prefix=date_prefix)
        print_recent_trades(conn, limit=args.limit, date_prefix=date_prefix)


if __name__ == "__main__":
    main()

