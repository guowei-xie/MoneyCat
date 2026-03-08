# -*- coding: utf-8 -*-
"""策略模块：基类与示例策略。"""
from strategy.base import BaseStrategy
from strategy.simple_polling import SimplePollingStrategy

__all__ = ["BaseStrategy", "SimplePollingStrategy"]
