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
