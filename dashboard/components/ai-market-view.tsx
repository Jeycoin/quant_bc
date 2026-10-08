"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type AiAnalysis } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const ACTION_STYLE: Record<string, string> = {
  LONG: "border-emerald-500/40 bg-emerald-500/10 text-emerald-400",
  SHORT: "border-red-500/40 bg-red-500/10 text-red-400",
  WATCH: "border-amber-500/40 bg-amber-500/10 text-amber-400",
  WAIT: "border-zinc-500/40 bg-zinc-500/10 text-zinc-400",
};

export function AnalysisCard({ symbol }: { symbol: string }) {
  const q = useQuery({
    queryKey: ["ai-analysis", symbol],
    queryFn: () => api.aiAnalysis(symbol),
    refetchInterval: 30_000,
  });
  const a: AiAnalysis | undefined = q.data?.data?.[0];

  return (
    <Card className="bg-card/60">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm">
          {symbol}
          {a ? (
            <Badge variant="outline" className={cn("text-xs", ACTION_STYLE[a.action ?? ""] ?? "")}>
              {a.action}
            </Badge>
          ) : (
            <Badge variant="outline" className="text-xs text-muted-foreground">NO DATA</Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="text-xs">
        {a ? (
          <div className="space-y-1.5">
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-muted-foreground">
              <span>regime <span className="text-foreground">{a.regime ?? "—"}</span></span>
              <span>volatility <span className="text-foreground">{a.volatility ?? "—"}</span></span>
              <span>strategy <span className="text-foreground">{a.strategy ?? "—"}</span></span>
              <span>
                confidence{" "}
                <span className="text-foreground">
                  {a.confidence != null ? `${(a.confidence * 100).toFixed(0)}%` : "—"}
                </span>
              </span>
            </div>
            <p className="text-muted-foreground">{a.trend}</p>
            <p className="border-l-2 border-violet-500/40 pl-2 text-muted-foreground">{a.evidence}</p>
            <p className="text-[10px] text-muted-foreground/60">
              {new Date(a.ts * 1000).toLocaleString("zh-CN", { hour12: false })} · {a.mode}
            </p>
          </div>
        ) : (
          <p className="text-muted-foreground">
            暂无 AI 分析——运行 Agent 扫描 {symbol} 后自动生成。
          </p>
        )}
      </CardContent>
    </Card>
  );
}

export function AiMarketView() {
  return (
    <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
      {["BTC", "ETH", "SOL", "XRP", "SUI"].map((s) => (
        <AnalysisCard key={s} symbol={s} />
      ))}
    </div>
  );
}
