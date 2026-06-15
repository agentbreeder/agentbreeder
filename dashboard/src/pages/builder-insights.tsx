import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BarChart3 } from "lucide-react";
import { api, type FunnelMetrics } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

const PERIODS = ["7d", "30d", "all"] as const;

function fmtSeconds(s: number | null): string {
  if (s == null) return "--";
  if (s < 90) return `${Math.round(s)}s`;
  return `${(s / 60).toFixed(1)}m`;
}

export default function BuilderInsightsPage() {
  const [period, setPeriod] = useState<(typeof PERIODS)[number]>("7d");
  const { data, isLoading, error } = useQuery({
    queryKey: ["builder-funnel", period],
    queryFn: () => api.analytics.funnel(period),
  });
  const metrics: FunnelMetrics | null = data?.data ?? null;
  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <div className="flex items-center justify-between">
        <h1 className="flex items-center gap-2 font-display text-2xl">
          <BarChart3 className="h-6 w-6" aria-hidden /> Builder
        </h1>
        <Tabs value={period} onValueChange={(v) => setPeriod(v as typeof period)}>
          <TabsList>
            {PERIODS.map((p) => (
              <TabsTrigger key={p} value={p}>
                {p}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      {isLoading && (
        <div className="animate-pulse space-y-4" aria-busy="true">
          <div className="h-28 rounded-lg border border-border bg-card" />
          <div className="h-72 rounded-lg border border-border bg-card" />
        </div>
      )}

      {error && (
        <div className="text-sm text-destructive">
          Failed to load builder analytics: {(error as Error).message}
        </div>
      )}

      {metrics && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Time to first deployed agent</CardTitle>
            </CardHeader>
            <CardContent className="flex items-end gap-8">
              <div>
                <div className="text-xs text-muted-foreground">p50</div>
                <div className="text-4xl font-semibold tabular-nums">
                  {fmtSeconds(metrics.time_to_first_deploy_p50_s)}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">p90</div>
                <div className="text-2xl font-medium tabular-nums text-muted-foreground">
                  {fmtSeconds(metrics.time_to_first_deploy_p90_s)}
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Conversion funnel</CardTitle>
            </CardHeader>
            <CardContent>
              {metrics.stages.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No builder sessions yet. Start one from the Agent Wizard.
                </p>
              ) : (
                <ul role="list" className="space-y-3" aria-label={`Builder funnel for ${metrics.period}`}>
                  {metrics.stages.map((s, i) => {
                    const top = metrics.stages[0]?.count || 1;
                    const pct = Math.round((s.count / top) * 100);
                    const worst = Math.max(...metrics.stages.map((x) => x.dropoff_pct));
                    const isWorst = i > 0 && s.dropoff_pct === worst && worst > 0;
                    return (
                      <li key={s.key} className={cn("rounded-md", isWorst && "border-l-2 border-amber-500 pl-2")}>
                        <div className="flex items-center justify-between text-sm">
                          <span>{s.label}</span>
                          <span className="tabular-nums text-muted-foreground">
                            {s.count.toLocaleString()}
                            {i > 0 && (
                              <span className="ml-2 text-amber-600 dark:text-amber-400">▼ {s.dropoff_pct}%</span>
                            )}
                          </span>
                        </div>
                        <div className="mt-1 h-2 w-full rounded-full bg-muted">
                          <div
                            className="h-full rounded-full bg-emerald-500 transition-all"
                            style={{ width: `${pct}%` }}
                          />
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </CardContent>
          </Card>

          {metrics.engines.length > 0 && (
            <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
              {metrics.engines.map((e) => (
                <Card key={e.engine}>
                  <CardHeader>
                    <CardTitle className="capitalize">
                      {e.engine} ({e.samples})
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2 text-sm">
                    <ScoreRow label="Spec validity" v={e.spec_validity_rate} />
                    <ScoreRow label="Deploy success" v={e.deploy_success_rate} />
                    <div className="flex justify-between">
                      <span>Turns to spec</span>
                      <span className="tabular-nums">{e.turns_to_spec}</span>
                    </div>
                    <ScoreRow label="Hallucinated fields" v={e.hallucinated_field_rate} invert />
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function ScoreRow({ label, v, invert = false }: { label: string; v: number; invert?: boolean }) {
  const good = invert ? v <= 0.2 : v >= 0.8;
  const mid = invert ? v <= 0.4 : v >= 0.6;
  const color = good ? "bg-emerald-500" : mid ? "bg-amber-500" : "bg-red-500";
  return (
    <div>
      <div className="flex justify-between">
        <span>{label}</span>
        <span className="tabular-nums">{Math.round(v * 100)}%</span>
      </div>
      <div className="mt-1 h-1.5 w-full rounded-full bg-muted">
        <div className={cn("h-full rounded-full", color)} style={{ width: `${Math.round(v * 100)}%` }} />
      </div>
    </div>
  );
}
