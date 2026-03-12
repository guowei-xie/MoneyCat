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

