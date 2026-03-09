# MoneyCat

基于 QMT（迅投）的实盘量化交易框架，使用 xtquant 获取行情与交易，内置「突破前高涨停打板」实盘策略与简单轮询示例策略。

## 项目结构

```
MoneyCat/
├── main.py                # 主入口：初始化 → 盘前准备 → 盘中交易 → 盘后总结
├── config.ini.example     # 配置示例（复制为 config.ini 使用）
├── logging_config.py      # 日志模块，统一日志级别与输出格式
├── broker/                # 经纪层封装
│   ├── data.py            # 行情：连接 xtdata、下载历史、获取 K 线 / tick、构建主板股票池 (DataBroker)
│   ├── trade.py           # 交易：连接 xttrader、买入/卖出委托 (TradeBroker)
│   └── account.py         # 账户：资金与持仓查询、可用数量计算 (AccountBroker)
├── strategy/              # 策略层
│   ├── base.py            # 策略基类 BaseStrategy：on_init/on_prepare/on_trading_loop/on_after_close
│   ├── simple_polling.py  # 示例策略：每秒轮询行情，仅做信号与日志演示
│   └── break_prev_high_limitup.py  # 实盘策略：突破前高涨停打板策略
├── utils/                 # 工具模块
│   ├── common.py          # 股票代码规范化、交易日/交易时间判断、交易日历 (akshare)
│   ├── universe.py        # 股票池工具：主板/创业板/科创板/北交所识别与主板过滤
│   ├── market_rules.py    # 涨跌停规则：涨跌停幅度估算、涨跌停价计算、是否涨跌停判断
│   ├── indicators.py      # 技术指标：MACD 计算、MACD 顶形态识别
│   └── position_sizing.py # 仓位/数量工具：卖出数量安全转换（100 股整数倍等）
├── xtquant/               # QMT xtquant 库（行情 xtdata、交易 xttrader，按本地环境放置）
└── logs/                  # 日志输出目录
```

## 环境与依赖

- **Python**：建议 3.8+（兼容 xtquant 所需版本）。  
- **QMT / MiniQMT**：需安装并**登录** MiniQMT 或 QMT 投研版，保证 xtquant 可用。  
- **核心三方依赖**：
  - `xtquant`（随 QMT 安装，提供 `xtdata` / `xttrader`）；
  - `pandas`（行情与 K 线处理）；
  - `akshare`（交易日历，用于 `utils.common.is_trading_day` / `get_trading_dates`）；
  - （可选）`tqdm`：用于长周期数据下载与盘前选股时的进度条。

使用 `pip` 安装示例（需根据本机环境调整）:

```bash
pip install pandas akshare tqdm
```

## 配置说明（config.ini）

以 `config.ini.example` 为模板，在项目根目录复制为 `config.ini` 并按实盘环境修改：

```ini
[ACCOUNT]
ACCOUNT_ID = your_account_id          # 资金账号
MINI_QMT_PATH = C:\path\to\userdata   # MiniQMT userdata 目录绝对路径

[LOG]
LEVEL = INFO                          # 日志级别: DEBUG / INFO / WARNING / ERROR

[DATA]
HISTORY_START = 20250101              # 历史数据补全起始日期（YYYYMMDD）
DOWNLOAD_HISTORY = 1                  # 是否在启动时补全日线历史数据（1/true 开启，0/false 关闭）

[STRATEGY]
# 策略名称：BreakPrevHighLimitUp / SimplePolling
NAME = BreakPrevHighLimitUp

# 单仓最大资金占总资产的比例（0~1 之间的小数）
# 例如：0.1 表示单只股票最多使用当前总资产的 10% 金额买入，
# 实际下单金额仍会受当前可用现金限制（不足则用全部可用现金）。
BUY_CASH_RATIO = 0.1
```

`main.py` 中会：

- 读取上述配置并初始化日志级别；
- 连接 xtdata（行情）与 xttrader（交易，需配置 `ACCOUNT_ID` 和 `MINI_QMT_PATH`）；
- 构建「沪深 A 股主板」股票池并缓存到 `DataBroker.main_board_universe`；
- 按需补全主板股票池的日线历史数据（`DATA.DOWNLOAD_HISTORY`）；
- 根据 `STRATEGY.NAME` 选择并运行具体策略：
  - `SimplePolling`：`SimplePollingStrategy`；
  - `BreakPrevHighLimitUp`：`BreakPrevHighLimitUpStrategy`。

## 运行方式

1. 安装依赖（`xtquant` + `pandas` + `akshare` + 可选 `tqdm`）。  
2. 启动 **MiniQMT** 或 **QMT 投研版** 并登录。  
3. 在项目根目录复制配置文件：`cp config.ini.example config.ini`，修改账户与路径等参数。  
4. 确保当前为交易日（否则默认直接退出，可在 `main.py` 中注释掉交易日检查做纯框架测试）。  

在项目根目录执行：

```bash
python main.py
```

整体流程：

- **初始化**：加载配置、初始化日志、连接行情与交易、按需补全主板股票池的历史日线数据；  
- **盘前准备**（由具体策略实现）：
  - 简单轮询策略：设定固定股池（如 `000001.SZ` / `600000.SH`）、构建缓存；
  - 打板策略：基于主板股票池 + 日线数据，筛选出接近前高的「预买入池」，并缓存昨收价、前高价等；
  - 同时根据当前持仓构建「预卖出池」。  
- **盘中交易**：
  - 框架由 `BaseStrategy.on_trading_loop` 每秒轮询全推 tick，并调用策略的 `on_tick`；
  - 简单轮询策略：定期打印涨跌幅，并在涨幅超过阈值时记录“模拟买入”日志，不实盘下单；
  - 打板策略：结合 tick 与 1 分钟分时 K 线，判断买入/卖出信号并通过 `TradeBroker` 下单。  
- **盘后总结**：
  - 简单轮询策略：输出当日轮询次数与产生的模拟信号；
  - 打板策略：输出部分缓存统计（如每只股票的昨收价、前高价、剩余分批次数等，主要用于调试）。

## 内置策略简介

- **SimplePollingStrategy (`strategy/simple_polling.py`)**  
  - 适合快速验证行情连接与整体流程；  
  - 固定小股池，盘中每秒拉 tick，仅打印行情与模拟信号，不真实下单。  

- **BreakPrevHighLimitUpStrategy (`strategy/break_prev_high_limitup.py`)**  
  - 实盘向的「突破前高涨停打板」策略，核心逻辑：  
    - 盘前：
      - 在沪深 A 股主板股票池中，基于近 N 日日线筛选「接近前高」的标的；
      - 过滤近期振幅过大、近期已有涨停但长期涨停次数不足的标的；
      - 缓存昨日收盘价、前高价、分批卖出次数等信息，并订阅 1 分钟分时。  
    - 盘中：
      - 买入：当 tick 涨幅接近涨停、当前价突破前高、且曾有“低于前高回踩”时，再结合分时数据确认，在 9:30–11:00 时间窗内发出买入信号；
      - 卖出：
        - 当前在涨停附近不卖；
        - 若出现「炸板」（曾封住涨停后明显跌破涨停价且持续一定分钟数），则清仓；
        - 否则根据 1 分钟 MACD 顶/顶背离，按批次分批止盈卖出。  

## 扩展自定义策略

要新增策略，可在 `strategy/` 目录下创建新的策略文件，并继承 `BaseStrategy`：

- 实现 `on_init()`：初始化参数、连接所需资源；  
- 实现 `on_prepare()`：盘前股票池构建、缓存准备；  
- 实现 `get_watch_list()`：返回盘中需要订阅/拉取 tick 的股票代码列表；  
- 实现 `on_tick(tick_data)`：根据 tick（及需要时的 K 线）计算买卖信号并调用 `TradeBroker` 下单；  
- 实现 `on_after_close()`：盘后统计与日志输出。  

然后在 `main.py` 中按名称增加策略实例化逻辑，并在 `config.ini` 中将 `STRATEGY.NAME` 设置为对应名称即可切换。

## 参考

- [XtQuant.XtData 行情模块](https://dict.thinktrader.net/nativeApi/xtdata.html?id=7zqjlm)
