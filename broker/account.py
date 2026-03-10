# -*- coding: utf-8 -*-
"""
账户模块：读取资金、持仓等信息，基于 xttrader。
"""
from typing import Optional, List, Dict, Any

from utils.path import ensure_project_root_on_path

ensure_project_root_on_path(__file__, levels_up=2)

from logging_config import logger
from utils.common import add_stock_suffix

# 延迟导入，避免未连接时依赖
def _get_trader_and_account(broker):
    if not getattr(broker, "_trader", None) or not getattr(broker, "_account", None):
        return None, None
    if not getattr(broker._trader, "connected", False):
        return None, None
    return broker._trader, broker._account


class AccountBroker:
    """
    账户代理：查询资金、持仓。需先通过 TradeBroker 连接，再传入 trader 实例查询；
    或本类仅提供静态方法，由外部传入已连接的 trader 与 account。
    此处设计为：依赖 TradeBroker 实例，复用其连接。
    """

    def __init__(self, trade_broker):
        """
        :param trade_broker: 已 connect 的 TradeBroker 实例
        """
        self._trade = trade_broker

    @property
    def _trader(self):
        return getattr(self._trade, "_trader", None)

    @property
    def _account(self):
        return getattr(self._trade, "_account", None)

    def _connected(self) -> bool:
        return self._trade.is_connected

    def get_asset(self) -> Optional[Dict[str, float]]:
        """
        查询资金。

        :return: { 'cash', 'frozen_cash', 'market_value', 'total_asset', 'fetch_balance' } 或 None
        """
        trader, account = _get_trader_and_account(self._trade)
        if not trader or not account:
            logger.warning("账户未连接，无法查询资金")
            return None
        try:
            asset = trader.query_stock_asset(account)
            if asset is None:
                return None
            return {
                "cash": getattr(asset, "cash", 0),
                "frozen_cash": getattr(asset, "frozen_cash", 0),
                "market_value": getattr(asset, "market_value", 0),
                "total_asset": getattr(asset, "total_asset", 0),
                "fetch_balance": getattr(asset, "fetch_balance", 0),
            }
        except Exception as e:
            logger.error("查询资金异常: %s", e)
            return None

    def get_positions(self) -> List[Dict[str, Any]]:
        """
        查询持仓列表。

        :return: [ { 'stock_code', 'volume', 'can_use_volume', 'avg_price', 'market_value', ... } ]
        """
        trader, account = _get_trader_and_account(self._trade)
        if not trader or not account:
            logger.warning("账户未连接，无法查询持仓")
            return []
        try:
            pos_list = trader.query_stock_positions(account)
            if not pos_list:
                return []
            out = []
            for p in pos_list:
                out.append({
                    "stock_code": getattr(p, "stock_code", ""),
                    "volume": getattr(p, "volume", 0),
                    "can_use_volume": getattr(p, "can_use_volume", 0),
                    "avg_price": getattr(p, "avg_price", 0),
                    "market_value": getattr(p, "market_value", 0),
                    "open_price": getattr(p, "open_price", 0),
                    "frozen_volume": getattr(p, "frozen_volume", 0),
                })
            return out
        except Exception as e:
            logger.error("查询持仓异常: %s", e)
            return []

    def get_available_volume(self, stock_code: str) -> int:
        """某只股票可用数量（可卖）。"""
        code = add_stock_suffix(stock_code)
        for p in self.get_positions():
            if p.get("stock_code") == code:
                return int(p.get("can_use_volume", 0))
        return 0
