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
