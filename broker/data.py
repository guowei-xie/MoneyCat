# -*- coding: utf-8 -*-
"""
行情数据模块：从 QMT(xtdata) 获取、读取、简单清洗行情数据。
"""
from typing import List, Optional, Dict, Any
import sys
import os

# 项目根目录加入 path，保证可引用 xtquant
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from xtquant import xtdata
from logging_config import logger
from utils.common import add_stock_suffix, add_stock_suffix_list

try:
    from tqdm import tqdm
except ImportError:
    def tqdm(iterable, **kwargs):
        return iterable


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
            return xtdata.get_instrument_detail(code, iscomplete)
        except Exception as e:
            logger.warning("get_instrument_detail %s: %s", code, e)
            return None
