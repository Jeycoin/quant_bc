"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const CHECK_LABELS: Record<string, string> = {
  agent: "AGENT",
  hummingbot: "HUMMINGBOT",
  mcp: "MCP",
  exchange: "EXCHANGE",
  database: "DATABASE",
};

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      className={cn(
        "inline-block h-2 w-2 rounded-full",
        ok ? "bg-emerald-400 shadow-[0_0_6px_2px_rgba(52,211,153,0.4)]" : "bg-red-500 shadow-[0_0_6px_2px_rgba(239,68,68,0.4)]"
      )}
    />
  );
}

export function Topbar() {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    refetchInterval: 15_000,
  });

  const mode = health.data?.mode ?? "…";
  const modeColor =
    mode === "LIVE"
      ? "bg-red-500/15 text-red-400 border-red-500/40"
      : mode === "TESTNET"
        ? "bg-amber-500/15 text-amber-400 border-amber-500/40"
        : "bg-emerald-500/15 text-emerald-400 border-emerald-500/40";

  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-border bg-card/60 px-4 backdrop-blur">
      <div className="flex items-center gap-6">
        <span className="text-sm font-semibold tracking-widest text-foreground">
          AI QUANT TERMINAL
        </span>
        <div className="hidden items-center gap-4 md:flex">
          {Object.entries(CHECK_LABELS).map(([key, label]) => {
            const check = health.data?.checks?.[key];
            return (
              <span
                key={key}
                title={check?.detail ?? "checking…"}
                className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground"
              >
                <StatusDot ok={check?.ok ?? false} />
                {label}
              </span>
            );
          })}
        </div>
      </div>
      <Badge variant="outline" className={cn("px-3 py-1 text-xs font-bold tracking-wider", modeColor)}>
        {mode}
      </Badge>
    </header>
  );
}
