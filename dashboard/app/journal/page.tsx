"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type JournalEntry } from "@/lib/api";
import { DecisionReplay } from "@/components/decision-replay";
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
import { cn } from "@/lib/utils";

const fmtUsd = (n: number) =>
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 4 });
const fmtTime = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString("zh-CN", { hour12: false }) : "—";
const fmtDuration = (s?: number | null) => {
  if (s == null) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
};

/** Decision Replay section: finds the agent decision that created this
 *  executor (via execution_id link) and renders the full replay bundle. */
function LinkedReplay({ executorId }: { executorId: string }) {
  const linked = useQuery({
    queryKey: ["lab-decision-by-exec", executorId],
    queryFn: () => api.labDecisions({ execution_id: executorId }),
    retry: false,
  });
  if (linked.isPending) return <Skeleton className="h-24" />;
  const decision = linked.data?.data?.[0];
  if (!decision) {
    return (
      <p className="text-xs text-muted-foreground">
        无关联的 AI 决策记录（该执行器可能是手动创建的 baseline，或创建于
        decision events 上线之前）。
      </p>
    );
  }
  return <DecisionReplay decisionId={decision.decision_id} />;
}

export default function JournalPage() {
  const [symbol, setSymbol] = useState<string>("");
  const [strategy, setStrategy] = useState<string>("");
  const [result, setResult] = useState<string>("");
  const [selected, setSelected] = useState<JournalEntry | null>(null);

  const journal = useQuery({ queryKey: ["journal"], queryFn: api.journal, refetchInterval: 30_000 });
  const rows = useMemo(() => journal.data?.data ?? [], [journal.data]);

  const symbols = useMemo(() => [...new Set(rows.map((r) => r.symbol))], [rows]);
  const strategies = useMemo(() => [...new Set(rows.map((r) => r.strategy))], [rows]);

  const filtered = rows.filter(
    (r) =>
      (!symbol || r.symbol === symbol) &&
      (!strategy || r.strategy === strategy) &&
      (!result || (result === "win" ? r.pnl_quote > 0 : r.pnl_quote <= 0))
  );

  const totalPnl = filtered.reduce((s, r) => s + (r.pnl_quote ?? 0), 0);

  const selectCls =
    "rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground";

  return (
    <Card className="bg-card/60">
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-3 text-sm">
          Trade Journal
          <span className="text-xs font-normal text-muted-foreground">
            {filtered.length} trades · total PnL{" "}
            <span className={totalPnl >= 0 ? "text-emerald-400" : "text-red-400"}>
              {fmtUsd(totalPnl)}
            </span>
          </span>
          <span className="ml-auto flex gap-2">
            <select className={selectCls} value={symbol} onChange={(e) => setSymbol(e.target.value)}>
              <option value="">All symbols</option>
              {symbols.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            <select className={selectCls} value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              <option value="">All strategies</option>
              {strategies.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            <select className={selectCls} value={result} onChange={(e) => setResult(e.target.value)}>
              <option value="">Win + Loss</option>
              <option value="win">Win</option>
              <option value="loss">Loss</option>
            </select>
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {journal.isPending ? (
          <Skeleton className="h-40" />
        ) : journal.isError ? (
          <p className="text-sm text-red-400">{String(journal.error)}</p>
        ) : filtered.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No completed trades.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Closed</TableHead>
                <TableHead>Symbol</TableHead>
                <TableHead>Strategy</TableHead>
                <TableHead className="text-right">Duration</TableHead>
                <TableHead className="text-right">Volume</TableHead>
                <TableHead className="text-right">Fees</TableHead>
                <TableHead className="text-right">PnL</TableHead>
                <TableHead>Review</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((t) => (
                <TableRow
                  key={t.executor_id}
                  className="cursor-pointer hover:bg-accent/50"
                  onClick={() => setSelected(t)}
                >
                  <TableCell className="text-xs text-muted-foreground">{fmtTime(t.closed_at)}</TableCell>
                  <TableCell className="font-medium">{t.symbol}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{t.strategy.replace("_executor", "")}</Badge>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{fmtDuration(t.duration_s)}</TableCell>
                  <TableCell className="text-right tabular-nums">{fmtUsd(t.filled_amount_quote)}</TableCell>
                  <TableCell className="text-right tabular-nums">{fmtUsd(t.fees_quote)}</TableCell>
                  <TableCell
                    className={cn(
                      "text-right tabular-nums",
                      t.pnl_quote >= 0 ? "text-emerald-400" : "text-red-400"
                    )}
                  >
                    {fmtUsd(t.pnl_quote)} ({(t.pnl_pct * 100).toFixed(2)}%)
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {t.memory_id != null ? `#${t.memory_id}` : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>

      <Dialog open={!!selected} onOpenChange={() => setSelected(null)}>
        <DialogContent className="max-w-xl">
          {selected && (
            <>
              <DialogHeader>
                <DialogTitle className="text-sm">
                  {selected.symbol} · {selected.strategy}
                </DialogTitle>
                <DialogDescription className="break-all font-mono text-xs">
                  {selected.executor_id}
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-3 text-sm">
                <div className="grid grid-cols-2 gap-2">
                  <div>Opened: {fmtTime(selected.opened_at)}</div>
                  <div>Closed: {fmtTime(selected.closed_at)}</div>
                  <div>
                    PnL:{" "}
                    <span className={selected.pnl_quote >= 0 ? "text-emerald-400" : "text-red-400"}>
                      {fmtUsd(selected.pnl_quote)} ({(selected.pnl_pct * 100).toFixed(3)}%)
                    </span>
                  </div>
                  <div>Close type: {selected.close_type ?? "—"}</div>
                </div>
                <div>
                  <p className="mb-1 text-xs font-medium uppercase text-muted-foreground">
                    Decision Replay
                  </p>
                  <LinkedReplay executorId={selected.executor_id} />
                </div>
                <div>
                  <p className="mb-1 text-xs font-medium uppercase text-muted-foreground">
                    Agent Review {selected.memory_id != null && `(memory #${selected.memory_id})`}
                  </p>
                  <p className="whitespace-pre-wrap rounded-md bg-accent/40 p-3 text-xs leading-relaxed">
                    {selected.review ?? "暂无复盘记录——可以让 Agent 复盘该执行器后生成。"}
                  </p>
                </div>
              </div>
            </>
          )}
        </DialogContent>
      </Dialog>
    </Card>
  );
}
