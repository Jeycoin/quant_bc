# v0.4 Phase 10-11:信息消融与模型 A/B 方法论

日期:2026-09-28

## 为什么消融不能用历史回放做

历史回放(bt-replay-*)只有 Market-only 一种配置:Socia/On-chain/News
数据采集从 2026-09-28 才开始,历史上没有这些数据的快照。回放时注入
"现在的" social/news 会造成 **前视偏差(look-ahead bias)**,实验无效。

因此消融只能**前向进行**(forward ablation),依赖 data/intelligence.db
从现在起累积的时间戳数据。

## 消融设计(前向)

四个配置,共享同一时段、同一 prompt、同一 validator:

| 配置 | 输入 | 实现 |
|---|---|---|
| A Market-only | 行情 | `INTEL_DISABLED=1` |
| B +Social | 行情+社交/情绪 | intel 注入,屏蔽 onchain/news/narrative |
| C +Social+On-chain | +链上 | intel 注入,屏蔽 news/narrative |
| D Full | 全部 | 默认 |

`INTEL_DISABLED=1` 已实现(agent/agent.py `_intel_context`)。B/C 的屏蔽
通过在 `IntelSnapshot.for_llm()` 加字段级开关实现(见 `INTEL_SECTIONS`
环境变量,逗号分隔:social,onchain,news,narratives)。

每个配置至少运行 7 天 testnet 前瞻,决策落 `decision_events`(含
`llm_model`、版本哈希、config_version)。比较指标:Net PnL、Max DD、
Profit Factor、Win Rate、WAIT 比例、拒绝率分布。

**样本量警告**:7 天约 170 个决策点/币种,交易次数可能只有个位数。
单窗口的 PnL 差异不构成统计证据 —— 只在多个 regime 窗口上稳定复现的
差异才可采信(master prompt §36)。

## 模型 A/B(Phase 11)

GLM-5.3-Flash 是 baseline。架构上模型只是环境变量
(`LLM_PROVIDER`/`LLM_MODEL`/`LLM_API_KEY`),回放与实盘共用同一
`create_llm_client()`。

A/B 规程:
1. 同一历史窗口、同一 prompt 文件、同一 settings.yaml,分别用两个模型
   跑 `scripts/backtest_agent.py`(每个模型独立 experiment_id,notes 里
   已自动记录 `model=...`)。
2. 比较:决策一致性(regime 分类一致率)、交易频率、COST/RISK 拒绝率、
   Net PnL、Profit Factor、Max DD。
3. **不要因为一次回放亏损就换模型**(master prompt §45)。模型切换需要
   多窗口证据 + 前向验证。

注意:历史回放存在训练数据污染风险(模型可能"记得"历史行情),
所有回放结论标注 indicative only;模型比较以前向实验为准。

## 未来:Model Router(暂缓)

`uncertain market / major event → stronger model` 的路由接口预留,但
v0.4 不实现 —— 没有证据表明路由比单一模型好之前,不增加复杂度
(master prompt §32)。
