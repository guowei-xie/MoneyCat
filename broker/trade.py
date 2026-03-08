# -*- coding: utf-8 -*-
"""
交易模块：委托下单、撤单等订单操作，基于 xttrader。
"""
import random
import sys
import os

_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from xtquant.xttrader import XtQuantTrader
from xtquant.xttype import StockAccount
from xtquant import xtconstant
from logging_config import logger
from utils.common import add_stock_suffix


class TradeBroker:
    """
    交易代理：连接 MiniQMT、下单、撤单。
    """

    def __init__(self, userdata_path: str, account_id: str, callback=None):
        """
        :param userdata_path: MiniQMT 的 userdata 目录路径
        :param account_id: 资金账号
        :param callback: XtQuantTraderCallback 实例，可选
        """
        self.userdata_path = userdata_path
        self.account_id = account_id
        self.callback = callback
        self._trader = None
        self._account = None
        self._connected = False

    def connect(self) -> bool:
        """连接交易并订阅账号。"""
        try:
            session_id = random.randint(100000, 999999)
            self._trader = XtQuantTrader(self.userdata_path, session_id, self.callback)
            self._trader.start()
            ret = self._trader.connect()
            if ret != 0:
                logger.error("交易连接失败，返回码: %s", ret)
                return False
            self._account = StockAccount(self.account_id)
            sub_ret = self._trader.subscribe(self._account)
            if sub_ret != 0:
                logger.error("账号订阅失败，返回码: %s", sub_ret)
                return False
            self._connected = True
            logger.info("交易连接成功，账号: %s", self.account_id)
            return True
        except Exception as e:
            logger.error("交易连接异常: %s", e)
            self._connected = False
            return False

    @property
    def is_connected(self) -> bool:
        return getattr(self._trader, "connected", False) and self._account is not None

    def order(
        self,
        stock_code: str,
        order_type: int,
        volume: int,
        price: float,
        strategy_name: str = "",
        order_remark: str = "",
    ) -> int:
        """
        限价委托。

        :param stock_code: 股票代码（可带后缀）
        :param order_type: xtconstant.STOCK_BUY(23) / STOCK_SELL(24)
        :param volume: 数量（股）
        :param price: 价格
        :param strategy_name: 策略名
        :param order_remark: 备注
        :return: 委托编号，失败为 -1
        """
        if not self.is_connected:
            logger.error("交易未连接，无法下单")
            return -1
        code = add_stock_suffix(stock_code)
        try:
            seq = self._trader.order_stock(
                self._account,
                code,
                order_type,
                volume,
                xtconstant.FIX_PRICE,
                price,
                strategy_name,
                order_remark,
            )
            if seq is not None and int(seq) >= 0:
                logger.info("委托已发送 %s %s 数量=%s 价格=%s 单号=%s", "买" if order_type == xtconstant.STOCK_BUY else "卖", code, volume, price, seq)
                return int(seq)
            return -1
        except Exception as e:
            logger.error("下单异常 %s: %s", code, e)
            return -1

    def buy(self, stock_code: str, volume: int, price: float, strategy_name: str = "", order_remark: str = "") -> int:
        """买入，返回委托编号。"""
        return self.order(stock_code, xtconstant.STOCK_BUY, volume, price, strategy_name, order_remark)

    def sell(self, stock_code: str, volume: int, price: float, strategy_name: str = "", order_remark: str = "") -> int:
        """卖出，返回委托编号。"""
        return self.order(stock_code, xtconstant.STOCK_SELL, volume, price, strategy_name, order_remark)

    def cancel(self, order_id: int) -> int:
        """
        撤单。

        :param order_id: 委托编号（下单时返回）
        :return: 0 成功，-1 失败
        """
        if not self.is_connected:
            logger.error("交易未连接，无法撤单")
            return -1
        try:
            ret = self._trader.cancel_order_stock(self._account, order_id)
            if ret == 0:
                logger.info("撤单成功 order_id=%s", order_id)
            else:
                logger.warning("撤单失败 order_id=%s ret=%s", order_id, ret)
            return ret
        except Exception as e:
            logger.error("撤单异常 order_id=%s: %s", order_id, e)
            return -1

    def stop(self) -> None:
        """停止交易连接。"""
        if self._trader:
            try:
                self._trader.stop()
            except Exception as e:
                logger.warning("trader.stop 异常: %s", e)
            self._trader = None
        self._account = None
        self._connected = False
        logger.info("交易连接已关闭")
