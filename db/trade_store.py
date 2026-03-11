# -*- coding: utf-8 -*-
"""
本地交易记录存储：使用 SQLite 记录所有订单与成交事件。

设计要点：
- 通过 init_trade_store 在程序启动时初始化，全局复用单一连接。
- TradeBroker 下单 / 撤单、成交回调等模块按需调用记录函数。
- 若未初始化（如单元测试场景），记录函数自动降级为无操作。
"""
import os
import sqlite3
import threading
from datetime import datetime
from typing import Optional, Any, Dict, List, Tuple

from logging_config import logger

_DB_LOCK = threading.Lock()
_STORE: "Optional[SqliteTradeStore]" = None


class SqliteTradeStore:
    """基于 SQLite 的交易记录存储实现。"""

    def __init__(self, db_path: str) -> None:
        """初始化存储实例并创建必要的表结构。"""
        self._db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        logger.info("SQLite 交易记录数据库已初始化: %s", self._db_path)

    def _init_schema(self) -> None:
        """创建基础表结构（如不存在）。"""
        sql = """
        CREATE TABLE IF NOT EXISTS trade_records (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time    TEXT    NOT NULL,
            event_type    TEXT    NOT NULL,   -- ORDER / ORDER_ERROR / TRADE / CANCEL / CANCEL_ERROR
            account_id    TEXT,
            stock_code    TEXT,
            direction     TEXT,               -- BUY / SELL / NONE
            volume        INTEGER,
            price         REAL,
            amount        REAL,
            order_id      TEXT,
            strategy_name TEXT,
            remark        TEXT,
            error_msg     TEXT,
            raw_text      TEXT                -- 预留：存放原始对象的 str() 结果
        );
        """
        with _DB_LOCK:
            self._conn.execute(sql)
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_trade_records_order_id ON trade_records(order_id);"
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_trade_records_event_time ON trade_records(event_time);"
            )
            self._conn.commit()

    def _now(self) -> str:
        """当前时间戳（ISO 格式）。"""
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def _insert(
        self,
        *,
        event_type: str,
        account_id: Optional[str] = None,
        stock_code: Optional[str] = None,
        direction: Optional[str] = None,
        volume: Optional[int] = None,
        price: Optional[float] = None,
        amount: Optional[float] = None,
        order_id: Optional[str] = None,
        strategy_name: Optional[str] = None,
        remark: Optional[str] = None,
        error_msg: Optional[str] = None,
        raw_text: Optional[str] = None,
    ) -> None:
        """通用插入函数。"""
        sql = """
        INSERT INTO trade_records (
            event_time, event_type, account_id, stock_code, direction,
            volume, price, amount, order_id, strategy_name, remark,
            error_msg, raw_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = [
            self._now(),
            event_type,
            account_id or "",
            stock_code or "",
            direction or "",
            int(volume) if volume is not None else None,
            float(price) if price is not None else None,
            float(amount) if amount is not None else None,
            str(order_id) if order_id is not None else "",
            strategy_name or "",
            remark or "",
            error_msg or "",
            raw_text or "",
        ]
        try:
            with _DB_LOCK:
                self._conn.execute(sql, params)
                self._conn.commit()
        except Exception as e:
            logger.warning(
                "写入 SQLite 交易记录失败: %s (event_type=%s order_id=%s stock_code=%s params_len=%d)",
                e,
                event_type,
                str(order_id) if order_id is not None else "",
                stock_code or "",
                len(params),
            )

    def log_order(
        self,
        *,
        account_id: str,
        stock_code: str,
        order_type: int,
        volume: int,
        price: float,
        strategy_name: str = "",
        order_remark: str = "",
        order_id: Optional[Any] = None,
        success: bool = True,
        error_msg: str = "",
    ) -> None:
        """记录下单事件（含成功/失败）。"""
        direction = "BUY" if order_type == 23 else "SELL"  # 23/24 对应 xtconstant.STOCK_BUY/SELL
        event_type = "ORDER" if success else "ORDER_ERROR"
        self._insert(
            event_type=event_type,
            account_id=account_id,
            stock_code=stock_code,
            direction=direction,
            volume=volume,
            price=price,
            amount=(price * volume if success else None),
            order_id=order_id,
            strategy_name=strategy_name,
            remark=order_remark,
            error_msg=error_msg,
        )

    def log_cancel(
        self,
        *,
        account_id: str,
        order_id: Any,
        success: bool,
        error_msg: str = "",
    ) -> None:
        """记录撤单事件。"""
        self._insert(
            event_type="CANCEL" if success else "CANCEL_ERROR",
            account_id=account_id,
            order_id=order_id,
            error_msg=error_msg,
        )

    def log_trade_callback(self, trade: Any, account_id: Optional[str] = None) -> None:
        """根据 XtTrade 对象记录成交事件。"""
        try:
            stock_code = getattr(trade, "stock_code", "") or ""
            order_type = getattr(trade, "order_type", 0)
            direction = "BUY" if order_type == 23 else "SELL"
            volume = getattr(trade, "traded_volume", 0) or 0
            price = getattr(trade, "traded_price", 0) or 0.0
            amount = getattr(trade, "traded_amount", 0) or 0.0
            order_id = getattr(trade, "order_id", "") or ""
            strategy_name = getattr(trade, "strategy_name", "") or ""
            remark = getattr(trade, "order_remark", "") or ""
            raw_text = repr(trade)
        except Exception as e:
            logger.warning("解析 XtTrade 成交对象失败，无法写入 SQLite: %s", e)
            return

        self._insert(
            event_type="TRADE",
            account_id=account_id or "",
            stock_code=stock_code,
            direction=direction,
            volume=volume,
            price=price,
            amount=amount,
            order_id=order_id,
            strategy_name=strategy_name,
            remark=remark,
            raw_text=raw_text,
        )

    def get_daily_summary(
        self,
        *,
        date_yyyymmdd: str,
        strategy_name: str = "",
    ) -> Dict[str, Any]:
        """
        查询指定日期（按 event_time 的日期前缀）在本地库中的交易记录并汇总。

        :param date_yyyymmdd: 日期字符串，如 '20260310'
        :param strategy_name: 可选，按策略名过滤（仅对 ORDER/ORDER_ERROR/TRRADE 中 strategy_name 字段生效）
        :return: 汇总 dict，包含 counts、amounts 与最近成交列表（最多 5 条）
        """
        date_yyyymmdd = str(date_yyyymmdd or "").strip()
        if len(date_yyyymmdd) != 8 or not date_yyyymmdd.isdigit():
            return {
                "counts": {},
                "amounts": {},
                "recent_trades": [],
            }
        date_prefix = f"{date_yyyymmdd[0:4]}-{date_yyyymmdd[4:6]}-{date_yyyymmdd[6:8]}"

        where = "event_time LIKE ?"
        params: List[Any] = [f"{date_prefix}%"]
        if strategy_name:
            where += " AND (strategy_name = ? OR strategy_name = '')"
            params.append(strategy_name)

        sql = f"""
        SELECT event_type, direction,
               COUNT(1) AS cnt,
               COALESCE(SUM(COALESCE(amount, 0)), 0) AS amt
        FROM trade_records
        WHERE {where}
        GROUP BY event_type, direction
        """

        counts: Dict[str, int] = {}
        amounts: Dict[str, float] = {}

        try:
            with _DB_LOCK:
                rows = list(self._conn.execute(sql, params).fetchall())
        except Exception as e:
            logger.warning("读取 SQLite 日汇总失败: %s", e)
            rows = []

        for r in rows:
            et = str(r["event_type"] or "")
            dr = str(r["direction"] or "")
            key = f"{et}:{dr}" if dr else et
            try:
                counts[key] = int(r["cnt"] or 0)
            except Exception:
                counts[key] = 0
            try:
                amounts[key] = float(r["amt"] or 0.0)
            except Exception:
                amounts[key] = 0.0

        # 最近 5 条成交明细
        recent_sql = f"""
        SELECT event_time, stock_code, direction, volume, price, amount, order_id, remark
        FROM trade_records
        WHERE {where} AND event_type='TRADE'
        ORDER BY id DESC
        LIMIT 5
        """
        recent: List[Dict[str, Any]] = []
        try:
            with _DB_LOCK:
                recent_rows = list(self._conn.execute(recent_sql, params).fetchall())
            for rr in recent_rows:
                recent.append(
                    {
                        "event_time": str(rr["event_time"] or ""),
                        "stock_code": str(rr["stock_code"] or ""),
                        "direction": str(rr["direction"] or ""),
                        "volume": int(rr["volume"] or 0),
                        "price": float(rr["price"] or 0.0),
                        "amount": float(rr["amount"] or 0.0),
                        "order_id": str(rr["order_id"] or ""),
                        "remark": str(rr["remark"] or ""),
                    }
                )
        except Exception as e:
            logger.warning("读取 SQLite 最近成交失败: %s", e)

        return {
            "counts": counts,
            "amounts": amounts,
            "recent_trades": recent,
        }

    def get_daily_new_position_count(
        self,
        *,
        date_yyyymmdd: str,
        strategy_name: str = "",
    ) -> int:
        """
        统计指定日期的“当日新建仓数量”（按成交 TRADE + BUY，去重股票代码）。

        说明：
        - 使用 trade_records 表中的成交记录（event_type='TRADE'），方向为 BUY；
        - 对 stock_code 做 DISTINCT 去重，得到“当日新开仓的股票只数”；
        - 可选按 strategy_name 过滤（与 get_daily_summary 同口径：允许空策略名记录）。

        :param date_yyyymmdd: 日期字符串，如 '20260310'
        :param strategy_name: 可选，按策略名过滤
        :return: 去重后的股票数量（>=0）
        """
        date_yyyymmdd = str(date_yyyymmdd or "").strip()
        if len(date_yyyymmdd) != 8 or not date_yyyymmdd.isdigit():
            return 0
        date_prefix = f"{date_yyyymmdd[0:4]}-{date_yyyymmdd[4:6]}-{date_yyyymmdd[6:8]}"

        where = "event_time LIKE ? AND event_type='TRADE' AND direction='BUY' AND stock_code!=''"
        params: List[Any] = [f"{date_prefix}%"]
        if strategy_name:
            where += " AND (strategy_name = ? OR strategy_name = '')"
            params.append(strategy_name)

        sql = f"SELECT COUNT(DISTINCT stock_code) AS cnt FROM trade_records WHERE {where}"
        try:
            with _DB_LOCK:
                row = self._conn.execute(sql, params).fetchone()
            return int((row["cnt"] if row is not None else 0) or 0)
        except Exception as e:
            logger.warning("读取 SQLite 当日新建仓数量失败: %s", e)
            return 0


def init_trade_store(db_path: str) -> None:
    """根据给定路径初始化全局交易记录存储。"""
    global _STORE
    try:
        _STORE = SqliteTradeStore(db_path)
    except Exception as e:
        logger.error("初始化 SQLite 交易记录数据库失败，将跳过本地持久化: %s", e)
        _STORE = None


def get_trade_store() -> Optional[SqliteTradeStore]:
    """获取全局 SQLite 存储实例（可能为 None）。"""
    return _STORE

