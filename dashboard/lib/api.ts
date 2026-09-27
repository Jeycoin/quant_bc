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
};
