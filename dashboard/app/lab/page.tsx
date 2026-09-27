"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Bar,
  BarChart,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  api,
  type AttributionRow,
  type DecisionEvent,
  type TradingMetrics,
} from "@/lib/api";
import { DecisionReplay } from "@/components/decision-replay";
import { StatCard } from "@/components/stat-card";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

const fmtUsd = (n: number | null | undefined) =>
  n == null
    ? "—"
    : n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });
const fmtPct = (n: number | null | undefined) =>
  n == null ? "—" : `${(n * 100).toFixed(2)}%`;
const fmtTs = (ts: number) => new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
const pnlCls = (n: number | null | undefined) =>
  n == null ? "" : n >= 0 ? "text-emerald-400" : "text-red-400";

const DECISION_COLORS: Record<string, string> = {
  LONG: "#34d399", SHORT: "#f87171", WAIT: "#fbbf24", WATCH: "#60a5fa",
  TRADE: "#34d399", REJECT: "#f87171", SCAN: "#a78bfa",
};

function AttributionTable({ title, rows }: { title: string; rows: AttributionRow[] }) {
  return (
    <Card className="bg-card/60">
      <CardHeader><CardTitle className="text-sm">{title}</CardTitle></CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="py-4 text-center text-xs text-muted-foreground">No trades yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{title.includes("Strategy") ? "Strategy" : "Regime"}</TableHead>
                <TableHead className="text-right">Trades</TableHead>
                <TableHead className="text-right">PnL</TableHead>
                <TableHead className="text-right">Fees</TableHead>
                <TableHead className="text-right">Win Rate</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((r) => (
                <TableRow key={r.bucket}>
                  <TableCell><Badge variant="outline">{r.bucket}</Badge></TableCell>
                  <TableCell className="text-right tabular-nums">{r.closed_count}/{r.trade_count}</TableCell>
                  <TableCell className={cn("text-right tabular-nums", pnlCls(r.total_pnl_quote))}>
                    {fmtUsd(r.total_pnl_quote)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{fmtUsd(r.total_fees_quote)}</TableCell>
                  <TableCell className="text-right tabular-nums">{fmtPct(r.win_rate)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function CompareTable({ data }: { data: Record<string, TradingMetrics> }) {
  const sources = Object.keys(data);
  const rows: [string, (m: TradingMetrics) => string][] = [
    ["Trades", (m) => String(m.closed_count)],
    ["Total PnL", (m) => fmtUsd(m.total_pnl_quote)],
    ["Win Rate", (m) => fmtPct(m.win_rate)],
    ["Profit Factor", (m) => (m.profit_factor == null ? "—" : m.profit_factor.toFixed(2))],
    ["Expectancy", (m) => fmtUsd(m.expectancy)],
    ["Avg Holding", (m) => (m.avg_holding_s == null ? "—" : `${(m.avg_holding_s / 3600).toFixed(1)}h`)],
    ["Fees", (m) => fmtUsd(m.total_fees_quote)],
  ];
  return (
    <Card className="bg-card/60">
      <CardHeader>
        <CardTitle className="text-sm">AI vs Baseline</CardTitle>
      </CardHeader>
      <CardContent>
        {sources.length === 0 ? (
          <p className="py-4 text-center text-xs text-muted-foreground">No trades yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Metric</TableHead>
                {sources.map((s) => (
                  <TableHead key={s} className="text-right">
                    <Badge variant={s === "agent" ? "default" : "outline"}>
                      {s === "agent" ? "AI Assisted" : s}
                    </Badge>
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map(([label, fn]) => (
                <TableRow key={label}>
                  <TableCell className="text-xs text-muted-foreground">{label}</TableCell>
                  {sources.map((s) => (
                    <TableCell key={s} className="text-right tabular-nums">{fn(data[s])}</TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <p className="mt-2 text-[10px] text-muted-foreground">
          样本量、周期、市场环境、费用和参数差异都会影响可比性——PnL 更高不代表 AI 更好。
        </p>
      </CardContent>
    </Card>
  );
}

export default function LabPage() {
  const [experimentId, setExperimentId] = useState<string>("");
  const [selected, setSelected] = useState<DecisionEvent | null>(null);
  const expParam = experimentId || undefined;

  const experiments = useQuery({ queryKey: ["lab-experiments"], queryFn: api.labExperiments, retry: false });
  const metrics = useQuery({
    queryKey: ["lab-metrics", experimentId],
    queryFn: () => api.labMetrics(expParam),
    refetchInterval: 30_000,
    retry: false,
  });
  const compare = useQuery({
    queryKey: ["lab-compare", experimentId],
    queryFn: () => api.labCompare(expParam),
    refetchInterval: 30_000,
    retry: false,
  });
  const decisions = useQuery({
    queryKey: ["lab-decisions", experimentId],
    queryFn: () => api.labDecisions(experimentId ? { experiment_id: experimentId } : {}),
    refetchInterval: 15_000,
    retry: false,
  });

  const distData = useMemo(
    () =>
      Object.entries(metrics.data?.data.decisions ?? {}).map(([action, count]) => ({
        action,
        count,
      })),
    [metrics.data]
  );

  const m = metrics.data?.data;

  return (
    <div className="space-y-4">
      <Card className="bg-card/60">
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-3 text-sm">
            AI Quant Lab
            <span className="text-xs font-normal text-muted-foreground">
              实验分析平台——AI 决策是否带来可测量的增量价值
            </span>
            <select
              className="ml-auto rounded-md border border-border bg-background px-2 py-1 text-xs"
              value={experimentId}
              onChange={(e) => setExperimentId(e.target.value)}
            >
              <option value="">All data (no experiment filter)</option>
              {(experiments.data?.data ?? []).map((e) => (
                <option key={e.experiment_id} value={e.experiment_id}>
                  {e.status === "running" ? "🟢 " : ""}{e.name} ({e.experiment_id})
                </option>
              ))}
            </select>
          </CardTitle>
        </CardHeader>
      </Card>

      {metrics.isPending ? (
        <Skeleton className="h-64" />
      ) : metrics.isError ? (
        <Card className="bg-card/60">
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            {String(metrics.error)}
          </CardContent>
        </Card>
      ) : m ? (
        <>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <StatCard title="Total PnL" value={fmtUsd(m.trading.total_pnl_quote)}
              tone={m.trading.total_pnl_quote >= 0 ? "up" : "down"} />
            <StatCard title="Total Return" value={fmtPct(m.equity.total_return)} />
            <StatCard title="Max Drawdown"
              value={m.equity.max_drawdown_pct == null ? "—" : `${m.equity.max_drawdown_pct.toFixed(2)}%`} />
            <StatCard title="Win Rate" value={fmtPct(m.trading.win_rate)} />
            <StatCard title="Profit Factor"
              value={m.trading.profit_factor == null ? "—" : m.trading.profit_factor.toFixed(2)} />
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="bg-card/60">
              <CardHeader><CardTitle className="text-sm">AI Decisions</CardTitle></CardHeader>
              <CardContent>
                {distData.length === 0 ? (
                  <p className="py-4 text-center text-xs text-muted-foreground">No decisions yet.</p>
                ) : (
                  <ResponsiveContainer width="100%" height={180}>
                    <BarChart data={distData}>
                      <XAxis dataKey="action" tick={{ fontSize: 11 }} />
                      <YAxis allowDecimals={false} tick={{ fontSize: 11 }} width={30} />
                      <Tooltip />
                      <Bar dataKey="count">
                        {distData.map((d) => (
                          <Cell key={d.action} fill={DECISION_COLORS[d.action] ?? "#8884d8"} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                )}
                <div className="mt-2 grid grid-cols-2 gap-1 text-[11px] text-muted-foreground">
                  <span>fill rate {fmtPct(m.execution.fill_rate)}</span>
                  <span>cancel rate {fmtPct(m.execution.cancel_rate)}</span>
                  <span>slippage {m.execution.avg_slippage_pct?.toFixed(3) ?? "—"}%</span>
                  <span>exposure {m.exposure.exposure_pct?.toFixed(1) ?? "—"}%</span>
                </div>
              </CardContent>
            </Card>
            <CompareTable data={compare.data?.data ?? {}} />
            <Card className="bg-card/60">
              <CardHeader><CardTitle className="text-sm">Experiments</CardTitle></CardHeader>
              <CardContent className="space-y-2 text-xs">
                {(experiments.data?.data ?? []).length === 0 && (
                  <p className="text-muted-foreground">
                    还没有实验。用 <code>python scripts/experiment.py start ...</code> 创建。
                  </p>
                )}
                {(experiments.data?.data ?? []).map((e) => (
                  <div key={e.experiment_id} className="rounded-md bg-accent/40 p-2">
                    <div className="flex items-center gap-2">
                      <Badge variant={e.status === "running" ? "default" : "outline"}>{e.status}</Badge>
                      <span className="font-medium">{e.name}</span>
                      <a
                        className="ml-auto text-blue-400 hover:underline"
                        href={`${process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8200"}/api/lab/report/${e.experiment_id}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        report
                      </a>
                    </div>
                    <p className="mt-1 text-muted-foreground">
                      {fmtTs(e.started_at)} · agent {e.agent_version} · prompt {e.prompt_version}
                    </p>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <AttributionTable title="Strategy Performance" rows={m.attribution.strategy} />
            <AttributionTable title="Market Regime Performance" rows={m.attribution.regime} />
          </div>

          <Card className="bg-card/60">
            <CardHeader>
              <CardTitle className="text-sm">
                Decisions <span className="text-xs font-normal text-muted-foreground">click for replay</span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              {(decisions.data?.data ?? []).length === 0 ? (
                <p className="py-4 text-center text-xs text-muted-foreground">No decision events yet.</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Time</TableHead>
                      <TableHead>Symbol</TableHead>
                      <TableHead>Regime</TableHead>
                      <TableHead>Action</TableHead>
                      <TableHead>Strategy</TableHead>
                      <TableHead className="text-right">Conf</TableHead>
                      <TableHead>Risk</TableHead>
                      <TableHead>Outcome</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(decisions.data?.data ?? []).map((d) => (
                      <TableRow
                        key={d.decision_id}
                        className="cursor-pointer hover:bg-accent/50"
                        onClick={() => setSelected(d)}
                      >
                        <TableCell className="text-xs text-muted-foreground">{fmtTs(d.ts)}</TableCell>
                        <TableCell className="font-medium">{d.symbol ?? "—"}</TableCell>
                        <TableCell><Badge variant="outline">{d.market_regime ?? "—"}</Badge></TableCell>
                        <TableCell>
                          <Badge
                            variant="outline"
                            className={cn(
                              d.action === "WAIT" || d.action === "REJECT"
                                ? "text-amber-400"
                                : "text-emerald-400"
                            )}
                          >
                            {d.action ?? "—"}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-xs">{d.strategy ?? "—"}</TableCell>
                        <TableCell className="text-right tabular-nums">{d.confidence ?? "—"}</TableCell>
                        <TableCell className="text-xs">{d.risk_status ?? "—"}</TableCell>
                        <TableCell className="text-xs">{d.outcome_status ?? "—"}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </>
      ) : null}

      <Dialog open={!!selected} onOpenChange={() => setSelected(null)}>
        <DialogContent className="max-w-2xl">
          {selected && (
            <>
              <DialogHeader>
                <DialogTitle className="text-sm">
                  Decision Replay · {selected.symbol ?? "—"} · {selected.action ?? "—"}
                </DialogTitle>
                <DialogDescription className="break-all font-mono text-xs">
                  {selected.decision_id}
                </DialogDescription>
              </DialogHeader>
              <DecisionReplay decisionId={selected.decision_id} />
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
