# MoneyCat

基于 QMT（迅投）的实盘量化交易系统框架，使用 xtquant 获取行情与交易。

## 项目结构

```
MoneyCat/
├── main.py              # 主入口：初始化 → 盘前 → 盘中 → 盘后
├── config.ini.example    # 配置示例（复制为 config.ini 使用）
├── logging_config.py     # 日志模块，支持级别配置
├── broker/               # 经纪层
│   ├── data.py           # 行情：获取、下载、清洗（DataBroker）
│   ├── trade.py          # 交易：下单、撤单（TradeBroker）
│   └── account.py        # 账户：资金、持仓（AccountBroker）
├── strategy/             # 策略
│   ├── base.py           # 策略基类 BaseStrategy
│   └── simple_polling.py # 示例：每秒轮询行情
├── utils/                # 工具
│   └── common.py         # 股票代码、交易日/交易时间判断等
├── xtquant/              # QMT xtquant 库（行情 xtdata、交易 xttrader）
└── logs/                 # 日志目录
```

## 运行前准备

1. 安装 Python 3.7+，确保已安装 pandas（xtquant 依赖）。
2. 启动 **MiniQMT** 或 **QMT 投研版** 并登录。
3. 将 `config.ini.example` 复制为 `config.ini`，填写 `ACCOUNT_ID` 与 `MINI_QMT_PATH`（userdata 目录）。

## 运行方式

在项目根目录执行：

```bash
python main.py
```

流程简述：

- **初始化**：读配置、连接行情与交易、按需下载历史数据。
- **盘前准备**：策略设定股池、持仓池、缓存（示例策略中为 000001.SZ、600000.SH）。
- **盘中交易**：每秒轮询全推 tick，策略计算信号（示例仅打日志，不实盘下单）。
- **盘后**：输出当日轮询次数与信号摘要。

非交易日会直接退出；若仅做框架演示，可在 `main.py` 中注释掉“非交易日退出”逻辑。

## 扩展策略

继承 `strategy.base.BaseStrategy`，实现：

- `on_init()`：初始化与连接、补数据。
- `on_prepare()`：盘前股池与缓存。
- `get_watch_list()`：盘中关注标的列表。
- `on_tick(tick_data)`：每秒回调，处理行情与下单。
- `on_after_close()`：盘后统计与总结。

## 参考

- [XtQuant.XtData 行情模块](https://dict.thinktrader.net/nativeApi/xtdata.html?id=7zqjlm)
