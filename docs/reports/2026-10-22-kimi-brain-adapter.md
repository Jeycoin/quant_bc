# Kimi Brain Adapter — 人工驾驶舱接管 LLM 决策点

**日期**: 2026-10-22 · **版本**: v1.5-kimi-manual · **状态**: 已上线(dry-run 验证 + 一次真实创建/停止冒烟通过)

## 这是什么

GLM API 欠费弃用后,框架的 LLM 调用点被替换为"人工驾驶舱":上层会话里的 Kimi agent(或人)充当交易大脑,每 30 分钟拉一次决策上下文、写一份决策 JSON,由 `scripts/kimi_brain.py` 把它推过**既有的完整门控链**后执行。六道门控(safety → protections → grid protection → entry gate → cost → risk)零改动;`agent_loop.py` 保留不动。

```
大脑(会话) --读取--> kimi_brain.py context  --写决策--> decision.json
decision.json --kimi_brain.py execute--> schema -> min_confidence -> safety
    -> protections -> grid protection -> entry gate -> cost -> risk
    -> 通过: Hummingbot REST 建执行器 / 拒绝: 记录原因
    -> 无论结果: 落库 decision_events + memory
```

## 用法

```bash
# 1. 拉决策上下文(紧凑 JSON;约 20KB / 30-60s)
python scripts/kimi_brain.py context --out data/kimi_context.json
python scripts/kimi_brain.py context --indent 2        # 直接看

# 2. 执行决策文件(首次用 --dry-run:只验证+落库,不建执行器)
python scripts/kimi_brain.py execute decision.json --dry-run
python scripts/kimi_brain.py execute decision.json
```

决策文件支持三种形态:单个对象、对象数组、`{"decisions": [...]}`。一个文件可以装全部 5 个币种的决策(WAIT/WATCH 也落库,保持决策序列连续)。

### decision.json schema

```json
{"symbol": "BTC",
 "action": "WAIT | WATCH | PROPOSE_GRID | PROPOSE_POSITION",
 "regime": "TRENDING_BEAR", "strategy": "none",
 "confidence": 0.7,
 "evidence": "一句话证据",
 "thesis": "命题(PROPOSE_* 必填)", "invalidation": "证伪条件(PROPOSE_POSITION 必填)",
 "gate_adjustments": {"entry_gate.rsi_overbought": {"value": 80, "reason": "..."}},
 "executor_config": {"side": "LONG", "amount_quote": 200,
                     "take_profit": 0.02, "stop_loss": 0.01}}
```

- `executor_config` 缺省补全:`connector_name=binance_perpetual_testnet`(env `EXECUTOR_CONNECTOR` 可改)、`trading_pair=<symbol>-USDT`、`type` 按 action 推导。显式字段永远优先。
- grid 的 `keep_position` 强制 false(v1.4.1 红线,与旧环一致)。
- position 的 top-level tp/sl/time_limit 自动并入 `triple_barrier_config`,`amount_quote` 按现价折成 base `amount`(沿用 v1.4.2 归一化)。
- `gate_adjustments` 走 v1.5 全量机制:白名单校验、reason 必填、clamp + 事件、per-decision 作用域、cost/risk 恒用原配置。

### context 输出内容

| 段 | 来源 | 说明 |
|---|---|---|
| `market_features` | `agent._market_features_context()` | 5 币种 × 45 因子,30m bar,与回放/旧实盘同一函数同一数据源(HB probe `hyperliquid_perpetual`,浅历史自动回退 hyperliquid 公共 API) |
| `portfolio` | `/portfolio/state` + `/trading/positions` | equity、余额、持仓含浮动盈亏(三笔遗留:XRP 415.0、BTC 0.0012、SOL 2.67,binance_perpetual_testnet) |
| `active_executors` | `search_executors(RUNNING)` | id/类型/交易对/方向/金额/pnl |
| `intelligence` | `intelligence.snapshot.build_snapshot` | 与旧 LLM 环相同的 for_llm 快照 |
| `memory` | `agent._memory_context()` | 近期判断/已平仓/track record/lessons |
| `gates.tunable` | `tunable_spec()` | 23 旋钮 default/min/max |
| `gates.protections` | `evaluate_protections()` | 当前全局锁/币种锁/剩余时间 |
| `gates.grid_state` | 公共 1h K 线 + `evaluate_grid_state` | 见下文"差异 4" |
| `gates.hard_limits` | settings.yaml | safety/risk/entry_gate 原值(只读参考) |
| `recent_decisions` | decision_events 最近 10 条 | 含 age_min、outcome、rejection 摘要 |

## 与旧 LLM 环的差异

1. **传输层**:建执行器走 `hummingbot_api_client` REST(`POST /executors/`)而不是 MCP stdio。payload 语义相同(`executor_config` 内含 `type`),省掉每次 uvx 启动;安全校验仍在调用前完成。这是唯一有意的传输偏离。
2. **min_confidence 门生效**:旧 LLM 环没有这道门(tool call 不携带 confidence),回放有。kimi 决策携带 confidence,故按回放语义启用(默认 0.6,tunable,拒绝记 `CONF_REJECTED`)。
3. **time_stop_hours 旋钮生效**:旧 live 路径里该旋钮只记录不消费(position 的 time_limit 完全由 executor_config 携带)。现在 position 未显式给 `time_limit` 时,用有效值(默认 6h,可经 gate_adjustments 调)写入 barrier,与回放语义一致。
4. **context 的 grid_state 用公共 K 线**:门控自己的 `_grid_state` 只看 HB candles 端点(该端点只服务 connector 实盘收集到的历史,当前只有 3 根),无公共回退——旧环时它同样 fail-open。context 若复用它则永远显示 null,大脑失明;故 context 侧改用公共 1h K 线(150h)计算,纯上下文展示,**门控路径未动**。
5. **落库标记**:`llm_model='kimi-code'`、`agent_version='v1.5-kimi-manual'`、`mode='PAPER'`;thesis/invalidation/applied_gate_adjustments/dry_run 标志存 `market_context` JSON(v1.5 做法,无 schema 变更)。dry-run 通过的记 `RECORDED` + `dry_run:true` + `risk_status:PASSED`。
6. **无 LLM 工具循环**:旧环 LLM 可自由调 get_market_data/manage_controllers 等;驾驶舱模式下大脑的"工具"就是 context JSON,执行面只剩 manage_executors/create 一条路。

## 验证记录(2026-10-22 实盘冒烟)

- `context`:5 币种 45 因子齐(BTC RSI 12.3 / ADX 57 / ema_cross BEAR)、三笔遗留持仓浮亏正确(BTC -2.54、SOL -0.06、XRP -5.45)、protections 无锁、grid_state SOL=WARNING 其余 NORMAL、20.4KB / 31s。
- WAIT → `RECORDED` 落库 ✓
- 逆趋势 LONG(带 rsi_overbought 95→clamp 85、time_stop 12h)→ 真实 entry gate 拒绝:`counter-trend: ema_cross=BEAR; 8h momentum -2.646%; vol ratio 0.39 < 0.7`,clamp 事件与 thesis/invalidation 全部落库 ✓
- XRP grid dry-run → 全门通过 `DRY_RUN_PASSED`,config 归一化(keep_position=false)✓
- XRP grid **真实创建** → executor RUNNING,id 落库 `EXECUTED`,随即 stop 清理,0 遗留运行执行器 ✓
- 测试:21 个新测试(`tests/test_kimi_brain.py`),全套 **178 passed**。
- 冒烟产生的 decision_events(标记 smoke test 字样)留在 analytics.db——是新数据不是历史改动。

## 局限与坑

1. **大脑只活在线会话里**:session 关闭 = 决策停止。已建执行器靠自身 barrier(tp/sl/time_limit)+ Hummingbot 端保护安全降级;grid 不会主动止损出场,靠 limit_price 兜底。
2. **grid protection 门实际是半盲的**(继承自旧环):HB candles 端点历史太浅时 `_grid_state` fail-open,DEFENSIVE/EXIT 拦不住新 grid。context 里能看到真实状态,但**门本身**仍可能放行。这是旧环就有的洞,本次未动门控逻辑;若要修,给 `_grid_state` 加公共 K 线回退(一行级别的改动,但属于门控行为变更,留给显式决策)。
3. **base-unit amount 的 notional 上限同旧环有缝**:position 归一化先于 knob clamp,`amount_quote` 折成 base `amount` 后,500 quote 的 per-order 上限和 notional clamp 都看不到它(与旧 live 环完全一致);risk 门的 symbol exposure(4000)仍兜住。决策侧请自觉用 ≤500 quote。
4. **每 30 分钟粒度**:context 生成要 30-60s,决策到执行又隔一层人工;这不是 HFT 框架,barrier 设计必须假设决策延迟。
5. **protections 依赖 trade_events 同步**:熔断器读 analytics.db 的 closed trades,依赖既有 sync 流程持续运行;sync 停了熔断器就看到旧世界(fail-open)。
6. **intelligence 新鲜度**:context 里的情报取决于采集器运行状态;各条目带 age,大脑自行判断。
7. 冒烟时观察:XRP 遗留持仓实际为 **415.0**(非先前记录的 580.7),以 context 的 portfolio 段为准——它是实时拉的。

## 文件

- `scripts/kimi_brain.py` — 全部新代码(context/execute/验证接线/落库)
- `tests/test_kimi_brain.py` — 21 个测试
- 未改动:六道门控、agent_loop.py、analytics schema、历史数据
