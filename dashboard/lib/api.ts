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

export const api = {
  mode: () => get<{ mode: string; live_trading_env: boolean; note: string }>("/api/mode"),
  health: () => get<Health>("/api/health"),
  overview: () => get<Overview>("/api/overview"),
  decisions: (limit = 50) => get<{ data: Decision[] }>(`/api/memory/decisions?limit=${limit}`),
};
