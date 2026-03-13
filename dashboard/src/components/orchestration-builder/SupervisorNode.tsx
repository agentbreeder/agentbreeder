import { Shield } from "lucide-react";
import type { OrchNodeData } from "./types";

export function SupervisorNode({ data }: { data: OrchNodeData }) {
  return (
    <div className="rounded-lg border-2 border-purple-300 bg-purple-50 dark:bg-purple-950/30 px-4 py-3 min-w-[140px]">
      <div className="flex items-center gap-2">
        <Shield className="size-4 text-purple-600" />
        <span className="text-sm font-medium">{data.label}</span>
      </div>
      <p className="text-xs text-muted-foreground mt-1">Supervisor</p>
    </div>
  );
}
