"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { CandleChart } from "@/components/candle-chart";
import { StatCard } from "@/components/stat-card";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

const INTERVALS = ["1m", "5m", "15m", "1h"] as const;

export default function MarketPage() {
  const [symbol, setSymbol] = useState<"BTC" | "ETH">("BTC");
  const [interval, setInterval_] = useState<string>("1m");

  const market = useQuery({
    queryKey: ["market", symbol, interval],
    queryFn: () => api.market(symbol, interval),
    refetchInterval: 10_000,
  });

  const d = market.data;
  const fundingPct = d ? (d.funding.funding_rate * 100).toFixed(4) : "—";
  const nextFunding = d
    ? new Date(d.funding.next_funding_time * 1000).toLocaleTimeString("zh-CN", { hour12: false })
    : "—";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <Tabs value={symbol} onValueChange={(v) => setSymbol(v as "BTC" | "ETH")}>
          <TabsList>
            <TabsTrigger value="BTC">BTC</TabsTrigger>
            <TabsTrigger value="ETH">ETH</TabsTrigger>
          </TabsList>
        </Tabs>
        <div className="flex gap-1">
          {INTERVALS.map((i) => (
            <button
              key={i}
              onClick={() => setInterval_(i)}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs transition-colors",
                interval === i
                  ? "bg-accent text-foreground"
                  : "text-muted-foreground hover:bg-accent/50"
              )}
            >
              {i}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatCard
          title={`${symbol} Price`}
          value={d?.last_price ? d.last_price.toLocaleString() : "—"}
          sub={d ? `${d.pair} · ${d.connector}` : ""}
        />
        <StatCard title="Funding Rate" value={`${fundingPct}%`} sub={`next ${nextFunding}`} />
        <StatCard
          title="Spread"
          value={d?.order_book.spread != null ? `$${d.order_book.spread.toFixed(1)}` : "—"}
          sub={d?.order_book.spread_pct != null ? `${d.order_book.spread_pct.toFixed(4)}%` : ""}
        />
        <StatCard title="Open Interest" value="—" sub="not exposed by Hummingbot" />
        <StatCard
          title="Mark / Index"
          value={d ? d.funding.mark_price.toLocaleString() : "—"}
          sub={d ? d.funding.index_price.toLocaleString() : ""}
        />
      </div>

      <Card className="bg-card/60">
        <CardContent className="pt-4">
          {market.isPending ? (
            <Skeleton className="h-[380px]" />
          ) : market.isError ? (
            <p className="text-sm text-red-400">{String(market.error)}</p>
          ) : (
            <CandleChart candles={d!.candles} />
          )}
        </CardContent>
      </Card>

      <div className="grid gap-3 lg:grid-cols-2">
        <Card className="bg-card/60">
          <CardHeader>
            <CardTitle className="text-sm">Order Book (top 10)</CardTitle>
          </CardHeader>
          <CardContent>
            {d ? (
              <div className="grid grid-cols-2 gap-4 font-mono text-xs">
                <div>
                  <p className="mb-1 text-emerald-400">Bids</p>
                  {d.order_book.bids.slice(0, 10).map((b) => (
                    <div key={b.price} className="flex justify-between py-0.5">
                      <span>{b.price.toLocaleString()}</span>
                      <span className="text-muted-foreground">{b.amount}</span>
                    </div>
                  ))}
                </div>
                <div>
                  <p className="mb-1 text-red-400">Asks</p>
                  {d.order_book.asks.slice(0, 10).map((a) => (
                    <div key={a.price} className="flex justify-between py-0.5">
                      <span>{a.price.toLocaleString()}</span>
                      <span className="text-muted-foreground">{a.amount}</span>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <Skeleton className="h-40" />
            )}
          </CardContent>
        </Card>

        <Card className="border-dashed bg-card/40">
          <CardHeader>
            <CardTitle className="text-sm">AI Market Analysis</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            <p>
              Phase 5 接入：Agent 的结构化市场判断（regime / trend / volatility / action /
              confidence）落库后，这里展示最近一次的 AI 分析结论与依据摘要。
            </p>
            <p className="mt-2 text-xs">
              前端不会自己计算策略信号——所有 AI 判断都来自 Agent。
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
