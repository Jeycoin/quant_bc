"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type Order } from "@/lib/api";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

const STATUS_STYLE: Record<string, string> = {
  OPEN: "bg-sky-500/15 text-sky-400 border-sky-500/40",
  PARTIALLY_FILLED: "bg-amber-500/15 text-amber-400 border-amber-500/40",
  FILLED: "bg-emerald-500/15 text-emerald-400 border-emerald-500/40",
  CANCELLED: "bg-zinc-500/15 text-zinc-400 border-zinc-500/40",
  FAILED: "bg-red-500/15 text-red-400 border-red-500/40",
};

function fmtTime(iso: string) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("zh-CN", { hour12: false });
}

function OrdersTable({ orders, onSelect }: { orders: Order[]; onSelect: (o: Order) => void }) {
  if (orders.length === 0) {
    return <p className="py-6 text-center text-sm text-muted-foreground">No orders.</p>;
  }
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Time</TableHead>
          <TableHead>Pair</TableHead>
          <TableHead>Side</TableHead>
          <TableHead>Type</TableHead>
          <TableHead className="text-right">Price</TableHead>
          <TableHead className="text-right">Amount</TableHead>
          <TableHead className="text-right">Filled</TableHead>
          <TableHead>Status</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {orders.map((o) => (
          <TableRow
            key={o.order_id}
            className="cursor-pointer hover:bg-accent/50"
            onClick={() => onSelect(o)}
          >
            <TableCell className="text-xs text-muted-foreground">{fmtTime(o.created_at)}</TableCell>
            <TableCell className="font-medium">{o.trading_pair}</TableCell>
            <TableCell className={o.trade_type === "BUY" ? "text-emerald-400" : "text-red-400"}>
              {o.trade_type}
            </TableCell>
            <TableCell className="text-xs text-muted-foreground">{o.order_type}</TableCell>
            <TableCell className="text-right tabular-nums">{o.price?.toLocaleString()}</TableCell>
            <TableCell className="text-right tabular-nums">{o.amount}</TableCell>
            <TableCell className="text-right tabular-nums">{o.filled_amount ?? "—"}</TableCell>
            <TableCell>
              <Badge variant="outline" className={STATUS_STYLE[o.status] ?? ""}>
                {o.status}
              </Badge>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function OrderDetail({ order, onClose }: { order: Order | null; onClose: () => void }) {
  if (!order) return null;
  const fields: [string, React.ReactNode][] = [
    ["Order ID", order.order_id],
    ["Exchange Order ID", order.exchange_order_id ?? "—"],
    ["Connector", order.connector_name],
    ["Pair", order.trading_pair],
    ["Side / Type", `${order.trade_type} / ${order.order_type}`],
    ["Price", order.price?.toLocaleString()],
    ["Amount", String(order.amount)],
    ["Filled Amount", String(order.filled_amount ?? "—")],
    ["Avg Fill Price", order.average_fill_price?.toLocaleString() ?? "—"],
    ["Fee", order.fee_paid != null ? `${order.fee_paid} ${order.fee_currency ?? ""}` : "—"],
    ["Status", order.status],
    ["Created", fmtTime(order.created_at)],
    ["Updated", fmtTime(order.updated_at)],
    ["Error", order.error_message ?? "—"],
  ];
  return (
    <Dialog open={!!order} onOpenChange={() => onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-mono text-sm">Order {order.order_id}</DialogTitle>
          <DialogDescription className="text-xs">
            {order.connector_name} · {order.trading_pair}
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
          {fields.map(([k, v]) => (
            <div key={k} className="flex justify-between gap-2 border-b border-border/50 py-1">
              <span className="text-muted-foreground">{k}</span>
              <span className="break-all text-right font-mono text-xs">{v}</span>
            </div>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default function OrdersPage() {
  const [selected, setSelected] = useState<Order | null>(null);

  const active = useQuery({ queryKey: ["orders", "active"], queryFn: api.activeOrders, refetchInterval: 5_000 });
  const filled = useQuery({ queryKey: ["orders", "FILLED"], queryFn: () => api.orders("FILLED"), refetchInterval: 15_000 });
  const cancelled = useQuery({ queryKey: ["orders", "CANCELLED"], queryFn: () => api.orders("CANCELLED"), refetchInterval: 15_000 });

  const queries = { open: active, filled, cancelled };
  const anyError = active.error ?? filled.error ?? cancelled.error;

  return (
    <Card className="bg-card/60">
      <CardHeader>
        <CardTitle className="text-sm">Orders</CardTitle>
      </CardHeader>
      <CardContent>
        {anyError && (
          <p className="mb-3 text-sm text-red-400">{String(anyError)}</p>
        )}
        <Tabs defaultValue="open">
          <TabsList>
            <TabsTrigger value="open">Open ({active.data?.data.length ?? 0})</TabsTrigger>
            <TabsTrigger value="filled">Filled</TabsTrigger>
            <TabsTrigger value="cancelled">Cancelled</TabsTrigger>
          </TabsList>
          {(["open", "filled", "cancelled"] as const).map((tab) => (
            <TabsContent key={tab} value={tab}>
              {queries[tab].isPending ? (
                <Skeleton className="h-40" />
              ) : (
                <OrdersTable orders={queries[tab].data?.data ?? []} onSelect={setSelected} />
              )}
            </TabsContent>
          ))}
        </Tabs>
      </CardContent>
      <OrderDetail order={selected} onClose={() => setSelected(null)} />
    </Card>
  );
}
