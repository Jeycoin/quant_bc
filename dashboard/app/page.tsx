"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { StatCard } from "@/components/stat-card";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
  n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 });

export default function OverviewPage() {
  const overview = useQuery({
    queryKey: ["overview"],
    queryFn: api.overview,
    refetchInterval: 10_000,
  });

  if (overview.isPending) {
    return (
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
    );
  }

  if (overview.isError) {
    return (
      <Card className="border-red-500/40 bg-red-500/5">
        <CardHeader>
          <CardTitle className="text-red-400">Backend unreachable</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          {String(overview.error)} — is the dashboard API running? (python -m uvicorn
          dashboard_api.main:app --port 8200)
        </CardContent>
      </Card>
    );
  }

  const d = overview.data;
  const pnl = d.executors.total_pnl_quote ?? 0;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatCard title="Equity" value={fmtUsd(d.equity)} sub={`${d.balances.length} assets`} />
        <StatCard
          title="Total Executor PnL"
          value={fmtUsd(pnl)}
          tone={pnl > 0 ? "up" : pnl < 0 ? "down" : "neutral"}
          sub={`volume ${fmtUsd(d.executors.total_volume_quote ?? 0)}`}
        />
        <StatCard title="Drawdown" value="—" sub="phase 2: equity history" />
        <StatCard
          title="Active Executors"
          value={String(d.executors.total_active ?? 0)}
          sub={`${d.open_position_count} open positions`}
        />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <Card className="bg-card/60">
          <CardHeader>
            <CardTitle className="text-sm">Balances</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Token</TableHead>
                  <TableHead>Connector</TableHead>
                  <TableHead className="text-right">Units</TableHead>
                  <TableHead className="text-right">Available</TableHead>
                  <TableHead className="text-right">Value</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {d.balances.map((b) => (
                  <TableRow key={`${b.connector}-${b.token}`}>
                    <TableCell className="font-medium">{b.token}</TableCell>
                    <TableCell className="text-muted-foreground">{b.connector}</TableCell>
                    <TableCell className="text-right tabular-nums">{b.units}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {b.available_units}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{fmtUsd(b.value)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card className="bg-card/60">
          <CardHeader>
            <CardTitle className="text-sm">Open Positions</CardTitle>
          </CardHeader>
          <CardContent>
            {d.open_positions.length === 0 ? (
              <p className="text-sm text-muted-foreground">No open positions.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Pair</TableHead>
                    <TableHead className="text-right">Amount</TableHead>
                    <TableHead className="text-right">Entry</TableHead>
                    <TableHead className="text-right">Unrealized PnL</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {d.open_positions.map((p) => (
                    <TableRow key={`${p.connector_name}-${p.trading_pair}`}>
                      <TableCell className="font-medium">{p.trading_pair}</TableCell>
                      <TableCell className="text-right tabular-nums">{p.amount}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {p.entry_price?.toLocaleString()}
                      </TableCell>
                      <TableCell
                        className={`text-right tabular-nums ${p.unrealized_pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}
                      >
                        {fmtUsd(p.unrealized_pnl)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
