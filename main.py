# -*- coding: utf-8 -*-
"""
MoneyCat 主入口：初始化 → 盘前准备 → 盘中交易 → 盘后总结。

运行前请先：
1. 将 config.ini.example 复制为 config.ini 并填写账号与 MiniQMT 路径
2. 启动 MiniQMT（或 QMT 投研版）并登录
3. 在交易日运行（非交易日仅做框架演示可注释掉交易日检查）
"""
import os
import sys

# 保证项目根在 path 中
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from configparser import ConfigParser
from logging_config import logger, setup_logger
from utils.common import is_trading_day
from broker.data import DataBroker
from broker.trade import TradeBroker
from broker.account import AccountBroker
from strategy.simple_polling import SimplePollingStrategy


def load_config(config_path: str = "config.ini") -> ConfigParser:
    """加载配置，若文件不存在则返回空配置。"""
    cfg = ConfigParser()
    if os.path.isfile(config_path):
        cfg.read(config_path, encoding="utf-8")
    else:
        logger.warning("未找到 %s，将使用默认/空配置", config_path)
    return cfg


def main() -> None:
    # 1) 读取配置与日志级别
    config = load_config()
    log_level = config.get("LOG", "LEVEL", fallback="INFO")
    setup_logger(level=log_level)

    # 2) 可选：非交易日直接退出（演示时可注释）
    if not is_trading_day():
        logger.info("当前不是交易日，程序退出。若仅演示框架可注释本段。")
        return

    # 3) 初始化：创建 broker 并连接
    data_broker = DataBroker()
    account_id = config.get("ACCOUNT", "ACCOUNT_ID", fallback="")
    mini_qmt_path = config.get("ACCOUNT", "MINI_QMT_PATH", fallback="")
    trade_broker = TradeBroker(mini_qmt_path, account_id)
    account_broker = AccountBroker(trade_broker)

    # 连接行情
    if not data_broker.connect():
        logger.error("行情连接失败，请确认 MiniQMT 已启动")
        return

    # 连接交易（若配置了账号与路径）
    if account_id and mini_qmt_path and os.path.isdir(mini_qmt_path):
        if not trade_broker.connect():
            logger.warning("交易连接失败，将仅运行行情与策略逻辑，不执行实盘下单")
    else:
        logger.info("未配置 ACCOUNT_ID / MINI_QMT_PATH，跳过交易连接")

    # 4) 可选：更新历史行情（示例：为策略股池下载日线）
    history_start = config.get("DATA", "HISTORY_START", fallback="")
    if history_start:
        # 示例股池，实际可由策略或配置提供
        sample_pool = ["000001.SZ", "600000.SH"]
        data_broker.download_history(sample_pool, period="1d", start_time=history_start)

    # 5) 创建策略并运行完整流程：初始化 → 盘前 → 盘中 → 盘后
    strategy = SimplePollingStrategy(config, data_broker, trade_broker, account_broker)
    try:
        strategy.run()
    finally:
        if trade_broker.is_connected:
            trade_broker.stop()
    logger.info("MoneyCat 运行结束")


if __name__ == "__main__":
    main()
