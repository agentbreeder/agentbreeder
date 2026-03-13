import { useQuery } from "@tanstack/react-query";
import { Loader2, Network } from "lucide-react";
import { api } from "@/lib/api";

/**
 * A2A Topology Graph — shows connections between A2A agents.
 * 
 * Uses a simple force-directed layout. For full ReactFlow integration,
 * this component can be swapped with a ReactFlow canvas.
 */
export function A2ATopology() {
  const { data, isLoading } = useQuery({
    queryKey: ["a2a-agents"],
    queryFn: () => api.a2a.list(),
  });

  const agents = data?.data ?? [];

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (agents.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
        <Network className="size-8 mb-2" />
        <p>No A2A agents registered</p>
      </div>
    );
  }

  // Simple circular layout
  const cx = 200;
  const cy = 200;
  const radius = 140;
  const angleStep = (2 * Math.PI) / agents.length;

  return (
    <div className="rounded-lg border p-4">
      <h3 className="mb-3 font-medium">Agent Topology</h3>
      <svg viewBox="0 0 400 400" className="w-full max-w-md mx-auto">
        {/* Connection lines between all agents */}
        {agents.map((a, i) => {
          const x1 = cx + radius * Math.cos(i * angleStep - Math.PI / 2);
          const y1 = cy + radius * Math.sin(i * angleStep - Math.PI / 2);
          return agents.slice(i + 1).map((b, j) => {
            const idx = i + j + 1;
            const x2 = cx + radius * Math.cos(idx * angleStep - Math.PI / 2);
            const y2 = cy + radius * Math.sin(idx * angleStep - Math.PI / 2);
            return (
              <line
                key={`${a.id}-${b.id}`}
                x1={x1} y1={y1} x2={x2} y2={y2}
                stroke="currentColor"
                strokeOpacity={0.15}
                strokeWidth={1}
              />
            );
          });
        })}
        {/* Agent nodes */}
        {agents.map((agent, i) => {
          const x = cx + radius * Math.cos(i * angleStep - Math.PI / 2);
          const y = cy + radius * Math.sin(i * angleStep - Math.PI / 2);
          const statusColor =
            agent.status === "active" ? "#10b981" :
            agent.status === "error" ? "#ef4444" :
            agent.status === "registered" ? "#3b82f6" : "#9ca3af";
          return (
            <g key={agent.id}>
              <circle cx={x} cy={y} r={24} fill="currentColor" fillOpacity={0.05} stroke={statusColor} strokeWidth={2} />
              <circle cx={x + 16} cy={y - 16} r={4} fill={statusColor} />
              <text x={x} y={y + 4} textAnchor="middle" className="text-[10px] fill-current font-medium">
                {agent.name.length > 10 ? agent.name.slice(0, 9) + "…" : agent.name}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
