"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type ReplayBundle } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const fmtUsd = (n: number | null | undefined) =>
  n == null
    ? "—"
    : n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 4 });
const fmtTs = (ts: number | null | undefined) =>
  ts ? new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false }) : "—";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="mb-1 text-xs font-medium uppercase text-muted-foreground">{title}</p>
      <div className="rounded-md bg-accent/40 p-3 text-xs leading-relaxed">{children}</div>
    </div>
  );
}

/** Decision Replay: market → decision → risk → execution → outcome → review.
 *  Renders structured summaries only — never model chain-of-thought. */
export function DecisionReplay({ decisionId }: { decisionId: string }) {
  const replay = useQuery({
    queryKey: ["lab-replay", decisionId],
    queryFn: () => api.labDecisionReplay(decisionId),
  });

  if (replay.isPending) return <Skeleton className="h-48" />;
  if (replay.isError)
    return <p className="text-xs text-red-400">{String(replay.error)}</p>;

  const { decision: d, trade, reviews }: ReplayBundle = replay.data.data;
  const portfolio = d.portfolio_context ?? {};
  const market = d.market_context ?? {};

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <Badge variant="outline">{d.symbol ?? "—"}</Badge>
        <Badge variant="outline">{d.market_regime ?? "—"}</Badge>
        <Badge
          className={cn(
            d.action === "WAIT" || d.action === "REJECT"
              ? "text-amber-400"
              : "text-emerald-400"
          )}
          variant="outline"
        >
          {d.action ?? "—"}
        </Badge>
        <Badge variant="outline">conf {d.confidence ?? "—"}</Badge>
        <Badge variant="outline">risk {d.risk_status ?? "—"}</Badge>
        <span className="ml-auto text-muted-foreground">{fmtTs(d.ts)}</span>
      </div>

      <Section title="AI Decision">
        <p>{d.evidence ?? "—"}</p>
        <p className="mt-1 text-muted-foreground">
          strategy: {d.strategy ?? "—"} · outcome: {d.outcome_status ?? "—"}
        </p>
      </Section>

      <Section title="Portfolio Context (at decision time)">
        <p>
          equity {fmtUsd(portfolio.equity)} · open positions{" "}
          {portfolio.open_position_count ?? 0}
        </p>
        {(portfolio.open_positions ?? []).map((p, i) => (
          <p key={i} className="text-muted-foreground">
            {p.trading_pair} {p.side} {p.amount} @ {p.entry_price} (uPnL{" "}
            {fmtUsd(p.unrealized_pnl)})
          </p>
        ))}
      </Section>

      {Object.keys(market).length > 0 && (
        <Section title="Market Context (tool result snapshot)">
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all">
            {String(market.tool_result ?? JSON.stringify(market)).slice(0, 1500)}
          </pre>
        </Section>
      )}

      {trade && (
        <Section title="Execution & Outcome">
          <p>
            {trade.strategy} · {trade.symbol} · {trade.status}
            {trade.close_type ? ` (${trade.close_type})` : ""}
          </p>
          <p
            className={cn(
              (trade.pnl_quote ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"
            )}
          >
            PnL {fmtUsd(trade.pnl_quote)} (
            {trade.pnl_pct != null ? `${(trade.pnl_pct * 100).toFixed(3)}%` : "—"})
          </p>
          <p className="text-muted-foreground">
            regime at entry: {trade.regime_at_entry ?? "—"} · source:{" "}
            {trade.source ?? "—"}
          </p>
        </Section>
      )}

      {reviews.length > 0 && (
        <Section title="Review">
          {reviews.map((r) => (
            <div key={r.review_id} className="mb-2 last:mb-0">
              <p>
                quality: {r.decision_quality ?? "—"} · outcome: {r.outcome ?? "—"}
              </p>
              {r.lesson && <p className="text-muted-foreground">lesson: {r.lesson}</p>}
            </div>
          ))}
        </Section>
      )}

      <p className="text-right text-[10px] text-muted-foreground">
        agent {d.agent_version} · prompt {d.prompt_version} · config {d.config_version}
        {d.experiment_id ? ` · exp ${d.experiment_id}` : ""}
      </p>
    </div>
  );
}
