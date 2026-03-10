# -*- coding: utf-8 -*-
"""
行情数据模块：从 QMT(xtdata) 获取、读取、简单清洗行情数据。
"""
from typing import List, Optional, Dict, Any, Tuple

import pandas as pd

from utils.path import ensure_project_root_on_path
from utils.optional import get_tqdm

# 项目根目录加入 path，保证可引用 xtquant
ensure_project_root_on_path(__file__, levels_up=2)

from xtquant import xtdata
from logging_config import logger
from utils.common import add_stock_suffix, add_stock_suffix_list
from utils.universe import filter_main_board, is_st_name, is_delisting_name

tqdm = get_tqdm()


class DataBroker:
    """
    行情数据代理：连接 xtdata、下载历史、获取 K 线与全推 tick、简单清洗。
    """

    def __init__(self, ip: str = "", port=None):
        """
        :param ip: xtdata 服务 IP，空则本地
        :param port: 端口，None 则自动扫描
        """
        self._ip = ip
        self._port = port
        self._connected = False
        self._main_board_universe: List[str] = []
        self._instrument_cache: Dict[str, Dict[str, Any]] = {}

    @property
    def main_board_universe(self) -> List[str]:
        """
        主板股票池缓存（沪深A股主板）。

        约定：由主入口统一初始化，策略与其它模块只读取，不重复构建。
        """
        return list(self._main_board_universe)

    def connect(self) -> bool:
        """连接 xtdata 服务（MiniQMT 行情）。"""
        try:
            xtdata.connect(self._ip, self._port)
            self._connected = True
            logger.info("行情数据(xtdata)连接成功")
            return True
        except Exception as e:
            logger.error("行情数据(xtdata)连接失败: %s", e)
            self._connected = False
            return False

    @property
    def is_connected(self) -> bool:
        """是否已连接。"""
        try:
            if self._connected and xtdata.get_client() and xtdata.get_client().is_connected():
                return True
        except Exception:
            pass
        self._connected = False
        return False

    def download_history(
        self,
        stock_list: List[str],
        period: str = "1d",
        start_time: str = "",
        end_time: str = "",
        incrementally: bool = True,
        progress_bar: bool = True,
    ) -> None:
        """
        下载历史行情到本地，供 get_market_data 使用。

        :param stock_list: 股票代码列表（可带或不带后缀）
        :param period: 周期 1d/1m/5m 等
        :param start_time: 起始 YYYYMMDD
        :param end_time: 结束 YYYYMMDD
        :param incrementally: 是否增量
        :param progress_bar: 是否显示进度条（依赖 tqdm，未安装时自动关闭）
        """
        codes = add_stock_suffix_list(stock_list)
        iterator = tqdm(codes, desc="下载历史数据", ncols=100, unit="只") if progress_bar else codes
        for code in iterator:
            try:
                xtdata.download_history_data(
                    code, period=period,
                    start_time=start_time, end_time=end_time,
                    incrementally=incrementally,
                )
            except Exception as e:
                logger.warning("下载历史数据失败 %s: %s", code, e)
        logger.info("历史数据下载完成，共 %d 只", len(codes))

    def get_market_data(
        self,
        stock_list: List[str],
        period: str = "1d",
        start_time: str = "",
        end_time: str = "",
        count: int = -1,
        dividend_type: str = "none",
        fill_data: bool = True,
    ) -> Dict[str, Any]:
        """
        从缓存/本地获取 K 线数据。

        :param stock_list: 股票代码列表
        :param period: 1d/1m/5m 等
        :param start_time: 起始时间
        :param end_time: 结束时间
        :param count: 条数，-1 表示全部
        :param dividend_type: 复权
        :param fill_data: 是否填充缺失
        :return: 字段名为 key、DataFrame 为 value 的 dict（index=股票，columns=时间）
        """
        codes = add_stock_suffix_list(stock_list)
        if not codes:
            return {}
        try:
            data = xtdata.get_market_data(
                field_list=[],
                stock_list=codes,
                period=period,
                start_time=start_time,
                end_time=end_time,
                count=count,
                dividend_type=dividend_type,
                fill_data=fill_data,
            )
            return self._clean_market_data(data, codes) if data else {}
        except Exception as e:
            logger.error("get_market_data 失败: %s", e)
            return {}

    def _clean_market_data(self, data: Dict, stock_list: List[str]) -> Dict:
        """简单清洗：去除全 NaN 的列（可选），此处仅做透传。"""
        return data

    def get_kline_bars(
        self,
        stock_list: List[str],
        period: str = "1d",
        start_time: str = "",
        end_time: str = "",
        count: int = -1,
        field_list: Optional[List[str]] = None,
    ) -> Dict[str, pd.DataFrame]:
        """
        获取按股票聚合的 K 线数据（基于 xtdata.get_market_data_ex）。

        :param stock_list: 股票代码列表
        :param period: 周期，如 '1d' / '1m'
        :param start_time: 起始时间（YYYYMMDD 或 YYYYMMDDHHMMSS）
        :param end_time: 结束时间
        :param count: 最大条数，-1 表示全部
        :param field_list: 需要的字段列表，None 或空则为全部字段
        :return: { '000001.SZ': DataFrame(columns=['open','high','low','close',...], index=时间) }
        """
        codes = add_stock_suffix_list(stock_list)
        if not codes:
            return {}
        try:
            # 对于 K 线周期，xtdata.get_market_data_ex 返回结构：
            # { stock_code: DataFrame(index=时间, columns=字段) }
            raw = xtdata.get_market_data_ex(
                field_list=field_list or [],
                stock_list=codes,
                period=period,
                start_time=start_time,
                end_time=end_time,
                count=count,
            )
        except Exception as e:
            logger.error("get_kline_bars 失败: %s", e)
            return {}
        if not raw or not isinstance(raw, dict):
            return {}

        result: Dict[str, pd.DataFrame] = {}
        for code in codes:
            df = raw.get(code)
            if isinstance(df, pd.DataFrame) and not df.empty:
                # 直接复用 xtdata 返回的结构：index 为时间，列为字段
                result[code] = df
        return result

    def get_full_tick(self, stock_list: List[str]) -> Dict[str, Dict]:
        """
        获取全推 tick（实时快照）。

        :param stock_list: 股票代码列表
        :return: { stock_code: { lastPrice, volume, ... } }
        """
        codes = add_stock_suffix_list(stock_list)
        if not codes:
            return {}
        try:
            raw = xtdata.get_full_tick(codes)
            return raw if isinstance(raw, dict) else {}
        except Exception as e:
            logger.error("get_full_tick 失败: %s", e)
            return {}

    def subscribe_kline(
        self,
        stock_list: List[str],
        period: str = "1m",
        start_time: str = "",
        end_time: str = "",
        count: int = -1,
    ) -> None:
        """
        订阅分时 / K 线数据，使 xtdata 本地缓存自动更新。

        :param stock_list: 股票代码列表
        :param period: 周期，如 '1m' / '5m' / '1d'
        :param start_time: 起始时间，可留空
        :param end_time: 结束时间，可留空
        :param count: 补历史条数，-1 表示全部
        """
        codes = add_stock_suffix_list(stock_list)
        for code in codes:
            try:
                xtdata.subscribe_quote(
                    stock_code=code,
                    period=period,
                    start_time=start_time,
                    end_time=end_time,
                    count=count,
                    callback=None,
                )
            except Exception as e:
                logger.warning("订阅 K 线失败 %s: %s", code, e)

    def ensure_today_kline_history(
        self,
        stock_list: List[str],
        period: str = "1m",
        *,
        only_if_trading_time: bool = True,
        incrementally: bool = True,
        show_progress: bool = True,
    ) -> None:
        """
        确保“当日指定周期 K 线历史”已下载到本地缓存（常用于盘中重启后的分钟线回补）。

        设计意图：
        - `subscribe_kline` 负责让 xtdata 持续增量更新；
          但盘中重启场景下，仅订阅可能无法补齐“当日已产生”的历史分时缺口。
        - 这里显式触发一次 `download_history_data`，将当日历史补到本地缓存，
          供后续 `get_kline_bars(period=...)` 稳定读取。

        :param stock_list: 股票代码列表（可带或不带后缀）
        :param period: 周期，如 '1m' / '5m' / '1d'
        :param only_if_trading_time: True 则仅在交易时段内执行（默认）
        :param incrementally: 是否增量下载（默认 True）
        :param show_progress: 是否显示下载进度条（默认 True）
        """
        codes = add_stock_suffix_list(list(stock_list or []))
        if not codes:
            return

        if only_if_trading_time:
            import time as _time

            # 仅在 09:30~15:00 时段内进行当日分时回补（不中断午休时段），
            # 以覆盖盘中重启但当前处于休市时间的场景。
            now_hms = _time.strftime("%H:%M:%S", _time.localtime())
            if now_hms < "09:30:00" or now_hms > "15:00:00":
                return

        from utils.common import current_date_str
        today = current_date_str()
        try:
            logger.info("盘中回补当日K线历史开始：period=%s stocks=%s date=%s", period, len(codes), today)
            self.download_history(
                stock_list=codes,
                period=period,
                start_time=today,
                end_time=today,
                incrementally=incrementally,
                progress_bar=show_progress,
            )
            logger.info("盘中回补当日K线历史完成：period=%s stocks=%s date=%s", period, len(codes), today)
        except Exception as exc:
            logger.warning("盘中回补当日K线历史失败：period=%s date=%s err=%s", period, today, exc)

    def get_latest_price(self, stock_code: str) -> Optional[float]:
        """获取单只股票最新价，失败返回 None。"""
        code = add_stock_suffix(stock_code)
        ticks = self.get_full_tick([code])
        if code in ticks and isinstance(ticks[code], dict):
            return ticks[code].get("lastPrice")
        return None

    def get_instrument_detail(self, stock_code: str, iscomplete: bool = False) -> Optional[Dict]:
        """获取合约基础信息。"""
        code = add_stock_suffix(stock_code)
        try:
            cache_key = f"{code}|{int(bool(iscomplete))}"
            if cache_key in self._instrument_cache:
                return self._instrument_cache[cache_key]
            info = xtdata.get_instrument_detail(code, iscomplete)
            if isinstance(info, dict):
                self._instrument_cache[cache_key] = info
            return info
        except Exception as e:
            logger.warning("get_instrument_detail %s: %s", code, e)
            return None

    def get_instrument_type(self, stock_code: str) -> Optional[Dict[str, bool]]:
        """
        获取合约类型信息（如 stock/fund/etf/index 等）。

        :param stock_code: 合约代码（可带或不带后缀）
        :return: 形如 {'stock': True, 'fund': False, ...} 的字典；失败返回 None
        """
        code = add_stock_suffix(stock_code)
        try:
            info = xtdata.get_instrument_type(code)
            return info if isinstance(info, dict) else None
        except Exception as e:
            logger.warning("get_instrument_type %s: %s", code, e)
            return None

    def filter_tradeable_stock_codes(self, stock_list: List[str], tag: str = "stock_pool") -> List[str]:
        """
        过滤出“可交易的沪深股票标的”列表（用于预卖池等小规模列表）。

        主要规避的特殊情况：
        - 停牌：InstrumentStatus >= 1 视为停牌
        - 不可交易：IsTrading 为 False（集合竞价/退市/到期等情况下可能为 False）
        - 非股票标的：get_instrument_type 返回 stock!=True（如 ETF/基金/指数/期货等）
        - 非沪深A股：交易所后缀不为 .SH/.SZ（如 .BJ 或其他市场）

        :param stock_list: 股票代码列表（可带或不带后缀）
        :param tag: 日志标签，便于区分来源（如 'pre_sell_pool'）
        :return: 过滤后的股票代码列表（带后缀）
        """
        codes = add_stock_suffix_list(list(stock_list or []))
        if not codes:
            return []

        ok: List[str] = []
        drop = 0
        bad_format = 0
        non_stock = 0
        suspended = 0
        not_trading = 0
        non_a_share = 0
        no_detail = 0
        no_type = 0

        for raw in codes:
            code = str(raw or "").strip()
            if not code or "." not in code:
                bad_format += 1
                drop += 1
                continue

            # 仅保留沪深标的，避免把北交所/其他市场带进策略（按你当前策略主板池的口径）
            if not (code.endswith(".SH") or code.endswith(".SZ")):
                non_a_share += 1
                drop += 1
                continue

            detail = self.get_instrument_detail(code, iscomplete=False)
            if not isinstance(detail, dict) or not detail:
                no_detail += 1
                drop += 1
                continue

            # 是否可交易（xtdata 直接给出）
            # 注意：部分环境下盘前/非交易时段 IsTrading 可能为 False，直接剔除会误伤预卖池；
            # 因此仅在交易时段内将 IsTrading==False 作为剔除条件。
            try:
                from utils.common import is_trading_time
                in_session = bool(is_trading_time())
            except Exception:
                in_session = False
            is_trading = detail.get("IsTrading", True)
            if in_session and is_trading is False:
                not_trading += 1
                drop += 1
                continue

            # 停牌状态：>=1 视为停牌（与当前项目主板池过滤保持一致）
            instrument_status = detail.get("InstrumentStatus", 0)
            try:
                instrument_status_int = int(instrument_status)
            except Exception:
                instrument_status_int = 0
            if instrument_status_int >= 1:
                suspended += 1
                drop += 1
                continue

            inst_type = self.get_instrument_type(code)
            if not isinstance(inst_type, dict) or not inst_type:
                no_type += 1
                drop += 1
                continue
            if not bool(inst_type.get("stock", False)):
                non_stock += 1
                drop += 1
                continue

            ok.append(code)

        logger.info(
            "[%s] 过滤可交易股票：in=%s ok=%s drop=%s bad_format=%s non_stock=%s suspended=%s not_trading=%s non_a_share=%s no_detail=%s no_type=%s",
            tag,
            len(codes),
            len(ok),
            drop,
            bad_format,
            non_stock,
            suspended,
            not_trading,
            non_a_share,
            no_detail,
            no_type,
        )
        return ok

    def get_stock_list_in_sector(self, sector_name: str) -> List[str]:
        """
        获取板块成分股列表。

        :param sector_name: 板块名称，如 '沪深A股'
        :return: 股票代码列表（带后缀）
        """
        try:
            codes = xtdata.get_stock_list_in_sector(sector_name)
            return add_stock_suffix_list(list(codes or []))
        except Exception as e:
            logger.warning("get_stock_list_in_sector(%s) 失败: %s", sector_name, e)
            return []

    def _get_stock_flags(self, stock_code: str) -> Tuple[str, bool, bool, bool]:
        """
        获取股票筛选所需的基础标记：名称、是否ST、是否停牌、是否退市。

        判定规则（对齐 QuantLab-Real 思路，尽量用 xtdata 合约信息替代）：
        - ST：名称包含 'ST' 或 '*ST'
        - 退市：名称包含 '退市'（保守判定）
        - 停牌：InstrumentStatus >= 1 视为停牌；InstrumentStatus <= 0 视为正常交易（-1 表示复牌）
        """
        info = self.get_instrument_detail(stock_code, iscomplete=False) or {}
        name = str(info.get("InstrumentName") or "")
        is_st = is_st_name(name)
        is_delisting = is_delisting_name(name)

        instrument_status = info.get("InstrumentStatus", 0)
        try:
            instrument_status_int = int(instrument_status)
        except Exception:
            instrument_status_int = 0
        suspended = instrument_status_int >= 1

        return name, is_st, suspended, is_delisting

    def get_stock_list_in_main_board(self) -> List[str]:
        """
        获取沪深A股主板股票池（主板、非ST、非停牌、非退市）。

        参考逻辑：QuantLab-Real/laboratory/pool.py + laboratory/utils.py
        - 先取沪深A股全体成分股
        - 过滤主板
        - 再过滤：非ST、非停牌、非退市

        :return: 主板股票代码列表（带后缀）
        """
        if self._main_board_universe:
            return list(self._main_board_universe)
        stocks = self.get_stock_list_in_sector("沪深A股")
        main_board = filter_main_board(stocks)

        result: List[str] = []
        # 先按代码过滤主板后，再查合约信息过滤 ST/停牌/退市，减少请求量
        for code in main_board:
            _, is_st, is_suspended, is_delisting = self._get_stock_flags(code)
            if is_st or is_suspended or is_delisting:
                continue
            result.append(code)

        self._main_board_universe = result
        return list(self._main_board_universe)
