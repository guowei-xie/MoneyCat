# -*- coding: utf-8 -*-
"""
飞书群自定义机器人通知工具。

支持：
1. 通过配置文件 [FEISHU] 设置 webhook 与开关；
2. 发送简单文本消息，用于关键节点/异常告警。
"""

from typing import Optional

import json
import logging

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore


logger = logging.getLogger(__name__)

#: webhook 地址，仅从配置文件 [FEISHU] WEBHOOK 读取，不硬编码
WEBHOOK_URL = ""
#: 是否启用飞书通知（从配置文件 [FEISHU] ENABLE 读取）
ENABLED = False


def init_from_config(config) -> None:
    """
    根据配置初始化飞书通知开关与 webhook 地址。

    约定（config.ini）：
    [FEISHU]
    ENABLE = 1            # 是否开启通知（1/true/yes 为开启）
    WEBHOOK = https://... # 飞书群机器人 webhook 地址（必填则通知才生效）
    """
    global WEBHOOK_URL, ENABLED

    if not hasattr(config, "get"):
        return

    try:
        ENABLED = config.getboolean("FEISHU", "ENABLE", fallback=False)
        WEBHOOK_URL = config.get("FEISHU", "WEBHOOK", fallback="").strip()
    except Exception as exc:  # pragma: no cover
        logger.warning("读取 FEISHU 配置失败，飞书通知将不生效：%s", exc)


def send_text(
    content: str,
    webhook_url: Optional[str] = None,
) -> bool:
    """
    发送飞书文本消息。

    :param content: 文本内容，将直接作为消息体发送
    :param webhook_url: 可选，指定 webhook；为空时使用配置中的 WEBHOOK
    :return: 发送是否成功（仅代表 HTTP 与飞书返回 code 判断，不保证真正送达）
    """
    if not ENABLED:
        logger.debug("飞书通知已在配置中关闭，本次消息未发送：%s", content)
        return False

    url = (webhook_url or WEBHOOK_URL).strip()
    if not url:
        logger.warning("飞书 webhook 未配置，本次消息未发送：%s", content)
        return False

    if requests is None:
        logger.error("requests 未安装，无法发送飞书通知。本次内容：%s", content)
        return False

    payload = {
        "msg_type": "text",
        "content": {
            "text": content,
        },
    }

    try:
        resp = requests.post(url, json=payload, timeout=5)
    except Exception as exc:  # pragma: no cover
        logger.error("发送飞书通知失败（请求异常）：%s，内容=%s", exc, content)
        return False

    if not resp.ok:
        logger.error("发送飞书通知失败（HTTP %s）：%s", resp.status_code, content)
        return False

    try:
        data = resp.json()
    except Exception:
        logger.warning("解析飞书响应失败：%s", resp.text)
        return False

    if data.get("code") != 0:
        logger.error("飞书返回错误：code=%s msg=%s content=%s", data.get("code"), data.get("msg"), content)
        return False

    # 成功发送属于高频事件，避免在 INFO 级别刷屏；必要时可将本模块 logger 级别调为 DEBUG。
    logger.debug("飞书通知发送成功：%s", json.dumps(payload, ensure_ascii=False))
    return True

