# v0.4 Architecture Audit

日期:2026-09-28 · 范围:v0.4 master prompt §二(先审计,再修改)

## 数据流(已验证)

```
Hyperliquid 公共 API(行情)/ Binance Testnet(执行)
    ↓ Hummingbot API :8100(WSL2 docker)
    ↓ hummingbot-mcp(stdio, uvx 按需启动)
    ↓ agent/agent.py TradingAgent(单 Agent,GLM/Claude 可插拔)
    ↓ SafetyGuard(config/settings.yaml 白名单 + PAPER/LIVE 隔离)
    ↓ manage_executors → Hummingbot → Exchange
分析路径:decision/review/trade events → data/analytics.db(4 表)
         + data/agent_memory.db(4 表)→ dashboard_api → Dashboard
回放路径:scripts/backtest_agent.py(Hyperliquid 历史 K 线直连,
         与实盘共用 prompt/LLM,但执行是本地模拟器)
```

## §二 七点确认

1. **已存在**:MCP 工具循环、SafetyGuard(PAPER/LIVE、白名单、仓位上限)、
   结构化 analysis/review 块、decision/trade/review 事件落库、实验管理
   (experiments 表含 agent/prompt/config version)、7d 历史回放、
   固定网格基线、Memory 降级模式、Dashboard(/lab 含 Decision Replay)。
2. **可直接复用**:SafetyGuard 的 check 管线(validator 挂在这里)、
   analytics.store 的 additive schema 模式、Hummingbot 费率(可从交易所
   connector 查询)、backtest_agent 的 replay 骨架、Dashboard 轮询框架。
3. **缺失(v0.4 需新增)**:
   - Cost Validator(手续费+滑点+安全边际的确定性前置校验)——**完全缺失**,
     这是 bt-replay-7d 网格净亏 -5.0 USDT 的直接原因
   - Risk Validator(独立于 SafetyGuard 的策略级风险校验;SafetyGuard 只做
     硬边界,不做 exposure/回撤评估)
   - Grid Protection 状态机(NORMAL/WARNING/DEFENSIVE/EXIT)
   - REGIME_TRANSITION regime 值
   - On-chain / Social / News / Narrative 数据层——**全部缺失**
   - Feature Engine、统一 Intelligence Schema
   - trade_events 缺 gross_pnl / entry_fee / exit_fee / slippage 列
   - Social/Narrative 信号独立验证、信息消融实验
4. **需修改**(不重写):
   - `agent/agent.py`:executor create 前插入 propose→validate→execute
   - `agent/prompts/trading_manager.md`:输出 proposal schema(→ 新 prompt 版本哈希)
   - `analytics/store.py`:additive 列迁移
   - `scripts/backtest_agent.py`:回放路径同样走 validator(否则实验不可比)
5. **外部数据接入方式**:无现成 MCP;走成熟 REST API(LunarCrush /
   CryptoCompare / alternative.me 等),Normalize→Feature 后才进 LLM。
   原始帖子/文章永远不直接喂模型。
6. **进 LLM 的**:结构化 features、signals、events、narratives(带 timestamp/
   age_minutes/confidence)。
7. **确定性代码处理的**:手续费、滑点、仓位限额、risk 批准/拒绝、
   Grid 保护状态迁移、所有安全边界。

## 不可变约束

- `exp-a856ad913012`(bt-replay-7d)已完成,**禁止修改**
- `exp-d713167f1466`(7d testnet 前瞻)运行中;改 prompt 会产生新
  prompt_version,需在实验备注中声明断点,或先停实验
- DB 只做 additive migration

## 实施顺序(按 master prompt §四十三)

Phase 2 Cost/Risk 层 → Phase 3 Intelligence Schema → Phase 4 Social/News →
Phase 5 Feature/Narrative Engine → Phase 6 LLM 集成 → Phase 7 Dashboard →
Phase 8-11 回放/基线/消融/模型 A/B
