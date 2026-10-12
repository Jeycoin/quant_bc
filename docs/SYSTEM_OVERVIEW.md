# AI Quant Trading System — 综合概述(v1.2+,2026-10-12)

> 一个 AI 驱动的加密货币量化研究与执行平台。LLM 负责理解市场、提出假设;
> 确定性代码负责算术、风控与底线;Hummingbot 负责成熟执行;回放与消融
> 实验负责验证"AI 是否真的带来了增量"。

## 1. 系统定位

不是"预测涨跌的 AI",而是:

```
Observe → Understand → Propose → Validate(Cost/Risk) → Execute
    → Measure → Replay → Learn → Test Again
```

最终目标:证明哪些信息、哪些策略、哪些市场环境,在扣除真实交易
成本后,能产生可重复的正向统计优势。

## 2. 总体架构

```
市场数据(Hyperliquid 公共 API / Hummingbot)
        │
情报采集层(15min 循环): 新闻RSS / 恐惧贪婪指数 / 链上流向 /
        │                Hyperliquid 衍生品(OI/资金费率)
        ▼
Intelligence Layer(intelligence.db)
  特征提取 → 信号检测 → 叙事引擎(Narrative Engine)
        │
        ▼
Trading Agent(glm-4.5-air, 单 Agent, 30min 决策周期)
  输入: 36 因子/币种 + 价格路径 + 情报快照 + 组合状态 + 自身记忆
  输出: 结构化提案(regime / action / confidence / evidence)
        │
        ▼
五道确定性门控(全部只看客观数字,不看 LLM 文字)
  ① Safety Guard    PAPER/LIVE 隔离、币种白名单、单笔/持仓上限
  ② Grid Protection  NORMAL/WARNING/DEFENSIVE/EXIT(仅网格)
  ③ Entry Gate       方向单必须顺 8h 动量+EMA,RSI 衰竭保护,
                     突破需放量,RANGING 不开方向单,回调纪律(仅方向单)
  ④ Cost Validator   预期毛利 − 双边手续费 − 滑点 − 资金费率 − 安全余量 > 0
  ⑤ Risk Validator   总敞口 / 单币敞口上限
        │
        ▼
Hummingbot Executors(grid / position)
  退出完全由代码执行: TP / SL(≈4×ATR) / 保本 / 移动止盈 /
  TIME_STOP(underwater 模式, v1.2)
        │
        ▼
Analytics(analytics.db): 决策事件 / 交易事件 / 复盘事件 / 实验记录
        │
        ▼
Memory(agent_memory.db) → 仅作为未来决策上下文,永不自动改规则
        │
        ▼
Dashboard(:3000 前端 / :8200 后端): KPI / 决策时间线 / 拒绝分析 /
  网格保护状态 / AI vs Baseline / Decision Replay
```

## 3. AI 的输入(每 30 分钟,每币种)

**市场因子(36 个,实盘与回放同一函数计算)**
- 收益: 1/2/4/8/16/48 bar 收益率(30m–24h)
- 趋势: EMA8/21 交叉与距离、EMA50 偏离、ADX(趋势强度)、
  Kaufman 效率比、MACD 柱
- 波动: 8/48bar 已实现波动、布林带宽(挤压检测)、ATR% 及其
  历史分位(vol regime)
- 量价: 成交量 z(含时段去季节化版本)、上涨 bar 量能占比、
  VWAP 偏离、连续涨/跌根数、RSI 及斜率
- 位置: 4h/24h 区间位置、距区间高点距离、距新高经过的 bar 数
- 形态: 最近 24 根 bar 的价格路径(%序列,AI 能看到形状)
- 衍生品: 资金费率、OI 及 24h 变化
- 跨币种: 与 BTC 的 48bar 相关性、8h 相对强度(识别"真领涨
  vs 只是 BTC beta")

**情报快照(带时间戳与时效)**
- 新闻(RSS, 去重+摘要)、恐惧贪婪指数、链上流向、
  Hyperliquid OI/资金费率;叙事引擎输出的 narrative +
  strength/confidence

**组合状态**: 权益、现金、持仓、挂单

**记忆(只读上下文)**: 每币种最近 3 次自己的判断、最近 4 笔
已平仓交易(方向/结果/时间)、最近复盘教训

## 4. 关键设计原则

1. **复用成熟框架**: 交易所连接、订单、持仓、策略执行全部
   Hummingbot;不自研交易引擎/连接器/订单系统
2. **AI 提议,代码裁决**: LLM 不能决定 approved=true;五道门控
   全部确定性;AI 不能改 API key、不能切 LIVE、不能移动止损
3. **PAPER 默认**: LIVE 需显式 `LIVE_TRADING=true`;Dashboard
   不能改变交易模式
4. **分析系统不阻塞交易系统**: 所有 analytics/memory 写入
   best-effort,失败降级(记日志+重连),绝不让交易循环崩溃
5. **实验纪律**: 预注册假设与判决规则;回放/反事实/零智能对照
   三层验证;历史实验不可修改(executor id 按实验命名空间隔离)
6. **不留 Chain-of-Thought**: 只存结构化结论与证据

## 5. 已验证的证据(截至 2026-10-12)

| 实验 | 结论 |
|---|---|
| 7d/30d 回放 + 规则对照 | **AI ≈ 纯规则**(两次确认):当前信息下 LLM 决策与 EMA 规则不可区分 |
| 7d 回放死因归因 | 网格毛利 +0.21、手续费 -5.21 → 催生 Cost Validator |
| 30d 回放归因 | 盈利全靠 11 笔拿住的 TP/移动止盈(+47.6);亏损全是被踢的 105 笔(止损 -65 + churn -24) |
| v1.2 退出逻辑 A/B(预注册) | underwater TIME_STOP 采纳为默认:-0.41% → -0.06%,maxDD 0.52→0.36 |
| 情报源前瞻 IC(13 天样本) | network_activity 等有弱信号,但**没有任何信源过门禁**;12 月初 60 天数据复验 |

## 6. 当前实盘运行状态

- 实盘环 v5(PAPER, 30min 周期, 每 6 周期复盘): 36 因子 +
  价格路径 + 记忆上下文,决策事件全部落库
- 情报采集环(15min): 持续积累,为 12 月 IC 复验攒数据
- Dashboard: localhost:3000
- 进行中实验: exp-1f8085469e25(增强栈 vs 原始栈,同 7d 窗口,
  预注册 docs/experiments/2026-10-12-enriched-factors-7d.md)

## 7. 已知边界与诚实结论

- AI 的增量**尚未被证明**。系统价值目前在于:干净的测量框架 +
  成本/风控门控 + 可复现的实验基础设施
- 如果 B 组消融实验(全情报栈)失败,按预注册计划回退到
  "规则执行 + AI 研究层"
- 样本量警告:7d/30d 回放的统计功效有限,所有结论均为指示性

## 8. 仓库与运维

- Repo: github.com/Jeycoin/quant_bc (main), 最新 c61af54
- 测试: 105 passed
- 关键脚本: scripts/agent_loop.py(实盘) / backtest_agent.py(回放,
  支持 --counterfactual-exp / --rule-proposer / --time-stop-mode) /
  collect_intelligence.py(情报) / forward_test_intel.py(前瞻 IC)
