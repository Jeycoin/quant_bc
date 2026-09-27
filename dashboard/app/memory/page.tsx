"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

const fmtTime = (ts: number) => new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });

function DecisionsTab() {
  const q = useQuery({ queryKey: ["decisions"], queryFn: () => api.decisions(100), refetchInterval: 15_000 });
  if (q.isPending) return <Skeleton className="h-40" />;
  const rows = q.data?.data ?? [];
  return (
    <div className="space-y-2">
      {rows.map((d) => (
        <details key={d.id} className="group rounded-md border border-border/60 bg-accent/20 px-3 py-2">
          <summary className="flex cursor-pointer items-center gap-3 text-sm">
            <span className="font-mono text-xs text-muted-foreground">#{d.id}</span>
            <span className="text-xs text-muted-foreground">{fmtTime(d.ts)}</span>
            <Badge variant="outline" className="text-[10px]">{d.mode}</Badge>
            <span className="font-mono text-xs">{d.tool_name ?? "—"}</span>
            <span
              className={cn(
                "ml-auto text-xs",
                d.outcome === "OK" && "text-emerald-400",
                d.outcome?.startsWith("ERROR") && "text-red-400",
                d.outcome?.startsWith("BLOCKED") && "text-amber-400"
              )}
            >
              {d.outcome ?? "—"}
            </span>
          </summary>
          <pre className="mt-2 overflow-x-auto rounded bg-background p-2 font-mono text-[11px] text-muted-foreground">
            {JSON.stringify(JSON.parse(d.tool_arguments || "{}"), null, 2)}
          </pre>
        </details>
      ))}
      {rows.length === 0 && <p className="py-4 text-center text-sm text-muted-foreground">No decisions.</p>}
    </div>
  );
}

function ReviewsTab() {
  const q = useQuery({ queryKey: ["reviews"], queryFn: api.reviews, refetchInterval: 30_000 });
  if (q.isPending) return <Skeleton className="h-32" />;
  const rows = q.data?.data ?? [];
  return (
    <div className="space-y-2">
      {rows.map((r) => (
        <div key={r.id} className="rounded-md border border-border/60 bg-accent/20 px-3 py-2">
          <div className="flex items-center gap-3">
            <span className="font-mono text-xs text-muted-foreground">#{r.id}</span>
            <span className="text-xs text-muted-foreground">{fmtTime(r.ts)}</span>
            <span className="text-sm font-medium">{r.subject}</span>
          </div>
          <p className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">{r.review}</p>
        </div>
      ))}
      {rows.length === 0 && <p className="py-4 text-center text-sm text-muted-foreground">No reviews.</p>}
    </div>
  );
}

function AnalysisTab() {
  const q = useQuery({ queryKey: ["analysis"], queryFn: () => api.aiAnalysis(), refetchInterval: 15_000 });
  if (q.isPending) return <Skeleton className="h-32" />;
  const rows = q.data?.data ?? [];
  return (
    <div className="space-y-2">
      {rows.map((a) => (
        <div key={a.id} className="rounded-md border border-border/60 bg-accent/20 px-3 py-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-muted-foreground">#{a.id}</span>
            <span className="text-xs text-muted-foreground">{fmtTime(a.ts)}</span>
            <Badge variant="outline">{a.symbol}</Badge>
            <Badge variant="outline" className="border-violet-500/40 text-violet-400">{a.regime}</Badge>
            <Badge
              variant="outline"
              className={cn(
                a.action === "LONG" && "border-emerald-500/40 text-emerald-400",
                a.action === "SHORT" && "border-red-500/40 text-red-400",
                (a.action === "WAIT" || a.action === "WATCH") && "border-amber-500/40 text-amber-400"
              )}
            >
              {a.action}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {a.strategy} · confidence {a.confidence}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">{a.evidence}</p>
        </div>
      ))}
      {rows.length === 0 && (
        <p className="py-4 text-center text-sm text-muted-foreground">
          No structured analyses yet — ask the agent to scan BTC/ETH and it will record them here.
        </p>
      )}
    </div>
  );
}

function NotesTab() {
  const q = useQuery({ queryKey: ["notes"], queryFn: api.notes, refetchInterval: 30_000 });
  if (q.isPending) return <Skeleton className="h-32" />;
  const rows = q.data?.data ?? [];
  return (
    <div className="space-y-2">
      {rows.map((n) => (
        <div key={n.id} className="rounded-md border border-border/60 bg-accent/20 px-3 py-2">
          <div className="flex items-center gap-3">
            <span className="font-mono text-xs text-muted-foreground">#{n.id}</span>
            <span className="text-xs text-muted-foreground">{fmtTime(n.ts)}</span>
            <span className="text-sm font-medium">{n.topic}</span>
          </div>
          <p className="mt-1 whitespace-pre-wrap text-xs text-muted-foreground">{n.note}</p>
        </div>
      ))}
      {rows.length === 0 && <p className="py-4 text-center text-sm text-muted-foreground">No notes.</p>}
    </div>
  );
}

export default function MemoryPage() {
  return (
    <Card className="bg-card/60">
      <CardHeader>
        <CardTitle className="text-sm">Agent Memory</CardTitle>
      </CardHeader>
      <CardContent>
        <Tabs defaultValue="analysis">
          <TabsList>
            <TabsTrigger value="analysis">Analysis</TabsTrigger>
            <TabsTrigger value="decisions">Decisions</TabsTrigger>
            <TabsTrigger value="reviews">Reviews</TabsTrigger>
            <TabsTrigger value="notes">Notes</TabsTrigger>
          </TabsList>
          <TabsContent value="analysis"><AnalysisTab /></TabsContent>
          <TabsContent value="decisions"><DecisionsTab /></TabsContent>
          <TabsContent value="reviews"><ReviewsTab /></TabsContent>
          <TabsContent value="notes"><NotesTab /></TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
