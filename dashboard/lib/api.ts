const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8200";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`API ${path} -> ${res.status}: ${body.slice(0, 200)}`);
  }
  return res.json() as Promise<T>;
}

export type HealthCheck = { ok: boolean; detail: string };
export type Health = {
  mode: string;
  ok: boolean;
  checks: Record<string, HealthCheck>;
};

export type Balance = {
  account: string;
  connector: string;
  token: string;
  units: number;
  available_units: number;
  price: number;
  value: number;
};

export type ExecutorSummary = {
  total_active: number;
  total_pnl_quote: number;
  total_volume_quote: number;
  by_type: Record<string, number>;
  by_connector: Record<string, number>;
  by_status: Record<string, number>;
};

export type Position = {
  account_name: string;
  connector_name: string;
  trading_pair: string;
  side: string;
  amount: number;
  entry_price: number;
  unrealized_pnl: number;
  leverage: number;
  mark_price?: number | null;
  mark_change_pct?: number | null;
};

export type Order = {
  order_id: string;
  account_name: string;
  connector_name: string;
  trading_pair: string;
  trade_type: string;
  order_type: string;
  amount: number;
  price: number;
  status: string;
  filled_amount: number | null;
  average_fill_price: number | null;
  fee_paid: number | null;
  fee_currency: string | null;
  created_at: string;
  updated_at: string;
  exchange_order_id: string | null;
  error_message: string | null;
};

export type Overview = {
  mode: string;
  equity: number;
  balances: Balance[];
  executors: ExecutorSummary;
  open_positions: Position[];
  open_position_count: number;
  equity_history: { ts: number; equity: number }[];
  peak_equity: number;
  drawdown_pct: number;
};

export type Decision = {
  id: number;
  ts: number;
  mode: string;
  market_context: string;
  analysis: string;
  tool_name: string | null;
  tool_arguments: string;
  outcome: string | null;
};

export type TimelineEvent = {
  id: number | string;
  ts: number;
  mode: string | null;
  event_type: string;
  symbol: string | null;
  summary: string;
  result: string;
  source: string;
};

export type AiAnalysis = {
  id: number;
  ts: number;
  mode: string;
  symbol: string | null;
  regime: string | null;
  trend: string | null;
  volatility: string | null;
  action: string | null;
  strategy: string | null;
  confidence: number | null;
  evidence: string | null;
};

export type JournalEntry = {
  executor_id: string;
  symbol: string;
  strategy: string;
  connector: string;
  opened_at: string | null;
  closed_at: string | null;
  duration_s: number | null;
  pnl_quote: number;
  pnl_pct: number;
  filled_amount_quote: number;
  fees_quote: number;
  close_type: string | null;
  review: string | null;
  memory_id: number | null;
};

export type Review = { id: number; ts: number; subject: string; review: string };
export type Note = { id: number; ts: number; topic: string; note: string };

export type DecisionEvent = {
  decision_id: string;
  ts: number;
  mode: string;
  symbol: string | null;
  market_regime: string | null;
  action: string | null;
  strategy: string | null;
  confidence: number | null;
  evidence: string | null;
  risk_status: string | null;
  outcome_status: string | null;
  execution_id: string | null;
  agent_version: string | null;
  prompt_version: string | null;
  config_version: string | null;
  experiment_id: string | null;
};

export type DecisionEventFull = DecisionEvent & {
  tool_name: string | null;
  tool_arguments: Record<string, unknown>;
  market_context: Record<string, unknown>;
  portfolio_context: {
    equity?: number;
    balances?: { account: string; connector: string; token: string; value: number }[];
    open_positions?: {
      trading_pair: string; side: string; amount: number;
      entry_price: number; unrealized_pnl: number;
    }[];
    open_position_count?: number;
  };
};

export type TradeEvent = {
  executor_id: string;
  decision_id: string | null;
  ts_open: number | null;
  ts_close: number | null;
  symbol: string | null;
  strategy: string | null;
  connector: string | null;
  side: string | null;
  status: string | null;
  close_type: string | null;
  pnl_quote: number | null;
  pnl_pct: number | null;
  filled_amount_quote: number | null;
  fees_quote: number | null;
  regime_at_entry: string | null;
  source: string | null;
  experiment_id: string | null;
};

export type ReviewEvent = {
  review_id: number;
  ts: number;
  decision_id: string | null;
  execution_id: string | null;
  outcome: string | null;
  decision_quality: string | null;
  execution_quality: string | null;
  regime_accuracy: string | null;
  main_error: string | null;
  main_success: string | null;
  lesson: string | null;
};

export type ReplayBundle = {
  decision: DecisionEventFull;
  trade: TradeEvent | null;
  reviews: ReviewEvent[];
};

export type Experiment = {
  experiment_id: string;
  name: string;
  status: string;
  started_at: number;
  ended_at: number | null;
  symbols: string;
  strategies: string;
  agent_version: string | null;
  prompt_version: string | null;
  config_version: string | null;
  notes: string | null;
};

export type TradingMetrics = {
  trade_count: number;
  closed_count: number;
  total_pnl_quote: number;
  win_rate: number | null;
  avg_win: number | null;
  avg_loss: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  avg_holding_s: number | null;
  total_fees_quote: number;
};

export type AttributionRow = {
  bucket: string;
  trade_count: number;
  closed_count: number;
  total_pnl_quote: number;
  total_fees_quote: number;
  win_rate: number | null;
};

export type LabMetrics = {
  trading: TradingMetrics;
  equity: {
    total_return: number | null;
    daily_return: number | null;
    weekly_return: number | null;
    max_drawdown_pct: number | null;
    volatility_daily: number | null;
    sharpe_like: number | null;
    snapshot_count: number;
  };
  execution: {
    order_count: number;
    fill_rate: number | null;
    cancel_rate: number | null;
    failure_rate: number | null;
    avg_slippage_pct: number | null;
  };
  exposure: {
    gross_exposure: number;
    exposure_pct: number | null;
    position_concentration: number | null;
    by_symbol: Record<string, number>;
  };
  decisions: Record<string, number>;
  attribution: { strategy: AttributionRow[]; regime: AttributionRow[] };
};

export type Executor = {
  executor_id: string;
  executor_type: string;
  account_name: string;
  connector_name: string;
  trading_pair: string;
  side: string | null;
  status: string;
  close_type: string | null;
  is_active: boolean;
  is_trading: boolean;
  created_at: string;
  closed_at?: string | null;
  close_timestamp?: number | null;
  controller_id: string;
  net_pnl_quote: number;
  net_pnl_pct: number;
  cum_fees_quote: number;
  filled_amount_quote: number;
  error_count?: number;
  last_error?: string | null;
  config?: Record<string, unknown>;
  grid_info?: GridInfo | null;
};

export type GridInfo = {
  start_price: number;
  end_price: number;
  limit_price: number | null;
  total_amount_quote: number | null;
  take_profit_per_level: number;
  level_count: number;
  level_prices: number[];
  indicative: boolean;
};

export type Candle = {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  n_trades: number;
};

export type MarketData = {
  symbol: string;
  pair: string;
  connector: string;
  interval: string;
  last_price: number | null;
  candles: Candle[];
  funding: {
    funding_rate: number;
    next_funding_time: number;
    mark_price: number;
    index_price: number;
  };
  order_book: {
    bids: { price: number; amount: number }[];
    asks: { price: number; amount: number }[];
    best_bid: number | null;
    best_ask: number | null;
    spread: number | null;
    spread_pct: number | null;
  };
  open_interest: number | null;
};

export const api = {
  mode: () => get<{ mode: string; live_trading_env: boolean; note: string }>("/api/mode"),
  health: () => get<Health>("/api/health"),
  overview: () => get<Overview>("/api/overview"),
  decisions: (limit = 50) => get<{ data: Decision[] }>(`/api/memory/decisions?limit=${limit}`),
  orders: (status?: string, limit = 100) =>
    get<{ data: Order[] }>(
      `/api/orders?limit=${limit}${status ? `&status=${status}` : ""}`
    ),
  activeOrders: () => get<{ data: Order[] }>("/api/orders/active"),
  positions: () => get<{ data: Position[] }>("/api/positions"),
  executors: (status?: string) =>
    get<{ data: Executor[] }>(`/api/executors${status ? `?status=${status}` : ""}`),
  executor: (id: string) => get<Executor>(`/api/executors/${id}`),
  market: (symbol: string, interval = "1m", limit = 200) =>
    get<MarketData>(`/api/market?symbol=${symbol}&interval=${interval}&limit=${limit}`),
  timeline: (limit = 100) => get<{ data: TimelineEvent[] }>(`/api/ai/timeline?limit=${limit}`),
  aiAnalysis: (symbol?: string) =>
    get<{ data: AiAnalysis[] }>(`/api/ai/analysis${symbol ? `?symbol=${symbol}` : ""}`),
  journal: () => get<{ data: JournalEntry[] }>("/api/journal"),
  reviews: () => get<{ data: Review[] }>("/api/memory/reviews"),
  notes: () => get<{ data: Note[] }>("/api/memory/notes"),
  labDecisions: (params: Record<string, string> = {}) => {
    const qs = new URLSearchParams(params).toString();
    return get<{ data: DecisionEvent[] }>(`/api/lab/decisions${qs ? `?${qs}` : ""}`);
  },
  labDecisionReplay: (id: string) =>
    get<{ data: ReplayBundle }>(`/api/lab/decisions/${id}`),
  labTrades: (params: Record<string, string> = {}) => {
    const qs = new URLSearchParams(params).toString();
    return get<{ synced: number; data: TradeEvent[] }>(`/api/lab/trades${qs ? `?${qs}` : ""}`);
  },
  labDecisionDistribution: () =>
    get<{ data: Record<string, number> }>("/api/lab/decision-distribution"),
  labExperiments: () => get<{ data: Experiment[] }>("/api/lab/experiments"),
  labMetrics: (experimentId?: string) =>
    get<{ data: LabMetrics }>(
      `/api/lab/metrics${experimentId ? `?experiment_id=${experimentId}` : ""}`
    ),
  labCompare: (experimentId?: string) =>
    get<{ data: Record<string, TradingMetrics> }>(
      `/api/lab/compare${experimentId ? `?experiment_id=${experimentId}` : ""}`
    ),
  labReport: (experimentId: string) =>
    get<{ data: string }>(`/api/lab/report/${experimentId}`),
};

export const API_BASE_URL = API_BASE;
