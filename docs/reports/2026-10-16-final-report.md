# AI Crypto Quant Trading System — 项目综合报告

> 归档日期:2026-10-16
> 状态:**实盘环(testnet)已终止**(GLM API 欠费,不再续费),系统代码与数据完整归档于 GitHub `Jeycoin/quant_bc` main 分支。
> 最终版本:v1.4.2(commit `386b4b4`)

---

## 一、项目定位AI Crypto Quant Trading System — 项目综合报告

> 归档日期:2026-10-16
> 状态:**实盘环(testnet)已终止**(GLM API 欠费,不再续费),系统代码与数据完整归档于 GitHub `Jeycoin/quant_bc` main 分支。
> 最终版本:v1.4.2(commit `386b4b4`)

---

## 一、项目定位

一个 **AI 驱动的加密货币量化研究与执行平台**,而不是"预测涨跌的 AI":

```
Observe → Understand → Hypothesis → Cost 验证 → Risk 验证 → Execute → Measure → Replay → Learn
```

核心原则:LLM 负责理解复杂信息与提出策略提案;**所有确定性计算(费用、滑点、风险限额)由代码门控负责**;交易执行完全交给成熟框架 Hummingbot,不自研交易引擎。

---

## 二、实盘(Testnet)最终报告

**运行区间**:2026-09-27 → 2026-10-16 18:39(19.6 天,因 GLM 欠费终止)
**环境**:Binance Perpetual Testnet,20x 杠杆上限,PAPER/TESTNET 安全模式

### 2.1 账户结果

| 指标               | 数值                               |
| ------------------ | ---------------------------------- |
| 总权益(终止时)     | 10,822.16 USDT                     |
| 已实现 PnL         | **+3.84 USDT**(7 笔平仓,5 盈 2 平) |
| 未实现浮动         | +0.92 USDT(3 笔遗留持仓)           |
| 合计               | **约 +4.76 USDT,收益率 ≈ +0.04%**  |
| 累计手续费         | 0.74 USDT                          |
| 活跃执行器(终止时) | 3 个网格 RUNNING                   |

### 2.2 平仓明细

| 时间  | 币种     | 净利  | 费用 | 关闭原因       |
| ----- | -------- | ----- | ---- | -------------- |
| 09-27 | BTC 网格 | +0.19 | 0.03 | SYSTEM_CLEANUP |
| 10-14 | XRP 网格 | +1.42 | 0.09 | TAKE_PROFIT    |
| 10-14 | XRP 网格 | +0.58 | 0.18 | TAKE_PROFIT    |
| 10-14 | XRP 网格 | +1.55 | 0.31 | POSITION_HOLD  |
| 10-15 | XRP 网格 | 0.00  | 0.00 | POSITION_HOLD  |
| 10-15 | SOL 网格 | +0.10 | 0.02 | POSITION_HOLD  |
| 10-15 | SOL 网格 | 0.00  | 0.00 | POSITION_HOLD  |

### 2.3 决策行为统计(695 次决策)

| 动作                   | 次数 | 占比  |
| ---------------------- | ---- | ----- |
| WAIT(不交易)           | 435  | 62.6% |
| WATCH(观察)            | 134  | 19.3% |
| 网格提案+执行          | 98   | 14.1% |
| 方向单提案             | 12   | 1.7%  |
| 其他(SCAN/HOLD/REJECT) | 15   | 2.2%  |

**门控拦截 64 次**:Cost Validator 59 次(其中 48 次是 v1.4.2 修复的 notional 解析 bug;8 次真实经济性不达标,如 gross 0.0017 < 成本 0.0033);Entry Gate 3 次(RSI 94.6 追顶拦截、8h 动量背离拦截等)。

### 2.4 结论

系统**小幅盈利且零风险事故**:19 天无一次绕过门控、无一次超额开仓、无安全边界违规。瓶颈不在纪律而在**交易经济学**——500 USDT 级网格的利润空间被费用吃掉大半,AI 多数时候选择 WAIT 是正确的。方向单通道因 notional bug 直到最后一天才真正可用,未获得有效样本。

---

## 三、系统架构

```
                ┌───────────────────────┐
                │ Market Data (Hummingbot│
                │  + Binance testnet)   │
                └───────────┬───────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
  ┌───────────┐      ┌─────────────┐     ┌─────────────┐
  │ K线/资金费 │      │ 情报层       │     │ 账户/持仓    │
  │ 44 因子    │      │ 新闻/情绪/   │     │ (Hummingbot) │
  │            │      │ 链上/衍生品  │     └──────┬──────┘
  └─────┬─────┘      └──────┬──────┘            │
        └───────────────────┼───────────────────┘
                            ▼
                  ┌──────────────────┐
                  │  LLM Agent        │  GLM-4.5-flash(主力)
                  │  30min 决策周期    │  结构化 JSON 提案
                  └────────┬─────────┘
                           ▼
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
  ┌───────────┐     ┌─────────────┐    ┌──────────────┐
  │ Entry Gate │     │ Cost Validator│   │ Risk Validator│
  │ (方向准入)  │     │ (经济可行性)  │    │ (敞口/回撤)   │
  └─────┬─────┘     └──────┬──────┘    └──────┬───────┘
        └──────────────────┼──────────────────┘
                           ▼
              ┌────────────────────────┐
              │ Grid Protection 状态机   │
              │ NORMAL/WARNING/         │
              │ DEFENSIVE/EXIT          │
              └───────────┬────────────┘
                          ▼
              ┌────────────────────────┐
              │ Protections 事后熔断     │
              │ (连亏/回撤/低收益拉黑)   │
              └───────────┬────────────┘
                          ▼
                   Hummingbot Executors
                   (grid / position)
                          ▼
                   Exchange (testnet)
                          ▼
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   Analytics DB      Memory(复盘记忆)    Dashboard
   (SQLite)          (agent_memory.db)   (Next.js :3000)
```

**六道确定性门控**(LLM 无权绕过):

1. **Safety 边界**(config/settings.yaml):connector 白名单(仅 paper/testnet)、币种白名单、单笔 ≤500 USDT、持仓 ≤3 个、禁止删凭证
2. **Entry Gate**(方向单准入):方向须与 8h 动量 + EMA(8/21) 一致;RSI≥75/≤25 禁追;BREAKOUT 需量能确认;LONG 限区间下 60%,SHORT 限上 60%;波动率坍缩禁入
3. **Cost Validator**(经济可行性):期望毛利 − 双边手续费 − 滑点 − 安全边际 > 0,否则 REJECT
4. **Risk Validator**(组合风险):总敞口 ≤8000、单币 ≤4000、回撤 ≤10%
5. **Grid Protection**(网格状态机):趋势强度/ATR 上升 → WARNING;突破 48bar 区间 0.5×ATR → EXIT
6. **Protections**(事后熔断,Freqtrade 移植):连亏 4 笔/24h 全局暂停 6h;回撤 3% 暂停 12h;持续亏损币种拉黑 72h

---

## 四、技术栈

| 层           | 技术                                                         |
| ------------ | ------------------------------------------------------------ |
| 语言         | Python ≥3.11                                                 |
| 交易基础设施 | **Hummingbot**(executors、connectors、订单管理,经 hummingbot-api-client) |
| Agent 通信   | MCP(Model Context Protocol)+ Hummingbot REST API             |
| LLM          | GLM-4.5-flash(主力)、GLM-4.5-air、DeepSeek、Qwen(OpenAI 兼容接口,可插拔) |
| 分析存储     | SQLite(`data/analytics.db`:decision_events / trade_events / review_events / experiments) |
| Agent 记忆   | SQLite(`data/agent_memory.db`)                               |
| Dashboard    | FastAPI 后端(:8200)+ Next.js/pnpm 前端(:3000)                |
| 回测         | 自研历史前瞻回放(historical forward-replay,决策点只见到当时数据,无未来函数) |
| 测试         | pytest,130 个测试                                            |
| 实验管理     | 预注册文档 + experiment_id + 版本指纹(model/prompt/config/feature/executor) |
| 运维         | watchdog(45min 静默自动重启)、keep-awake、cron 巡检          |

---

## 五、因子库(44 个,agent/market_features.py)

实盘与回放共用同一函数 `compute_market_features()`,保证线上线下一致。

### 5.1 收益/动量(6)

`ret_1bar_pct` `ret_2bar_pct` `ret_4bar_pct` `ret_8bar_pct` `ret_16bar_pct` `ret_48bar_pct`

### 5.2 区间结构(4)

`range_8bar`(high/low/pos)、`range_48bar`、`bars_since_48bar_high`、`dist_from_48bar_high_pct`

### 5.3 波动率(7)

`realized_vol_8bar_pct` `realized_vol_48bar_pct` `atr_14bar_pct` `atr_pctile_48bar` `bb_width_pct` `chop_14bar`(震荡度) `path_pct`(路径效率)

### 5.4 量价(4)

`volume_z_48bar` `volume_z_deseason`(去季节性量能 z) `up_vol_share_8bar`(上涨量占比) `pv_corr_12bar`(量价相关)

### 5.5 趋势(9)

`ema_fast_8bar` `ema_slow_21bar` `ema_cross` `ema_dist_pct` `ema50_dist_pct` `adx_14bar` `macd_hist_pct` `efficiency_ratio_16bar` `ewo_pct`(艾略特波浪振荡器)

### 5.6 截面/相对(3)

`ret_rank_4bar` `ret_rank_8bar`(全币种收益分位)、`streak_bars`(连续阴阳)

### 5.7 振荡器(2)

`rsi_14bar` `rsi_slope_4bar`

### 5.8 K线解剖(3)

`upper_wick_pct` `lower_wick_pct` `cascade_signal`(连环止损信号)

### 5.9 衍生品(2)

`funding_rate` `funding_z_7d`(资金费 7 日 z 分数)

### 5.10 其他(3)

`vwap_dist_48bar_pct`(VWAP 乖离)、`htf_2h`(2h 高级别上下文块)、元数据(`bar_minutes`/`asof_ts`/`price`)

---

## 六、情报层(intelligence/,6 类数据源)

| 源              | 数据                           | 状态          |
| --------------- | ------------------------------ | ------------- |
| RSS News        | 加密新闻(标题/摘要/严重度打分) | 运行中        |
| Fear & Greed    | 恐惧贪婪指数 + 变化            | 运行中        |
| Blockchain.info | BTC 链上网络活跃度             | 运行中        |
| Hyperliquid     | 全币种资金费 + 持仓量(OI)      | 运行中,免 key |
| LunarCrush      | 社交量/情绪/创作者活动         | 需 API key    |
| Glassnode       | 链上因子                       | 需 API key    |

采集器 `scripts/collect_intelligence.py` 每 15 分钟运行,各源独立失败互不影响。所有情报带 timestamp/age/confidence/relevance,经归一化→聚合→特征提取后才进 LLM(不喂原始文本)。

---

## 七、实验历史(预注册 + A/B 文化)

30+ 个实验,关键节点:

| 实验                                    | 结论                                                         |
| --------------------------------------- | ------------------------------------------------------------ |
| bt-replay-7d(exp-a856ad913012,不可修改) | 首次暴露交易经济学问题:网格毛利 +0.21 vs 费用 -5.21,净 -5.00 → 催生 Cost Validator |
| bt-replay-30d/90d(DeepSeek/GLM)         | 多模型对比:决策质量差异不显著,模型不是瓶颈                   |
| **rule-proposer null test**             | 纯 EMA 规则 vs LLM 提案结果几乎一致 → "AI 提案层在当前因子集上增量≈0"的重要负结果 |
| exit-logic A/B(10-10)                   | 采纳 underwater-only TIME_STOP(v1.2)                         |
| enriched-factors 7d/30d(10-12/13)       | 预注册阈值"压线达成":36 因子栈 vs 原栈 30d 净 PnL 改善,但统计功效有限 |
| counterfactual replay ×3                | 反事实回放框架验证 LLM 决策路径可复现                        |

**核心发现(冷血结论)**:在纯技术因子、5 币种、六道门控下,LLM 与简单规则不可区分;信息增量假设未被证明也未被证伪——需要更长前瞻样本,这也是实盘环持续积累数据的原因(现因欠费中断)。

---

## 八、版本演进

- **v0.4-0.8**:多源情报栈、Cost/Risk Validator、Grid Protection、方向单框架(禁空→高置信 BREAKOUT)、多币种(BTC/ETH/SOL/XRP/SUI)
- **v1.1**:AI-Native 架构设计(沙箱+A/B+shadow,设计文档 `docs/design/v1.1-ai-native-architecture.md`)
- **v1.2**:underwater-only TIME_STOP 退出逻辑(预注册 A/B 采纳)
- **v1.3**:高级别上下文块 + 资金费动量 + 历史业绩上下文
- **v1.4**(commit `32395ff`):因子 36→44(借鉴 Phandas/Freqtrade/StratEvo)、Protections 四件套、复合适应度(GT-Score)、因子权重进化 harness(GA,实验性)
- **v1.4.1**:网格强制 `keep_position=false`(修 XRP 库存浮亏教训:套利腿赚 +3.55,遗留库存浮亏 -17.9)
- **v1.4.2**:修复 position notional 解析 bug(09-28 起堵死方向单通道,LLM 把 take_profit 放顶层/给 quote 数量而代码只认 base)

---

## 九、经验教训(写给未来的自己)

1. **门控比模型重要**:19 天零事故靠的是六道确定性门控,不是 LLM 聪明
2. **经济学先于策略**:gross − fees − slippage − margin > 0 这个简单不等式拦掉了大部分"看起来不错"的交易
3. **负结果也是结果**:rule-null test 证明 LLM 增量≈0,避免了在 prompt 调参上无限烧钱
4. **预注册防止自欺**:所有 A/B 先写判决规则再跑实验,"压线达成"也如实记录
5. **基础设施教训**:GLM 断连是常态(回放 10 次连续失败即终止 + `--resume-exp` 续跑);Windows 电池供电会绕过 keep-awake(夜间必须插电);Bash 后台任务默认 600s 超时必须 `disable_timeout`
6. **遗留库存是配置错误的产物**,不是策略头寸——该手动平掉锁定亏损

---

## 十、重启指南

如未来恢复运行:

1. 充值 GLM 或更换 `config` 中的 LLM key(DeepSeek/Qwen 备用 key 均已失效,需新的)
2. 启动顺序:Hummingbot → `scripts/collect_intelligence.py --loop 15` → `scripts/agent_loop.py` → `scripts/watchdog.py`(全部 `disable_timeout`)
3. 处置遗留持仓(XRP 580.7 / BTC 0.0012 / SOL 2.67,建议手动平仓)
4. 数据积累中断点:2026-10-16 19:00 起无决策数据
5. 恢复后优先:用积累的样本跑 v1.4 因子栈的预注册 A/B;12 月情报源 IC 复验;`scripts/evolve_weights.py` 正式权重进化

---

*报告由系统运维 Agent 自动生成,数据来源:`data/analytics.db`、Hummingbot API、git log。*一个 **AI 驱动的加密货币量化研究与执行平台**,而不是"预测涨跌的 AI":

```
Observe → Understand → Hypothesis → Cost 验证 → Risk 验证 → Execute → Measure → Replay → Learn
```

核心原则:LLM 负责理解复杂信息与提出策略提案;**所有确定性计算(费用、滑点、风险限额)由代码门控负责**;交易执行完全交给成熟框架 Hummingbot,不自研交易引擎。

---

## 二、实盘(Testnet)最终报告

**运行区间**:2026-09-27 → 2026-10-16 18:39(19.6 天,因 GLM 欠费终止)
**环境**:Binance Perpetual Testnet,20x 杠杆上限,PAPER/TESTNET 安全模式

### 2.1 账户结果

| 指标 | 数值 |
|---|---|
| 总权益(终止时) | 10,822.16 USDT |
| 已实现 PnL | **+3.84 USDT**(7 笔平仓,5 盈 2 平) |
| 未实现浮动 | +0.92 USDT(3 笔遗留持仓) |
| 合计 | **约 +4.76 USDT,收益率 ≈ +0.04%** |
| 累计手续费 | 0.74 USDT |
| 活跃执行器(终止时) | 3 个网格 RUNNING |

### 2.2 平仓明细

| 时间 | 币种 | 净利 | 费用 | 关闭原因 |
|---|---|---|---|---|
| 09-27 | BTC 网格 | +0.19 | 0.03 | SYSTEM_CLEANUP |
| 10-14 | XRP 网格 | +1.42 | 0.09 | TAKE_PROFIT |
| 10-14 | XRP 网格 | +0.58 | 0.18 | TAKE_PROFIT |
| 10-14 | XRP 网格 | +1.55 | 0.31 | POSITION_HOLD |
| 10-15 | XRP 网格 | 0.00 | 0.00 | POSITION_HOLD |
| 10-15 | SOL 网格 | +0.10 | 0.02 | POSITION_HOLD |
| 10-15 | SOL 网格 | 0.00 | 0.00 | POSITION_HOLD |

### 2.3 决策行为统计(695 次决策)

| 动作 | 次数 | 占比 |
|---|---|---|
| WAIT(不交易) | 435 | 62.6% |
| WATCH(观察) | 134 | 19.3% |
| 网格提案+执行 | 98 | 14.1% |
| 方向单提案 | 12 | 1.7% |
| 其他(SCAN/HOLD/REJECT) | 15 | 2.2% |

**门控拦截 64 次**:Cost Validator 59 次(其中 48 次是 v1.4.2 修复的 notional 解析 bug;8 次真实经济性不达标,如 gross 0.0017 < 成本 0.0033);Entry Gate 3 次(RSI 94.6 追顶拦截、8h 动量背离拦截等)。

### 2.4 结论

系统**小幅盈利且零风险事故**:19 天无一次绕过门控、无一次超额开仓、无安全边界违规。瓶颈不在纪律而在**交易经济学**——500 USDT 级网格的利润空间被费用吃掉大半,AI 多数时候选择 WAIT 是正确的。方向单通道因 notional bug 直到最后一天才真正可用,未获得有效样本。

---

## 三、系统架构

```
                ┌───────────────────────┐
                │ Market Data (Hummingbot│
                │  + Binance testnet)   │
                └───────────┬───────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
  ┌───────────┐      ┌─────────────┐     ┌─────────────┐
  │ K线/资金费 │      │ 情报层       │     │ 账户/持仓    │
  │ 44 因子    │      │ 新闻/情绪/   │     │ (Hummingbot) │
  │            │      │ 链上/衍生品  │     └──────┬──────┘
  └─────┬─────┘      └──────┬──────┘            │
        └───────────────────┼───────────────────┘
                            ▼
                  ┌──────────────────┐
                  │  LLM Agent        │  GLM-4.5-flash(主力)
                  │  30min 决策周期    │  结构化 JSON 提案
                  └────────┬─────────┘
                           ▼
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
  ┌───────────┐     ┌─────────────┐    ┌──────────────┐
  │ Entry Gate │     │ Cost Validator│   │ Risk Validator│
  │ (方向准入)  │     │ (经济可行性)  │    │ (敞口/回撤)   │
  └─────┬─────┘     └──────┬──────┘    └──────┬───────┘
        └──────────────────┼──────────────────┘
                           ▼
              ┌────────────────────────┐
              │ Grid Protection 状态机   │
              │ NORMAL/WARNING/         │
              │ DEFENSIVE/EXIT          │
              └───────────┬────────────┘
                          ▼
              ┌────────────────────────┐
              │ Protections 事后熔断     │
              │ (连亏/回撤/低收益拉黑)   │
              └───────────┬────────────┘
                          ▼
                   Hummingbot Executors
                   (grid / position)
                          ▼
                   Exchange (testnet)
                          ▼
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   Analytics DB      Memory(复盘记忆)    Dashboard
   (SQLite)          (agent_memory.db)   (Next.js :3000)
```

**六道确定性门控**(LLM 无权绕过):

1. **Safety 边界**(config/settings.yaml):connector 白名单(仅 paper/testnet)、币种白名单、单笔 ≤500 USDT、持仓 ≤3 个、禁止删凭证
2. **Entry Gate**(方向单准入):方向须与 8h 动量 + EMA(8/21) 一致;RSI≥75/≤25 禁追;BREAKOUT 需量能确认;LONG 限区间下 60%,SHORT 限上 60%;波动率坍缩禁入
3. **Cost Validator**(经济可行性):期望毛利 − 双边手续费 − 滑点 − 安全边际 > 0,否则 REJECT
4. **Risk Validator**(组合风险):总敞口 ≤8000、单币 ≤4000、回撤 ≤10%
5. **Grid Protection**(网格状态机):趋势强度/ATR 上升 → WARNING;突破 48bar 区间 0.5×ATR → EXIT
6. **Protections**(事后熔断,Freqtrade 移植):连亏 4 笔/24h 全局暂停 6h;回撤 3% 暂停 12h;持续亏损币种拉黑 72h

---

## 四、技术栈

| 层 | 技术 |
|---|---|
| 语言 | Python ≥3.11 |
| 交易基础设施 | **Hummingbot**(executors、connectors、订单管理,经 hummingbot-api-client) |
| Agent 通信 | MCP(Model Context Protocol)+ Hummingbot REST API |
| LLM | GLM-4.5-flash(主力)、GLM-4.5-air、DeepSeek、Qwen(OpenAI 兼容接口,可插拔) |
| 分析存储 | SQLite(`data/analytics.db`:decision_events / trade_events / review_events / experiments) |
| Agent 记忆 | SQLite(`data/agent_memory.db`) |
| Dashboard | FastAPI 后端(:8200)+ Next.js/pnpm 前端(:3000) |
| 回测 | 自研历史前瞻回放(historical forward-replay,决策点只见到当时数据,无未来函数) |
| 测试 | pytest,130 个测试 |
| 实验管理 | 预注册文档 + experiment_id + 版本指纹(model/prompt/config/feature/executor) |
| 运维 | watchdog(45min 静默自动重启)、keep-awake、cron 巡检 |

---

## 五、因子库(44 个,agent/market_features.py)

实盘与回放共用同一函数 `compute_market_features()`,保证线上线下一致。

### 5.1 收益/动量(6)
`ret_1bar_pct` `ret_2bar_pct` `ret_4bar_pct` `ret_8bar_pct` `ret_16bar_pct` `ret_48bar_pct`

### 5.2 区间结构(4)
`range_8bar`(high/low/pos)、`range_48bar`、`bars_since_48bar_high`、`dist_from_48bar_high_pct`

### 5.3 波动率(7)
`realized_vol_8bar_pct` `realized_vol_48bar_pct` `atr_14bar_pct` `atr_pctile_48bar` `bb_width_pct` `chop_14bar`(震荡度) `path_pct`(路径效率)

### 5.4 量价(4)
`volume_z_48bar` `volume_z_deseason`(去季节性量能 z) `up_vol_share_8bar`(上涨量占比) `pv_corr_12bar`(量价相关)

### 5.5 趋势(9)
`ema_fast_8bar` `ema_slow_21bar` `ema_cross` `ema_dist_pct` `ema50_dist_pct` `adx_14bar` `macd_hist_pct` `efficiency_ratio_16bar` `ewo_pct`(艾略特波浪振荡器)

### 5.6 截面/相对(3)
`ret_rank_4bar` `ret_rank_8bar`(全币种收益分位)、`streak_bars`(连续阴阳)

### 5.7 振荡器(2)
`rsi_14bar` `rsi_slope_4bar`

### 5.8 K线解剖(3)
`upper_wick_pct` `lower_wick_pct` `cascade_signal`(连环止损信号)

### 5.9 衍生品(2)
`funding_rate` `funding_z_7d`(资金费 7 日 z 分数)

### 5.10 其他(3)
`vwap_dist_48bar_pct`(VWAP 乖离)、`htf_2h`(2h 高级别上下文块)、元数据(`bar_minutes`/`asof_ts`/`price`)

---

## 六、情报层(intelligence/,6 类数据源)

| 源 | 数据 | 状态 |
|---|---|---|
| RSS News | 加密新闻(标题/摘要/严重度打分) | 运行中 |
| Fear & Greed | 恐惧贪婪指数 + 变化 | 运行中 |
| Blockchain.info | BTC 链上网络活跃度 | 运行中 |
| Hyperliquid | 全币种资金费 + 持仓量(OI) | 运行中,免 key |
| LunarCrush | 社交量/情绪/创作者活动 | 需 API key |
| Glassnode | 链上因子 | 需 API key |

采集器 `scripts/collect_intelligence.py` 每 15 分钟运行,各源独立失败互不影响。所有情报带 timestamp/age/confidence/relevance,经归一化→聚合→特征提取后才进 LLM(不喂原始文本)。

---

## 七、实验历史(预注册 + A/B 文化)

30+ 个实验,关键节点:

| 实验 | 结论 |
|---|---|
| bt-replay-7d(exp-a856ad913012,不可修改) | 首次暴露交易经济学问题:网格毛利 +0.21 vs 费用 -5.21,净 -5.00 → 催生 Cost Validator |
| bt-replay-30d/90d(DeepSeek/GLM) | 多模型对比:决策质量差异不显著,模型不是瓶颈 |
| **rule-proposer null test** | 纯 EMA 规则 vs LLM 提案结果几乎一致 → "AI 提案层在当前因子集上增量≈0"的重要负结果 |
| exit-logic A/B(10-10) | 采纳 underwater-only TIME_STOP(v1.2) |
| enriched-factors 7d/30d(10-12/13) | 预注册阈值"压线达成":36 因子栈 vs 原栈 30d 净 PnL 改善,但统计功效有限 |
| counterfactual replay ×3 | 反事实回放框架验证 LLM 决策路径可复现 |

**核心发现(冷血结论)**:在纯技术因子、5 币种、六道门控下,LLM 与简单规则不可区分;信息增量假设未被证明也未被证伪——需要更长前瞻样本,这也是实盘环持续积累数据的原因(现因欠费中断)。

---

## 八、版本演进

- **v0.4-0.8**:多源情报栈、Cost/Risk Validator、Grid Protection、方向单框架(禁空→高置信 BREAKOUT)、多币种(BTC/ETH/SOL/XRP/SUI)
- **v1.1**:AI-Native 架构设计(沙箱+A/B+shadow,设计文档 `docs/design/v1.1-ai-native-architecture.md`)
- **v1.2**:underwater-only TIME_STOP 退出逻辑(预注册 A/B 采纳)
- **v1.3**:高级别上下文块 + 资金费动量 + 历史业绩上下文
- **v1.4**(commit `32395ff`):因子 36→44(借鉴 Phandas/Freqtrade/StratEvo)、Protections 四件套、复合适应度(GT-Score)、因子权重进化 harness(GA,实验性)
- **v1.4.1**:网格强制 `keep_position=false`(修 XRP 库存浮亏教训:套利腿赚 +3.55,遗留库存浮亏 -17.9)
- **v1.4.2**:修复 position notional 解析 bug(09-28 起堵死方向单通道,LLM 把 take_profit 放顶层/给 quote 数量而代码只认 base)

---

## 九、经验教训(写给未来的自己)

1. **门控比模型重要**:19 天零事故靠的是六道确定性门控,不是 LLM 聪明
2. **经济学先于策略**:gross − fees − slippage − margin > 0 这个简单不等式拦掉了大部分"看起来不错"的交易
3. **负结果也是结果**:rule-null test 证明 LLM 增量≈0,避免了在 prompt 调参上无限烧钱
4. **预注册防止自欺**:所有 A/B 先写判决规则再跑实验,"压线达成"也如实记录
5. **基础设施教训**:GLM 断连是常态(回放 10 次连续失败即终止 + `--resume-exp` 续跑);Windows 电池供电会绕过 keep-awake(夜间必须插电);Bash 后台任务默认 600s 超时必须 `disable_timeout`
6. **遗留库存是配置错误的产物**,不是策略头寸——该手动平掉锁定亏损

---

## 十、重启指南

如未来恢复运行:

1. 充值 GLM 或更换 `config` 中的 LLM key(DeepSeek/Qwen 备用 key 均已失效,需新的)
2. 启动顺序:Hummingbot → `scripts/collect_intelligence.py --loop 15` → `scripts/agent_loop.py` → `scripts/watchdog.py`(全部 `disable_timeout`)
3. 处置遗留持仓(XRP 580.7 / BTC 0.0012 / SOL 2.67,建议手动平仓)
4. 数据积累中断点:2026-10-16 19:00 起无决策数据
5. 恢复后优先:用积累的样本跑 v1.4 因子栈的预注册 A/B;12 月情报源 IC 复验;`scripts/evolve_weights.py` 正式权重进化

---

*报告由系统运维 Agent 自动生成,数据来源:`data/analytics.db`、Hummingbot API、git log。*
