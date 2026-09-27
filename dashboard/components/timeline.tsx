"use client";

import { useQuery } from "@tanstack/react-query";
import { api, type TimelineEvent } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const EVENT_STYLE: Record<string, { dot: string; badge: string }> = {
  MARKET_SCAN: { dot: "bg-sky-400", badge: "border-sky-500/40 text-sky-400" },
  ANALYSIS: { dot: "bg-violet-400", badge: "border-violet-500/40 text-violet-400" },
  ACCOUNT_CHECK: { dot: "bg-cyan-400", badge: "border-cyan-500/40 text-cyan-400" },
  STRATEGY_SELECTION: { dot: "bg-amber-400", badge: "border-amber-500/40 text-amber-400" },
  RISK_CHECK: { dot: "bg-red-400", badge: "border-red-500/40 text-red-400" },
  EXECUTOR_CREATED: { dot: "bg-emerald-400", badge: "border-emerald-500/40 text-emerald-400" },
  EXECUTOR_STOPPED: { dot: "bg-orange-400", badge: "border-orange-500/40 text-orange-400" },
  REVIEW: { dot: "bg-pink-400", badge: "border-pink-500/40 text-pink-400" },
};

const fmtTime = (ts: number) =>
  new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });

export function Timeline({ limit = 50, compact = false }: { limit?: number; compact?: boolean }) {
  const events = useQuery({
    queryKey: ["timeline", limit],
    queryFn: () => api.timeline(limit),
    refetchInterval: 15_000,
  });

  if (events.isPending) return <Skeleton className="h-64" />;
  if (events.isError) return <p className="text-sm text-red-400">{String(events.error)}</p>;

  const rows = events.data.data;

  return (
    <div className="relative space-y-0">
      {rows.map((e: TimelineEvent, i: number) => {
        const style = EVENT_STYLE[e.event_type] ?? { dot: "bg-zinc-400", badge: "" };
        return (
          <div key={`${e.source}-${e.id}`} className="relative flex gap-3 pb-4">
            {i < rows.length - 1 && (
              <div className="absolute left-[5px] top-4 h-full w-px bg-border" />
            )}
            <div className={cn("mt-1.5 h-[11px] w-[11px] shrink-0 rounded-full", style.dot)} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline" className={cn("text-[10px]", style.badge)}>
                  {e.event_type}
                </Badge>
                {e.symbol && (
                  <span className="text-xs font-medium text-foreground">{e.symbol}</span>
                )}
                <span className="text-[11px] text-muted-foreground">{fmtTime(e.ts)}</span>
                <span
                  className={cn(
                    "text-[11px] font-medium",
                    e.result === "OK" && "text-emerald-400",
                    e.result === "ERROR" && "text-red-400",
                    e.result === "BLOCKED" && "text-amber-400"
                  )}
                >
                  {e.result}
                </span>
              </div>
              {!compact && (
                <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
                  {e.summary}
                </p>
              )}
            </div>
          </div>
        );
      })}
      {rows.length === 0 && (
        <p className="py-6 text-center text-sm text-muted-foreground">No agent events yet.</p>
      )}
    </div>
  );
}
