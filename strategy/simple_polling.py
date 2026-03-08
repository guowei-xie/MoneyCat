# -*- coding: utf-8 -*-
"""
示例策略：基于每秒轮询行情，仅做信号逻辑演示（不实际下单）。
"""
from typing import Dict, List, Any
from strategy.base import BaseStrategy
from logging_config import logger


class SimplePollingStrategy(BaseStrategy):
    """
    简单轮询示例：盘前设定股池，盘中每秒拉 tick，仅打印行情与模拟信号，盘后打印总结。
    """

    def __init__(self, config: Any, data_broker: Any, trade_broker: Any, account_broker: Any):
        super().__init__(config, data_broker, trade_broker, account_broker)
        self.stock_pool: List[str] = []   # 预选股池
        self.position_pool: List[str] = [] # 持仓股池（代码）
        self.cache: Dict[str, Any] = {}   # 盘前缓存
        self.tick_count = 0
        self.signal_log: List[str] = []

    def on_init(self) -> None:
        """初始化：读配置、连接行情与交易、更新历史数据。"""
        logger.info("[%s] 初始化开始", self.name)
        # 连接行情（若尚未连接）
        if not self.data.is_connected:
            self.data.connect()
        # 从配置读取账号与路径并连接交易（若配置存在）
        try:
            account_id = self.config.get("ACCOUNT", "ACCOUNT_ID") if hasattr(self.config, "get") else None
            userdata = self.config.get("ACCOUNT", "MINI_QMT_PATH") if hasattr(self.config, "get") else None
            if account_id and userdata and not self.trade.is_connected:
                self.trade.connect()
        except Exception as e:
            logger.warning("[%s] 交易连接跳过（无配置或失败）: %s", self.name, e)
        # 示例：为股池下载近期日线（股池在 on_prepare 里设置后再下载也可）
        logger.info("[%s] 初始化完成", self.name)

    def on_prepare(self) -> None:
        """盘前：设定预选股池、持仓股池、缓存。"""
        logger.info("[%s] 盘前准备开始", self.name)
        # 示例股池：可从配置或板块接口获取，这里写死几只做演示
        self.stock_pool = ["000001.SZ", "600000.SH"]
        self.position_pool = []
        if self.account._connected():
            for p in self.account.get_positions():
                self.position_pool.append(p.get("stock_code", ""))
        self.cache = {"pre_close": {}}
        for code in self.stock_pool:
            detail = self.data.get_instrument_detail(code)
            if detail and "PreClose" in detail:
                self.cache["pre_close"][code] = detail["PreClose"]
        self.tick_count = 0
        self.signal_log = []
        logger.info("[%s] 股池数量=%s 持仓数量=%s", self.name, len(self.stock_pool), len(self.position_pool))

    def get_watch_list(self) -> List[str]:
        """盘中关注列表 = 股池 + 持仓。"""
        return list(set(self.stock_pool + self.position_pool))

    def on_tick(self, tick_data: Dict[str, Any]) -> None:
        """每秒回调：取最新价，演示信号判断（不真实下单）。"""
        self.tick_count += 1
        if not tick_data:
            return
        for code, tick in tick_data.items():
            if not isinstance(tick, dict):
                continue
            last = tick.get("lastPrice")
            if last is None:
                continue
            pre = self.cache.get("pre_close", {}).get(code)
            if pre and pre > 0 and self.tick_count % 10 == 0:
                # 每 10 秒打印一次涨跌幅
                pct = (last - pre) / pre * 100
                logger.debug("[%s] %s 最新价=%.2f 涨跌幅=%.2f%%", self.name, code, last, pct)
            # 示例信号：涨幅 > 2% 记一条“模拟买入”日志（不真实下单）
            if pre and pre > 0 and (last - pre) / pre > 0.02:
                msg = f"模拟信号 买入 {code} 价={last:.2f}"
                if msg not in self.signal_log:
                    self.signal_log.append(msg)
                    logger.info("[%s] %s", self.name, msg)

    def on_after_close(self) -> None:
        """盘后：今日总结。"""
        logger.info("[%s] 盘后总结: 轮询次数=%s 产生信号数=%s", self.name, self.tick_count, len(self.signal_log))
        for s in self.signal_log:
            logger.info("[%s] 信号: %s", self.name, s)
