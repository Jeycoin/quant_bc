# AI Quant Trading Agent 使用文档

一个由大语言模型驱动的 AI 交易经理。它通过 Hummingbot 官方 MCP 工具观察市场、
分析行情、选择成熟策略、在 testnet（模拟盘）上执行交易、监控持仓并复盘。

**底层交易能力全部来自 Hummingbot，本项目只包含 Agent 编排层。**

```
LLM (GLM / Claude / 任意 OpenAI 兼容模型)
   ↓
agent/  ← 编排循环 + 安全护栏 + 交易记忆（本项目）
   ↓ stdio
hummingbot-mcp（11 个官方工具）
   ↓ HTTP
Hummingbot API（WSL2 Docker，端口 8100）
   ↓
交易所（行情：Hyperliquid 实盘只读；执行：Binance 合约 testnet 模拟盘）
```

---

## 1. 快速开始

### 1.1 启动基础设施（WSL2）

Hummingbot API 运行在 WSL2 的 Docker 里（三个容器：api / emqx / postgres）：

```bash
wsl bash -c "cd ~/hummingbot-api && docker compose up -d"
wsl bash -c "docker ps"   # 确认三个容器 Up 且 healthy
```

> 如果 WSL 异常重启过（Windows 更新、手动 `wsl --shutdown`），重新执行上面的命令即可，
> 容器配置了自动重启策略。

### 1.2 验证系统健康（Windows）

```bash
python scripts/verify_paper.py    # API 连通性 + testnet 连接器 + 行情数据
python scripts/test_mcp.py        # MCP 11 个工具 + 实时价格
```

### 1.3 与 Agent 对话（主要使用方式）

```bash
python -m agent.agent
```

进入交互模式后直接用自然语言提需求，例如：

```
> 扫描 BTC 和 ETH 的行情，分析市场状态
> 现在 BTC 永续的资金费率是多少？盘口深度怎么样？
> 在 testnet 上给我部署一个 300 USDT 以内的网格
> 查看当前所有执行器的运行状态和盈亏
> 复盘一下最近的交易决策
> quit    # 退出
```

### 1.4 单次任务模式（脚本化）

```bash
python scripts/e2e_mvp.py "你的指令，例如：查看 BTC 价格并分析"
```

---

## 2. 配置说明

### 2.1 `.env`（凭证与环境，不入库）

| 变量 | 说明 |
|---|---|
| `LIVE_TRADING` | **安全总开关**。`false`（默认）= PAPER 模式，只允许 testnet 连接器；`true` = 实盘 |
| `LLM_PROVIDER` | `anthropic` 或 `openai_compat` |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | Claude（provider=anthropic 时） |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | OpenAI 兼容模型（provider=openai_compat 时） |
| `HUMMINGBOT_API_URL` | 默认 `http://127.0.0.1:8100` |
| `HUMMINGBOT_USERNAME` / `HUMMINGBOT_PASSWORD` | Hummingbot API 凭证（部署时生成） |

**切换国产模型示例**（改 `.env` 三个变量即可）：

```bash
# 智谱 GLM
LLM_PROVIDER=openai_compat
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LLM_MODEL=glm-5.3-flash

# 阿里百炼
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus

# DeepSeek
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

### 2.2 `config/settings.yaml`（安全边界）

| 配置 | 当前值 | 说明 |
|---|---|---|
| `paper_connector_patterns` | `_paper_trade`, `testnet` | PAPER 模式下只允许名字含这些串的连接器 |
| `allowed_base_assets` | BTC, UBTC, ETH, UETH, SOL | 可交易币种白名单 |
| `allowed_quote_assets` | USDT, USDC, USD | 计价币白名单 |
| `max_order_quote_amount` | 500 | 单笔/单执行器最大金额（计价币） |
| `max_open_positions` | 3 | 最大同时持仓数 |
| `blocked_tool_actions` | `setup_connector: [delete]` | 任何模式下都禁止的操作 |

**Agent 可以**选择策略、币种、方向、在限额内调参数。
**Agent 不可以**删凭证、超限额、在非 testnet 连接器下单（PAPER 模式）、绕过护栏。
护栏代码在 `agent/safety.py`，每个工具调用都经过它。

---

## 3. 常见任务

### 查看市场
```
> 获取 BTC 最新价格、资金费率和最近 3 天的 1 小时 K线，分析市场状态
```

### 查看账户与持仓
```
> 查看 master_account 在所有连接器上的余额、持仓和活跃订单
```

### 部署 paper 策略（testnet）
```
> 在 binance_perpetual_testnet 上为 BTC-USDT 创建一个 grid_executor，
  区间 83700-84400，总仓位不超过 300 USDT，设置区间外止损
```
Agent 会先查余额、再创建、最后确认执行器状态并汇报。

### 监控与止损
```
> 查看所有运行中的执行器，汇报成交和盈亏
> 停止执行器 <ID> 并平仓
```

### 复盘（Research Mode，只读）
```
> 复盘最近的交易决策：每次操作的理由、结果和教训
```
决策记录存在 `data/agent_memory.db`（SQLite），复盘不会修改任何策略参数。

---

## 4. 当前环境的重要事实

- **行情来源**：Hyperliquid 实盘（只读）。Binance/OKX/Bybit 实盘 API 在本机网络不可达；
  Agent 也会自行fallback 到 gate_io 取价。
- **执行场所**：Binance 合约 testnet（`binance_perpetual_testnet`），5000 USDT 测试金。
  交易对格式为 `BTC-USDT`。
- **Hyperliquid 交易对格式**：现货 `UBTC-USDC`，永续 `BTC-USD`。
- **testnet 连接器不支持 K 线接口**：行情分析读实盘行情，执行走 testnet，属正常架构。
- **Hummingbot API 端口是 8100**（8000 被本机其他服务占用）。
- **WSL 空闲超时已禁用**（`C:\Users\ligao\.wslconfig` 中
  `instanceIdleTimeout=-1` / `vmIdleTimeout=-1`）。删除该配置会导致 WSL 每分钟重启。
- **容器出网代理**：`http://192.168.192.1:7890`（Clash Allow LAN），写在
  WSL `~/hummingbot-api/docker-compose.yml` 的 api 服务环境变量里。

---

## 5. 故障排查

| 现象 | 处理 |
|---|---|
| `Cannot connect to host 127.0.0.1:8100` | WSL 或容器没起：`wsl bash -c "cd ~/hummingbot-api && docker compose up -d"` |
| 容器反复重启、uptime 总是很小 | `.wslconfig` 的两个 idleTimeout 被删了，补回后 `wsl --shutdown` 再进 |
| 行情查询超时（binance/okx/bybit） | 预期内——这些实盘 API 被墙。用 hyperliquid 或 gate_io 的行情 |
| 容器完全无法出网 | 检查 Clash「允许局域网连接」是否开启；`docker compose up -d --force-recreate hummingbot-api` 重建容器 |
| MCP 启动失败 | `uvx --from hummingbot-mcp --with "mcp>=1.0,<2" hummingbot-mcp` 手动跑看报错 |
| LLM 401 | 检查 `.env` 里对应 provider 的 key 与 `LLM_PROVIDER` 是否匹配 |
| Agent 下单被拒（SAFETY VIOLATION） | 护栏工作正常。检查是否触碰到白名单/限额/testnet 限制 |

查看 Agent 决策日志：

```bash
python -c "from agent.memory.store import MemoryStore; m=MemoryStore('data/agent_memory.db'); [print(d['ts'], d['tool_name'], d['outcome'][:60]) for d in m.recent_decisions(20)]"
```

---

## 6. 项目结构

```
agent/
├── agent.py              # 编排循环：LLM ↔ MCP 工具循环
├── safety.py             # 安全护栏（每个工具调用的闸门）
├── memory/store.py       # SQLite 决策/复盘/研究笔记
└── prompts/trading_manager.md   # Agent 系统提示词
integrations/llm/         # 多 provider LLM 适配层（Claude / OpenAI 兼容）
config/settings.yaml      # 安全边界配置
scripts/
├── verify_paper.py       # 系统健康检查
├── test_mcp.py           # MCP 工具冒烟测试
└── e2e_mvp.py            # 单次任务运行
docs/research-notes.md    # Hummingbot 能力调研与实测记录
dashboard_api/            # Dashboard 只读后端（FastAPI，端点白名单）
dashboard/                # Dashboard 前端（Next.js + Tailwind + shadcn/ui）
```

## 7. Dashboard（可视化层）

Dashboard 是现有系统的只读可视化层，不包含任何交易逻辑。

```bash
# 后端（FastAPI，端口 8200，只读白名单）
pip install -e ".[dashboard]"
python -m uvicorn dashboard_api.main:app --port 8200

# 前端（Next.js，端口 3000）
cd dashboard
pnpm install
pnpm dev          # 开发模式；或 pnpm build && pnpm start
```

打开 http://localhost:3000 ：顶栏显示五项系统健康状态（AGENT / HUMMINGBOT /
MCP / EXCHANGE / DATABASE）、当前交易模式（TESTNET / LIVE），以及 BTC/ETH
实时价格（SSE 推送，断线自动重连）。

页面：

- **Overview** — 净值、日/总盈亏、回撤（基于净值快照）、执行器盈亏、余额、
  持仓、AI Market View（BTC/ETH 的 regime/trend/action/confidence + 证据摘要）、
  净值曲线、最近 AI 决策
- **Market** — BTC/ETH K线图（lightweight-charts）、成交量、资金费率、价差，
  以及该币种最新的 AI 市场分析
- **Executors** — 运行中/已终止执行器，网格执行器显示价格区间和网格层
- **Orders / Positions** — 订单和持仓明细
- **Timeline** — AI 决策时间线（MARKET_SCAN → ANALYSIS → EXECUTOR_CREATED →
  ORDER → FILL → REVIEW），只展示结构化摘要，不含模型内部推理
- **Journal** — 历史交易复盘（执行器 ⋈ trade_reviews），支持按币种/策略/盈亏筛选
- **Memory** — Agent 记忆四 Tab：Analysis / Decisions / Reviews / Notes

AI Market View 的数据来源：Agent 每次分析后在回复末尾输出 ```analysis JSON 块，
`agent/agent.py` 解析后写入 memory 库的 `market_analysis` 表，Dashboard 只读展示。
若某币种显示"暂无分析"，说明 Agent 还没分析过它，跑一次分析即可。

净值快照存储在 `data/dashboard.db`（60 秒节流），回撤和净值曲线随使用逐渐积累。

安全设计：`dashboard_api/hummingbot.py` 用端点白名单约束后端能调用的
Hummingbot API——下单、撤单、创建执行器等写接口在代码层面不可达。
Dashboard 不能切换交易模式（模式由 `.env` 的 `LIVE_TRADING` + 重启决定）。

### AI Quant Lab（/lab）

实验分析平台：KPI（PnL/回撤/胜率/Profit Factor）、AI 决策分布、
AI vs Baseline 对比、策略归因、Market Regime 归因、Decisions 列表
（点击查看 Decision Replay：市场快照 → 决策 → 风险 → 执行 → 复盘）、
实验列表与报告下载。

数据来自 `data/analytics.db`（decision_events / trade_events /
review_events / experiments 四张表），与交易路径隔离——分析库故障不会
影响交易，Agent 端写入是 best-effort。

实验管理：

```bash
python scripts/experiment.py start "7d-testnet-v1" --symbols BTC,ETH --strategies grid
python scripts/experiment.py list
python scripts/experiment.py report <experiment_id> -o report.md
python scripts/experiment.py stop <experiment_id>

# Baseline（不经过 Agent 的固定网格策略，用于 AI vs Baseline 对比）
python scripts/baseline_grid.py --pair BTC-USDT --amount 200 \
    --start 83000 --end 85000 --experiment <experiment_id>

# 长期实验的自动扫描循环（每 30 分钟扫描一次，每 6 个周期复盘一次）
python scripts/agent_loop.py --interval-min 30 --review-every 6
```

实验运行期间不要修改 prompt / settings.yaml——版本哈希会变化，不同版本
的交易不能混在一起分析。

### 7 天 → 30 天晋级（Phase 9 → Phase 10）

7 天实验无严重问题后晋级：

```bash
# 1. 生成并检查 7 天报告（重点：风险事件、执行失败率、决策质量分布）
python scripts/experiment.py report exp-<id> -o report-7d.md

# 2. 结束 7 天实验，开启 30 天实验（同一套基础设施，同一版本冻结纪律）
python scripts/experiment.py stop exp-<7d-id>
python scripts/experiment.py start "30d-testnet-v1" --symbols BTC,ETH \
    --strategies grid,momentum,mean_reversion,breakout

# 3. agent loop 无需重启——它自动打标当前 running 状态的实验
```

晋级门槛（全部满足才晋级）：无安全违规绕过、无执行器异常失控、
Memory/Analytics 无持续写入失败、复盘质量分布中 execution_failure 占多数
时先修执行链路再晋级。

## 8. 安全须知

- 默认且始终建议 **PAPER 模式**。`LIVE_TRADING=true` 只会由你本人设置，
  Agent 无法自行开启。
- 实盘阶段还需另行解决 Binance 实盘 API 的网络可达性问题。
- 不要把含真实凭证的 `.env` 提交到 git。
- testnet 凭证无真实价值，但仍建议定期轮换。
