# -*- coding: utf-8 -*-
"""
日志模块：支持常用级别配置，输出到控制台与按日文件。
"""
import logging
import os
from datetime import date

# 默认格式
DEFAULT_FORMAT = "[%(levelname)s][%(asctime)s] %(message)s"
DEFAULT_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 级别名字到常量的映射，便于配置
LEVEL_MAP = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def setup_logger(
    name: str = "moneycat",
    log_dir: str = "logs",
    level: str = "INFO",
    log_format: str = None,
    date_format: str = None,
) -> logging.Logger:
    """
    创建并配置日志记录器。

    :param name: 日志器名称
    :param log_dir: 日志文件目录
    :param level: 日志级别，如 'DEBUG', 'INFO', 'WARNING', 'ERROR'
    :param log_format: 日志格式
    :param date_format: 日期格式
    :return: 配置好的 Logger 实例
    """
    log_level = LEVEL_MAP.get(level.upper(), logging.INFO)
    formatter = logging.Formatter(
        log_format or DEFAULT_FORMAT,
        datefmt=date_format or DEFAULT_DATE_FORMAT,
    )

    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    if logger.handlers:
        logger.handlers.clear()

    # 控制台
    ch = logging.StreamHandler()
    ch.setLevel(log_level)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # 按日文件
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{date.today().strftime('%Y-%m-%d')}.log")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(log_level)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    return logger


# 全局默认 logger，可在配置加载后重新 setup
logger = setup_logger()
