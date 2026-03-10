# -*- coding: utf-8 -*-
"""
交易模块：委托下单、撤单等订单操作，基于 xttrader。
支持委托成功与成交成功时的飞书通知（成交通过 xt 的 on_stock_trade 回调）。
"""
import random

from utils.path import ensure_project_root_on_path

ensure_project_root_on_path(__file__, levels_up=2)

from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
from xtquant.xttype import StockAccount
from xtquant import xtconstant
from logging_config import logger
from utils.common import add_stock_suffix
from utils.feishu_notify import send_text as feishu_send_text
from db.trade_store import get_trade_store


def _format_hms(value) -> str:
    """
    将成交时间格式化为 HH:MM:SS。

    XtTrade.traded_time 在不同环境可能是 int/str（如 20260310103059、103059、"10:30:59"），
    这里统一提取最后 6 位数字作为时分秒。
    """
    if value is None:
        return "-"
    s = str(value).strip()
    if not s:
        return "-"
    # 已是标准格式
    if len(s) >= 8 and s.count(":") >= 2:
        # 可能包含日期等前缀，取最后一个 HH:MM:SS
        parts = s.split()
        for p in reversed(parts):
            if p.count(":") >= 2 and len(p) >= 8:
                return p[-8:]
        return s[-8:]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) < 6:
        return s
    hms = digits[-6:]
    return f"{hms[0:2]}:{hms[2:4]}:{hms[4:6]}"


def _trade_callback_notify(trade) -> None:
    """
    根据 XtTrade 成交对象组装飞书文案并发送（成交成功回调通知）。
    :param trade: XtTrade，属性含 stock_code, order_type, traded_volume, traded_price, traded_amount, traded_time, order_id, strategy_name, order_remark 等
    """
    try:
        code = getattr(trade, "stock_code", "") or ""
        order_type = getattr(trade, "order_type", 0)
        direction = "买入" if order_type == xtconstant.STOCK_BUY else "卖出"
        vol = getattr(trade, "traded_volume", 0) or 0
        price = getattr(trade, "traded_price", 0) or 0
        amount = getattr(trade, "traded_amount", 0) or 0
        traded_time = _format_hms(getattr(trade, "traded_time", "") or "")
        order_id = getattr(trade, "order_id", "") or ""
        remark = getattr(trade, "order_remark", "") or ""
        name = getattr(trade, "instrument_name", "") or ""
        msg = (
            f"【成交】{direction} {code}"
            f" 数量 {vol} 成交价 {price:.2f} 金额 {amount:.2f}"
            f" 时间 {traded_time} 委托号 {order_id}"
            f" 备注 {remark or '-'}"
        )
        if name:
            msg = msg.replace(f" {code} ", f" {code}({name}) ")
        feishu_send_text(msg)
        # 记录成交到本地 SQLite
        store = get_trade_store()
        if store is not None:
            try:
                store.log_trade_callback(trade)
            except Exception as log_err:
                logger.warning("成交回调写入本地数据库失败: %s", log_err)
    except Exception as e:
        logger.warning("成交回调飞书通知异常: %s", e)


class _FeishuTradeCallback(XtQuantTraderCallback):
    """仅做成交成功飞书通知的 callback，其它回调空实现。"""

    def on_stock_trade(self, trade):
        _trade_callback_notify(trade)


class _FeishuTradeCallbackWrapper(XtQuantTraderCallback):
    """包装用户 callback：成交时先发飞书通知，再转发用户 on_stock_trade。其它方法直接转发。"""

    def __init__(self, user_callback):
        self._user = user_callback

    def on_stock_trade(self, trade):
        _trade_callback_notify(trade)
        if hasattr(self._user, "on_stock_trade") and callable(self._user.on_stock_trade):
            self._user.on_stock_trade(trade)

    def on_connected(self):
        if hasattr(self._user, "on_connected") and callable(self._user.on_connected):
            self._user.on_connected()

    def on_disconnected(self):
        if hasattr(self._user, "on_disconnected") and callable(self._user.on_disconnected):
            self._user.on_disconnected()

    def on_account_status(self, status):
        if hasattr(self._user, "on_account_status") and callable(self._user.on_account_status):
            self._user.on_account_status(status)

    def on_stock_asset(self, asset):
        if hasattr(self._user, "on_stock_asset") and callable(self._user.on_stock_asset):
            self._user.on_stock_asset(asset)

    def on_stock_order(self, order):
        if hasattr(self._user, "on_stock_order") and callable(self._user.on_stock_order):
            self._user.on_stock_order(order)

    def on_stock_position(self, position):
        if hasattr(self._user, "on_stock_position") and callable(self._user.on_stock_position):
            self._user.on_stock_position(position)

    def on_order_error(self, order_error):
        if hasattr(self._user, "on_order_error") and callable(self._user.on_order_error):
            self._user.on_order_error(order_error)

    def on_cancel_error(self, cancel_error):
        if hasattr(self._user, "on_cancel_error") and callable(self._user.on_cancel_error):
            self._user.on_cancel_error(cancel_error)

    def on_order_stock_async_response(self, response):
        if hasattr(self._user, "on_order_stock_async_response") and callable(self._user.on_order_stock_async_response):
            self._user.on_order_stock_async_response(response)

    def on_cancel_order_stock_async_response(self, response):
        if hasattr(self._user, "on_cancel_order_stock_async_response") and callable(self._user.on_cancel_order_stock_async_response):
            self._user.on_cancel_order_stock_async_response(response)


class TradeBroker:
    """
    交易代理：连接 MiniQMT、下单、撤单。
    """

    def __init__(self, userdata_path: str, account_id: str, callback=None):
        """
        :param userdata_path: MiniQMT 的 userdata 目录路径
        :param account_id: 资金账号
        :param callback: XtQuantTraderCallback 实例，可选；不传时仅启用成交飞书通知
        """
        self.userdata_path = userdata_path
        self.account_id = account_id
        self.callback = callback
        self._trader_callback = _FeishuTradeCallbackWrapper(callback) if callback else _FeishuTradeCallback()
        self._trader = None
        self._account = None
        self._connected = False

    def connect(self) -> bool:
        """连接交易并订阅账号。"""
        try:
            session_id = random.randint(100000, 999999)
            self._trader = XtQuantTrader(self.userdata_path, session_id, self._trader_callback)
            self._trader.start()
            ret = self._trader.connect()
            if ret != 0:
                logger.error("交易连接失败，返回码: %s", ret)
                feishu_send_text(f"【错误】交易连接失败 返回码 {ret} 账号 {self.account_id}")
                return False
            self._account = StockAccount(self.account_id)
            sub_ret = self._trader.subscribe(self._account)
            if sub_ret != 0:
                logger.error("账号订阅失败，返回码: %s", sub_ret)
                feishu_send_text(f"【错误】交易账号订阅失败 返回码 {sub_ret} 账号 {self.account_id}")
                return False
            self._connected = True
            logger.info("交易连接成功，账号: %s", self.account_id)
            return True
        except Exception as e:
            logger.error("交易连接异常: %s", e)
            feishu_send_text(f"【错误】交易连接异常 账号 {self.account_id} 异常 {e}")
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
            feishu_send_text(f"【警告】下单时交易未连接 {stock_code} 数量 {volume} 价格 {price:.2f}")
            # 本地记录下单失败
            store = get_trade_store()
            if store is not None:
                store.log_order(
                    account_id=self.account_id,
                    stock_code=stock_code,
                    order_type=order_type,
                    volume=volume,
                    price=price,
                    strategy_name=strategy_name,
                    order_remark=order_remark,
                    success=False,
                    error_msg="trade not connected",
                )
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
                # 记录本地下单成功
                store = get_trade_store()
                if store is not None:
                    try:
                        store.log_order(
                            account_id=self.account_id,
                            stock_code=code,
                            order_type=order_type,
                            volume=volume,
                            price=price,
                            strategy_name=strategy_name,
                            order_remark=order_remark,
                            order_id=int(seq),
                            success=True,
                        )
                    except Exception as log_err:
                        logger.warning("下单写入本地数据库失败: %s", log_err)
                # 委托通知仅推送买入，忽略卖出委托
                if order_type == xtconstant.STOCK_BUY:
                    feishu_send_text(
                        f"【委托】买入 {code} 数量 {volume} 价格 {price:.2f} 单号 {int(seq)} 备注 {order_remark or '-'}"
                    )
                return int(seq)
            # 写入下单失败记录
            store = get_trade_store()
            if store is not None:
                store.log_order(
                    account_id=self.account_id,
                    stock_code=code,
                    order_type=order_type,
                    volume=volume,
                    price=price,
                    strategy_name=strategy_name,
                    order_remark=order_remark,
                    order_id=seq,
                    success=False,
                    error_msg="order_stock returned invalid seq",
                )
            return -1
        except Exception as e:
            logger.error("下单异常 %s: %s", code, e)
            feishu_send_text(f"【错误】下单异常 {code} 异常 {e}")
            # 写入异常记录
            store = get_trade_store()
            if store is not None:
                store.log_order(
                    account_id=self.account_id,
                    stock_code=code,
                    order_type=order_type,
                    volume=volume,
                    price=price,
                    strategy_name=strategy_name,
                    order_remark=order_remark,
                    success=False,
                    error_msg=str(e),
                )
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
            store = get_trade_store()
            if store is not None:
                store.log_cancel(
                    account_id=self.account_id,
                    order_id=order_id,
                    success=False,
                    error_msg="trade not connected",
                )
            return -1
        try:
            ret = self._trader.cancel_order_stock(self._account, order_id)
            store = get_trade_store()
            if ret == 0:
                logger.info("撤单成功 order_id=%s", order_id)
                if store is not None:
                    store.log_cancel(
                        account_id=self.account_id,
                        order_id=order_id,
                        success=True,
                    )
            else:
                logger.warning("撤单失败 order_id=%s ret=%s", order_id, ret)
                if store is not None:
                    store.log_cancel(
                        account_id=self.account_id,
                        order_id=order_id,
                        success=False,
                        error_msg=f"ret={ret}",
                    )
            return ret
        except Exception as e:
            logger.error("撤单异常 order_id=%s: %s", order_id, e)
            store = get_trade_store()
            if store is not None:
                store.log_cancel(
                    account_id=self.account_id,
                    order_id=order_id,
                    success=False,
                    error_msg=str(e),
                )
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
