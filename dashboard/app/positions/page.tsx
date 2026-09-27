"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
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

export default function PositionsPage() {
  const positions = useQuery({
    queryKey: ["positions"],
    queryFn: api.positions,
    refetchInterval: 10_000,
  });

  const rows = positions.data?.data ?? [];

  return (
    <Card className="bg-card/60">
      <CardHeader>
        <CardTitle className="text-sm">
          Positions
          <span className="ml-2 text-xs font-normal text-muted-foreground">
            mark price: indicative, from hyperliquid_perpetual (live feed)
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {positions.isPending ? (
          <Skeleton className="h-32" />
        ) : positions.isError ? (
          <p className="text-sm text-red-400">{String(positions.error)}</p>
        ) : rows.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">No open positions.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Pair</TableHead>
                <TableHead>Connector</TableHead>
                <TableHead>Side</TableHead>
                <TableHead className="text-right">Amount</TableHead>
                <TableHead className="text-right">Entry</TableHead>
                <TableHead className="text-right">Mark</TableHead>
                <TableHead className="text-right">vs Entry</TableHead>
                <TableHead className="text-right">Unrealized PnL</TableHead>
                <TableHead className="text-right">Leverage</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((p) => (
                <TableRow key={`${p.connector_name}-${p.trading_pair}`}>
                  <TableCell className="font-medium">{p.trading_pair}</TableCell>
                  <TableCell className="text-xs text-muted-foreground">{p.connector_name}</TableCell>
                  <TableCell>{p.side}</TableCell>
                  <TableCell className="text-right tabular-nums">{p.amount}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {p.entry_price?.toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {p.mark_price ? p.mark_price.toLocaleString() : "—"}
                  </TableCell>
                  <TableCell
                    className={`text-right tabular-nums ${
                      (p.mark_change_pct ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"
                    }`}
                  >
                    {p.mark_change_pct != null ? `${p.mark_change_pct.toFixed(2)}%` : "—"}
                  </TableCell>
                  <TableCell
                    className={`text-right tabular-nums ${p.unrealized_pnl >= 0 ? "text-emerald-400" : "text-red-400"}`}
                  >
                    {fmtUsd(p.unrealized_pnl)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{p.leverage}x</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
