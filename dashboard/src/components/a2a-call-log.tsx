import { useQuery } from "@tanstack/react-query";
import { Loader2, Phone } from "lucide-react";
import { api } from "@/lib/api";
import { RelativeTime } from "@/components/ui/relative-time";
import { Badge } from "@/components/ui/badge";

/**
 * A2A Call Log — shows recent inter-agent calls from the tracing API.
 */
export function A2ACallLog({ agentName }: { agentName?: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ["traces", agentName, "a2a"],
    queryFn: () =>
      api.traces.list({
        agent_name: agentName,
        per_page: 20,
      }),
  });

  const traces = data?.data ?? [];

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (traces.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-8 text-muted-foreground">
        <Phone className="size-6 mb-2" />
        <p className="text-sm">No recent A2A calls</p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border">
      <div className="border-b px-4 py-2">
        <h3 className="font-medium text-sm">Recent Calls</h3>
      </div>
      <div className="divide-y">
        {traces.map((trace) => (
          <div key={trace.trace_id} className="flex items-center gap-3 px-4 py-2.5">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium truncate">{trace.agent_name}</p>
              <p className="text-xs text-muted-foreground truncate">{trace.input_preview}</p>
            </div>
            <Badge variant={trace.status === "success" ? "secondary" : "destructive"} className="text-xs">
              {trace.status}
            </Badge>
            <span className="text-xs text-muted-foreground whitespace-nowrap">
              {trace.duration_ms}ms
            </span>
            <span className="text-xs text-muted-foreground whitespace-nowrap">
              <RelativeTime date={trace.created_at} />
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
