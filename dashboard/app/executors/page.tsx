"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Executor } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
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

const fmtUsd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 4 });
const fmtTime = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString("zh-CN", { hour12: false }) : "—";

const STATUS_STYLE: Record<string, string> = {
  RUNNING: "bg-emerald-500/15 text-emerald-400 border-emerald-500/40",
  TERMINATED: "bg-zinc-500/15 text-zinc-400 border-zinc-500/40",
  FAILED: "bg-red-500/15 text-red-400 border-red-500/40",
};

function GridRangeBar({ e }: { e: Executor }) {
  const g = e.grid_info;
  if (!g) return null;
  const lo = Math.min(g.start_price, g.limit_price ?? g.start_price);
  const hi = g.end_price;
  const span = hi - lo || 1;
  const pct = (p: number) => `${(((p - lo) / span) * 100).toFixed(2)}%`;

  return (
    <div className="space-y-2">
      <div className="flex justify-between text-xs text-muted-foreground">
        <span>
          Range {g.start_price.toLocaleString()} → {g.end_price.toLocaleString()}
        </span>
        <span>
          {g.level_count} levels · {(g.take_profit_per_level * 100).toFixed(3)}%/level ·{" "}
          {g.total_amount_quote ?? "?"} quote
        </span>
      </div>
      <div className="relative h-10 rounded-md bg-accent/40">
        {g.level_prices.map((p) => (
          <div
            key={p}
            className="absolute top-1 bottom-1 w-px bg-sky-500/30"
            style={{ left: pct(p) }}
          />
        ))}
        <div
          className="absolute -top-0.5 h-11 w-0.5 bg-emerald-400"
          style={{ left: pct(g.start_price) }}
          title={`start ${g.start_price}`}
        />
        <div
          className="absolute -top-0.5 h-11 w-0.5 bg-emerald-400"
          style={{ left: pct(g.end_price) }}
          title={`end ${g.end_price}`}
        />
        {g.limit_price != null && (
          <div
            className="absolute -top-0.5 h-11 w-0.5 bg-red-400"
            style={{ left: pct(g.limit_price) }}
            title={`stop/limit ${g.limit_price}`}
          />
        )}
      </div>
      <p className="text-[11px] text-muted-foreground">
        Indicative layout derived from config (green = range bounds, red = stop limit).
      </p>
    </div>
  );
}

function ExecutorDetail({ id, onClose }: { id: string | null; onClose: () => void }) {
  const detail = useQuery({
    queryKey: ["executor", id],
    queryFn: () => api.executor(id!),
    enabled: !!id,
  });
  const e = detail.data;
  return (
    <Dialog open={!!id} onOpenChange={() => onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="break-all pr-6 font-mono text-sm">{id}</DialogTitle>
          <DialogDescription className="text-xs">
            {e ? `${e.executor_type} · ${e.connector_name} · ${e.trading_pair}` : "loading…"}
          </DialogDescription>
        </DialogHeader>
        {detail.isPending || !e ? (
          <Skeleton className="h-40" />
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-3 gap-x-6 gap-y-2 text-sm">
              {(
                [
                  ["Status", e.status],
                  ["Close type", e.close_type ?? "—"],
                  ["Created", fmtTime(e.created_at)],
                  ["Closed", e.closed_at ? fmtTime(e.closed_at) : e.close_timestamp ? fmtTime(new Date(e.close_timestamp * 1000).toISOString()) : "—"],
                  ["Net PnL", `${fmtUsd(e.net_pnl_quote)} (${(e.net_pnl_pct * 100).toFixed(3)}%)`],
                  ["Fees", fmtUsd(e.cum_fees_quote)],
                  ["Filled volume", fmtUsd(e.filled_amount_quote)],
                  ["Errors", String(e.error_count ?? 0)],
                  ["Controller", e.controller_id],
                ] as [string, string][]
              ).map(([k, v]) => (
                <div key={k} className="flex justify-between gap-2 border-b border-border/50 py-1">
                  <span className="text-muted-foreground">{k}</span>
                  <span className="text-right font-mono text-xs">{v}</span>
                </div>
              ))}
            </div>
            {e.grid_info && <GridRangeBar e={e} />}
            {e.last_error && (
              <p className="rounded-md bg-red-500/10 p-2 text-xs text-red-400">{e.last_error}</p>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default function ExecutorsPage() {
  const [selected, setSelected] = useState<string | null>(null);
  const executors = useQuery({
    queryKey: ["executors"],
    queryFn: () => api.executors(),
    refetchInterval: 10_000,
  });

  const rows = executors.data?.data ?? [];

  return (
    <Card className="bg-card/60">
      <CardHeader>
        <CardTitle className="text-sm">Executors</CardTitle>
      </CardHeader>
      <CardContent>
        {executors.isPending ? (
          <Skeleton className="h-40" />
        ) : executors.isError ? (
          <p className="text-sm text-red-400">{String(executors.error)}</p>
        ) : rows.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No executors yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>ID</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Pair</TableHead>
                <TableHead>Connector</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="text-right">Filled Vol</TableHead>
                <TableHead className="text-right">Fees</TableHead>
                <TableHead className="text-right">Net PnL</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((e) => (
                <TableRow
                  key={e.executor_id}
                  className="cursor-pointer hover:bg-accent/50"
                  onClick={() => setSelected(e.executor_id)}
                >
                  <TableCell className="font-mono text-xs">
                    {e.executor_id.slice(0, 8)}…
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{e.executor_type.replace("_executor", "")}</Badge>
                  </TableCell>
                  <TableCell className="font-medium">{e.trading_pair}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{e.connector_name}</TableCell>
                  <TableCell>
                    <Badge variant="outline" className={STATUS_STYLE[e.status] ?? ""}>
                      {e.status}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {fmtTime(e.created_at)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {fmtUsd(e.filled_amount_quote)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {fmtUsd(e.cum_fees_quote)}
                  </TableCell>
                  <TableCell
                    className={`text-right tabular-nums ${e.net_pnl_quote >= 0 ? "text-emerald-400" : "text-red-400"}`}
                  >
                    {fmtUsd(e.net_pnl_quote)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
      <ExecutorDetail id={selected} onClose={() => setSelected(null)} />
    </Card>
  );
}
