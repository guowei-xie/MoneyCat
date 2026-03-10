# -*- coding: utf-8 -*-
"""
可选依赖统一入口（避免到处 try/except）。
"""

from typing import Any, Callable, Iterable, TypeVar

T = TypeVar("T")


def get_tqdm() -> Callable[[Iterable[T]], Iterable[T]]:
    """
    获取 tqdm 进度条函数（未安装时返回原 iterable）。

    :return: tqdm(iterable, **kwargs) 或降级函数
    """
    try:
        from tqdm import tqdm as _tqdm  # type: ignore

        return _tqdm
    except Exception:

        def _noop(iterable: Iterable[T], **_: Any) -> Iterable[T]:
            return iterable

        return _noop

