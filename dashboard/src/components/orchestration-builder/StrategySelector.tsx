import { type OrchestrationStrategy } from "./types";

const STRATEGIES: { value: OrchestrationStrategy; label: string; description: string }[] = [
  { value: "router", label: "Router", description: "Route to one agent based on conditions" },
  { value: "sequential", label: "Sequential", description: "Chain agents in order" },
  { value: "parallel", label: "Parallel", description: "Run all agents simultaneously" },
  { value: "hierarchical", label: "Hierarchical", description: "Supervisor delegates to workers" },
  { value: "supervisor", label: "Supervisor", description: "Supervisor plans, delegates, synthesizes" },
  { value: "fan_out_fan_in", label: "Fan-Out/Fan-In", description: "Parallel execution + merge agent" },
];

interface StrategySelectorProps {
  value: OrchestrationStrategy;
  onChange: (strategy: OrchestrationStrategy) => void;
}

export function StrategySelector({ value, onChange }: StrategySelectorProps) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {STRATEGIES.map((s) => (
        <button
          key={s.value}
          className={`rounded-lg border p-3 text-left transition-colors ${
            value === s.value
              ? "border-primary bg-primary/5"
              : "hover:bg-muted/50"
          }`}
          onClick={() => onChange(s.value)}
        >
          <p className="text-sm font-medium">{s.label}</p>
          <p className="text-xs text-muted-foreground">{s.description}</p>
        </button>
      ))}
    </div>
  );
}
