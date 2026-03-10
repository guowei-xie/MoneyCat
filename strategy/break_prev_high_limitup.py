# -*- coding: utf-8 -*-
"""
突破前高涨停打板实盘策略（基于回测版 BreakPrevHighLimitUp_v2 改写）。

核心思想：
1. 盘前：在给定股票池中，根据近 N 日日线数据筛选“接近前高”的标的，并缓存昨日收盘价与前高价；
2. 盘中：每秒轮询 tick，同时依赖 1 分钟分时 K 线信号：
   - 买入：涨幅接近涨停 + 当日最低或昨收低于前高 + 当前价突破前高；
   - 卖出：当前涨停不卖；炸板则清仓；否则按分时 MACD 顶/顶背离分批止盈。
"""

from typing import Dict, List, Any, Optional

import time
import pandas as pd

from logging_config import logger
from strategy.base import BaseStrategy
from utils.common import get_trading_dates, current_date_str
from utils.market_rules import is_limit, get_limit_price
from utils.position_sizing import convert_to_safe_sell_volume
from utils.indicators import get_macd, is_macd_top
from utils.feishu_notify import send_text as feishu_send_text

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable


class BreakPrevHighLimitUpStrategy(BaseStrategy):
    """
    突破前高涨停打板策略（实盘版，tick 轮询 + 1 分钟分时订阅）。
    """

    def __init__(self, config: Any, data_broker: Any, trade_broker: Any, account_broker: Any):
        """
        初始化策略参数与缓存。

        :param config: 配置对象
        :param data_broker: DataBroker 实例
        :param trade_broker: TradeBroker 实例
        :param account_broker: AccountBroker 实例
        """
        super().__init__(config, data_broker, trade_broker, account_broker)

        # ↓ 选股与买卖参数（默认值与回测工程保持一致，可通过配置覆盖）
        self.lookback_days = 90
        self.margin_pct = 0.10
        self.limit_near_pct = 0.095
        self.batch_sell_count = 2
        self.sell_macd_min_bars = 5
        self.sell_broken_limit_gap_minutes = 3
        self.interval_days = 10
        self.interval_max_amplitude_pct = 0.20
        self.limit_count_check_days = 250
        self.min_limit_count = 1
        self.buy_max_bars = 90  # 分时根数，约 9:30~11:00
        self.buy_cash_ratio = 0.1  # 每次买入最多使用可用资金比例

        # ↓ 运行时缓存
        self.trade_calendar: List[str] = []
        self.universe: List[str] = []            # 总股票池
        self.selected_stocks: List[str] = []     # 向后兼容：当前仍作为“预买池”的别名使用
        self.pre_buy_pool: List[str] = []        # 预买入池（盘前预选股）
        self.pre_sell_pool: List[str] = []       # 预卖出池（当前持仓）
        self.cached: Dict[str, Dict[str, Any]] = {}  # 各股票盘前缓存（昨收、前高、卖出状态等）

        # 资金不足日志节流：记录每只股票上次输出时间戳
        self._last_insufficient_cash_log: Dict[str, float] = {}

        self._load_params_from_config()

    def _load_params_from_config(self) -> None:
        """
        从配置中加载策略参数与股票池设置。
        """
        if not hasattr(self.config, "get"):
            return
        try:
            sec = "STRATEGY"
            self.lookback_days = self.config.getint(sec, "LOOKBACK_DAYS", fallback=self.lookback_days)
            self.margin_pct = self.config.getfloat(sec, "MARGIN_PCT", fallback=self.margin_pct)
            self.limit_near_pct = self.config.getfloat(sec, "LIMIT_NEAR_PCT", fallback=self.limit_near_pct)
            self.interval_days = self.config.getint(sec, "INTERVAL_DAYS", fallback=self.interval_days)
            self.interval_max_amplitude_pct = self.config.getfloat(
                sec, "INTERVAL_MAX_AMPLITUDE_PCT", fallback=self.interval_max_amplitude_pct
            )
            self.limit_count_check_days = self.config.getint(
                sec, "LIMIT_COUNT_CHECK_DAYS", fallback=self.limit_count_check_days
            )
            self.min_limit_count = self.config.getint(sec, "MIN_LIMIT_COUNT", fallback=self.min_limit_count)
            self.buy_max_bars = self.config.getint(sec, "BUY_MAX_BARS", fallback=self.buy_max_bars)
            self.buy_cash_ratio = self.config.getfloat(sec, "BUY_CASH_RATIO", fallback=self.buy_cash_ratio)
        except Exception as e:
            logger.warning("[%s] 读取 STRATEGY 配置失败: %s", self.name, e)

    def on_init(self) -> None:
        """
        初始化阶段：连接行情 / 交易，加载交易日历。
        """
        logger.info("[%s] 策略初始化开始", self.name)

        if not self.data.is_connected:
            self.data.connect()

        # 交易连接（若配置了账号与路径）
        try:
            if hasattr(self.config, "get"):
                account_id = self.config.get("ACCOUNT", "ACCOUNT_ID", fallback="")
                userdata = self.config.get("ACCOUNT", "MINI_QMT_PATH", fallback="")
            else:
                account_id = userdata = ""
            if account_id and userdata and not self.trade.is_connected:
                self.trade.connect()
        except Exception as e:
            logger.warning("[%s] 交易连接跳过: %s", self.name, e)

        # 使用 akshare 交易日历构建 trade_calendar，供之后选 T-1 日使用
        try:
            today = current_date_str()
            self.trade_calendar = get_trading_dates(end_time=today, count=-1)
        except Exception as e:
            logger.warning("[%s] 获取交易日历失败: %s", self.name, e)
            self.trade_calendar = []

        logger.info("[%s] 策略初始化完成", self.name)

    def on_prepare(self) -> None:
        """
        盘前准备：构建股票池、基于日线筛选标的并缓存昨收与前高，订阅 1 分钟分时。
        """
        logger.info("[%s] 盘前准备开始", self.name)

        # 股票池来源：统一使用主入口准备好的“沪深A股主板”股票池缓存
        if not self.universe:
            self.universe = getattr(self.data, "main_board_universe", []) or []
        if not self.universe:
            logger.error("[%s] 未初始化主板股票池缓存，请确认 main.py 已先构建股票池", self.name)
            self.selected_stocks = []
            self.cached = {}
            return

        # 使用 T-1 作为选股与缓存基准日
        trade_date = self._get_yesterday_trade_date()
        if not trade_date:
            logger.warning("[%s] 无法确定昨日交易日，跳过盘前选股", self.name)
            self.pre_buy_pool = list(self.universe)
            self.pre_sell_pool = []
            self.selected_stocks = list(self.pre_buy_pool)
            self.cached = {}
            return

        bars_count = max(self.lookback_days, self.limit_count_check_days) + 1
        # 1) 先基于全股票池日线数据做预选股
        daily_bars_universe = self.data.get_kline_bars(
            self.universe,
            period="1d",
            end_time=trade_date,
            count=bars_count,
            field_list=["open", "high", "low", "close", "preClose"],
        )

        self.pre_buy_pool = self._select_stocks_by_daily(daily_bars_universe)
        self.selected_stocks = list(self.pre_buy_pool)
        logger.info(f"{self.name} 盘前预选股完成，预选股池=%s", len(self.pre_buy_pool))

        # 2) 获取当前持仓，构建预卖出池
        holding_codes = {p.get("stock_code") for p in self.account.get_positions()} if self.account._connected() else set()
        self.pre_sell_pool = sorted(code for code in holding_codes if code)

        # 预买入池 + 预卖出池 合并为统一缓存池
        cache_pool = sorted(set(self.pre_buy_pool) | set(self.pre_sell_pool))

        # 3) 对“预买入池 + 预卖出池”统一做盘前缓存（相同缓存逻辑）
        if cache_pool:
            daily_bars_cache = self.data.get_kline_bars(
                list(cache_pool),
                period="1d",
                end_time=trade_date,
                count=self.lookback_days + 1,
                field_list=["open", "high", "low", "close", "preClose"],
            )
            self._init_cache_from_daily(daily_bars_cache)

        # 订阅 1 分钟 K 线，便于盘中快速获取分时数据
        logger.info(f"{self.name} 开始订阅1分钟K线，订阅股票数={len(cache_pool)}只")
        self.data.subscribe_kline(cache_pool, period="1m", count=-1)

        logger.info(
            "[%s] 盘前准备完成，股票池总数=%s，预买池=%s，预卖池=%s",
            self.name,
            len(self.universe),
            len(self.pre_buy_pool),
            len(self.pre_sell_pool),
        )
        self._notify_prepare_account_summary()

    def _notify_prepare_account_summary(self) -> None:
        """
        盘前准备结束后推送账户摘要信息，减少盯盘负担。

        推送内容包含：
        - 总资金（total_asset）
        - 持仓金额（market_value）
        - 持仓数量（持仓股票只数）
        - 预选数量（预买池数量）
        """
        total_asset = 0.0
        market_value = 0.0
        holding_count = 0
        preselect_count = len(self.pre_buy_pool)

        try:
            if getattr(self.account, "_connected", None) and self.account._connected():
                asset = self.account.get_asset() or {}
                total_asset = float(asset.get("total_asset", 0) or 0)
                market_value = float(asset.get("market_value", 0) or 0)
                positions = self.account.get_positions() or []
                holding_count = sum(1 for p in positions if float(p.get("volume", 0) or 0) > 0)

            msg = (
                f"【提示】盘前准备完成：总资金={total_asset:.2f} "
                f"持仓金额={market_value:.2f} 持仓数量={holding_count} 预选数量={preselect_count}"
            )
            feishu_send_text(msg)
        except Exception as exc:
            logger.warning("[%s] 盘前账户摘要推送失败: %s", self.name, exc)

    def _get_yesterday_trade_date(self) -> Optional[str]:
        """
        基于 trade_calendar 获取“昨日交易日”。
        """
        if not self.trade_calendar:
            return None
        today = current_date_str()
        dates = [d for d in self.trade_calendar if d <= today]
        if len(dates) >= 2:
            return dates[-2]
        return dates[-1] if dates else None

    def _select_stocks_by_daily(self, daily_bars: Dict[str, pd.DataFrame]) -> List[str]:
        """
        基于日线数据进行股票筛选（去掉回测版中使用 T+1 未来数据的部分，只保留 T 日条件）。

        条件概要：
        1) 当前交易日收盘价位于 [前高*(1-margin_pct), 前高] 区间内，前高为近 lookback_days 日实体最高价（不含 T 日）；
        2) 近 interval_days+1 个交易日区间振幅 <= interval_max_amplitude_pct；
        3) 近 interval_days 个交易日内没有涨停；
        4) 近 limit_count_check_days 日涨停次数不少于 min_limit_count。
        """
        result: List[str] = []
        min_bars = max(2, self.interval_days + 1)

        iterator = tqdm(
            daily_bars.items(),
            desc=f"{self.name} 盘前预选股",
            ncols=100,
            unit="只",
        )

        for code, df in iterator:
            if df is None or df.empty or len(df) < min_bars:
                continue

            df_sorted = df.sort_index()
            # 取最近 bars_count 条
            if len(df_sorted) > self.lookback_days + 1:
                df_sorted = df_sorted.iloc[-(self.lookback_days + 1) :]

            # T 日为最后一条，前高不包含 T 日
            df_before_t = df_sorted.iloc[:-1]
            if df_before_t.empty:
                continue

            entity_high = df_before_t[["open", "close"]].max(axis=1).max()
            current_close = float(df_sorted.iloc[-1]["close"])
            threshold = float(entity_high) * (1 - self.margin_pct)
            if current_close <= threshold or current_close > float(entity_high):
                continue

            # T~T-n 日区间振幅
            interval_slice = df_sorted.iloc[-(self.interval_days + 1) :]
            interval_low = float(interval_slice["low"].min())
            if interval_low <= 0:
                continue
            amplitude = float(interval_slice["high"].max()) / interval_low - 1
            if amplitude > self.interval_max_amplitude_pct:
                continue

            # 近 interval_days 日不能有涨停
            recent = df_sorted.iloc[-self.interval_days :]
            has_limit_recent = False
            for _, row in recent.iterrows():
                pre_close = float(row.get("preClose", 0) or 0)
                close_price = float(row["close"])
                if pre_close > 0 and is_limit(code, close_price, pre_close):
                    has_limit_recent = True
                    break
            if has_limit_recent:
                continue

            # 近 limit_count_check_days 日涨停次数不少于 min_limit_count
            last_n = df_sorted.iloc[-self.limit_count_check_days :] if len(df_sorted) >= self.limit_count_check_days else df_sorted
            limit_up_count = 0
            for _, row in last_n.iterrows():
                pre_close = float(row.get("preClose", 0) or 0)
                close_price = float(row["close"])
                if pre_close > 0 and is_limit(code, close_price, pre_close):
                    limit_up_count += 1
            if limit_up_count < self.min_limit_count:
                continue

            result.append(code)

        return result

    def _init_cache_from_daily(self, daily_bars: Dict[str, pd.DataFrame]) -> None:
        """
        根据日线数据初始化缓存：昨收价、前高价、分批卖出状态等。
        """
        iterator = tqdm(
            daily_bars.items(),
            desc=f"{self.name} 盘前缓存",
            ncols=100,
            unit="只",
        )

        for code, df in iterator:
            if df is None or df.empty or len(df) < 2:
                continue
            df_sorted = df.sort_index()
            # 取近 lookback_days+1 条
            if len(df_sorted) > self.lookback_days + 1:
                df_sorted = df_sorted.iloc[-(self.lookback_days + 1) :]

            pre_close = float(df_sorted.iloc[-1]["close"])
            df_before_t = df_sorted.iloc[:-1]
            if df_before_t.empty:
                continue
            entity_high = df_before_t[["open", "close"]].max(axis=1).max()

            self.cached.setdefault(code, {})
            self.cached[code]["pre_close"] = pre_close
            self.cached[code]["prev_high_price"] = float(entity_high)
            self.cached[code]["batch_sell_count"] = self.batch_sell_count
            self.cached[code]["top_price"] = 0.0
            self.cached[code]["top_macd"] = 0.0

    def get_watch_list(self) -> List[str]:
        """
        返回盘中关注的股票列表（预选股池 + 当前持仓）。
        """
        # 预买入池 + 预卖出池（均为带后缀代码）
        return list(set(self.pre_buy_pool) | set(self.pre_sell_pool))

    def _calc_buy_volume(self, price: float) -> int:
        """
        根据账户总资产和 buy_cash_ratio 计算单次买入股数（100 股整数倍），
        单次买入金额上限为「总资产 * 比例」，但不超过当前可用现金。
        """
        if price <= 0:
            return 0
        asset = self.account.get_asset()
        if not asset:
            return 0
        total_asset = float(asset.get("total_asset", 0) or 0)
        cash = float(asset.get("cash", 0) or 0)
        if total_asset <= 0 or cash <= 0:
            return 0
        ratio = max(min(self.buy_cash_ratio, 1.0), 0.0)
        target_amount = total_asset * ratio
        use_cash = min(target_amount, cash)
        volume = int(use_cash // (price * 100)) * 100
        return max(volume, 0)

    def _has_position(self, stock_code: str) -> bool:
        """
        判断是否已持有指定股票（任意可见数量即视为有持仓，不再加仓）。
        """
        if not getattr(self.account, "_connected", None) or not self.account._connected():
            return False
        for pos in self.account.get_positions():
            if pos.get("stock_code") == stock_code and float(pos.get("volume", 0) or 0) > 0:
                return True
        return False

    def _log_insufficient_cash(self, stock_code: str, price: float) -> None:
        """
        资金不足时按单票节流输出提示日志，避免在高频 tick 下刷屏。
        """
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

    def on_tick(self, tick_data: Dict[str, Any]) -> None:
        """
        每秒回调：基于 tick 与 1 分钟分时数据判断买卖信号并执行下单。
        """
        if not tick_data:
            return

        # 盘中买入只关注“预买入池”中且当前 tick 涨幅已接近涨停的标的，减少分时 K 请求量；
        # 盘中卖出则默认对预卖出池全部监控。
        watch_codes = list(tick_data.keys())

        pre_buy_set = set(self.pre_buy_pool)

        buy_candidates: List[str] = []
        for code in watch_codes:
            if code not in pre_buy_set:
                continue
            tick = tick_data.get(code) or {}
            last_price = float(tick.get("lastPrice") or 0)
            cached = self.cached.get(code) or {}
            pre_close = float(cached.get("pre_close", 0) or 0)
            prev_high = float(cached.get("prev_high_price", 0) or 0)
            if last_price <= 0 or pre_close <= 0 or prev_high <= 0:
                continue
            # 仅当 tick 涨幅已接近涨停，且当前价已大于前高时，才拉取分时 K 做进一步信号判断
            if last_price / pre_close >= (1 + self.limit_near_pct) and last_price > prev_high:
                buy_candidates.append(code)

        # 卖出池默认全部监控，无需根据 tick_data 再做筛选
        sell_candidates = list(self.pre_sell_pool)

        kline_codes = sorted(set(buy_candidates) | set(sell_candidates))
        if not kline_codes:
            return

        # 获取当日所有 1 分钟分时 K 线（买入逻辑内部仍会通过 buy_max_bars 限制时间窗）
        minute_bars = self.data.get_kline_bars(
            kline_codes,
            period="1m",
            start_time=current_date_str(),
            end_time="",
            count=-1,
            field_list=["open", "high", "low", "close", "volume", "amount"],
        )

        buy_set = set(buy_candidates)
        sell_set = set(sell_candidates)

        for code in kline_codes:
            bars = minute_bars.get(code)
            if code in sell_set:
                sell_sig = self.sell_signal(code, bars)
                if sell_sig:
                    self._execute_sell(sell_sig)
            if code in buy_set:
                buy_sig = self.buy_signal(code, bars)
                if buy_sig:
                    self._execute_buy(buy_sig)

    def buy_signal(self, stock_code: str, bars: Optional[pd.DataFrame]) -> Optional[Dict[str, Any]]:
        """
        买入信号：接近涨停 + 低于前高回踩 + 当前价突破前高，且只在 9:30~11:00 时间窗内生效。
        """
        # 若已经有持仓，则本策略不再加仓，与回测版行为保持一致
        if self._has_position(stock_code):
            return None

        if bars is None or bars.empty or stock_code not in self.cached:
            return None
        # 分时根数限制：第 90 根大致对应 11:00
        if len(bars) > self.buy_max_bars:
            return None

        pre_close = float(self.cached[stock_code].get("pre_close", 0) or 0)
        prev_high = float(self.cached[stock_code].get("prev_high_price", 0) or 0)
        if pre_close <= 0 or prev_high <= 0:
            return None

        current_price = float(bars.iloc[-1]["close"])
        intraday_low = float(bars["low"].min())
        limit_threshold = pre_close * (1 + self.limit_near_pct)

        # 开盘价涨幅不能大于 limit_near_pct
        day_open = float(bars.iloc[0]["open"])
        if day_open > limit_threshold:
            return None

        # 当前分时之前不允许已触发过接近涨停
        if len(bars) > 1:
            high_before = float(bars.iloc[:-1]["high"].max())
            if high_before >= limit_threshold:
                return None

        # 1) 当前价是否接近涨停
        if current_price / pre_close < (1 + self.limit_near_pct):
            return None
        # 2) 当日最低价或昨收是否曾低于前高
        if intraday_low >= prev_high and pre_close >= prev_high:
            return None
        # 3) 当前价必须突破前高
        if current_price <= prev_high:
            return None

        volume = self._calc_buy_volume(current_price)
        if volume <= 0:
            return None

        return {
            "action": "buy",
            "stock_code": stock_code,
            "price": current_price,
            "volume": volume,
            "minute_k_count": len(bars),
            "time": bars.index[-1],
            "desc": "突破前高接近涨停买入",
        }

    def _check_macd_top_gate(self, stock_code: str, bars: Optional[pd.DataFrame]) -> Optional[Dict[str, float]]:
        """
        分时 MACD 顶点过滤：出现顶点且满足：
        - 首个顶点；或
        - 当前价低于上一顶点价；或
        - 价格创新高但 MACD 柱未创新高（顶背离）。
        """
        # 注意：xtquant 的 1 分钟 K 线最后一根在当前分钟内会不断更新，这里仅使用“已收盘”的分钟 K
        # 即忽略最后一根正在形成的 K，防止 MACD 顶形态在分钟内抖动。
        if bars is None or bars.empty or len(bars) < self.sell_macd_min_bars + 1:
            return None
        closed_bars = bars.iloc[:-1]
        macd_df = get_macd(closed_bars)
        if not is_macd_top(macd_df):
            return None

        # 用最近一根“已收盘”的分钟 K 作为 MACD 对应的价格
        current_price = float(closed_bars.iloc[-1]["close"])
        current_macd = float(macd_df.iloc[-1]["macd"])
        last_top_price = float(self.cached[stock_code].get("top_price", 0.0) or 0.0)
        last_top_macd = float(self.cached[stock_code].get("top_macd", 0.0) or 0.0)

        is_first_top = last_top_price <= 0
        is_lower_than_last = last_top_price > 0 and current_price < last_top_price
        is_divergence = (
            last_top_price > 0
            and last_top_macd != 0
            and current_price >= last_top_price
            and current_macd < last_top_macd
        )
        if not (is_first_top or is_lower_than_last or is_divergence):
            return None
        return {
            "current_price": current_price,
            "current_macd": current_macd,
            "last_top_price": last_top_price,
            "last_top_macd": last_top_macd,
        }

    def _update_top_cache(self, stock_code: str, top_price: float, top_macd: float) -> None:
        """
        更新分时 MACD 顶点缓存，用于后续顶背离判断。
        """
        self.cached.setdefault(stock_code, {})
        self.cached[stock_code]["top_price"] = float(top_price)
        self.cached[stock_code]["top_macd"] = float(top_macd)

    def _refresh_top_cache_on_macd_top(self, stock_code: str, bars: Optional[pd.DataFrame]) -> None:
        """
        在任意出现分时 MACD 顶点时刷新顶点缓存，用于对齐回测版策略的 on_minute_end 逻辑。
        """
        if stock_code not in self.cached:
            return
        # 仅基于“已收盘”的分钟 K 做 MACD 顶更新，忽略当前正在形成的最后一根 K。
        if bars is None or getattr(bars, "empty", True) or len(bars) < self.sell_macd_min_bars + 1:
            return
        closed_bars = bars.iloc[:-1]
        macd_df = get_macd(closed_bars)
        if is_macd_top(macd_df):
            self._update_top_cache(
                stock_code,
                top_price=float(closed_bars.iloc[-1]["close"]),
                top_macd=float(macd_df.iloc[-1]["macd"]),
            )

    def _get_sell_volume_by_batch(self, stock_code: str, available_volume: int) -> int:
        """
        基于剩余分批次数计算本次应卖出的股数。
        """
        batch_count = int(self.cached[stock_code].get("batch_sell_count", self.batch_sell_count) or self.batch_sell_count)
        batch_count = max(batch_count, 1)
        plan_volume = available_volume // batch_count
        return convert_to_safe_sell_volume(plan_volume, available_volume)

    def _sell_broken_limit(
        self,
        stock_code: str,
        bars: Optional[pd.DataFrame],
        yesterday_close: float,
        available_volume: int,
    ) -> Optional[Dict[str, Any]]:
        """
        炸板清仓：曾触及涨停且当前价显著跌破涨停价，且距离最近一次封板时间超过 sell_broken_limit_gap_minutes。
        """
        if bars is None or bars.empty:
            return None
        current_price = float(bars.iloc[-1]["close"])
        limit_price_up = get_limit_price(stock_code, yesterday_close, "up")
        if limit_price_up is None:
            return None
        # 当日最高价未触及涨停或当前仍在涨停附近，则不视作炸板
        if float(bars["high"].max()) < limit_price_up or current_price >= limit_price_up:
            return None

        # 最近一次收盘在涨停价上的分钟位置
        closed_at_limit = bars.index[bars["close"] >= limit_price_up]
        if len(closed_at_limit) <= 0:
            return None
        last_limit_pos = int(bars.index.get_loc(closed_at_limit[-1]))
        gap = (len(bars) - 1) - last_limit_pos
        if gap < self.sell_broken_limit_gap_minutes:
            return None

        logger.debug("%s 触发炸板清仓 gap=%s", stock_code, gap)
        return {
            "action": "sell",
            "stock_code": stock_code,
            "price": current_price,
            "volume": int(available_volume),
            "minute_k_count": len(bars),
            "time": bars.index[-1],
            "desc": "止盈（炸板清仓）",
        }

    def _sell_batch_on_macd_top(
        self,
        stock_code: str,
        bars: Optional[pd.DataFrame],
        available_volume: int,
        top_ctx: Dict[str, float],
    ) -> Optional[Dict[str, Any]]:
        """
        MACD 首个顶或顶背离触发的分批卖出逻辑。
        """
        if bars is None or bars.empty:
            return None
        sell_volume = self._get_sell_volume_by_batch(stock_code, available_volume)
        if sell_volume <= 0:
            return None

        current_price = float(top_ctx["current_price"])
        current_macd = float(top_ctx["current_macd"])
        # 扣减剩余分批次数
        remain = int(self.cached[stock_code].get("batch_sell_count", self.batch_sell_count) or self.batch_sell_count)
        self.cached[stock_code]["batch_sell_count"] = max(remain - 1, 0)
        self._update_top_cache(stock_code, current_price, current_macd)

        return {
            "action": "sell",
            "stock_code": stock_code,
            "price": current_price,
            "volume": int(sell_volume),
            "minute_k_count": len(bars),
            "time": bars.index[-1],
            "desc": "止盈（MACD 顶 / 顶背离）",
        }

    def sell_signal(self, stock_code: str, bars: Optional[pd.DataFrame]) -> Optional[Dict[str, Any]]:
        """
        卖出信号：
        1) 当前涨停则不卖；
        2) 若炸板，则立即清仓；
        3) 否则按分时 MACD 首个顶 / 顶背离分批卖出。
        """
        if bars is None or bars.empty or stock_code not in self.cached:
            return None

        available_volume = int(self.account.get_available_volume(stock_code))
        if available_volume <= 0:
            return None

        yesterday_close = float(self.cached[stock_code].get("pre_close", 0) or 0)
        current_price = float(bars.iloc[-1]["close"])
        if yesterday_close <= 0:
            return None

        # 当前在涨停价附近则不卖
        if is_limit(stock_code, current_price, yesterday_close):
            return None

        # 炸板清仓优先
        broken_sig = self._sell_broken_limit(stock_code, bars, yesterday_close, available_volume)
        if broken_sig is not None:
            return broken_sig

        # MACD 顶 / 顶背离分批止盈
        top_ctx = self._check_macd_top_gate(stock_code, bars)
        if top_ctx is not None:
            # _sell_batch_on_macd_top 内部已调用 _update_top_cache，直接返回信号
            return self._sell_batch_on_macd_top(stock_code, bars, available_volume, top_ctx)

        # 未触发任何卖出信号，但若当前分钟存在 MACD 顶点，则仅刷新顶点缓存，供后续分钟做背离对比
        self._refresh_top_cache_on_macd_top(stock_code, bars)

        return None

    def _execute_buy(self, signal: Dict[str, Any]) -> None:
        """
        执行买入委托，并记录日志。
        """
        code = signal["stock_code"]
        price = float(signal["price"])
        volume = int(signal["volume"])
        if volume <= 0 or price <= 0:
            return
        order_id = self.trade.buy(code, volume, price, strategy_name=self.name, order_remark=signal.get("desc", ""))
        logger.info(
            "[%s] 买入委托: %s 价=%.2f 量=%s 返回单号=%s",
            self.name,
            code,
            price,
            volume,
            order_id,
        )

    def _execute_sell(self, signal: Dict[str, Any]) -> None:
        """
        执行卖出委托，并记录日志。
        """
        code = signal["stock_code"]
        price = float(signal["price"])
        volume = int(signal["volume"])
        if volume <= 0 or price <= 0:
            return
        order_id = self.trade.sell(code, volume, price, strategy_name=self.name, order_remark=signal.get("desc", ""))
        logger.info(
            "[%s] 卖出委托: %s 价=%.2f 量=%s 返回单号=%s",
            self.name,
            code,
            price,
            volume,
            order_id,
        )

    def on_after_close(self) -> None:
        """
        盘后总结：当前缓存中仍有的分批次数等信息仅做调试打印。
        """
        logger.info("[%s] 盘后总结：缓存股票数量=%s", self.name, len(self.cached))
        for code, info_dict in list(self.cached.items())[:20]:
            logger.debug(
                "[%s] %s pre_close=%.2f prev_high=%.2f batch_left=%s",
                self.name,
                code,
                float(info_dict.get("pre_close", 0) or 0),
                float(info_dict.get("prev_high_price", 0) or 0),
                info_dict.get("batch_sell_count", 0),
            )

