# AI Quant Trading System — 框架完整分析报告

生成时间：2026-10-03 · 系统版本 v0.4 · 数据截至本报告生成时

---

## 0. 一句话结论

**框架的工程目标已经达成**（多源情报 → LLM 决策 → 确定性成本/风控校验 → Hummingbot 执行 → 结构化复盘全链路可运行、可审计、可复现）。跨 3 个窗口 × 2 个模型的回放给出一致结论：**AI 的增量价值 = 识别 BREAKOUT（唯一稳定盈利 regime）+ 避免固定趋势基线的大亏（-0.1% vs -18/-31），但尚不能战胜固定网格和买入持有；手续费（占毛利 48-57%）是收益侧最大瓶颈。** "AI 稳定增强收益"这一命题尚未被证明。

---

## 1. 系统架构（现状）

```
Market Data (Hyperliquid 公共 API + Hummingbot)
        │
On-chain(Blockchain.info)  Social(FearGreed/LunarCrush*)  News(RSS)
        └──────────┬───────┘
           Intelligence Layer（features / signals / narratives, 15min 采集）
                   ↓
           LLM Agent（当前 GLM-5.3-Flash fallback / DeepSeek-V4.1-Flash）
                   ↓ 结构化 Proposal（非最终批准）
        ┌──────────┴──────────┐
   Cost Validator        Risk Validator     ← 确定性代码，LLM 无权绕过
   (费用/滑点/边际)        (敞口/仓位限制)
        └──────────┬──────────┘
              Hummingbot Executors（grid / position）
                   ↓
        Binance Perpetual Testnet
                   ↓
   Analytics(SQLite: decisions/trades/reviews) → Memory → Dashboard
                   ↓
        历史回放（forward-replay，无未来函数）
```

关键设计约束全部成立：Agent 不发明策略、不碰凭证、不能自行开 LIVE；Dashboard 只读；分析链路故障不影响交易链路。

---

## 2. 实验资产盘点

| 实验 | 类型 | 模型 | 窗口 | 状态 | 净 PnL |
|---|---|---|---|---|---|
| exp-a856ad913012 | 7d 回放 | GLM（v0.3 prompt） | 09-20→09-27 | completed | **-$1.64** |
| exp-ac0ec11406f7 | 30d 回放 | deepseek-flash | 09-03→10-02 | completed | **+$16.92** |
| exp-eb401d316df2 | 90d 回放 | glm-4.5-flash（关思考） | 07-05→10-03 | completed | **-$27.38（-0.27%）** |
| exp-23f2df348a8e | 90d 回放（86.9/90 天） | deepseek-flash | 07-05→09-30 | failed（402 尾部中断，数据完整） | **-$10.15（-0.10%）** |
| exp-d713167f1466 | 7d testnet 前瞻 | GLM→DeepSeek→GLM | 09-27 起（含 4d 停机） | running | $0（0 成交） |
| exp-c4cc23730c9d / exp-baa33d326dcf | 30d 回放 | GLM/DeepSeek | — | aborted（关机中断） | — |
| exp-0a51b3661c70 | 90d 回放 | deepseek-flash | — | aborted（休眠冻结） | — |
| exp-7881c7f72f2f | 90d 回放 | deepseek-flash | — | **invalid（402 余额耗尽）** | — |

---

## 3. 核心实验结果

### 3.1 7d 回放（GLM，v0.3，无成本校验）—— 暴露问题

- 16 executor（12 网格 + 4 仓位），净 **-1.64 USDT**
- **网格毛利 +0.21，手续费 -5.21 → 净 -5.00**：手续费是毛利的 25 倍，结构性亏损
- 仓位策略 +3.36（唯一亮点，但样本仅 4 笔）
- 结论：问题不是"LLM 不够聪明"，而是**交易经济学没有闸门** → 直接催生了 v0.4 的 Cost Validator

### 3.2 30d 回放（DeepSeek，v0.4 全链路）—— 验证修复

713 决策点（BTC+ETH，2h 间隔），29 天窗口：

| 维度 | 结果 |
|---|---|
| 净收益 | **+$16.92（+0.17%）**，maxDD **0.24%** |
| 决策分布 | WATCH 460 / WAIT 191 / LONG 46 / SHORT 9 —— 91% 时间选择不动 |
| 网格（28 笔） | 净 +3.94，费用 9.89（费用仍是净利的 2.5 倍，但毛利已覆盖成本） |
| 仓位（37 笔） | 净 +12.98，费用 5.93，胜率 43% |
| Regime 归因 | BREAKOUT **+$16.37（75% 胜率）** / RANGING +3.94 / TRENDING_BULL -0.27 / TRENDING_BEAR -3.12 |
| 基线对比 | 固定网格 +4.94/+11.03 · 固定趋势 +4.05/+5.92 · **买入持有 +22.22/+26.22** |

解读：
1. **Cost Validator 有效**：网格从 7d 的"毛利不抵手续费"变成净赚，profit factor 1.32
2. **BREAKOUT 是 AI 的主要超额来源**；趋势策略（TRENDING_*）合计亏损，是明确弱项
3. **买入持有跑赢一切** —— 该窗口是单边上涨（BTC +约12%）。AI 当前的价值体现在**回撤控制（0.24% vs 市场波动）和费用纪律**，而非收益增强
4. 污染警示：DeepSeek 训练截止未知，回放窗口可能在训练集内；前瞻实验才是干净证据

### 3.3 90d 回放（双线：GLM 全程 + DeepSeek 87/90 天）

**GLM-4.5-flash（关思考）全程 90d**（exp-eb401d316df2，2155 决策点）：
- 净 **-0.27%**，maxDD 0.45%，82 笔
- 网格 68 笔净 **-18.15**（费用 30.61 —— 费用再次吞掉毛利 12.5）
- 仓位 12 笔 -7.61，胜率 25%
- 决策分布：92% WATCH —— GLM 比 DeepSeek 更保守

**DeepSeek 90d 部分**（exp-23f2df348a8e，07-05→09-30 共 86.9 天，2057 决策点；
尾部 3 天因余额耗尽中断，fail-fast 保住数据完整性，该窗口已被 30d 实验覆盖）：
- 净 **-0.10%**，maxDD 0.42%，174 笔，profit factor 0.94
- 总费用 **41.96 USDT**（若零费用，毛利约 +32）——费用是这个框架的第一大户
- 基线：固定网格 +16.09/+7.14 · 固定趋势 **-18.32/-30.64** · 买入持有 +69.80/+103.42

### 3.4 跨窗口综合（本报告最重要的结论）

三个窗口 × 两个模型的 Regime 归因高度一致：

| Regime | 7d GLM | 30d DeepSeek | 90d DeepSeek | 结论 |
|---|---|---|---|---|
| BREAKOUT | +3.84 | +16.37 (75%) | +34.05 (54%) | **唯一稳定的正贡献来源** |
| RANGING（网格） | -5.00 | +3.94 | -12.04 | 费用敏感，整体不可持续 |
| TRENDING_BULL | -0.48 | -0.27 | -14.33 (34%) | 稳定亏损 |
| TRENDING_BEAR | — | -3.12 | -17.81 (**14%**) | 最差：熊市做空反而亏最多 |

1. **AI 的超额收益全部来自 BREAKOUT 识别**——这在 3 个窗口、2 个模型上复现，是当前最有统计信心的发现
2. **趋势跟随在两个模型上都是净亏损**；TRENDING_BEAR 做空胜率仅 14%——追空在牛市大窗口里被反复止损
3. **Agent 显著跑赢固定趋势基线**（-0.1% vs -18/-31），但跑输固定网格和买入持有——AI 当前的增量是"避免最差情况"，还不是"增强收益"
4. 费用占毛利的比例：30d 约 48%，90d 约 57%——**maker 单改造和网格间距自适应是收益侧最大杠杆**

### 3.5 7d testnet 前瞻（真实 API，运行中）

- 38 决策：WAIT 55% / WATCH 32% / PROPOSE 8%，**0 成交**
- 3 笔网格提案被 Cost Validator 拒绝 —— 事后核查系 09-28 当时代码尚未从 `triple_barrier_config` 提取 take_profit（当日已修复），当前代码对相同参数判定通过
- 前瞻账户：5000 USDT + 5000 USDC + 0.0012 BTC 多头（+2.57 浮盈，停机前遗留）
- 教训：前瞻实验跨了 4 天停机 + 一次休眠冻结，**运维连续性是目前最大的数据质量风险**

---

## 4. 框架能力验证清单（对照 v0.4 验收标准）

| 项 | 状态 |
|---|---|
| 统一 Intelligence Schema（market/onchain/social/news/narrative，全带时间戳） | ✅ |
| Narrative Engine（多源交叉确认打分） | ✅（已产出 8 条叙事记录） |
| LLM 结构化 Proposal + Evidence（不存 CoT） | ✅ |
| Cost Validator（费用/滑点/安全边际，fail-closed） | ✅ 并在 30d 回放中证明有效 |
| Risk Validator + Grid 四态保护（NORMAL/WARNING/DEFENSIVE/EXIT） | ✅ |
| REGIME_TRANSITION 状态 | ✅（30d 中出现 34 次） |
| 回放不可变（旧实验禁止修改；新增 additive migration） | ✅ |
| 基线对比（固定网格/固定趋势/买入持有，同窗口同费率） | ✅ |
| 报告离线可生成（不依赖交易栈在线） | ✅ |
| 回放 fail-fast（402/401/连续失败即中止，不静默空跑） | ✅（本次新增） |
| 消融实验（INTEL_DISABLED / INTEL_SECTIONS） | ✅ 开关就绪，待前瞻数据积累 |
| 模型 A/B | ✅ 首个对照完成：DeepSeek-90d -0.10% vs GLM-90d -0.27%（同窗口，DeepSeek 交易更积极 174 vs 82 笔） |

---

## 5. 事故与运维教训

| 事故 | 根因 | 修复 |
|---|---|---|
| 容器断网（TCP 通/TLS 卡死） | .wslconfig `networkingMode=mirrored` 破坏 docker bridge NAT | 改回 NAT 模式，备份原配置 |
| 90d v1 冻结 | 机器休眠后 python 进程变僵尸（CPU 归零） | kill+重启；**待办：回放 checkpoint/resume** |
| 90d v2 数据污染 | DeepSeek 余额耗尽（402），脚本静默 fallback 跑完全程 | 标记 invalid + fail-fast 修复 |
| Dashboard report 500 | uvicorn 热重载模块状态不一致 | 重启后端；配置改动后必须重启 |
| 前瞻 3 笔误拒 | 当日 validator 未识别 triple_barrier_config.take_profit | 当日已修，本次回归验证通过 |

---

## 6. 当前最大的三个改进方向（按证据强度排序）

1. **费用结构**（证据：90d 费用 41.96 USDT，占毛利 57%；网格在 RANGING 净利为负）
   —— maker 单比例提升、网格间距与波动率自适应、taker 入场改限价，是收益侧最大杠杆
2. **趋势/做空入场质量**（证据：TRENDING_BEAR 14% 胜率 × 14 笔，两个模型一致亏损）
   —— 考虑在 prompt 层提高 TRENDING_BEAR 做空的证据门槛，或先禁用做空方向直到样本足够
3. **前瞻数据积累**（证据：所有干净结论都需要样本；7d 前瞻因停机只攒了 38 决策）
   —— 保持 testnet loop 不间断运行，比任何参数调整都重要

### 不建议做的事（防止过拟合）

- 不因 BREAKOUT 盈利而降低其入场门槛（虽然是三窗口复现，但仍是 regime 条件而非独立样本）
- 不因趋势策略亏损而永久关闭——先在回放里验证"提高做空门槛"的假设
- 不因单窗口结果更换模型——DeepSeek 与 GLM 的 90d 差异（-0.10% vs -0.27%）不足以区分模型能力

---

## 7. 下一步

1. DeepSeek 充值后重跑一次完整 90d（当前 exp-23f2df348a8e 缺最后 3 天，已被 30d 实验覆盖但非同次运行）
2. 给 backtest_agent.py 加 checkpoint/resume（本次 3 次中断的直接教训）
3. 前瞻实验攒满 7 个完整运行日后出首份干净样本报告
4. 实验"maker-only 网格"与"TRENDING_BEAR 禁空"两个假设——都有明确的回放证据支持，且可用现有框架离线验证，不影响实时链路
