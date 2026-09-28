"use client";

import { useQuery } from "@tanstack/react-query";
import {
  api,
  type IntelOverview,
  type NarrativeRow,
  type NewsRow,
  type RejectionRow,
} from "@/lib/api";
import { StatCard } from "@/components/stat-card";
import { Badge } from "@/components/ui/badge";
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
import { cn } from "@/lib/utils";

const fmtTs = (ts: number) => new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false });
const fmtAge = (mins: number | null | undefined) =>
  mins == null ? "—" : mins < 60 ? `${mins.toFixed(0)}m ago` : `${(mins / 60).toFixed(1)}h ago`;
const pct = (n: number | null | undefined) => (n == null ? "—" : `${(n * 100).toFixed(0)}%`);

const GRID_STATE_STYLE: Record<string, string> = {
  GRID_NORMAL: "text-emerald-400 border-emerald-400/40",
  GRID_WARNING: "text-amber-400 border-amber-400/40",
  GRID_DEFENSIVE: "text-orange-400 border-orange-400/40",
  GRID_EXIT: "text-red-400 border-red-400/40",
  UNKNOWN: "text-muted-foreground",
};

const GATE_STYLE: Record<string, string> = {
  COST_REJECTED: "text-amber-400",
  RISK_REJECTED: "text-red-400",
  GRID_REJECTED: "text-orange-400",
};

function NarrativeCard({ n }: { n: NonNullable<IntelOverview["narratives"]>[number] }) {
  return (
    <div className="rounded-md border border-border/60 p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge variant="outline">{n.symbol ?? "MARKET"}</Badge>
          <span className="font-mono text-sm">{n.name}</span>
        </div>
        <span className="tabular-nums text-sm text-cyan-400">{pct(n.strength)}</span>
      </div>
      <div className="mt-2 grid grid-cols-4 gap-2 text-center text-xs">
        <div><div className="text-muted-foreground">novelty</div>{pct(n.novelty)}</div>
        <div><div className="text-muted-foreground">social</div>{pct(n.social_momentum)}</div>
        <div><div className="text-muted-foreground">price conf.</div>{pct(n.price_confirmation)}</div>
        <div><div className="text-muted-foreground">onchain</div>{pct(n.onchain_confirmation)}</div>
      </div>
      {n.evidence?.length > 0 && (
        <ul className="mt-2 list-inside list-disc text-xs text-muted-foreground">
          {n.evidence.slice(0, 3).map((e, i) => <li key={i}>{e}</li>)}
        </ul>
      )}
    </div>
  );
}

export default function IntelPage() {
  const overview = useQuery({
    queryKey: ["intel-overview"],
    queryFn: api.intelOverview,
    refetchInterval: 60_000,
  });
  const rejections = useQuery({
    queryKey: ["intel-rejections"],
    queryFn: () => api.intelRejections(168),
    refetchInterval: 60_000,
  });

  if (overview.isLoading) {
    return <div className="space-y-3"><Skeleton className="h-24" /><Skeleton className="h-64" /></div>;
  }
  const d = overview.data;
  if (!d?.available) {
    return (
      <Card className="bg-card/60">
        <CardContent className="py-8 text-center text-sm text-muted-foreground">
          Intelligence layer unavailable: {d?.reason ?? "no data"}. The collector
          (scripts/collect_intelligence.py) may not be running.
        </CardContent>
      </Card>
    );
  }

  const fng = d.social?.market?.fear_greed;
  const rej = rejections.data?.summary ?? {};
  const totalRejected = Object.entries(rej)
    .filter(([k]) => k.endsWith("REJECTED"))
    .reduce((s, [, v]) => s + v, 0);
  const passed = rej["PASSED"] ?? 0;

  return (
    <div className="space-y-4">
      {/* KPI row */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatCard
          title="Fear & Greed"
          value={fng ? `${fng.value.toFixed(0)} ${fng.classification ?? ""}` : "—"}
          tone={fng && fng.value <= 25 ? "down" : "neutral"}
        />
        <StatCard
          title="Active Narratives"
          value={String(d.narratives?.length ?? 0)}
        />
        <StatCard
          title="Rejected (7d)"
          value={String(totalRejected)}
          tone={totalRejected > passed ? "down" : "neutral"}
        />
        <StatCard
          title="Proposals Passed (7d)"
          value={String(passed)}
        />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        {/* Narrative view */}
        <Card className="bg-card/60">
          <CardHeader><CardTitle className="text-sm">Narrative Engine</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {(d.narratives ?? []).length === 0 ? (
              <p className="py-4 text-center text-xs text-muted-foreground">
                No active narrative above the detection threshold.
              </p>
            ) : (
              (d.narratives ?? []).map((n, i) => <NarrativeCard key={i} n={n} />)
            )}
          </CardContent>
        </Card>

        {/* Grid protection */}
        <Card className="bg-card/60">
          <CardHeader><CardTitle className="text-sm">Grid Protection</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {Object.entries(d.grid_protection ?? {}).map(([sym, g]) => (
              <div key={sym} className="rounded-md border border-border/60 p-3">
                <div className="flex items-center justify-between">
                  <Badge variant="outline">{sym}</Badge>
                  <span className={cn("font-mono text-sm", GRID_STATE_STYLE[g.state] ?? "")}>
                    {g.state}
                  </span>
                </div>
                <ul className="mt-1 list-inside list-disc text-xs text-muted-foreground">
                  {g.reasons.slice(0, 3).map((r, i) => <li key={i}>{r}</li>)}
                </ul>
              </div>
            ))}
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        {/* On-chain + social detail */}
        <Card className="bg-card/60">
          <CardHeader><CardTitle className="text-sm">On-chain / Social</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Symbol</TableHead><TableHead>Metric</TableHead>
                  <TableHead className="text-right">Value</TableHead>
                  <TableHead className="text-right">Age</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {Object.entries(d.onchain ?? {}).flatMap(([sym, metrics]) =>
                  Object.entries(metrics).map(([k, v]) => (
                    <TableRow key={`${sym}-${k}`}>
                      <TableCell><Badge variant="outline">{sym}</Badge></TableCell>
                      <TableCell className="font-mono text-xs">
                        {k}{v.change_pct_1d != null && (
                          <span className={cn("ml-1", v.change_pct_1d >= 0 ? "text-emerald-400" : "text-red-400")}>
                            {v.change_pct_1d >= 0 ? "+" : ""}{v.change_pct_1d.toFixed(1)}%
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{v.value.toLocaleString()}</TableCell>
                      <TableCell className="text-right text-muted-foreground">{fmtAge(v.age_minutes)}</TableCell>
                    </TableRow>
                  ))
                )}
                {Object.values(d.onchain ?? {}).every((m) => Object.keys(m).length === 0) && (
                  <TableRow><TableCell colSpan={4} className="text-center text-xs text-muted-foreground">
                    No on-chain data (key-gated providers disabled or no BTC/ETH signal yet).
                  </TableCell></TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        {/* Top news */}
        <Card className="bg-card/60">
          <CardHeader><CardTitle className="text-sm">Top News Events</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            {(d.news ?? []).length === 0 ? (
              <p className="py-4 text-center text-xs text-muted-foreground">No recent news events.</p>
            ) : (
              (d.news ?? []).map((n, i) => (
                <div key={i} className="rounded-md border border-border/60 p-2 text-xs">
                  <div className="flex items-center gap-2">
                    {n.asset && <Badge variant="outline">{n.asset}</Badge>}
                    <Badge variant="outline">{n.category}</Badge>
                    <span className={cn("ml-auto tabular-nums",
                      n.severity >= 0.75 ? "text-red-400" : "text-muted-foreground")}>
                      sev {n.severity.toFixed(2)}
                    </span>
                    <span className="text-muted-foreground">{fmtAge(n.age_minutes)}</span>
                  </div>
                  <div className="mt-1">{n.title}</div>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      {/* Rejected opportunities */}
      <Card className="bg-card/60">
        <CardHeader><CardTitle className="text-sm">Rejected Opportunities (7d)</CardTitle></CardHeader>
        <CardContent>
          <div className="mb-3 flex flex-wrap gap-2">
            {Object.entries(rej).map(([gate, n]) => (
              <Badge key={gate} variant="outline" className={GATE_STYLE[gate] ?? ""}>
                {gate}: {n}
              </Badge>
            ))}
          </div>
          {(rejections.data?.recent ?? []).length === 0 ? (
            <p className="py-4 text-center text-xs text-muted-foreground">No rejections recorded.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead><TableHead>Symbol</TableHead>
                  <TableHead>Proposal</TableHead><TableHead>Gate</TableHead>
                  <TableHead>Reason</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(rejections.data?.recent ?? []).map((r: RejectionRow) => (
                  <TableRow key={r.decision_id}>
                    <TableCell className="text-xs text-muted-foreground">{fmtTs(r.ts)}</TableCell>
                    <TableCell><Badge variant="outline">{r.symbol ?? "—"}</Badge></TableCell>
                    <TableCell className="font-mono text-xs">{r.action ?? "—"}</TableCell>
                    <TableCell className={cn("text-xs", GATE_STYLE[r.risk_status ?? ""] ?? "")}>
                      {r.risk_status}
                    </TableCell>
                    <TableCell className="max-w-md truncate text-xs text-muted-foreground"
                               title={r.rejection_reason ?? ""}>
                      {r.rejection_reason}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
