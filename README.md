# MoneyCat

基于 QMT（迅投）的**实盘量化交易小框架**：封装 `xtquant` 行情/交易接口，提供可扩展的策略运行骨架、飞书通知与本地 SQLite 交易事件落库。

## 你需要准备什么

- **QMT 环境**：已安装并登录 **MiniQMT / QMT 投研版**，且本机可正常导入/使用 `xtquant`
- **交易端状态**：交易端已启动并已登录（交易不可用时程序会退出，并在启用飞书时推送告警）
- **Python**：建议 Python 3.10+

## 快速开始

1. **安装依赖**

```bash
pip install -r requirements.txt
```

2. **创建配置**

- 复制 `config.ini.example` 为 `config.ini`
- 至少确认以下配置正确：
  - `[ACCOUNT].ACCOUNT_ID`：资金账号
  - `[ACCOUNT].MINI_QMT_PATH`：MiniQMT 的 `userdata` 目录绝对路径（示例：`C:\path\to\userdata` 或 `/path/to/userdata`）
  - `[STRATEGY].NAME`：策略名（例如 `BreakPrevHighLimitUp` 或 `SimplePolling`）
  - （可选）`[FEISHU]`：开启后会推送关键节点与异常告警
  - （可选）`[DB].TRADE_DB_PATH`：SQLite 文件路径（默认 `trade_records.db`）

3. **运行**

```bash
python main.py
```

运行时会自动初始化日志/通知、连接行情与交易、构建股票池与按需补全历史数据，并按 `STRATEGY.NAME` 启动策略。

## 项目结构（简版）

```text
MoneyCat/
├── main.py                  # 主入口
├── inspect_trade_db.py      # 交易记录库浏览脚本
├── config.ini.example       # 配置示例
├── logging_config.py        # 日志配置
├── broker/                  # 行情 / 交易 / 账户封装
├── strategy/                # 策略基类与具体策略
├── utils/                   # 通用工具函数
├── xtquant/                 # QMT 提供的 xtquant 库（本地放置）
└── logs/                    # 日志输出目录
```

## 内置策略（入口配置）

- **SimplePolling**：示例轮询策略，主要用于跑通订阅与框架流程
- **BreakPrevHighLimitUp**：突破前高涨停打板实盘策略
  - 买入：当日涨幅接近涨停（默认 ≥9.8%，可通过 `LIMIT_NEAR_PCT` 调整）+ 当日最低或昨收低于前高 + 当前价突破前高 + 分时 MACD 上行；
  - 卖出：当前涨停不卖；炸板清仓；其余按分时 MACD 顶/顶背离分批止盈；
  - 预选股：近 N 个交易日区间振幅受限、无涨停、历史涨停次数达标，且当日日线 MACD 为负时不能下行（≥昨日 MACD）。

## 常用策略参数（`[STRATEGY]` 节）

| 配置项 | 说明 | 默认 |
|---|---|---|
| `NAME` | 策略名 | `BreakPrevHighLimitUp` |
| `TICK_INTERVAL_SEC` | 盘中信号轮询间隔（秒，可配，最小 0.1） | `1.0` |
| `LIMIT_NEAR_PCT` | 触发买入的涨幅阈值（相对昨收） | `0.098` |
| `BUY_CASH_RATIO` | 单仓最大资金占总资产比例 | `0.1` |
| `BUY_END_HMS` | 买入信号截止时间（`HH:MM:SS`） | `11:00:00` |
| `INTERVAL_DAYS` / `INTERVAL_MAX_AMPLITUDE_PCT` | 预选股区间天数与最大振幅 | `10` / `0.20` |
| `LIMIT_COUNT_CHECK_DAYS` / `MIN_LIMIT_COUNT` | 历史涨停统计窗口与最低次数 | `250` / `1` |

## 本地交易记录（SQLite）

- 默认生成 `trade_records.db`（可用 `[DB].TRADE_DB_PATH` 修改）。
- 实盘相关事件会写入表 `trade_records`（下单/成交/撤单及失败等），用于复盘与排障。

可使用 `inspect_trade_db.py` 快速浏览，或任意 SQLite 客户端查询，例如：

```bash
python inspect_trade_db.py --date 20260312
sqlite3 trade_records.db "SELECT event_time,event_type,stock_code,direction,volume,price FROM trade_records ORDER BY id DESC LIMIT 20;"
```

## 自定义策略扩展

在 `strategy/` 下新建策略并继承 `BaseStrategy`，实现核心方法后，在 `main.py` 按名称注册；再在 `config.ini` 里切换 `STRATEGY.NAME` 即可：

- `on_init()`：初始化参数 / 资源；
- `on_prepare()`：盘前选股与缓存准备；
- `get_watch_list()`：返回盘中需要订阅的股票列表；
- `on_tick(tick_data)`：盘中信号计算与下单；
- `on_after_close()`：盘后统计与清理。

