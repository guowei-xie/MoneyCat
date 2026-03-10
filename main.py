# -*- coding: utf-8 -*-
"""
MoneyCat 主入口：初始化 → 盘前准备 → 盘中交易 → 盘后总结。

运行前请先：
1. 将 config.ini.example 复制为 config.ini 并填写账号与 MiniQMT 路径
2. 启动 MiniQMT（或 QMT 投研版）并登录
3. 在交易日运行（非交易日仅做框架演示可注释掉交易日检查）
"""
import os
import time
from datetime import datetime, timedelta

# 保证项目根在 path 中（兼容不同启动 cwd）
from utils.path import ensure_project_root_on_path

ensure_project_root_on_path(__file__, levels_up=1)

from configparser import ConfigParser
from logging_config import logger, setup_logger
from utils.common import is_trading_day
from utils.feishu_notify import init_from_config, send_text as feishu_send_text
from broker.data import DataBroker
from broker.trade import TradeBroker
from broker.account import AccountBroker
from strategy.simple_polling import SimplePollingStrategy
from strategy.break_prev_high_limitup import BreakPrevHighLimitUpStrategy


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
    # 初始化飞书通知配置（可在 config.ini 中关闭或自定义 webhook）
    init_from_config(config)

    # 2) 可选：非交易日直接退出（演示时可注释）
    if not is_trading_day():
        logger.info("当前不是交易日，程序退出。若仅演示框架可注释本段。")
        feishu_send_text("【提示】当前不是交易日，程序已退出。")
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
        feishu_send_text("【错误】行情连接失败，请检查 MiniQMT/xtdata 服务。")
        return

    # 连接交易（本项目定位为实盘框架：无交易环境直接退出并通知人工处理）
    if not account_id or not mini_qmt_path or not os.path.isdir(mini_qmt_path):
        logger.error("无可用交易环境：请配置有效的 ACCOUNT_ID 与 MINI_QMT_PATH（userdata 目录）")
        feishu_send_text("【错误】无可用交易环境：ACCOUNT_ID / MINI_QMT_PATH 未配置或路径无效，程序已退出，请人工检查。")
        return
    if not trade_broker.connect():
        logger.error("交易连接失败，程序退出（需人工处理）")
        # 具体原因 TradeBroker.connect() 内已发飞书，这里补充“退出”语义，避免误以为仍在运行
        feishu_send_text("【错误】交易连接失败，程序已退出，请人工处理。")
        return

    # 4) 初始化统一股票池（沪深A股主板）并可选下载历史数据
    main_board_pool = data_broker.get_stock_list_in_main_board()
    if not main_board_pool:
        logger.error("主板股票池获取失败（需要板块数据/行情服务可用），程序退出")
        feishu_send_text("【错误】主板股票池获取失败，请检查行情服务与板块数据。")
        return
    logger.info("主板股票池获取完成：%d 只", len(main_board_pool))

    history_start = config.get("DATA", "HISTORY_START", fallback="")
    if not history_start:
        history_start = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")

    download_history = config.getboolean("DATA", "DOWNLOAD_HISTORY", fallback=True)
    if download_history:
        logger.info(
            "补全历史数据已开启：period=1d start=%s（可通过 DATA.DOWNLOAD_HISTORY 关闭）",
            history_start,
        )
        data_broker.download_history(main_board_pool, period="1d", start_time=history_start)
    else:
        logger.info("已通过配置 DATA.DOWNLOAD_HISTORY=0 关闭补全历史数据")

    # 5) 创建策略并运行完整流程：初始化 → 盘前 → 盘中 → 盘后
    strategy_name = config.get("STRATEGY", "NAME", fallback="SimplePolling") if isinstance(config, ConfigParser) else "SimplePolling"
    if strategy_name == "BreakPrevHighLimitUp":
        strategy = BreakPrevHighLimitUpStrategy(config, data_broker, trade_broker, account_broker)
    else:
        strategy = SimplePollingStrategy(config, data_broker, trade_broker, account_broker)
    try:
        strategy.run()
        feishu_send_text("【提示】策略运行已正常结束。")
    except Exception as exc:
        logger.exception("策略运行过程中发生未捕获异常：%s", exc)
        feishu_send_text(f"【错误】策略运行异常：{exc}")
        raise
    finally:
        # 策略运行结束后，延迟 10 分钟再做一次日线历史数据补全（无需额外配置开关）
        try:
            logger.info("策略运行结束，10 分钟后开始执行日线历史数据补全。")
            time.sleep(600)
            logger.info("开始执行策略结束后的日线历史数据补全：period=1d start=%s", history_start)
            data_broker.download_history(main_board_pool, period="1d", start_time=history_start)
            logger.info("策略结束后的日线历史数据补全完成。")
        except Exception as exc:
            logger.exception("策略结束后补全历史数据失败：%s", exc)
            feishu_send_text(f"【错误】策略结束后补全历史数据失败：{exc}")
        finally:
            if trade_broker.is_connected:
                trade_broker.stop()
    logger.info("MoneyCat 运行结束")


if __name__ == "__main__":
    main()
