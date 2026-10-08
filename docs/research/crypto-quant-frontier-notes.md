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
