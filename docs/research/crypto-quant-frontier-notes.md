# 前沿虚拟货币量化研究笔记(2026-10)

> 目的:为本项目决策框架 v0.5 改造提供外部证据。每条结论都标注来源与
> 对本系统的具体含义。原则:稳健性来自结构,而不是复杂度(QuantPedia)。

## 1. 多时间框架趋势过滤(Elder 三重滤网)——最重要的一条

来源:[QuantPedia — How to Design a Simple Multi-Timeframe Trend Strategy on Bitcoin](https://quantpedia.com/how-to-design-a-simple-multi-timeframe-trend-strategy-on-bitcoin/)

BTC 小时线 MACD 趋势策略逐步改进实验(2018-2025):

| 版本 | 交易数 | 年化 | Sharpe | maxDD |
|---|---|---|---|---|
| 纯 1H MACD | 2262 | 4.6% | 0.33 | -23.9% |
| +D1 趋势过滤(只做日线方向) | ~1000 | 6.6% | **0.80** | -12.4% |
| +移动止损(trailing) | — | — | **1.07** | Calmar 0.87 |

结论:
- **单一短周期信号 = 噪声。** 高周期(日线/8h)定方向,低周期(30m/1h)找入场。
- 只顺高周期趋势交易,交易次数减半,Sharpe 翻倍以上。
- 移动止损优于固定止盈:让趋势奔跑,自动截断震荡。
- 该研究刻意 long-only(BTC 长期向上漂移 + 做空成本/复杂度)。

**对本系统的含义**:我们的 AI 在 30m 频率做方向择时,三轮回放(+0.25% /
-4.09% / -16.79%)证明其在噪声中无优势。改造方向 = 用 8h/24h 动量与
EMA 交叉作为**确定性入场门**,LLM 不再能逆着高周期趋势开仓。

## 2. 波动率目标仓位 + 固定风险比例

来源:[Cas Abbé / Blockchain News — ATR-Based Position Sizing](https://blockchain.news/flashnews/crypto-risk-management-rising-volatility-signals-focus-on-atr-based-position-sizing-and-large-cap-coins-first)、[Quantified Strategies — Position Sizing in Trend Following](https://www.quantifiedstrategies.com/position-sizing-in-trend-following-system/)、[PortfolioWiser — Volatility Targeting](https://www.portfoliowiser.com/knowledge-hub/volatility-targeting)

- 单笔固定风险 1-2% 组合权益(risk = margin × leverage × SL%)。
- 仓位随波动率反向缩放:ATR 高 → 仓位小,ATR 低 → 仓位大。
- 等风险(equivalent risk)而非等金额:同样 2% 风险在不同波动率下名义仓位应不同。

**对本系统的含义**:之前固定 margin=1000 + 20x,SL 放宽后单笔亏损 -386
到 -480(≈4% 权益)。改为 `margin = equity × 2% / (leverage × SL%)`,
止损宽时自动缩仓,单笔最大亏损恒定 ≈2% 权益。杠杆降到 5x——
在信号优势未被证明前,杠杆只是噪声放大器。

## 3. 资金费率不能单独预测单一资产价格

来源:[Presto Research — Can Funding Rate Predict Price Change?](https://www.prestolabs.io/research/can-funding-rate-predict-price-change)

- 资金费率变化与**同期**价格变化相关(R²=12.5%),但对**下一期**
  价格变化的预测力 R²≈0(p 值不显著)。
- 资金费率的alpha在横截面(多资产对比),不在单资产择时。

**对本系统的含义**:prompt 中 funding 定位为"慢变量/拥挤度指标,
不是择时信号"是正确的,保持不变。LLM 若用 funding 作为入场理由,
evidence 中应被标注为弱证据。

## 4. 突破需要成交量确认

来源:[Quantt — Crypto Quant Strategies 2026](https://www.quantt.co.uk/resources/crypto-quant-strategies-2026)、[FX Replay — How to Backtest a Breakout Strategy](https://fxreplay.com/learn/can-you-backtest-a-breakout-strategy-heres-how-to-do-it-right)

- 加密市场假突破多(散户情绪驱动),突破 + 成交量放大是标准过滤器。
- 趋势跟踪在 crypto 可行的前提:严格风控 + 多时间框架 + 突破时
  关注资金费率(拥挤度过高的突破容易失败)。

**对本系统的含义**:BREAKOUT regime 的方向单必须要求
`volume_z_48bar ≥ 1`,否则降级为 WATCH。

## 5. 趋势跟踪在加密的特殊困难

来源:Quantt(同上)

- 高波动 → 锯齿(whipsaw)风险严重 → 止损后同方向冷静期是常见做法。
- 大趋势有负偏(2022 式崩跌),空头在反弹中易被挤爆。
- 趋势信号应作为多策略组合的一个输入,不是唯一信号。

## 6. 链上/情绪数据的现实定位

来源:Quantt(同上)

- 链上资金流信号在 1-7 天视野有效;朴素的"跟鲸鱼"无效(幸存者偏差)。
- 数据服务于"市场状态分类",而不是直接产生交易信号。

**对本系统的含义**:Intelligence 层(社交/链上/新闻)维持"regime 与
风险升级"用途,不直接进入入场门。

## 改造决策汇总(v0.5)

| # | 改造 | 依据 | 实现位置 |
|---|---|---|---|
| 1 | 确定性入场门:方向必须与 8h 动量 + EMA8/21 同向;RSI 极值禁追;BREAKOUT 需量能确认;RANGING 禁方向单 | §1 §4 | `validators.validate_entry`,回放+实时共用 |
| 2 | 波动率目标仓位:margin = 2% 权益 / (杠杆 × SL%),上限封顶 | §2 | 回放 `position_margin_for`;实时由 prompt 指引 |
| 3 | 杠杆 20x → 5x(信号优势未证明前) | §2 §5 | 回放参数;settings 不变(实时由 AI 自选,提示词引导) |
| 4 | 移动止损:+1×SL 后保本,+1.5×SL 后以 1×SL 跟踪 | §1 | `SimPosition` trailing |
| 5 | 止损后同向冷静期 6h(回放) | §5 | 回放 entry gate 状态 |
| 6 | prompt:多时间框架纪律 + 入场门规则透明化 | §1 | `trading_manager.md` |

---

# 多币种/横截面研究(2026-10,v0.6 设计依据)

详细设计见 `multi-asset-framework-design.md`,此处只记研究结论。

## 7. 时间序列动量强,横截面动量弱——只做多头侧

来源:[Han/Kang/Ryu — TS & CS Momentum in Crypto under Realistic Assumptions](https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf)

真实成本(15bps)+ 逐日盯市 + 清算风险下:
- TS 动量强(28d lookback/5d holding,Sharpe 1.51 vs 市场 0.84),
  优势来自下跌时防守(只在多头市场持仓)。
- CS 动量弱:21 组合中 5 个清算,仅 6 个跑赢市场;最优 Sharpe 1.28 vs 1.01。
- 动量利润集中在**多头腿 + 大市值币**;空头腿被反弹击穿;多空市场中性不可行。
- 厚尾下平均收益 t 检验失效,必须看 log return 和实际盈亏。

**含义**:横截面排序只用于"选谁做多",不做空弱者;做空门槛 > 做多;
大市值 watchlist(BTC/ETH/SOL/XRP/SUI)正好是动量唯一显著的子集。

## 8. 风险分配优于收益预测(组合层)

来源:[arXiv 2412.02654 — Simple and Effective Portfolio Construction with Crypto Assets](https://arxiv.org/abs/2412.02654)

- 约束风险分配(CRA)/风险平价:各仓位风险贡献均衡,**只需协方差估计**。
- EWMA:波动率 63 天半衰期,相关 125 天;crypto 厚尾下标准方法足够。
- 动态现金稀释达到目标风险。

**含义**:组合层用等风险贡献 + 相关性折扣,叠加在 v0.5 波动率仓位上。

## 9. 横截面资金费率

来源:Presto(§3 同篇)

- 单资产 funding 无择时力;**五币 funding 的相对排序**反映拥挤度差异。
- 同组内 funding 显著偏高 = 多头拥挤 → 突破失败/回撤风险高。

**含义**:funding 升级为横截面排序的降分因子,仍不做单币择时。

## 10. 轮动 regime 的陷阱

来源:[CoinAPI](https://www.coinapi.io/blog/what-altcoin-dominance-really-tells-you-and-how-to-trade-it)、[Gate](https://www.gate.com/blog/8968/btc-dominance-2025-impact-on-crypto-markets-and-altcoin-cycles)

- BTC dominance 下降 ≠ 一定是 alt 季:也可能是"BTC 跌得更快"。
- 轮动判断必须结合绝对方向,不能单独用相对强弱。

**含义**:框架中 `rotation` 字段由相对分数差 + BTC 绝对方向联合判定。

---

# 短线交易决策框架研究(2026-10,v0.5.1 改造依据)

## 11. 日内季节性:成交量/波动率有稳定的时段结构

来源:[Petrov/Golub/Olsen — Instantaneous Volatility Seasonality of Bitcoin](https://smallake.kr/wp-content/uploads/2019/02/SSRN-id3243797.pdf)、[UNSW Hawkes 模型研究(周日/周一异常)](https://unsworks.unsw.edu.au/bitstreams/44b2fb76-1683-427f-9b56-d110e739e13b/download)、[MQL5 同时段归一化指标](https://www.mql5.com/en/market/product/193758)

- BTC 成交量与波动率呈稳定 U 型日内曲线(美盘时段高、亚洲凌晨低),
  且周日/周一有日历异常。
- **含义**:全局 24h 基线的 volume_z 在安静时段高估异常、在繁忙时段
  低估异常。正确做法是同时段(time-of-day)归一化后再算 z。
- **已实现**:`compute_market_features` 新增 `volume_z_deseason`
  (7 天同时段 profile 去季节化,≥4 天历史才输出,否则 None 回退);
  BREAKOUT 入场门优先使用它。

## 12. Funding 是真实持仓成本,不只是情绪指标

来源:[BloFin — Funding + Open Interest: Signals Traders Use (and Misuse)](https://blofin.com/en/academy/education/trading/funding-and-open-interest-signals)

- 永续每 8h 结算 funding;多头在 funding>0 时付费,空头在 funding<0 时
  付费。短线持仓跨一个结算窗口就产生真实现金流。
- 高 OI 环境下清算缓冲规则:入场价距清算价 ≥20-30%。
- **已实现**:CostValidator 新增 funding 成本(只在付费方向收取,
  收入方向永不计入——保守原则);`cost.funding_interval_hours` /
  `cost.expected_hold_hours` 可配。回放与实时共用。

## 13. OI(持仓量)是短线衍生品情报的核心缺口

来源:BloFin(同上)、[清算瀑布研究汇总](https://www.kucoin.com/blog/jp-why-bitcoin-futures-trading-can-cause-a-liquidation-cascade)

- 价涨 + OI 增 = 新钱进场(较可持续);价涨 + OI 降 = 空头回补
  (脆弱,易回落)。突破确认只有成交量没有 OI 是半盲的。
- 高 OI + 极端 funding = 拥挤,清算瀑布易发 → 应触发风险升级而非追单。
- **已实现**:HyperliquidDeriv provider(公共 API,与行情同源),
  采集 open_interest(USD 名义)与 funding_rate;features 层输出
  `oi_change_pct`(~24h 窗口);snapshot 新增 derivatives 区,
  可经 INTEL_SECTIONS 做消融实验。
- **未做**:清算热力图(Coinglass 需 key,列为下一步);事件驱动
  风险升级(30min 周期外的波动冲击检测,写入设计文档 v0.6.1)。

## 14. 短线 time-stop:不动的仓位就是错的仓位

来源:短线交易通则(QuantPedia 多 timeframe 研究中 trailing/time exit
的组合实践)+ 本系统回放证据(亏损单平均 1-1.5h 内止损,盈利单很快
进入盈利区)。

- 入场后数小时内未达 +1×SL 的仓位,方向判断大概率错误;
  24h TIME_LIMIT 对短线系统太松,让死仓位持续捐手续费与 funding。
- **已实现**:回放 `--time-stop-hours 6`(默认):6h 未激活保本即
  市价离场(TIME_STOP);已进入盈利区的仓位不受影响(trailing 接管)。
  实时路径由 prompt 纪律承载(Hummingbot executor 无原生 time-stop)。

## 改造决策汇总(v0.5.1)

| # | 改造 | 依据 | 实现位置 |
|---|---|---|---|
| 1 | volume_z 去季节化(同时段 7 天 profile) | §11 | `market_features._deseasonalized_volume_z`,入场门优先使用 |
| 2 | Cost Validator 计入 funding 成本(只收不贷) | §12 | `CostValidator._funding_cost`,回放+实时共用 |
| 3 | 回放 time-stop 6h(未激活保本即离场) | §14 | `SimPosition.time_stop_s`,`--time-stop-hours` |
| 4 | OI/funding 衍生品情报层(Hyperliquid 公共 API) | §13 | `HyperliquidDeriv` provider + derivatives snapshot 区 |
| 5 | 实时特征历史加深(120→400 bars / 公共回退 48→200h) | §11 | `agent.py` 特征获取 |
| 6 | prompt:OI 解读、funding 成本意识、time-stop 纪律 | §12-14 | `trading_manager.md` |
