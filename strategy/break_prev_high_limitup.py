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
from utils.optional import get_tqdm
from utils.time_utils import get_last_bar_hms
from db.trade_store import get_trade_store

tqdm = get_tqdm()


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
        self.buy_cash_ratio = 0.1  # 每次买入最多使用可用资金比例
        self.buy_end_hms = "14:30:00"  # 买入信号截止时间（含），格式 HH:MM:SS

        # ↓ 运行时缓存
        self.trade_calendar: List[str] = []
        self.universe: List[str] = []            # 总股票池
        self.pre_buy_pool: List[str] = []        # 预买入池（盘前预选股）
        self.pre_sell_pool: List[str] = []       # 预卖出池（当前持仓）
        self.cached: Dict[str, Dict[str, Any]] = {}  # 各股票盘前缓存（昨收、前高、卖出状态等）
        # 买入信号跳过缓存：命中“开盘过高/已持仓”后，当日不再做买入信号判断，直接从候选池过滤
        self._buy_skip_cache: Dict[str, str] = {}

        # 收盘前是否已执行过一次“撤掉所有未成交订单”
        self._has_canceled_unfilled_orders_before_close: bool = False

        # ↓ 当日策略统计相关字段（用于盘后摘要）
        self._start_total_asset: float = 0.0
        self._start_market_value: float = 0.0
        self._buy_signal_count: int = 0
        self._sell_signal_count: int = 0
        self._buy_order_count: int = 0
        self._sell_order_count: int = 0

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
            self.buy_cash_ratio = self.config.getfloat(sec, "BUY_CASH_RATIO", fallback=self.buy_cash_ratio)
            self.buy_end_hms = str(self.config.get(sec, "BUY_END_HMS", fallback=self.buy_end_hms) or self.buy_end_hms)
        except Exception as e:
            logger.warning("[%s] 读取 STRATEGY 配置失败: %s", self.name, e)

    def on_init(self) -> None:
        """
        初始化阶段：连接行情 / 交易，加载交易日历。
        """
        logger.info("[%s] 策略初始化开始", self.name)
        self.ensure_data_connected()

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
            self.cached = {}
            return

        # 使用 T-1 作为选股与缓存基准日
        trade_date = self._get_yesterday_trade_date()
        if not trade_date:
            logger.warning("[%s] 无法确定昨日交易日，跳过盘前选股", self.name)
            self.pre_buy_pool = list(self.universe)
            self.pre_sell_pool = []
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
        logger.info(f"{self.name} 盘前预选股完成，预选股池=%s", len(self.pre_buy_pool))

        # 2) 获取当前持仓，构建预卖出池
        holding_codes = {p.get("stock_code") for p in self.account.get_positions()} if self.account._connected() else set()
        raw_pre_sell = sorted(code for code in holding_codes if code)
        # 仅对“预卖出池（持仓）”做可交易过滤，规避停牌/非股票标的等特殊情况
        self.pre_sell_pool = self.data.filter_tradeable_stock_codes(raw_pre_sell, tag="pre_sell_pool")
        # 已持仓标的：当日不再做买入信号判断，直接加入跳过缓存
        for code in self.pre_sell_pool:
            self._buy_skip_cache[code] = "has_position"

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

        # 盘中若发生系统重启，仅订阅可能无法补齐“当日已产生”的历史分时。
        # 这里在确认处于交易时段后，补全下载当日 1 分钟历史，避免后续取分时信号缺失。
        if cache_pool:
            self.data.ensure_today_kline_history(cache_pool, period="1m", only_if_trading_time=True)

        logger.info(
            "[%s] 盘前准备完成，股票池总数=%s，预买池=%s，预卖池=%s",
            self.name,
            len(self.universe),
            len(self.pre_buy_pool),
            len(self.pre_sell_pool),
        )
        logger.info("[%s] 正在等待开盘/信号...", self.name)
        self._notify_prepare_account_summary()

    def _notify_prepare_account_summary(self) -> None:
        """
        盘前准备结束后推送账户摘要信息，减少盯盘负担。

        推送内容包含：
        - 总资金（total_asset）
        - 持仓金额（market_value）
        - 预卖数量（预卖池数量，口径与盘前日志一致）
        - 预选数量（预买池数量）
        """
        total_asset = 0.0
        market_value = 0.0
        pre_sell_count = len(self.pre_sell_pool)
        preselect_count = len(self.pre_buy_pool)

        try:
            if getattr(self.account, "_connected", None) and self.account._connected():
                asset = self.account.get_asset() or {}
                total_asset = float(asset.get("total_asset", 0) or 0)
                market_value = float(asset.get("market_value", 0) or 0)

            # 记录日初资金概览，供盘后统计使用
            self._start_total_asset = total_asset
            self._start_market_value = market_value

            msg = (
                f"【提示】盘前准备完成：总资金 {total_asset:.2f} "
                f"持仓金额{market_value:.2f} 持仓数量{pre_sell_count}只 预选数量{preselect_count}只"
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


    def on_tick(self, tick_data: Dict[str, Any]) -> None:
        """
        每秒回调：基于 tick 与 1 分钟分时数据判断买卖信号并执行下单。
        """
        if not tick_data:
            return

        # 收盘前 14:55 撤掉所有未成交委托（仅执行一次）
        now_hms = time.strftime("%H:%M:%S", time.localtime())
        if (
            not self._has_canceled_unfilled_orders_before_close
            and "14:55:00" <= now_hms <= "14:59:59"
        ):
            # 使用基类提供的通用批量撤单能力，仅在收盘前通知一次
            self.cancel_all_unfilled_orders(notify=True)
            self._has_canceled_unfilled_orders_before_close = True

        buy_end_hms = self.buy_end_hms if isinstance(self.buy_end_hms, str) and len(self.buy_end_hms) == 8 else "11:00:00"
        # 买入时段：仅 09:30~buy_end_hms 内才收集买候选、拉分时、做买入信号判断
        in_buy_window = "09:30:00" <= now_hms <= buy_end_hms

        # 盘中买入只关注“预买入池”中且当前 tick 涨幅已接近涨停的标的，减少分时 K 请求量；
        # 盘中卖出则默认对预卖出池全部监控。
        tick_count = len(tick_data)
        pre_buy_set = set(self.pre_buy_pool)
        skip_cache = self._buy_skip_cache

        buy_candidates: List[str] = []
        if in_buy_window:
            for code, tick in tick_data.items():
                if code not in pre_buy_set:
                    continue
                if code in skip_cache:
                    continue
                last_price = float((tick or {}).get("lastPrice") or 0)
                cached = self.cached.get(code) or {}
                pre_close = float(cached.get("pre_close", 0) or 0)
                prev_high = float(cached.get("prev_high_price", 0) or 0)
                if last_price <= 0 or pre_close <= 0 or prev_high <= 0:
                    continue
                # 仅当 tick 涨幅已接近涨停，且当前价已大于前高时，才拉取分时 K 做进一步信号判断
                if last_price / pre_close >= (1 + self.limit_near_pct) and last_price > prev_high:
                    buy_candidates.append(code)
                    self._log_throttled(
                        f"buy_candidate:{code}",
                        "debug",
                        "[%s] 盘中候选: %s last=%.2f pre_close=%.2f涨幅=%.4f prev_high=%.2f",
                        self.name,
                        code,
                        last_price,
                        pre_close,
                        (last_price / pre_close - 1) if pre_close > 0 else 0.0,
                        prev_high,
                        interval_sec=15,
                    )

        # 卖出池默认全部监控
        sell_candidates = list(self.pre_sell_pool)

        kline_codes = sorted(set(buy_candidates) | set(sell_candidates))
        if not kline_codes:
            return

        self._log_throttled(
            "tick_summary",
            "debug",
            "[%s] 盘中轮询: tick=%s 预买池=%s 预卖池=%s 买候选=%s 卖监控=%s 拉取1mK=%s",
            self.name,
            tick_count,
            len(self.pre_buy_pool),
            len(self.pre_sell_pool),
            len(buy_candidates),
            len(sell_candidates),
            len(kline_codes),
            interval_sec=20,
        )

        # 获取当日所有 1 分钟分时 K 线
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
        买入信号：接近涨停 + 低于前高回踩 + 当前价突破前高。
        调用方（on_tick）保证仅在 09:30~11:00 时段内调用本方法。
        """
        if stock_code in self._buy_skip_cache:
            return None

        if bars is None or bars.empty or stock_code not in self.cached:
            return None

        # 若该标的已有未完全成交买单，直接跳过，避免高频 tick 下重复发送买入委托
        if self.has_unfinished_buy_order(stock_code):
            self._log_throttled(
                f"buy_reject_pending_order:{stock_code}",
                "debug",
                "[%s] 买入跳过(已有未成交买单): %s",
                self.name,
                stock_code,
                interval_sec=30,
            )
            return None

        # 若已经有持仓，则本策略不再加仓，与回测版行为保持一致
        if self.has_position(stock_code):
            self._buy_skip_cache[stock_code] = "has_position"
            self._log_throttled(
                f"buy_reject_has_pos:{stock_code}",
                "debug",
                "[%s] 买入跳过(已持仓): %s",
                self.name,
                stock_code,
                interval_sec=60,
            )
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
            self._buy_skip_cache[stock_code] = "open_too_high"
            self._log_throttled(
                f"buy_reject_open_high:{stock_code}",
                "debug",
                "[%s] 买入跳过(开盘过高): %s open=%.2f threshold=%.2f pre_close=%.2f",
                self.name,
                stock_code,
                day_open,
                limit_threshold,
                pre_close,
                interval_sec=30,
            )
            return None

        # 当前分时之前不允许已触发过接近涨停
        if len(bars) > 1:
            high_before = float(bars.iloc[:-1]["high"].max())
            if high_before >= limit_threshold:
                self._buy_skip_cache[stock_code] = "hit_before"
                self._log_throttled(
                    f"buy_reject_hit_before:{stock_code}",
                    "debug",
                    "[%s] 买入跳过(此前已接近涨停): %s high_before=%.2f threshold=%.2f",
                    self.name,
                    stock_code,
                    high_before,
                    limit_threshold,
                    interval_sec=30,
                )
                return None

        # 1) 当前价是否接近涨停
        if current_price / pre_close < (1 + self.limit_near_pct):
            return None
        # 2) 当日最低价或昨收是否曾低于前高
        if intraday_low >= prev_high and pre_close >= prev_high:
            self._buy_skip_cache[stock_code] = "no_pullback"
            self._log_throttled(
                f"buy_reject_no_pullback:{stock_code}",
                "debug",
                "[%s] 买入跳过(无回踩): %s low=%.2f pre_close=%.2f prev_high=%.2f",
                self.name,
                stock_code,
                intraday_low,
                pre_close,
                prev_high,
                interval_sec=30,
            )
            return None
        # 3) 当前价必须突破前高
        if current_price <= prev_high:
            return None

        volume = self.calc_buy_volume_by_ratio(
            price=float(current_price),
            cash_ratio=float(self.buy_cash_ratio),
            lot_size=100,
        )
        if volume <= 0:
            # 首次可买量为 0，尝试撤掉最早未成交买单以释放资金（基类通用能力）
            released = self.cancel_earliest_unfilled_buy_order()
            if not released:
                self.log_insufficient_cash(stock_code, current_price)
                self._log_throttled(
                    f"buy_reject_volume0:{stock_code}",
                    "debug",
                    "[%s] 买入跳过(可买量为0): %s price=%.2f total_asset/cash不足或比例过低",
                    self.name,
                    stock_code,
                    current_price,
                    interval_sec=60,
                )
                return None

            # 撤单成功后重新计算一次可买量（若资金尚未完全释放，可能仍为 0）
            volume = self.calc_buy_volume_by_ratio(
                price=float(current_price),
                cash_ratio=float(self.buy_cash_ratio),
                lot_size=100,
            )
            if volume <= 0:
                self.log_insufficient_cash(stock_code, current_price)
                self._log_throttled(
                    f"buy_reject_volume0_after_cancel:{stock_code}",
                    "debug",
                    "[%s] 买入跳过(撤单后仍可买量为0): %s price=%.2f total_asset/cash不足或比例过低",
                    self.name,
                    stock_code,
                    current_price,
                    interval_sec=60,
                )
                return None

        limit_up_price = get_limit_price(stock_code, pre_close, "up")
        order_price = float(limit_up_price) if limit_up_price is not None else current_price

        self._log_throttled(
            f"buy_signal:{stock_code}",
            "debug",
            "[%s] 买入信号: %s order_price=%.2f last_price=%.2f low=%.2f prev_high=%.2f pre_close=%.2f threshold=%.2f vol=%s k=%s",
            self.name,
            stock_code,
            order_price,
            current_price,
            intraday_low,
            prev_high,
            pre_close,
            limit_threshold,
            volume,
            len(bars),
            interval_sec=10,
        )
        # 统计买入信号次数
        self._buy_signal_count += 1
        return {
            "action": "buy",
            "stock_code": stock_code,
            "price": order_price,
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
            new_top_price = float(closed_bars.iloc[-1]["close"])
            new_top_macd = float(macd_df.iloc[-1]["macd"])
            old_top_price = float(self.cached.get(stock_code, {}).get("top_price", 0.0) or 0.0)
            old_top_macd = float(self.cached.get(stock_code, {}).get("top_macd", 0.0) or 0.0)
            self._update_top_cache(
                stock_code,
                top_price=new_top_price,
                top_macd=new_top_macd,
            )
            self._log_throttled(
                f"macd_top_refresh:{stock_code}",
                "debug",
                "[%s] MACD顶点刷新: %s new_top=(%.2f,%.6f) old_top=(%.2f,%.6f) closed_k=%s",
                self.name,
                stock_code,
                new_top_price,
                new_top_macd,
                old_top_price,
                old_top_macd,
                len(closed_bars),
                interval_sec=20,
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

        self._log_throttled(
            f"sell_broken:{stock_code}",
            "debug",
            "[%s] 触发炸板清仓: %s current=%.2f limit_up=%.2f gap(min)=%s high_max=%.2f",
            self.name,
            stock_code,
            current_price,
            float(limit_price_up),
            gap,
            float(bars["high"].max()),
            interval_sec=10,
        )
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

        self._log_throttled(
            f"sell_macd_top:{stock_code}",
            "debug",
            "[%s] 分批止盈(MACD顶/背离): %s price=%.2f macd=%.6f 卖量=%s 可卖=%s batch_remain=%s last_top=(%.2f,%.6f)",
            self.name,
            stock_code,
            current_price,
            current_macd,
            int(sell_volume),
            int(available_volume),
            int(self.cached[stock_code].get("batch_sell_count", 0) or 0),
            float(top_ctx.get("last_top_price", 0.0) or 0.0),
            float(top_ctx.get("last_top_macd", 0.0) or 0.0),
            interval_sec=10,
        )
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

        # 卖出信号时间窗控制：仅在 09:30~14:55 内产生新的卖出信号
        last_hms = get_last_bar_hms(bars)
        if last_hms is not None and (
            last_hms < "09:30:00" or last_hms > "14:55:00"
        ):
            self._log_throttled(
                f"sell_reject_timewin:{stock_code}",
                "debug",
                "[%s] 卖出跳过(超出时间窗): %s time=%s",
                self.name,
                stock_code,
                last_hms,
                interval_sec=60,
            )
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
            self._log_throttled(
                f"sell_skip_limit:{stock_code}",
                "debug",
                "[%s] 卖出跳过(涨停不卖): %s price=%.2f pre_close=%.2f",
                self.name,
                stock_code,
                current_price,
                yesterday_close,
                interval_sec=30,
            )
            return None

        # 炸板清仓优先
        broken_sig = self._sell_broken_limit(stock_code, bars, yesterday_close, available_volume)
        if broken_sig is not None:
            # 统计卖出信号次数
            self._sell_signal_count += 1
            return broken_sig

        # MACD 顶 / 顶背离分批止盈
        top_ctx = self._check_macd_top_gate(stock_code, bars)
        if top_ctx is not None:
            # _sell_batch_on_macd_top 内部已调用 _update_top_cache，直接返回信号
            sig = self._sell_batch_on_macd_top(stock_code, bars, available_volume, top_ctx)
            if sig is not None:
                self._sell_signal_count += 1
            return sig

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
        order_id = self.place_buy_order(code, volume, price, remark=str(signal.get("desc", "") or ""))
        if order_id is None:
            return
        # 统计买入委托次数
        self._buy_order_count += 1
        self._log_throttled(
            f"order_buy:{code}",
            "debug",
            "[%s] 买入回执: %s order_id=%s signal=%s",
            self.name,
            code,
            order_id,
            signal,
            interval_sec=10,
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
        order_id = self.place_sell_order(code, volume, price, remark=str(signal.get("desc", "") or ""))
        if order_id is None:
            return
        # 统计卖出委托次数
        self._sell_order_count += 1
        self._log_throttled(
            f"order_sell:{code}",
            "debug",
            "[%s] 卖出回执: %s order_id=%s signal=%s",
            self.name,
            code,
            order_id,
            signal,
            interval_sec=10,
        )

    def on_after_close(self) -> None:
        """
        盘后总结：当前缓存中仍有的分批次数等信息仅做调试打印。
        """
        logger.info("[%s] 盘后总结开始", self.name)

        # 盘后再次获取账户资产与持仓，用于生成当日策略摘要
        end_total_asset = 0.0
        end_market_value = 0.0
        positions: List[Dict[str, Any]] = []
        try:
            if getattr(self.account, "_connected", None) and self.account._connected():
                asset = self.account.get_asset() or {}
                end_total_asset = float(asset.get("total_asset", 0) or 0)
                end_market_value = float(asset.get("market_value", 0) or 0)
                positions = self.account.get_positions() or []
        except Exception as exc:
            logger.warning("[%s] 盘后获取账户信息失败: %s", self.name, exc)

        # 计算当日盈亏与收益率
        pnl = 0.0
        pnl_pct_str = "N/A"
        if self._start_total_asset > 0:
            pnl = end_total_asset - self._start_total_asset
            pnl_pct = pnl / self._start_total_asset
            pnl_pct_str = f"{pnl_pct:.2%}"

        # 统计“当日新建仓数量”：使用本地 SQLite 成交记录 TRADE + BUY 去重股票代码
        new_position_count = 0
        try:
            store = get_trade_store()
            if store is not None:
                new_position_count = int(
                    store.get_daily_new_position_count(
                        date_yyyymmdd=current_date_str(),
                        strategy_name=self.name,
                    )
                    or 0
                )
        except Exception as exc:
            logger.warning("[%s] 读取当日新建仓数量失败，将按 0 处理: %s", self.name, exc)
            new_position_count = 0

        account_id = ""
        try:
            account_id = str(getattr(self.trade, "account_id", "") or "")
        except Exception:
            account_id = ""

        # 组装精简版策略执行摘要
        hold_count = len(positions)
        msg = (
            f"【策略日结】{self.name}\n"
            f"日期：{current_date_str()}\n"
            f"账号：{account_id or '-'}\n"
            "------------------------------\n"
            "资金概览：\n"
            f"- 日初总资产：{self._start_total_asset:.2f}\n"
            f"- 日末总资产：{end_total_asset:.2f}\n"
            f"- 当日盈亏：{pnl:.2f}（{pnl_pct_str}）\n"
            "\n"
            "交易统计：\n"
            f"- 新建仓：{new_position_count} 只\n"
            f"- 买入信号：{self._buy_signal_count}\n"
            f"- 卖出信号：{self._sell_signal_count}\n"
            f"- 买入委托：{self._buy_order_count}\n"
            f"- 卖出委托：{self._sell_order_count}\n"
            "\n"
            "股票池概览：\n"
            f"- 预选股票：{len(self.pre_buy_pool)}\n"
            f"- 预卖股票：{len(self.pre_sell_pool)}\n"
            f"- 当前持仓：{hold_count}\n"
        )

        logger.info("[%s] 当日策略执行摘要：\n%s", self.name, msg.replace("\n", " | "))
        try:
            feishu_send_text(msg)
        except Exception as exc:
            logger.warning("[%s] 盘后策略摘要飞书推送失败: %s", self.name, exc)

        logger.info("[%s] 盘后总结完成", self.name)

