# MoneyCat

基于 QMT（迅投）的实盘量化交易小框架，封装 `xtquant` 行情 / 交易接口，并内置一个「突破前高涨停打板」策略与一个简单轮询示例策略，方便在此基础上继续扩展。

## 功能概览

- **行情与交易封装**：统一封装 `xtdata` / `xttrader`，提供 `DataBroker` / `TradeBroker` / `AccountBroker`。
- **统一股票池**：自动构建「沪深 A 股主板」股票池，可选补全日线历史数据。
- **策略运行框架**：`BaseStrategy.run()` 串起「初始化 → 盘前准备 → 盘中轮询 → 盘后总结」全流程。
- **通知能力**：可选接入飞书群自定义机器人，推送异常与关键节点提示。

## 快速开始

1. **准备环境**
   - 安装并登录 **MiniQMT** 或 **QMT 投研版**，确保本机已安装 `xtquant`。
   - 确保 **交易端已启动并已登录**（本项目为实盘框架：交易不可用将直接退出并飞书告警）。
   - 安装 Python 依赖（按需调整）：

   ```bash
   pip install pandas akshare tqdm
   ```

2. **配置文件**
   - 复制根目录下的 `config.ini.example` 为 `config.ini`，按实盘环境修改：

   ```ini
   [ACCOUNT]
   ACCOUNT_ID = your_account_id
   # MiniQMT 的 userdata 目录绝对路径（必须为目录）
   MINI_QMT_PATH = C:\path\to\userdata

   [LOG]
   LEVEL = INFO

   [FEISHU]
   ENABLE = 1
   WEBHOOK = https://your-feishu-webhook

   [DATA]
   HISTORY_START = 20250101
   DOWNLOAD_HISTORY = 1

   [STRATEGY]
   NAME = BreakPrevHighLimitUp   # 或 SimplePolling
   BUY_CASH_RATIO = 0.1
   ```

3. **启动策略**
   - 确认当前为交易日（否则程序会直接退出；若你确实要在非交易日跑通流程，可自行在 `main.py` 调整交易日检查逻辑）。
   - 确认 `ACCOUNT_ID` 与 `MINI_QMT_PATH` 配置正确且交易连接可用（否则会直接退出并飞书告警）。
   - 在项目根目录执行：

   ```bash
   python main.py
   ```

程序会自动：

- 初始化日志与飞书通知；
- 连接行情与交易（若交易配置缺失或交易连接失败将直接退出并飞书告警）；
- 构建主板股票池并按需补全日线历史数据；
- 根据 `STRATEGY.NAME` 选择并运行对应策略。

## 项目结构（简版）

```text
MoneyCat/
├── main.py                  # 主入口
├── config.ini.example       # 配置示例
├── logging_config.py        # 日志配置
├── broker/                  # 行情 / 交易 / 账户封装
├── strategy/                # 策略基类与具体策略
├── utils/                   # 通用工具函数
├── xtquant/                 # QMT 提供的 xtquant 库（本地放置）
└── logs/                    # 日志输出目录
```

## 内置策略

- **SimplePollingStrategy**（`strategy/simple_polling.py`）  
  - 小股池每秒轮询 tick，仅输出行情与“模拟信号”，不真实下单；但仍会按统一启动流程校验交易环境（交易不可用将退出并告警）。

- **BreakPrevHighLimitUpStrategy**（`strategy/break_prev_high_limitup.py`）  
  - 实盘向的「突破前高涨停打板」策略：
    - 盘前：基于主板股票池与近 N 日日线，筛选接近前高的标的并构建预买入 / 预卖出池；
    - 盘中：结合 tick 与 1 分钟分时，在 9:30–11:00 内根据涨停接近度、前高突破情况与 MACD 等条件发出买卖指令；
    - 盘后：输出主要统计信息，便于回顾与调试。

## 自定义策略扩展

在 `strategy/` 目录中新建文件并继承 `BaseStrategy`，实现以下核心方法后再到 `main.py` 中按名称注册，并在 `config.ini` 里切换 `STRATEGY.NAME` 即可：

- `on_init()`：初始化参数 / 资源；
- `on_prepare()`：盘前选股与缓存准备；
- `get_watch_list()`：返回盘中需要订阅的股票列表；
- `on_tick(tick_data)`：盘中信号计算与下单；
- `on_after_close()`：盘后统计与清理。

