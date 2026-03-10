# -*- coding: utf-8 -*-
"""
策略基类：定义初始化、盘前、盘中、盘后四阶段接口。
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BaseStrategy(ABC):
    """
    策略基类。子类实现：on_init、on_prepare、on_tick（或盘中循环）、on_after_close。
    """

    def __init__(self, config: Any, data_broker: Any, trade_broker: Any, account_broker: Any):
        """
        :param config: 配置对象（如 ConfigParser 或 dict）
        :param data_broker: DataBroker 实例
        :param trade_broker: TradeBroker 实例
        :param account_broker: AccountBroker 实例
        """
        self.config = config
        self.data = data_broker
        self.trade = trade_broker
        self.account = account_broker
        self.name = self.__class__.__name__
        # 日志节流：避免盘中高频循环刷屏（key 建议包含场景与股票代码）
        self._throttle_log_ts: Dict[str, float] = {}
        # 资金不足日志节流：记录每只股票上次输出时间戳
        self._last_insufficient_cash_log: Dict[str, float] = {}

    def run(self) -> None:
        """完整流程：初始化 → 盘前 → 盘中 → 盘后。"""
        self.on_init()
        self.on_prepare()
        self.on_trading_loop()
        self.on_after_close()

    @abstractmethod
    def on_init(self) -> None:
        """初始化：读配置、连接账户、更新历史数据等。"""
        pass

    @abstractmethod
    def on_prepare(self) -> None:
        """盘前准备：预选股池、持仓股池、缓存等。"""
        pass

    def on_trading_loop(self) -> None:
        """
        盘中交易循环：轮询行情、产生信号、执行交易。
        默认实现为每秒轮询并调用 on_tick；子类可重写整段逻辑。
        """
        import time
        from utils.common import is_trading_time, is_market_closed
        from logging_config import logger

        while True:
            if is_market_closed():
                logger.info("[%s] 已收盘，退出盘中循环", self.name)
                break
            if not is_trading_time():
                time.sleep(1)
                continue
            # 每秒轮询一次
            tick_data = self._fetch_tick_data()
            self.on_tick(tick_data)
            time.sleep(1)

    def ensure_data_connected(self) -> bool:
        """
        确保行情连接已建立。

        :return: True 表示已连接或连接成功；False 表示连接失败。
        """
        from logging_config import logger

        try:
            if getattr(self.data, "is_connected", False):
                return True
            if hasattr(self.data, "connect"):
                self.data.connect()
            return bool(getattr(self.data, "is_connected", False))
        except Exception as exc:
            logger.warning("[%s] 行情连接失败: %s", self.name, exc)
            return False

    def ensure_trade_connected_from_config(
        self,
        account_section: str = "ACCOUNT",
        id_key: str = "ACCOUNT_ID",
        path_key: str = "MINI_QMT_PATH",
    ) -> bool:
        """
        从配置读取账号信息并尝试建立交易连接（未配置则静默跳过）。

        :param account_section: 配置段名
        :param id_key: 账号 ID 键名
        :param path_key: 交易端路径键名
        :return: True 表示已连接或连接成功；False 表示未连接（含未配置/失败）。
        """
        from logging_config import logger

        try:
            if getattr(self.trade, "is_connected", False):
                return True
            if not hasattr(self.config, "get"):
                return False
            account_id = self.config.get(account_section, id_key, fallback="")
            userdata = self.config.get(account_section, path_key, fallback="")
            if not account_id or not userdata:
                return False
            if hasattr(self.trade, "connect"):
                self.trade.connect()
            return bool(getattr(self.trade, "is_connected", False))
        except Exception as exc:
            logger.warning("[%s] 交易连接跳过: %s", self.name, exc)
            return False

    def _log_throttled(self, key: str, level: str, msg: str, *args: Any, interval_sec: int = 30) -> None:
        """
        按 key 节流输出日志，适用于盘中高频循环。

        :param key: 节流 key（建议包含场景与股票代码）
        :param level: 日志级别（debug/info/warning/error）
        :param msg: 日志模板
        :param args: 模板参数
        :param interval_sec: 最小输出间隔（秒）
        """
        import time
        from logging_config import logger

        now = time.time()
        last_ts = float(self._throttle_log_ts.get(key, 0.0) or 0.0)
        if now - last_ts < max(int(interval_sec), 1):
            return
        self._throttle_log_ts[key] = now

        fn = getattr(logger, str(level).lower(), None)
        if not callable(fn):
            fn = logger.debug
        fn(msg, *args)

    def get_asset_safe(self) -> Dict[str, Any]:
        """
        安全获取账户资产信息（失败时返回空 dict）。
        """
        try:
            asset = self.account.get_asset()
            return asset or {}
        except Exception:
            return {}

    def has_position(self, stock_code: str) -> bool:
        """
        判断是否持有指定股票（任意可见数量即视为有持仓）。
        """
        try:
            if not getattr(self.account, "_connected", None) or not self.account._connected():
                return False
            for pos in self.account.get_positions() or []:
                if pos.get("stock_code") == stock_code and float(pos.get("volume", 0) or 0) > 0:
                    return True
            return False
        except Exception:
            return False

    def log_insufficient_cash(self, stock_code: str, price: float) -> None:
        """
        资金不足时按单票节流输出提示日志，避免在高频 tick 下刷屏。
        """
        import time
        from logging_config import logger

        now = time.time()
        last_ts = float(self._last_insufficient_cash_log.get(stock_code, 0.0) or 0.0)
        # 默认同一只股票 60 秒内只提示一次
        if now - last_ts < 60:
            return
        self._last_insufficient_cash_log[stock_code] = now
        asset = self.account.get_asset()
        cash = float((asset or {}).get("cash", 0) or 0)
        logger.info(
            "[%s] 资金不足，无法按计划买入 %s 当前价=%.2f 可用资金=%.2f",
            self.name,
            stock_code,
            price,
            cash,
        )

    def get_unfinished_orders(self) -> List[Any]:
        """
        查询当前未完全成交的委托（买卖均返回），按委托时间升序排序。
        """
        from logging_config import logger

        trader = getattr(self.trade, "_trader", None)
        account = getattr(self.trade, "_account", None)
        if trader is None or account is None:
            return []
        try:
            orders = trader.query_stock_orders(account) or []
        except Exception as exc:
            logger.warning("[%s] 查询委托列表失败: %s", self.name, exc)
            return []

        unfinished: List[Any] = []
        for o in orders:
            try:
                order_volume = int(getattr(o, "order_volume", 0) or 0)
                traded_volume = int(getattr(o, "traded_volume", 0) or 0)
                if order_volume <= 0 or traded_volume >= order_volume:
                    continue
                unfinished.append(o)
            except Exception:
                continue

        unfinished.sort(key=lambda x: getattr(x, "order_time", 0))
        return unfinished

    def has_unfinished_buy_order(self, stock_code: str) -> bool:
        """
        判断指定股票是否存在“未完全成交”的买入委托。

        用途：盘中高频轮询下，避免同一标的在首笔买单未成交/部分成交时重复下单。

        :param stock_code: 股票代码（与委托对象的 stock_code 字段一致）
        :return: True 表示存在未完全成交的买入委托；否则 False
        """
        if not stock_code:
            return False
        try:
            from xtquant import xtconstant  # 延迟导入，避免无交易环境时报错
        except Exception:
            # 无交易环境下无法判断委托方向，默认不拦截
            return False

        for o in self.get_unfinished_orders():
            try:
                if str(getattr(o, "stock_code", "") or "") != stock_code:
                    continue
                order_type = int(getattr(o, "order_type", 0) or 0)
                if order_type != xtconstant.STOCK_BUY:
                    continue
                order_volume = int(getattr(o, "order_volume", 0) or 0)
                traded_volume = int(getattr(o, "traded_volume", 0) or 0)
                if order_volume > 0 and traded_volume < order_volume:
                    return True
            except Exception:
                continue
        return False

    def cancel_earliest_unfilled_buy_order(self) -> bool:
        """
        撤掉最新的未完全成交买入委托，用于释放被占用资金。
        """
        from logging_config import logger
        from utils.feishu_notify import send_text as feishu_send_text

        try:
            from xtquant import xtconstant  # 延迟导入，避免无交易环境时报错
        except Exception:
            return False

        orders = self.get_unfinished_orders()
        if not orders:
            return False

        # get_unfinished_orders 返回的是按委托时间升序的列表，这里改为选择“最新”的买入单
        target = None
        for o in reversed(orders):
            try:
                order_type = int(getattr(o, "order_type", 0) or 0)
                if order_type == xtconstant.STOCK_BUY:
                    target = o
                    break
            except Exception:
                continue

        if target is None:
            return False

        order_id = int(getattr(target, "order_id", -1) or -1)
        stock_code = str(getattr(target, "stock_code", "") or "")
        stock_name = str(getattr(target, "instrument_name", "") or "")
        if order_id < 0:
            return False

        ret = self.trade.cancel(order_id)
        if ret != 0:
            logger.warning(
                "[%s] 撤掉最新未成交买单失败: %s order_id=%s ret=%s",
                self.name,
                stock_code,
                order_id,
                ret,
            )
            return False

        logger.info(
            "[%s] 已撤掉最新未成交买单: %s order_id=%s",
            self.name,
            stock_code,
            order_id,
        )
        try:
            if stock_name:
                feishu_send_text(
                    f"【策略自动撤单】因资金不足，撤掉最新未成交买单 {stock_code}({stock_name}) 委托号 {order_id}"
                )
            else:
                feishu_send_text(
                    f"【策略自动撤单】因资金不足，撤掉最新未成交买单 {stock_code} 委托号 {order_id}"
                )
        except Exception:
            # 飞书通知失败不影响交易主流程
            pass
        return True

    def cancel_all_unfilled_orders(self, notify: bool = True) -> None:
        """
        撤掉当前所有未完全成交的委托（买入与卖出均撤），可选飞书通知。
        """
        from logging_config import logger
        from utils.feishu_notify import send_text as feishu_send_text

        orders = self.get_unfinished_orders()
        if not orders:
            logger.info("[%s] 当前无未成交委托需要撤单", self.name)
            return

        logger.info(
            "[%s] 开始撤掉所有未成交委托，数量=%s",
            self.name,
            len(orders),
        )

        success, fail = 0, 0
        for o in orders:
            try:
                order_id = int(getattr(o, "order_id", -1) or -1)
                stock_code = str(getattr(o, "stock_code", "") or "")
                if order_id < 0:
                    continue
                ret = self.trade.cancel(order_id)
                if ret == 0:
                    success += 1
                    logger.info(
                        "[%s] 撤单成功: %s order_id=%s",
                        self.name,
                        stock_code,
                        order_id,
                    )
                else:
                    fail += 1
                    logger.warning(
                        "[%s] 撤单失败: %s order_id=%s ret=%s",
                        self.name,
                        stock_code,
                        order_id,
                        ret,
                    )
            except Exception as exc:
                fail += 1
                logger.warning("[%s] 撤单异常: %s", self.name, exc)

        if not notify:
            return
        try:
            feishu_send_text(
                f"【策略自动撤单】批量撤单完成 成功 {success} 笔 失败 {fail} 笔"
            )
        except Exception:
            pass

    def calc_buy_volume_by_ratio(self, price: float, cash_ratio: float, lot_size: int = 100) -> int:
        """
        按「总资产 * 比例」与「可用现金」的较小值计算买入股数（按 lot_size 取整）。

        :param price: 买入价格
        :param cash_ratio: 单次买入最多使用总资产比例（0~1）
        :param lot_size: 交易单位（默认 100 股）
        :return: 可买股数（>=0）
        """
        if price <= 0:
            return 0
        asset = self.get_asset_safe()
        total_asset = float(asset.get("total_asset", 0) or 0)
        cash = float(asset.get("cash", 0) or 0)
        if total_asset <= 0 or cash <= 0:
            return 0
        ratio = max(min(float(cash_ratio), 1.0), 0.0)
        use_cash = min(total_asset * ratio, cash)
        if lot_size <= 0:
            lot_size = 1
        volume = int(use_cash // (price * lot_size)) * lot_size
        return max(int(volume), 0)

    def place_buy_order(self, stock_code: str, volume: int, price: float, remark: str = "") -> Optional[Any]:
        """
        下买单并做统一日志与异常保护。

        :return: 下单返回值（如 order_id），失败返回 None
        """
        from logging_config import logger

        if volume <= 0 or price <= 0:
            return None
        try:
            order_id = self.trade.buy(stock_code, int(volume), float(price), strategy_name=self.name, order_remark=remark)
            logger.info("[%s] 买入委托: %s 价=%.2f 量=%s 返回单号=%s", self.name, stock_code, float(price), int(volume), order_id)
            return order_id
        except Exception as exc:
            logger.exception(
                "[%s] 买入下单异常: %s remark=%s 价=%.2f 量=%s err=%s",
                self.name,
                stock_code,
                remark,
                float(price),
                int(volume),
                exc,
            )
            return None

    def place_sell_order(self, stock_code: str, volume: int, price: float, remark: str = "") -> Optional[Any]:
        """
        下卖单并做统一日志与异常保护。

        :return: 下单返回值（如 order_id），失败返回 None
        """
        from logging_config import logger

        if volume <= 0 or price <= 0:
            return None
        try:
            order_id = self.trade.sell(stock_code, int(volume), float(price), strategy_name=self.name, order_remark=remark)
            logger.info("[%s] 卖出委托: %s 价=%.2f 量=%s 返回单号=%s", self.name, stock_code, float(price), int(volume), order_id)
            return order_id
        except Exception as exc:
            logger.exception(
                "[%s] 卖出下单异常: %s remark=%s 价=%.2f 量=%s err=%s",
                self.name,
                stock_code,
                remark,
                float(price),
                int(volume),
                exc,
            )
            return None

    def _fetch_tick_data(self) -> Dict[str, Any]:
        """获取当前 tick 数据，供 on_tick 使用。子类可重写以传入股池。"""
        return self.data.get_full_tick(self.get_watch_list())

    def get_watch_list(self) -> List[str]:
        """当前关注的股票列表（用于拉 tick）。子类实现。"""
        return []

    def on_tick(self, tick_data: Dict[str, Any]) -> None:
        """
        每秒回调：根据 tick 判断信号并下单等。
        子类实现具体逻辑；默认空实现。
        """
        pass

    @abstractmethod
    def on_after_close(self) -> None:
        """盘后：今日总结、统计等。"""
        pass
