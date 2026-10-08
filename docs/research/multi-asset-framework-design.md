# 多币种决策框架设计(v0.6 设计稿,未实现)

> 状态:**纯设计文档**。基于 v0.5 回放验证结果与外部研究,不修改代码、
> 不做回测。实现前需用户确认。
>
> 适用范围:watchlist 扩展为 BTC / ETH / SOL / XRP / SUI 之后,Agent 面临
> 的核心问题从"这个币该不该交易"变成"**五个高度相关的币种中,相对强度
> 最强的是谁,组合层面风险怎么分配**"。

---

## 1. 问题定义:为什么"每个币独立跑一遍 v0.5"是错的

五个币种日收益相关性长期在 0.7-0.9(系统性 beta 主导,个股性 alpha 稀薄):

- 对 BTC、ETH、SOL 同时开多 ≠ 3 个独立机会 ≈ 1 个 crypto beta 仓位 ×3 杠杆。
- v0.5 的入场门(方向须与 8h 动量同向)在趋势日会让**五个币同时通过**,
  Agent 若对每个都开仓,实际敞口是设计值的 3-5 倍,回撤同步放大。
- 反过来,RANGING 日五个币同时被禁,资金利用率趋零——横截面视角下
  这些时刻恰恰是"相对强弱分化"信息含量最高的时刻。

结论:多币种不是把单币种循环 ×5,而是需要**横截面排序层**和**组合风险层**
两个新组件。

## 2. 外部研究证据(设计依据)

### 2.1 加密横截面动量:弱,且只做多头侧

来源:[Han, Kang, Ryu — Time-Series and Cross-Sectional Momentum in the
Cryptocurrency Market(Sungkyunkwan/ACFR)](https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf)

在真实成本(15bps)、逐日盯市、考虑清算风险后:

- **时间序列动量证据强**(lookback 28d / holding 5d,Sharpe 1.51 vs 市场 0.84),
  优势主要来自**下跌时的防守**(只在多头市场持仓)。
- **横截面动量证据弱**:21 个组合中 5 个被清算,仅 6 个跑赢市场;
  最优(lookback 14d / holding 7d)Sharpe 1.28 vs 市场 1.01——增量有限。
- **动量利润集中在多头腿和大市值币**;空头腿被跳涨反弹反复击穿
  ("losers often rebound and inflict significant losses")。
- 多空市场中性策略"appears unattainable"。
- 平均收益 t 检验在厚尾下失效,必须看 log return 与实际盈亏。

**设计含义**:
1. 横截面排序只用于**选择做多对象**(相对强者),不做空相对弱者。
2. 做空信号门槛显著高于做多(与 v0.5"禁空倾向"一致并给出理论依据)。
3. 我们的 watchlist 全是大市值币,正好是动量效应唯一显著的子集——
   币种选择方向正确。
4. 排序价值的期望值必须保守:它提供的是**集中度控制 + 相对优选**,
   不是独立 alpha 来源。

### 2.2 组合构建:风险分配优于收益预测

来源:[arXiv 2412.02654 — Simple and Effective Portfolio Construction with
Crypto Assets(cvxgrp)](https://arxiv.org/abs/2412.02654)

- 风险平价 / 约束风险分配(CRA):各仓位风险贡献 ρ_i 均衡,
  **不需要预测收益,只需要协方差估计**。
- EWMA 估波动率(63 天半衰期)+ 相关(125 天半衰期)即可;
  crypto 高波动厚尾但标准风险分配方法足够。
- 动态现金稀释达到目标风险水平。

**设计含义**:组合层用**等风险贡献**而非等金额/等 margin 分配;
与 v0.5 的波动率目标仓位(`margin = 2%权益/(杠杆×SL%)`)天然兼容——
单仓公式不变,只需在组合层加一个相关性折扣。

### 2.3 横截面资金费率

来源:[Presto Research — Can Funding Rate Predict Price Change?](https://www.prestolabs.io/research/can-funding-rate-predict-price-change)

- 单资产 funding 对 T→T+1 预测力 R²≈0(v0.5 已按"慢变量"处理,正确)。
- 价值在**横截面**:同一时刻五币 funding 的相对排序反映拥挤度差异——
  funding 显著高于同组的币,多头拥挤,突破失败率与回撤风险更高。

**设计含义**:funding 从"单币慢变量"升级为"横截面拥挤度排序输入",
仍不做单币择时。

### 2.4 轮动 regime 的客观指标

来源:[CoinAPI — What Altcoin Dominance Really Tells You](https://www.coinapi.io/blog/what-altcoin-dominance-really-tells-you-and-how-to-trade-it)、
[Gate — BTC Dominance 2025](https://www.gate.com/blog/8968/btc-dominance-2025-impact-on-crypto-markets-and-altcoin-cycles)

- BTC dominance 下降 = 资金向 alt 轮动(alt 相对强);上升 = 避险回流 BTC。
- **陷阱**:dominance 下降也可能只是"BTC 跌得比 alt 快"——必须结合
  绝对方向判断,不能单独作为 alt 偏好信号。

**设计含义**:引入轻量的 ROTATION regime 维度(五币内部的相对强弱扩散度),
作为 LLM 上下文而非硬门。

## 3. 框架设计:三层架构

```
五币 Market Features(已有,compute_market_features 币种无关)
        │
        ▼
┌─────────────────────────────────────────────┐
│  L1 横截面排序层(确定性代码,不许 LLM 排序)   │
│  对 5 币按多因子打分 → rank 1..5              │
│  输出:相对强弱榜 + 可交易候选集(top-N)       │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│  L2 LLM 决策层(现有 Agent,职责收窄)          │
│  只对 top-N 候选做 regime 判断与时机选择      │
│  输入增加:横截面排名、相对 funding、轮动状态  │
└──────────────────┬──────────────────────────┘
                   ▼
┌─────────────────────────────────────────────┐
│  L3 组合风险层(确定性代码)                   │
│  等风险贡献分配 + 相关性合并敞口 + 总风险预算  │
│  在 v0.5 波动率仓位公式上加组合折扣           │
└──────────────────┬──────────────────────────┘
                   ▼
        v0.5 入场门 → Cost Validator → Risk Validator → Executor
        (全部保留,不变)
```

关键原则:**排序必须确定性**。LLM 排序不一致(同样的数据两次问排名不同),
而排序直接决定资金分配,必须由代码完成。LLM 的职责收窄为:
**在排序后的候选集内判断"现在是不是好时机、用哪个策略"**——这正是
v0.5 已验证有效的部分(入场门 + 择时纪律)。

### 3.1 L1 横截面打分(确定性)

每个决策周期(30min)对五币计算,全部因子已存在于
`compute_market_features`(币种无关,无需新数据):

| 因子 | 字段 | 方向 | 权重思路 |
|---|---|---|---|
| 8h 动量 | `ret_16bar_pct` | 越高越强 | 主因子 |
| 24h 动量 | `ret_48bar_pct` | 越高越强 | 主因子(对应研究中 14d lookback 的短周期类比,先保守) |
| 趋势质量 | `ema_dist_pct`(EMA8/21 乖离) | 同向放大,背离扣分 | 过滤反弹陷阱 |
| 量能确认 | `volume_z_48bar` | 与动量同向时加分,背离时扣分 | 假突破过滤(§4 of frontier notes) |
| 拥挤度 | `funding_rate` 横截面分位 | 同组内显著偏高 → 多头降分 | Presto §2.3 |

打分方式:各因子横截面 z-score 后加权求和(权重初值等权,属实验变量,
**禁止根据单次回放结果调权**)。

输出示例:

```json
{
  "ranking": ["SOL", "ETH", "BTC", "SUI", "XRP"],
  "scores": {"SOL": 1.42, "ETH": 0.87, "BTC": 0.31, "SUI": -0.55, "XRP": -1.03},
  "dispersion": 0.83,
  "rotation": "ALT_FAVOR",
  "tradable": ["SOL", "ETH"]
}
```

- `dispersion`(横截面离散度):五币分数的标准差。离散度低 = 市场齐涨齐跌,
  横截面信息为噪声 → `tradable` 为空,LLM 只能 WAIT。
  **这是多币种框架最重要的纪律:齐涨齐跌日不选边,只看 beta。**
- `rotation`:由 BTC 与其余四币平均分数差 + BTC 绝对方向联合判定
  (避免"BTC 跌得更快"的假 alt 信号,§2.4 陷阱)。
- `tradable`:top-N(N 默认 2,实验变量)且分数 > 0 且通过 v0.5 入场门
  前置筛查的币。

### 3.2 L2 LLM 职责变化

prompt 输入从"五币各自独立分析"改为:

```json
{
  "cross_section": { "ranking": [...], "scores": {...}, "dispersion": ..., "rotation": ... },
  "candidates": {
    "SOL": { "features": {...}, "memory": [...] },
    "ETH": { "features": {...}, "memory": [...] }
  },
  "portfolio": {...}
}
```

LLM 只能对 `candidates` 内的币提出 PROPOSE_*;对榜外币种只能 WAIT/WATCH。
非候选币的 features 不再进入 prompt(省 token,也防止 LLM 绕过排序层)。

### 3.3 L3 组合风险层(确定性)

在 v0.5 `position_margin_for` 基础上增加:

1. **相关性合并敞口**:同向仓位的有效敞口按平均相关系数 ρ(约 0.8)
   合并计算:`effective_exposure = Σ|position_i| × ρ^(n-1)`。
   第二个同向仓位的风险预算自动打 ~5 折,第三个打 ~2.5 折。
2. **总风险预算**:所有持仓的单笔最大亏损之和 ≤ 权益 × R_max
   (R_max 初值 4%,即同时两个满仓 2% 风险仓位;实验变量)。
3. **方向预算分离**:空头仓位总预算减半(§2.1:空头腿跳涨风险),
   且空头只对排名末位 + 高周期趋势向下的币开放。
4. **EWMA 相关/波动估计**:滚动窗口从回放数据离线计算即可,
   不需要实时估计器(§2.2:标准方法足够)。

### 3.4 与 v0.5 的关系

**全部是叠加,不是替换**:

| v0.5 组件 | v0.6 变化 |
|---|---|
| 入场门(validate_entry) | 保留;L1 候选筛查复用它 |
| 波动率目标仓位 | 保留;L3 在其上乘组合折扣 |
| 移动止损 / 冷静期 | 不变 |
| Cost Validator | 不变 |
| LLM prompt 纪律 | 收窄到候选集,增加横截面上下文 |

## 4. 明确不做的

1. **不训练 ML 排序模型**(learning-to-rank / gradient boosting)——
   样本量(五币 × 历史窗口)不支持,违反项目"不自行训练模型"原则。
2. **不做均值-方差优化**——§2.2 结论:只需风险分配,不需收益预测。
3. **不做横截面多空**——§2.1:空头腿在 crypto 不可行。
4. **不让 LLM 参与排序**——不一致性直接污染资金分配。
5. **不新增数据源**——所有因子来自现有 features + funding;
   dominance 类外部指标暂以五币内部相对强弱替代。
6. **不根据单次回放调因子权重**——权重属实验变量,走实验框架
   (30d/90d 回放 + 基线对比),不手动拟合。

## 5. 实验计划(实现后,本次不执行)

| 实验 | 内容 | 假设 |
|---|---|---|
| E1 | 7d/30d 回放:单币独立 v0.5 ×5(现状) vs 横截面框架 | 横截面版回撤显著更低 |
| E2 | 消融:去掉 funding 拥挤度因子 | 判断横截面 funding 是否有增量(§2.3 验证) |
| E3 | 消融:dispersion 门开关 | 齐涨齐跌日不交易是否真的减少亏损 |
| E4 | top-N ∈ {1,2,3} | N=1 最集中 vs N=3 最接近现状 |
| E5 | AI 排序版 vs Fixed-Grid-per-coin 基线 | 沿用 v0.5 基线对比方法 |

判定标准沿用 v0.4/v0.5:Net PnL、maxDD、Profit Factor、胜率、
Fee/Gross PnL,且必须**在相同样本上与基线对比**,单次结果不下结论。

## 6. 实现顺序(确认后)

1. `intelligence/cross_section.py`:五币打分 + ranking + dispersion(纯函数,可单测)
2. 回放接入:`backtest_agent.py` 每周期先跑 L1,LLM 只见候选集
3. L3 组合风险层:`validators.py` 增加组合敞口校验(回放 + 实时共用)
4. prompt 更新:横截面上下文 + 候选集约束
5. Dashboard:Overview 增加横截面排名榜(只读展示)
6. 实验 E1-E5
