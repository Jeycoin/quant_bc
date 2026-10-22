# 门控参数下放重构(tunable gates,v1.5)

> 日期:2026-10-22
> 范围:把部分写死的门控参数以"硬编码区间 + clamp + 审计落库"的方式交给 AI 按决策调整。硬安全边界(safety 段、risk 上限、LIVE 开关)不下放,AI 连提案权都没有(不在 tunable 表内,提案即 unknown_path 拒绝)。
> 代码:`agent/tunable.py`(新)、`config/settings.yaml` 新增 `tunable:` 段、`agent/agent.py`(live 接线)、`scripts/backtest_agent.py`(回放接线)、`agent/prompts/trading_manager.md`(prompt)、`tests/test_tunable.py`(17 个新测试)。

## 诚实声明(先读)

- **GLM API 欠费未恢复,本次重构无法做回放或实盘 A/B 验证**,只有单元测试(17 个新测试,全套 157 通过)和一条端到端 smoke(默认门拦 RSI=80 LONG → AI 请求 95 → clamp 到 85 → 有效参数下放行 → 原配置不变)保障机制正确性。效果是"AI 决策空间扩大",**收益影响未知**。未来 API 恢复后必须用 `scripts/performance_report.py` 的七维度带 CI 指标做"AI 调参 vs 固定参数"正式 A/B。
- 此方向(扩大 AI 自主权)与历史实验证据存在张力:前两轮报告显示"门控收紧有效"(当前门控组 ctrl 重跑优于原始运行;6h 冷却反事实 +43~49%)。下放刻度给 AI 的理由是"门控的固定刻度可能同样不是最优",但这正是需要 A/B 验证的假设,不是结论。
- 一处对红线区间的工程性偏离:红线给定网格 take_profit 区间 0.5%-5%,但现行生产网格(实盘与回放)tp 均为 0.1%-0.2%,0.5% 下限会把所有现存网格配置 clamp 到失真。实际实现取 **0.1%-5%**,待网格经济学重新验证后再议。

## tunable 参数完整清单(config/settings.yaml `tunable:` 段)

| 路径 | default | min | max |
|---|---|---|---|
| entry_gate.rsi_overbought | 75 | 65 | 85 |
| entry_gate.rsi_oversold | 25 | 15 | 35 |
| entry_gate.breakout_volume_z | 1.0 | 0.5 | 2.5 |
| entry_gate.long_max_pos | 0.6 | 0.5 | 0.8 |
| entry_gate.short_min_pos | 0.4 | 0.2 | 0.5 |
| entry_gate.min_vol_ratio | 0.7 | 0.4 | 1.0 |
| time_stop_hours | 6 | 2 | 24 |
| protections.cooldown_stop_hours | 6 | 0 | 12 |
| grid_take_profit_pct | 0.002 | 0.001 | 0.05 |
| grid_stop_loss_pct | 0.02 | 0.005 | 0.05 |
| position_take_profit_pct | 0.02 | 0.005 | 0.05 |
| position_stop_loss_pct | 0.01 | 0.005 | 0.03 |
| protections.stoploss_guard.trade_limit | 4 | 2 | 8 |
| protections.max_drawdown.max_allowed_pct | 3 | 1 | 6 |
| protections.low_profit_pairs.min_trades | 4 | 2 | 10 |
| protections.low_profit_pairs.required_profit_quote | 0 | -5 | 5 |
| min_confidence | 0.6 | 0.5 | 0.85 |
| leverage | 1 | 1 | 20 |
| position_notional_quote | 200 | 50 | 500 |
| grid_protection.warn_trend_rise | 1.25 | 1.05 | 2.0 |
| grid_protection.defensive_trend_strength | 0.004 | 0.002 | 0.01 |
| grid_protection.warn_atr_rise | 1.3 | 1.05 | 2.0 |
| grid_protection.breakout_atr_mult | 0.5 | 0.25 | 1.5 |

executor 侧旋钮(leverage、notional、tp/sl)不走 gate_adjustments——它们是执行器属性,在创建时被 `clamp_executor_knobs` 确定性夹取,clamp 事件同样落库。

## 机制工作流(一个具体例子)

AI 在强趋势日想开多,RSI=80 会被默认入口门(ob=75)拦:

1. AI 在 `manage_executors(action="create")` 工具调用里附带:
   `gate_adjustments: {"entry_gate.rsi_overbought": {"value": 95, "reason": "ADX 42 trend day, RSI pinned"}}`
2. `agent/tunable.resolve_adjustments`:路径在表内 ✓、reason 非空 ✓、数值合法 ✓ → **clamp 95 → 85**(区间上限),记录 clamp 事件。
3. `effective_config` 生成**深拷贝**的有效参数集(原 config 不变,per-decision 作用域由构造保证)。
4. `_validate_executor_create(gate_config=eff)` 用有效参数跑 Protections → Grid Protection → Entry Gate → Cost → Risk;本例 Entry Gate 在 rsi_overbought=85 下放行,Cost/Risk 用原参数(成本与风险上限不可调)。
5. `applied_gate_adjustments`(overrides + clamp 事件 + 拒绝记录)写入 decision_events.market_context(JSON 列,无 schema 变更);executor 旋钮的 clamp 事件一并记录。
6. 决策结束,override 蒸发;下一个 30 分钟周期回到默认参数。

回放路径(backtest_agent.py)相同语义:analysis block 里的 gate_adjustments 逐 block 解析,作用于该 block 的 entry gate / grid protection / protections / min_confidence / time_stop / grid_tp,并落库。

**作用域决策**:默认且仅实现 per-decision。"session 内持续生效"档位被有意拒绝——调整理由(如"trend day")在 regime 变化后失效,持续的旧刻度比每周期重新提案更危险;LLM 每 30 分钟重新决策,重新提案成本为零。

## prompt 改动(agent/prompts/trading_manager.md)

- 新增 `# Gate adjustments (tunable knobs)` 段:完整旋钮表(default/min/max)、调用格式(JSON 示例)、3 个正例(强趋势放宽 RSI 上限、连亏后收紧 stoploss_guard、波段 thesis 拉长 time_stop)+ 1 个反例(**因为被门拦了而当场放宽同一道门**——明确禁止,理由必须来自拒绝之前的 regime 证据)。
- analysis block schema 增加 `thesis`、`invalidation`(开仓锁定)、可选 `gate_adjustments`;说明 WAIT/WATCH 不带调参。
- 回放附言(REPLAY_ADDENDUM)补一句 gate_adjustments 在回放中同样生效。

## 测试

- 新增 `tests/test_tunable.py` 17 个:合法调整、上下限 clamp+事件、未知路径拒绝(含 safety/risk 硬边界)、类型错误拒绝(str/list/dict/bool/None/NaN)、无理由拒绝、畸形 payload 不抛异常、整数旋钮保持整数、effective_config 深拷贝不变异原配置、leverage/tp/notional/amount 旋钮 clamp、边界内零事件。
- 全套 **157 个 pytest 通过**(140 旧 + 17 新)。

## 风险点(主动识别)

1. **调参过拟合**:AI 用当下的 regime 证据调刻度,可能系统性地在亏损 regime 放宽门控(反例已在 prompt 禁止,但只能靠日志审查执行,没有硬拦)。`applied_gate_adjustments` 落库就是为了事后能按"调整后 vs 默认"归因。
2. **live 表面的不对称**:live 中 gate_adjustments 必须随工具调用提交(工具调用先于最终 analysis block),AI 可能忘记这个顺序而在 block 里写——那种写法 live 不生效(只在回放生效)。prompt 已明确,但模型遵从度未验证(API 欠费无法测)。
3. **旋钮间交互未验证**:如同时放宽 rsi_overbought 和 min_vol_ratio 可能叠加出比单独放宽激进得多的有效门;单元测试只覆盖单旋钮语义。
4. ** clamp 向上**:notional 低于 50 会被 clamp 到 50(可能超过 AI 本意的小仓位试探);leverage 默认 1,AI 选 20 在区间内——区间上限即生产上限,区间内不再有额外审查。
5. **回放与 live 的消费点不完全一致**:min_confidence 只在回放消费(live 无 confidence 门),grid_take_profit_pct 在回放是 GridSim 间距、在 live 是 triple_barrier clamp——语义相近但非同一物,报告中如实说明。
6. **未做 A/B**:机制正确 ≠ 有益。上线顺序建议:API 恢复后先跑"固定参数 vs AI 调参"回放 A/B,再考虑 live 影子模式。
