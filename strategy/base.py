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

    def _get_config_float(self, section: str, key: str, default: float, min_val: Optional[float] = None) -> float:
        """
        从配置读取浮点值，兼容 ConfigParser 与 dict。

        :param section: 配置节
        :param key: 配置键
        :param default: 默认值
        :param min_val: 最小值下限（可选）
        """
        try:
            if self.config is None:
                return default
            if hasattr(self.config, "get"):
                try:
                    raw = self.config.get(section, key, fallback=str(default))
                except TypeError:
                    raw = str(default)
            else:
                raw = str(default)
            val = float(str(raw or str(default)).strip())
            return max(val, min_val) if min_val is not None else val
        except Exception:
            return default

    def _get_tick_interval_sec(self) -> float:
        """从配置读取信号监听轮询间隔（秒），默认 1，最小 0.1。"""
        return self._get_config_float("STRATEGY", "TICK_INTERVAL_SEC", 1.0, 0.1)

    def on_trading_loop(self) -> None:
        """
        盘中交易循环：轮询行情、产生信号、执行交易。
        默认实现按配置间隔轮询并调用 on_tick；子类可重写整段逻辑。
        """
        import time
        from utils.common import is_trading_time, is_market_closed
        from logging_config import logger

        interval = self._get_tick_interval_sec()
        logger.info("[%s] 信号监听间隔=%.1f 秒", self.name, interval)

        while True:
            if is_market_closed():
                logger.info("[%s] 已收盘，退出盘中循环", self.name)
                break
            if not is_trading_time():
                time.sleep(interval)
                continue
            tick_data = self._fetch_tick_data()
            self.on_tick(tick_data)
            time.sleep(interval)

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

    def get_position_market_value(self) -> float:
        """
        获取当前持仓总市值（失败时返回 0）。

        口径说明：
        - 使用 AccountBroker.get_positions() 返回的 market_value 字段做求和；
        - 若某些环境下 market_value 缺失，则回退用 avg_price * volume 粗略估算；
        - 仅统计 volume > 0 的持仓。
        """
        try:
            positions = self.account.get_positions() or []
        except Exception:
            return 0.0

        total = 0.0
        for p in positions:
            try:
                volume = float(p.get("volume", 0) or 0)
                if volume <= 0:
                    continue
                mv = p.get("market_value", None)
                if mv is None:
                    avg_price = float(p.get("avg_price", 0) or 0)
                    mv = avg_price * volume
                total += float(mv or 0)
            except Exception:
                continue
        return max(float(total), 0.0)

    def get_position_value_limit(self) -> float:
        """从配置读取持仓金额上限（0 表示不启用）。"""
        return self._get_config_float("RISK", "POSITION_VALUE_LIMIT", 0.0, 0.0)

    def should_block_buy_by_position_limit(self) -> bool:
        """
        判断是否应因“持仓金额上限”拦截新的买入委托。

        :return: True 表示应拦截买入；False 表示不拦截
        """
        limit = self.get_position_value_limit()
        if limit <= 0:
            return False
        return self.get_position_market_value() > limit

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

    def get_stock_position_codes(self, positions: List[Dict[str, Any]], tag: str = "positions") -> List[str]:
        """
        从持仓列表中提取有效股票代码（volume > 0，且为股票标的，排除 ETF/基金等）。

        :param positions: 券商返回的持仓列表
        :param tag: 日志标签，便于区分调用场景
        :return: 过滤后的股票代码列表
        """
        from logging_config import logger

        codes = [p.get("stock_code") for p in positions if p.get("stock_code") and int(p.get("volume", 0) or 0) > 0]
        if not codes:
            return []
        try:
            return self.data.filter_tradeable_stock_codes(codes, tag=tag)
        except Exception as exc:
            logger.warning("[%s] 过滤股票持仓失败，回退为全部持仓: %s", self.name, exc)
            return codes

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
        from xtquant import xtconstant

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
        if self.should_block_buy_by_position_limit():
            limit = self.get_position_value_limit()
            mv = self.get_position_market_value()
            self._log_throttled(
                "risk.position_value_limit.block_buy",
                "warning",
                "[%s] 风控拦截买入：当前持仓市值=%.2f 已超过上限=%.2f，跳过下单 stock=%s 价=%.2f 量=%s remark=%s",
                self.name,
                float(mv),
                float(limit),
                str(stock_code),
                float(price),
                int(volume),
                str(remark or ""),
                interval_sec=30,
            )
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
        轮询回调：根据 tick 判断信号并下单等。
        调用频率由 config.ini [STRATEGY] TICK_INTERVAL_SEC 控制。
        子类实现具体逻辑；默认空实现。
        """
        pass

    @abstractmethod
    def on_after_close(self) -> None:
        """盘后：今日总结、统计等。"""
        pass
